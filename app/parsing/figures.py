import re

from app.domain.papers import Block, Figure

LABEL = re.compile(r"(?:Figure|Fig\.?|图)\s*(\d+[a-z]?)", re.I)


def associate_figures(figures: list[Figure], blocks: list[Block]) -> None:
    title_tops = [b.bbox.top for b in blocks if b.page == 1 and b.kind == "title"]
    publisher = any(b.kind == "publisher_header" for b in blocks)
    for figure in figures:
        if match := LABEL.search(figure.caption):
            figure.caption = figure.caption[match.start() :]
            figure.label = f"Figure {match.group(1)}"
            figure.role = "research"
            figure.classification_reason = "已匹配带编号的论文图注"
            number = re.escape(match.group(1))
            pattern = re.compile(rf"(?:Figure|Fig\.?|图)\s*{number}(?![\da-z])", re.I)
            figure.mention_block_ids = [
                b.id
                for b in blocks
                if b.id not in figure.caption_block_ids
                and not b.excluded_reason
                and pattern.search(b.text)
            ]
        else:
            figure.label = None
            box = figure.bbox
            small = box.right - box.left < 36 and box.bottom - box.top < 36
            masthead = (
                figure.page == 1 and publisher and title_tops and box.bottom < min(title_tops)
            )
            if small or masthead:
                figure.role = "decoration"
                figure.classification_reason = "无图注的小图标或出版商页眉装饰"
            else:
                figure.role = "unclassified"
                figure.classification_reason = "缺少编号图注，保留供人工核对"
        for block in blocks:
            if block.page != figure.page or block.kind in {"caption", "footnote"}:
                continue
            box, area = block.bbox, figure.bbox
            if (
                area.left <= box.left
                and area.top <= box.top
                and area.right >= box.right
                and area.bottom >= box.bottom
            ):
                block.excluded_reason = block.excluded_reason or "figure_region_text"
