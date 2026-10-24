"""Score a production run: Recall@k / nDCG@k from the delivered ledger plus answer metrics.

The ledger recorded by `evaluation/collect_answers.py` is the first-round rerank order, so its
prefix is exactly what the implementation hands to the judge and generator. Headings come from the
same runtime parses (deterministic, no LLM). Meaning-based answer verdicts are supplied by the
reviewer in a judgments file; the scorer never invents them.

Usage:
    uv run python -m evaluation.score_production \
        --dataset evaluation/datasets/v0.3 \
        --run output/evaluation/production-v0.3-0.13.8 \
        --runtime output/evaluation/dataset-v0.3-0.13.8/runtime \
        --k 10 --review output/evaluation/production-v0.3-0.13.8/review.md
"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from app.repositories.papers import PaperRepository
from app.ui_components.page_reading import heading_depth
from evaluation.metrics import Dataset, score
from evaluation.pilot_headings import evaluate as heading_evaluate
from evaluation.replay_dataset import read_jsonl
from evaluation.replay_scoring import grade_window, heading_candidates, ndcg

KS = (5, 10, 20)


def load_outputs(run: Path) -> dict[str, dict]:
    rows = json.loads((run / "agent_outputs.json").read_text(encoding="utf-8"))
    return {row["question_id"]: row for row in rows}


def heading_report(runtime: Path, summary: dict, gold: list[dict]) -> tuple[dict, list[dict]]:
    papers = PaperRepository(runtime / "library.sqlite3")
    predictions = []
    for info in summary["document_breakdown"]:
        document = papers.get(str(uuid5(NAMESPACE_URL, info["document_id"])))
        if document is None or document.parsed is None:
            raise SystemExit("Runtime library is missing a parsed paper: " + info["document_id"])
        for block in document.parsed.blocks:
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
    report = heading_evaluate(gold, predictions)
    report["predictions"] = len(predictions)
    return report, predictions


def retrieval_rows(
    questions: list[dict], outputs: dict[str, dict], labels: dict[str, str], k: int
) -> tuple[list[dict], list[dict]]:
    records = []
    details = []
    for question in questions:
        output = outputs.get(question["question_id"], {})
        golds = [item for item in question["gold_evidence"] if item.get("relevance") == 2]
        if not golds:
            continue
        ledger = output.get("ledger") or []
        window = [SimpleNamespace(**item) for item in ledger[:k]]
        rows, found, ones = grade_window(window, golds, labels)
        grades = [row["relevance"] for row in rows]
        ideal = [2] * len(golds) + [1] * ones
        records.append(
            {
                "question_id": question["question_id"],
                "question": question["question"],
                "evidence_complete": True,
                "gold_evidence": [
                    {
                        "evidence_id": f"G{index + 1}",
                        "document_id": gold["document_id"],
                        "page": gold["page"],
                        "element_type": gold.get("element_type", "text"),
                        "table_label": gold.get("table_label"),
                        "evidence_text": gold.get("evidence_text", ""),
                        "relevance": gold.get("relevance", 2),
                    }
                    for index, gold in enumerate(golds)
                ],
                "retrieved_evidence": [
                    {
                        key: row[key]
                        for key in ("rank", "source_id", "relevance", "matched_gold_ids")
                    }
                    for row in rows
                ],
                "ideal_relevances": ideal,
            }
        )
        details.append(
            {
                "question_id": question["question_id"],
                "gold_direct": len(golds),
                "gold_covered": found,
                "first_hit_rank": next(
                    (index for index, grade in enumerate(grades, 1) if grade == 2), None
                ),
                "graded": grades,
                "delivered": len(ledger),
                **{f"ndcg@{size}": ndcg(grades, ideal, size) for size in KS},
            }
        )
    return records, details


def answer_rows(
    questions: list[dict], outputs: dict[str, dict], judgments: dict[str, dict]
) -> list[dict]:
    rows = []
    for question in questions:
        output = outputs.get(question["question_id"], {})
        judgment = judgments.get(question["question_id"])
        if judgment is None:
            raise SystemExit("Missing judgment for " + question["question_id"])
        answer = output.get("answer") or {}
        declared = [str(item) for item in (answer.get("citations") or [])]
        supports = judgment.get("citations") or {}
        citations = [
            {"citation_id": identifier, "supports_answer": bool(supports.get(identifier, False))}
            for identifier in declared
        ]
        rows.append(
            {
                "question_id": question["question_id"],
                "question": question["question"],
                "answer": answer.get("answer", ""),
                "answerable": bool(question["answerable"]),
                "answer_correct": bool(judgment.get("answer_correct")),
                "required_components_complete": bool(judgment.get("required_components_complete")),
                "citations_support_answer": all(item["supports_answer"] for item in citations),
                "scope_leakage": bool(judgment.get("scope_leakage")),
                "correct_abstention": bool(judgment.get("correct_abstention")),
                "citations": citations,
            }
        )
    return rows


def review_markdown(questions: list[dict], outputs: dict[str, dict]) -> str:
    lines = [
        "# 生产回答复核",
        "",
        "逐题对照 `reference_answer` 与 `required_answer_components`，判定：答案正确、要点完整、"
        "每条引用是否支持相邻断言、是否越界、不可答题是否正确拒答。引用编号为模型声明编号。",
        "",
    ]
    for question in questions:
        output = outputs.get(question["question_id"], {})
        answer = output.get("answer") or {}
        declared = "、".join(str(item) for item in (answer.get("citations") or []))
        lines += [
            f"## {question['question_id']} · {'可答' if question['answerable'] else '不可答'}",
            "",
            f"**问题**：{question['question']}",
            "",
            f"**参考答案**：{question.get('reference_answer', '')}",
            "",
            f"**必需要点**：{'；'.join(question.get('required_answer_components') or [])}",
            "",
            f"**模型答案**：{answer.get('answer', '(无)')}",
            "",
            f"**声明引用**：{declared or '无'}",
            "",
            f"**判分结论**：sufficient={output.get('sufficient')} "
            f"covered={(output.get('covered') or [])[:6]}",
            "",
        ]
        cited = {item.get("source_id"): item for item in (output.get("cited_sources") or [])}
        for identifier in answer.get("citations") or []:
            item = cited.get(str(identifier))
            if not item:
                lines.append(f"- `{identifier}`：未在本轮交付证据中找到")
                continue
            text = " ".join(str(item.get("text", "")).split())[:220]
            lines.append(f"- `{identifier}` p{item.get('page')}：{text}")
        if output.get("error_type"):
            lines.append(f"- 运行错误：{output['error_type']} {output.get('error_code')}")
        lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument(
        "--review", type=Path, help="Write the reviewer sheet and stop without judgments."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    summary = json.loads((args.dataset / "dataset_summary.json").read_text(encoding="utf-8-sig"))
    questions = read_jsonl(args.dataset / "rag_questions.jsonl")
    gold = read_jsonl(args.dataset / "structure_gold.jsonl")
    outputs = load_outputs(args.run)
    labels = {
        str(uuid5(NAMESPACE_URL, info["document_id"])): info["document_id"]
        for info in summary["document_breakdown"]
    }

    if args.review:
        args.review.write_text(review_markdown(questions, outputs), encoding="utf-8")
        print(f"wrote {args.review}; fill {args.run / 'judgments.json'} then rerun")

    judgments_path = args.run / "judgments.json"
    if not judgments_path.exists():
        print(f"No judgments yet: write {judgments_path} with per-question verdicts.")
        return
    judgments = json.loads(judgments_path.read_text(encoding="utf-8"))

    report, predictions = heading_report(args.runtime, summary, gold)
    retrieval, details = retrieval_rows(questions, outputs, labels, args.k)
    answers = answer_rows(questions, outputs, judgments)
    scored_input = {
        "run_id": f"production-{args.dataset.name}-{args.run.name}",
        "provenance": {
            "dataset": str(args.dataset),
            "run": str(args.run),
            "runtime": str(args.runtime),
            "window": f"first-round delivered top {args.k}",
            "models": "production pipeline: DeepSeek flash + local BGE-M3/BM25/Cross-Encoder",
            "judgments": (
                "assistant review with the semantic rubric; single reviewer, not double-blind"
            ),
            "gold_assumption": (
                "gold treated as complete by user decision 2026-10-24; Recall@k is published"
            ),
        },
        "top_k": args.k,
        "heading_candidates_complete": True,
        "headings": heading_candidates(report),
        "retrieval": retrieval,
        "answers": answers,
    }
    scored = score(Dataset.model_validate(scored_input))
    manifest = json.loads((args.run / "manifest.json").read_text(encoding="utf-8"))
    scored_input["provenance"].update(
        {
            "git_commit": manifest.get("git_commit", ""),
            "model_calls": str(manifest.get("model_calls", "")),
            "total_tokens": str(manifest.get("total_tokens", "")),
        }
    )
    suffix = f"-{args.k}" if args.k != 10 else ""
    (args.run / f"benchmark{suffix}.json").write_text(
        json.dumps(scored_input, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.run / f"retrieval_details{suffix}.json").write_text(
        json.dumps(
            {
                "k": args.k,
                "questions": details,
                "recall_at_k": scored["retrieval"]["recall_at_k"],
                "hit_at_k": scored["retrieval"]["hit_at_k"],
                "ndcg_at_k": scored["retrieval"]["ndcg_at_k"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (args.run / "heading_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.run / f"metrics{suffix}.json").write_text(
        json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(scored, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
