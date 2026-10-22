"""Re-extract from retained PDF blocks, preserving originals and human corrections."""

import hashlib
from uuid import uuid4

from app.core.errors import AppError
from app.domain.papers import Document, JournalMetric, ParsedPaper
from app.metadata.extract import extract_metadata
from app.parsing.cleaning import clean_blocks
from app.parsing.figures import associate_figures
from app.parsing.front_matter import repair_front_matter
from app.parsing.table_captions import associate_table_captions
from app.repositories.papers import PaperRepository


def repaired_paper(paper: ParsedPaper) -> ParsedPaper:
    result = paper.model_copy(deep=True)
    clean_blocks(result.blocks, result.pages)
    repair_front_matter(result.blocks, result.pages)
    metadata = extract_metadata(result.blocks)
    for field, source in result.metadata.provenance.items():
        if source.source == "manual":
            setattr(metadata, field, getattr(result.metadata, field))
            metadata.provenance[field] = source
    before = (result.metadata.venue_type, result.metadata.venue, result.metadata.issn)
    after = (metadata.venue_type, metadata.venue, metadata.issn)
    result.metadata = metadata
    if before != after:
        result.metric = JournalMetric(
            status="not_applicable"
            if metadata.venue_type in {"conference", "preprint"}
            else "missing"
        )
    associate_figures(result.figures, result.blocks)
    associate_table_captions(result.tables, result.blocks)
    # A rules refresh must not claim an old parse has new table/structure extraction.
    result.parser = result.parser.split("+rules-")[0] + "+rules-s1.2"
    return result


def refresh_extraction(repo: PaperRepository, document_id: str) -> Document:
    directory = repo.path.parent / "papers" / document_id
    original = directory / "original.pdf"

    def mutate(document: Document) -> None:
        if document.status != "parsed" or not document.parsed:
            raise AppError("document_not_parsed", "请先成功解析论文。", 409)
        if not original.is_file():
            raise AppError("original_missing", "原始 PDF 缺失，无法核对来源。", 409)
        with original.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != document.sha256:
            raise AppError("source_mismatch", "原始 PDF 与入库指纹不符，已停止更新。", 409)
        repaired = repaired_paper(document.parsed)
        backup = directory / "history"
        backup.mkdir(exist_ok=True)
        # Complete record snapshot, including manual edits, before activating repaired results.
        (backup / f"before-extraction-{uuid4()}.json").write_text(
            document.model_dump_json(), encoding="utf-8"
        )
        document.parsed = repaired

    return repo.update(document_id, mutate)
