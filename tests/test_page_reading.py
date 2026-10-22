from copy import deepcopy

from app.ui_components.page_reading import (
    block_html,
    heading_depth,
    is_note,
    page_elements,
    previous_heading,
)


def block(identifier, page, kind, text, order=0, **kwargs):
    return {
        "id": identifier,
        "page": page,
        "kind": kind,
        "text": text,
        "order": order,
        "excluded_reason": None,
        **kwargs,
    }


def test_page_structure_follows_saved_order_preserves_media_and_never_leaks_next_page():
    paper = {
        "blocks": [
            block("next", 2, "text", "Other page"),
            block("body", 1, "text", "Body"),
            block("heading", 1, "section_header", "1 Introduction"),
            block("footer", 1, "text", "1", excluded_reason="margin_page_number"),
        ],
        "figures": [{"id": "f1", "page": 1}],
        "tables": [{"id": "t1", "page": 1}],
        "reading_order": [
            {"kind": kind, "id": identifier, "page": page}
            for kind, identifier, page in [
                ("block", "heading", 1),
                ("figure", "f1", 1),
                ("block", "body", 1),
                ("table", "t1", 1),
                ("block", "body", 1),
                ("block", "footer", 1),
                ("block", "next", 2),
            ]
        ],
    }
    before = deepcopy(paper)
    assert [e["id"] for e in page_elements(paper, 1)] == ["heading", "f1", "body", "t1"]
    assert paper == before


def test_printed_subsection_depth_and_caption_misclassification_are_presented_safely():
    heading = block("h", 1, "section_header", "3.1.2 Visual <script>alert(1)</script>")
    assert heading_depth(heading) == 3
    html = block_html(heading)
    assert html.startswith('<h4 class="paper-heading">3.1.2')
    assert "<script>" not in html and "&lt;script&gt;" in html
    caption = block("c", 1, "section_header", "Fig. 1 Architecture")
    assert heading_depth(caption) is None
    assert 'class="paper-caption"' in block_html(caption)


def test_legacy_page_order_continuation_and_scientific_notes_remain_available():
    paper = {
        "blocks": [
            block("b", 2, "text", "Continuation", 4),
            block("h", 1, "section_header", "2 Methods", 2),
            block("n", 2, "footnote", "Scientific qualification", 5),
            block("contact", 1, "text", "Author name author@example.org", 3),
        ],
        "figures": [],
    }
    page = page_elements(paper, 2)
    assert [e["id"] for e in page] == ["b", "n"]
    assert previous_heading(paper, 2) == "2 Methods"
    assert is_note(page[1])
    assert is_note(paper["blocks"][3])
    assert not is_note(page[0])
    assert previous_heading(paper, 1) is None


def test_reader_previews_only_formula_regions_of_the_selected_document_page(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from app.ui_components import structure

    calls = []
    # Valid PNG bytes, derived in memory; this checks presentation, not model recognition.
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (100, 30), "white").save(buffer, format="PNG")
    monkeypatch.setattr(
        structure,
        "image_bytes",
        lambda identifier, asset: calls.append((identifier, asset)) or buffer.getvalue(),
    )
    app = AppTest.from_string("""
from app.ui_components.structure import reading_body
paper = {
    "blocks": [{"id":"b1", "page":1, "kind":"formula", "text":"x=1", "order":0}],
    "figures": [],
    "equations": [
        {"block_id":"b1", "page":1, "image":"generation/equation-1.png"},
        {"block_id":"b2", "page":2, "image":"generation/equation-2.png"},
    ],
}
reading_body({"id":"paper-A", "parsed":paper}, 1)
""").run()
    assert not app.exception
    assert calls == [("paper-A", "generation/equation-1.png")]
    assert len(app.get("imgs")) + len(app.get("image")) >= 1
