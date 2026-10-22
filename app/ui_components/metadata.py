from typing import Any

import streamlit as st

from app.ui_components.client import api

VENUES = {"unknown": "未确认", "journal": "期刊", "conference": "会议", "preprint": "预印本"}


def metadata_editor(document: dict[str, Any]) -> None:
    metadata = document["parsed"]["metadata"]
    prefix = f"metadata:{document['id']}:{document['updated_at']}"
    st.subheader("论文信息")
    st.caption("自动提取结果为候选。修正后保存的字段会标记为人工确认。")
    with st.form(f"metadata-{document['id']}"):
        title = st.text_input("论文标题", value=metadata["title"] or "", key=f"{prefix}:title")
        authors = st.text_input(
            "作者（使用分号分隔）", value="; ".join(metadata["authors"]), key=f"{prefix}:authors"
        )
        columns = st.columns(2)
        year = columns[0].text_input(
            "发表年份", value=str(metadata["publication_year"] or ""), key=f"{prefix}:year"
        )
        kind = columns[1].selectbox(
            "出版类型",
            list(VENUES),
            index=list(VENUES).index(metadata["venue_type"]),
            format_func=lambda v: VENUES[v],
            key=f"{prefix}:kind",
        )
        venue = st.text_input("期刊 / 会议", value=metadata["venue"] or "", key=f"{prefix}:venue")
        doi = st.text_input("DOI", value=metadata["doi"] or "", key=f"{prefix}:doi")
        issn = st.text_input("ISSN", value=metadata["issn"] or "", key=f"{prefix}:issn")
        abstract = st.text_area(
            "原文摘要（缺失可手工补充）",
            value=metadata["abstract"] or "",
            height=280,
            key=f"{prefix}:abstract",
        )
        if st.form_submit_button("保存论文信息"):
            if year and (not year.isdigit() or not 1500 <= int(year) <= 2100):
                st.error("请填写 1500–2100 之间的有效年份。")
            else:
                values = {
                    "title": title.strip() or None,
                    "authors": [n.strip() for n in authors.split(";") if n.strip()],
                    "publication_year": int(year) if year else None,
                    "venue_type": kind,
                    "venue": venue.strip() or None,
                    "doi": doi.strip() or None,
                    "issn": issn.strip() or None,
                    "abstract": abstract.strip() or None,
                }
                changes = {key: value for key, value in values.items() if value != metadata[key]}
                if not changes:
                    st.info("没有需要保存的修改。")
                    return
                api(
                    "PATCH",
                    f"/documents/{document['id']}/metadata",
                    json=changes,
                )
                st.rerun()
    with st.expander("查看字段来源"):
        st.json(metadata["provenance"])


def metric_editor(document: dict[str, Any]) -> None:
    paper = document["parsed"]
    metric = paper["metric"]
    prefix = f"metric:{document['id']}:{document['updated_at']}"
    st.subheader("期刊影响因子")
    if metric["status"] == "not_applicable":
        st.info("会议 / 预印本：JIF 不适用。")
        return
    if metric["status"] == "available":
        st.metric(f"{metric['year']} 年 JIF", metric["value"])
        st.caption(f"来源：{metric['source']} · 记录时间：{metric['retrieved_at']}")
    else:
        st.info("未获取。可以录入已有来源的期刊指标，不会自动猜测数值。")
    if paper["metadata"]["venue_type"] != "journal" or not paper["metadata"]["venue"]:
        st.caption("录入指标前，请在论文信息中确认期刊类型与名称。")
        return
    with st.form(f"metric-{document['id']}"):
        year = st.number_input(
            "指标年份", min_value=1900, max_value=2100, value=2025, key=f"{prefix}:year"
        )
        value = st.number_input(
            "JIF", min_value=0.0, max_value=10000.0, step=0.1, key=f"{prefix}:value"
        )
        source = st.text_input("指标来源（报告、链接或导入说明）", key=f"{prefix}:source")
        if st.form_submit_button("保存指标"):
            api(
                "PUT",
                f"/documents/{document['id']}/metric",
                json={
                    "year": year,
                    "value": value,
                    "source": source,
                },
            )
            st.rerun()
