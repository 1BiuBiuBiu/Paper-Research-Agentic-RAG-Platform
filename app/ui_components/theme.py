from pathlib import Path

import streamlit as st

PAGES = {"library": "论文库", "agent": "Agent 平台", "import": "导入", "settings": "设置"}


def apply_theme() -> None:
    st.html("""<style>
    .stApp {background:#fff; color:#17263d;}
    header[data-testid="stHeader"] {visibility:hidden; height:0;}
    [data-testid="stSidebar"] {background:#f3f5f8; border-right:1px solid #dce1e8;
        width:200px !important; min-width:200px !important; max-width:200px !important;
        transform:none !important;}
    [data-testid="stSidebarHeader"] {display:none;}
    [data-testid="stSidebarUserContent"] {padding:1rem .25rem;}
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {gap:.15rem;}
    [data-testid="stSidebar"] h3 {font-size:.95rem; padding:.25rem 0 .5rem;}
    [data-testid="stSidebar"] button {justify-content:flex-start; min-height:28px; padding:3px 8px;}
    .block-container {padding:1rem 1.25rem 2rem; max-width:1800px;}
    h1,h2,h3 {font-weight:600 !important; letter-spacing:-.02em;}
    h1 {font-size:1.5rem !important;} h2 {font-size:1.25rem !important;}
    h3 {font-size:1.05rem !important;}
    [data-testid="stVerticalBlock"] {gap:.6rem;}
    [data-testid="stText"] {line-height:1.7;}
    @media(max-width:900px) {.block-container {padding:1rem .75rem 2rem;}}
    </style>""")
    st.html(
        "<style>"
        + Path(__file__).with_name("workspace.css").read_text(encoding="utf-8")
        + "</style>"
    )


def navigate(page: str) -> None:
    if page not in PAGES.values():
        page = "论文库"
    st.session_state["page"] = page
    st.query_params["view"] = next(k for k, v in PAGES.items() if v == page)
    st.query_params.pop("paper", None)
    if page == "论文库":
        st.session_state.pop("selected_id", None)


def navigation() -> str:
    view = st.query_params.get("view", "")
    page = PAGES.get("agent" if view == "qa" else view, st.session_state.get("page", "论文库"))
    if page not in PAGES.values():
        page = "论文库"
    st.session_state["page"] = page
    with st.sidebar:
        for name, icon in [
            ("论文库", ":material/library_books:"),
            ("Agent 平台", ":material/forum:"),
            ("导入", ":material/upload_file:"),
            ("设置", ":material/tune:"),
        ]:
            st.button(
                name,
                icon=icon,
                key="nav:" + name,
                width="stretch",
                type="primary" if page == name else "tertiary",
                on_click=navigate,
                args=(name,),
            )
        st.divider()
    return page
