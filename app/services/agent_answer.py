import hashlib
import json
import time

from app.core.errors import AppError
from app.domain.agent import AgentResult, AgentTurnInput
from app.domain.qa import Evidence
from app.providers.registry import ModelRegistry
from app.providers.web_search import TavilySearch, WebSearch
from app.retrieval.indexing import IndexService
from app.retrieval.reranking import CrossEncoderReranker, Reranker
from app.retrieval.search import retrieve
from app.services.agent_scope import ScopeResolver
from app.services.corrective_agent import CorrectiveAgent
from app.services.qa import clip_bytes


class AgentAnswerService:
    def __init__(
        self,
        indices: IndexService,
        registry: ModelRegistry,
        context_bytes: int,
        reranker: Reranker | None = None,
        web: WebSearch | None = None,
    ) -> None:
        self.indices, self.registry, self.context_bytes = indices, registry, context_bytes
        self.scopes = ScopeResolver(indices.papers)
        self.reranker = reranker or CrossEncoderReranker(registry.settings)
        self.web = web or TavilySearch(registry.settings.tavily_api_key)

    def answer(self, request: AgentTurnInput, turns: list[dict]) -> AgentResult:
        started = time.monotonic()
        documents = self.scopes.resolve(request.scope)
        manifests = [self.indices.require(self.indices.document(d.id)) for d in documents]
        provider = self.registry.resolve(request.connection_id, request.model)
        if not provider.configured:
            raise AppError(
                "model_not_configured", "此模型服务尚未配置服务端密钥，请先在本机环境配置。", 503
            )
        signature = hashlib.sha256(
            json.dumps(
                {
                    "kind": request.scope.kind,
                    "allow_web": request.allow_web,
                    "papers": [
                        (d.id, m.fingerprint) for d, m in zip(documents, manifests, strict=True)
                    ],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        completed = [t for t in turns if t["status"] == "completed"]
        history: list[dict] = []
        for turn in reversed(completed):
            if turn["response"]["scope_signature"] != signature or len(history) >= 6:
                break
            history.insert(0, turn)

        def validate() -> None:
            fresh = self.scopes.resolve(request.scope)
            if [d.id for d in fresh] != [d.id for d in documents]:
                raise AppError("scope_changed", "回答期间文件夹成员变化，请重新提问。", 409)
            if any(not self.indices.current(d, m) for d, m in zip(fresh, manifests, strict=True)):
                raise AppError("document_changed", "回答期间文献发生变化，请更新索引。", 409)

        def search(question: str) -> list[Evidence]:
            parts = [question[start : start + 700] for start in range(0, len(question), 600)]
            vectors = self.indices.embedder.encode(parts)
            candidates = [
                e
                for d, m in zip(documents, manifests, strict=True)
                for e in retrieve(
                    self.indices, d, m, question, top_k=20, query_vectors=vectors, post_filter=False
                )
            ]
            candidates.sort(key=lambda e: (-e.fusion_score, e.document_id, e.id))
            return [
                e.model_copy(update={"source_id": f"E{i + 1}"})
                for i, e in enumerate(candidates[:20])
            ]

        overviews = [
            {
                "document_id": d.id,
                "title": clip_bytes(d.parsed.metadata.title or d.filename, 80),
                "abstract": clip_bytes(
                    d.parsed.metadata.abstract or "", max(8, 3000 // len(documents))
                ),
            }
            for d in documents
            if d.parsed
        ]
        question = "\n".join(
            [t["request"]["question"][:400] for t in history[-3:]] + [request.question]
        )
        workflow = CorrectiveAgent(
            provider,
            self.reranker,
            self.web,
            request.model,
            self.context_bytes,
            self.registry.settings.agent_timeout,
        )
        state = workflow.run(
            request.question,
            question,
            search,
            validate,
            overviews,
            history,
            allow_web=request.allow_web,
            chat=request.scope.kind == "chat",
        )
        validate()
        completion = state["completion"]
        reason = {
            "disabled": "本轮已关闭联网补充。",
            "unconfigured": "未配置联网搜索服务。",
            "failed": "联网搜索失败。",
            "empty": "联网搜索未返回可用结果。",
        }.get(state["web_status"], "")
        return AgentResult(
            answer=completion.result.answer
            if completion
            else "当前范围没有足够原文证据。" + reason,
            sources=state["accepted"],
            web_sources=state["web"],
            resolved_document_ids=[d.id for d in documents],
            scope_documents=[
                {"id": d.id, "title": d.parsed.metadata.title or d.filename, "filename": d.filename}
                for d in documents
                if d.parsed
            ],
            context_document_ids=state["context_document_ids"],
            scope_signature=signature,
            model=request.model,
            connection_id=request.connection_id,
            insufficient_evidence=completion.result.insufficient_evidence if completion else True,
            latency_ms=int((time.monotonic() - started) * 1000),
            context_bytes=state["context_bytes"],
            history_turns=len(history),
            history_reset=bool(completed and not history),
            served_model=workflow.served_model,
            usage=workflow.usage,
            used_web=state["web_used"],
            web_status=state["web_status"],
            rewrite_count=state["rewrite_count"],
            model_calls=workflow.calls,
            trace=state["trace"],
        )
