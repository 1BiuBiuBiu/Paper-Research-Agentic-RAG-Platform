from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from app.api import create_app
from app.core.errors import AppError
from app.ui_components.batch_import import (
    MAX_FILE_BYTES,
    ImportItem,
    recover_queue,
    run_queue,
    stage_files,
)
from app.ui_components.client import ClientError
from scripts.make_fixture import make_fixture
from tests.conftest import FixtureParser


@dataclass
class Selected:
    name: str
    content: bytes

    @property
    def size(self):
        return len(self.content)

    def getvalue(self):
        return self.content


def requester(client, calls):
    def request(method, path, **kwargs):
        calls.append((method, path))
        response = client.request(method, path, **kwargs)
        if response.is_error:
            raise ClientError(response.json()["message"])
        return response.json()

    return request


def test_batch_continues_after_invalid_file_and_retains_duplicates_and_distinct_same_name(
    client, pdf_path, tmp_path
):
    other = make_fixture(tmp_path / "other.pdf", title="Different article").read_bytes()
    files = [
        Selected("same.pdf", pdf_path.read_bytes()),
        Selected("broken.pdf", b"not a PDF"),
        Selected("renamed.pdf", pdf_path.read_bytes()),
        Selected("same.pdf", other),
    ]
    queue = []
    assert stage_files(files, queue) == 4
    assert stage_files(files, queue) == 0
    calls, changes = [], []
    run_queue(queue, requester(client, calls), lambda: changes.append(queue[0].status))
    assert [i.status for i in queue] == ["parsed", "error", "duplicate", "parsed"]
    assert queue[0].document_id == queue[2].document_id != queue[3].document_id
    assert client.get("/documents").json()["total"] == 2
    assert all(not i.content for i in queue if i.document_id)
    assert queue[1].content and len(changes) == 8
    calls.clear()
    run_queue(queue, requester(client, calls), lambda: None)
    assert calls == [("POST", "/documents")]


def test_failed_parse_retries_same_record_without_upload_or_overwriting_original(
    settings, pdf_path
):
    class FailOnce(FixtureParser):
        attempts = 0

        def parse(self, source, assets):
            self.attempts += 1
            if self.attempts == 1:
                raise AppError("temporary_failure", "模型暂时不可用")
            return super().parse(source, assets)

    with TestClient(create_app(settings, FailOnce())) as client:
        queue, calls = [], []
        stage_files([Selected("paper.pdf", pdf_path.read_bytes())], queue)
        run_queue(queue, requester(client, calls), lambda: None)
        item = queue[0]
        identifier = item.document_id
        assert item.status == "failed" and not item.content
        original = settings.data_dir / "papers" / identifier / "original.pdf"
        assert original.read_bytes() == pdf_path.read_bytes()
        calls.clear()
        run_queue(queue, requester(client, calls), lambda: None)
        assert calls == [
            ("GET", f"/documents/{identifier}"),
            ("POST", f"/documents/{identifier}/retry"),
        ]
        assert item.status == "parsed" and item.document_id == identifier
        assert original.read_bytes() == pdf_path.read_bytes()


def test_lost_success_response_recovers_by_hash_without_duplicate_records(client, pdf_path):
    queue, calls = [], []
    stage_files([Selected("paper.pdf", pdf_path.read_bytes())], queue)
    request = requester(client, calls)

    def lose_response(method, path, **kwargs):
        request(method, path, **kwargs)
        raise ClientError("无法连接服务或响应无效")

    run_queue(queue, lose_response, lambda: None)
    assert queue[0].status == "error" and queue[0].content
    run_queue(queue, request, lambda: None)
    assert queue[0].status == "duplicate"
    assert client.get("/documents").json()["total"] == 1


def test_batch_limits_are_atomic_and_checked_before_loading_content():
    class Oversize:
        name = "huge.pdf"
        size = MAX_FILE_BYTES + 1

        def getvalue(self):
            raise AssertionError("must validate before loading content")

    queue = []
    with pytest.raises(ValueError, match="20"):
        stage_files([Oversize()] * 21, queue)
    with pytest.raises(ValueError, match="50 MiB"):
        stage_files([Oversize()], queue)
    Oversize.size = MAX_FILE_BYTES
    with pytest.raises(ValueError, match="200 MiB"):
        stage_files([Oversize()] * 5, queue)
    assert queue == []


def test_import_page_supports_multiple_files_does_not_resubmit_and_clears_only_queue(monkeypatch):
    from app.ui_components import importer

    calls = []
    monkeypatch.setattr(importer, "api", lambda *args, **kwargs: calls.append(args))
    app = AppTest.from_string("from app.ui_components.importer import import_page\nimport_page()")
    app.session_state["import:queue"] = [
        ImportItem("ready.pdf", "digest", 1, b"", status="parsed", document_id="existing")
    ]
    app.run()
    assert not app.exception and app.get("file_uploader")[0].proto.multiple_files
    assert len(app.dataframe) == 1
    assert next(b for b in app.button if b.label == "开始／继续解析").disabled
    app.run()
    assert calls == []
    next(b for b in app.button if b.label == "清空导入队列").click().run()
    assert not app.exception and not app.dataframe and calls == []


def test_interrupted_session_marks_item_retryable_without_automatic_request():
    queue = [ImportItem("a.pdf", "digest", 3, b"pdf", status="processing")]
    recover_queue(queue)
    assert queue[0].status == "error" and queue[0].content == b"pdf"


def test_import_button_processes_pending_batch_and_retains_results_across_reruns(
    client, pdf_path, monkeypatch
):
    from app.ui_components import importer

    calls, queue = [], []
    stage_files(
        [Selected("broken.pdf", b"invalid"), Selected("paper.pdf", pdf_path.read_bytes())], queue
    )
    monkeypatch.setattr(importer, "api", requester(client, calls))
    app = AppTest.from_string("from app.ui_components.importer import import_page\nimport_page()")
    app.session_state["import:queue"] = queue
    app.run()
    assert calls == []
    next(b for b in app.button if b.label == "开始／继续解析").click().run()
    assert not app.exception and app.warning
    assert [i.status for i in app.session_state["import:queue"]] == ["error", "parsed"]
    assert len(calls) == 2 and client.get("/documents").json()["total"] == 1
    app.run()
    assert len(calls) == 2
