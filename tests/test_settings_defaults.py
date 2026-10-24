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
    settings = Settings(data_dir=Path("unused"))
    assert settings.auto_index is True
    assert settings.docling_idle_seconds == 120
    assert settings.embedding_idle_seconds == 300
    assert settings.reranker_idle_seconds == 300
