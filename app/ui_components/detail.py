from typing import Any

import streamlit as st

from app.ui_components.client import API_BASE, api, image_bytes
from app.ui_components.figures import figure_gallery
from app.ui_components.metadata import metadata_editor, metric_editor
from app.ui_components.structure import reading_body, structure_view


def paper_detail(document: dict[str, Any]) -> None:
    with st.container(key="paper-detail"):
        detail_workspace(document)


def detail_workspace(document: dict[str, Any]) -> None:
    identifier = document["id"]
    paper = document.get("parsed")
    title = paper["metadata"]["title"] if paper else None
    heading, actions = st.columns([8, 1.2], vertical_alignment="center")
    with heading:
        st.subheader(title or document["filename"])
        st.caption(f"{document['filename']} · 记录 {identifier[:8]}")
    if document.get("is_sample"):
        st.info("这是开发验证用的演示样本。")
    with actions.popover("文献操作"):
        st.link_button("打开原始 PDF", f"{API_BASE}/documents/{identifier}/original")
        if paper and st.button("更新提取规则", key=f"repair:{identifier}"):
            with st.spinner("正在更新提取结果…"):
                api("POST", f"/documents/{identifier}/refresh-extraction")
            st.rerun()
    if document["status"] == "failed":
        st.error(document["error_message"])
        if st.button("重试解析", key=f"retry:{identifier}"):
            with st.spinner("重新解析中…"):
                api("POST", f"/documents/{identifier}/retry")
            st.rerun()
        return
    if not paper:
        st.info("正在解析，稍后刷新可查看结果。")
        return
    view = st.radio(
        "阅读视图",
        [
            "原文与解析",
            "仅原始 PDF",
            "仅解析结果",
            "论文信息",
            "研究图表",
            "表格与结构",
            "期刊指标",
        ],
        key=f"reader-view:{identifier}",
        horizontal=True,
        label_visibility="collapsed",
    )
    revision = f"{identifier}:{document['updated_at']}"
    if view in {"原文与解析", "仅原始 PDF", "仅解析结果"}:
        reader(document, revision, view)
    else:
        with st.container(height=500, border=False, key="detail-scroll"):
            if view == "论文信息":
                metadata_editor(document)
            elif view == "研究图表":
                figure_gallery(document)
            elif view == "表格与结构":
                structure_view(document)
            else:
                metric_editor(document)


def reader(document: dict[str, Any], revision: str, view: str = "原文与解析") -> None:
    paper = document["parsed"]
    if not paper["pages"]:
        st.info("没有可预览的页面。")
        return
    pages = [p["number"] for p in paper["pages"]]
    page_key = f"page:{revision}"
    page = st.session_state.setdefault(page_key, pages[0])
    if page not in pages:
        page = st.session_state[page_key] = pages[0]
    selected = next(p for p in paper["pages"] if p["number"] == page)
    with st.container(key="reader-content"):
        if view == "原文与解析":
            original, parsed = st.columns([0.85, 1.15], gap="small")
            with original:
                original_page(document["id"], selected)
            with parsed:
                parsed_page(document, page)
        elif view == "仅原始 PDF":
            original_page(document["id"], selected)
        else:
            parsed_page(document, page)
    with st.container(
        key="reader-pagination",
        horizontal=True,
        horizontal_alignment="right",
        vertical_alignment="center",
    ):
        with st.popover("清洗记录"):
            st.json([b for b in paper["blocks"] if b["page"] == page])
        st.caption(f"PDF 第 {page} 页 / 共 {len(pages)} 页")
        index = pages.index(page)
        st.button(
            "上一页",
            icon=":material/chevron_left:",
            disabled=index == 0,
            key=f"prev:{revision}",
            on_click=set_page,
            args=(page_key, pages[max(0, index - 1)]),
        )
        st.number_input(
            "PDF 页码",
            min_value=pages[0],
            max_value=pages[-1],
            step=1,
            key=page_key,
            label_visibility="collapsed",
            width=100,
        )
        st.button(
            "下一页",
            icon=":material/chevron_right:",
            disabled=index == len(pages) - 1,
            key=f"next:{revision}",
            on_click=set_page,
            args=(page_key, pages[min(len(pages) - 1, index + 1)]),
        )


def set_page(key: str, page: int) -> None:
    st.session_state[key] = page


def original_page(identifier: str, page: dict[str, Any]) -> None:
    st.caption("原始 PDF")
    with st.container(height=500, border=False, key=f"source-preview:{identifier}:{page['image']}"):
        st.image(image_bytes(identifier, page["image"]), width="stretch")


def parsed_page(document: dict[str, Any], page: int) -> None:
    st.caption("解析结果")
    with st.container(
        height=500,
        border=True,
        key=f"parsed-preview:{document['id']}:{document['updated_at']}:{page}",
    ):
        reading_body(document, page)
