from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.core.errors import AppError
from app.domain.agent import AgentScope, AgentTurnInput, ConnectionInput, ConversationInput
from app.domain.agent_flow import AgentStep
from app.domain.projects import ConversationUpdate
from app.services.key_dialog import open_key_dialog
from app.services.progress import STEP_LABELS

router = APIRouter()


@router.get("/connections")
def connections(request: Request) -> dict:
    return {"items": request.app.state.registry.list()}


@router.post("/connections/{identifier}/key-dialog")
def connection_key_dialog(request: Request, identifier: str) -> dict:
    if identifier == "tavily":
        return open_key_dialog("TAVILY_API_KEY")
    connection = next((c for c in request.app.state.registry.list() if c["id"] == identifier), None)
    if not connection:
        raise AppError("connection_not_found", "模型服务不存在。", 404)
    if not connection["requires_key"] or not connection["api_key_env"]:
        raise AppError("key_not_required", "此服务无需配置密钥。", 409)
    return open_key_dialog(connection["api_key_env"])


@router.post("/connections", status_code=201)
def create_connection(request: Request, body: ConnectionInput) -> dict:
    return request.app.state.connections.save(body)


@router.put("/connections/{identifier}")
def update_connection(request: Request, identifier: UUID, body: ConnectionInput) -> dict:
    return request.app.state.connections.save(body, str(identifier))


@router.get("/conversations")
def list_conversations(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    project_id: UUID | None = None,
    unassigned: bool = False,
) -> dict:
    if project_id is not None and unassigned:
        raise AppError("project_filter_conflict", "项目与未分组条件不能同时选择。", 422)
    return {
        "items": request.app.state.conversations.repository.list(
            offset, limit, project_id=str(project_id) if project_id else None, unassigned=unassigned
        )
    }


@router.post("/conversations", status_code=201)
def create_conversation(request: Request, body: ConversationInput) -> dict:
    return request.app.state.conversations.repository.create(
        body.title, str(body.project_id) if body.project_id else None
    )


@router.get("/conversations/{identifier}")
def get_conversation(request: Request, identifier: UUID) -> dict:
    return request.app.state.conversations.repository.get(str(identifier))


@router.patch("/conversations/{identifier}")
def rename_conversation(request: Request, identifier: UUID, body: ConversationUpdate) -> dict:
    return request.app.state.conversations.repository.update(
        str(identifier), body.model_dump(exclude_unset=True, mode="json")
    )


@router.post("/conversations/{identifier}/turns")
def conversation_turn(request: Request, identifier: UUID, body: AgentTurnInput) -> dict:
    progress = request.app.state.progress
    request_id = str(body.request_id)
    progress.start(request_id)

    def on_step(step: AgentStep) -> None:
        progress.publish(
            request_id,
            {
                "node": step.node,
                "label": STEP_LABELS.get(step.node, step.node),
                "detail": step.reason or (f"{step.count} 条" if step.count else ""),
                "query": step.query,
                "elapsed_ms": step.elapsed_ms,
            },
        )

    try:
        result = request.app.state.conversations.ask(str(identifier), body, on_step=on_step)
    except AppError as error:
        progress.finish(request_id, "failed", error.message)
        raise
    except Exception:
        progress.finish(request_id, "failed", "本轮处理失败，可手动重试。")
        raise
    progress.finish(request_id, "done")
    return result


@router.get("/agent/progress/{request_id}")
def agent_progress(request: Request, request_id: str) -> dict:
    return request.app.state.progress.snapshot(request_id)


@router.post("/agent/scope")
def preview_scope(request: Request, body: AgentScope) -> dict:
    papers = request.app.state.agent_answers.scopes.resolve(body)
    items = []
    for paper in papers:
        try:
            status = request.app.state.indexing.status(paper.id).status
        except AppError:
            status = "unavailable"
        items.append(
            {
                "id": paper.id,
                "title": paper.parsed.metadata.title if paper.parsed else paper.filename,
                "filename": paper.filename,
                "index_status": status,
            }
        )
    return {"items": items, "count": len(items)}


@router.get("/agent/status")
def agent_status(request: Request) -> dict:
    answers = request.app.state.agent_answers
    return {
        "reranker_ready": bool(getattr(answers.reranker, "ready", lambda: True)()),
        "reranker_model": request.app.state.settings.reranker_model,
        "web_configured": answers.web.configured,
        "max_targeted_retrievals": 1,
        "max_model_calls": request.app.state.settings.agent_max_calls,
        "index_cache": request.app.state.indexing.cache.stats(),
    }
