"""Offline replay helpers: PDF lookup, gold grading and benchmark assembly (no models)."""

import json
from types import SimpleNamespace

from evaluation.pilot_headings import evaluate as heading_evaluate
from evaluation.replay_dataset import cached_parse, resolve_pdf
from evaluation.replay_scoring import grade_window, heading_candidates, ndcg


def item(document_id, page, text):
    return SimpleNamespace(document_id=document_id, page=page, text=text)


def test_resolve_pdf_accepts_browser_duplicate_name(tmp_path):
    canonical = tmp_path / "paper.pdf"
    canonical.write_bytes(b"%PDF-1.4 offline fixture")
    assert resolve_pdf("paper.pdf", tmp_path) == canonical
    assert resolve_pdf("paper(1).pdf", tmp_path) == canonical
    assert resolve_pdf("missing.pdf", tmp_path) is None


def test_cached_parse_reads_nested_result(tmp_path):
    result = tmp_path / "paper" / "result.json"
    result.parent.mkdir()
    result.write_text("{}", encoding="utf-8")
    assert cached_parse("paper", [tmp_path]) == result
    assert cached_parse("other", [tmp_path]) is None


def test_grade_window_marks_matches_once_and_background_once():
    labels = {"doc-uuid": "doc:1"}
    gold = [
        {"document_id": "doc:1", "page": 3, "evidence_text": "Top1 WAF 57.44"},
        {"document_id": "doc:1", "page": 3, "evidence_text": "Top2 WAF 55.24"},
    ]
    window = [
        item("doc-uuid", 3, "Top1 row: WAF 57.44"),
        item("doc-uuid", 3, "Top1 row again: WAF 57.44"),
        item("doc-uuid", 3, "unrelated sentence on the gold page"),
        item("doc-uuid", 9, "Top2 row with 55.24 on another page"),
    ]
    rows, found, ones = grade_window(window, gold, labels)
    assert found == 1
    assert ones == 2
    assert [row["relevance"] for row in rows] == [2, 1, 1, 0]
    assert rows[0]["matched_gold_ids"] == ["G1"]
    assert rows[1]["matched_gold_ids"] == []


def test_grade_window_ignores_other_documents_with_same_page():
    labels = {"a": "doc:1", "b": "doc:2"}
    gold = [{"document_id": "doc:2", "page": 3, "evidence_text": "Top1 WAF 57.44"}]
    rows, found, ones = grade_window([item("a", 3, "Top1 WAF 57.44")], gold, labels)
    assert (found, ones) == (0, 0)
    assert rows[0]["relevance"] == 0


def test_ndcg_is_one_for_perfect_and_none_without_ideal():
    assert ndcg([2, 1, 0], [2, 1], 3) == 1.0
    assert ndcg([0, 0], [2], 3) == 0.0
    assert ndcg([2], [], 3) is None


def test_heading_candidates_follow_the_scored_rows():
    report = heading_evaluate(
        [
            {
                "document_id": "doc:1",
                "page": 1,
                "text": "1 Introduction",
                "is_real_section_heading": True,
                "level": 1,
                "heading_type": "body_section",
            },
            {
                "document_id": "doc:1",
                "page": 1,
                "text": "Appendix",
                "is_real_section_heading": False,
                "level": None,
                "heading_type": "toc_entry",
                "needs_review": True,
            },
        ],
        [
            {"document_id": "doc:1", "page": 1, "text": "1 Introduction", "level": 1},
            {"document_id": "doc:1", "page": 2, "text": "Extra bold line", "level": 1},
        ],
    )
    candidates = heading_candidates(report)
    assert len(candidates) == 2
    assert candidates[0]["predicted_heading"] is True
    unmatched = [row for row in candidates if row["heading_type"] == "unmatched_prediction"]
    assert len(unmatched) == 1
    assert unmatched[0]["is_real_section_heading"] is False
    assert json.dumps(candidates, ensure_ascii=False)
