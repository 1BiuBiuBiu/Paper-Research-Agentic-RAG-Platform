"""Offline CPU inference; inputs are deleted with the bounded parent temporary directory."""

import json
import sys
from pathlib import Path


def main() -> None:
    import numpy as np
    import torch
    from sentence_transformers import CrossEncoder

    path = Path(sys.argv[1])
    request = json.loads((path / "input.json").read_text(encoding="utf-8"))
    try:
        torch.set_num_threads(4)
        model = CrossEncoder(
            request["model"],
            revision=request["revision"],
            device="cpu",
            local_files_only=True,
            trust_remote_code=False,
            max_length=512,
        )
        scores = model.predict(request["pairs"], batch_size=4, show_progress_bar=False)
        np.save(
            path / "scores.npy", np.asarray(scores, dtype="float32").reshape(-1), allow_pickle=False
        )
    except Exception:
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
