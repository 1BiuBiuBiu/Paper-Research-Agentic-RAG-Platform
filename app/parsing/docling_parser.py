from importlib.metadata import version
from pathlib import Path

from app.domain.papers import Block, Box, Figure, JournalMetric, PageInfo, ParsedPaper
from app.metadata.extract import extract_metadata
from app.parsing.cleaning import clean_blocks
from app.parsing.front_matter import repair_front_matter
from app.parsing.headings import normalize_headings
from app.parsing.structure import extract_tables, preserve_structure


class DoclingParser:
    """Load the converter once and reuse its local models across PDF jobs."""

    def __init__(self, *, table_cell_matching: bool = True) -> None:
        # Import heavy model dependencies only inside the parser worker.
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions()
        options.do_ocr = False
        options.do_table_structure = True
        options.table_structure_options.mode = TableFormerMode.ACCURATE
        options.table_structure_options.do_cell_matching = table_cell_matching
        options.generate_page_images = True
        options.generate_picture_images = True
        options.images_scale = 1.5
        options.enable_remote_services = False
        self.converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )

    def parse(self, path: Path, assets: Path) -> ParsedPaper:
        from docling_core.types.doc import TextItem

        result = self.converter.convert(path, raises_on_error=True)
        doc = result.document
        assets.mkdir(parents=True, exist_ok=True)
        pages: list[PageInfo] = []
        for number, page in sorted(doc.pages.items()):
            filename = f"page-{number}.png"
            image = page.image.pil_image if page.image is not None else None
            if image is None:
                from app.core.errors import AppError

                raise AppError("page_render_failed", "原页预览渲染失败，请重试。")
            image.save(assets / filename)
            pages.append(
                PageInfo(
                    number=number,
                    width=page.size.width,
                    height=page.size.height,
                    image=filename,
                )
            )
        blocks: list[Block] = []
        ref_to_ids: dict[str, list[str]] = {}
        # Include furniture in the trace even though it is not part of the body reading order.
        ordered = [item for item, _ in doc.iterate_items() if isinstance(item, TextItem)]
        seen = {item.self_ref for item in ordered}
        ordered.extend(item for item in doc.texts if item.self_ref not in seen)
        for item in ordered:
            for provenance in item.prov:
                number = provenance.page_no
                box = provenance.bbox.to_top_left_origin(page_height=doc.pages[number].size.height)
                block_id = f"b{len(blocks) + 1}"
                start, end = provenance.charspan
                segment = item.text[start:end] if end > start else item.text
                blocks.append(
                    Block(
                        id=block_id,
                        page=number,
                        order=len(blocks),
                        kind=item.label.value,
                        bbox=Box(left=box.l, top=box.t, right=box.r, bottom=box.b),
                        raw_text=segment,
                        text=segment,
                        source_ref=item.self_ref,
                        parent_ref=item.parent.cref if item.parent else None,
                        heading_level=getattr(item, "level", None),
                    )
                )
                ref_to_ids.setdefault(item.self_ref, []).append(block_id)
        clean_blocks(blocks, pages)
        repair_front_matter(blocks, pages)
        normalize_headings(blocks)
        metadata = extract_metadata(blocks)
        figures: list[Figure] = []
        for picture in doc.pictures:
            if not picture.prov:
                continue
            provenance = picture.prov[0]
            number = provenance.page_no
            box = provenance.bbox.to_top_left_origin(page_height=doc.pages[number].size.height)
            image = picture.get_image(doc)
            if image is None:
                continue
            filename = f"figure-{len(figures) + 1}.png"
            image.save(assets / filename)
            caption_ids = [
                block_id for ref in picture.captions for block_id in ref_to_ids.get(ref.cref, [])
            ]
            figures.append(
                Figure(
                    id=f"f{len(figures) + 1}",
                    page=number,
                    bbox=Box(left=box.l, top=box.t, right=box.r, bottom=box.b),
                    image=filename,
                    caption=picture.caption_text(doc),
                    caption_block_ids=caption_ids,
                    quality="extracted" if caption_ids else "caption_missing",
                    source_ref=picture.self_ref,
                )
            )
        from app.parsing.figures import associate_figures

        associate_figures(figures, blocks)
        tables = extract_tables(doc, assets, ref_to_ids, blocks)
        reading_order, equations = preserve_structure(doc, assets, blocks, figures, tables)
        warnings = ["元数据为提取候选，请核对；S0 未联网补全。", "当前不识别扫描正文。"]
        if not any(b.text and not b.excluded_reason for b in blocks):
            from app.core.errors import AppError

            raise AppError("no_text", "未发现可提取正文，扫描论文需要 OCR。")
        metric = JournalMetric()
        if metadata.venue_type in {"conference", "preprint"}:
            metric.status = "not_applicable"
        return ParsedPaper(
            parser=f"docling/{version('docling')}+research-s3.7.2",
            pages=pages,
            blocks=blocks,
            figures=figures,
            metadata=metadata,
            metric=metric,
            warnings=warnings,
            structure_version=1,
            tables=tables,
            equations=equations,
            reading_order=reading_order,
            exports={"docling_json": "docling.json", "markdown": "paper.md"},
        )


def parse_pdf(path: Path, assets: Path, *, table_cell_matching: bool = True) -> ParsedPaper:
    """Compatibility entry point for one-off parsing."""
    return DoclingParser(table_cell_matching=table_cell_matching).parse(path, assets)
