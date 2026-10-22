from dataclasses import replace

import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.qa import MODEL_IDS, Completion
from app.providers.deepseek import CompletionProvider, DeepSeekProvider
from app.repositories.connections import ConnectionRepository, credential


class CompatibleProvider(DeepSeekProvider):
    def __init__(
        self,
        settings: Settings,
        connection: dict,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        secret = credential(connection["api_key_env"])
        super().__init__(
            replace(settings, deepseek_api_key=secret, deepseek_base_url=connection["base_url"]),
            transport,
            allow_local_http=True,
        )
        self.configured = not connection["requires_key"] or bool(secret)
        self.json_mode = connection["json_mode"]

    def _payload(self, model: str, messages: list[dict[str, str]]) -> dict:
        result = {
            "model": model,
            "messages": messages,
            "stream": False,
            "max_tokens": self.settings.max_output_tokens,
            "temperature": 0.1,
        }
        if self.json_mode:
            result["response_format"] = {"type": "json_object"}
        return result

    def complete(self, model: str, messages: list[dict[str, str]]) -> Completion:
        try:
            return super().complete(model, messages)
        except AppError as error:
            raise AppError(
                error.code,
                error.message.replace("DeepSeek", "模型服务").replace(
                    "DEEPSEEK_API_KEY", "对应密钥变量"
                ),
                error.status,
            ) from None


class ModelRegistry:
    def __init__(
        self, settings: Settings, connections: ConnectionRepository, builtin: CompletionProvider
    ) -> None:
        self.settings, self.connections, self.builtin = settings, connections, builtin
        self.transport: httpx.AsyncBaseTransport | None = None

    def list(self) -> list[dict]:
        return [
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "models": list(MODEL_IDS),
                "configured": self.builtin.configured,
                "enabled": True,
                "api_key_env": "DEEPSEEK_API_KEY",
                "base_url": self.settings.deepseek_base_url,
                "requires_key": True,
                "json_mode": True,
            },
            *self.connections.list(),
        ]

    def resolve(self, identifier: str, model: str) -> CompletionProvider:
        if identifier == "deepseek":
            if model not in MODEL_IDS:
                raise AppError("model_not_allowed", "请从此服务配置的模型中选择。", 422)
            return self.builtin
        connection = self.connections.get(identifier)
        if not connection["enabled"]:
            raise AppError("connection_disabled", "模型服务已停用。", 409)
        if model not in connection["models"]:
            raise AppError("model_not_allowed", "请从此服务配置的模型中选择。", 422)
        return CompatibleProvider(self.settings, connection, self.transport)

    def catalog(self) -> dict:
        items = []
        for connection in self.list():
            for model in connection["models"]:
                actual = MODEL_IDS.get(model, model) if connection["id"] == "deepseek" else model
                items.append(
                    {
                        "id": connection["id"] + "::" + model,
                        "connection_id": connection["id"],
                        "model": model,
                        "model_id": actual,
                        "provider_name": connection["name"],
                        "label": actual + " · " + connection["name"],
                        "enabled": connection["enabled"],
                        "configured": connection["configured"],
                    }
                )
        return {"items": items, "default": self.connections.default_model()}

    def set_default(self, identifier: str, model: str) -> dict:
        self.resolve(identifier, model)
        selection = {"connection_id": identifier, "model": model}
        self.connections.set_default_model(selection)
        return selection
