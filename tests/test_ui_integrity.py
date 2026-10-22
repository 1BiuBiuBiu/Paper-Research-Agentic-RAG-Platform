import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.domain.papers import Document, PaperMetadata, ParsedPaper, utc_now
from app.ui_components import metadata as editor


def test_document_switch_preserves_identity_and_only_saves_changed_fields(tmp_path, monkeypatch):
    records = []
    for name in ("A", "B"):
        records.append(
            Document(
                id=name,
                filename=f"{name}.pdf",
                sha256=name,
                status="parsed",
                created_at=utc_now(),
                updated_at=utc_now(),
                parsed=ParsedPaper(
                    parser="test",
                    pages=[],
                    blocks=[],
                    figures=[],
                    metadata=PaperMetadata(
                        title=f"Title {name}", abstract=f"Original abstract {name}"
                    ),
                ),
            ).model_dump()
        )
    path = tmp_path / "records.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    calls = []
    monkeypatch.setattr(editor, "api", lambda *args, **kw: calls.append((args, kw)))
    source = f"""import json
import streamlit as st
from app.ui_components.metadata import metadata_editor
records=json.loads(open({str(path)!r},encoding='utf-8').read())
name=st.radio('Document',['A','B'],key='current')
metadata_editor(next(d for d in records if d['id']==name))
"""
    app = AppTest.from_string(source).run()
    app.text_area[0].input("Unsaved text from A")
    app.radio[0].set_value("B").run()
    assert not app.exception
    assert app.text_area[0].value == "Original abstract B"
    assert app.text_input[0].value == "Title B"
    app.text_input[0].input("Human title B")
    app.button[0].click().run()
    assert calls[0][0] == ("PATCH", "/documents/B/metadata")
    assert calls[0][1]["json"] == {"title": "Human title B"}


def setup_library(client, monkeypatch):
    from app.ui_components import categories, detail, library

    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        response = client.request(method, path, **kwargs)
        assert response.status_code < 400, response.text
        return response.json()

    for module in (library, categories, detail):
        monkeypatch.setattr(module, "api", request)
    monkeypatch.setattr(
        detail,
        "image_bytes",
        lambda doc, asset: client.get(f"/documents/{doc}/assets/{asset}").content,
    )
    return calls


def library_app():
    return AppTest.from_string("from app.ui_components.library import library_page\nlibrary_page()")


def open_paper(app, identifier):
    app.query_params["paper"] = identifier
    app.run()


def button(app, key):
    return next(b for b in app.button if b.key == key)


def test_library_move_and_empty_folder_have_no_stale_reader(client, pdf_path, monkeypatch):
    from tests.test_ingestion import upload

    identifier = upload(client, pdf_path)["document"]["id"]
    folder = client.post("/categories", json={"name": "Topic"}).json()["id"]
    setup_library(client, monkeypatch)
    app = library_app().run()
    assert not app.exception and not app.get("image")
    open_paper(app, identifier)
    assert app.radio[0].value == "原文与解析" and len(app.get("image")) == 1
    next(s for s in app.selectbox if s.label == "移入文件夹").set_value(folder).run()
    next(b for b in app.button if b.label == "移动").click().run()
    assert not app.exception and not app.get("image")
    assert client.get(f"/documents/{identifier}").json()["category_id"] == folder
    button(app, "folder:" + folder).click().run()
    open_paper(app, identifier)
    next(s for s in app.selectbox if s.label == "移入文件夹").set_value("uncategorized").run()
    next(b for b in app.button if b.label == "移动").click().run()
    assert not app.exception and not app.get("image")
    assert client.get("/documents", params={"category_id": folder}).json()["total"] == 0


def test_library_opens_reader_directly_and_allows_individual_views(client, pdf_path, monkeypatch):
    from tests.test_ingestion import upload

    identifier = upload(client, pdf_path)["document"]["id"]
    calls = setup_library(client, monkeypatch)
    app = library_app()
    app.session_state["selected_id"] = identifier
    app.run()
    assert not app.exception and not app.radio and not app.get("image")
    assert not app.get("file_uploader") and not app.dataframe
    assert not any(path == f"/documents/{identifier}" for _, path, _ in calls)
    assert "文献列表" in app.html[0].value
    open_paper(app, identifier)
    assert not app.exception and app.radio[0].value == "原文与解析"
    assert len(app.get("image")) == 1
    app.radio[0].set_value("仅解析结果").run()
    assert not app.exception and not app.get("image")
    app.radio[0].set_value("仅原始 PDF").run()
    assert not app.exception and len(app.get("image")) == 1
    next(b for b in app.button if b.label == "返回论文库").click().run()
    assert not app.exception and not app.radio and not app.get("image")
    next(t for t in app.text_input if t.label == "搜索文献").input("no-match").run()
    assert not app.exception and not app.radio and not app.get("image")
    assert not app.html


def test_forged_paper_link_cannot_load_outside_folder(client, pdf_path, monkeypatch):
    from tests.test_ingestion import upload

    identifier = upload(client, pdf_path)["document"]["id"]
    empty = client.post("/categories", json={"name": "Empty"}).json()["id"]
    calls = setup_library(client, monkeypatch)
    app = library_app()
    app.query_params.update({"folder": empty, "paper": identifier})
    app.run()
    assert not app.exception and app.warning and not app.get("image")
    assert not any(path == f"/documents/{identifier}" for _, path, _ in calls)


def test_folder_create_and_rename_are_inside_library(client, monkeypatch):
    setup_library(client, monkeypatch)
    app = library_app().run()
    next(t for t in app.text_input if t.label == "新文件夹名称").input("New Research")
    next(b for b in app.button if b.label == "新建文件夹").click().run()
    assert not app.exception
    next(t for t in app.text_input if t.label == "文件夹名称").input("Renamed Research")
    next(b for b in app.button if b.label == "重命名").click().run()
    assert not app.exception
    assert client.get("/categories").json()["items"][0]["name"] == "Renamed Research"
    assert not any(b.label == "分类管理" for b in app.button)


def test_import_has_its_own_workspace(client, monkeypatch):
    setup_library(client, monkeypatch)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app/main.py").run()
    assert not app.exception and not app.get("file_uploader")
    button(app, "nav:导入").click().run()
    assert not app.exception and len(app.get("file_uploader")) == 1
    assert not any(b.key and b.key.startswith("folder:") for b in app.button)
    button(app, "nav:论文库").click().run()
    assert not app.exception and not app.get("file_uploader")
    assert any(b.key == "folder:all" for b in app.button)


def test_selected_folder_loads_beyond_first_api_page(monkeypatch):
    from app.ui_components import library

    records = [{"id": str(i)} for i in range(205)]
    calls = []

    def request(method, path, **kwargs):
        params = kwargs["params"]
        calls.append(params)
        assert params["category_id"] == "selected-folder"
        offset = params["offset"]
        return {"items": records[offset : offset + 100], "total": len(records)}

    monkeypatch.setattr(library, "api", request)
    result = library.load_documents({"category_id": "selected-folder", "include_samples": False})
    assert [r["id"] for r in result] == [str(i) for i in range(205)]
    assert len(calls) == 3


def test_reader_page_controls_keep_original_and_text_on_same_document_revision(monkeypatch):
    from app.ui_components import detail

    calls = []
    monkeypatch.setattr(
        detail, "original_page", lambda doc, page: calls.append((doc, page["number"]))
    )
    monkeypatch.setattr(detail, "parsed_page", lambda doc, page: calls.append((doc["id"], page)))
    source = """import streamlit as st
from app.ui_components.detail import reader
doc = st.radio("Document", ["A", "B"])
revision = st.radio("Revision", ["old", "new"])
paper = {"pages": [{"number": i} for i in (1, 2, 3)], "blocks": []}
reader({"id": doc, "parsed": paper}, doc + revision)
"""
    app = AppTest.from_string(source).run()
    assert not app.exception and calls[-2:] == [("A", 1), ("A", 1)]
    assert next(b for b in app.button if b.label == "上一页").disabled
    next(b for b in app.button if b.label == "下一页").click().run()
    assert calls[-2:] == [("A", 2), ("A", 2)]
    app.number_input[0].set_value(3).run()
    assert calls[-2:] == [("A", 3), ("A", 3)]
    assert next(b for b in app.button if b.label == "下一页").disabled
    app.radio[0].set_value("B").run()
    assert calls[-2:] == [("B", 1), ("B", 1)]
    app.number_input[0].set_value(2).run()
    app.radio[1].set_value("new").run()
    assert not app.exception and calls[-2:] == [("B", 1), ("B", 1)]
