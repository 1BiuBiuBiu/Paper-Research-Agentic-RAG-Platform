from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.agent_flow import AgentDiagnostics
from app.domain.qa import Evidence, TokenUsage


class AgentScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["documents", "folder", "mixed", "all", "chat"] = "documents"
    document_ids: list[UUID] = Field(default_factory=list, max_length=100)
    excluded_document_ids: list[UUID] = Field(default_factory=list, max_length=1000)
    folder_ids: list[UUID] = Field(default_factory=list, max_length=100)
    folder_id: UUID | None = None
    uncategorized: bool = False
    include_samples: bool = False

    @model_validator(mode="after")
    def explicit_scope(self) -> "AgentScope":
        if self.kind == "all":
            if self.document_ids or self.folder_ids or self.folder_id or self.uncategorized:
                raise ValueError("全部论文不能附带其他范围")
            return self
        if self.kind == "mixed":
            if self.folder_id or not (self.document_ids or self.folder_ids or self.uncategorized):
                raise ValueError("请明确选择论文或文件夹")
            return self
        if self.folder_ids or self.excluded_document_ids:
            raise ValueError("多文件夹请使用组合范围")
        if self.kind == "documents":
            if not self.document_ids or self.folder_id or self.uncategorized:
                raise ValueError("请明确选择文献")
        elif self.document_ids or (self.kind == "chat" and (self.folder_id or self.uncategorized)):
            raise ValueError("范围条件冲突")
        elif self.kind == "folder" and bool(self.folder_id) == self.uncategorized:
            raise ValueError("请选择一个文件夹或未分类")
        return self


class AgentTurnInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    expected_revision: int = Field(ge=0)
    question: str = Field(min_length=1, max_length=4000)
    scope: AgentScope
    connection_id: str = Field(default="deepseek", min_length=1, max_length=64)
    model: str = Field(default="flash", min_length=1, max_length=160)
    allow_web: bool = True

    @field_validator("question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    base_url: str = Field(max_length=2048)
    api_key_env: str | None = Field(default=None, pattern=r"^RESEARCH_MODEL_KEY_[A-Z0-9_]{1,64}$")
    models: list[str] = Field(min_length=1, max_length=20)
    requires_key: bool = True
    json_mode: bool = True
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value: str) -> str:
        if any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
            raise ValueError("服务地址不能包含空白或控制字符")
        try:
            url = urlsplit(value)
            host = url.hostname
            _ = url.port
        except ValueError as error:
            raise ValueError("服务地址无效") from error
        local = host in {"127.0.0.1", "localhost", "::1"}
        if (url.scheme != "https" and not (url.scheme == "http" and local)) or not host:
            raise ValueError("外部接口须 HTTPS，本机接口可 HTTP")
        if url.username is not None or url.password is not None or url.query or url.fragment:
            raise ValueError("服务地址不能包含凭据、查询或片段")
        return url.geturl().rstrip("/")

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("连接名称无效")
        return value

    @field_validator("models")
    @classmethod
    def model_names(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 160 or any(ord(c) < 32 for c in v) for v in values):
            raise ValueError("模型名称无效")
        return list(dict.fromkeys(v.strip() for v in values))

    @model_validator(mode="after")
    def key_reference(self) -> "ConnectionInput":
        if self.requires_key and not self.api_key_env:
            raise ValueError("请填写服务端密钥环境变量名")
        return self


class AgentResult(AgentDiagnostics):
    answer: str
    sources: list[Evidence]
    resolved_document_ids: list[str]
    scope_documents: list[dict[str, str]] = Field(default_factory=list)
    context_document_ids: list[str]
    scope_signature: str
    model: str
    served_model: str | None = None
    connection_id: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    insufficient_evidence: bool
    latency_ms: int
    context_bytes: int
    history_turns: int = 0
    history_reset: bool = False
    used_web: bool = False


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="新对话", min_length=1, max_length=120)
    project_id: UUID | None = None

    @field_validator("title")
    @classmethod
    def title_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("对话名称无效")
        return value
