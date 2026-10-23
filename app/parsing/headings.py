"""Shared printed hierarchy and publication-metadata heading policy."""

import re
import unicodedata
from typing import Any

from app.domain.papers import Block

CAPTION = re.compile(r"^(?:Fig(?:ure)?\.?|Table|图|表)\s*\d", re.I)
NUMBER = re.compile(r"^(\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)(?:\.(?=\s|$)|\s|$)")
# Match semantic roles, not publisher names or arbitrary substring occurrences.
# Deliberately leave scientific disclosures (ethics, funding, availability, etc.) intact.
METADATA_LABEL = re.compile(
    r"(?:"
    r"(?:ccs|acm computing) (?:concepts|classification(?: system)?)|"
    r"(?:msc|pacs|jel|ams)(?: (?:classification|codes?|numbers?))?|"
    r"(?:[a-z]+ )?(?:key\s*words?|index terms|subject (?:terms|headings|classification))|"
    r"(?:[a-z]+ )?(?:reference|citation) format|"
    r"(?:how to cite(?: this (?:article|paper))?|cite this (?:article|paper))|"
    r"(?:article|publication|document) (?:info(?:rmation)?|history)|"
    r"(?:copyright(?: notice| information)?|rights and permissions|reprints and permissions)|"
    r"关键词|关键字|索引词|中图分类号|文章编号|文献标识码|"
    r"引用格式|本文引用格式|文章信息|出版信息|收稿日期|修回日期|录用日期"
    r")",
    re.I,
)


def metadata_heading(text: str) -> bool:
    value = unicodedata.normalize("NFKC", text).strip()
    # A spaced-letter label (e.g. Elsevier's A R T I C L E I N F O) is equivalent.
    words = value.split()
    if len(words) > 3 and all(len(w) == 1 for w in words):
        value = "".join(words)
        value = re.sub(r"^(article)(info(?:rmation)?)$", r"\1 \2", value, flags=re.I)
    value = NUMBER.sub("", value, count=1).strip()
    label = re.split(r"[:：]", value, maxsplit=1)[0].strip().rstrip(".")
    label = " ".join(label.replace("-", " ").split())
    return METADATA_LABEL.fullmatch(label) is not None


def heading_depth(block: dict[str, Any]) -> int | None:
    text = block["text"].strip()
    if (
        block["kind"] != "section_header"
        or block.get("excluded_reason")
        or CAPTION.match(text)
        or metadata_heading(text)
    ):
        return None
    number = NUMBER.match(text)
    if number:
        return min(4, len(number.group(1).split(".")))
    return max(1, min(4, block.get("heading_level") or 1))


def normalize_headings(blocks: list[Block]) -> None:
    """Retain metadata text and provenance, but remove its chapter role."""
    for block in blocks:
        if block.kind != "section_header" or block.excluded_reason:
            continue
        if metadata_heading(block.text):
            block.kind = "publication_metadata"
            block.heading_level = None
        else:
            block.heading_level = heading_depth(block.model_dump())
