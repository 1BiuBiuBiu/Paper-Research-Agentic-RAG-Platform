"""Citation contract: declared sources must be allowed, bracketed markers must be declared."""

import pytest

from app.core.errors import AppError
from app.domain.qa import ModelAnswer
from app.services.agent_messages import validate_citations


def answer(text: str, citations: list[str]) -> ModelAnswer:
    return ModelAnswer(answer=text, citations=citations, insufficient_evidence=False)


def test_declared_sources_may_exceed_bracketed_markers():
    # "89.6（E1）" mentions E1 without brackets; declared still covers it.
    result = validate_citations(
        answer("89.6（E1）与 86.7（E2）。[E1]", ["E1", "E2"]), {"E1", "E2"}, True
    )
    assert result == {"E1", "E2"}


def test_a_bracketed_marker_must_be_declared():
    with pytest.raises(AppError):
        validate_citations(answer("value [E2]", ["E1"]), {"E1", "E2"}, True)


def test_a_declared_source_must_be_in_the_input():
    with pytest.raises(AppError):
        validate_citations(answer("value [E3]", ["E3"]), {"E1", "E2"}, True)


def test_an_answer_without_support_is_rejected_when_evidence_is_required():
    with pytest.raises(AppError):
        validate_citations(answer("no support", []), {"E1"}, True)
