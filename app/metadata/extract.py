import re

from app.domain.papers import Block, PaperMetadata, Provenance
from app.metadata.headings import abstract_prefix, is_auxiliary

DOI = re.compile(r"\b10\.\d{4,9}/[^\s<>\"{}]+", re.I)
VENUE = re.compile(
    r"\b(?:journal|transactions|proceedings|conference|symposium)\b|期刊|学报|会议", re.I
)
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def extract_metadata(blocks: list[Block]) -> PaperMetadata:
    """Extract candidates, never manufacture missing bibliographic fields."""
    metadata = PaperMetadata()
    first = [b for b in blocks if b.page == 1 and b.kind != "reference"]
    references = [b.bbox.top for b in first if re.fullmatch(r"References|参考文献", b.text, re.I)]
    if references:
        first = [
            b
            for b in first
            if b.bbox.top < min(references) or b.kind in {"page_header", "page_footer"}
        ]
    titles = [b for b in first if b.kind == "title" and not b.excluded_reason]
    if titles:
        metadata.title = " ".join(titles[0].text.split())
        metadata.provenance["title"] = Provenance(block_ids=[titles[0].id])
    for block in first:
        text = block.text
        if not metadata.doi and (match := DOI.search(text)):
            metadata.doi = match.group().rstrip(".,;)")
            metadata.provenance["doi"] = Provenance(block_ids=[block.id])
        if (
            not metadata.venue
            and VENUE.search(text)
            and len(text) <= 350
            and not re.search(r"journal\s+homepage|https?://|copyright", text, re.I)
        ):
            metadata.venue = " ".join(text.split())
            metadata.venue_type = (
                "conference"
                if re.search(r"proceedings|conference|symposium|会议", text, re.I)
                else "journal"
            )
            for field in ("venue", "venue_type"):
                metadata.provenance[field] = Provenance(block_ids=[block.id])
            if match := YEAR.search(text):
                metadata.publication_year = int(match.group())
                metadata.provenance["publication_year"] = Provenance(block_ids=[block.id])
            elif match := re.search(r"\bAAAI[-–](\d{2})\b", text):
                metadata.publication_year = 2000 + int(match.group(1))
                metadata.provenance["publication_year"] = Provenance(block_ids=[block.id])
        if metadata.venue is None and block.kind in {"publisher_header", "page_header"}:
            journal = re.fullmatch(r"(.+?)\s+\d+\s*\(((?:19|20)\d{2})\)\s+\S+", text)
            if journal and not re.search(r"https?://|copyright", journal.group(1), re.I):
                metadata.venue = journal.group(1).strip()
                metadata.venue_type = "journal"
                metadata.publication_year = int(journal.group(2))
                for field in ("venue", "venue_type", "publication_year"):
                    metadata.provenance[field] = Provenance(block_ids=[block.id])
        if metadata.issn is None and (match := re.search(r"\b\d{4}-\d{3}[\dX]\b", text)):
            metadata.issn = match.group()
            metadata.provenance["issn"] = Provenance(block_ids=[block.id])
        if re.search(r"arxiv\s*:|bioRxiv|medRxiv", text, re.I) and metadata.venue is None:
            metadata.venue_type = "preprint"
            metadata.provenance["venue_type"] = Provenance(block_ids=[block.id])
        if re.match(r"^(?:Authors?|作者)\s*[:：]", text, re.I):
            names = re.sub(r"^(?:Authors?|作者)\s*[:：]\s*", "", text, flags=re.I)
            names = re.split(r"\b(?:DOI|ISSN)\s*:", names, flags=re.I)[0].strip()
            metadata.authors = [n.strip() for n in re.split(r";|\band\b|、", names) if n.strip()]
            metadata.provenance["authors"] = Provenance(block_ids=[block.id])
    if not metadata.authors and titles:
        _author_candidates(metadata, first, titles[0])
    _abstract(metadata, blocks)
    return metadata


def _author_candidates(metadata: PaperMetadata, first: list[Block], title: Block) -> None:
    anchors = [b.bbox.top for b in first if abstract_prefix(b.text)]
    boundary = min(anchors) if anchors else title.bbox.bottom + 120
    evidence: list[str] = []
    for block in first:
        if not title.bbox.bottom <= block.bbox.top < boundary:
            continue
        if block.excluded_reason or block.kind == "title":
            continue
        text = block.text
        # Scientific title pages often use author markers and put affiliations in the same block.
        marked = re.match(r"^([^\W\d_][\w.'-]*(?:\s+[^\W\d_][\w.'-]*){1,4})[∗*†‡]", text)
        if marked:
            name = marked.group(1)
            if name not in metadata.authors:
                metadata.authors.append(name)
                evidence.append(block.id)
            continue
        if re.search(
            r"@|DOI|ISSN|university|institute|department|laboratory|copyright", text, re.I
        ):
            continue
        names = [re.sub(r"[∗*†‡\d]+", "", n).strip() for n in re.split(r";|,|\band\b|、", text)]
        if names and all(re.fullmatch(r"[A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){1,4}", n) for n in names):
            metadata.authors = names
            metadata.provenance["authors"] = Provenance(block_ids=[block.id])
            evidence.append(block.id)
    if evidence:
        metadata.provenance["authors"] = Provenance(block_ids=evidence)


def _abstract(metadata: PaperMetadata, blocks: list[Block]) -> None:
    collecting = False
    selected: list[Block] = []
    for block in blocks:
        if block.page > 2 or block.excluded_reason:
            continue
        heading = abstract_prefix(block.text)
        if heading:
            collecting = True
            if block.text[heading.end() :].strip():
                selected.append(block.model_copy(update={"text": block.text[heading.end() :]}))
            continue
        if collecting and (
            block.kind in {"section_header", "title"}
            or is_auxiliary(block.text)
            or re.match(r"^(?:\d[.\s]|Keywords|Index Terms|关键词)", block.text, re.I)
        ):
            break
        if collecting and block.kind not in {"caption", "footnote", "formula"}:
            selected.append(block)
    if selected:
        metadata.abstract = "\n\n".join(b.text for b in selected)
        metadata.provenance["abstract"] = Provenance(block_ids=[b.id for b in selected])
