import re
import unicodedata
from collections import defaultdict

from app.domain.papers import Block, PageInfo

PROTECTED = {"title", "section_header", "caption", "footnote", "formula", "reference"}


def clean_blocks(blocks: list[Block], pages: list[PageInfo]) -> None:
    """Exclude furniture without discarding its text or source coordinates."""
    dimensions = {p.number: p for p in pages}
    repeated: dict[tuple[str, str], set[int]] = defaultdict(set)
    for block in blocks:
        page = dimensions[block.page]
        zone = "top" if block.bbox.bottom < page.height * 0.09 else "bottom"
        is_margin = block.bbox.bottom < page.height * 0.09 or block.bbox.top > page.height * 0.92
        if is_margin and block.kind not in PROTECTED:
            key = re.sub(r"\d+", "#", " ".join(block.raw_text.lower().split()))
            repeated[(zone, key)].add(block.page)
    for block in blocks:
        block.text = unicodedata.normalize("NFC", block.raw_text).replace("\x00", "").strip()
        if block.page == 1 and publication_admin(block.text):
            block.excluded_reason = "publication_admin"
            continue
        if block.kind in {"page_header", "page_footer"}:
            block.excluded_reason = "parser_furniture"
        if block.kind in PROTECTED:
            continue
        page = dimensions[block.page]
        top = block.bbox.bottom < page.height * 0.09
        bottom = block.bbox.top > page.height * 0.92
        if not (top or bottom):
            continue
        if re.fullmatch(r"(?:Page\s*)?\d+(?:\s*(?:/|of)\s*\d+)?", block.text, re.I):
            block.excluded_reason = "margin_page_number"
        key = re.sub(r"\d+", "#", " ".join(block.raw_text.lower().split()))
        if len(repeated[("top" if top else "bottom", key)]) >= 2:
            block.excluded_reason = block.excluded_reason or "repeated_margin"


def publication_admin(text: str) -> bool:
    if len(text) > 1000:
        return False
    contact = re.match(
        r"^[∗*†‡\d\s]*(?:Corresponding authors?\.?|E-?mail address(?:es)?\s*:)", text, re.I
    )
    history = re.match(r"^Received\s+\d{1,2}\s+[A-Za-z]+\s+(?:19|20)\d{2}", text, re.I)
    return bool(contact or (history and re.search(r"Accepted|revised form", text, re.I)))
