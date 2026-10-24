import json
from pathlib import Path

import numpy as np
import pytest

from app.core.errors import AppError
from app.core.model_worker import WorkerTimedOut
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


class ScoreWorker:
    def request(self, payload, response_path, timeout):
        path = Path(payload["directory"])
        body = json.loads((path / "input.json").read_text(encoding="utf-8"))
        assert timeout > 0
        assert response_path.parent == path
        assert len(body["pairs"]) == 2
        assert body["revision"]
        np.save(path / "scores.npy", np.array([-2, 5], dtype="float32"))
        return {"ok": True}

    def close(self):
        pass


class TimeoutWorker:
    def request(self, payload, response_path, timeout):
        raise WorkerTimedOut

    def close(self):
        pass


def test_reranker_worker_maps_scores(settings, monkeypatch):
    ranker = CrossEncoderReranker(settings, ScoreWorker())
    monkeypatch.setattr(ranker, "ready", lambda: True)
    ranked = ranker.rank("synthetic question", evidence())
    assert [e.id for e in ranked] == ["1", "0"]
    assert ranked[0].rerank_score == 5
    assert not list(settings.data_dir.glob("reranker-*"))


def test_missing_model_and_timeout_fail_without_silent_fallback(settings, monkeypatch):
    ranker = CrossEncoderReranker(settings, TimeoutWorker())
    monkeypatch.setattr(ranker, "ready", lambda: False)
    with pytest.raises(AppError, match="未就绪"):
        ranker.rank("q", evidence())
    monkeypatch.setattr(ranker, "ready", lambda: True)
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
