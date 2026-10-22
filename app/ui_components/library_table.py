from html import escape
from typing import Any
from urllib.parse import urlencode

STATUS = {"parsed": "已解析", "parsing": "解析中", "failed": "失败"}


def literature_table(records: list[dict[str, Any]], folder: str, query: str) -> str:
    """Wrapping native table with escaped paper text and local navigation URLs."""
    rows = []
    for paper in records:
        url = "?" + urlencode(
            {"view": "library", "folder": folder, "q": query, "paper": paper["id"]}
        )
        title = escape(paper["title"] or paper["filename"])
        filename = escape(paper["filename"])
        authors = escape("、".join(paper.get("authors", [])) or "—")
        venue = escape(paper.get("venue") or "—")
        year = escape(str(paper.get("year") or "—"))
        pages = escape(str(paper.get("pages", "—")))
        status = escape(STATUS.get(paper["status"], "未知"))
        rows.append(
            f'<tr><td><a class="paper-title" href="{escape(url, quote=True)}" target="_self">'
            f'{title}</a><span class="paper-filename">{filename}</span></td>'
            f"<td>{authors}</td><td>{year}</td><td>{venue}</td><td>{pages}</td><td>{status}</td></tr>"
        )
    return (
        """<style>
    .literature-list {width:100%; border-collapse:collapse; table-layout:fixed; font-size:13px;}
    .literature-list th,.literature-list td {padding:10px 7px; text-align:left;
        vertical-align:top; overflow-wrap:anywhere; white-space:normal;
        border-bottom:1px solid #e0e4ea;}
    .literature-list th {font-weight:600; color:#536174; background:#f3f5f8;
        padding-top:8px; padding-bottom:8px;}
    .literature-list tr:hover td {background:#f1f5fb;}
    .literature-list .paper-title {display:block; color:#1f3554;
        text-decoration:none; line-height:1.5;}
    .literature-list .paper-title:hover {color:#356ac3; text-decoration:underline;}
    .paper-filename {display:block; font-size:11px; color:#7b8798; margin-top:4px;}
    </style><table class="literature-list" aria-label="文献列表">
    <colgroup><col style="width:33%"><col style="width:19%"><col style="width:8%">
    <col style="width:24%"><col style="width:7%"><col style="width:9%"></colgroup>
    <thead><tr><th scope="col">标题</th><th scope="col">作者</th><th scope="col">年份</th>
    <th scope="col">期刊 / 会议</th><th scope="col">页数</th><th scope="col">状态</th></tr></thead>
    <tbody>"""
        + "".join(rows)
        + "</tbody></table>"
    )
