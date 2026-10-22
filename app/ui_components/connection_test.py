import streamlit as st

from app.ui_components.client import ClientError, api


def existing_connection_test(connections: list[dict]) -> None:
    st.markdown("**测试已有服务**")
    choices = {c["id"]: c for c in connections}
    choices["tavily"] = {"name": "Tavily 联网搜索", "models": ["search"]}
    identifier = st.selectbox(
        "要测试的服务", list(choices), format_func=lambda i: choices[i]["name"]
    )
    model = st.selectbox("测试模型", choices[identifier]["models"], key="test-model:" + identifier)
    st.caption(
        "仅点击时发送固定测试文本，不发送论文；每个模型一次短请求，可能计费。Tavily 测试一次搜索。"
    )
    if st.button("测试连接", key="test-existing"):
        try:
            with st.spinner("正在测试服务…"):
                report = api(
                    "POST",
                    f"/connection-tests/saved/{identifier}",
                    json={"model": model},
                    timeout=30,
                )
            st.session_state["connection-test:" + identifier + "::" + model] = report
        except ClientError as error:
            st.session_state.pop("connection-test:" + identifier + "::" + model, None)
            st.error(str(error))
    report = st.session_state.get("connection-test:" + identifier + "::" + model)
    if report:
        show_report(report)


def show_report(report: dict) -> None:
    st.success("连接测试通过 · " + report["tested_at"])
    for model in report.get("models", []):
        st.text(model["requested_model"] + " → " + model["served_model"])
    st.caption("测试结果仅表示当次调用成功；配置或密钥变化后请重新测试。")
