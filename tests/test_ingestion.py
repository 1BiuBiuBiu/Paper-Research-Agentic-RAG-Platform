from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter

from app.api import create_app
from app.core.config import Settings
from app.core.errors import AppError
from scripts.make_fixture import make_fixture
from tests.conftest import FixtureParser


def upload(client: TestClient, pdf: Path, name: str = "paper.pdf") -> dict:
    response = client.post(
        "/documents", files={"file": (name, pdf.read_bytes(), "application/pdf")}
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_duplicate_same_name_restart_and_assets(client, settings, pdf_path, tmp_path):
    first = upload(client, pdf_path)
    identifier = first["document"]["id"]
    assert first["document"]["status"] == "parsed"
    paper_root = settings.data_dir / "papers"
    files = sorted(str(p.relative_to(paper_root)) for p in paper_root.rglob("*") if p.is_file())
    duplicate = upload(client, pdf_path, name="renamed.pdf")
    assert duplicate["duplicate"] is True and duplicate["document"]["id"] == identifier
    assert (
        sorted(str(p.relative_to(paper_root)) for p in paper_root.rglob("*") if p.is_file())
        == files
    )
    assert len(list(paper_root.glob("*/original.pdf"))) == 1
    other = make_fixture(tmp_path / "other.pdf", title="Another Research Article")
    assert upload(client, other)["document"]["id"] != identifier
    with TestClient(create_app(settings, FixtureParser())) as restarted:
        assert restarted.get("/documents").json()["total"] == 2
        document = restarted.get(f"/documents/{identifier}").json()
        image = document["parsed"]["pages"][0]["image"]
        response = restarted.get(f"/documents/{identifier}/assets/{image}")
        assert response.status_code == 200 and response.content.startswith(b"\x89PNG")
        original = restarted.get(f"/documents/{identifier}/original")
        assert original.content == pdf_path.read_bytes()
        assert original.headers["content-disposition"].startswith("inline")
        assert restarted.get(f"/documents/{identifier}/assets/library.sqlite3").status_code == 404


def test_metadata_metric_edit_and_restart(client, settings, pdf_path):
    identifier = upload(client, pdf_path)["document"]["id"]
    url = f"/documents/{identifier}"
    patch = {"title": "Human checked title", "venue_type": "journal", "venue": "Test Journal"}
    response = client.patch(url + "/metadata", json=patch)
    assert response.status_code == 200
    assert response.json()["parsed"]["metadata"]["provenance"]["title"]["source"] == "manual"
    metric = {"year": 2024, "value": 3.2, "source": "Self-authored test indicator"}
    assert client.put(url + "/metric", json=metric).status_code == 200
    with TestClient(create_app(settings, FixtureParser())) as restarted:
        paper = restarted.get(url).json()["parsed"]
        assert paper["metadata"]["title"] == patch["title"]
        assert paper["metric"]["year"] == 2024 and paper["metric"]["value"] == 3.2
        paper = restarted.patch(url + "/metadata", json={"venue": "Changed Journal"}).json()[
            "parsed"
        ]
        assert paper["metric"]["status"] == "missing"
        paper = restarted.patch(url + "/metadata", json={"venue_type": "conference"}).json()[
            "parsed"
        ]
        assert paper["metric"]["status"] == "not_applicable"
        assert restarted.put(url + "/metric", json=metric).status_code == 409
    assert client.patch(url + "/metadata", json={"doi": "not-a-doi"}).status_code == 422


def test_reject_bad_files_and_paths(client, pdf_path, tmp_path):
    for name, body in [
        ("paper.txt", b"hello"),
        ("../bad.pdf", pdf_path.read_bytes()),
        ("bad.pdf", b"%PDF-broken"),
    ]:
        assert client.post("/documents", files={"file": (name, body)}).status_code == 422
    writer = PdfWriter()
    for page in PdfReader(pdf_path).pages:
        writer.add_page(page)
    writer.encrypt("secret")
    encrypted = tmp_path / "encrypted.pdf"
    writer.write(encrypted)
    result = client.post("/documents", files={"file": ("encrypted.pdf", encrypted.read_bytes())})
    assert result.status_code == 422 and result.json()["code"] == "encrypted_pdf"
    blank = PdfWriter()
    blank.add_blank_page(width=600, height=800)
    path = tmp_path / "blank.pdf"
    blank.write(path)
    assert (
        client.post("/documents", files={"file": ("blank.pdf", path.read_bytes())}).json()["code"]
        == "no_text"
    )
    assert client.get("/documents/not-a-uuid").status_code == 422
    assert client.get("/documents?offset=-1").status_code == 422


def test_windows_filename_normalized_by_multipart_parser(client, pdf_path):
    # python-multipart normalizes browser-supplied Windows paths to their basename.
    result = upload(client, pdf_path, "C:\\bad.pdf")
    assert result["document"]["filename"] == "bad.pdf"
    assert result["document"]["id"] != "bad"


def test_upload_page_limits_and_origin(settings, pdf_path):
    limited = Settings(data_dir=settings.data_dir, max_upload_bytes=128)
    with TestClient(create_app(limited, FixtureParser())) as test:
        assert (
            test.post("/documents", files={"file": ("test.pdf", pdf_path.read_bytes())}).status_code
            == 413
        )
    limited = Settings(data_dir=settings.data_dir, max_pages=1)
    with TestClient(create_app(limited, FixtureParser())) as test:
        result = test.post("/documents", files={"file": ("test.pdf", pdf_path.read_bytes())})
        assert result.status_code == 413 and result.json()["code"] == "page_limit"
        result = test.post("/documents", headers={"origin": "https://attacker.example"})
        assert result.status_code == 403


def test_failure_retry_and_interrupted_recovery(settings, pdf_path):
    class FailedParser:
        def parse(self, source, assets):
            raise AppError("test_failure", "Model missing")

    with TestClient(create_app(settings, FailedParser())) as test:
        document = upload(test, pdf_path)["document"]
        assert document["status"] == "failed" and document["error_code"] == "test_failure"
        identifier = document["id"]
    with TestClient(create_app(settings, FixtureParser())) as test:
        assert test.post(f"/documents/{identifier}/retry").json()["status"] == "parsed"
        repo = test.app.state.repository
        record = repo.get(identifier)
        record.status, record.parsed = "parsing", None
        repo.save(record)
    with TestClient(create_app(settings, FixtureParser())) as test:
        record = test.get(f"/documents/{identifier}").json()
        assert record["status"] == "failed" and record["error_code"] == "interrupted"
