"""Library-style scope dialog: folders, paginated checkbox rows and group actions."""

import math
from collections.abc import Callable

import streamlit as st

from app.ui_components.client import api
from app.ui_components.library import load_documents
from app.ui_components.scope_state import (
    clear_scope,
    draft_for,
    effective_ids,
    folder_of,
    remember,
    selected_scope,
    toggle_documents,
    toggle_folders,
)

PAGE_SIZE = 8


def scope_picker(key: str, previous: dict) -> dict | None:
    draft = draft_for(key, previous)
    for field, label in [("chat", "通用对话（不使用资料）"), ("include_samples", "包含演示样本")]:
        widget = f"agent:scope:{field}:{key}"
        if widget not in st.session_state:
            st.session_state[widget] = draft[field]
        st.checkbox(label, key=widget, on_change=remember, args=(key, field, widget))
    documents = load_documents({"include_samples": draft["include_samples"]})
    categories = api("GET", "/categories")["items"]
    folders = {"uncategorized": "未分类", **{c["id"]: c["name"] for c in categories}}
    if draft["chat"]:
        st.info("本轮不使用资料；关闭通用对话后恢复下面的选择。")
    else:
        render_browser(key, documents, folders)
    if draft.get("all_documents", False):
        excluded_count = len(draft["excluded_document_ids"])
        st.caption(
            f"全库范围：已排除 {excluded_count} 篇" if excluded_count else "默认范围：全部论文"
        )
    count = len(effective_ids(draft, documents))
    group = (
        "全部论文" if draft.get("all_documents", False) else f"{len(draft['folders'])} 个整文件夹"
    )
    st.caption(f"已选 {count} 篇文献 · {group} · 单轮最多 100 篇")
    if count > 100:
        st.warning("已选范围超过 100 篇，请取消部分选择后提问。")
    st.button("清空资料选择", key="agent:clear-scope:" + key, on_click=clear_scope, args=(key,))
    return selected_scope(key, previous)


def render_browser(key: str, documents: list[dict], folders: dict[str, str]) -> None:
    draft = draft_for(key, {})
    navigation, listing = st.columns([1, 3], gap="medium")
    with navigation:
        st.markdown("**文件夹**")
        folder = st.radio(
            "浏览文件夹",
            list(folders),
            format_func=lambda i: folders[i],
            label_visibility="collapsed",
            key="agent:browse:" + key,
        )
    with listing:
        rows = [d for d in documents if folder_of(d) == folder]
        # Folder navigation owns its pagination; switching folders cannot reuse an invalid page.
        pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
        page = st.selectbox(
            "文献列表页",
            range(1, pages + 1),
            format_func=lambda n: f"第 {n} / {pages} 页",
            key=f"agent:page:{key}:{folder}",
        )
        visible = rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
        selected = effective_ids(draft, documents)
        covered = [
            d["id"]
            for d in documents
            if draft.get("all_documents", False) or folder_of(d) in draft["folders"]
        ]
        current = [d["id"] for d in visible]
        controls = st.columns(3)
        actions = [
            (
                "选择本页",
                bool(current) and set(current) <= selected,
                toggle_documents,
                (key, current, covered),
                not current,
            ),
            (
                "选择整个文件夹",
                (draft.get("all_documents", False) or folder in draft["folders"])
                and all(d["id"] in selected for d in rows),
                toggle_folders,
                (key, [folder], documents),
                False,
            ),
            (
                "选择所有文件夹",
                (draft.get("all_documents", False) or set(folders) <= set(draft["folders"]))
                and len(selected) == len(documents),
                toggle_folders,
                (key, list(folders), documents),
                False,
            ),
        ]
        for column, (label, value, callback, args, disabled) in zip(controls, actions, strict=True):
            widget = f"agent:group:{key}:{label}"
            st.session_state[widget] = value
            column.checkbox(
                label,
                key=widget,
                disabled=disabled,
                on_change=group_changed,
                args=(callback, args, widget),
            )
        st.caption(f"{folders[folder]} · {len(rows)} 篇 · 本页 {len(visible)} 篇")
        with st.container(height=320, border=True):
            if not visible:
                st.caption("此文件夹暂无文献。")
            for document in visible:
                identifier = document["id"]
                widget = f"agent:file:{key}:{identifier}"
                st.session_state[widget] = identifier in selected
                st.checkbox(
                    document["title"] or document["filename"],
                    key=widget,
                    on_change=file_changed,
                    args=(key, identifier, covered, widget),
                )
                st.caption(document["filename"])
        st.caption(
            "逐项取消会从本轮范围排除该论文，即使它属于已选文件夹。翻页和切换文件夹保留选择。"
        )


def group_changed(callback: Callable, args: tuple, widget: str) -> None:
    callback(*args, st.session_state[widget])


def file_changed(key: str, identifier: str, covered: list[str], widget: str) -> None:
    toggle_documents(key, [identifier], covered, st.session_state[widget])


def close_scope_dialog() -> None:
    st.session_state.pop("agent:scope-dialog", None)


@st.dialog("选择资料范围", width="large", on_dismiss=close_scope_dialog)
def scope_dialog(key: str, previous: dict, previous_web: bool) -> None:
    scope_picker(key, previous)
    st.checkbox("本地不足时联网补充", value=previous_web, key="agent:web:" + key)
    st.caption("联网仅发送检索问题，需配置 Tavily 密钥。")
    if st.button("完成选择", type="primary"):
        close_scope_dialog()
        st.rerun()
