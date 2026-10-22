import streamlit as st

from app.ui_components.batch_import import (
    ImportItem,
    queue_rows,
    recover_queue,
    run_queue,
    stage_files,
)
from app.ui_components.client import api
from app.ui_components.theme import navigate


def import_page() -> None:
    st.subheader("批量导入文献")
    uploaded = st.file_uploader(
        "拖入 PDF 文件，或点击选择（可多选）",
        type=["pdf"],
        accept_multiple_files=True,
        key="import:files",
        help="支持一次拖入多个 PDF；添加到队列后再开始解析。",
    )
    st.caption("单批最多 20 篇 / 合计 200 MiB；每篇最多 50 MiB / 500 页。新文献进入未分类。")
    st.caption("按顺序在本机逐篇解析，一篇失败不影响后续；不会调用付费问答服务。")
    queue: list[ImportItem] = st.session_state.setdefault("import:queue", [])
    recover_queue(queue)
    if st.button("添加到待解析队列", disabled=not uploaded):
        try:
            added = stage_files(uploaded, queue)
            st.success(f"已添加 {added} 个文件。" if added else "所选文件已在当前队列中。")
        except ValueError as error:
            st.error(str(error))
    if not queue:
        st.info("可拖动多个 PDF 到上方区域，也可以点击选择文件。")
        return
    pending = any(item.status in {"waiting", "failed", "error"} for item in queue)
    start, clear, library = st.columns(3)
    execute = start.button("开始／继续解析", type="primary", disabled=not pending)
    if clear.button("清空导入队列", help="只清空本页队列，不删除已入库的论文。"):
        st.session_state["import:queue"] = []
        st.rerun()
    if library.button("返回论文库"):
        navigate("论文库")
        st.rerun()
    st.caption("失败项可点击“开始／继续解析”重试；已成功的项目不会再次提交。")
    progress = st.empty()
    table = st.empty()

    def refresh() -> None:
        completed = sum(item.status in {"parsed", "duplicate", "failed", "error"} for item in queue)
        processing = next((item.filename for item in queue if item.status == "processing"), "")
        progress.progress(
            completed / len(queue),
            text=f"已处理 {completed} / {len(queue)}"
            + (f" · 当前：{processing}" if processing else ""),
        )
        table.dataframe(
            queue_rows(queue),
            hide_index=True,
            width="stretch",
            column_config={"文献": st.column_config.LinkColumn("文献", display_text="查看文献")},
        )

    refresh()
    if execute:
        run_queue(queue, api, refresh)
        st.rerun()
    if not pending:
        st.success("本批导入已完成，可继续添加文件或返回论文库。")
    elif any(item.status in {"failed", "error"} for item in queue):
        st.warning("有文件未完成，详细原因见列表；其他成功论文已保留。")
    st.caption("队列只保存在当前页面会话；刷新或断开后，已入库论文及服务端解析状态仍然保留。")
