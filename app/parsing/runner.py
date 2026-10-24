import json
from pathlib import Path

from app.core.errors import AppError
from app.core.model_worker import (
    PersistentWorker,
    WorkerClient,
    WorkerStopped,
    WorkerTimedOut,
    isolated_environment,
)
from app.domain.papers import ParsedPaper


class DoclingRunner:
    def __init__(
        self, timeout: int, idle_seconds: float = 120, worker: WorkerClient | None = None
    ) -> None:
        self.timeout = timeout
        self.worker = worker or PersistentWorker(
            "app.parsing.worker",
            idle_seconds,
            environment=isolated_environment({"HF_HUB_DISABLE_XET": "1"}),
        )

    def parse(self, source: Path, assets: Path) -> ParsedPaper:
        assets.mkdir(parents=True, exist_ok=True)
        response = assets / ".worker-response.json"
        try:
            result = self.worker.request(
                {"source": str(source), "destination": str(assets)},
                response,
                self.timeout,
            )
        except WorkerTimedOut:
            raise AppError(
                "parse_timeout", "解析超时，请减少页数或先预下载解析模型。", 504
            ) from None
        except WorkerStopped:
            raise AppError(
                "parser_failed", "版面解析进程异常退出，请检查本地模型后重试。", 503
            ) from None
        if not result.get("ok"):
            if (assets / "error.json").exists():
                error = json.loads((assets / "error.json").read_text(encoding="utf-8"))
                raise AppError(error["code"], error["message"])
            raise AppError("parser_failed", "版面解析失败，请检查本地模型是否下载完整或重试。", 503)
        return ParsedPaper.model_validate_json((assets / "result.json").read_bytes())

    def close(self) -> None:
        self.worker.close()
