"""Subprocess entrypoint: bounded parsing, no API state or credentials required."""

import json
import sys
from pathlib import Path

from app.core.errors import AppError


def main() -> None:
    from app.parsing.docling_parser import parse_pdf

    source, destination = (Path(v) for v in sys.argv[1:3])
    destination.mkdir(parents=True, exist_ok=True)
    try:
        paper = parse_pdf(source, destination)
        (destination / "result.json").write_text(paper.model_dump_json(), encoding="utf-8")
    except AppError as error:
        (destination / "error.json").write_text(
            json.dumps({"code": error.code, "message": error.message}), encoding="utf-8"
        )
        raise SystemExit(2) from None
    except Exception as error:
        # Keep diagnostic evidence without leaking document text, paths or credentials.
        (destination / "error.json").write_text(
            json.dumps(
                {
                    "code": "parser_failed",
                    "message": "版面解析失败，请检查模型安装后重试。",
                    "exception_type": type(error).__name__,
                }
            ),
            encoding="utf-8",
        )
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
