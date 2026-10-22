from uuid import uuid4

import streamlit as st

from app.ui_components.agent_trace import render_trace
from app.ui_components.chat_navigation import (
    chat_navigation,
    choose_conversation,
    conversation_options,
)
from app.ui_components.client import API_BASE, ClientError, api
from app.ui_components.conversation_outline import conversation_outline, turn_anchor
from app.ui_components.model_picker import model_picker
from app.ui_components.scope_picker import scope_dialog
from app.ui_components.scope_state import selected_scope


def agent_page() -> None:
    with st.container(key="agent-workspace"):
        agent_workspace()


def agent_workspace() -> None:
    conversation, projects = chat_navigation()
    current = conversation["id"]
    project_id = conversation["project_id"]
    project_name = next((p["name"] for p in projects if p["id"] == project_id), "未分组")
    title_col, options_col = st.columns([4, 1])
    with title_col:
        st.caption(project_name)
        st.subheader(conversation["title"])
    with options_col:
        conversation_options(conversation, projects)
    completed = [t for t in conversation["turns"] if t["status"] == "completed"]
    previous = completed[-1]["request"] if completed else {}
    key = current or "draft:" + (project_id or "unassigned")
    with st.container(key="agent-body"):
        chat, outline = st.columns([5, 1.2], gap="medium")
        with outline:
            conversation_outline(conversation["turns"])
        with chat:
            with st.container(key="agent-main"):
                with st.container(
                    key="agent-transcript", height=500, border=False, autoscroll=True
                ):
                    st.html('<div id="conversation-start"></div>')
                    for turn in conversation["turns"]:
                        st.html(f'<div id="{turn_anchor(turn["id"])}" class="turn-anchor"></div>')
                        render_turn(turn, conversation)
                    if not conversation["turns"]:
                        with st.container(key="agent-empty"):
                            st.markdown("### 从一个研究问题开始")
                            st.caption("选择论文或文件夹，也可以直接进行通用对话。")
                    st.html('<div id="conversation-end"></div>')
                with st.container(key="agent-composer"):
                    input_area = st.empty()
                    status_area = st.empty()
                    model_col, scope_col = st.columns([3, 2])
                    with model_col:
                        selected_model = model_picker(key, previous)
                    if scope_col.button("资料范围", icon=":material/attach_file:", width="stretch"):
                        st.session_state["agent:scope-dialog"] = key
                    if st.session_state.get("agent:scope-dialog") == key:
                        scope_dialog(
                            key, previous.get("scope", {}), previous.get("allow_web", True)
                        )
                    scope = selected_scope(key, previous.get("scope", {}))
                    allow_web = st.session_state.get(
                        "agent:web:" + key, previous.get("allow_web", True)
                    )
                    preview = None
                    if scope:
                        try:
                            preview = api("POST", "/agent/scope", json=scope)
                        except ClientError as error:
                            st.caption(str(error))
                    ready = bool(
                        preview is not None
                        and all(d["index_status"] == "ready" for d in preview["items"])
                    )
                    flow = api("GET", "/agent/status")
                    if scope and scope["kind"] != "chat" and not flow["reranker_ready"]:
                        ready = False
                        st.caption(
                            "本机精排模型未就绪，请运行：uv run python -m scripts.download_reranker"
                        )
                    configured = bool(selected_model and selected_model["configured"])
                    pending = any(t["status"] == "pending" for t in conversation["turns"])
                    with status_area.container():
                        if preview:
                            st.caption(
                                "通用对话"
                                if scope and scope["kind"] == "chat"
                                else (
                                    f"全库（排除 {len(scope['excluded_document_ids'])} 篇） · "
                                    f"{preview['count']} 篇"
                                )
                                if scope
                                and scope["kind"] == "all"
                                and scope.get("excluded_document_ids")
                                else f"默认范围：全部论文 · {preview['count']} 篇"
                                if scope and scope["kind"] == "all"
                                else f"当前范围：{preview['count']} 篇论文"
                            )
                            unready = [p for p in preview["items"] if p["index_status"] != "ready"]
                            if unready:
                                st.caption(f"{len(unready)} 篇需要建立索引。")
                                if st.button("建立当前范围索引"):
                                    with st.status("正在本机建立索引…") as status:
                                        for paper in unready:
                                            st.write(paper["title"])
                                            api(
                                                "POST",
                                                f"/documents/{paper['id']}/index",
                                                timeout=660,
                                            )
                                        status.update(label="索引建立完成", state="complete")
                                    st.rerun()
                    if not configured:
                        st.caption("模型尚未配置；请到设置 → 模型管理。")
                    with input_area:
                        question = st.chat_input(
                            "输入问题…",
                            disabled=not ready or not configured or pending,
                            max_chars=4000,
                        )
    if question and scope and selected_model:
        if not current:
            conversation = api("POST", "/conversations", json={"project_id": project_id})
            current = conversation["id"]
            choose_conversation(current)
        body = {
            "request_id": str(uuid4()),
            "expected_revision": conversation["revision"],
            "question": question,
            "scope": scope,
            "allow_web": allow_web,
            "connection_id": selected_model["connection_id"],
            "model": selected_model["model"],
        }
        send_turn(current, body)


def send_turn(identifier: str, body: dict) -> None:
    with st.spinner("正在检索并回答…"):
        try:
            api("POST", f"/conversations/{identifier}/turns", json=body, timeout=750)
        except ClientError as error:
            st.session_state["agent:last_error"] = str(error)
    st.rerun()


def render_turn(turn: dict, conversation: dict) -> None:
    with st.chat_message("user"):
        st.text(turn["request"]["question"])
    with st.chat_message("assistant"):
        if turn["status"] == "pending":
            st.caption("本轮仍在处理中；可刷新对话查看结果。")
            return
        if turn["status"] == "failed":
            st.error(turn["error_message"] or "本轮失败。")
            if turn["request"]["expected_revision"] == conversation["revision"] and st.button(
                "重试本轮", key="retry-turn:" + turn["id"]
            ):
                send_turn(conversation["id"], turn["request"])
            return
        result = turn["response"]
        st.text(result["answer"])
        if result["history_reset"]:
            st.caption("资料范围或版本已变化，本轮未发送旧范围对话。")
        count = len(result["resolved_document_ids"])
        if count:
            with st.expander(f"本轮资料范围 · {count} 篇"):
                for paper in result.get("scope_documents", []):
                    st.text(paper["title"] + " · " + paper["filename"])
        coverage = len(result["context_document_ids"])
        tokens = result["usage"]["total_tokens"]
        st.caption(
            f"证据覆盖 {coverage}/{count} 篇 · 历史 {result['history_turns']} 轮 · "
            f"模型 {result['served_model'] or '未调用'} · "
            f"Token {tokens if tokens is not None else '未知'}"
        )
        render_trace(result)
        if result["sources"]:
            with st.expander("本轮原文证据"):
                for source in result["sources"]:
                    st.text(
                        f"[{source['source_id']}] {source['title']} · PDF 第 {source['page']} 页"
                    )
                    st.text(source["text"])
                    st.link_button(
                        "打开对应 PDF 页",
                        f"{API_BASE}/documents/{source['document_id']}/original#page={source['page']}",
                    )
