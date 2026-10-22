import json
import os

import pytest

from app.core.errors import AppError
from app.domain.papers import PaperTable, TableCell
from app.parsing.cleaning import clean_blocks
from app.parsing.runner import DoclingRunner
from app.parsing.table_captions import associate_table_captions
from app.parsing.table_data import grid_warnings, row_evidence, table_html, valid_grid
from app.repositories.papers import PaperRepository
from app.retrieval.chunks import build_chunks
from scripts.make_table_fixture import make_table_fixture
from tests.conftest import FixtureParser
from tests.qa_helpers import seed_document
from tests.test_extraction import block
from tests.test_ingestion import upload


def synthetic_table():
    return PaperTable(
        id="t1",
        source_ref="#/tables/0",
        page=1,
        bbox=block("x").bbox,
        rows=3,
        columns=2,
        label="Table 1",
        caption="Table 1. Measured performance.",
        cells=[
            TableCell(
                text="Metrics", row_start=0, row_end=1, col_start=0, col_end=2, column_header=True
            ),
            TableCell(text="Baseline", row_start=1, row_end=2, col_start=0, col_end=1),
            TableCell(text="0.731", row_start=1, row_end=2, col_start=1, col_end=2),
            TableCell(text="Proposed", row_start=2, row_end=3, col_start=0, col_end=1),
            TableCell(
                text="<img src=x onerror=alert(1)>", row_start=2, row_end=3, col_start=1, col_end=2
            ),
        ],
    )


def test_publication_admin_retained_in_trace_but_not_body_or_retrieval(settings):
    document = seed_document(
        PaperRepository(settings.data_dir / "library.sqlite3"),
        "A scientific paragraph reports measurements.",
    )
    paper = document.parsed
    paper.blocks += [
        block(
            "∗ Corresponding author. E-mail address: author@example.com",
            kind="footnote",
            top=680,
            bottom=700,
        ),
        block(
            "Received 1 February 2025; Received in revised form 28 April 2025; "
            "Accepted 29 April 2025",
            top=710,
            bottom=720,
        ),
        block(
            "1 Important scientific qualification: measurements exclude outliers.",
            kind="footnote",
            top=730,
            bottom=740,
        ),
    ]
    clean_blocks(paper.blocks, paper.pages)
    assert paper.blocks[-3].raw_text and paper.blocks[-3].excluded_reason == "publication_admin"
    assert paper.blocks[-2].excluded_reason == "publication_admin"
    assert paper.blocks[-1].excluded_reason is None
    text = " ".join(c.text for c in build_chunks(document))
    assert "author@example.com" not in text and "Received" not in text
    assert "scientific qualification" in text


def test_merged_cells_escaped_html_and_row_context(settings):
    table = synthetic_table()
    html = table_html(table)
    assert 'colspan="2"' in html and "&lt;img" in html and "<img " not in html
    rows = row_evidence(table)
    assert any("Baseline" in row and "0.731" in row and "Metrics" in row for row in rows)
    document = seed_document(
        PaperRepository(settings.data_dir / "library.sqlite3"),
        "A scientific paragraph describes the experiment.",
    )
    document.parsed.tables = [table]
    chunks = [c for c in build_chunks(document) if c.element_type == "table"]
    assert chunks and all(c.element_id == "t1" and c.page == 1 for c in chunks)
    assert any("Baseline" in c.text and "0.731" in c.text for c in chunks)
    table.cells[0].row_end = 10
    assert not valid_grid(table) and table_html(table) == "" and row_evidence(table) == []
    table = synthetic_table()
    table.rows = 4
    assert grid_warnings(table)
    # Storage/rendering supports vertical spans even when the model misses them in a real PDF.
    table.cells[-2].row_end = 4
    table.cells[-1].row_end = 4
    assert 'rowspan="2"' in table_html(table)


@pytest.mark.parametrize("kind", ["caption", "text"])
def test_table_caption_fallback_matches_column_coordinates(kind):
    table = synthetic_table()
    table.label = None
    table.caption = ""
    table.bbox = block("x", top=430, bottom=520).bbox
    caption = block("Table 3", kind=kind, top=410, bottom=418)
    continuation = block("Comparison of measured results.", top=420, bottom=427)
    other = block("Table 4", kind="caption", top=410, bottom=418)
    other.bbox.left = 520
    other.bbox.right = 590
    associate_table_captions([table], [caption, continuation, other])
    assert table.label == "Table 3" and "Comparison" in table.caption
    assert table.caption_block_ids == [caption.id, continuation.id]


def test_failed_reparse_keeps_working_record_and_original(client, pdf_path, settings):
    identifier = upload(client, pdf_path)["document"]["id"]
    client.patch(f"/documents/{identifier}/metadata", json={"title": "Manual checked title"})
    repo = client.app.state.repository
    before = repo.get(identifier)

    class FailingParser:
        def parse(self, source, assets):
            raise AppError("simulated", "Expected failure", 503)

    client.app.state.ingestion.parser = FailingParser()
    assert client.post(f"/documents/{identifier}/reparse").status_code == 503
    assert repo.get(identifier) == before
    client.app.state.ingestion.parser = FixtureParser()
    assert client.post(f"/documents/{identifier}/reparse").status_code == 200
    after = repo.get(identifier)
    assert after.parsed.metadata.title == "Manual checked title"
    histories = list(
        (settings.data_dir / "papers" / identifier / "history").glob("before-reparse-*.json")
    )
    assert (
        len(histories) == 1
        and json.loads(histories[0].read_text(encoding="utf-8"))["parsed"]
        == before.parsed.model_dump()
    )
    assert client.get(f"/documents/{identifier}/exports/unknown").status_code == 404


@pytest.fixture(scope="module")
def real_table_paper(tmp_path_factory):
    if os.getenv("RUN_DOCLING_TESTS") != "1":
        pytest.skip("Requires real layout/TableFormer models")
    directory = tmp_path_factory.mktemp("real-table")
    source = make_table_fixture(directory / "table.pdf")
    assets = directory / "assets"
    return DoclingRunner(300).parse(source, assets), assets


@pytest.mark.docling
def test_real_merged_table_structure_exports_and_numbers(real_table_paper):
    paper, assets = real_table_paper
    assert paper.structure_version == 1 and paper.tables
    table = next(t for t in paper.tables if "0.731" in " ".join(c.text for c in t.cells))
    values = {c.text for c in table.cells}
    assert {"0.731", "0.704", "0.812", "0.793"} <= values
    assert table.rows == 4 and table.columns == 3
    assert any(c.col_end - c.col_start > 1 for c in table.cells)
    assert table.structure_warnings  # Known missing vertical merge must remain visible.
    assert table.caption and table.page == 1 and valid_grid(table)
    assert (assets / table.image).read_bytes().startswith(b"\x89PNG")
    raw = json.loads((assets / paper.exports["docling_json"]).read_text(encoding="utf-8"))
    assert raw["body"]["children"] and raw["tables"][0]["data"]["table_cells"]
    assert any(e.kind == "table" and e.source_ref == table.source_ref for e in paper.reading_order)
    assert any(b.excluded_reason == "publication_admin" for b in paper.blocks)


@pytest.mark.docling
@pytest.mark.xfail(
    strict=True, reason="Known TableFormer v2.3.0 defect: vertical Method header merge is missed"
)
def test_real_vertical_header_merge_gold_standard(real_table_paper):
    paper, _ = real_table_paper
    assert any(c.row_end - c.row_start > 1 for t in paper.tables for c in t.cells)
