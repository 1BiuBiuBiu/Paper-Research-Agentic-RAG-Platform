from app.domain.papers import Block, Box, PaperTable, TableCell
from app.parsing.table_data import row_evidence
from app.repositories.papers import PaperRepository
from app.retrieval.chunks import build_chunks, table_section
from tests.qa_helpers import seed_document


def cell(text: str, row: int, column: int, header: bool = False) -> TableCell:
    return TableCell(
        text=text,
        row_start=row,
        row_end=row + 1,
        col_start=column,
        col_end=column + 1,
        column_header=header,
    )


def variant_table(caption: str = "Table 3: Performance on MSED.") -> PaperTable:
    cells = [cell("Method", 0, 0, header=True)]
    cells += [
        cell(name, 0, index, header=True) for index, name in enumerate(("Precision", "Mac-F1"), 1)
    ]
    cells += [cell("-w/ WisdoM Qwen-VL [3]*", 1, 0), cell("90.5", 1, 1), cell("89.4", 1, 2)]
    cells += [cell("-w/ WisdoM", 2, 0), cell("91.8", 2, 1), cell("91.8", 2, 2)]
    return PaperTable(
        id="t1",
        source_ref="#/tables/0",
        page=1,
        bbox=Box(left=0, top=0, right=10, bottom=10),
        rows=3,
        columns=3,
        label="Table 3",
        caption=caption,
        cells=cells,
    )


def test_variant_label_is_moved_back_to_its_own_row():
    rows = row_evidence(variant_table())
    baseline = next(row for row in rows if "89.4" in row)
    enhanced = next(row for row in rows if "91.8" in row)
    assert "Method = Qwen-VL [3]*;" in baseline
    assert "-w/ WisdoM" not in baseline.split("Method = ")[1].split(";")[0]
    assert "Method = Qwen-VL [3]* / -w/ WisdoM;" in enhanced


def test_full_caption_and_section_are_part_of_the_row_identity():
    caption = (
        "Table 3: Performance of applying our WisdoM to advanced models on MSED benchmark, "
        "with reference results from existing works. The best results are bolded."
    )
    rows = row_evidence(variant_table(caption), "4.4.2 Results of Sentence-Level MSA Task")
    assert all(caption in row for row in rows)
    assert all("4.4.2 Results of Sentence-Level MSA Task | row" in row for row in rows)


def block(
    identifier: str, order: int, text: str, kind: str = "text", level: int | None = None
) -> Block:
    return Block(
        id=identifier,
        page=1,
        order=order,
        kind=kind,
        bbox=Box(left=0, top=order, right=10, bottom=order + 5),
        raw_text=text,
        text=text,
        heading_level=level,
    )


def test_table_section_uses_the_mentioning_paragraph_not_the_page_position():
    blocks = [
        block("h1", 0, "2 Method", kind="section_header", level=1),
        block("h2", 1, "2.3 Contextual Fusion", kind="section_header", level=2),
        block("m1", 2, "As shown in Table 3, WisdoM improves Qwen-VL."),
        block("h3", 3, "3 Experiments", kind="section_header", level=1),
        block("c1", 4, "Table 3: Performance on MSED.", kind="caption"),
    ]
    table = variant_table()
    table.mention_block_ids = ["m1"]
    table.caption_block_ids = ["c1"]
    assert table_section(table, blocks) == "2 Method / 2.3 Contextual Fusion"


def test_chunks_carry_section_and_untruncated_caption(settings):
    document = seed_document(
        PaperRepository(settings.data_dir / "library.sqlite3"),
        "A scientific paragraph introduces the benchmark.",
    )
    caption = (
        "Table 4 Ablation study of PS-Mixer on different components and three losses, "
        "test on CMU-MOSI."
    )
    table = variant_table(caption)
    table.mention_block_ids = ["m1"]
    document.parsed.blocks.extend(
        [
            block("h1", 90, "4.5 Ablation Study", kind="section_header", level=1),
            block("m1", 91, "We ablate MLP-C in Table 4."),
        ]
    )
    document.parsed.tables = [table]
    rows = [chunk for chunk in build_chunks(document) if chunk.element_type == "table"]
    assert rows and all("4.5 Ablation Study" in row.text for row in rows)
    assert all(caption in row.text for row in rows)
