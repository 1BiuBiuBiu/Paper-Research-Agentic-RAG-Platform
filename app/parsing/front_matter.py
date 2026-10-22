import re

from app.domain.papers import Block, PageInfo
from app.metadata.headings import is_abstract


def repair_front_matter(blocks: list[Block], pages: list[PageInfo]) -> None:
    """Repair a known layout-model error: detached title/author groups after body text."""
    first = [b for b in blocks if b.page == 1 and not b.excluded_reason]
    homepages = [b for b in first if re.search(r"journal\s+homepage\s*:", b.text, re.I)]
    if homepages:
        masthead_bottom = max(b.bbox.bottom for b in homepages)
        abstracts = [b for b in first if is_abstract(b.text)]
        boundary = min(b.bbox.top for b in abstracts) if abstracts else pages[0].height * 0.45
        candidates = [
            b
            for b in first
            if b.kind in {"title", "section_header"}
            and masthead_bottom < b.bbox.top < boundary
            and len(b.text) >= 25
            and not re.search(r"article\s*info|abstract|@|,.*,", b.text, re.I)
        ]
        if candidates:
            title = max(candidates, key=lambda b: len(b.text))
            title.kind = "title"
            for block in first:
                if block.bbox.bottom <= masthead_bottom:
                    block.kind = "publisher_header"
                    block.excluded_reason = "publisher_furniture"
    anchors = [
        b
        for b in first
        if b.kind == "section_header"
        and (is_abstract(b.text) or re.match(r"^1[.\s]+(?:Introduction|引言)\b", b.text, re.I))
    ]
    if not anchors:
        return
    boundary = min(b.bbox.top for b in anchors)
    front = [b for b in first if b.bbox.bottom <= boundary and b.bbox.top < pages[0].height * 0.3]
    front.sort(key=lambda b: (b.bbox.top, b.bbox.left))
    if not any(b.kind == "title" for b in front):
        candidates = [
            b
            for b in front
            if b.kind == "section_header"
            and len(b.text) >= 8
            and not re.search(
                r"^(?:\d|Authors?:|DOI:)|journal|proceedings|conference", b.text, re.I
            )
        ]
        if candidates:
            candidates[0].kind = "title"
    front_ids = {b.id for b in front}
    # Only front matter moves; preserve model reading order for the main columns.
    blocks[:] = front + [b for b in blocks if b.id not in front_ids]
    for index, block in enumerate(blocks):
        block.order = index
