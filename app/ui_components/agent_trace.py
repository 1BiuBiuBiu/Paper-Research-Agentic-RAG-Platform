import streamlit as st

NODES = {
    "route_query": "范围路由",
    "translate_query": "问题英译（仅精排）",
    "retrieve": "库级混合检索",
    "rerank": "Cross-Encoder 精排",
    "grade_documents": "判分：能否完整回答",
    "targeted_retrieve": "定向补检（第 2 轮）",
    "web_search": "联网补充",
    "generate": "最终回答",
    # Stored turns from before 0.13.0 still carry the old rewrite node.
    "rewrite_query": "查询改写（旧版）",
}
STATUS = {
    "not_needed": "本地证据足够",
    "disabled": "本轮关闭",
    "unconfigured": "未配置",
    "empty": "无可用结果",
    "failed": "搜索失败",
    "available": "已获取搜索摘要",
}


def render_trace(result: dict) -> None:
    if "trace" not in result:
        return
    with st.expander("本轮执行过程"):
        st.caption(
            f"定向补检 {result['rewrite_count']} 次 · 模型调用 {result['model_calls']} 次 · "
            f"联网：{STATUS.get(result['web_status'], result['web_status'])}"
        )
        for step in result["trace"]:
            label = NODES.get(step["node"], step["node"])
            st.text(
                f"{label} · 轮次 {step['attempt'] + 1} · "
                f"{step['count']} 条 · {step['elapsed_ms']} ms"
            )
            if step.get("query"):
                st.text(step["query"])
            if step.get("reason"):
                st.caption(step["reason"])
    if result.get("web_sources"):
        with st.expander("本轮联网来源（搜索摘要）"):
            for source in result["web_sources"]:
                st.text(f"[{source['source_id']}] {source['title']}")
                st.caption("抓取时间：" + source["retrieved_at"])
                st.text(source["text"])
                st.link_button("打开来源网页", source["url"])
