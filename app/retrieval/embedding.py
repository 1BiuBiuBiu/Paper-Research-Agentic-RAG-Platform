import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from app.core.config import Settings
from app.core.errors import AppError
from app.core.model_worker import (
    PersistentWorker,
    WorkerClient,
    WorkerStopped,
    WorkerTimedOut,
    isolated_environment,
)


class Embedder(Protocol):
    model: str
    revision: str

    def encode(self, texts: list[str]) -> NDArray[np.float32]: ...


class BGEEmbedder:
    def __init__(self, settings: Settings, worker: WorkerClient | None = None) -> None:
        self.settings = settings
        self.model = settings.embedding_model
        self.revision = settings.embedding_revision
        self.lock = threading.Lock()
        self.worker = worker or PersistentWorker(
            "app.retrieval.embedding_worker",
            settings.embedding_idle_seconds,
            environment=isolated_environment(
                {"HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1"}
            ),
        )

    def encode(self, texts: list[str]) -> NDArray[np.float32]:
        if not self.lock.acquire(blocking=False):
            raise AppError("embedding_busy", "正在进行另一项向量计算，请稍后重试。", 409)
        try:
            self.settings.data_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="embedding-", dir=self.settings.data_dir) as temporary:
                path = Path(temporary)
                (path / "input.json").write_text(
                    json.dumps(
                        {
                            "model": self.model,
                            "revision": self.revision,
                            "texts": texts,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                try:
                    result = self.worker.request(
                        {"directory": temporary},
                        path / ".worker-response.json",
                        self.settings.index_timeout,
                    )
                except WorkerTimedOut:
                    raise AppError(
                        "embedding_timeout", "本地向量计算超时，请缩小文档或重试。", 504
                    ) from None
                except WorkerStopped:
                    raise AppError(
                        "embedding_unavailable", "本地 BGE-M3 进程异常退出，请检查模型文件。", 503
                    ) from None
                if not result.get("ok"):
                    raise AppError(
                        "embedding_unavailable",
                        "本地 BGE-M3 未就绪，请先运行模型下载命令或检查模型文件。",
                        503,
                    )
                vectors = np.load(path / "vectors.npy", allow_pickle=False)
                if (
                    vectors.ndim != 2
                    or len(vectors) != len(texts)
                    or not np.isfinite(vectors).all()
                ):
                    raise AppError("invalid_embeddings", "向量输出无效，请重新建立索引。", 503)
                return np.asarray(vectors, dtype="float32")
        finally:
            self.lock.release()

    def close(self) -> None:
        self.worker.close()
