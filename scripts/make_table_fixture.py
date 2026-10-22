"""Self-authored merged-header table fixture with known numeric ground truth."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle


def make_table_fixture(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
    pdf.setTitle("Structured Experimental Results")
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawString(48, 730, "Structured Experimental Results")
    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(48, 680, "1 Experimental Results")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(48, 650, "Table 1 compares the measured accuracy and F1 of two methods.")
    pdf.drawString(48, 615, "Table 1. Evaluation on the fixed benchmark.")
    table = Table(
        [
            ["Method", "Evaluation Metrics", ""],
            ["", "Accuracy", "F1"],
            ["Baseline", "0.731", "0.704"],
            ["Proposed", "0.812", "0.793"],
        ],
        colWidths=[175, 155, 155],
        rowHeights=[40] * 4,
    )
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (0, 1)),
                ("SPAN", (1, 0), (2, 0)),
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#e6edf7")),
                ("FONT", (0, 0), (-1, 1), "Helvetica-Bold", 12),
                ("FONT", (0, 2), (-1, -1), "Helvetica", 12),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]
        )
    )
    table.wrapOn(pdf, 485, 200)
    table.drawOn(pdf, 48, 430)
    pdf.setFont("Helvetica", 10)
    pdf.drawString(
        48, 400, "The values are fractions. Higher accuracy and F1 indicate better performance."
    )
    pdf.setFont("Helvetica-Oblique", 14)
    pdf.drawString(240, 340, "E = m c^2")
    pdf.setFont("Helvetica", 8)
    pdf.drawString(48, 42, "* Corresponding author. E-mail address: demo@example.com")
    pdf.drawString(48, 28, "Received 1 February 2025; Accepted 29 April 2025")
    pdf.save()
    return path


if __name__ == "__main__":
    print(make_table_fixture(Path("tests/fixtures/structured.pdf")))
