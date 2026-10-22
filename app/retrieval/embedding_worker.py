"""Run local BGE encoding in a resource-bounded subprocess, without remote services."""

import json
import sys
from pathlib import Path


def main() -> None:
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    directory = Path(sys.argv[1])
    request = json.loads((directory / "input.json").read_text(encoding="utf-8"))
    try:
        torch.set_num_threads(4)
        model = SentenceTransformer(
            request["model"],
            revision=request["revision"],
            device="cpu",
            local_files_only=True,
            trust_remote_code=False,
        )
        model.max_seq_length = 1024
        vectors = model.encode(
            request["texts"],
            batch_size=4,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        np.save(directory / "vectors.npy", vectors.astype("float32"), allow_pickle=False)
    except Exception as error:
        (directory / "error.json").write_text(
            json.dumps({"exception_type": type(error).__name__}), encoding="utf-8"
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
