import json

import pytest
from pydantic import ValidationError

from evaluation.evaluate import run_evaluation
from evaluation.metrics import Dataset, score


def base(**updates):
    data = dict(
        run_id="synthetic-unit",
        provenance={"source": "synthetic"},
        heading_candidates_complete=True,
        headings=[],
        retrieval=[],
        answers=[],
    )
    data.update(updates)
    return Dataset.model_validate(data)


def test_heading_false_positive_and_missed_heading():
    def heading(text, gold, predicted, level, predicted_level):
        return dict(
            document_id="a",
            text=text,
            page=1,
            is_real_section_heading=gold,
            predicted_heading=predicted,
            level=level,
            predicted_level=predicted_level,
            heading_type="body_section" if gold else "toc_entry",
        )

    result = score(
        base(
            headings=[
                heading("one", True, True, 2, 1),
                heading("toc", False, True, None, 1),
                heading("miss", True, False, 1, None),
            ]
        )
    )
    assert result["heading"]["f1"] == 0.5
    assert result["heading"]["level_accuracy"] == 0


def retrieval(complete=True):
    return dict(
        question_id="q",
        question="value?",
        evidence_complete=complete,
        gold_evidence=[
            dict(
                evidence_id=str(i),
                document_id="a",
                page=1,
                element_type="table",
                evidence_text=str(i),
                relevance=2,
            )
            for i in range(3)
        ],
        retrieved_evidence=[dict(rank=1, source_id="s", relevance=2, matched_gold_ids=["0", "1"])],
        ideal_relevances=[2, 2, 2],
    )


def test_recall_deduplicates_and_hit_is_separate():
    result = score(base(retrieval=[retrieval()]))["retrieval"]
    assert result["recall_at_5"] == pytest.approx(2 / 3)
    assert result["hit_at_5"] is None
    assert 0 < result["ndcg_at_5"] < 1
    result = score(base(retrieval=[retrieval(False)]))["retrieval"]
    assert result["hit_at_5"] == 1
    assert result["recall_at_5"] is None


def test_reject_unknown_match_and_missing_annotations():
    row = retrieval()
    row["retrieved_evidence"][0]["matched_gold_ids"] = ["unknown"]
    with pytest.raises(ValidationError):
        base(retrieval=[row])
    with pytest.raises(ValidationError):
        base()
    row = retrieval()
    del row["evidence_complete"]
    with pytest.raises(ValidationError):
        base(retrieval=[row])


def test_answer_and_report(tmp_path):
    answer = dict(
        question_id="q",
        question="value?",
        answer="42",
        answerable=True,
        answer_correct=True,
        required_components_complete=True,
        citations_support_answer=True,
        scope_leakage=False,
        correct_abstention=False,
        citations=[
            dict(citation_id="s1", supports_answer=True),
            dict(citation_id="s2", supports_answer=False),
        ],
    )
    data = base(answers=[answer])
    result = score(data)["answer"]
    assert result["grounded_answer_success_rate"] == 0
    assert result["citation_precision"] == 0.5
    assert result["scope_leakage_rate"] == 0
    source, dest = tmp_path / "dataset.json", tmp_path / "report.json"
    source.write_text(data.model_dump_json(), encoding="utf-8")
    report = run_evaluation(source, dest)
    assert len(report["dataset_sha256"]) == 64
    assert json.loads(dest.read_text(encoding="utf-8")) == report
    with pytest.raises(ValueError):
        run_evaluation(source, source)


def test_abstention_and_empty_citations():
    answer = dict(
        question_id="q",
        question="missing?",
        answer="unknown",
        answerable=False,
        answer_correct=True,
        required_components_complete=True,
        citations_support_answer=True,
        scope_leakage=False,
        correct_abstention=True,
        citations=[],
    )
    result = score(base(answers=[answer]))["answer"]
    assert result["grounded_answer_success_rate"] == 1
    assert result["citation_precision"] is None
    answer["scope_leakage"] = True
    assert score(base(answers=[answer]))["answer"]["grounded_answer_success_rate"] == 0
