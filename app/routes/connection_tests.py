from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import AppError
from app.domain.agent import ConnectionInput
from app.domain.papers import utc_now

router = APIRouter()


class DraftTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection: ConnectionInput
    identifier: UUID | None = None


class SavedTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=160)


@router.post("/connection-tests/draft")
def test_draft(request: Request, body: DraftTest) -> dict:
    return request.app.state.connection_tester.draft(body.connection)


@router.post("/connection-tests/save")
def test_and_save(request: Request, body: DraftTest) -> dict:
    if body.identifier:
        request.app.state.connections.get(str(body.identifier))
    report = request.app.state.connection_tester.draft(body.connection)
    connection = request.app.state.connections.save(
        body.connection, str(body.identifier) if body.identifier else None
    )
    return {"connection": connection, "test": report}


@router.post("/connection-tests/saved/{identifier}")
def test_saved(request: Request, identifier: str, body: SavedTest) -> dict:
    if identifier == "tavily":
        web = request.app.state.agent_answers.web
        if not web.configured:
            raise AppError("search_not_configured", "请先配置 Tavily 服务端密钥。", 503)
        results = web.search("Tavily connection test")
        return {"status": "passed", "tested_at": utc_now(), "result_count": len(results)}
    return request.app.state.connection_tester.saved(identifier, body.model)
