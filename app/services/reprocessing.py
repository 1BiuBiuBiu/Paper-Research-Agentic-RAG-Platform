import hashlib
from pathlib import Path
from uuid import uuid4

from app.core.errors import AppError
from app.domain.papers import Document
from app.repositories.papers import PaperRepository
from app.services.ingestion import Parser
from app.services.parse_assets import prefix_assets


def reprocess(repo: PaperRepository, parser: Parser, data_dir: Path, identifier: str) -> Document:
    previous = repo.get(identifier)
    if previous.status != "parsed" or previous.parsed is None:
        raise AppError("document_not_parsed", "仅能重新解析已成功入库的论文。", 409)
    previous_parser = previous.parsed.parser
    directory = data_dir / "papers" / identifier
    original = directory / "original.pdf"
    verify_source(original, previous.sha256)
    assets = directory / str(uuid4())
    assets.mkdir()
    # Stage everything before touching the current record. Failure retains the working parse.
    paper = parser.parse(original, assets)
    verify_source(original, previous.sha256)
    prefix_assets(paper, assets.name)

    def activate(current: Document) -> None:
        old = current.parsed
        if old is None or old.parser != previous_parser:
            raise AppError("parse_changed", "解析期间记录发生变化，请重试。", 409)
        for field, provenance in old.metadata.provenance.items():
            if provenance.source == "manual":
                setattr(paper.metadata, field, getattr(old.metadata, field))
                paper.metadata.provenance[field] = provenance
        if (old.metadata.venue, old.metadata.venue_type, old.metadata.issn) == (
            paper.metadata.venue,
            paper.metadata.venue_type,
            paper.metadata.issn,
        ):
            paper.metric = old.metric
        history = directory / "history"
        history.mkdir(exist_ok=True)
        (history / f"before-reparse-{uuid4()}.json").write_text(
            current.model_dump_json(), encoding="utf-8"
        )
        current.parsed = paper

    return repo.update(identifier, activate)


def verify_source(path: Path, expected: str) -> None:
    if not path.is_file():
        raise AppError("original_missing", "原始 PDF 缺失。", 409)
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise AppError("source_mismatch", "原 PDF 与入库指纹不符，已停止更新。", 409)
