"""Chinese research workbench, migrated from the upstream Streamlit entrypoint."""

import streamlit as st

from app.ui_components.agent import agent_page
from app.ui_components.client import ClientError, ConnectionFailure
from app.ui_components.importer import import_page
from app.ui_components.library import library_page
from app.ui_components.settings import settings_page
from app.ui_components.theme import apply_theme, navigation

st.set_page_config(
    page_title="Research Desk · 科研论文工作台",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()
page = navigation()
try:
    if page == "论文库":
        library_page()
    elif page == "Agent 平台":
        agent_page()
    elif page == "导入":
        import_page()
    else:
        settings_page()
except ClientError as error:
    st.error(str(error))
    if isinstance(error, ConnectionFailure):
        st.caption("启动窗口显示“平台已启动”后，点击下方按钮重新加载；不会自动重发问答或上传。")
        if st.button("重新连接本机服务", type="primary"):
            st.rerun()
