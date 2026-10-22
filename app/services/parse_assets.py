from app.domain.papers import ParsedPaper


def prefix_assets(paper: ParsedPaper, generation: str) -> None:
    for page in paper.pages:
        page.image = f"{generation}/{page.image}"
    for figure in paper.figures:
        figure.image = f"{generation}/{figure.image}"
    for table in paper.tables:
        if table.image:
            table.image = f"{generation}/{table.image}"
    for equation in paper.equations:
        equation.image = f"{generation}/{equation.image}"
    paper.exports = {key: f"{generation}/{value}" for key, value in paper.exports.items()}
