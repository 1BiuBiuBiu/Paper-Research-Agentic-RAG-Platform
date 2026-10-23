"""Page text for the automatic index build: “索引中”, failure and retry."""

from dataclasses import replace

from fastapi.testclient import TestClient

from app.api import create_app
from tests.qa_helpers import RecordingProvider, RecordingReranker
from tests.test_agent_ui import app_ui, nav
from tests.test_index_boundaries import (
    FailableEmbedder,
    GatedEmbedder,
    TextParser,
    upload,
    wait_status,
)


def auto_client(settings, embedder):
    app = create_app(
        replace(settings, auto_index=True),
        TextParser(),
        embedder,
        RecordingProvider(),
        RecordingReranker(),
    )
    return TestClient(app, base_url="http://127.0.0.1")


def test_agent_page_reports_indexing_then_enables_the_question_box(settings, monkeypatch, pdf_path):
    embedder = GatedEmbedder()
    with auto_client(settings, embedder) as client:
        document = upload(client, pdf_path)
        page = app_ui(client, monkeypatch).run()
        nav(page, "Agent 平台")
        assert not page.exception
        assert any("正在建立索引" in caption.value for caption in page.caption)
        assert page.chat_input[0].disabled
        embedder.gate.set()
        wait_status(client, document["id"], "ready")
        page.run()
        assert not page.exception
        assert not page.chat_input[0].disabled
        assert not any("正在建立索引" in caption.value for caption in page.caption)


def test_agent_page_reports_index_failure_and_retries_it(settings, monkeypatch, pdf_path):
    embedder = FailableEmbedder()
    with auto_client(settings, embedder) as client:
        document = upload(client, pdf_path)
        wait_status(client, document["id"], "failed")
        page = app_ui(client, monkeypatch).run()
        nav(page, "Agent 平台")
        assert not page.exception
        assert any("索引失败" in caption.value for caption in page.caption)
        assert page.chat_input[0].disabled
        embedder.fail = False
        next(button for button in page.button if button.label == "重试失败的索引").click().run()
        assert not page.exception
        assert wait_status(client, document["id"], "ready")["chunks"] > 0
        assert not page.chat_input[0].disabled
