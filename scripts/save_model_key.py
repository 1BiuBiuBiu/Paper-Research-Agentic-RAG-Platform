"""Native dialog helper: accept a key over stdin, never argv or HTTP."""

import argparse
import base64
import re
import sys
from pathlib import Path

from dotenv import set_key


def save_key(path: Path, variable: str, encoded: str) -> None:
    if not re.fullmatch(
        r"DEEPSEEK_API_KEY|TAVILY_API_KEY|RESEARCH_MODEL_KEY_[A-Z0-9_]{1,80}", variable
    ):
        raise ValueError("Invalid credential variable")
    secret = base64.b64decode(encoded, validate=True).decode("utf-8").strip()
    if not secret or len(secret) > 4096 or any(ord(c) < 32 for c in secret):
        raise ValueError("Invalid credential value")
    set_key(path, variable, secret, quote_mode="always", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variable", required=True)
    args = parser.parse_args()
    try:
        save_key(
            Path(__file__).resolve().parents[1] / ".env",
            args.variable,
            sys.stdin.readline(20000).strip(),
        )
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
