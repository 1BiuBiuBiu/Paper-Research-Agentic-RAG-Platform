"""Page-local presentation of retained structure; never rewrites extraction records."""

import re
from html import escape
from typing import Any

from app.parsing.headings import CAPTION, heading_depth  # noqa: F401

EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.I)


def page_elements(paper: dict[str, Any], page: int) -> list[dict[str, Any]]:
    """Follow the saved reading order, with a deterministic fallback for old records."""
    objects = {
        "block": {b["id"]: b for b in paper["blocks"]},
        "figure": {f["id"]: f for f in paper["figures"]},
        "table": {t["id"]: t for t in paper.get("tables", [])},
    }
    order = paper.get("reading_order") or [
        {"kind": "block", "id": b["id"], "page": b["page"]}
        for b in sorted(paper["blocks"], key=lambda b: b.get("order", 0))
    ]
    result = []
    seen = set()
    for element in order:
        key = element["kind"], element["id"]
        item = objects.get(key[0], {}).get(key[1])
        if element["page"] != page or key in seen or not item:
            continue
        seen.add(key)
        if key[0] == "block" and item.get("excluded_reason"):
            continue
        result.append({**item, "element_kind": key[0]})
    return result


def is_note(block: dict[str, Any]) -> bool:
    # All scientific footnotes remain available too; no affiliation inference or deletion.
    return block["kind"] == "footnote" or (
        block["page"] == 1 and len(block["text"]) < 250 and bool(EMAIL.search(block["text"]))
    )


def block_html(block: dict[str, Any]) -> str:
    text = escape(block["text"])
    if block["kind"] == "title":
        return f'<h1 class="paper-title">{text}</h1>'
    depth = heading_depth(block)
    if depth is not None:
        tag = f"h{min(5, depth + 1)}"
        return f'<{tag} class="paper-heading">{text}</{tag}>'
    if block["kind"] == "caption" or CAPTION.match(block["text"]):
        return f'<p class="paper-caption">{text}</p>'
    if block["kind"] == "list_item":
        return f'<p class="paper-list-item">{text}</p>'
    if block["kind"] == "formula":
        return f'<p class="paper-formula">{text}</p>'
    return f'<p class="paper-paragraph">{text}</p>'


def previous_heading(paper: dict[str, Any], page: int) -> str | None:
    headings = [
        b
        for b in paper["blocks"]
        if b["page"] < page and not b.get("excluded_reason") and heading_depth(b) is not None
    ]
    return max(headings, key=lambda b: (b["page"], b.get("order", 0)))["text"] if headings else None
