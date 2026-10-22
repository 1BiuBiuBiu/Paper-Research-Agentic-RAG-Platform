import hashlib
import json
from uuid import UUID, uuid5

from app.domain.papers import Document
from app.domain.qa import Chunk
from app.parsing.table_data import row_evidence

CHUNK_SIZE = 700
OVERLAP = 100
CHUNK_VERSION = "research-chunks-v2"


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
        for start in range(0, len(text), CHUNK_SIZE - OVERLAP):
            segment = text[start : start + CHUNK_SIZE]
            if len(segment.strip()) < 20:
                continue
            identifier = str(uuid5(UUID(document.id), f"{block.id}:{start}:{segment}"))
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
        for index, text in enumerate(row_evidence(table)):
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
