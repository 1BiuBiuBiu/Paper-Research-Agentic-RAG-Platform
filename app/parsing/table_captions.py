import re

from app.domain.papers import Block, PaperTable

LABEL = re.compile(r"^(?:Table|表)\s*(\d+[a-z]?)\b", re.I)


def associate_table_captions(tables: list[PaperTable], blocks: list[Block]) -> None:
    used = {bid for table in tables for bid in table.caption_block_ids}
    for table in tables:
        if table.label is None:
            candidates = []
            for block in blocks:
                if (
                    block.page != table.page
                    or block.id in used
                    or (block.kind != "caption" and not LABEL.fullmatch(block.text.strip()))
                    or not LABEL.match(block.text)
                    or block.excluded_reason
                ):
                    continue
                center = (block.bbox.left + block.bbox.right) / 2
                if not table.bbox.left - 5 <= center <= table.bbox.right + 5:
                    continue
                gap = table.bbox.top - block.bbox.bottom
                if -5 <= gap <= 120:
                    candidates.append((abs(gap), block))
            if candidates:
                _, caption = min(candidates, key=lambda pair: pair[0])
                table.caption = caption.text
                table.caption_block_ids = [caption.id]
                used.add(caption.id)
                for block in blocks:
                    if (
                        block.page == table.page
                        and block.kind == "text"
                        and not block.excluded_reason
                        and caption.bbox.bottom <= block.bbox.top
                        and block.bbox.bottom <= table.bbox.top + 2
                        and table.bbox.left - 5 <= block.bbox.left < table.bbox.right
                        and len(block.text) <= 600
                    ):
                        table.caption += " " + block.text
                        table.caption_block_ids.append(block.id)
                match = LABEL.match(table.caption)
                if match:
                    table.label = f"Table {match.group(1)}"
                    table.structure_warnings.append("表注按页内坐标补充关联，请对照原页核对。")
        if match := LABEL.match(table.caption):
            pattern = re.compile(rf"(?:Table|表)\s*{re.escape(match.group(1))}(?![\da-z])", re.I)
            table.mention_block_ids = [
                b.id
                for b in blocks
                if not b.excluded_reason
                and b.id not in table.caption_block_ids
                and pattern.search(b.text)
            ]
