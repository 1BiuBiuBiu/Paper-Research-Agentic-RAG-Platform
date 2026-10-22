"""Download a pinned, public upstream Cross-Encoder; never send document content."""

import os


def main() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import snapshot_download

    from app.core.config import Settings

    settings = Settings.from_env()
    snapshot_download(
        settings.reranker_model,
        revision=settings.reranker_revision,
        allow_patterns=["*.json", "vocab.txt", "model.safetensors"],
        max_workers=2,
    )
    print("Pinned reranker downloaded; no documents transmitted.")


if __name__ == "__main__":
    main()
