from typing import Any

import streamlit as st

from app.ui_components.client import api


def open_category(identifier: str) -> None:
    st.session_state["library:category"] = identifier
    st.query_params["folder"] = identifier
    st.query_params.pop("paper", None)
    st.query_params.pop("q", None)
    st.session_state.pop("library:search", None)
    st.session_state.pop("selected_id", None)


def category_filter(include_samples: bool) -> tuple[str, list[dict[str, Any]]]:
    listing = api("GET", "/categories", params={"include_samples": include_samples})
    categories = listing["items"]
    names = {"all": "全部文献", "uncategorized": "未分类"}
    names.update({c["id"]: c["name"] for c in categories})
    selected = st.query_params.get("folder", st.session_state.get("library:category", "all"))
    if selected not in names:
        selected = "all"
        open_category(selected)
    st.session_state["library:category"] = selected
    counts = {"uncategorized": listing["uncategorized_count"]}
    counts.update({c["id"]: c["paper_count"] for c in categories})
    counts["all"] = sum(counts.values())
    with st.sidebar:
        st.subheader("文件夹")
        for identifier, name in names.items():
            st.button(
                f"{name}  {counts[identifier]}",
                icon=":material/folder_open:" if identifier == selected else ":material/folder:",
                key=f"folder:{identifier}",
                type="primary" if identifier == selected else "tertiary",
                width="stretch",
                on_click=open_category,
                args=(identifier,),
            )
        folder_actions(selected, categories)
    return selected, categories


def folder_actions(selected: str, categories: list[dict[str, Any]]) -> None:
    with st.popover("文件夹操作", icon=":material/more_horiz:", width="stretch"):
        with st.form("folder:create", clear_on_submit=True):
            name = st.text_input("新文件夹名称", max_chars=80)
            if st.form_submit_button("新建文件夹"):
                result = api("POST", "/categories", json={"name": name})
                open_category(result["id"])
                st.rerun()
        current = next((c for c in categories if c["id"] == selected), None)
        if current:
            with st.form(f"folder:rename:{selected}:{current['updated_at']}"):
                renamed = st.text_input("文件夹名称", value=current["name"], max_chars=80)
                if st.form_submit_button("重命名"):
                    api("PATCH", f"/categories/{selected}", json={"name": renamed})
                    st.rerun()
            if st.button("归档空文件夹", disabled=current["paper_count"] > 0):
                api("POST", f"/categories/{selected}/archive")
                open_category("all")
                st.rerun()
        if st.checkbox("显示已归档文件夹", key="folder:archived"):
            archived = [
                c
                for c in api("GET", "/categories", params={"include_archived": True})["items"]
                if c["archived"]
            ]
            for category in archived:
                if st.button("恢复：" + category["name"], key="restore:" + category["id"]):
                    api("POST", f"/categories/{category['id']}/restore")
                    st.rerun()


def category_params(selected: str) -> dict[str, Any]:
    if selected == "uncategorized":
        return {"uncategorized": True}
    return {} if selected == "all" else {"category_id": selected}


def paper_mover(records: list[dict[str, Any]], categories: list[dict[str, Any]]) -> None:
    if not records:
        return
    names = {"uncategorized": "未分类", **{c["id"]: c["name"] for c in categories}}
    options = {r["id"]: r for r in records}
    signature = ":".join(options)
    selected = st.multiselect(
        "选择要移动的文献",
        list(options),
        default=list(options) if len(options) == 1 else [],
        max_selections=100,
        format_func=lambda key: options[key]["title"] or options[key]["filename"],
        key="move:papers:" + signature,
    )
    target = st.selectbox(
        "移入文件夹",
        list(names),
        format_func=lambda key: names[key],
        key="move:target:" + signature,
    )
    if st.button("移动", disabled=not selected):
        result = api(
            "POST",
            "/documents/move",
            json={
                "document_ids": selected,
                "category_id": None if target == "uncategorized" else target,
            },
        )
        st.query_params.pop("paper", None)
        st.session_state.pop("selected_id", None)
        st.session_state["library:notice"] = (
            f"已移动 {result['moved']} 篇文献至「{names[target]}」。"
        )
        st.rerun()
