"""Aggregate separately reviewed pilot labels without treating LLM grades as gold."""

import hashlib
import json

from evaluation.collect_pilot import OUT, load, save
from evaluation.metrics import dcg


def main():
    answers = json.loads((OUT / "agent_outputs.json").read_text(encoding="utf-8"))
    judgments = json.loads((OUT / "judgments.json").read_text(encoding="utf-8"))
    headings = json.loads((OUT / "heading_report.json").read_text(encoding="utf-8"))
    gold = {q["question_id"]: q for q in load("rag_questions.jsonl")}
    reviewed = {r["question_id"]: r for r in judgments["questions"]}
    if set(reviewed) != set(gold) or {a["question_id"] for a in answers} != set(gold):
        raise ValueError("Missing or duplicate question review")
    rows, citation_total, supported = [], 0, 0
    for a in answers:
        j = reviewed[a["question_id"]]
        if len(j["final_grades"]) != len(a["final_top5"]):
            raise ValueError("Review does not cover every final result")
        if len(j["citation_support"]) != len(a["answer"]["citations"]):
            raise ValueError("Review does not cover every citation")
        for grades in [j["final_grades"], j["ideal_pool_grades"]]:
            if any(type(g) is not int or g not in (0, 1, 2) for g in grades):
                raise ValueError("Invalid relevance label")
        answerable = gold[a["question_id"]]["answerable"]
        ideal = dcg(sorted(j["ideal_pool_grades"], reverse=True))
        success = (
            j["answer_correct"]
            and j["required_components_complete"]
            and all(j["citation_support"])
            and not j["scope_leakage"]
            and (bool(j["citation_support"]) if answerable else j["correct_abstention"])
        )
        citation_total += len(j["citation_support"])
        supported += sum(j["citation_support"])
        rows.append(
            dict(
                question_id=a["question_id"],
                answerable=answerable,
                hit_at_5=bool(2 in j["final_grades"]) if answerable else None,
                ndcg_at_5=dcg(j["final_grades"]) / ideal if answerable and ideal else None,
                grounded_success=success,
                reason=j["reason"],
            )
        )
    available = [r for r in rows if r["answerable"]]
    summary = dict(
        status="pilot_codex_review_pending_human_approval",
        heading_macro_f1=headings["macro_f1"],
        heading_micro_f1=headings["micro_f1"],
        level_accuracy=headings["level_accuracy"],
        recall_at_5=None,
        hit_at_5=sum(r["hit_at_5"] for r in available) / len(available),
        provisional_pool_ndcg_at_5=sum(r["ndcg_at_5"] for r in available) / len(available),
        grounded_answer_success_rate=sum(r["grounded_success"] for r in rows) / len(rows),
        citation_precision=supported / citation_total if citation_total else None,
        citations_supported=supported,
        citations_total=citation_total,
        scope_leakage_rate=sum(r["scope_leakage"] for r in reviewed.values()) / len(rows),
        questions=len(rows),
        answerable_questions=len(available),
        model_calls=sum(a["model_calls"] for a in answers),
        total_tokens=sum(a["usage"]["total_tokens"] or 0 for a in answers),
        rows=rows,
        evidence_hashes={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in OUT.glob("*.json")
            if p.name != "summary_report.json"
        },
    )
    save("summary_report.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
