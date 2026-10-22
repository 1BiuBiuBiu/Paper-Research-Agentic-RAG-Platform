import asyncio
import json
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.core.errors import AppError
from app.domain.agent_flow import WebEvidence
from app.domain.papers import utc_now


class WebSearch(Protocol):
    configured: bool

    def search(self, query: str) -> list[WebEvidence]: ...


class TavilySearch:
    def __init__(self, key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.key, self.transport = key, transport
        self.configured = bool(key.strip())

    async def _request(self, query: str) -> bytes:
        async with asyncio.timeout(15):
            async with httpx.AsyncClient(
                timeout=15, transport=self.transport, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(
                    "POST",
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={
                        "query": query[:400],
                        "max_results": 3,
                        "search_depth": "basic",
                        "include_answer": False,
                        "include_raw_content": False,
                    },
                ) as response:
                    if response.status_code != 200:
                        raise AppError(
                            "web_search_failed", "联网搜索失败，请检查搜索服务配置。", 502
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 1024 * 1024:
                            raise AppError("web_response_limit", "搜索响应超过大小限制。", 502)
                    return bytes(body)

    def search(self, query: str) -> list[WebEvidence]:
        if not self.configured:
            return []
        try:
            body = asyncio.run(self._request(query))
            if self.key in body.decode("utf-8"):
                raise ValueError("credential echo")
            items = json.loads(body)["results"]
            if not isinstance(items, list):
                raise ValueError("invalid results")
            results: list[WebEvidence] = []
            seen = set()
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                url, text = item.get("url"), item.get("content")
                if not isinstance(url, str) or not isinstance(text, str) or not text.strip():
                    continue
                parsed = urlsplit(url)
                if (
                    parsed.scheme not in {"https", "http"}
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or url in seen
                ):
                    continue
                seen.add(url)
                title = item.get("title")
                results.append(
                    WebEvidence(
                        source_id=f"W{len(results) + 1}",
                        title=title[:300] if isinstance(title, str) else url,
                        url=url[:2048],
                        text=text[:3500],
                        query=query[:400],
                        retrieved_at=utc_now(),
                    )
                )
            return results
        except AppError:
            raise
        except (TimeoutError, httpx.HTTPError, ValueError, KeyError, TypeError):
            raise AppError(
                "web_search_failed", "联网搜索未完成，将明确保留证据不足状态。", 502
            ) from None
