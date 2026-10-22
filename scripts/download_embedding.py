"""Download the fixed public BGE-M3 snapshot without loading model weights."""

import os
import sys


def main() -> None:
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import snapshot_download

    from app.core.config import Settings

    settings = Settings.from_env()
    patterns = [
        "config.json",
        "config_sentence_transformers.json",
        "modules.json",
        "sentence_bert_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "sentencepiece.bpe.model",
        "1_Pooling/config.json",
    ]
    direct = "--direct-https" in sys.argv
    directory = snapshot_download(
        settings.embedding_model,
        revision=settings.embedding_revision,
        allow_patterns=patterns if direct else patterns + ["pytorch_model.bin"],
        max_workers=2,
    )
    if direct:
        from pathlib import Path

        from scripts.model_download import direct_weights

        direct_weights(settings.embedding_model, settings.embedding_revision, Path(directory))
    print("BGE-M3 fixed snapshot downloaded; no user documents transmitted.")


if __name__ == "__main__":
    main()
