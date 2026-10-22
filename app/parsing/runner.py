import json
import os
import subprocess
import sys
from pathlib import Path

from app.core.errors import AppError
from app.domain.papers import ParsedPaper


class DoclingRunner:
    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    def parse(self, source: Path, assets: Path) -> ParsedPaper:
        assets.mkdir(parents=True, exist_ok=True)
        # Prevent future provider credentials from being inherited by the PDF worker.
        env = {
            key: value
            for key, value in os.environ.items()
            if not any(word in key.upper() for word in ("API_KEY", "MODEL_KEY", "SECRET", "TOKEN"))
        }
        # Direct HTTPS downloads avoid stalled Xet transfers on some Windows networks.
        env.setdefault("HF_HUB_DISABLE_XET", "1")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "app.parsing.worker", str(source), str(assets)],
                cwd=Path(__file__).resolve().parents[2],
                env=env,
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise AppError(
                "parse_timeout", "解析超时，请减少页数或先预下载解析模型。", 504
            ) from None
        if result.returncode:
            if (assets / "error.json").exists():
                error = json.loads((assets / "error.json").read_text(encoding="utf-8"))
                raise AppError(error["code"], error["message"])
            raise AppError("parser_failed", "版面解析失败，请检查本地模型是否下载完整或重试。", 503)
        return ParsedPaper.model_validate_json((assets / "result.json").read_bytes())
