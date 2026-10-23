from app.domain.qa import Evidence
from app.retrieval.search import fuse


def row(document_id: str, identifier: str, score: float, bm25: float = 0.0) -> Evidence:
    return Evidence(
        id=identifier,
        page=1,
        text=f"{document_id} {identifier}",
        block_ids=[],
        boxes=[],
        source_id="",
        document_id=document_id,
        title=document_id,
        filename=f"{document_id}.pdf",
        score=score,
        bm25_score=bm25,
    )


def test_global_ranking_does_not_reserve_a_slot_per_paper():
    candidates = [row("a", str(index), 0.9 - index / 100) for index in range(4)]
    candidates += [row("b", str(index), 0.2 - index / 100) for index in range(4)]
    selected = fuse(candidates, 3)
    assert [item.document_id for item in selected] == ["a", "a", "a"]


def test_a_weak_paper_contributes_nothing():
    candidates = [row("a", str(index), 0.9 - index / 100) for index in range(3)]
    candidates += [row("b", "only", 0.05)]
    selected = fuse(candidates, 3)
    assert len(selected) == 3 and all(item.document_id == "a" for item in selected)


def test_fusion_prefers_rows_ranked_by_both_retrievers():
    candidates = [
        row("a", "both", 0.95, 9.0),
        row("a", "dense", 0.9, 0.0),
        row("a", "sparse", 0.1, 8.0),
    ]
    assert fuse(candidates, 1)[0].id == "both"


def test_fusion_is_deterministic_for_equal_scores():
    first = [row("a", "1", 0.5), row("a", "2", 0.5)]
    second = list(reversed(first))
    assert [e.id for e in fuse(first, 2)] == [e.id for e in fuse(second, 2)]


def test_fused_pool_never_exceeds_the_global_cap():
    candidates = [row("a", str(index), 0.9 - index / 1000) for index in range(120)]
    assert len(fuse(candidates, 60)) == 60
