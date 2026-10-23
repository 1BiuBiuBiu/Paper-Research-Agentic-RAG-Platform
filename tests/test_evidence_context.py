"""Reference assembly: the citing paragraph joins the table/figure it talks about."""

import json
from pathlib import Path

from app.domain.papers import (
    Block,
    Box,
    Document,
    Figure,
    PageInfo,
    PaperMetadata,
    PaperTable,
    ParsedPaper,
    utc_now,
)
from app.domain.qa import Chunk, Evidence, IndexManifest
from app.services.corrective_agent import CorrectiveAgent
from app.services.evidence_context import attach_references
from tests.qa_helpers import RecordingReranker
from tests.test_corrective_agent import Scripted, Search, payload_of

BOX = Box(left=48, top=100, right=550, bottom=180)


def block(identifier: str, page: int, kind: str, text: str, order: int = 0) -> Block:
    return Block(
        id=identifier,
        page=page,
        order=order,
        kind=kind,
        raw_text=text,
        text=text,
        bbox=BOX,
    )


def chunk(identifier: str, page: int, text: str, block_ids: list[str], **kwargs) -> Chunk:
    return Chunk(id=identifier, page=page, text=text, block_ids=block_ids, boxes=[BOX], **kwargs)


def evidence(identifier: str, page: int, text: str, block_ids: list[str], **kwargs) -> Evidence:
    return Evidence(
        id=identifier,
        page=page,
        text=text,
        block_ids=block_ids,
        boxes=[BOX],
        source_id="E1",
        document_id="doc",
        title="Synthetic",
        filename="synthetic.pdf",
        score=0.5,
        **kwargs,
    )


def build(
    *,
    table_mentions: list[str] | None = None,
    figure_mentions: list[str] | None = None,
    chunks: list[Chunk] | None = None,
) -> tuple[dict[str, Document], dict[str, IndexManifest]]:
    table = PaperTable(
        id="t1",
        source_ref="#/tables/0",
        page=9,
        bbox=BOX,
        label="Table 1",
        caption="Table 1: Results on CMU-MOSI.",
        caption_block_ids=["cap"],
        mention_block_ids=table_mentions or [],
        rows=1,
        columns=2,
        cells=[],
    )
    figure = Figure(
        id="f1",
        page=5,
        bbox=BOX,
        image="figure-1.png",
        label="Figure 1",
        caption="Figure 1: Training curves.",
        caption_block_ids=["fcap"],
        mention_block_ids=figure_mentions or [],
    )
    parsed = ParsedPaper(
        parser="synthetic",
        pages=[
            PageInfo(number=5, width=612, height=792, image="p5.png"),
            PageInfo(number=8, width=612, height=792, image="p8.png"),
            PageInfo(number=9, width=612, height=792, image="p9.png"),
        ],
        blocks=[
            block("p1", 8, "text", "Table 1 summarizes the full-modality comparison."),
            block("pfig", 4, "text", "Figure 1 shows the training curves of both runs."),
            block("cap", 9, "caption", "Table 1: Results on CMU-MOSI."),
            block("fcap", 5, "caption", "Figure 1: Training curves."),
        ],
        figures=[figure],
        tables=[table],
        metadata=PaperMetadata(title="Synthetic"),
    )
    document = Document(
        id="doc",
        filename="synthetic.pdf",
        sha256="0" * 64,
        status="parsed",
        created_at=utc_now(),
        updated_at=utc_now(),
        parsed=parsed,
    )
    default_chunks = [
        chunk("c-para", 8, "Table 1 summarizes the full-modality comparison.", ["p1"]),
        chunk("c-figpara", 4, "Figure 1 shows the training curves of both runs.", ["pfig"]),
        chunk("c-cap", 5, "Figure 1: Training curves.", ["fcap"]),
        chunk(
            "c-row",
            9,
            "Table 1: Results on CMU-MOSI. | row 1 | Acc2 = 92.1",
            ["cap"],
            element_type="table",
            element_id="t1",
        ),
    ]
    manifest = IndexManifest(
        document_id="doc",
        generation="generation",
        fingerprint="fingerprint",
        model="synthetic",
        revision="0",
        dimension=4,
        created_at=utc_now(),
        chunks=chunks if chunks is not None else default_chunks,
    )
    return {"doc": document}, {"doc": manifest}


def row_and_paragraph():
    return [
        evidence("c-para", 8, "Table 1 summarizes the full-modality comparison.", ["p1"]),
        evidence(
            "c-row",
            9,
            "Table 1: Results on CMU-MOSI. | row 1 | Acc2 = 92.1",
            ["cap"],
            element_type="table",
            element_id="t1",
        ),
    ]


def test_citing_paragraph_is_merged_into_its_table_row():
    documents, manifests = build(table_mentions=["p1"])
    merged = attach_references(row_and_paragraph(), documents, manifests)
    assert len(merged) == 1
    item = merged[0]
    assert item.id == "c-row" and item.page == 9
    assert "summarizes the full-modality comparison" in item.text
    assert "Acc2 = 92.1" in item.text


def test_citing_paragraph_is_attached_when_it_was_not_retrieved():
    documents, manifests = build(table_mentions=["p1"])
    merged = attach_references([row_and_paragraph()[1]], documents, manifests)
    assert len(merged) == 1
    assert "引用该图表的原文（第 8 页，节选）" in merged[0].text
    assert "summarizes" in merged[0].text and merged[0].page == 9


def test_table_without_a_citing_paragraph_is_unchanged():
    documents, manifests = build(table_mentions=[])
    item = row_and_paragraph()[1]
    merged = attach_references([item], documents, manifests)
    assert merged[0].text == item.text


def test_paragraph_without_its_table_is_left_alone():
    documents, manifests = build(table_mentions=["p1"])
    merged = attach_references([row_and_paragraph()[0]], documents, manifests)
    assert len(merged) == 1
    # A caption belongs to its table; it must never be attached to the paragraph alone.
    assert merged[0].text == "Table 1 summarizes the full-modality comparison."


def test_figure_caption_gets_its_citing_paragraph():
    documents, manifests = build(figure_mentions=["pfig"])
    merged = attach_references(
        [
            evidence("c-figpara", 4, "Figure 1 shows the training curves.", ["pfig"]),
            evidence("c-cap", 5, "Figure 1: Training curves.", ["fcap"]),
        ],
        documents,
        manifests,
    )
    assert len(merged) == 1 and merged[0].id == "c-cap"
    assert "training curves of both runs" in merged[0].text
    assert "Figure 1: Training curves." in merged[0].text


def test_paragraph_naming_a_figure_without_evidence_is_left_alone():
    documents, manifests = build(figure_mentions=["pfig"])
    item = evidence("c-figpara", 4, "Figure 1 shows the training curves.", ["pfig"])
    merged = attach_references([item], documents, manifests)
    assert merged[0].text == "Figure 1 shows the training curves."


def test_a_table_is_not_attached_twice_to_its_own_rows():
    documents, manifests = build(table_mentions=["p1"])
    first = evidence("c-row", 9, "row one", ["cap"], element_type="table", element_id="t1")
    second = evidence("c-row2", 9, "row two", ["cap"], element_type="table", element_id="t1")
    merged = attach_references([first, second], documents, manifests)
    assert sum("引用该图表的原文" in item.text for item in merged) == 1


def test_unknown_documents_are_left_alone():
    documents, manifests = build(table_mentions=["p1"])
    item = evidence("c-row", 9, "row", ["cap"], element_type="table", element_id="t1")
    item = item.model_copy(update={"document_id": "other"})
    merged = attach_references([item], documents, manifests)
    assert merged[0].text == "row"


def test_assembly_does_not_need_the_pdf_on_disk(tmp_path: Path):
    documents, manifests = build(table_mentions=["p1"])
    assert not list(tmp_path.glob("*.pdf"))
    assert attach_references(row_and_paragraph(), documents, manifests)


def test_generation_receives_the_merged_row_and_paragraph():
    documents, manifests = build(table_mentions=["p1"])
    provider = Scripted([{"citations": ["E1"], "sufficient": True}])
    agent = CorrectiveAgent(provider, RecordingReranker(), Search(), "flash", 12000)
    agent.run(
        "Which Acc2 does Table 1 report?",
        "Which Acc2 does Table 1 report?",
        lambda query: [row_and_paragraph()[1]],
        lambda: None,
        [],
        [],
        allow_web=False,
        chat=False,
        enrich=lambda items: attach_references(items, documents, manifests),
    )
    sent = json.dumps(payload_of(provider.calls[-1]), ensure_ascii=False)
    assert "summarizes the full-modality comparison" in sent
    assert "Acc2 = 92.1" in sent
