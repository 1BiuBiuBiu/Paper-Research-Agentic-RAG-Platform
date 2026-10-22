import hashlib
import threading
from pathlib import Path
from typing import Literal
from uuid import uuid4

import faiss
import numpy as np

from app.core.errors import AppError
from app.domain.papers import Document, utc_now
from app.domain.qa import IndexManifest, IndexStatus
from app.repositories.indices import IndexRepository
from app.repositories.papers import PaperRepository
from app.retrieval.chunks import build_chunks, fingerprint
from app.retrieval.embedding import Embedder


class IndexService:
    def __init__(
        self, papers: PaperRepository, indices: IndexRepository, embedder: Embedder
    ) -> None:
        self.papers, self.indices, self.embedder = papers, indices, embedder
        self.data_dir = papers.path.parent
        self.lock = threading.Lock()
        self.building: str | None = None

    def document(self, document_id: str) -> Document:
        document = self.papers.get(document_id)
        if document.status != "parsed" or not document.parsed:
            raise AppError("document_not_parsed", "所选论文尚未成功解析。", 409)
        return document

    def status(self, document_id: str) -> IndexStatus:
        document = self.document(document_id)
        manifest = self.indices.get(document_id)
        state: Literal["building", "missing", "ready", "stale"]
        if self.building == document_id:
            state = "building"
        elif manifest is None:
            state = "missing"
        else:
            state = "ready" if self.current(document, manifest) else "stale"
        return IndexStatus(
            document_id=document_id,
            status=state,
            model=self.embedder.model,
            chunks=len(manifest.chunks) if manifest else 0,
        )

    def current(self, document: Document, manifest: IndexManifest) -> bool:
        if manifest.fingerprint != fingerprint(
            document, self.embedder.model, self.embedder.revision
        ):
            return False
        try:
            with self.index_path(manifest).open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest() == manifest.file_sha256
        except OSError:
            return False

    def index_path(self, manifest: IndexManifest) -> Path:
        return (
            self.data_dir
            / "papers"
            / manifest.document_id
            / "retrieval"
            / manifest.generation
            / "dense.faiss"
        )

    def require(self, document: Document) -> IndexManifest:
        manifest = self.indices.get(document.id)
        if manifest is None or not self.current(document, manifest):
            raise AppError("index_not_ready", "所选论文尚无有效索引，请先建立或更新检索索引。", 409)
        return manifest

    def build(self, document_id: str) -> IndexStatus:
        if not self.lock.acquire(blocking=False):
            raise AppError("index_busy", "正在建立另一个索引，请稍后重试。", 409)
        self.building = document_id
        try:
            document = self.document(document_id)
            existing = self.indices.get(document_id)
            if existing and self.current(document, existing):
                return IndexStatus(
                    document_id=document_id,
                    status="ready",
                    chunks=len(existing.chunks),
                    model=self.embedder.model,
                )
            chunks = build_chunks(document)
            if not chunks:
                raise AppError("no_indexable_text", "论文没有可检索的正文片段。", 422)
            abstract = document.parsed.metadata.abstract if document.parsed else None
            overview = (
                [abstract[start : start + 700] for start in range(0, min(len(abstract), 4000), 600)]
                if abstract
                else []
            )
            vectors = self.embedder.encode(overview + [c.text for c in chunks])
            if vectors.shape[0] != len(overview) + len(chunks) or vectors.shape[1] < 1:
                raise AppError("invalid_embeddings", "向量数量或维度不匹配。", 503)
            faiss.normalize_L2(vectors)
            index = faiss.IndexFlatIP(vectors.shape[1])
            index.add(np.ascontiguousarray(vectors, dtype="float32"))
            manifest = IndexManifest(
                document_id=document_id,
                generation=str(uuid4()),
                fingerprint=fingerprint(document, self.embedder.model, self.embedder.revision),
                model=self.embedder.model,
                revision=self.embedder.revision,
                dimension=vectors.shape[1],
                created_at=utc_now(),
                chunks=chunks,
                abstract_count=len(overview),
            )
            path = self.index_path(manifest)
            path.parent.mkdir(parents=True)
            faiss.write_index(index, str(path))
            if faiss.read_index(str(path)).ntotal != len(vectors):
                raise AppError("index_write_failed", "索引校验失败，请重试。", 503)
            with path.open("rb") as stream:
                manifest.file_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            # Do not activate a generation built from concurrently edited text.
            if (
                fingerprint(self.document(document_id), self.embedder.model, self.embedder.revision)
                != manifest.fingerprint
            ):
                raise AppError("document_changed", "建索引期间论文发生修改，请重试。", 409)
            self.indices.activate(manifest)
            return IndexStatus(
                document_id=document_id,
                status="ready",
                chunks=len(chunks),
                model=self.embedder.model,
            )
        finally:
            self.building = None
            self.lock.release()
