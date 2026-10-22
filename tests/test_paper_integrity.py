from app.domain.papers import Figure, PageInfo
from app.metadata.extract import extract_metadata
from app.parsing.figures import associate_figures
from app.parsing.front_matter import repair_front_matter
from app.services.extraction_repair import repaired_paper
from app.ui_components.library_table import literature_table
from tests.test_extraction import block
from tests.test_ingestion import upload


def publisher_layout():
    blocks = [
        block("Contents lists available at ScienceDirect", top=60, bottom=70),
        block("Pattern Recognition", kind="title", top=85, bottom=100),
        block("journal homepage: www.example.org/pr", top=115, bottom=125),
        block(
            "Fusion-Net: Context enhanced networks for multimodal sentiment analysis",
            kind="section_header",
            top=160,
            bottom=195,
        ),
        block("Alice Chen , Bob Lu ∗, Carol Wang", top=205, bottom=217),
        block("A B S T R A C T", kind="section_header", top=248, bottom=256),
        block("This is the original scientific abstract.", top=268, bottom=390),
        block("1. Introduction", kind="section_header", top=423, bottom=430),
        block("Pattern Recognition 168 (2025) 111785", kind="page_header", top=30, bottom=45),
    ]
    pages = [PageInfo(number=1, width=595, height=792, image="page.png")]
    return blocks, pages


def test_publisher_masthead_is_venue_and_spaced_abstract_is_not_missing():
    blocks, pages = publisher_layout()
    repair_front_matter(blocks, pages)
    metadata = extract_metadata(blocks)
    assert metadata.title.startswith("Fusion-Net:")
    assert metadata.venue == "Pattern Recognition"
    assert metadata.publication_year == 2025
    assert metadata.abstract == "This is the original scientific abstract."
    assert metadata.authors == ["Alice Chen", "Bob Lu", "Carol Wang"]
    assert all(b.excluded_reason for b in blocks if b.kind == "publisher_header")
    assert metadata.provenance["title"].block_ids != metadata.provenance["venue"].block_ids


def test_author_header_and_abstract_auxiliary_links_do_not_pollute_metadata():
    blocks = [
        block("Do Models Feel? A Study of Emotion Recognition", kind="title", top=90, bottom=120),
        block(
            "Alice Li, Bob Liu, Carol Qiao, David Xu*", kind="section_header", top=140, bottom=152
        ),
        block("Abstract", kind="section_header", top=215, bottom=230),
        block("Emotion recognition is the task under study.", top=240, bottom=500),
        block("Code - https://example.org/repository", top=510, bottom=520),
        block("Extended Version - https://example.org/paper", top=525, bottom=535),
        block("Introduction", kind="section_header", top=550, bottom=560),
        block(
            "The Fortieth AAAI Conference on Artificial Intelligence (AAAI-26)", kind="page_header"
        ),
    ]
    metadata = extract_metadata(blocks)
    assert metadata.abstract == "Emotion recognition is the task under study."
    assert metadata.authors == ["Alice Li", "Bob Liu", "Carol Qiao", "David Xu"]
    assert metadata.publication_year == 2026


def test_logo_icon_and_uncaptioned_images_are_preserved_outside_research_gallery():
    blocks, pages = publisher_layout()
    repair_front_matter(blocks, pages)
    figures = [
        Figure(id="f1", page=1, bbox=block("x", top=60, bottom=120).bbox, image="logo.png"),
        Figure(
            id="f2",
            page=2,
            bbox=block("x", top=50, bottom=350).bbox,
            image="research.png",
            caption="Fig. 1. The full experimental framework.",
        ),
        Figure(id="f3", page=5, bbox=block("x", top=50, bottom=95).bbox, image="unknown.png"),
    ]
    associate_figures(figures, blocks)
    assert [f.role for f in figures] == ["decoration", "research", "unclassified"]
    assert figures[1].label == "Figure 1" and figures[1].id == "f2"
    assert len(figures) == 3


def test_refresh_retains_manual_edits_and_backups_and_checks_pdf_hash(client, pdf_path, settings):
    identifier = upload(client, pdf_path)["document"]["id"]
    url = f"/documents/{identifier}"
    repo = client.app.state.repository

    def seed(document):
        document.parsed.blocks, document.parsed.pages = publisher_layout()

    repo.update(identifier, seed)
    client.patch(url + "/metadata", json={"abstract": "Human corrected abstract."})
    before = repo.get(identifier)
    response = client.post(url + "/refresh-extraction")
    assert response.status_code == 200
    after = repo.get(identifier)
    assert after.parsed.metadata.title.startswith("Fusion-Net:")
    assert after.parsed.metadata.abstract == "Human corrected abstract."
    assert after.parsed.metadata.provenance["abstract"].source == "manual"
    snapshots = list((settings.data_dir / "papers" / identifier / "history").glob("*.json"))
    assert (
        len(snapshots) == 1 and snapshots[0].read_text(encoding="utf-8") == before.model_dump_json()
    )
    assert repaired_paper(after.parsed).metadata.title == after.parsed.metadata.title
    (settings.data_dir / "papers" / identifier / "original.pdf").write_bytes(b"changed")
    assert client.post(url + "/refresh-extraction").json()["code"] == "source_mismatch"
    assert repo.get(identifier) == after


def test_literature_table_escapes_paper_text_and_keeps_navigation_scope():
    from html.parser import HTMLParser
    from urllib.parse import parse_qs, urlparse

    links = []

    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "a":
                links.append(dict(attrs)["href"])
            assert tag not in {"script", "img", "iframe"}

    html = literature_table(
        [
            {
                "id": "A",
                "title": '<img src="https://example.org/x">',
                "filename": "x.pdf",
                "authors": ["<script>unsafe</script>"],
                "venue": "A & B",
                "year": 2025,
                "pages": 9,
                "status": "parsed",
            }
        ],
        "folder-a",
        "x&paper=outside",
    )
    Links().feed(html)
    params = parse_qs(urlparse(links[0]).query)
    assert params == {
        "view": ["library"],
        "folder": ["folder-a"],
        "paper": ["A"],
        "q": ["x&paper=outside"],
    }
    assert "&lt;img src=" in html and "A &amp; B" in html
    assert html.count('<th scope="col">') == 6
