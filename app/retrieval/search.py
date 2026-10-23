import re

import faiss
import numpy as np
from rank_bm25 import BM25Okapi

from app.core.errors import AppError
from app.domain.papers import Document
from app.domain.qa import Evidence, IndexManifest
from app.retrieval.indexing import IndexService

STOP = set(
    (
        "the of and to is a in what how does are on this paper study that which "
        "from with an for was be or by it as"
    ).split()
)


def tokens(text: str) -> list[str]:
    result: list[str] = []
    for word in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            result.extend(word[i : i + 2] for i in range(max(1, len(word) - 1)))
        elif word not in STOP:
            result.append(word)
    return result


def retrieve(
    service: IndexService,
    document: Document,
    manifest: IndexManifest,
    question: str,
    top_k: int = 5,
    query_vectors: np.ndarray | None = None,
    post_filter: bool = True,
    depth: int | None = None,
) -> list[Evidence]:
    """Load only the manifest belonging to the explicit scope, never a global top-k."""
    if manifest.document_id != document.id:
        raise AppError("scope_mismatch", "检索范围校验失败。", 409)
    # Recall depth must be able to feed the fused library-level pool, otherwise a larger
    # top_k silently returns nothing extra because dense/BM25 still stop at 20.
    recall_depth = max(top_k, depth or 0, 20)
    parts = [question[start : start + 700] for start in range(0, len(question), 600)]
    vectors = service.embedder.encode(parts) if query_vectors is None else query_vectors
    if vectors.shape != (len(parts), manifest.dimension):
        raise AppError("embedding_mismatch", "向量模型维度变化，请重新建立索引。", 409)
    vector = np.asarray(vectors.mean(axis=0, keepdims=True), dtype="float32")
    faiss.normalize_L2(vector)
    try:
        index = faiss.read_index(str(service.index_path(manifest)))
        if index.ntotal != len(manifest.chunks) + manifest.abstract_count:
            raise ValueError("Index count mismatch")
        similarities, identifiers = index.search(
            vector, min(recall_depth + manifest.abstract_count, index.ntotal)
        )
    except (RuntimeError, ValueError):
        raise AppError("index_corrupt", "索引损坏，请重新建立索引。", 503) from None
    # Abstract rows are considered first for orientation; only original body chunks are citable.
    dense: list[int] = []
    cosine: dict[int, float] = {}
    for identifier, score in zip(identifiers[0], similarities[0], strict=True):
        body_id = int(identifier) - manifest.abstract_count
        if 0 <= body_id < len(manifest.chunks):
            dense.append(body_id)
            cosine[body_id] = float(score)
    query_terms = tokens(question)
    sparse = BM25Okapi([tokens(c.text) or ["__empty__"] for c in manifest.chunks])
    sparse_scores = sparse.get_scores(query_terms)
    sparse_ids = [
        int(i) for i in np.argsort(sparse_scores)[::-1][:recall_depth] if sparse_scores[i] > 0
    ]
    fused: dict[int, float] = {}
    for ranking in (dense, sparse_ids):
        for rank, identifier in enumerate(ranking):
            fused[identifier] = fused.get(identifier, 0) + 1 / (60 + rank + 1)
    selected = sorted(fused, key=lambda i: fused[i], reverse=True)[:top_k]
    evidence: list[Evidence] = []
    for identifier in selected:
        chunk = manifest.chunks[identifier]
        lexical = bool(set(query_terms).intersection(tokens(chunk.text)))
        if post_filter and not lexical and cosine.get(identifier, -1) < 0.35:
            continue
        evidence.append(
            Evidence(
                **chunk.model_dump(),
                source_id=f"E{len(evidence) + 1}",
                document_id=document.id,
                title=document.parsed.metadata.title or document.filename
                if document.parsed
                else document.filename,
                filename=document.filename,
                score=cosine.get(identifier, 0),
                fusion_score=fused[identifier],
            )
        )
    return evidence
