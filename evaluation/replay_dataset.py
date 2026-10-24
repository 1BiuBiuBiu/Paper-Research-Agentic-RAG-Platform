"""Free, local-only replay of a labelled dataset: parse, index, rerank, score.

Only local models run here: Docling for PDFs without a cached parse, BGE-M3 for vectors, the
BM25 statistics and the Cross-Encoder. No LLM provider, web search or paid service is called, so
answer and citation metrics stay empty in the benchmark until a separate paid run is authorised.

Usage:
    uv run python -m evaluation.replay_dataset \
        --dataset evaluation/datasets/v0.3 \
        --out output/evaluation/dataset-v0.3-0.13.8 \
        --cache output/evaluation/pilot-2026-10-23 \
        --cache output/evaluation/expanded-v0.3-0.11.2
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import tomllib
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
from evaluation.metrics import Dataset, score
from evaluation.pilot_headings import evaluate as heading_evaluate
from evaluation.replay_scoring import (
    BENCHMARK_WINDOW,
    coverage_row,
    heading_candidates,
    retrieval_record,
    summarise,
)

DUPLICATE_SUFFIX = re.compile(r"\(\d+\)(?=\.pdf$)", re.IGNORECASE)


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_pdf(name: str, dataset: Path) -> Path | None:
    """Accept the recorded filename or its de-duplicated form as saved by the browser."""
    canonical = DUPLICATE_SUFFIX.sub("", name)
    for base in (dataset, Path.home() / "Downloads"):
        for candidate_name in dict.fromkeys((name, canonical)):
            candidate = base / candidate_name
            if candidate.is_file():
                return candidate
    return None


def cached_parse(stem: str, caches: list[Path]) -> Path | None:
    for base in caches:
        candidate = base / stem / "result.json"
        if candidate.is_file():
            return candidate
    return None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cache", action="append", default=[], type=Path)
    parser.add_argument("--window", type=int, default=20, help="Reranked rows kept for the report.")
    parser.add_argument("--prefix", default="", help="Only replay matching question IDs.")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args(argv)


def load_documents(args, summary, out, manifest):
    """Verify each PDF fingerprint, reuse a cached parse or run Docling, collect predictions."""
    predictions: list[dict] = []
    labels: dict[str, str] = {}
    documents = []
    for info in summary["document_breakdown"]:
        source = resolve_pdf(info["filename"], args.dataset)
        if source is None:
            raise SystemExit("PDF not found: " + info["filename"])
        sha = digest(source)
        if sha != info["pdf_sha256"]:
            raise SystemExit("PDF fingerprint mismatch: " + info["filename"])
        document_id = str(uuid5(NAMESPACE_URL, info["document_id"]))
        labels[document_id] = info["document_id"]
        cached = cached_parse(source.stem, [path.resolve() for path in args.cache])
        print(f"Parsing {info['filename']} (cache={bool(cached)})", flush=True)
        began = time.monotonic()
        if cached:
            parsed = ParsedPaper.model_validate_json(cached.read_bytes())
            manifest.setdefault("cached_parses", {})[info["document_id"]] = {
                "path": str(cached),
                "sha256": digest(cached),
            }
            origin = "cached_local_parse_from_previous_run"
        else:
            assets = out / source.stem
            parsed = parse_pdf(source.resolve(), assets.resolve())
            (assets / "result.json").write_text(parsed.model_dump_json(), encoding="utf-8")
            origin = "fresh_local_docling"
        if len(parsed.pages) != info["page_count"]:
            raise SystemExit(
                f"Page count mismatch for {info['filename']}: "
                f"{len(parsed.pages)} != {info['page_count']}"
            )
        documents.append(
            Document(
                id=document_id,
                filename=info["filename"],
                sha256=sha,
                status="parsed",
                created_at=utc_now(),
                updated_at=utc_now(),
                parsed=parsed,
            )
        )
        for block in parsed.blocks:
            level = heading_depth(block.model_dump())
            if level is not None and not block.excluded_reason:
                predictions.append(
                    {
                        "document_id": info["document_id"],
                        "page": block.page,
                        "text": block.text,
                        "level": level,
                    }
                )
        manifest["documents"].append(
            {
                "document_id": info["document_id"],
                "filename": info["filename"],
                "pdf": str(source),
                "pdf_sha256": sha,
                "pages": len(parsed.pages),
                "blocks": len(parsed.blocks),
                "parse_origin": origin,
                "parse_seconds": round(time.monotonic() - began, 2),
            }
        )
        save(out / "predicted_headings.json", predictions)
        save(out / "manifest.json", manifest)
    return labels, documents, predictions


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"Output directory already has results: {out}")
    out.mkdir(parents=True, exist_ok=True)

    summary = json.loads((args.dataset / "dataset_summary.json").read_text(encoding="utf-8-sig"))
    gold = read_jsonl(args.dataset / "structure_gold.jsonl")
    questions = read_jsonl(args.dataset / "rag_questions.jsonl")
    if args.prefix:
        questions = [row for row in questions if row["question_id"].startswith(args.prefix)]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        raise SystemExit("No matching questions")

    settings = replace(
        Settings.from_env(),
        data_dir=(out / "runtime").resolve(),
        auto_index=False,
        parse_timeout=1200,
    )
    if args.window > settings.candidates:
        raise SystemExit("--window cannot exceed the candidate pool")
    if settings.rerank_top_n > args.window:
        raise SystemExit("--window must cover RESEARCH_RERANK_TOP_N")

    started = time.monotonic()
    manifest = {
        "application": tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"][
            "version"
        ],
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "mode": "local_only_replay_no_llm_no_web",
        "paid_calls": 0,
        "dataset": str(args.dataset),
        "dataset_sha256": {
            path.name: digest(path) for path in sorted(args.dataset.glob("*.json*"))
        },
        "candidates": settings.candidates,
        "rerank_window": args.window,
        "rerank_top_n": settings.rerank_top_n,
        "retrieval": "BGE-M3 dense + BM25 fused by RRF then Cross-Encoder",
        "documents": [],
    }
    papers = PaperRepository(settings.data_dir / "library.sqlite3")
    labels, documents, predictions = load_documents(args, summary, out, manifest)
    for document in documents:
        papers.save(document)
    indices = IndexService(papers, IndexRepository(papers), BGEEmbedder(settings))
    reranker = CrossEncoderReranker(settings)

    print("Building indexes", flush=True)
    index_started = time.monotonic()
    for document in documents:
        indices.build(document.id)
    chunks = 0
    for info in manifest["documents"]:
        document = papers.get(str(uuid5(NAMESPACE_URL, info["document_id"])))
        index_manifest = indices.require(document)
        info["chunks"] = len(index_manifest.chunks)
        info["index_model"] = f"{index_manifest.model}@{index_manifest.revision}"
        info["index_generation"] = index_manifest.generation
        chunks += info["chunks"]
    manifest["index_seconds"] = round(time.monotonic() - index_started, 2)
    manifest["chunks"] = chunks
    save(out / "manifest.json", manifest)
    print(f"Indexed {len(documents)} papers, {chunks} chunks", flush=True)

    report = heading_evaluate(gold, predictions)
    save(out / "heading_report.json", report)
    headline = {
        key: report[key] for key in ("micro_f1", "macro_f1", "level_accuracy", "tp", "fp", "fn")
    }
    print("Heading " + json.dumps(headline), flush=True)

    coverage: list[dict] = []
    retrieval: list[dict] = []
    for question in questions:
        in_scope = [
            papers.get(str(uuid5(NAMESPACE_URL, item))) for item in question["scope_document_ids"]
        ]
        manifests = [indices.require(document) for document in in_scope]
        vectors = indices.embedder.encode([question["question"]])
        pool = fuse(
            library_candidates(
                indices,
                in_scope,
                manifests,
                question["question"],
                settings.candidates,
                query_vectors=vectors,
            ),
            settings.candidates,
        )
        window = reranker.rank(question["question"], pool, top_n=args.window)
        golds = [item for item in question["gold_evidence"] if item.get("relevance") == 2]
        coverage.append(coverage_row(question, pool, window, labels))
        if golds:
            retrieval.append(retrieval_record(question, golds, coverage[-1]))
        save(out / "coverage.json", coverage)
        row = coverage[-1]
        print(
            f"{question['question_id']} gold={row['gold_direct']} pool={row['pool_rank']} "
            f"window={row['first_hit_rank']} "
            f"covered={row['gold_covered_window']}/{row['gold_direct']}",
            flush=True,
        )
    metrics = summarise(coverage, time.monotonic() - started)
    save(out / "coverage_summary.json", metrics)

    benchmark = {
        "run_id": (
            f"dataset-{args.dataset.name}-{manifest['application']}-{time.strftime('%Y-%m-%d')}"
        ),
        "provenance": {
            "application": manifest["application"],
            "git_commit": manifest["git_commit"],
            "dataset": str(args.dataset),
            "runtime": str(out / "runtime"),
            "window": f"rerank top {BENCHMARK_WINDOW} of {args.window} replayed rows",
            "models": "local BGE-M3 + BM25 + Cross-Encoder; no LLM, no web search",
            "gold_assumption": (
                "direct_evidence_exhaustive=false, so evidence_complete=false "
                "and Hit is the publishable metric"
            ),
            "annotator": "assistant text-match grading; single reviewer, not double-blind",
            "heading_excluded_pending_review": str(
                sum(row["category"] == "excluded_pending_review" for row in report["rows"])
            ),
        },
        "top_k": BENCHMARK_WINDOW,
        "heading_candidates_complete": True,
        "headings": heading_candidates(report),
        "retrieval": retrieval,
        "answers": [],
    }
    save(out / "benchmark.json", benchmark)
    scored = score(Dataset.model_validate(benchmark))
    save(out / "metrics.json", scored)
    manifest["finished"] = utc_now()
    manifest["seconds"] = round(time.monotonic() - started, 1)
    save(out / "manifest.json", manifest)
    print(json.dumps({"coverage": metrics, "metrics": scored}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
