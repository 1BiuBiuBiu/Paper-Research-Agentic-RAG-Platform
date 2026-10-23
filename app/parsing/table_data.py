from html import escape

from app.domain.papers import PaperTable


def grid_warnings(table: PaperTable) -> list[str]:
    if not valid_grid(table):
        return ["行列区间无效或重叠，仅保留原区域供核对，不用于数值检索。"]
    covered = {
        (r, c)
        for cell in table.cells
        for r in range(cell.row_start, cell.row_end)
        for c in range(cell.col_start, cell.col_end)
    }
    if len(covered) < table.rows * table.columns:
        return ["存在未覆盖网格，可能是空白或合并识别遗漏；请核对原表，不自动推断合并。"]
    return []


def valid_grid(table: PaperTable) -> bool:
    if not 0 < table.rows <= 2000 or not 0 < table.columns <= 200 or not table.cells:
        return False
    occupied: set[tuple[int, int]] = set()
    for cell in table.cells:
        if not (
            0 <= cell.row_start < cell.row_end <= table.rows
            and 0 <= cell.col_start < cell.col_end <= table.columns
        ):
            return False
        for row in range(cell.row_start, cell.row_end):
            for col in range(cell.col_start, cell.col_end):
                if (row, col) in occupied:
                    return False
                occupied.add((row, col))
    return True


def table_html(table: PaperTable) -> str:
    """Only fixed tags/validated integer spans; never execute PDF-provided markup."""
    if not valid_grid(table):
        return ""
    starts = {(cell.row_start, cell.col_start): cell for cell in table.cells}
    covered = {
        (r, c)
        for cell in table.cells
        for r in range(cell.row_start, cell.row_end)
        for c in range(cell.col_start, cell.col_end)
        if (r, c) != (cell.row_start, cell.col_start)
    }
    rows = []
    for row in range(table.rows):
        cells = []
        for col in range(table.columns):
            if (row, col) in covered:
                continue
            cell = starts.get((row, col))
            if cell is None:
                cells.append('<td aria-label="未提取单元格">—</td>')
                continue
            tag = "th" if cell.column_header or cell.row_header else "td"
            cells.append(
                f'<{tag} rowspan="{cell.row_end - cell.row_start}" '
                f'colspan="{cell.col_end - cell.col_start}">{escape(cell.text)}</{tag}>'
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<table class="paper-table">' + "".join(rows) + "</table>"


def _variant(text: str) -> str:
    """Return the remainder when a label is a sub-row variant such as '-w/ WisdoM'."""
    stripped = text.strip()
    lowered = stripped.lower()
    for prefix in ("-w/", "+w/", "w/o", "w/"):
        if lowered.startswith(prefix):
            return stripped[len(prefix) :].strip(" -–—")
    return ""


def row_labels(table: PaperTable) -> dict[int, str]:
    """Rebuild row labels, repairing a variant cell that Docling merged into the row above.

    Layouts like "Model | -w/ WisdoM" parse as row N = "-w/ WisdoM Model" and row N+1 =
    "-w/ WisdoM", which makes the baseline row look like the enhanced one. When a label
    starts with the next row's whole label and that label is a variant, the model name is
    moved back to its own row and the variant is re-attached as "Model / variant".
    """
    labels: dict[int, str] = {}
    for row in range(table.rows):
        cells = sorted(
            (
                cell
                for cell in table.cells
                if cell.row_start <= row < cell.row_end and not cell.column_header
            ),
            key=lambda cell: cell.col_start,
        )
        if cells:
            labels[row] = cells[0].text.strip()
    for row in sorted(labels)[:-1]:
        current, following = labels[row], labels.get(row + 1, "")
        if following and _variant(following) and current.startswith(following):
            remainder = current[len(following) :].strip(" -–—/")
            if remainder:
                labels[row] = remainder
                labels[row + 1] = f"{remainder} / {following}"
    return labels


def row_evidence(table: PaperTable, section: str = "") -> list[str]:
    if not valid_grid(table):
        return []
    headers = [
        " / ".join(
            cell.text
            for cell in table.cells
            if cell.column_header and cell.col_start <= col < cell.col_end
        )
        or f"column {col + 1}"
        for col in range(table.columns)
    ]
    labels = row_labels(table)
    output: list[str] = []
    for row in range(table.rows):
        cells = sorted(
            (c for c in table.cells if c.row_start <= row < c.row_end and not c.column_header),
            key=lambda c: c.col_start,
        )
        if not cells:
            continue
        prefix = (
            f"{table.label or table.id}: {table.caption[:300]}"
            f"{' | ' + section if section else ''} | row {row + 1} | 自动表格，待核对 | "
        )
        part = prefix
        for cell in cells:
            label = " / ".join(headers[cell.col_start : cell.col_end])
            text = labels.get(row, cell.text) if cell is cells[0] else cell.text
            # Retain each value's column and row identity even if a long cell needs splitting.
            for start in range(0, max(1, len(text)), 350):
                value = f"{label[:100]} = {text[start : start + 350]}; "
                if len(part) + len(value) > 700:
                    output.append(part)
                    part = prefix
                part += value
        if part != prefix:
            output.append(part)
    return output
