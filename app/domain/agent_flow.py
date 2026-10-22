from pydantic import BaseModel, Field


class WebEvidence(BaseModel):
    source_id: str
    title: str
    url: str
    text: str
    query: str
    retrieved_at: str
    content_kind: str = "search_snippet"


class AgentStep(BaseModel):
    node: str
    attempt: int = 0
    query: str = ""
    count: int = 0
    reason: str = ""
    elapsed_ms: int = 0


class AgentDiagnostics(BaseModel):
    rewrite_count: int = 0
    model_calls: int = 0
    web_status: str = "not_needed"
    web_sources: list[WebEvidence] = Field(default_factory=list)
    trace: list[AgentStep] = Field(default_factory=list)
