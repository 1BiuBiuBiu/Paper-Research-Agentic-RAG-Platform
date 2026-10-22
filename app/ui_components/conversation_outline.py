"""Local, escaped fragment links into the current conversation's message pane."""

from hashlib import sha256
from html import escape

import streamlit as st


def turn_anchor(identifier: str) -> str:
    return "turn-" + sha256(identifier.encode()).hexdigest()[:24]


def outline_html(turns: list[dict]) -> str:
    links = []
    for index, turn in enumerate(turns, 1):
        question = escape(turn["request"]["question"])
        status = {"completed": "", "failed": " · 失败", "pending": " · 处理中"}[turn["status"]]
        links.append(
            f'<a href="#{turn_anchor(turn["id"])}" target="_self" title="{question}">'
            f"<span>{index:02d}{status}</span><strong>{question}</strong></a>"
        )
    return (
        '<nav class="conversation-outline" aria-label="对话导航">'
        '<a href="#conversation-start" target="_self">回到开头 ↑</a>'
        + "".join(links)
        + '<a href="#conversation-end" target="_self">最新消息 ↓</a></nav>'
    )


def conversation_outline(turns: list[dict]) -> None:
    with st.container(key="conversation-outline", height=500, border=False):
        st.caption(f"对话导航 · {len(turns)} 轮")
        if turns:
            st.html(outline_html(turns))
        else:
            st.caption("每轮提问会显示在这里，点击即可定位。")
