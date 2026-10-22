import streamlit as st

from app.ui_components.client import api


def model_picker(key: str, previous: dict) -> dict | None:
    catalog = api("GET", "/model-catalog")
    items = {m["id"]: m for m in catalog["items"] if m["enabled"]}
    prior = previous or catalog["default"] or {"connection_id": "deepseek", "model": "flash"}
    prior_id = prior.get("connection_id", "") + "::" + prior.get("model", "")
    widget = "agent:model-choice:" + key
    if widget in st.session_state and st.session_state[widget] not in items:
        st.session_state[widget] = None
    options = list(items)
    selected = st.selectbox(
        "模型",
        options,
        index=options.index(prior_id) if prior_id in options else None,
        format_func=lambda i: items[i]["label"] + ("" if items[i]["configured"] else " · 未配置"),
        key=widget,
        label_visibility="collapsed",
        placeholder="选择模型",
    )
    return items.get(selected) if selected else None
