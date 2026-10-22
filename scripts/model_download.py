"""HTTPS fallback for stalled large public model transfers; verify the official LFS digest."""

import hashlib
import shutil
import subprocess
from pathlib import Path

from huggingface_hub import get_hf_file_metadata, hf_hub_url


def direct_weights(
    model: str, revision: str, directory: Path, filename: str = "pytorch_model.bin"
) -> None:
    url = hf_hub_url(model, filename, revision=revision)
    metadata = get_hf_file_metadata(url)
    expected, size = metadata.etag, metadata.size
    if not expected or len(expected) != 64 or size is None:
        raise RuntimeError(
            "The official weight size/digest is unavailable; refusing unverified data"
        )
    target = directory / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        with target.open("rb") as existing:
            if hashlib.file_digest(existing, "sha256").hexdigest() == expected:
                return
        raise RuntimeError(
            "An existing model file has the wrong digest; preserve it and inspect first"
        )
    partial = target.with_name(target.stem + ".direct-download.part")
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("HTTPS fallback requires the system curl command")
    result = subprocess.run(
        [
            curl,
            "--fail",
            "--location",
            "--retry",
            "1",
            "--retry-delay",
            "2",
            "--retry-max-time",
            "1800",
            "--connect-timeout",
            "15",
            "--max-time",
            "1800",
            "--continue-at",
            "-",
            "--output",
            str(partial),
            url,
        ],
        check=False,
    )
    if result.returncode:
        raise RuntimeError("HTTPS download failed; partial data retained for a later resume")
    total = partial.stat().st_size
    with partial.open("rb") as downloaded:
        digest = hashlib.file_digest(downloaded, "sha256").hexdigest()
    if total != size or digest != expected:
        raise RuntimeError("Model size or SHA256 mismatch; not activating the downloaded model")
    partial.replace(target)
