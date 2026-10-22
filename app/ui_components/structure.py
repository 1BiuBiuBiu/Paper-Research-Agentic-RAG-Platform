from typing import Any

import streamlit as st

from app.domain.papers import PaperTable
from app.parsing.table_data import table_html
from app.ui_components.client import API_BASE, ClientError, api, image_bytes
from app.ui_components.page_reading import (
    block_html,
    heading_depth,
    is_note,
    page_elements,
    previous_heading,
)


def reading_body(document: dict[str, Any], page: int) -> None:
    paper = document["parsed"]
    elements = page_elements(paper, page)
    context = previous_heading(paper, page)
    first_block = next((e for e in elements if e["element_kind"] == "block"), None)
    if context and first_block and heading_depth(first_block) is None:
        st.caption(f"承接章节：{context} · 本栏仅显示 PDF 第 {page} 页")
    notes: list[dict[str, Any]] = []
    with st.container(key="parsed-article"):
        render_elements(document, elements, notes)
    if notes:
        with st.expander(f"本页脚注与附属信息 · {len(notes)} 项（原文保留）"):
            for note in notes:
                st.text(note["text"])


def render_elements(
    document: dict[str, Any], elements: list[dict[str, Any]], notes: list[dict[str, Any]]
) -> None:
    text_run: list[str] = []

    def flush() -> None:
        if text_run:
            st.html('<article class="paper-reading">' + "".join(text_run) + "</article>")
            text_run.clear()

    for element in elements:
        kind = element["element_kind"]
        if kind == "block":
            if is_note(element):
                notes.append(element)
                continue
            equation = (
                next(
                    (
                        e
                        for e in document["parsed"].get("equations", [])
                        if e["block_id"] == element["id"] and e["page"] == element["page"]
                    ),
                    None,
                )
                if element["kind"] == "formula"
                else None
            )
            if equation:
                flush()
                st.caption(f"公式原图 · PDF 第 {element['page']} 页 · 未启用 LaTeX 识别")
                try:
                    st.image(image_bytes(document["id"], equation["image"]), width="stretch")
                except ClientError:
                    st.warning("公式区域暂时无法读取，请对照左侧原页。")
                    st.text(element["text"])
                continue
            text_run.append(block_html(element))
        elif kind == "figure":
            figure = element
            if figure["role"] == "research":
                flush()
                st.caption(figure["label"] or "论文图像")
                st.image(image_bytes(document["id"], figure["image"]), width="stretch")
        elif kind == "table":
            flush()
            table = element
            st.caption(
                f"{table['label'] or '表格'} · {table['rows']} × {table['columns']} · 数值待核对"
            )
            if table["image"]:
                st.image(image_bytes(document["id"], table["image"]), width="stretch")
            st.caption("行列与合并单元格见「表格与结构」。")
    flush()


def structure_view(document: dict[str, Any]) -> None:
    paper = document["parsed"]
    identifier = document["id"]
    if not paper.get("structure_version"):
        st.info("此记录来自旧解析版本，尚未提取表格行列与完整结构，需要重新解析原 PDF。")
    else:
        st.caption(
            f"表格 {len(paper['tables'])} · 公式区域 {len(paper['equations'])} · "
            f"阅读顺序元素 {len(paper['reading_order'])}"
        )
    if st.button(
        "重新解析图表与结构",
        key=f"reparse:{identifier}",
        help="使用原 PDF 重新运行本机解析模型；旧结果先保留，成功后切换版本，人工修正保留。",
    ):
        with st.spinner("正在本机识别版面与表格行列，首次需要下载表格模型…"):
            api("POST", f"/documents/{identifier}/reparse", timeout=660)
        st.rerun()
    for kind, label in [
        ("docling_json", "下载 Docling 结构 JSON"),
        ("markdown", "下载阅读版 Markdown"),
    ]:
        if kind in paper.get("exports", {}):
            st.link_button(label, f"{API_BASE}/documents/{identifier}/exports/{kind}")
    st.caption("结构 JSON 保留引擎的父子关系、坐标、行列合并与引用；Markdown 为阅读派生格式。")
    available = {t["id"]: t for t in paper.get("tables", [])}
    selected = (
        st.selectbox(
            "选择表格",
            list(available),
            format_func=lambda key: (
                f"{available[key]['label'] or key} · 第 {available[key]['page']} 页"
            ),
            key=f"table:{identifier}:{document['updated_at']}",
        )
        if available
        else None
    )
    for value in [available[selected]] if selected else []:
        table = PaperTable.model_validate(value)
        with st.container(border=True):
            st.subheader(table.label or "未编号表格")
            st.caption(
                f"第 {table.page} 页 · {table.rows} 行 × {table.columns} 列 · "
                "自动识别，数值尚未人工核对"
            )
            st.text(table.caption)
            for warning in table.structure_warnings:
                st.warning(warning)
            html = table_html(table)
            if html:
                st.html(
                    """<style>.paper-table {border-collapse:collapse;width:100%;font-size:14px;}
                .paper-table td,.paper-table th {
                    border:1px solid #ccd7e7;padding:8px;white-space:pre-wrap;}
                .paper-table th {background:#edf2fa;}</style>"""
                    + html
                )
            else:
                st.warning("未获得有效行列结构，原区域已保留，请对照原文。")
            if table.image:
                with st.expander("对照原始表格区域"):
                    st.image(image_bytes(identifier, table.image), width="stretch")
            with st.expander("单元格坐标、合并与正文关联"):
                st.json(value)
    with st.expander("章节与阅读顺序"):
        for block in paper["blocks"]:
            if block["kind"] == "section_header" and not block["excluded_reason"]:
                st.text(
                    f"第 {block['page']} 页 · 层级 {block.get('heading_level') or '未判定'} · "
                    + block["text"]
                )
        st.json(paper.get("reading_order", []))
    if paper.get("equations"):
        with st.expander("公式区域（未启用 LaTeX 识别）"):
            if st.checkbox("加载公式区域预览", key=f"equations:{identifier}"):
                options = {e["id"]: e for e in paper["equations"]}
                choice = st.selectbox("选择公式区域", list(options), key=f"equation:{identifier}")
                equation = options[choice]
                st.caption(f"第 {equation['page']} 页 · {equation['quality']}")
                st.image(image_bytes(identifier, equation["image"]), width="stretch")
                st.text(equation["text"])
