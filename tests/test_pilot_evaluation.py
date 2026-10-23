from evaluation.pilot_headings import evaluate


def test_pilot_alignment_scope_false_positives_and_levels():
    gold = [
        dict(document_id="a", page=1, text="1 Intro", level=1, is_real_section_heading=True),
        dict(document_id="a", page=2, text="Other", level=2, is_real_section_heading=True),
    ]
    predicted = [
        dict(document_id="a", page=1, text="Intro", level=2),
        dict(document_id="a", page=1, text="Unknown", level=1),
    ]
    result = evaluate(gold, predicted)
    assert (result["tp"], result["fp"], result["fn"]) == (1, 1, 1)
    assert result["level_accuracy"] == 0
    assert result["macro_f1"] == 0.5


def test_pilot_alignment_counts_duplicate_prediction_as_false_positive():
    gold = [dict(document_id="a", page=1, text="Intro", level=1, is_real_section_heading=True)]
    predicted = [dict(document_id="a", page=1, text="Intro", level=1) for _ in range(2)]
    result = evaluate(gold, predicted)
    assert result["tp"] == 1
    assert result["fp"] == 1


def test_pilot_pending_review_is_excluded():
    gold = [
        dict(document_id="a", page=1, text="Intro", level=1, is_real_section_heading=True),
        dict(
            document_id="a",
            page=2,
            text="Appendix",
            level=None,
            is_real_section_heading=False,
            needs_review=True,
        ),
    ]
    predicted = [
        dict(document_id="a", page=1, text="Intro", level=1),
        dict(document_id="a", page=2, text="Appendix", level=1),
    ]
    assert evaluate(gold, predicted)["fp"] == 0
