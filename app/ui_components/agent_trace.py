import streamlit as st

NODES = {
    "route_query": "范围路由",
    "retrieve": "混合检索",
    "rerank": "Cross-Encoder 精排",
    "grade_documents": "相关性过滤",
    "rewrite_query": "查询改写",
    "web_search": "联网补充",
    "generate": "最终回答",
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
            f"查询改写 {result['rewrite_count']}/2 次 · 模型调用 {result['model_calls']}/6 次 · "
            f"联网：{STATUS.get(result['web_status'], result['web_status'])}"
        )
        for step in result["trace"]:
            label = NODES.get(step["node"], step["node"])
            st.text(
                f"{label} · 改写轮次 {step['attempt']} · "
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
