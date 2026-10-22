import streamlit as st

from app.ui_components.client import api


def choose_conversation(identifier: str) -> None:
    st.query_params["conversation"] = identifier


def choose_project(identifier: str | None) -> None:
    st.query_params.pop("conversation", None)
    if identifier:
        st.query_params["project"] = identifier
    else:
        st.query_params.pop("project", None)


def load_projects(*, archived: bool = False) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        page = api(
            "GET",
            "/projects",
            params={
                "offset": offset,
                "limit": 100,
                "archived": archived,
            },
        )
        items.extend(page["items"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            return items


def chat_navigation() -> tuple[dict, list[dict]]:
    current = st.query_params.get("conversation")
    conversation = api("GET", f"/conversations/{current}") if current else None
    selected = conversation["project_id"] if conversation else st.query_params.get("project")
    projects = load_projects()
    if selected and selected not in {p["id"] for p in projects}:
        st.warning("项目不存在或已归档，请从左侧重新选择。")
        selected = None
    with st.sidebar:
        if st.button("新建对话", icon=":material/edit_square:", width="stretch"):
            created = api("POST", "/conversations", json={"project_id": selected})
            choose_conversation(created["id"])
            st.rerun()
        with st.popover("新建项目", icon=":material/create_new_folder:", width="stretch"):
            with st.form("new-chat-project"):
                name = st.text_input("项目名称", max_chars=80, placeholder="例如：多模态情感识别")
                if st.form_submit_button("创建项目", type="primary"):
                    created = api("POST", "/projects", json={"name": name})
                    choose_project(created["id"])
                    st.rerun()
        st.caption("项目")
        for project in projects:
            st.button(
                project["name"] + f"  ({project['count']})",
                key="project:" + project["id"],
                icon=":material/folder_open:" if selected == project["id"] else ":material/folder:",
                width="stretch",
                type="secondary" if selected == project["id"] else "tertiary",
                on_click=choose_project,
                args=(project["id"],),
            )
            if selected == project["id"]:
                chat_list(selected, current)
        if not projects:
            st.caption("创建项目，将同一课题的对话放在一起。")
        st.button(
            "未分组对话",
            key="project:unassigned",
            icon=":material/chat_bubble_outline:",
            width="stretch",
            type="secondary" if not selected else "tertiary",
            on_click=choose_project,
            args=(None,),
        )
        if not selected:
            chat_list(None, current)
        with st.popover("归档项目", width="stretch"):
            archived = load_projects(archived=True)
            if not archived:
                st.caption("暂无归档项目")
            for project in archived:
                if st.button("恢复 · " + project["name"], key="restore-project:" + project["id"]):
                    api("POST", f"/projects/{project['id']}/restore")
                    choose_project(project["id"])
                    st.rerun()
    return conversation or {
        "id": None,
        "title": "新对话",
        "revision": 0,
        "turns": [],
        "project_id": selected,
    }, projects


def chat_list(project_id: str | None, current: str | None) -> None:
    key = "chat-offset:" + (project_id or "unassigned")
    offset = st.session_state.setdefault(key, 0)
    params: dict[str, str | int | bool] = {"offset": offset, "limit": 20}
    if project_id:
        params["project_id"] = project_id
    else:
        params["unassigned"] = True
    sessions = api("GET", "/conversations", params=params)["items"]
    for session in sessions:
        st.button(
            session["title"],
            key="chat:" + session["id"],
            width="stretch",
            type="primary" if session["id"] == current else "tertiary",
            on_click=choose_conversation,
            args=(session["id"],),
        )
    if not sessions:
        st.caption("暂无对话")
    if offset and st.button("上一页对话", key=key + ":prev"):
        st.session_state[key] = max(0, offset - 20)
        st.rerun()
    if len(sessions) == 20 and st.button("更多对话", key=key + ":next"):
        st.session_state[key] = offset + 20
        st.rerun()


def conversation_options(conversation: dict, projects: list[dict]) -> None:
    current = conversation["id"]
    selected = conversation["project_id"]
    if selected:
        project = next(p for p in projects if p["id"] == selected)
        with st.popover("项目设置"):
            with st.form("rename-project:" + selected):
                name = st.text_input("项目名称", value=project["name"], max_chars=80)
                if st.form_submit_button("重命名项目"):
                    api("PATCH", f"/projects/{selected}", json={"name": name})
                    st.rerun()
            if st.button("归档空项目", disabled=bool(project["count"])):
                api("POST", f"/projects/{selected}/archive")
                choose_project(None)
                st.rerun()
    with st.popover("对话选项"):
        if current:
            with st.form("rename-chat:" + current):
                title = st.text_input("对话名称", value=conversation["title"], max_chars=120)
                targets = {"unassigned": "未分组", **{p["id"]: p["name"] for p in projects}}
                target = st.selectbox(
                    "所属项目",
                    list(targets),
                    index=list(targets).index(selected or "unassigned"),
                    format_func=lambda i: targets[i],
                )
                if st.form_submit_button("保存对话设置"):
                    api(
                        "PATCH",
                        f"/conversations/{current}",
                        json={
                            "title": title,
                            "project_id": None if target == "unassigned" else target,
                        },
                    )
                    choose_project(None if target == "unassigned" else target)
                    choose_conversation(current)
                    st.rerun()
        if st.button("刷新对话"):
            st.rerun()
