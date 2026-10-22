"""Bounded session queue; reuse the existing single-document server validation."""

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.ui_components.client import ClientError

MAX_FILES = 20
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_BATCH_BYTES = 200 * 1024 * 1024


class SelectedFile(Protocol):
    name: str
    size: int

    def getvalue(self) -> bytes: ...


@dataclass
class ImportItem:
    filename: str
    digest: str
    size: int
    content: bytes = field(repr=False)
    status: str = "waiting"
    document_id: str | None = None
    duplicate: bool = False
    message: str = "等待上传与解析"


def stage_files(files: Sequence[SelectedFile], queue: list[ImportItem]) -> int:
    if len(files) > MAX_FILES:
        raise ValueError("单批最多选择 20 个 PDF，请分批导入。")
    if any(f.size > MAX_FILE_BYTES for f in files):
        raise ValueError("单个 PDF 不能超过 50 MiB，请移除超限文件。")
    if sum(f.size for f in files) > MAX_BATCH_BYTES:
        raise ValueError("单批文件合计不能超过 200 MiB，请分批导入。")
    known = {(item.filename, item.digest) for item in queue}
    additions = []
    for uploaded in files:
        content = uploaded.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        key = uploaded.name, digest
        if key not in known:
            additions.append(ImportItem(uploaded.name, digest, len(content), content))
            known.add(key)
    if len(queue) + len(additions) > MAX_FILES:
        raise ValueError("当前队列最多 20 项；请处理后清空队列再添加下一批。")
    if sum(len(item.content) for item in queue + additions) > MAX_BATCH_BYTES:
        raise ValueError("当前待处理文件合计超过 200 MiB，请先完成现有队列。")
    queue.extend(additions)
    return len(additions)


def run_queue(
    queue: list[ImportItem], request: Callable[..., Any], changed: Callable[[], None]
) -> None:
    for item in queue:
        if item.status not in {"waiting", "failed", "error"}:
            continue
        item.status, item.message = "processing", "正在本机上传与解析…"
        changed()
        try:
            if item.document_id:
                document = request("GET", f"/documents/{item.document_id}")
                if document["status"] == "failed":
                    document = request("POST", f"/documents/{item.document_id}/retry")
            else:
                result = request(
                    "POST",
                    "/documents",
                    files={"file": (item.filename, item.content, "application/pdf")},
                )
                document, item.duplicate = result["document"], result["duplicate"]
            item.document_id = document["id"]
            item.content = b""
            if document["status"] == "parsed":
                item.status = "duplicate" if item.duplicate else "parsed"
                item.message = "已存在，保留原记录和分类" if item.duplicate else "解析完成，已入库"
            elif document["status"] == "failed":
                item.status = "failed"
                item.message = document.get("error_message") or "解析失败，可重试"
            else:
                item.status, item.message = "error", "服务端仍在解析，请稍后点击继续解析确认状态"
        except ClientError as error:
            item.status, item.message = "error", str(error)
        except Exception:
            item.status, item.message = "error", "请求未完成，请检查本机服务后重试。"
        changed()


def recover_queue(queue: list[ImportItem]) -> None:
    for item in queue:
        if item.status == "processing":
            item.status = "error"
            item.message = "上次操作中断；继续解析会核对已有记录，不重复创建论文"


def queue_rows(queue: list[ImportItem]) -> list[dict[str, Any]]:
    labels = {
        "waiting": "待处理",
        "processing": "解析中",
        "parsed": "已解析",
        "duplicate": "已存在",
        "failed": "解析失败",
        "error": "待重试",
    }
    return [
        {
            "文件": item.filename,
            "大小 (MiB)": round(item.size / 1024 / 1024, 2),
            "状态": labels[item.status],
            "结果": item.message,
            "文献": f"/?view=library&folder=all&paper={item.document_id}"
            if item.document_id
            else "",
        }
        for item in queue
    ]
