"""Library-level retrieval over the union of the selected papers.

There is one index over the union: dense cosine and BM25 each return a global top k, and
fusion happens on those global ranks. A paper whose chunks all rank below the cut simply
contributes nothing — there is no per-paper depth and no per-paper floor.
"""

import faiss
import numpy as np

from app.core.errors import AppError
from app.domain.papers import Document
from app.domain.qa import Evidence, IndexManifest
from app.retrieval.bm25 import BM25Library, tokens
from app.retrieval.cache import LoadedIndex
from app.retrieval.indexing import IndexService

RRF_CONSTANT = 60


def _query_vector(
    service: IndexService,
    dimension: int,
    question: str,
    query_vectors: np.ndarray | None,
) -> np.ndarray:
    parts = [question[start : start + 700] for start in range(0, len(question), 600)]
    vectors = service.embedder.encode(parts) if query_vectors is None else query_vectors
    if vectors.shape != (len(parts), dimension):
        raise AppError("embedding_mismatch", "向量模型维度变化，请重新建立索引。", 409)
    vector = np.asarray(vectors.mean(axis=0, keepdims=True), dtype="float32")
    faiss.normalize_L2(vector)
    return vector


def _dense_top_k(
    libraries: list[LoadedIndex], vector: np.ndarray, k: int
) -> dict[tuple[int, int], float]:
    """Score every body chunk of every paper, then keep the global top k.

    Taking each paper's own top k before merging is exact: an item in the global top k
    can never be worse than position k inside its own paper.
    """
    found: dict[tuple[int, int], float] = {}
    for paper, library in enumerate(libraries):
        body = library.vectors[library.abstract_count :]
        if not len(body):
            continue
        scores = body @ vector[0]
        depth = min(k, len(scores))
        order = np.argsort(-scores, kind="stable")[:depth]
        for position in order:
            found[(paper, int(position))] = float(scores[int(position)])
    ranked = sorted(found.items(), key=lambda item: (-item[1], item[0]))[:k]
    return dict(ranked)


def library_candidates(
    service: IndexService,
    documents: list[Document],
    manifests: list[IndexManifest],
    question: str,
    k: int,
    query_vectors: np.ndarray | None = None,
) -> list[Evidence]:
    """One dense top k and one BM25 top k over the union, returned as a mergeable pool."""
    if not documents or not manifests:
        return []
    for document, manifest in zip(documents, manifests, strict=True):
        if manifest.document_id != document.id:
            raise AppError("scope_mismatch", "检索范围校验失败。", 409)
    # Release indexes of papers that are no longer in scope before loading this one.
    service.cache.retain({document.id for document in documents})
    libraries = [service.library(document) for document in documents]
    dimensions = {library.vectors.shape[1] for library in libraries}
    if len(dimensions) != 1:
        raise AppError("embedding_mismatch", "向量模型维度变化，请重新建立索引。", 409)
    vector = _query_vector(service, dimensions.pop(), question, query_vectors)
    dense = _dense_top_k(libraries, vector, k)
    sparse = BM25Library([library.terms for library in libraries]).top_k(question, k)
    evidence: list[Evidence] = []
    for paper, position in sorted(set(dense) | set(sparse)):
        document, manifest = documents[paper], manifests[paper]
        chunk = manifest.chunks[position]
        evidence.append(
            Evidence(
                **chunk.model_dump(),
                source_id="",
                document_id=document.id,
                title=document.parsed.metadata.title or document.filename
                if document.parsed
                else document.filename,
                filename=document.filename,
                score=dense.get((paper, position), 0.0),
                bm25_score=sparse.get((paper, position), 0.0),
            )
        )
    return evidence


def fuse(candidates: list[Evidence], k: int) -> list[Evidence]:
    """Reciprocal-rank fusion of the global dense and sparse rankings, then take k."""
    if not candidates or k <= 0:
        return []
    unique: dict[tuple[str, str], Evidence] = {}
    for item in candidates:
        unique.setdefault((item.document_id, item.id), item)
    pool = list(unique.values())
    dense = sorted(pool, key=lambda e: (-e.score, e.document_id, e.id))
    sparse = sorted(pool, key=lambda e: (-e.bm25_score, e.document_id, e.id))
    fused: dict[tuple[str, str], float] = {}
    for ranking in (dense, sparse):
        for rank, item in enumerate(ranking):
            key = (item.document_id, item.id)
            fused[key] = fused.get(key, 0.0) + 1.0 / (RRF_CONSTANT + rank + 1)
    ordered = [
        item.model_copy(update={"fusion_score": fused[(item.document_id, item.id)]})
        for item in sorted(pool, key=lambda e: (-fused[(e.document_id, e.id)], e.document_id, e.id))
    ]
    return ordered[:k]


def retrieve(
    service: IndexService,
    document: Document,
    manifest: IndexManifest,
    question: str,
    top_k: int = 5,
    query_vectors: np.ndarray | None = None,
    post_filter: bool = True,
) -> list[Evidence]:
    """Single-document convenience path: rank, fuse locally, then apply the lexical guard."""
    candidates = library_candidates(
        service, [document], [manifest], question, max(top_k, 20), query_vectors
    )
    selected = fuse(candidates, top_k)
    query_terms = tokens(question)
    evidence: list[Evidence] = []
    for item in selected:
        lexical = bool(set(query_terms).intersection(tokens(item.text)))
        if post_filter and not lexical and item.score < 0.35:
            continue
        evidence.append(item.model_copy(update={"source_id": f"E{len(evidence) + 1}"}))
    return evidence
