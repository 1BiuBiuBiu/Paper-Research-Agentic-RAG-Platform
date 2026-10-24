import hashlib
import json
import re
from uuid import UUID, uuid5

from app.domain.papers import Block, Document, PaperTable
from app.domain.qa import Chunk
from app.parsing.table_data import row_evidence

CHUNK_SIZE = 800
# Overlap is one whole sentence, and it must stay under 150 characters.
OVERLAP_MAX = 149
CHUNK_VERSION = "research-chunks-v5"

_SENTENCE = re.compile(r"(?<=[.!?。！？；;])\s+")


def table_section(table: PaperTable, blocks: list[Block]) -> str:
    """Section of the paragraph that references the table, falling back to its caption position."""
    ordering = {block.id: block.order for block in blocks}
    references = table.mention_block_ids or table.caption_block_ids
    anchors = [ordering[item] for item in references if item in ordering]
    if not anchors:
        return ""
    limit = min(anchors)
    headings = [
        block
        for block in blocks
        if block.kind == "section_header" and block.heading_level and block.order < limit
    ]
    if not headings:
        return ""
    path = [headings[-1].text.strip()]
    level = headings[-1].heading_level or 1
    for block in reversed(headings[:-1]):
        if (block.heading_level or 1) < level:
            path.append(block.text.strip())
            level = block.heading_level or 1
            if level <= 1:
                break
    return " / ".join(reversed(path))[:120]


def fingerprint(document: Document, model: str, revision: str) -> str:
    if not document.parsed:
        return ""
    body = {
        "parser": document.parsed.parser,
        "model": model,
        "revision": revision,
        "chunk_version": CHUNK_VERSION,
        "blocks": [b.model_dump() for b in document.parsed.blocks],
        "abstract": document.parsed.metadata.abstract,
        "tables": [t.model_dump() for t in document.parsed.tables],
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def sentences(text: str) -> list[str]:
    """Split at sentence boundaries; an oversized sentence is hard-split at CHUNK_SIZE."""
    parts = [part.strip() for part in _SENTENCE.split(text) if part.strip()]
    result: list[str] = []
    for part in parts:
        while len(part) > CHUNK_SIZE:
            result.append(part[:CHUNK_SIZE])
            part = part[CHUNK_SIZE:]
        if part:
            result.append(part)
    return result


def windows(text: str) -> list[str]:
    """Pack sentences into chunks of at most CHUNK_SIZE, overlapping one short sentence."""
    parts = sentences(text)
    if not parts:
        return []
    chunks: list[str] = []
    index = 0
    while index < len(parts):
        previous = parts[index - 1] if chunks and len(parts[index - 1]) < OVERLAP_MAX else None
        reserve = len(previous) + 1 if previous else 0
        end, size = index, 0
        while end < len(parts):
            add = len(parts[end]) + (1 if end > index else 0)
            if size + add + reserve > CHUNK_SIZE:
                break
            size += add
            end += 1
        if end == index:
            # Even one sentence does not fit next to the overlap: drop the overlap.
            reserve, end = 0, index + 1
        chunk = " ".join(parts[index:end])
        if reserve:
            chunk = f"{previous} {chunk}"
        chunks.append(chunk)
        index = end
    return chunks


def build_chunks(document: Document) -> list[Chunk]:
    if not document.parsed:
        return []
    chunks: list[Chunk] = []
    for block in document.parsed.blocks:
        if block.excluded_reason or block.kind in {"reference", "title"}:
            continue
        text = block.text.strip()
        if len(text) < 20:
            continue
        for index, segment in enumerate(windows(text)):
            if len(segment.strip()) < 20:
                continue
            identifier = str(uuid5(UUID(document.id), f"{block.id}:{index}:{segment}"))
            chunks.append(
                Chunk(
                    id=identifier,
                    page=block.page,
                    text=segment,
                    block_ids=[block.id],
                    boxes=[block.bbox],
                )
            )
    for table in document.parsed.tables:
        section = table_section(table, document.parsed.blocks)
        for index, text in enumerate(row_evidence(table, section)):
            chunks.append(
                Chunk(
                    id=str(uuid5(UUID(document.id), f"{table.source_ref}:{index}:{text}")),
                    page=table.page,
                    text=text,
                    block_ids=table.caption_block_ids,
                    boxes=[table.bbox],
                    element_type="table",
                    element_id=table.id,
                )
            )
    return chunks
