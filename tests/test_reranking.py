import json
import subprocess

import numpy as np
import pytest

from app.core.errors import AppError
from app.domain.qa import Evidence
from app.retrieval.reranking import CrossEncoderReranker


def evidence():
    return [
        Evidence(
            id=str(i),
            page=1,
            text="Synthetic text",
            block_ids=[],
            boxes=[],
            source_id=f"E{i}",
            document_id="fixture",
            title="fixture",
            filename="none",
            score=1,
        )
        for i in range(2)
    ]


def test_reranker_worker_is_offline_no_credentials_and_maps_scores(settings, monkeypatch):
    ranker = CrossEncoderReranker(settings)
    monkeypatch.setattr(ranker, "ready", lambda: True)
    monkeypatch.setenv("TAVILY_API_KEY", "synthetic-secret")
    from pathlib import Path

    def run(command, **kwargs):
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert "TAVILY_API_KEY" not in kwargs["env"]
        path = Path(command[-1])
        payload = json.loads((path / "input.json").read_text(encoding="utf-8"))
        assert len(payload["pairs"]) == 2 and payload["revision"] == settings.reranker_revision
        np.save(path / "scores.npy", np.array([-2, 5], dtype="float32"))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    ranked = ranker.rank("synthetic question", evidence())
    assert [e.id for e in ranked] == ["1", "0"]
    assert ranked[0].rerank_score == 5
    assert not list(settings.data_dir.glob("reranker-*"))


def test_missing_model_and_timeout_fail_without_silent_fallback(settings, monkeypatch):
    ranker = CrossEncoderReranker(settings)
    monkeypatch.setattr(ranker, "ready", lambda: False)
    with pytest.raises(AppError, match="未就绪"):
        ranker.rank("q", evidence())
    monkeypatch.setattr(ranker, "ready", lambda: True)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("offline", 120)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(AppError, match="超时"):
        ranker.rank("q", evidence())
    assert not ranker.lock.locked()


def test_rerank_candidate_limit_comes_from_settings(settings, monkeypatch):
    ranker = CrossEncoderReranker(settings)
    monkeypatch.setattr(ranker, "ready", lambda: True)
    oversized = [
        item.model_copy(update={"id": str(index), "source_id": f"E{index}"})
        for index, item in enumerate(evidence() * 31)
    ]
    assert len(oversized) > settings.candidates
    with pytest.raises(AppError, match=f"超过 {settings.candidates} 条上限"):
        ranker.rank("q", oversized)
