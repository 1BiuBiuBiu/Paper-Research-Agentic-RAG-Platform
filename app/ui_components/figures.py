from typing import Any

import streamlit as st

from app.ui_components.client import API_BASE, image_bytes


def figure_gallery(document: dict[str, Any]) -> None:
    paper = document["parsed"]
    figures = paper["figures"]
    research = [f for f in figures if f["role"] == "research"]
    other = [f for f in figures if f["role"] != "research"]
    st.caption(f"研究图表 {len(research)} 张 · 其他图像 {len(other)} 张。图表编号取自原文图注。")
    if not research:
        st.info("未匹配到带编号图注的研究图表。其他图像保留在下方供核对。")
    for figure in research:
        with st.container(border=True):
            st.subheader(figure["label"])
            st.caption(f"PDF 第 {figure['page']} 页")
            st.image(image_bytes(document["id"], figure["image"]), width="stretch")
            st.text(figure["caption"])
            st.link_button(
                "对照原页", f"{API_BASE}/documents/{document['id']}/original#page={figure['page']}"
            )
            with st.expander("正文中的图表引用"):
                mentions = [b for b in paper["blocks"] if b["id"] in figure["mention_block_ids"]]
                for block in mentions:
                    st.caption(f"第 {block['page']} 页")
                    st.text(block["text"])
    if other:
        with st.expander(f"其他图像与装饰 · {len(other)} 张"):
            # Explicit opt-in: don't load publisher logos/author photos as research figures.
            if st.checkbox("查看其他图像", key=f"other-figures:{document['id']}"):
                for figure in other:
                    st.caption(f"第 {figure['page']} 页 · {figure['classification_reason']}")
                    st.image(image_bytes(document["id"], figure["image"]), width=220)
