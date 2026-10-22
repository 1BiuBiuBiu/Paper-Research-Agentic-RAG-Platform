import asyncio
import json
from typing import Protocol

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.qa import MODEL_IDS, Completion, ModelAnswer, TokenUsage


class CompletionProvider(Protocol):
    configured: bool

    def complete(self, model: str, messages: list[dict[str, str]]) -> Completion: ...


class DeepSeekProvider:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        allow_local_http: bool = False,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self.configured = bool(settings.deepseek_api_key.strip())
        self.live_verified = False
        url = httpx.URL(settings.deepseek_base_url)
        local_http = (
            allow_local_http
            and url.scheme == "http"
            and url.host in {"localhost", "127.0.0.1", "::1"}
        )
        if (
            (url.scheme != "https" and not local_http)
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "DEEPSEEK_BASE_URL must be HTTPS without credentials, query or fragment"
            )
        self.endpoint = str(url).rstrip("/") + "/chat/completions"

    def _payload(self, model: str, messages: list[dict[str, str]]) -> dict:
        return {
            "model": MODEL_IDS[model],
            "messages": messages,
            "stream": False,
            "thinking": {"type": "disabled"},
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
            "max_tokens": self.settings.max_output_tokens,
            "temperature": 0.1,
        }

    def complete(self, model: str, messages: list[dict[str, str]]) -> Completion:
        if not self.configured:
            raise AppError(
                "model_not_configured", "请先在服务端配置 DEEPSEEK_API_KEY 并重启 API。", 503
            )
        payload = self._payload(model, messages)
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 65536:
            raise AppError("context_limit", "问题与证据超过单次请求预算，请缩短问题。", 413)
        try:
            self.live_verified = False
            result = self._decode(asyncio.run(self._request(encoded)))
            self.live_verified = True
            return result
        except (TimeoutError, httpx.TimeoutException):
            raise AppError("model_timeout", "DeepSeek 响应超时，请稍后重试。", 504) from None
        except httpx.HTTPError:
            raise AppError(
                "model_unreachable", "无法连接 DeepSeek，请检查网络与服务地址。", 502
            ) from None

    async def _request(self, encoded: bytes) -> bytes:
        # A total deadline also covers DeepSeek's non-streaming keepalive bytes.
        async with asyncio.timeout(self.settings.model_timeout):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.model_timeout, connect=10),
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(
                    "POST",
                    self.endpoint,
                    content=encoded,
                    headers={
                        **(
                            {"Authorization": f"Bearer {self.settings.deepseek_api_key}"}
                            if self.settings.deepseek_api_key
                            else {}
                        ),
                        "Content-Type": "application/json",
                    },
                ) as response:
                    self._check_status(response.status_code)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise AppError("model_response_limit", "模型响应超过大小限制。", 502)
            return bytes(body)

    @staticmethod
    def _check_status(status: int) -> None:
        errors = {
            401: ("model_auth_failed", "DeepSeek API Key 无效，请检查服务端配置。", 502),
            402: ("model_balance", "DeepSeek 账户余额不足。", 502),
            403: ("model_forbidden", "DeepSeek 拒绝访问，请检查账户权限。", 502),
            429: ("model_rate_limited", "DeepSeek 请求限流，请稍后重试。", 429),
        }
        if status in errors:
            raise AppError(*errors[status])
        if status < 200 or status >= 300:
            raise AppError(
                "model_request_failed", "DeepSeek 调用失败，请检查模型配置或稍后重试。", 502
            )

    def _decode(self, content: bytes) -> Completion:
        try:
            data = json.loads(content)
            choice = data["choices"][0]
            if choice["finish_reason"] != "stop":
                raise AppError("model_truncated", "回答未完整生成，请缩小问题后重试。", 502)
            answer = ModelAnswer.model_validate_json(choice["message"]["content"])
            served = data["model"]
            if not isinstance(served, str) or not served or len(served) > 200:
                raise ValueError("Missing model identifier")
            if self.settings.deepseek_api_key and (
                self.settings.deepseek_api_key in answer.answer
                or self.settings.deepseek_api_key in served
            ):
                raise ValueError("Credential echo")
            usage_data = data.get("usage") or {}
            if not isinstance(usage_data, dict):
                usage_data = {}
            usage = TokenUsage(
                **{
                    field: usage_data[field]
                    if type(usage_data.get(field)) is int and usage_data[field] >= 0
                    else None
                    for field in ("prompt_tokens", "completion_tokens", "total_tokens")
                }
            )
            return Completion(result=answer, served_model=served, usage=usage)
        except AppError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, ValidationError):
            raise AppError(
                "invalid_model_response", "模型未返回有效的结构化回答，请重试。", 502
            ) from None
