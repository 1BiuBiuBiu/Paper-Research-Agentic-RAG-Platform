from uuid import UUID

from fastapi import APIRouter, Request

from app.domain.qa import MODEL_IDS, IndexStatus, QueryRequest, QueryResponse

router = APIRouter()


@router.get("/models")
def models(request: Request) -> dict:
    return {
        "provider": "deepseek",
        "configured": request.app.state.provider.configured,
        "default": "flash",
        "thinking": "disabled",
        "live_verified": getattr(request.app.state.provider, "live_verified", False),
        "models": [
            {"id": key, "name": f"DeepSeek {key.title()}", "api_model": value}
            for key, value in MODEL_IDS.items()
        ],
    }


@router.get("/documents/{document_id}/index", response_model=IndexStatus)
def index_status(request: Request, document_id: UUID) -> IndexStatus:
    return request.app.state.indexing.status(str(document_id))


@router.post("/documents/{document_id}/index", response_model=IndexStatus)
def build_index(request: Request, document_id: UUID) -> IndexStatus:
    return request.app.state.indexing.build(str(document_id))


@router.post("/query", response_model=QueryResponse)
def query(request: Request, body: QueryRequest) -> QueryResponse:
    return request.app.state.qa.ask(body)
