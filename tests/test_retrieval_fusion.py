import pytest

from app.domain.qa import Evidence
from app.retrieval.search import fuse, retrieval_depth


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


def test_floor_guarantees_each_document_even_when_scores_are_low():
    candidates = [row("a", str(index), 0.9 - index / 100) for index in range(6)]
    candidates += [row("b", "only", 0.05)]
    selected = fuse(candidates, 4, floor=2, required_documents=["a", "b"])
    counts = {document: [e.document_id for e in selected].count(document) for document in "ab"}
    assert len(selected) == 4 and counts["a"] >= 2 and counts["b"] == 1


def test_floor_adds_rows_when_pool_is_not_full():
    candidates = [row("a", str(index), 0.9 - index / 100) for index in range(3)]
    candidates += [row("b", str(index), 0.2 - index / 100) for index in range(3)]
    selected = fuse(candidates, 10, floor=2, required_documents=["a", "b"])
    assert len(selected) == 6


def test_fusion_is_deterministic_for_equal_scores():
    first = [row("a", "1", 0.5), row("a", "2", 0.5)]
    second = list(reversed(first))
    assert [e.id for e in fuse(first, 2)] == [e.id for e in fuse(second, 2)]


@pytest.mark.parametrize(
    "candidates,documents,minimum,expected",
    [(60, 1, 20, 60), (60, 2, 20, 30), (60, 5, 20, 20), (60, 100, 20, 20)],
)
def test_retrieval_depth_fills_the_library_pool(candidates, documents, minimum, expected):
    assert retrieval_depth(candidates, documents, minimum) == expected
