"""Download the official TableFormer snapshot; no paper content is transmitted."""

import os
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import snapshot_download  # noqa: E402

from scripts.model_download import direct_weights  # noqa: E402

REVISION = "fc0f2d45e2218ea24bce5045f58a389aed16dc23"


def main() -> None:
    model = "docling-project/docling-models"
    directory = Path(snapshot_download(model, revision=REVISION, allow_patterns=["*.json"]))
    for mode in ("accurate", "fast"):
        direct_weights(
            model,
            REVISION,
            directory,
            f"model_artifacts/tableformer/{mode}/tableformer_{mode}.safetensors",
        )
    print("Official TableFormer weights downloaded and SHA256 verified")


if __name__ == "__main__":
    main()
