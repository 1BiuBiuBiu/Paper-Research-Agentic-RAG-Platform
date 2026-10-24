"""Run local BGE encoding in a resource-bounded subprocess, without remote services."""

import json
from pathlib import Path

from app.core.model_worker import commands, respond


def main() -> None:
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(4)
    model: SentenceTransformer | None = None
    identity: tuple[str, str] | None = None
    for command in commands():
        directory = Path(command["directory"])
        response = Path(command["response_path"])
        request = json.loads((directory / "input.json").read_text(encoding="utf-8"))
        try:
            requested = request["model"], request["revision"]
            if model is None or identity != requested:
                model = SentenceTransformer(
                    request["model"],
                    revision=request["revision"],
                    device="cpu",
                    local_files_only=True,
                    trust_remote_code=False,
                )
                model.max_seq_length = 1024
                identity = requested
            vectors = model.encode(
                request["texts"],
                batch_size=4,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            np.save(directory / "vectors.npy", vectors.astype("float32"), allow_pickle=False)
            respond(response, {"ok": True})
        except Exception as error:
            detail = {"exception_type": type(error).__name__}
            (directory / "error.json").write_text(json.dumps(detail), encoding="utf-8")
            respond(response, {"ok": False, **detail})


if __name__ == "__main__":
    main()
