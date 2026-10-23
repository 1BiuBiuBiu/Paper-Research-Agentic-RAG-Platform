"""Attach the paragraph that cites a table or figure to that table/figure unit.

Retrieval keeps its units: paragraph chunks and one chunk per table row. Captions stay
bound to their table/figure — a paragraph never receives a caption on its own, because a
caption without its table or figure is not evidence of anything. Before the final
generation call the ledger is assembled once, which is cheaper and safer than rebuilding
the index:

- a table row chunk gets the paragraph citing its table, so the claim and the numbers sit
  in one evidence item; when that paragraph was retrieved too, the two items are merged and
  the paragraph item is dropped instead of repeating its text;
- a figure caption chunk gets the paragraph that cites its figure, for the same reason;
- a caption longer than the row prefix keeps its remainder, so the assembled item carries
  the full caption even though the index only stores its first 300 characters;
- anything else, including a paragraph naming a table/figure that is not in the ledger, is
  left exactly as retrieved.

Nothing here is truncated: the citing paragraph is attached in full and the caption
remainder is appended. The only byte limit left is the turn's own context budget, applied
later by `context()` so the model request stays inside its cap.

No extra retrieval, embedding or model call happens here, and the judge still sees the
atomic rows so the grading prompt does not grow.
"""

from dataclasses import dataclass

from app.domain.papers import Document, Figure
from app.domain.qa import Chunk, Evidence, IndexManifest
from app.parsing.table_data import CAPTION_PREFIX_MAX


@dataclass(frozen=True)
class Attachment:
    before: str = ""
    after: str = ""
    drop: tuple[str, str] | None = None

    def apply(self, item: Evidence) -> Evidence:
        text = "\n".join(part for part in (self.before, item.text, self.after) if part)
        return item.model_copy(update={"text": text})


class _Paper:
    """Indexes one parsed paper for the reference lookups; nothing here hits the disk."""

    def __init__(self, document: Document, manifest: IndexManifest) -> None:
        parsed = document.parsed
        assert parsed is not None
        tables = parsed.tables or []
        figures = parsed.figures or []
        self.tables = {table.id: table for table in tables}
        self.figure_captions: dict[str, Figure] = {
            block_id: figure for figure in figures for block_id in figure.caption_block_ids
        }
        self.block_chunk: dict[str, Chunk] = {}
        for chunk in manifest.chunks:
            for block_id in chunk.block_ids:
                self.block_chunk.setdefault(block_id, chunk)

    def citing_chunk(self, mention_block_ids: list[str]) -> Chunk | None:
        for block_id in mention_block_ids:
            chunk = self.block_chunk.get(block_id)
            if chunk is not None:
                return chunk
        return None


def _citing(citing: Chunk | None) -> Attachment:
    if citing is None:
        return Attachment()
    return Attachment(before=f"引用该图表的原文（第 {citing.page} 页）：{citing.text}")


def _table_attachment(
    item: Evidence,
    paper: _Paper,
    present: set[tuple[str, str]],
    attached: set[tuple[str, str]],
) -> Attachment:
    if not item.element_id or item.element_type != "table":
        return Attachment()
    table = paper.tables.get(item.element_id)
    if table is None:
        return Attachment()
    after = ""
    remainder = table.caption[CAPTION_PREFIX_MAX:].strip()
    caption_key = (item.document_id, f"caption:{table.id}")
    if remainder and caption_key not in attached:
        # One row of the table carries the full caption once, not every row.
        attached.add(caption_key)
        after = f"表注（续）：{remainder}"
    citing = paper.citing_chunk(table.mention_block_ids)
    if citing is None or citing.id == item.id:
        return Attachment(after=after)
    key = (item.document_id, citing.id)
    if key in attached:
        return Attachment(after=after)
    attached.add(key)
    referenced = _citing(citing)
    if key in present:
        # The paragraph is already evidence: move it into this item instead of repeating it.
        return Attachment(before=referenced.before, after=after, drop=key)
    return Attachment(before=referenced.before, after=after)


def _figure_attachment(
    item: Evidence,
    paper: _Paper,
    present: set[tuple[str, str]],
    attached: set[tuple[str, str]],
) -> Attachment:
    figure = next(
        (
            paper.figure_captions[block_id]
            for block_id in item.block_ids
            if block_id in paper.figure_captions
        ),
        None,
    )
    if figure is None:
        return Attachment()
    citing = paper.citing_chunk(figure.mention_block_ids)
    if citing is None or citing.id == item.id:
        return Attachment()
    key = (item.document_id, citing.id)
    if key in attached:
        return Attachment()
    attached.add(key)
    referenced = _citing(citing)
    if key in present:
        return Attachment(before=referenced.before, drop=key)
    return referenced


def attach_references(
    evidence: list[Evidence],
    documents: dict[str, Document],
    manifests: dict[str, IndexManifest],
) -> list[Evidence]:
    """Return the same ledger with each table/figure unit's citing paragraph attached once."""
    papers = {
        document_id: _Paper(document, manifests[document_id])
        for document_id, document in documents.items()
        if document_id in manifests and document.parsed is not None
    }
    present = {(item.document_id, item.id) for item in evidence}
    attached: set[tuple[str, str]] = set()
    dropped: set[tuple[str, str]] = set()
    merged: list[Evidence] = []
    for item in evidence:
        paper = papers.get(item.document_id)
        attachment = Attachment()
        if paper is not None:
            attachment = _table_attachment(item, paper, present, attached)
            if not (attachment.before or attachment.after):
                attachment = _figure_attachment(item, paper, present, attached)
        if attachment.before or attachment.after:
            item = attachment.apply(item)
            if attachment.drop is not None:
                dropped.add(attachment.drop)
        merged.append(item)
    return [item for item in merged if (item.document_id, item.id) not in dropped]
