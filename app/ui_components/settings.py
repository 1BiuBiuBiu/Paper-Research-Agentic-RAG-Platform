from typing import Any

import streamlit as st

from app.ui_components.client import api


def settings_page() -> None:
    st.subheader("模型管理")
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
                    else ("已配置" if model["configured"] else "待配置密钥"),
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
        st.caption("已配置表示本机凭据齐全；保存和切换模型不会自动发起付费请求。")
    with providers_tab:
        credential_dialog(connections)
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
        state = "已配置" if connection["configured"] else "待配置密钥"
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
        if st.form_submit_button("保存服务商", type="primary"):
            body = {
                "name": name,
                "base_url": endpoint,
                "models": [m.strip() for m in models.splitlines() if m.strip()],
                "api_key_env": key_env.strip() or None,
                "requires_key": requires_key,
                "json_mode": json_mode,
                "enabled": enabled,
            }
            api(
                "POST" if selected == "new" else "PUT",
                "/connections" if selected == "new" else f"/connections/{selected}",
                json=body,
            )
            st.rerun()
