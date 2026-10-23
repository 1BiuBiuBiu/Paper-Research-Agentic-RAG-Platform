"""Offline evaluation CLI. No model calls or network access."""

import argparse
import hashlib
import json
from pathlib import Path

from evaluation.metrics import Dataset, score


def run_evaluation(source: Path, destination: Path) -> dict:
    if source.resolve() == destination.resolve():
        raise ValueError("output must not overwrite dataset")
    raw = source.read_bytes()
    data = Dataset.model_validate_json(raw)
    report = {
        "schema_version": 1,
        "dataset_sha256": hashlib.sha256(raw).hexdigest(),
        "metrics": score(data),
        "evaluated_records": data.model_dump(),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("output/evaluation/report.json"))
    args = parser.parse_args()
    try:
        report = run_evaluation(args.dataset, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
