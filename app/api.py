"""Research API. Migrated from the upstream FastAPI entrypoint."""

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app.core.body_limit import BodyLimitMiddleware
from app.core.config import Settings
from app.core.errors import AppError
from app.parsing.runner import DoclingRunner
from app.providers.deepseek import CompletionProvider, DeepSeekProvider
from app.providers.registry import ModelRegistry
from app.providers.web_search import WebSearch
from app.repositories.connections import ConnectionRepository
from app.repositories.conversations import ConversationRepository
from app.repositories.indices import IndexRepository
from app.repositories.papers import PaperRepository
from app.repositories.projects import ProjectRepository
from app.retrieval.embedding import BGEEmbedder, Embedder
from app.retrieval.indexing import IndexService
from app.retrieval.reranking import Reranker
from app.routes.agent import router as agent_router
from app.routes.categories import router as category_router
from app.routes.connection_tests import router as connection_test_router
from app.routes.papers import router
from app.routes.projects import router as project_router
from app.routes.qa import router as qa_router
from app.services.agent_answer import AgentAnswerService
from app.services.connection_test import ConnectionTester
from app.services.conversations import ConversationService
from app.services.ingestion import IngestionService, Parser
from app.services.query_adapter import QueryAdapter


def create_app(
    settings: Settings | None = None,
    parser: Parser | None = None,
    embedder: Embedder | None = None,
    provider: CompletionProvider | None = None,
    reranker: Reranker | None = None,
    web: WebSearch | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    repository = PaperRepository(settings.data_dir / "library.sqlite3")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        repository.recover_interrupted()
        application.state.conversations.repository.recover_interrupted()
        yield

    application = FastAPI(title="Research Paper RAG", version="0.10.0", lifespan=lifespan)
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )
    application.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_upload_bytes + 65536)
    application.state.settings = settings
    application.state.repository = repository
    application.state.ingestion = IngestionService(
        settings, repository, parser or DoclingRunner(settings.parse_timeout)
    )
    application.state.indexing = IndexService(
        repository, IndexRepository(repository), embedder or BGEEmbedder(settings)
    )
    application.state.provider = provider or DeepSeekProvider(settings)
    application.state.connections = ConnectionRepository(repository)
    application.state.registry = ModelRegistry(
        settings, application.state.connections, application.state.provider
    )
    application.state.connection_tester = ConnectionTester(application.state.registry)
    application.state.agent_answers = AgentAnswerService(
        application.state.indexing,
        application.state.registry,
        settings.context_bytes,
        reranker,
        web,
    )
    application.state.qa = QueryAdapter(application.state.agent_answers)
    application.state.conversations = ConversationService(
        ConversationRepository(repository), application.state.agent_answers
    )
    application.state.projects = ProjectRepository(repository)

    @application.middleware("http")
    async def local_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # No CORS wildcard. Reject browser writes initiated by unrelated origins.
        origin = request.headers.get("origin")
        allowed = {
            "http://127.0.0.1:8501",
            "http://localhost:8501",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        }
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in allowed:
            return JSONResponse(
                {"code": "origin_rejected", "message": "不允许此来源执行写操作。"}, 403
            )
        length = request.headers.get("content-length")
        try:
            oversized = length is not None and int(length) > settings.max_upload_bytes + 65536
        except ValueError:
            return JSONResponse({"code": "invalid_length", "message": "请求长度无效。"}, 400)
        if oversized:
            return JSONResponse({"code": "upload_limit", "message": "请求超过大小限制。"}, 413)
        return await call_next(request)

    @application.exception_handler(AppError)
    async def app_error(request: Request, error: AppError) -> JSONResponse:
        return JSONResponse({"code": error.code, "message": error.message}, error.status)

    @application.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError) -> JSONResponse:
        # Never echo a rejected request body or credential accidentally supplied by a client.
        return JSONResponse(
            {
                "code": "invalid_request",
                "message": "输入未通过校验，请检查问题、文档选择或参数。",
                "errors": [
                    {"location": list(item["loc"]), "type": item["type"]} for item in error.errors()
                ],
            },
            422,
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        request_id = str(uuid.uuid4())
        logging.getLogger(__name__).error(
            "request_failure id=%s type=%s", request_id, type(error).__name__
        )
        return JSONResponse(
            {"code": "internal_error", "message": "处理失败，请重试。", "request_id": request_id},
            500,
        )

    @application.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "version": "0.10.0",
            "slice": "S3.6",
            "parser": "docling",
            "rag_enabled": True,
            "model_configured": application.state.provider.configured,
            "rag_ready": getattr(application.state.provider, "live_verified", False),
            "rag_readiness": "需有效文档索引与可用服务端 Key；未自动验证远程服务",
        }

    application.include_router(router)
    application.include_router(category_router)
    application.include_router(qa_router)
    application.include_router(agent_router)
    application.include_router(connection_test_router)
    application.include_router(project_router)
    return application


app = create_app()
