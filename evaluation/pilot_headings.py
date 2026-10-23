"""Frozen same-document/page heading alignment for the supplied pilot protocol."""

import json
import re
import unicodedata
from difflib import SequenceMatcher

from evaluation.collect_pilot import OUT, load, save

THRESHOLD = 0.90


def normalized(text):
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"^\s*\d+(?:\.\d+)*(?:[.\s]|$)\s*", "", value)
    return re.sub(r"\W+", "", value)


def evaluate(gold, predictions):
    pairs = []
    for i, g in enumerate(gold):
        for j, p in enumerate(predictions):
            if (g["document_id"], g["page"]) != (p["document_id"], p["page"]):
                continue
            similarity = SequenceMatcher(None, normalized(g["text"]), normalized(p["text"])).ratio()
            if similarity >= THRESHOLD:
                pairs.append((similarity, i, j))
    matches, used = {}, set()
    for similarity, i, j in sorted(pairs, reverse=True):
        if i not in matches and j not in used:
            matches[i] = (j, similarity)
            used.add(j)
    counts, rows = {}, []
    for i, g in enumerate(gold):
        count = counts.setdefault(g["document_id"], dict(tp=0, fp=0, fn=0, level_correct=0))
        match = matches.get(i)
        p = predictions[match[0]] if match else None
        if g.get("needs_review"):
            category = "excluded_pending_review"
        elif g["is_real_section_heading"]:
            category = "tp" if p else "fn"
            count[category] += 1
            if p and p["level"] == g["level"]:
                count["level_correct"] += 1
        else:
            category = "fp" if p else "tn"
            if p:
                count["fp"] += 1
        rows.append(
            dict(gold=g, prediction=p, category=category, similarity=match[1] if match else None)
        )
    for j, p in enumerate(predictions):
        if j not in used:
            counts[p["document_id"]]["fp"] += 1
            rows.append(dict(gold=None, prediction=p, category="fp_unmatched"))
    for c in counts.values():
        c["f1"] = 2 * c["tp"] / (2 * c["tp"] + c["fp"] + c["fn"])
    tp = sum(c["tp"] for c in counts.values())
    fp = sum(c["fp"] for c in counts.values())
    fn = sum(c["fn"] for c in counts.values())
    correct = sum(c["level_correct"] for c in counts.values())
    return dict(
        threshold=THRESHOLD,
        per_document=counts,
        macro_f1=sum(c["f1"] for c in counts.values()) / len(counts),
        micro_f1=2 * tp / (2 * tp + fp + fn),
        level_accuracy=correct / tp if tp else None,
        tp=tp,
        fp=fp,
        fn=fn,
        level_correct=correct,
        rows=rows,
    )


def main():
    predictions = json.loads((OUT / "predicted_headings.json").read_text(encoding="utf-8"))
    result = evaluate(load("structure_gold.jsonl"), predictions)
    save("heading_report.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
