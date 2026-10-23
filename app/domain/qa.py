from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.agent_flow import AgentDiagnostics
from app.domain.papers import Box

ModelChoice = Literal["flash", "pro"]
MODEL_IDS: dict[str, str] = {"flash": "deepseek-flash", "pro": "deepseek-v4-pro"}


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["documents"] = "documents"
    ids: list[UUID] = Field(min_length=1, max_length=1)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    scope: Scope
    model: ModelChoice = "flash"
    allow_web: bool = False  # Legacy API remains offline unless explicitly requested.

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class Chunk(BaseModel):
    id: str
    page: int
    text: str
    block_ids: list[str]
    boxes: list[Box]
    element_type: Literal["text", "table"] = "text"
    element_id: str | None = None


class IndexManifest(BaseModel):
    document_id: str
    generation: str
    fingerprint: str
    model: str
    revision: str
    dimension: int
    file_sha256: str = ""
    bm25_sha256: str = ""
    created_at: str
    chunks: list[Chunk]
    abstract_count: int = 0


class IndexStatus(BaseModel):
    document_id: str
    status: Literal["missing", "stale", "ready", "building", "failed"]
    chunks: int = 0
    model: str = "BAAI/bge-m3"
    error_code: str | None = None
    message: str | None = None


class Evidence(Chunk):
    source_id: str
    document_id: str
    title: str
    filename: str
    score: float
    bm25_score: float = 0
    fusion_score: float = 0
    rerank_score: float | None = None


class TokenUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class ModelAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer: str = Field(min_length=1, max_length=20000)
    citations: list[str] = Field(max_length=20)
    insufficient_evidence: bool
    # Grading stages report whether this round's documents answer the question completely.
    # Generation leaves these unset; they never become part of the answer text.
    sufficient: bool | None = None
    covered: list[str] = Field(default_factory=list, max_length=12)
    missing: list[str] = Field(default_factory=list, max_length=12)
    next_query: str = Field(default="", max_length=4000)


class Completion(BaseModel):
    result: ModelAnswer
    served_model: str
    usage: TokenUsage


class QueryResponse(AgentDiagnostics):
    answer: str
    sources: list[Evidence]
    scope: Scope
    model: ModelChoice
    requested_model: str
    served_model: str | None = None
    usage: TokenUsage = Field(default_factory=TokenUsage)
    insufficient_evidence: bool
    used_web: bool = False
    rewrite_count: int = 0
    latency_ms: int
    context_bytes: int
    overview_used: bool
