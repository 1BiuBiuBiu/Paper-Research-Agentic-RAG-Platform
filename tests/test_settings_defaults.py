"""The retrieval and budget numbers the user agreed to, pinned so they cannot drift silently."""

from pathlib import Path

from app.core.config import Settings


def test_default_funnel_and_call_budget():
    settings = Settings(data_dir=Path("unused"))
    # One library over the selected papers, then the fused pool, then ten rows to the judge.
    assert settings.candidates == 60
    assert settings.rerank_top_n == 10
    # Grade ① + grade ② + generation; the reranker-only translation is not counted.
    assert settings.agent_max_calls == 3


def test_auto_index_is_on_by_default():
    assert Settings(data_dir=Path("unused")).auto_index is True
