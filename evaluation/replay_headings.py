"""Replay heading rules on retained real parses, without re-running PDF/LLM models."""

import argparse
import hashlib
import json
from pathlib import Path

from app.domain.papers import ParsedPaper
from app.parsing.headings import heading_depth, normalize_headings
from evaluation.pilot_headings import evaluate


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/datasets"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # New directory only: never replace an earlier evaluation or its judgments.
    args.output.mkdir(parents=True, exist_ok=False)
    summary_path = args.dataset / "dataset_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
    gold_path = args.dataset / "structure_gold.jsonl"
    paths = [summary_path, gold_path]
    predictions = []
    for info in summary["document_breakdown"]:
        pdf = args.dataset / info["filename"]
        if digest(pdf) != info["pdf_sha256"]:
            raise ValueError("PDF fingerprint mismatch: " + info["filename"])
        saved = args.source / pdf.stem / "result.json"
        paths.extend([pdf, saved])
        parsed = ParsedPaper.model_validate_json(saved.read_bytes())
        normalize_headings(parsed.blocks)
        for block in parsed.blocks:
            level = heading_depth(block.model_dump())
            if level is not None:
                predictions.append(
                    dict(
                        document_id=info["document_id"],
                        block_id=block.id,
                        page=block.page,
                        text=block.text,
                        level=level,
                    )
                )
    gold = [
        json.loads(line)
        for line in gold_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    report = evaluate(gold, predictions)
    manifest = dict(
        kind="heading_rules_replay",
        fresh_pdf_parse=False,
        model_calls=0,
        inputs={str(p): digest(p) for p in paths},
    )
    for name, value in [
        ("predicted_headings", predictions),
        ("heading_report", report),
        ("manifest", manifest),
    ]:
        (args.output / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
