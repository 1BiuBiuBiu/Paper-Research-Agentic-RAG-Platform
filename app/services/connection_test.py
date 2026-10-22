"""Explicit bounded probes using fixed public text; never use papers or chat history."""

import threading
import time
from dataclasses import replace

from app.core.errors import AppError
from app.domain.agent import ConnectionInput
from app.domain.papers import utc_now
from app.providers.deepseek import CompletionProvider, DeepSeekProvider
from app.providers.registry import CompatibleProvider, ModelRegistry

PROBE = [
    {
        "role": "system",
        "content": 'Return only JSON: {"answer":"OK","citations":[],"insufficient_evidence":false}',
    },
    {"role": "user", "content": "Connection test. Return OK using the required JSON format."},
]


class ConnectionTester:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry
        self.lock = threading.Lock()

    def draft(self, body: ConnectionInput) -> dict:
        provider = CompatibleProvider(
            replace(self.registry.settings, max_output_tokens=96, model_timeout=20),
            body.model_dump(),
            self.registry.transport,
        )
        return self.run(provider, body.models)

    def saved(self, identifier: str, model: str) -> dict:
        provider = self.registry.resolve(identifier, model)
        if isinstance(provider, DeepSeekProvider):
            if identifier == "deepseek":
                provider = DeepSeekProvider(
                    replace(provider.settings, max_output_tokens=96, model_timeout=20),
                    self.registry.transport or provider.transport,
                )
            else:
                provider = CompatibleProvider(
                    replace(self.registry.settings, max_output_tokens=96, model_timeout=20),
                    self.registry.connections.get(identifier),
                    self.registry.transport,
                )
        return self.run(provider, [model])

    def run(self, provider: CompletionProvider, models: list[str]) -> dict:
        if not provider.configured:
            raise AppError(
                "model_not_configured", "请先配置此服务的服务端密钥，再执行连接测试。", 503
            )
        if not self.lock.acquire(blocking=False):
            raise AppError("connection_test_busy", "另一个连接测试正在运行，请稍后再试。", 409)
        started = time.monotonic()
        try:
            results = []
            for model in models:
                result = provider.complete(model, [dict(message) for message in PROBE])
                if (
                    result.result.answer.strip() != "OK"
                    or result.result.citations
                    or result.result.insufficient_evidence
                ):
                    raise AppError(
                        "connection_probe_failed", "接口响应未通过应用所需的 JSON 格式测试。", 502
                    )
                results.append(
                    {
                        "requested_model": model,
                        "served_model": result.served_model,
                        "usage": result.usage.model_dump(),
                    }
                )
            return {
                "status": "passed",
                "tested_at": utc_now(),
                "models": results,
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        finally:
            self.lock.release()
