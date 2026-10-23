"""Measure what rerank_top_n buys and costs, using only local models and recorded data.

For each question the script recomputes the fused pool, reranks it with the same English
query the product used (recovered from a recorded run, so no LLM call), then reports how
much gold evidence survives at each k and how many bytes of evidence the grading prompt
would carry. No provider or web call is made.

Usage:
    uv run python -m evaluation.measure_top_n \
        --runtime output/evaluation/replay-v0.13/runtime \
        --questions evaluation/datasets/v0.2/rag_questions.jsonl \
        --translations output/evaluation/expanded-v0.3-rag-0.12.0/agent_outputs.json \
        --out output/evaluation/replay-v0.13/top_n_sweep.json
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
from app.retrieval.embedding import BGEEmbedder
from app.retrieval.indexing import IndexService
from app.retrieval.reranking import CrossEncoderReranker
from app.retrieval.search import fuse, library_candidates
from evaluation.replay_retrieval import gold_hit

KS = (4, 6, 8, 10, 12)


def load_translations(path: Path) -> dict[str, str]:
    """Recover the English rerank query of each recorded turn; no model is called."""
    translations: dict[str, str] = {}
    if not path.exists():
        return translations
    for row in json.loads(path.read_text(encoding="utf-8")):
        for step in row.get("trace") or []:
            reason = str(step.get("reason", ""))
            if step.get("node") == "translate_query" and reason.startswith("translated:"):
                translations[row["question_id"]] = reason.split("translated:", 1)[1].strip()
                break
    return translations


def evidence_bytes(evidence, k: int) -> int:
    payload = [
        {"source_id": f"E{index + 1}", "text": item.text} for index, item in enumerate(evidence[:k])
    ]
    return len(json.dumps(payload, ensure_ascii=False).encode())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--translations", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--prefix", default="ADD-")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = replace(
        Settings.from_env(), data_dir=args.runtime.resolve(), auto_index=False, candidates=60
    )
    papers = PaperRepository(settings.data_dir / "library.sqlite3")
    indices = IndexService(papers, IndexRepository(papers), BGEEmbedder(settings))
    reranker = CrossEncoderReranker(settings)

    translations = load_translations(args.translations) if args.translations else {}
    questions = [
        json.loads(line)
        for line in args.questions.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    questions = [row for row in questions if row["question_id"].startswith(args.prefix)]
    if not questions:
        raise SystemExit("No matching questions")

    documents: dict[str, object] = {}
    labels: dict[str, str] = {}
    for question in questions:
        for dataset_id in question["scope_document_ids"]:
            identifier = str(uuid5(NAMESPACE_URL, dataset_id))
            if identifier not in documents:
                document = papers.get(identifier)
                indices.require(document)
                documents[identifier] = document
                labels[identifier] = dataset_id

    rerank_queries = [translations.get(row["question_id"], row["question"]) for row in questions]
    started = time.monotonic()
    # One encoder call for every query: the model loads once instead of once per question.
    vectors = indices.embedder.encode(rerank_queries)
    print(f"encoded {len(questions)} queries in {time.monotonic() - started:.1f}s", flush=True)

    rows = []
    for index, question in enumerate(questions):
        scope = [
            documents[str(uuid5(NAMESPACE_URL, item))] for item in question["scope_document_ids"]
        ]
        manifests = [indices.require(document) for document in scope]
        pool = fuse(
            library_candidates(
                indices,
                scope,
                manifests,
                question["question"],
                settings.candidates,
                query_vectors=vectors[index : index + 1],
            ),
            settings.candidates,
        )
        ranked = reranker.rank(rerank_queries[index], pool, top_n=max(KS))
        golds = [gold for gold in question["gold_evidence"] if gold.get("relevance", 2) == 2]
        row = {
            "question_id": question["question_id"],
            "gold": len(golds),
            "translated": question["question_id"] in translations,
            "first_gold_rank": next(
                (
                    position
                    for position, item in enumerate(ranked, 1)
                    if any(gold_hit(gold, item, labels) for gold in golds)
                ),
                None,
            ),
            "bytes": {str(k): evidence_bytes(ranked, k) for k in KS},
            "covered": {
                str(k): sum(
                    1 for gold in golds if any(gold_hit(gold, item, labels) for item in ranked[:k])
                )
                for k in KS
            },
        }
        rows.append(row)
        print(
            f"{row['question_id']} first_gold_rank={row['first_gold_rank']} "
            f"bytes@8={row['bytes']['8']}",
            flush=True,
        )

    answered = [row for row in rows if row["gold"]]
    summary = {"questions": len(rows), "with_gold": len(answered), "sweep": {}}
    for k in KS:
        key = str(k)
        summary["sweep"][key] = {
            "any_gold": sum(1 for row in answered if row["covered"][key] > 0),
            "all_gold": sum(1 for row in answered if row["covered"][key] == row["gold"]),
            "mean_evidence_bytes": round(
                sum(row["bytes"][key] for row in answered) / len(answered)
            ),
            "max_evidence_bytes": max(row["bytes"][key] for row in answered),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
