"""Gold grading and benchmark assembly for the local dataset replay (no models, no IO)."""

import math
import re

NUMBER = re.compile(r"\d+\.\d+|\d{2,}")
WORD = re.compile(r"[A-Za-z]{5,}")
BENCHMARK_WINDOW = 10
KS = (5, 10, 20)


def matches(item, gold: dict, labels: dict[str, str]) -> bool:
    """A reranked chunk covers a gold snippet when page, paper and its numbers/terms agree."""
    if labels.get(item.document_id) != gold["document_id"] or item.page != gold["page"]:
        return False
    text = item.text
    numbers = NUMBER.findall(gold.get("evidence_text", ""))
    if numbers:
        return any(number in text for number in numbers)
    words = WORD.findall(gold.get("evidence_text", ""))
    return bool(words) and all(word.lower() in text.lower() for word in words[:3])


def grade_window(window, golds: list[dict], labels: dict[str, str]) -> tuple[list[dict], int, int]:
    """Grade a reranked window: 2 when it newly covers a direct gold, 1 for same-page background."""
    claimed: set[int] = set()
    rows: list[dict] = []
    ones = 0
    for rank, item in enumerate(window, 1):
        new = [
            i for i, gold in enumerate(golds) if i not in claimed and matches(item, gold, labels)
        ]
        if new:
            claimed.update(new)
            relevance = 2
        elif any(
            item.page == gold["page"] and labels.get(item.document_id) == gold["document_id"]
            for gold in golds
        ):
            relevance = 1
            ones += 1
        else:
            relevance = 0
        rows.append(
            {
                "rank": rank,
                "source_id": f"R{rank}",
                "relevance": relevance,
                "matched_gold_ids": [f"G{i + 1}" for i in new],
                "text": item.text,
                "page": item.page,
            }
        )
    return rows, len(claimed), ones


def dcg(grades: list[int], k: int) -> float:
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades[:k]))


def ndcg(grades: list[int], ideal: list[int], k: int) -> float | None:
    value = dcg(sorted(ideal, reverse=True), k)
    return dcg(grades, k) / value if value else None


def coverage_row(question: dict, pool, window, labels: dict[str, str]) -> dict:
    """One question's gold positions in the fused pool and in the reranked window."""
    golds = [item for item in question["gold_evidence"] if item.get("relevance") == 2]
    pool_ranks = [
        rank
        for rank, item in enumerate(pool, 1)
        if any(matches(item, gold, labels) for gold in golds)
    ]
    rows, found, ones = grade_window(window, golds, labels)
    grades = [row["relevance"] for row in rows]
    first_hit = next((index for index, grade in enumerate(grades, 1) if grade == 2), None)
    ideal = [2] * len(golds) + [1] * ones
    scores: dict[str, bool | float | None] = {
        f"hit@{k}": (first_hit is not None and first_hit <= k) if golds else None for k in KS
    }
    scores.update({f"ndcg@{k}": ndcg(grades, ideal, k) if golds else None for k in KS})
    return {
        "question_id": question["question_id"],
        "question": question["question"],
        "answerable": question["answerable"],
        "gold_direct": len(golds),
        "gold_covered_pool": sum(
            1 for gold in golds if any(matches(item, gold, labels) for item in pool)
        ),
        "pool_rank": min(pool_ranks) if pool_ranks else None,
        "gold_covered_window": found,
        "first_hit_rank": first_hit,
        "graded": grades,
        "ideal": ideal,
        "window": rows,
        **scores,
    }


def retrieval_record(question: dict, golds: list[dict], row: dict) -> dict:
    """Benchmark row for evaluation/metrics.py; gold is not exhaustive, so Hit is what counts."""
    return {
        "question_id": question["question_id"],
        "question": question["question"],
        "evidence_complete": False,
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
            {key: item[key] for key in ("rank", "source_id", "relevance", "matched_gold_ids")}
            for item in row["window"][:BENCHMARK_WINDOW]
        ],
        "ideal_relevances": row["ideal"],
    }


def heading_candidates(report: dict) -> list[dict]:
    """Rows the heading report actually scored, so the benchmark matches the frozen alignment."""
    candidates = []
    for row in report["rows"]:
        if row["category"] == "excluded_pending_review":
            continue
        if row["gold"] is not None:
            gold = row["gold"]
            prediction = row["prediction"]
            candidates.append(
                {
                    "document_id": gold["document_id"],
                    "text": gold["text"],
                    "page": gold["page"],
                    "is_real_section_heading": bool(gold["is_real_section_heading"]),
                    "level": gold["level"] if gold["is_real_section_heading"] else None,
                    "heading_type": gold["heading_type"],
                    "predicted_heading": prediction is not None,
                    "predicted_level": prediction["level"] if prediction else None,
                }
            )
        else:
            prediction = row["prediction"]
            candidates.append(
                {
                    "document_id": prediction["document_id"],
                    "text": prediction["text"],
                    "page": prediction["page"],
                    "is_real_section_heading": False,
                    "level": None,
                    "heading_type": "unmatched_prediction",
                    "predicted_heading": True,
                    "predicted_level": prediction["level"],
                }
            )
    return candidates


def summarise(coverage: list[dict], seconds: float) -> dict:
    """Question-level and gold-level rates; questions without direct gold stay out of averages."""
    answerable = [row for row in coverage if row["answerable"] and row["gold_direct"]]
    direct = sum(row["gold_direct"] for row in answerable)
    return {
        "questions": len(coverage),
        "answerable_with_gold": len(answerable),
        "unanswerable": sum(not row["answerable"] for row in coverage),
        "gold_pool_coverage_rate": sum(row["gold_covered_pool"] for row in answerable) / direct,
        "gold_window_coverage_rate": sum(row["gold_covered_window"] for row in answerable) / direct,
        "questions_all_gold_in_pool": sum(
            row["gold_covered_pool"] == row["gold_direct"] for row in answerable
        ),
        "questions_any_gold_in_pool": sum(bool(row["gold_covered_pool"]) for row in answerable),
        **{
            name: sum(row[name] for row in answerable if row[name] is not None)
            / sum(row[name] is not None for row in answerable)
            for name in (*[f"hit@{k}" for k in KS], *[f"ndcg@{k}" for k in KS])
        },
        "seconds": round(seconds, 1),
    }
