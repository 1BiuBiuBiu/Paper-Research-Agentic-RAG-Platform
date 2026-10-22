"""Adapt Docling's table cells and document tree without reducing them to plain text."""

import re
from pathlib import Path
from typing import Any

from app.domain.papers import Block, Box, Equation, Figure, PaperTable, ReadingElement, TableCell
from app.parsing.table_captions import associate_table_captions
from app.parsing.table_data import grid_warnings, valid_grid


def pdf_box(box: Any, height: float) -> Box:
    value = box.to_top_left_origin(page_height=height)
    return Box(left=value.l, top=value.t, right=value.r, bottom=value.b)


def extract_tables(
    doc: Any, assets: Path, refs: dict[str, list[str]], blocks: list[Block]
) -> list[PaperTable]:
    tables: list[PaperTable] = []
    for item in doc.tables:
        if not item.prov:
            continue
        provenance = item.prov[0]
        page = provenance.page_no
        height = doc.pages[page].size.height
        image = item.get_image(doc)
        filename = f"table-{len(tables) + 1}.png" if image else None
        if image and filename:
            image.save(assets / filename)
        caption = item.caption_text(doc)
        match = re.search(r"(?:Table|表)\s*(\d+[a-z]?)", caption, re.I)
        data = item.data
        cells = [
            TableCell(
                text=cell.text,
                row_start=cell.start_row_offset_idx,
                row_end=cell.end_row_offset_idx,
                col_start=cell.start_col_offset_idx,
                col_end=cell.end_col_offset_idx,
                column_header=cell.column_header,
                row_header=cell.row_header,
                row_section=cell.row_section,
                bbox=pdf_box(cell.bbox, height) if cell.bbox else None,
            )
            for cell in data.table_cells
        ]
        table = PaperTable(
            id=f"t{len(tables) + 1}",
            source_ref=item.self_ref,
            page=page,
            bbox=pdf_box(provenance.bbox, height),
            image=filename,
            caption=caption,
            label=f"Table {match.group(1)}" if match else None,
            caption_block_ids=[bid for ref in item.captions for bid in refs.get(ref.cref, [])],
            rows=data.num_rows,
            columns=data.num_cols,
            cells=cells,
            quality="structured_unverified" if cells else "region_only",
        )
        if match:
            pattern = re.compile(rf"(?:Table|表)\s*{re.escape(match.group(1))}(?![\da-z])", re.I)
            table.mention_block_ids = [
                b.id
                for b in blocks
                if b.id not in table.caption_block_ids
                and not b.excluded_reason
                and pattern.search(b.text)
            ]
        table.quality = "structured_unverified" if valid_grid(table) else "region_only"
        table.structure_warnings = grid_warnings(table)
        table.footnote_block_ids = [bid for ref in item.footnotes for bid in refs.get(ref.cref, [])]
        tables.append(table)
    associate_table_captions(tables, blocks)
    return tables


def preserve_structure(
    doc: Any, assets: Path, blocks: list[Block], figures: list[Figure], tables: list[PaperTable]
) -> tuple[list[ReadingElement], list[Equation]]:
    from docling_core.types.doc import ImageRefMode

    # Keep the engine's groups/parent-child links, all provenance, spans and references.
    # Images are separate versioned assets + the immutable original PDF, not repeated base64.
    doc.save_as_json(assets / "docling.json", image_mode=ImageRefMode.PLACEHOLDER)
    (assets / "paper.md").write_text(doc.export_to_markdown(), encoding="utf-8")
    refs: dict[str, list[ReadingElement]] = {}
    for block in blocks:
        if block.source_ref:
            refs.setdefault(block.source_ref, []).append(
                ReadingElement(
                    kind="block", id=block.id, page=block.page, source_ref=block.source_ref
                )
            )
    for kind, items in (("figure", figures), ("table", tables)):
        for item in items:
            if item.source_ref:
                refs.setdefault(item.source_ref, []).append(
                    ReadingElement(
                        kind="figure" if kind == "figure" else "table",
                        id=item.id,
                        page=item.page,
                        source_ref=item.source_ref,
                    )
                )
    order = [
        reference for item, _ in doc.iterate_items() for reference in refs.get(item.self_ref, [])
    ]
    front_ids: list[str] = []
    for block in blocks:
        if block.page != 1 or block.kind == "section_header":
            break
        front_ids.append(block.id)
    front = {e.id: e for e in order if e.kind == "block" and e.id in front_ids}
    order = [front[bid] for bid in front_ids if bid in front] + [
        e for e in order if not (e.kind == "block" and e.id in front)
    ]
    equations: list[Equation] = []
    for block in blocks:
        if block.kind != "formula":
            continue
        page = doc.pages[block.page]
        if page.image is None:
            continue
        image = page.image.pil_image
        sx, sy = image.width / page.size.width, image.height / page.size.height
        box = block.bbox
        filename = f"equation-{len(equations) + 1}.png"
        image.crop(
            (
                max(0, box.left * sx),
                max(0, box.top * sy),
                min(image.width, box.right * sx),
                min(image.height, box.bottom * sy),
            )
        ).save(assets / filename)
        equations.append(
            Equation(
                id=f"eq{len(equations) + 1}",
                block_id=block.id,
                source_ref=block.source_ref,
                page=block.page,
                bbox=box,
                image=filename,
                text=block.raw_text,
            )
        )
    return order, equations
