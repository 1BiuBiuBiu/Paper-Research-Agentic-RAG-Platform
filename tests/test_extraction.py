from app.domain.papers import Block, Box, PageInfo
from app.metadata.extract import extract_metadata
from app.parsing.cleaning import clean_blocks


def block(text, *, page=1, kind="text", top=25, bottom=35):
    return Block(
        id=f"{page}-{text}",
        page=page,
        order=0,
        kind=kind,
        bbox=Box(left=40, top=top, right=500, bottom=bottom),
        raw_text=text,
        text=text,
    )


def test_repeated_furniture_and_protected_scientific_content():
    pages = [PageInfo(number=p, width=612, height=792, image="x") for p in (1, 2)]
    blocks = [block("Repeated Journal", page=p) for p in (1, 2)]
    blocks += [
        block("Page 1", top=760, bottom=770),
        block("The actual title", kind="title"),
        block("Scientific footnote", kind="footnote", top=755, bottom=765),
        block("Figure 1. Result", kind="caption", top=745, bottom=755),
    ]
    clean_blocks(blocks, pages)
    assert blocks[0].excluded_reason and blocks[1].excluded_reason
    assert blocks[2].excluded_reason == "margin_page_number"
    assert all(b.excluded_reason is None for b in blocks[3:])
    assert blocks[0].raw_text == "Repeated Journal"


def test_metadata_own_doi_and_original_abstract():
    blocks = [
        block("Scientific Paper", kind="title"),
        block("Journal of Science (2024)"),
        block("Authors: Alice; Bob"),
        block("doi: 10.5555/own-paper"),
        block("Abstract", kind="section_header"),
        block("These are the actual results.", top=100, bottom=120),
        block("1 Introduction", kind="section_header"),
        block("Other doi: 10.9999/not-ours", page=2, kind="reference"),
    ]
    metadata = extract_metadata(blocks)
    assert metadata.title == "Scientific Paper" and metadata.authors == ["Alice", "Bob"]
    assert metadata.doi == "10.5555/own-paper" and metadata.publication_year == 2024
    assert metadata.abstract == "These are the actual results."
    assert metadata.provenance["title"].block_ids == [blocks[0].id]


def test_missing_metadata_stays_missing():
    metadata = extract_metadata([block("Plain text without metadata", top=200, bottom=220)])
    assert metadata.doi is None and metadata.title is None and metadata.authors == []


def test_copyright_is_not_journal_and_reference_doi_is_not_own():
    blocks = [
        block("Provided permission for journalistic or scholarly works."),
        block("References", kind="section_header", top=600, bottom=620),
        block("Another work DOI: 10.9999/not-this-paper", top=640, bottom=660),
    ]
    metadata = extract_metadata(blocks)
    assert metadata.venue is None and metadata.doi is None


def test_author_markers_with_affiliations():
    blocks = [
        block("Real title", kind="title", top=80, bottom=100),
        block("Ashish Vaswani∗ Google Brain avaswani@example.com", top=130, bottom=170),
        block("Łukasz Kaiser∗ Google Brain author@example.com", top=190, bottom=220),
        block("Abstract", kind="section_header", top=300, bottom=320),
    ]
    metadata = extract_metadata(blocks)
    assert metadata.authors == ["Ashish Vaswani", "Łukasz Kaiser"]
