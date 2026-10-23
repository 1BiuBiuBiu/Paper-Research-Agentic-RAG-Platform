import hashlib
import logging
import queue
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
from app.retrieval.bm25 import build_bm25, load_bm25, write_bm25
from app.retrieval.cache import IndexCache, LoadedIndex, index_size
from app.retrieval.chunks import build_chunks, fingerprint
from app.retrieval.embedding import Embedder

logger = logging.getLogger(__name__)


class IndexService:
    """Builds dense and sparse indexes, and keeps the queued build apart from parsing.

    A successful parse queues the index build in a background worker: the page can show
    "索引中" while the request returns, and an index failure is recorded separately from a
    parse failure so either side can be retried on its own.
    """

    def __init__(
        self,
        papers: PaperRepository,
        indices: IndexRepository,
        embedder: Embedder,
        cache: IndexCache | None = None,
    ) -> None:
        self.papers, self.indices, self.embedder = papers, indices, embedder
        self.data_dir = papers.path.parent
        self.cache = cache or IndexCache()
        self.lock = threading.Lock()
        self.building: str | None = None
        self._queue: queue.Queue[str] = queue.Queue()
        self._queued: set[str] = set()
        self._queue_lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def document(self, document_id: str) -> Document:
        document = self.papers.get(document_id)
        if document.status != "parsed" or not document.parsed:
            raise AppError("document_not_parsed", "所选论文尚未成功解析。", 409)
        return document

    def status(self, document_id: str) -> IndexStatus:
        document = self.document(document_id)
        manifest = self.indices.get(document_id)
        failure = self.indices.failure(document_id)
        state: Literal["building", "missing", "ready", "stale", "failed"]
        if self.building == document_id or document_id in self._queued:
            state = "building"
        elif manifest is not None and self.usable(document, manifest):
            state = "ready"
        elif failure is not None:
            state = "failed"
        elif manifest is None:
            state = "missing"
        else:
            state = "stale"
        code, message = failure if failure else (None, None)
        return IndexStatus(
            document_id=document_id,
            status=state,
            model=self.embedder.model,
            chunks=len(manifest.chunks) if manifest else 0,
            error_code=code if state == "failed" else None,
            message=message if state == "failed" else None,
        )

    def usable(self, document: Document, manifest: IndexManifest) -> bool:
        """Valid index: text fingerprints always count, file hashes only when not cached."""
        if not self._fingerprint_matches(document, manifest):
            return False
        if self.cache.peek(manifest.document_id, manifest.fingerprint) is not None:
            return True
        return self._files_match(manifest)

    def current(self, document: Document, manifest: IndexManifest) -> bool:
        return self._fingerprint_matches(document, manifest) and self._files_match(manifest)

    def _fingerprint_matches(self, document: Document, manifest: IndexManifest) -> bool:
        return manifest.fingerprint == fingerprint(
            document, self.embedder.model, self.embedder.revision
        )

    def _files_match(self, manifest: IndexManifest) -> bool:
        return self._matches(manifest) and all(
            self._digest(path) == expected
            for path, expected in (
                (self.index_path(manifest), manifest.file_sha256),
                (self.bm25_path(manifest), manifest.bm25_sha256),
            )
        )

    @staticmethod
    def _digest(path: Path) -> str:
        try:
            with path.open("rb") as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            return ""

    def _matches(self, manifest: IndexManifest) -> bool:
        """An index built before the sparse statistics existed cannot be reused."""
        return bool(manifest.file_sha256 and manifest.bm25_sha256)

    def index_path(self, manifest: IndexManifest) -> Path:
        return (
            self.data_dir
            / "papers"
            / manifest.document_id
            / "retrieval"
            / (manifest.generation)
            / "dense.faiss"
        )

    def bm25_path(self, manifest: IndexManifest) -> Path:
        return self.index_path(manifest).with_name("bm25.json")

    def pending_path(self, document_id: str) -> Path:
        return self.data_dir / "papers" / document_id / "retrieval" / "pending.json"

    def pending(self, document_id: str) -> bool:
        return self.pending_path(document_id).is_file()

    def mark_pending(self, document_id: str) -> None:
        path = self.pending_path(document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    def clear_pending(self, document_id: str) -> None:
        try:
            self.pending_path(document_id).unlink()
        except FileNotFoundError:
            pass

    def require(self, document: Document) -> IndexManifest:
        manifest = self.indices.get(document.id)
        if manifest is None or not self.usable(document, manifest):
            raise AppError("index_not_ready", "所选论文索引尚未就绪，请等待建立完成或重试。", 409)
        return manifest

    def library(self, document: Document) -> LoadedIndex:
        """Load this paper's dense vectors and BM25 statistics through the process cache."""
        manifest = self.indices.get(document.id)
        if manifest is None or not self.usable(document, manifest):
            raise AppError("index_not_ready", "所选论文索引尚未就绪，请等待建立完成或重试。", 409)
        return self.cache.get(
            manifest.document_id, manifest.fingerprint, lambda: self._load(manifest)
        )

    def _load(self, manifest: IndexManifest) -> LoadedIndex:
        try:
            index = faiss.read_index(str(self.index_path(manifest)))
            if index.ntotal != len(manifest.chunks) + manifest.abstract_count:
                raise ValueError("Index count mismatch")
            vectors = np.ascontiguousarray(index.reconstruct_n(0, index.ntotal), dtype="float32")
            terms = load_bm25(self.bm25_path(manifest))
            if len(terms["chunks"]) != len(manifest.chunks):
                raise ValueError("BM25 count mismatch")
        except (OSError, RuntimeError, ValueError):
            self.cache.discard(manifest.document_id)
            raise AppError("index_corrupt", "索引损坏，请重新建立索引。", 503) from None
        return LoadedIndex(
            document_id=manifest.document_id,
            fingerprint=manifest.fingerprint,
            vectors=vectors,
            abstract_count=manifest.abstract_count,
            terms=terms,
            size_bytes=index_size(vectors, terms),
        )

    def build(self, document_id: str) -> IndexStatus:
        """Build now; the manual rebuild entry keeps this synchronous for clear feedback."""
        if not self.lock.acquire(blocking=False):
            raise AppError("index_busy", "正在建立另一个索引，请稍后重试。", 409)
        try:
            return self._build(document_id)
        finally:
            self.lock.release()

    def queue_build(self, document_id: str) -> IndexStatus:
        """Queue an automatic build and return immediately with the "building" state."""
        document = self.document(document_id)
        manifest = self.indices.get(document_id)
        if manifest is not None and self.usable(document, manifest):
            self.clear_pending(document_id)
            return IndexStatus(
                document_id=document_id,
                status="ready",
                chunks=len(manifest.chunks),
                model=self.embedder.model,
            )
        with self._queue_lock:
            self.mark_pending(document_id)
            if document_id not in self._queued:
                self._queued.add(document_id)
                self._queue.put(document_id)
            self._start_worker()
        return IndexStatus(document_id=document_id, status="building", model=self.embedder.model)

    def resume_pending(self) -> int:
        """Rebuild interrupted builds and stale indexes once, when the process starts."""
        queued = 0
        offset = 0
        while True:
            rows, total = self.papers.list(offset=offset, limit=100, include_samples=True)
            for document in rows:
                if document.status != "parsed":
                    continue
                if self.pending(document.id) or self.status(document.id).status != "ready":
                    try:
                        self.queue_build(document.id)
                    except AppError:
                        continue
                    queued += 1
            offset += len(rows)
            if offset >= total or not rows:
                break
        return queued

    def _start_worker(self) -> None:
        if self._worker is not None:
            return
        self._worker = threading.Thread(target=self._run_worker, name="index-worker", daemon=True)
        self._worker.start()

    def _run_worker(self) -> None:
        while True:
            document_id = self._queue.get()
            try:
                with self.lock:
                    self._build(document_id)
            except AppError:
                pass  # The failure is recorded for this document; the queue keeps going.
            except Exception as error:  # pragma: no cover - defensive
                logger.error(
                    "index_worker_failure document_id=%s type=%s",
                    document_id,
                    type(error).__name__,
                )
            finally:
                with self._queue_lock:
                    self._queued.discard(document_id)
                self._queue.task_done()

    def _dense_reusable(self, document: Document, manifest: IndexManifest) -> bool:
        """True when only the sparse statistics are missing from a still-valid generation."""
        if manifest.bm25_sha256 or not manifest.file_sha256:
            return False
        if manifest.fingerprint != fingerprint(
            document, self.embedder.model, self.embedder.revision
        ):
            return False
        return self._digest(self.index_path(manifest)) == manifest.file_sha256

    def _backfill_sparse(self, manifest: IndexManifest) -> IndexStatus:
        """Write BM25 for an existing generation instead of re-encoding every chunk."""
        self.building = manifest.document_id
        try:
            write_bm25(self.bm25_path(manifest), build_bm25(manifest.chunks))
            manifest.bm25_sha256 = self._digest(self.bm25_path(manifest))
            if not manifest.bm25_sha256:
                raise AppError("index_write_failed", "稀疏索引校验失败，请重试。", 503)
            self.indices.activate(manifest)
            self.indices.clear_failure(manifest.document_id)
            self.clear_pending(manifest.document_id)
            self.cache.discard(manifest.document_id)
            return IndexStatus(
                document_id=manifest.document_id,
                status="ready",
                chunks=len(manifest.chunks),
                model=self.embedder.model,
            )
        except AppError as error:
            self.indices.fail(manifest.document_id, error.code, error.message, utc_now())
            raise
        finally:
            self.building = None

    def _build(self, document_id: str) -> IndexStatus:
        document = self.document(document_id)
        existing = self.indices.get(document_id)
        if existing is not None and self.usable(document, existing):
            self.indices.clear_failure(document_id)
            self.clear_pending(document_id)
            return IndexStatus(
                document_id=document_id,
                status="ready",
                chunks=len(existing.chunks),
                model=self.embedder.model,
            )
        if existing is not None and self._dense_reusable(document, existing):
            # The vectors are still valid; only the sparse statistics were never written.
            return self._backfill_sparse(existing)
        self.building = document_id
        try:
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
            path.parent.mkdir(parents=True, exist_ok=True)
            faiss.write_index(index, str(path))
            if faiss.read_index(str(path)).ntotal != len(vectors):
                raise AppError("index_write_failed", "索引校验失败，请重试。", 503)
            terms = build_bm25(chunks)
            write_bm25(self.bm25_path(manifest), terms)
            if len(load_bm25(self.bm25_path(manifest))["chunks"]) != len(chunks):
                raise AppError("index_write_failed", "稀疏索引校验失败，请重试。", 503)
            manifest.file_sha256 = self._digest(path)
            manifest.bm25_sha256 = self._digest(self.bm25_path(manifest))
            # Do not activate a generation built from concurrently edited text.
            if (
                fingerprint(self.document(document_id), self.embedder.model, self.embedder.revision)
                != manifest.fingerprint
            ):
                raise AppError("document_changed", "建索引期间论文发生修改，请重试。", 409)
            self.indices.activate(manifest)
            self.indices.clear_failure(document_id)
            self.clear_pending(document_id)
            self.cache.discard(document_id)
            return IndexStatus(
                document_id=document_id,
                status="ready",
                chunks=len(chunks),
                model=self.embedder.model,
            )
        except AppError as error:
            self.indices.fail(document_id, error.code, error.message, utc_now())
            raise
        except Exception as error:
            logger.error("index_failure document_id=%s type=%s", document_id, type(error).__name__)
            self.indices.fail(document_id, "index_failed", "建立索引失败，可稍后重试。", utc_now())
            raise AppError("index_failed", "建立索引失败，可稍后重试。", 503) from None
        finally:
            self.building = None
