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
- anything else, including a paragraph naming a table/figure that is not in the ledger, is
  left exactly as retrieved.

No extra retrieval, embedding or model call happens here, and the judge still sees the
atomic rows so the grading prompt does not grow.
"""

from app.domain.papers import Document, Figure
from app.domain.qa import Chunk, Evidence, IndexManifest

ATTACH_MAX = 320


def _snippet(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    cut = collapsed[:limit]
    for mark in ("。", "！", "？", "；", ". ", "; "):
        position = cut.rfind(mark)
        if position >= limit // 2:
            return cut[: position + len(mark)].strip()
    return cut.rstrip() + "…"


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


def _citation_prefix(page: int, excerpt: bool) -> str:
    suffix = "，节选" if excerpt else ""
    return f"引用该图表的原文（第 {page} 页{suffix}）："


def _table_addition(
    item: Evidence,
    paper: _Paper,
    present: set[tuple[str, str]],
    attached: set[tuple[str, str]],
    limit: int,
) -> tuple[str, tuple[str, str] | None]:
    if not item.element_id or item.element_type != "table":
        return "", None
    table = paper.tables.get(item.element_id)
    if table is None:
        return "", None
    chunk = paper.citing_chunk(table.mention_block_ids)
    if chunk is None or chunk.id == item.id:
        return "", None
    key = (item.document_id, chunk.id)
    if key in attached:
        return "", None
    attached.add(key)
    if key in present:
        # The paragraph is already evidence: move it into this item instead of repeating it.
        return _citation_prefix(chunk.page, excerpt=False) + chunk.text, key
    return (
        _citation_prefix(chunk.page, excerpt=True) + _snippet(chunk.text, limit),
        None,
    )


def _figure_addition(
    item: Evidence,
    paper: _Paper,
    present: set[tuple[str, str]],
    attached: set[tuple[str, str]],
    limit: int,
) -> tuple[str, tuple[str, str] | None]:
    figure = next(
        (
            paper.figure_captions[block_id]
            for block_id in item.block_ids
            if block_id in paper.figure_captions
        ),
        None,
    )
    if figure is None:
        return "", None
    chunk = paper.citing_chunk(figure.mention_block_ids)
    if chunk is None or chunk.id == item.id:
        return "", None
    key = (item.document_id, chunk.id)
    if key in attached:
        return "", None
    attached.add(key)
    if key in present:
        return _citation_prefix(chunk.page, excerpt=False) + chunk.text, key
    return _citation_prefix(chunk.page, excerpt=True) + _snippet(chunk.text, limit), None


def attach_references(
    evidence: list[Evidence],
    documents: dict[str, Document],
    manifests: dict[str, IndexManifest],
    limit: int = ATTACH_MAX,
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
        addition, drop = "", None
        if paper is not None:
            addition, drop = _table_addition(item, paper, present, attached, limit)
            if not addition:
                addition, drop = _figure_addition(item, paper, present, attached, limit)
        if addition:
            item = item.model_copy(update={"text": addition + "\n" + item.text})
            if drop is not None:
                dropped.add(drop)
        merged.append(item)
    return [item for item in merged if (item.document_id, item.id) not in dropped]
