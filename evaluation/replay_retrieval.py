"""Free, local-only replay of the library retrieval used by the agent.

Only local models run here: the dense encoder, the BM25 statistics and the cross-encoder.
No LLM provider, web search or paid service is called, so this measures how much of the
gold evidence reaches the fused pool and the reranked window (`rerank_top_n`) before any
answer is generated.

Usage:
    uv run python -m evaluation.replay_retrieval \
        --runtime output/evaluation/replay-v0.13/runtime \
        --questions evaluation/datasets/v0.2/rag_questions.jsonl \
        --prefix ADD- --out output/evaluation/replay-v0.13/coverage.json
"""

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.core.config import Settings
from app.repositories.indices import IndexRepository
from app.repositories.papers import PaperRepository
from app.retrieval.bm25 import tokens
from app.retrieval.embedding import BGEEmbedder
from app.retrieval.indexing import IndexService
from app.retrieval.reranking import CrossEncoderReranker
from app.retrieval.search import fuse, library_candidates


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def text_matches(quote: str, chunk: str) -> bool:
    """A gold quote covers a chunk when the quote (or most of its terms) is present."""
    gold = normalize(quote)
    if not gold:
        return False
    if gold in normalize(chunk):
        return True
    wanted = set(tokens(gold))
    if not wanted:
        return False
    return len(wanted & set(tokens(chunk))) / len(wanted) >= 0.6


def gold_hit(gold: dict, evidence, labels: dict[str, str]) -> bool:
    if gold.get("document_id") != labels.get(evidence.document_id):
        return False
    if gold.get("page") and evidence.page != gold["page"]:
        return False
    return text_matches(gold.get("evidence_text", ""), evidence.text)


def first_rank(golds: list[dict], evidence, labels: dict[str, str]) -> int | None:
    for rank, item in enumerate(evidence, 1):
        if any(gold_hit(gold, item, labels) for gold in golds):
            return rank
    return None


def covered(golds: list[dict], evidence, labels: dict[str, str]) -> int:
    return sum(
        1
        for gold in golds
        if any(gold_hit(gold, item, labels) for item in evidence)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--prefix", default="ADD-", help="Only replay matching question IDs.")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-rerank", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = replace(
        Settings.from_env(), data_dir=args.runtime.resolve(), auto_index=False, candidates=60
    )
    papers = PaperRepository(settings.data_dir / "library.sqlite3")
    indices = IndexService(papers, IndexRepository(papers), BGEEmbedder(settings))
    reranker = CrossEncoderReranker(settings)

    questions = [
        json.loads(line)
        for line in args.questions.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    questions = [row for row in questions if row["question_id"].startswith(args.prefix)]
    questions = questions[args.offset :]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        raise SystemExit("No matching questions")

    labels: dict[str, str] = {}
    documents = {}
    started = time.monotonic()
    for question in questions:
        for dataset_id in question["scope_document_ids"]:
            identifier = str(uuid5(NAMESPACE_URL, dataset_id))
            if identifier not in documents:
                document = papers.get(identifier)
                documents[identifier] = document
                labels[identifier] = dataset_id
    for identifier, document in documents.items():
        status = indices.status(identifier).status
        if status != "ready":
            print(f"Indexing {labels[identifier]} (was {status})", flush=True)
            indices.build(identifier)
        if not args.no_rerank and not reranker.ready():
            raise SystemExit("Reranker model is not available locally")

    results = []
    for question in questions:
        documents_in_scope = [papers.get(str(uuid5(NAMESPACE_URL, item))) for item in question["scope_document_ids"]]
        manifests = [indices.require(document) for document in documents_in_scope]
        question_started = time.monotonic()
        vectors = indices.embedder.encode([question["question"]])
        pool = fuse(
            library_candidates(
                indices,
                documents_in_scope,
                manifests,
                question["question"],
                settings.candidates,
                query_vectors=vectors,
            ),
            settings.candidates,
        )
        ranked = (
            []
            if args.no_rerank
            else reranker.rank(question["question"], pool, top_n=settings.rerank_top_n)
        )
        golds = [gold for gold in question["gold_evidence"] if gold.get("relevance", 2) == 2]
        results.append(
            {
                "question_id": question["question_id"],
                "language": question["language"],
                "answerable": question["answerable"],
                "gold": len(golds),
                "pool_rank": first_rank(golds, pool, labels),
                "pool_covered": covered(golds, pool, labels),
                "top12_rank": first_rank(golds, ranked, labels),
                "top12_covered": covered(golds, ranked, labels),
                "pool_size": len(pool),
                "seconds": round(time.monotonic() - question_started, 2),
            }
        )
        print(
            f"{question['question_id']} pool={results[-1]['pool_rank']} "
            f"top12={results[-1]['top12_rank']} "
            f"covered={results[-1]['top12_covered']}/{len(golds)}",
            flush=True,
        )

    answerable = [row for row in results if row["answerable"] and row["gold"]]
    summary = {
        "questions": len(results),
        "answerable_with_gold": len(answerable),
        "pool_any_gold": sum(bool(row["pool_rank"]) for row in answerable),
        "pool_all_gold": sum(row["pool_covered"] == row["gold"] for row in answerable),
        "top12_any_gold": sum(bool(row["top12_rank"]) for row in answerable),
        "top12_all_gold": sum(row["top12_covered"] == row["gold"] for row in answerable),
        "reranked": not args.no_rerank,
        "rerank_query": "original question (no English translation in this replay)",
        "seconds": round(time.monotonic() - started, 1),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"summary": summary, "questions": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
