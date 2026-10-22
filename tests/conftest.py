from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.core.config import Settings
from app.domain.papers import Block, Box, PageInfo, PaperMetadata, ParsedPaper
from scripts.make_fixture import make_fixture


class FixtureParser:
    """Application persistence tests only. Never claimed as PDF parser verification."""

    def parse(self, source: Path, assets: Path) -> ParsedPaper:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(source)
        image = pdf[0].render(scale=1).to_pil()
        image.save(assets / "page-1.png")
        pdf.close()
        return ParsedPaper(
            parser="test-double",
            pages=[PageInfo(number=1, width=612, height=792, image="page-1.png")],
            blocks=[
                Block(
                    id="b1",
                    page=1,
                    order=0,
                    kind="title",
                    raw_text="Fixture",
                    text="Fixture",
                    bbox=Box(left=48, top=40, right=550, bottom=60),
                )
            ],
            figures=[],
            metadata=PaperMetadata(title="Fixture"),
        )


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    return make_fixture(tmp_path / "paper.pdf")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "library")


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings, FixtureParser()), base_url="http://127.0.0.1") as test:
        yield test
