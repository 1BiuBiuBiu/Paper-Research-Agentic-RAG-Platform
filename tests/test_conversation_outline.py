from app.ui_components.conversation_outline import outline_html, turn_anchor


def test_outline_links_cover_failed_pending_and_completed_turns_with_escaped_questions():
    turns = [
        {"id": str(i), "status": status, "request": {"question": '<script>"&问题</script>'}}
        for i, status in enumerate(["completed", "failed", "pending"])
    ]
    html = outline_html(turns)
    assert "<script>" not in html and "&lt;script&gt;&quot;&amp;问题&lt;/script&gt;" in html
    for turn in turns:
        assert f'href="#{turn_anchor(turn["id"])}"' in html
    assert "失败" in html and "处理中" in html
    assert 'href="#conversation-start"' in html and 'href="#conversation-end"' in html
    assert turn_anchor('hostile"<id>') != turn_anchor("other")
    assert "<" not in turn_anchor('hostile"<id>')
