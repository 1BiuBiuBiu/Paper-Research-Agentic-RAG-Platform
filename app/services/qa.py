"""UTF-8 byte clipping shared by evidence and history packing."""


def clip_bytes(text: str, limit: int) -> str:
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
