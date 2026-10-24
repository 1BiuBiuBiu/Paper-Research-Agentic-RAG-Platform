"""Subprocess entrypoint: bounded parsing, no API state or credentials required."""

import json
from pathlib import Path

from app.core.errors import AppError
from app.core.model_worker import commands, respond


def main() -> None:
    from app.parsing.docling_parser import DoclingParser

    parser: DoclingParser | None = None
    for request in commands():
        source = Path(request["source"])
        destination = Path(request["destination"])
        response = Path(request["response_path"])
        destination.mkdir(parents=True, exist_ok=True)
        try:
            if parser is None:
                parser = DoclingParser()
            paper = parser.parse(source, destination)
            (destination / "result.json").write_text(paper.model_dump_json(), encoding="utf-8")
            respond(response, {"ok": True})
        except AppError as error:
            detail = {"code": error.code, "message": error.message}
            (destination / "error.json").write_text(
                json.dumps(detail, ensure_ascii=False), encoding="utf-8"
            )
            respond(response, {"ok": False, **detail})
        except Exception as error:
            # Keep diagnostic evidence without leaking document text, paths or credentials.
            detail = {
                "code": "parser_failed",
                "message": "版面解析失败，请检查模型安装后重试。",
                "exception_type": type(error).__name__,
            }
            (destination / "error.json").write_text(
                json.dumps(detail, ensure_ascii=False), encoding="utf-8"
            )
            respond(response, {"ok": False, **detail})


if __name__ == "__main__":
    main()
