"""Collect real local pilot outputs in an isolated runtime directory."""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.core.config import Settings
from app.domain.papers import Document, ParsedPaper, utc_now
from app.parsing.docling_parser import parse_pdf
from app.repositories.indices import IndexRepository
from app.repositories.papers import PaperRepository
from app.retrieval.embedding import BGEEmbedder
from app.retrieval.indexing import IndexService
from app.retrieval.reranking import CrossEncoderReranker
from app.retrieval.search import fuse, library_candidates
from app.ui_components.page_reading import heading_depth

ROOT = Path("evaluation/datasets")
OUT = Path("output/evaluation/pilot-2026-10-23")


def load(name):
    return [
        json.loads(line)
        for line in (ROOT / name).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    os.environ["HF_HUB_DISABLE_XET"] = "1"

    OUT.mkdir(parents=True, exist_ok=True)
    settings = replace(Settings.from_env(), data_dir=(OUT / "runtime").resolve(), parse_timeout=900)
    papers = PaperRepository(settings.data_dir / "library.sqlite3")
    indices = IndexService(papers, IndexRepository(papers), BGEEmbedder(settings))
    summary = json.loads((ROOT / "dataset_summary.json").read_text(encoding="utf-8-sig"))
    headings = []
    for info in summary["document_breakdown"]:
        path = ROOT / info["filename"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != info["pdf_sha256"]:
            raise ValueError("PDF fingerprint mismatch: " + info["filename"])
        key = path.stem
        assets = OUT / key
        result = assets / "result.json"
        print("Parsing " + key, flush=True)
        parsed = (
            ParsedPaper.model_validate_json(result.read_bytes())
            if result.exists()
            else parse_pdf(path.resolve(), assets.resolve())
        )
        if not result.exists():
            result.write_text(parsed.model_dump_json(), encoding="utf-8")
        doc = Document(
            id=str(uuid5(NAMESPACE_URL, info["document_id"])),
            filename=info["filename"],
            sha256=digest,
            status="parsed",
            created_at=utc_now(),
            updated_at=utc_now(),
            parsed=parsed,
        )
        papers.save(doc)
        for block in parsed.blocks:
            level = heading_depth(block.model_dump())
            if level is not None and not block.excluded_reason:
                headings.append(
                    dict(
                        document_id=info["document_id"],
                        block_id=block.id,
                        page=block.page,
                        text=block.text,
                        level=level,
                    )
                )
        save("predicted_headings.json", headings)
        print("Indexing " + key, flush=True)
    for info in summary["document_breakdown"]:
        indices.build(str(uuid5(NAMESPACE_URL, info["document_id"])))
    mapping = {
        i["document_id"]: str(uuid5(NAMESPACE_URL, i["document_id"]))
        for i in summary["document_breakdown"]
    }
    outputs = []
    reranker = CrossEncoderReranker(settings)
    for q in load("rag_questions.jsonl"):
        print("Retrieving " + q["question_id"], flush=True)
        vectors = indices.embedder.encode([q["question"]])
        documents = [papers.get(mapping[original_id]) for original_id in q["scope_document_ids"]]
        manifests = [indices.require(document) for document in documents]
        candidates = fuse(
            library_candidates(
                indices,
                documents,
                manifests,
                q["question"],
                settings.candidates,
                query_vectors=vectors,
            ),
            settings.candidates,
        )
        candidates = [
            e.model_copy(update={"source_id": f"E{i + 1}"}) for i, e in enumerate(candidates)
        ]
        ranked = reranker.rank(q["question"], candidates, top_n=5)
        outputs.append(
            dict(
                question_id=q["question_id"],
                question=q["question"],
                stage="reranked_pre_filter",
                scope_document_ids=q["scope_document_ids"],
                candidates=[e.model_dump() for e in candidates],
                top5=[e.model_dump() for e in ranked],
            )
        )
        save("retrieval_outputs.json", outputs)
    print("Local collection complete", flush=True)


if __name__ == "__main__":
    main()
