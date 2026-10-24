import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

import numpy as np

from app.core.config import Settings
from app.core.errors import AppError
from app.core.model_worker import (
    PersistentWorker,
    WorkerClient,
    WorkerStopped,
    WorkerTimedOut,
    isolated_environment,
)
from app.domain.qa import Evidence


class Reranker(Protocol):
    def rank(self, question: str, candidates: list[Evidence], top_n: int = 5) -> list[Evidence]: ...


class CrossEncoderReranker:
    def __init__(self, settings: Settings, worker: WorkerClient | None = None) -> None:
        self.settings = settings
        self.lock = threading.Lock()
        self.worker = worker or PersistentWorker(
            "app.retrieval.reranker_worker",
            settings.reranker_idle_seconds,
            environment=isolated_environment(
                {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
            ),
        )

    def ready(self) -> bool:
        from huggingface_hub import try_to_load_from_cache

        path = try_to_load_from_cache(
            self.settings.reranker_model,
            "model.safetensors",
            revision=self.settings.reranker_revision,
        )
        return isinstance(path, str) and Path(path).is_file()

    def rank(self, question: str, candidates: list[Evidence], top_n: int = 5) -> list[Evidence]:
        if not candidates:
            return []
        if len(candidates) > self.settings.candidates:
            raise AppError("rerank_limit", f"精排候选超过 {self.settings.candidates} 条上限。", 422)
        if not self.ready():
            raise AppError(
                "reranker_unavailable",
                "本地精排模型未就绪，请运行 python -m scripts.download_reranker。",
                503,
            )
        if not self.lock.acquire(blocking=False):
            raise AppError("reranker_busy", "本地精排正在处理另一请求，请稍后重试。", 409)
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="reranker-", dir=self.settings.data_dir) as temp:
                path = Path(temp)
                (path / "input.json").write_text(
                    json.dumps(
                        {
                            "model": self.settings.reranker_model,
                            "revision": self.settings.reranker_revision,
                            "pairs": [(question, c.text) for c in candidates],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                try:
                    result = self.worker.request(
                        {"directory": temp},
                        path / ".worker-response.json",
                        self.settings.reranker_timeout,
                    )
                except WorkerTimedOut:
                    raise AppError(
                        "reranker_timeout", "本地精排超时，请缩小范围后重试。", 504
                    ) from None
                except WorkerStopped:
                    raise AppError(
                        "reranker_unavailable", "本地精排进程异常退出，请检查模型文件。", 503
                    ) from None
                if not result.get("ok"):
                    raise AppError(
                        "reranker_unavailable", "本地精排模型加载或计算失败，请检查模型文件。", 503
                    )
                scores = np.load(path / "scores.npy", allow_pickle=False)
                if scores.shape != (len(candidates),) or not np.isfinite(scores).all():
                    raise AppError("invalid_reranker_output", "精排分值无效。", 503)
                indices = np.argsort(-scores, kind="stable")[:top_n]
                return [
                    candidates[int(i)].model_copy(update={"rerank_score": float(scores[i])})
                    for i in indices
                ]
        finally:
            self.lock.release()

    def close(self) -> None:
        self.worker.close()
