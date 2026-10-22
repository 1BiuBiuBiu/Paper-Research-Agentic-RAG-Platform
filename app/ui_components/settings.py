from typing import Any

import streamlit as st

from app.ui_components.client import ClientError, api
from app.ui_components.connection_test import existing_connection_test, show_report


def settings_page() -> None:
    st.subheader("模型管理")
    if notice := st.session_state.pop("settings:notice", None):
        st.success(notice)
    if report := st.session_state.pop("settings:test-report", None):
        show_report(report)
    catalog = api("GET", "/model-catalog")
    connections = api("GET", "/connections")["items"]
    models_tab, providers_tab = st.tabs(["模型", "服务商"])
    with models_tab:
        st.caption("这里管理可选模型；对话中直接从输入框下方切换。")
        rows = []
        for model in catalog["items"]:
            rows.append(
                {
                    "模型": model["model_id"],
                    "服务商": model["provider_name"],
                    "状态": "已停用"
                    if not model["enabled"]
                    else ("凭据已配置" if model["configured"] else "待配置密钥"),
                }
            )
        st.dataframe(rows, hide_index=True, width="stretch")
        choices = {m["id"]: m for m in catalog["items"] if m["enabled"]}
        default = catalog["default"] or {}
        selected = default.get("connection_id", "") + "::" + default.get("model", "")
        ids = list(choices)
        with st.form("default-model"):
            choice = st.selectbox(
                "新对话默认模型",
                ids,
                index=ids.index(selected) if selected in ids else None,
                format_func=lambda i: choices[i]["label"],
                placeholder="选择默认模型",
            )
            if st.form_submit_button("保存默认模型", disabled=not ids):
                if choice is None:
                    st.warning("请选择默认模型。")
                else:
                    model = choices[choice]
                    api(
                        "PUT",
                        "/models/default",
                        json={"connection_id": model["connection_id"], "model": model["model"]},
                    )
                    st.success("默认模型已保存，新对话会使用此选择。")
        st.caption("凭据已配置不代表连接测试通过；请在服务商页面显式测试。切换模型不会发起请求。")
    with providers_tab:
        credential_dialog(connections)
        existing_connection_test(connections)
        provider_editor(connections)
    with st.expander("本机运行状态"):
        state = api("GET", "/health")
        st.caption(f"版本 {state['version']} · {state['slice']}")
        flow = api("GET", "/agent/status")
        st.caption("Cross-Encoder：" + ("本机已就绪" if flow["reranker_ready"] else "需要下载模型"))
        st.caption(
            "Tavily："
            + ("已配置" if flow["web_configured"] else "未配置；本地证据不足时会说明缺口")
        )


def credential_dialog(connections: list[dict]) -> None:
    choices = {c["id"]: c for c in connections if c["requires_key"] and c["api_key_env"]}
    choices["tavily"] = {"name": "Tavily 联网搜索"}
    st.markdown("**配置 API 密钥**")
    selected = st.selectbox(
        "选择要配置密钥的服务商",
        list(choices),
        format_func=lambda identifier: choices[identifier]["name"],
    )
    if st.button("打开本机密钥输入窗口", disabled=selected is None):
        result = api("POST", f"/connections/{selected}/key-dialog")
        st.info(result["message"])
    st.caption("密码式输入窗口会出现在 Windows 桌面；密钥仅保存到本机服务端 .env，保存后重启平台。")


def provider_editor(connections: list[dict]) -> None:
    st.caption("服务商配置一次，模型出现在对话统一列表中。当前支持 Chat Completions 兼容 API。")
    for connection in connections:
        state = "凭据已配置" if connection["configured"] else "待配置密钥"
        st.text(
            connection["name"] + " · " + state + (" · 已停用" if not connection["enabled"] else "")
        )
    options = {
        "new": "添加服务商",
        **{c["id"]: c["name"] for c in connections if c["id"] != "deepseek"},
    }
    selected = st.selectbox("配置服务商", list(options), format_func=lambda i: options[i])
    current: dict[str, Any] = next((c for c in connections if c["id"] == selected), {})
    with st.form("model-connection:" + selected):
        name = st.text_input("服务名称", value=current.get("name", ""), max_chars=80)
        endpoint = st.text_input(
            "API Base URL", value=current.get("base_url", ""), placeholder="https://服务地址/v1"
        )
        models = st.text_area("模型 ID（每行一个）", value="\n".join(current.get("models", [])))
        enabled = st.checkbox("启用此服务商", value=current.get("enabled", True))
        with st.expander("高级连接配置"):
            key_env = st.text_input(
                "服务端密钥环境变量名",
                value=current.get("api_key_env") or "RESEARCH_MODEL_KEY_CUSTOM",
            )
            requires_key = st.checkbox("需要 API Key", value=current.get("requires_key", True))
            json_mode = st.checkbox("服务支持 JSON 输出参数", value=current.get("json_mode", True))
            st.caption(
                "密钥仅保存在本机服务端 .env，页面只引用变量名。内置服务使用 DEEPSEEK_API_KEY。"
            )
        st.caption(
            "测试每个所填模型一次短请求，可能计费；全部通过才保存。首次可先保存为停用，再配置密钥。"
        )
        test_only = st.form_submit_button("测试当前配置")
        save = st.form_submit_button("测试并保存服务商", type="primary")
        disabled_save = st.form_submit_button("保存为停用（不测试）")
        if test_only or save or disabled_save:
            body = {
                "name": name,
                "base_url": endpoint,
                "models": [m.strip() for m in models.splitlines() if m.strip()],
                "api_key_env": key_env.strip() or None,
                "requires_key": requires_key,
                "json_mode": json_mode,
                "enabled": enabled,
            }
            try:
                if disabled_save:
                    api(
                        "POST" if selected == "new" else "PUT",
                        "/connections" if selected == "new" else f"/connections/{selected}",
                        json={**body, "enabled": False},
                    )
                    st.session_state["settings:notice"] = "已保存为停用，请配置密钥后测试并启用。"
                    st.rerun()
                else:
                    with st.spinner("正在验证模型接口…"):
                        result = api(
                            "POST",
                            "/connection-tests/save" if save else "/connection-tests/draft",
                            json={
                                "connection": body,
                                "identifier": None if selected == "new" else selected,
                            },
                            timeout=450,
                        )
                    if save:
                        st.session_state["settings:test-report"] = result["test"]
                        st.session_state["settings:notice"] = "服务商已通过测试并保存。"
                        st.rerun()
                    else:
                        show_report(result)
            except ClientError as error:
                st.error(str(error))
