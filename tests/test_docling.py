import os

import pytest

from app.core.config import Settings
from app.parsing.runner import DoclingRunner
from app.repositories.papers import PaperRepository
from app.services.ingestion import IngestionService


@pytest.mark.docling
@pytest.mark.skipif(os.getenv("RUN_DOCLING_TESTS") != "1", reason="Requires local layout model")
def test_real_pdf_layout_metadata_figures_and_persistence(pdf_path, tmp_path):
    settings = Settings(data_dir=tmp_path / "library")
    repository = PaperRepository(settings.data_dir / "library.sqlite3")
    document, _ = IngestionService(settings, repository, DoclingRunner(300)).ingest(
        pdf_path, "scientific.pdf"
    )
    assert document.status == "parsed", document.error_message
    paper = document.parsed
    assert paper is not None
    assert paper.metadata.title == "Evidence Guided Research Systems"
    assert paper.metadata.authors == ["Ada Researcher", "Bo Scientist"]
    assert paper.metadata.doi == "10.5555/research-fixture.2024"
    assert paper.metadata.publication_year == 2024
    assert paper.metadata.venue == "Journal of Reproducible Research (2024)"
    assert paper.metric.status == "missing" and paper.metric.value is None
    body = "\n".join(b.text for b in paper.blocks if not b.excluded_reason)
    ordered = [
        "Evidence Guided Research Systems",
        "Abstract",
        "1 Introduction",
        "LEFT_ONE begins",
        "LEFT_TWO continues",
        "LEFT_THREE finishes",
        "RIGHT_ONE begins",
        "RIGHT_TWO must",
        "RIGHT_THREE ends",
    ]
    positions = [body.index(value) for value in ordered]
    assert positions == sorted(positions)
    assert "Important footnote" in body and "Journal of Reproducible Research" not in body
    assert all(b.excluded_reason for b in paper.blocks if b.raw_text.startswith("Page "))
    assert len(paper.pages) == 3 and len(paper.figures) == 2
    assert {f.label for f in paper.figures} == {"Figure 1", "Figure 2"}
    for figure in paper.figures:
        assert figure.caption.startswith(figure.label)
        assert figure.caption_block_ids and figure.mention_block_ids
        assert figure.bbox.right > figure.bbox.left and figure.bbox.bottom > figure.bbox.top
        asset = settings.data_dir / "papers" / document.id / figure.image
        assert asset.read_bytes().startswith(b"\x89PNG")
    reopened = PaperRepository(settings.data_dir / "library.sqlite3").get(document.id)
    assert reopened.parsed == paper
