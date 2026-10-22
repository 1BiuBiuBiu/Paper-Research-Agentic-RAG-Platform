import os
from typing import Any
from urllib.parse import urlsplit

import httpx

API_BASE = os.getenv("API_BASE", "http://127.0.0.1:8000").rstrip("/")


class ClientError(Exception):
    pass


class ConnectionFailure(ClientError):
    """Local transport failed or returned a non-API response; no write is retried."""


def trust_environment() -> bool:
    # Windows can supply system proxies even when HTTP_PROXY is absent.
    return urlsplit(API_BASE).hostname not in {"127.0.0.1", "localhost", "::1"}


def api(method: str, path: str, **kwargs: Any) -> Any:
    try:
        timeout = kwargs.pop("timeout", 360)
        response = httpx.request(
            method, API_BASE + path, timeout=timeout, trust_env=trust_environment(), **kwargs
        )
        if response.is_error:
            body = response.json()
            raise ClientError(body.get("message", "请求未通过验证，请检查输入。"))
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise ConnectionFailure("无法连接本机 API 或响应无效，请等待服务就绪后重新连接。") from None


def image_bytes(document_id: str, asset: str) -> bytes:
    try:
        response = httpx.get(
            f"{API_BASE}/documents/{document_id}/assets/{asset}",
            timeout=30,
            trust_env=trust_environment(),
        )
        response.raise_for_status()
        return response.content
    except httpx.HTTPError:
        raise ClientError("原页或图片暂时无法读取，请检查数据文件。") from None
