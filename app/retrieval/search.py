import math
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
RRF_CONSTANT = 60


def tokens(text: str) -> list[str]:
    result: list[str] = []
    for word in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            result.extend(word[i : i + 2] for i in range(max(1, len(word) - 1)))
        elif word not in STOP:
            result.append(word)
    return result


def rank_candidates(
    service: IndexService,
    document: Document,
    manifest: IndexManifest,
    question: str,
    depth: int = 20,
    query_vectors: np.ndarray | None = None,
) -> list[Evidence]:
    """Rank one document's chunks, keeping dense and BM25 scores for a later global fusion.

    Scores are attached unchanged: cosine is comparable across documents because every
    document uses the same fixed embedding model, BM25 is not, so fusion happens by rank.
    """
    if manifest.document_id != document.id:
        raise AppError("scope_mismatch", "检索范围校验失败。", 409)
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
            vector, min(depth + manifest.abstract_count, index.ntotal)
        )
    except (RuntimeError, ValueError):
        raise AppError("index_corrupt", "索引损坏，请重新建立索引。", 503) from None
    # Abstract rows are considered first for orientation; only original body chunks are citable.
    cosine: dict[int, float] = {}
    for identifier, score in zip(identifiers[0], similarities[0], strict=True):
        body_id = int(identifier) - manifest.abstract_count
        if 0 <= body_id < len(manifest.chunks):
            cosine[body_id] = float(score)
    query_terms = tokens(question)
    sparse = BM25Okapi([tokens(c.text) or ["__empty__"] for c in manifest.chunks])
    sparse_scores = sparse.get_scores(query_terms)
    sparse_ids = [int(i) for i in np.argsort(sparse_scores)[::-1][:depth] if sparse_scores[i] > 0]
    evidence: list[Evidence] = []
    for identifier in sorted(set(cosine) | set(sparse_ids)):
        chunk = manifest.chunks[identifier]
        evidence.append(
            Evidence(
                **chunk.model_dump(),
                source_id="",
                document_id=document.id,
                title=document.parsed.metadata.title or document.filename
                if document.parsed
                else document.filename,
                filename=document.filename,
                score=cosine.get(identifier, 0.0),
                bm25_score=float(sparse_scores[identifier]),
            )
        )
    return evidence


def fuse(
    candidates: list[Evidence],
    k: int,
    floor: int = 0,
    required_documents: list[str] | tuple[str, ...] = (),
) -> list[Evidence]:
    """Reciprocal-rank fusion across every selected document, then take k.

    Dense and sparse rankings are fused by global rank, not by each document's own rank,
    so a document cannot claim a slot just by being the best of a weak paper.
    """
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
    selected = ordered[:k]
    if floor > 0 and required_documents:
        by_document: dict[str, list[Evidence]] = {}
        for item in ordered:
            by_document.setdefault(item.document_id, []).append(item)
        targets = {
            document_id: min(floor, len(by_document.get(document_id, [])))
            for document_id in required_documents
        }
        counts: dict[str, int] = {}
        for item in selected:
            counts[item.document_id] = counts.get(item.document_id, 0) + 1
        chosen = {(item.document_id, item.id) for item in selected}
        for document_id, target in targets.items():
            for item in by_document.get(document_id, []):
                if counts.get(document_id, 0) >= target:
                    break
                key = (item.document_id, item.id)
                if key in chosen:
                    continue
                if len(selected) < k:
                    selected.append(item)
                else:
                    victim = next(
                        (
                            index
                            for index in range(len(selected) - 1, -1, -1)
                            if counts[selected[index].document_id]
                            > targets.get(selected[index].document_id, 1)
                        ),
                        None,
                    )
                    if victim is None:
                        break
                    counts[selected[victim].document_id] -= 1
                    chosen.discard((selected[victim].document_id, selected[victim].id))
                    selected[victim] = item
                chosen.add(key)
                counts[document_id] = counts.get(document_id, 0) + 1
        selected.sort(key=lambda e: (-e.fusion_score, e.document_id, e.id))
    return selected


def retrieval_depth(candidates: int, documents: int, minimum: int = 20) -> int:
    """Per-document recall depth needed to fill a library-level pool of `candidates`."""
    if documents <= 0:
        return max(minimum, 1)
    needed = math.ceil(candidates / documents)
    return max(minimum, min(candidates, needed))


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
    """Single-document convenience path: rank, fuse locally, then apply the lexical guard."""
    candidates = rank_candidates(
        service,
        document,
        manifest,
        question,
        depth=depth or max(top_k, 20),
        query_vectors=query_vectors,
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
