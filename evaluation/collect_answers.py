"""Production agent run over a labelled dataset: real retrieval, English translation and LLM.

This is the evaluation path that produces reported numbers. It calls the configured provider
(paid), so it needs explicit authorisation. Web search stays disabled; per-question results are
saved as they finish and a rerun skips the questions already recorded, including failures.

Usage:
    uv run python -m evaluation.collect_answers \
        --dataset evaluation/datasets/v0.3 \
        --out output/evaluation/production-v0.3-0.13.8 \
        --runtime output/evaluation/dataset-v0.3-0.13.8/runtime
"""

import argparse
import hashlib
import json
import subprocess
import time
import tomllib
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.core.config import Settings
from app.providers.deepseek import DeepSeekProvider
from app.repositories.indices import IndexRepository
from app.repositories.papers import PaperRepository
from app.retrieval.embedding import BGEEmbedder
from app.retrieval.indexing import IndexService
from app.retrieval.reranking import CrossEncoderReranker
from app.retrieval.search import fuse, library_candidates
from app.services.corrective_agent import CorrectiveAgent
from evaluation.replay_dataset import read_jsonl, resolve_pdf


class NoWeb:
    configured = False

    def search(self, question):
        raise RuntimeError("Network search forbidden for this evaluation")


class RecordingProvider:
    def __init__(self, settings):
        self.provider = DeepSeekProvider(settings)
        self.configured = self.provider.configured
        self.grades = []
        self.calls = 0
        self.usage = None

    def complete(self, model, messages):
        if not self.configured:
            raise RuntimeError("DeepSeek is not configured; set DEEPSEEK_API_KEY on the server")
        if "[stage:grade]" in messages[0]["content"]:
            self.grades.append(True)
        return self.provider.complete(model, messages)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--runtime", type=Path, help="Existing runtime dir; defaults to <out>/runtime"
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--model", default="flash")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    runtime = (args.runtime or (out / "runtime")).resolve()
    settings = replace(Settings.from_env(), data_dir=runtime)

    summary = json.loads((args.dataset / "dataset_summary.json").read_text(encoding="utf-8-sig"))
    dataset_questions = read_jsonl(args.dataset / "rag_questions.jsonl")
    questions = dataset_questions
    if args.prefix:
        questions = [row for row in questions if row["question_id"].startswith(args.prefix)]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        raise SystemExit("No questions selected")

    papers = PaperRepository(runtime / "library.sqlite3")
    indices = IndexService(papers, IndexRepository(papers), BGEEmbedder(settings))
    mapping = {
        info["document_id"]: str(uuid5(NAMESPACE_URL, info["document_id"]))
        for info in summary["document_breakdown"]
    }
    sources = {}
    for info in summary["document_breakdown"]:
        path = resolve_pdf(info["filename"], args.dataset)
        if path is None:
            raise SystemExit("PDF not found: " + info["filename"])
        if digest(path) != info["pdf_sha256"]:
            raise SystemExit("PDF fingerprint mismatch: " + info["filename"])
        sources[info["document_id"]] = path
    for document_id in mapping.values():
        document = papers.get(document_id)
        if document is None:
            raise SystemExit("Document is not in the runtime library: " + document_id)
        indices.require(document)

    reranker = CrossEncoderReranker(settings)
    result_path = out / "agent_outputs.json"
    outputs = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else []
    completed = {row.get("question_id") for row in outputs}
    manifest_path = out / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {
            "application": tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
                "project"
            ]["version"],
            "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "mode": "production_agent_no_web",
            "model": args.model,
            "dataset": str(args.dataset),
            "runtime": str(runtime),
            "allow_web": False,
            "candidates": settings.candidates,
            "rerank_top_n": settings.rerank_top_n,
            "context_bytes": settings.context_bytes,
            "agent_max_calls": settings.agent_max_calls,
            "questions": len(dataset_questions),
            "selected": len(questions),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    manifest["dataset_sha256"] = {
        path.name: digest(path) for path in sorted(args.dataset.glob("*.json*"))
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    calls = sum(row.get("model_calls") or 0 for row in outputs)
    tokens = sum((row.get("usage") or {}).get("total_tokens") or 0 for row in outputs)
    for question in questions:
        if question["question_id"] in completed:
            continue
        in_scope = [papers.get(mapping[item]) for item in question["scope_document_ids"]]

        def validate(documents=in_scope, ids=question["scope_document_ids"]):
            for document, dataset_id in zip(documents, ids, strict=True):
                if digest(sources[dataset_id]) != document.sha256:
                    raise ValueError("PDF source changed")
                indices.require(document)

        def search(query, documents=in_scope):
            parts = [query[i : i + 700] for i in range(0, len(query), 600)]
            vectors = indices.embedder.encode(parts)
            manifests = [indices.require(document) for document in documents]
            candidates = fuse(
                library_candidates(
                    indices,
                    documents,
                    manifests,
                    query,
                    settings.candidates,
                    query_vectors=vectors,
                ),
                settings.candidates,
            )
            return [
                item.model_copy(update={"source_id": f"E{index + 1}"})
                for index, item in enumerate(candidates)
            ]

        provider = RecordingProvider(settings)
        workflow = CorrectiveAgent(
            provider,
            reranker,
            NoWeb(),
            args.model,
            settings.context_bytes,
            settings.agent_timeout,
            top_n=settings.rerank_top_n,
            max_calls=settings.agent_max_calls,
        )
        print(f"Answering {question['question_id']}", flush=True)
        began = time.monotonic()
        try:
            state = workflow.run(
                question["question"],
                question["question"],
                search,
                validate,
                [
                    {
                        "document_id": document.id,
                        "title": document.parsed.metadata.title or document.filename,
                        "abstract": (document.parsed.metadata.abstract or "")[:3000],
                    }
                    for document in in_scope
                ],
                [],
                allow_web=False,
                chat=False,
            )
            completion = state["completion"]
            record = dict(
                question_id=question["question_id"],
                question=question["question"],
                answerable=question["answerable"],
                answer=(
                    completion.result.model_dump()
                    if completion
                    else {
                        "answer": "当前范围没有足够原文证据。本轮已关闭联网补充。",
                        "citations": [],
                        "insufficient_evidence": True,
                    }
                ),
                served_model=workflow.served_model,
                model_calls=workflow.calls,
                usage=workflow.usage.model_dump(),
                rewrite_count=state["round"],
                sufficient=state["sufficient"],
                covered=state["covered"],
                missing=state["missing"],
                ledger=[item.model_dump() for item in state["ledger"]],
                cited_sources=[item.model_dump() for item in state["accepted"]],
                trace=[item.model_dump() for item in state["trace"]],
                used_web=state["web_used"],
            )
        except Exception as exc:  # recorded, never retried automatically
            record = dict(
                question_id=question["question_id"],
                question=question["question"],
                answerable=question["answerable"],
                error_type=type(exc).__name__,
                error_code=getattr(exc, "code", "runtime_error"),
                model_calls=workflow.calls,
            )
        record["seconds"] = round(time.monotonic() - began, 1)
        outputs.append(record)
        calls += record.get("model_calls") or 0
        tokens += (record.get("usage") or {}).get("total_tokens") or 0
        result_path.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["model_calls"] = calls
        manifest["total_tokens"] = tokens
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"  -> calls={record.get('model_calls')} "
            f"tokens={(record.get('usage') or {}).get('total_tokens')} "
            f"seconds={record['seconds']}",
            flush=True,
        )
    manifest["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Production collection complete: {len(outputs)} questions, {calls} calls, {tokens} tokens"
    )


if __name__ == "__main__":
    main()
