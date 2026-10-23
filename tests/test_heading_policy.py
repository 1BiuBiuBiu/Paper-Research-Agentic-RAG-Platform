from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from app.domain.papers import Block, Box
from app.metadata.extract import extract_metadata
from app.parsing.headings import heading_depth, normalize_headings
from app.ui_components.page_reading import block_html, previous_heading


@pytest.mark.parametrize(
    ("text", "depth"),
    [
        ("F", 1),
        ("F.1 Emotion Wheels", 2),
        ("J Dataset Details", 1),
        ("J.1 Training", 2),
        ("J.2 Testing", 2),
        ("A.2.3 Details", 3),
        ("Z.1.2.3.4 Details", 4),
        ("3.1 Methods", 2),
        ("F. Appendix", 1),
    ],
)
def test_printed_hierarchy_overrides_engine(text, depth):
    assert heading_depth(dict(kind="section_header", text=text, heading_level=4)) == depth


@pytest.mark.parametrize(
    "text",
    [
        "CCS Concepts",
        "KEYWORDS",
        "ACM Reference Format",
        "Index Terms",
        "Author Keywords:",
        "Key words: emotion recognition",
        "Subject Classification",
        "MSC Classification",
        "IEEE Citation Format",
        "How to cite this article",
        "Article Information",
        "A R T I C L E I N F O",
        "Publication History",
        "Copyright Information",
        "Reprints and Permissions",
        "关键词：情感识别",
        "中图分类号",
        "引用格式",
        "1 Article Info",
    ],
)
def test_publication_metadata_is_not_a_chapter(text):
    item = dict(kind="section_header", text=text)
    assert heading_depth(item) is None
    assert 'class="paper-paragraph"' in block_html(item)


@pytest.mark.parametrize(
    "text",
    [
        "Abstract",
        "References",
        "Acknowledgments",
        "Ethics Statement",
        "Limitations",
        "Data Availability",
        "Funding",
        "F.1 Keywords in Retrieval",
        "1 Article Information Extraction",
        "2 Citation Format Prediction",
        "A New Method",
        "Appendix",
    ],
)
def test_research_sections_are_retained(text):
    assert heading_depth(dict(kind="section_header", text=text)) is not None


def test_normalization_retains_source_and_old_records_use_same_policy():
    blocks = [
        Block(
            id=str(i),
            page=1,
            order=i,
            kind="section_header",
            heading_level=1,
            bbox=Box(left=1, top=1, right=10, bottom=10),
            raw_text=text,
            text=text,
            source_ref=f"#/texts/{i}",
        )
        for i, text in enumerate(["J.1 Training", "Keywords", "1 Introduction"])
    ]
    before = deepcopy(blocks)
    normalize_headings(blocks)
    assert blocks[0].heading_level == 2
    assert blocks[1].kind == "publication_metadata"
    assert blocks[1].excluded_reason is None
    for old, new in zip(before, blocks, strict=True):
        assert (old.raw_text, old.text, old.bbox, old.source_ref) == (
            new.raw_text,
            new.text,
            new.bbox,
            new.source_ref,
        )
    snapshot = deepcopy(blocks)
    normalize_headings(blocks)
    assert blocks == snapshot
    paper = {"blocks": [before[0].model_dump(), before[1].model_dump()]}
    assert previous_heading(paper, 2) == "J.1 Training"


def test_actual_reader_renders_appendix_subsection_and_metadata_as_paragraph():
    app = AppTest.from_string("""
from app.ui_components.structure import reading_body
blocks = [dict(id=str(i), page=1, order=i, kind="section_header", text=text)
          for i, text in enumerate(["J Dataset Details", "J.1 Training", "CCS Concepts"])]
reading_body({"id":"selected-paper", "parsed":{"blocks":blocks, "figures":[]}}, 1)
""").run()
    assert not app.exception
    html = "".join(element.value for element in app.get("html"))
    assert '<h2 class="paper-heading">J Dataset Details</h2>' in html
    assert '<h3 class="paper-heading">J.1 Training</h3>' in html
    assert '<p class="paper-paragraph">CCS Concepts</p>' in html


def test_metadata_demotion_still_terminates_abstract():
    blocks = [
        Block(
            id=str(i),
            page=1,
            order=i,
            kind=kind,
            text=text,
            raw_text=text,
            bbox=Box(left=1, top=100 + i * 20, right=500, bottom=110 + i * 20),
        )
        for i, (kind, text) in enumerate(
            [
                ("section_header", "Abstract"),
                ("text", "Our scientific abstract."),
                ("section_header", "CCS Concepts"),
                ("text", "Computing methodologies"),
                ("section_header", "1 Introduction"),
                ("text", "Research body."),
            ]
        )
    ]
    normalize_headings(blocks)
    assert extract_metadata(blocks).abstract == "Our scientific abstract."
