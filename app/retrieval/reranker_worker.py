"""Offline CPU inference; inputs are deleted with the bounded parent temporary directory."""

import json
from pathlib import Path

from app.core.model_worker import commands, respond


def main() -> None:
    import numpy as np
    import torch
    from sentence_transformers import CrossEncoder

    torch.set_num_threads(4)
    model: CrossEncoder | None = None
    identity: tuple[str, str] | None = None
    for command in commands():
        path = Path(command["directory"])
        response = Path(command["response_path"])
        request = json.loads((path / "input.json").read_text(encoding="utf-8"))
        try:
            requested = request["model"], request["revision"]
            if model is None or identity != requested:
                model = CrossEncoder(
                    request["model"],
                    revision=request["revision"],
                    device="cpu",
                    local_files_only=True,
                    trust_remote_code=False,
                    max_length=512,
                )
                identity = requested
            scores = model.predict(request["pairs"], batch_size=4, show_progress_bar=False)
            np.save(
                path / "scores.npy",
                np.asarray(scores, dtype="float32").reshape(-1),
                allow_pickle=False,
            )
            respond(response, {"ok": True})
        except Exception as error:
            respond(response, {"ok": False, "exception_type": type(error).__name__})


if __name__ == "__main__":
    main()
