"""Generate a self-authored paper with known text order, furniture and figures."""

from pathlib import Path

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def make_fixture(path: Path, *, title: str = "Evidence Guided Research Systems") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
    pdf.setTitle(title)
    pdf.setAuthor("Ada Researcher; Bo Scientist")
    for number in range(1, 4):
        pdf.setFont("Helvetica", 8)
        pdf.drawString(48, 770, "Journal of Reproducible Research (2024)")
        pdf.drawRightString(564, 22, f"Page {number}")
        if number == 1:
            pdf.setFont("Helvetica-Bold", 20)
            pdf.drawCentredString(306, 724, title)
            pdf.setFont("Helvetica", 11)
            pdf.drawCentredString(306, 696, "Authors: Ada Researcher; Bo Scientist")
            pdf.setFont("Helvetica", 9)
            pdf.drawCentredString(306, 678, "DOI: 10.5555/research-fixture.2024")
            pdf.drawCentredString(306, 664, "ISSN 1234-567X")
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(48, 630, "Abstract")
            _lines(
                pdf,
                [
                    "This paper tests reliable scientific document extraction.",
                    "We compare two-column reading order and faithful figure grounding.",
                ],
                48,
                610,
            )
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(48, 554, "1 Introduction")
            _lines(
                pdf,
                [
                    "LEFT_ONE begins the first column.",
                    "LEFT_TWO continues the same argument.",
                    "LEFT_THREE finishes before the right column.",
                ],
                48,
                530,
            )
            _lines(
                pdf,
                [
                    "RIGHT_ONE begins after the left column.",
                    "RIGHT_TWO must never interrupt LEFT_TWO.",
                    "RIGHT_THREE ends this reading-order test.",
                ],
                322,
                530,
            )
            pdf.setFont("Helvetica", 8)
            pdf.drawString(48, 54, "1 Important footnote: preserve this scientific qualification.")
        elif number == 2:
            pdf.setFont("Helvetica-Bold", 15)
            pdf.drawString(48, 724, "2 Results")
            _lines(
                pdf,
                [
                    "Figure 1 presents a reproducible increasing trend.",
                    "The original vector chart is evidence, not a decorative image.",
                ],
                48,
                700,
            )
            _chart(pdf, 70, 380)
            pdf.setFont("Helvetica", 10)
            pdf.drawString(48, 350, "Figure 1. Vector chart: performance rises from 2 to 8.")
            _lines(
                pdf,
                [
                    "The cross-column caption belongs to the entire chart.",
                    "This statement follows the figure in the reading order.",
                ],
                48,
                320,
            )
        else:
            pdf.setFont("Helvetica-Bold", 15)
            pdf.drawString(48, 724, "3 Image Evidence")
            _lines(pdf, ["Figure 2 shows the same self-authored chart as a bitmap."], 48, 700)
            from io import BytesIO

            from PIL import Image, ImageDraw

            image = Image.new("RGB", (700, 300), "white")
            draw = ImageDraw.Draw(image)
            draw.line([(40, 30), (40, 260), (660, 260)], fill="black", width=4)
            draw.line([(60, 230), (220, 180), (420, 120), (640, 50)], fill="blue", width=6)
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            pdf.drawImage(ImageReader(buffer), 60, 430, width=490, height=210)
            pdf.setFont("Helvetica", 10)
            pdf.drawString(48, 400, "Figure 2. Bitmap chart with a rising blue line.")
            pdf.setFont("Helvetica-Bold", 12)
            pdf.drawString(48, 330, "References")
            _lines(pdf, ["Other Author. A different work. DOI: 10.9999/not-this-paper."], 48, 310)
        pdf.showPage()
    pdf.save()
    return path


def _lines(pdf: canvas.Canvas, lines: list[str], x: int, y: int) -> None:
    pdf.setFont("Helvetica", 10)
    for number, line in enumerate(lines):
        pdf.drawString(x, y - number * 18, line)


def _chart(pdf: canvas.Canvas, x: int, y: int) -> None:
    pdf.setLineWidth(1.2)
    pdf.line(x, y, x, y + 230)
    pdf.line(x, y, x + 460, y)
    pdf.setFont("Helvetica", 10)
    for index, value in enumerate([2, 4, 6, 8]):
        cx, cy = x + 40 + index * 120, y + value * 25
        pdf.circle(cx, cy, 4, fill=1)
        pdf.drawString(cx - 3, cy + 12, str(value))
        if index:
            pdf.line(cx - 120, cy - 50, cx, cy)
    pdf.drawString(x + 170, y - 25, "Experiment index")


if __name__ == "__main__":
    make_fixture(Path("tests/fixtures/scientific.pdf"))
    print("Created self-authored tests/fixtures/scientific.pdf")
