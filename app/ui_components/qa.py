from typing import Any

import streamlit as st

from app.ui_components.categories import category_params
from app.ui_components.client import API_BASE, api, image_bytes


def reset_qa_page() -> None:
    st.session_state["qa:offset"] = 0
    st.session_state.pop("qa_result", None)


def qa_page() -> None:
    st.session_state.setdefault("qa:offset", 0)
    st.subheader("论文问答")
    st.caption("先参考摘要定位，再检索相关原文。每次只使用明确选择的论文；当前每次提问独立处理。")
    show_samples = st.checkbox(
        "显示演示样本", value=False, key="qa:samples", on_change=reset_qa_page
    )
    categories = api("GET", "/categories", params={"include_samples": show_samples})["items"]
    folders = {
        "all": "全部论文",
        "uncategorized": "未分类",
        **{c["id"]: c["name"] for c in categories},
    }
    if st.session_state.get("qa:category", "all") not in folders:
        st.session_state["qa:category"] = "all"
    category = st.selectbox(
        "论文分类",
        list(folders),
        format_func=lambda key: folders[key],
        key="qa:category",
        on_change=reset_qa_page,
    )
    offset = (
        st.number_input("论文列表起始位置", min_value=0, value=None, step=100, key="qa:offset") or 0
    )
    listing = api(
        "GET",
        "/documents",
        params={
            "offset": offset,
            "limit": 100,
            "include_samples": show_samples,
            **category_params(category),
        },
    )
    st.caption(
        f"此分类共 {listing['total']} 篇，每页最多 100 篇；仍需明确选择一篇作为本次问答范围。"
    )
    records = [
        item
        for item in listing["items"]
        if item["status"] == "parsed" and (show_samples or not item.get("is_sample"))
    ]
    if not records:
        st.info("当前分类 / 分页没有已解析论文，请切换分类或先在论文库导入。")
        return
    options = {item["id"]: item for item in records}
    identifiers = list(options)
    selected = st.session_state.get("selected_id")
    identifier = st.selectbox(
        "本次问答范围（单篇）",
        identifiers,
        index=identifiers.index(selected) if selected in identifiers else 0,
        format_func=lambda key: options[key]["title"] or options[key]["filename"],
    )
    st.text(f"当前有效范围：{options[identifier]['title'] or options[identifier]['filename']}")
    st.caption(f"原始文件：{options[identifier]['filename']} · 记录 {identifier[:8]}")
    if options[identifier].get("is_sample"):
        st.info("这是开发验证用的自建演示样本。")
    models = api("GET", "/models")
    names = {item["id"]: item["name"] for item in models["models"]}
    selected_model = st.radio(
        "模型", list(names), format_func=lambda item: names[item], horizontal=True
    )
    scope_key = (identifier, selected_model)
    if st.session_state.get("qa_scope") != scope_key:
        st.session_state["qa_scope"] = scope_key
        st.session_state.pop("qa_result", None)
        st.session_state.pop("qa_question", None)
    status = api("GET", f"/documents/{identifier}/index")
    if status["status"] == "ready":
        st.caption(f"检索索引已就绪 · {status['chunks']} 个原文片段（解析后自动建立）")
    elif status["status"] == "building":
        st.info("索引正在后台建立；完成后即可提问，无需手动操作。")
        if st.button("刷新索引状态"):
            st.rerun()
    else:
        if status["status"] == "failed":
            st.error(
                "索引建立失败："
                + (status.get("message") or "未知原因")
                + "。解析结果已保留，可单独重试索引。"
            )
        else:
            st.info("这篇论文尚未建立有效检索索引（解析完成后会自动建立）。")
        if st.button("立即建立 / 重试索引"):
            with st.spinner("正在本机计算论文向量，首次载入模型需要等待…"):
                api("POST", f"/documents/{identifier}/index", timeout=660)
            st.rerun()
    if not models["configured"]:
        st.warning(
            "尚未配置 DeepSeek API Key。请在本机服务端 .env 中配置后重启 API；不要把 Key 发到聊天。"
        )
    st.caption("当前只依据本地论文回答；联网补充会在后续版本启用。")
    question = st.text_area(
        "你的问题", max_chars=4000, placeholder="例如：本文的主要方法和实验证据是什么？"
    )
    if st.button(
        "基于所选论文回答",
        type="primary",
        disabled=not models["configured"] or status["status"] != "ready",
    ):
        with st.spinner("正在检索相关原文并调用所选模型…"):
            st.session_state.pop("qa_result", None)
            st.session_state["qa_question"] = question
            st.session_state["qa_result"] = api(
                "POST",
                "/query",
                timeout=750,
                json={
                    "question": question,
                    "scope": {"type": "documents", "ids": [identifier]},
                    "model": selected_model,
                },
            )
    if result := st.session_state.get("qa_result"):
        with st.expander("本次已回答的问题"):
            st.text(st.session_state.get("qa_question", ""))
        show_answer(result)


def show_answer(result: dict[str, Any]) -> None:
    st.divider()
    st.subheader("回答")
    if result["insufficient_evidence"]:
        st.info("所选论文证据不足，以下回答会说明可确定内容与缺口。")
    # Plain text prevents model-generated links/HTML/images from executing or sending requests.
    st.text(result["answer"])
    usage = result["usage"]
    total = usage["total_tokens"]
    st.caption(
        f"实际模型：{result['served_model'] or '未调用'} · "
        f"耗时 {result['latency_ms'] / 1000:.1f} 秒 · "
        + (f"Token 用量 {total}" if total is not None else "Token 用量不可用")
    )
    st.caption(f"发送的证据上下文：{result['context_bytes']} UTF-8 字节；不等同于 token 数。")
    st.subheader("原文证据")
    if not result["sources"]:
        st.caption("本次没有可核对的引用。")
    for source in result["sources"]:
        with st.expander(f"[{source['source_id']}] PDF 第 {source['page']} 页"):
            st.text(source["title"])
            st.text(source["text"])
            st.link_button(
                "打开原文对应页",
                f"{API_BASE}/documents/{source['document_id']}/original#page={source['page']}",
            )
            document = api("GET", f"/documents/{source['document_id']}")
            page = next(
                (p for p in document["parsed"]["pages"] if p["number"] == source["page"]), None
            )
            if page:
                st.image(image_bytes(source["document_id"], page["image"]), width="stretch")
