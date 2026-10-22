from typing import Any

import streamlit as st

from app.ui_components.categories import category_filter, category_params, paper_mover
from app.ui_components.client import api
from app.ui_components.detail import paper_detail
from app.ui_components.library_table import literature_table


def load_documents(params: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the selected folder completely; pagination stays inside the API client."""
    result = api("GET", "/documents", params={**params, "offset": 0, "limit": 100})
    records = {r["id"]: r for r in result["items"]}
    offset, total = len(result["items"]), result["total"]
    while offset < total:
        batch = api("GET", "/documents", params={**params, "offset": offset, "limit": 100})
        if not batch["items"]:
            break
        records.update({r["id"]: r for r in batch["items"]})
        offset += len(batch["items"])
    return list(records.values())


def close_paper() -> None:
    st.query_params.pop("paper", None)
    st.session_state.pop("selected_id", None)


def library_page() -> None:
    with st.container(key="library-workspace"):
        library_workspace()


def library_workspace() -> None:
    if st.session_state.get("library:layout") != "unified-library-v2":
        st.session_state.pop("selected_id", None)
        st.session_state["library:layout"] = "unified-library-v2"
    if notice := st.session_state.pop("library:notice", None):
        st.success(notice)
    show_samples = st.session_state.get("library:samples", False)
    selected_category, categories = category_filter(show_samples)
    names = {"all": "全部文献", "uncategorized": "未分类"}
    names.update({c["id"]: c["name"] for c in categories})
    query = str(st.query_params.get("q", st.session_state.get("library:search", "")))[:200]
    records = load_documents(
        {"q": query, "include_samples": show_samples, **category_params(selected_category)}
    )
    identifier = st.query_params.get("paper")
    if identifier:
        with st.container(key="library-reader-nav", horizontal=True):
            st.button("返回论文库", icon=":material/arrow_back:", on_click=close_paper)
            with st.popover("移动此文献", icon=":material/drive_file_move:"):
                paper_mover([r for r in records if r["id"] == identifier], categories)
        if identifier not in {r["id"] for r in records}:
            st.session_state.pop("selected_id", None)
            st.warning("该文献不在当前文件夹或搜索范围内，请返回论文库重新选择。")
            return
        st.session_state["selected_id"] = identifier
        paper_detail(api("GET", f"/documents/{identifier}"))
        return
    st.session_state.pop("selected_id", None)
    heading, search, actions = st.columns([1.5, 2.3, 0.7], vertical_alignment="center")
    heading.subheader(f"{names[selected_category]} · {len(records)}")
    search.text_input(
        "搜索文献",
        value=query,
        key="library:search",
        placeholder="搜索标题、文件名、期刊",
        label_visibility="collapsed",
        max_chars=200,
        on_change=search_changed,
    )
    with actions.popover("整理"):
        paper_mover(records, categories)
        st.checkbox("显示演示样本", key="library:samples", on_change=close_paper)
        if st.button("刷新列表", icon=":material/refresh:"):
            st.rerun()
    if not records:
        st.caption("此文件夹没有匹配的文献。")
        return
    with st.container(key="library-list", height=500, border=False):
        st.html(literature_table(records, selected_category, query))
    st.caption(f"{len(records)} 篇文献 · 点击标题阅读 · 文件夹内文献在列表中滚动")


def search_changed() -> None:
    st.query_params["q"] = st.session_state["library:search"]
    close_paper()
