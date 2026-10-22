"""Keep /query's single-paper contract while sharing the production corrective graph."""

from uuid import uuid4

from app.domain.agent import AgentScope, AgentTurnInput
from app.domain.qa import MODEL_IDS, QueryRequest, QueryResponse
from app.services.agent_answer import AgentAnswerService


class QueryAdapter:
    def __init__(self, answers: AgentAnswerService) -> None:
        self.answers = answers

    def ask(self, request: QueryRequest) -> QueryResponse:
        result = self.answers.answer(
            AgentTurnInput(
                request_id=uuid4(),
                expected_revision=0,
                question=request.question,
                scope=AgentScope(
                    kind="documents", document_ids=request.scope.ids, include_samples=True
                ),
                model=request.model,
                allow_web=request.allow_web,
            ),
            [],
        )
        values = result.model_dump()
        document = self.answers.indices.document(str(request.scope.ids[0]))
        return QueryResponse(
            **{
                k: v
                for k, v in values.items()
                if k in QueryResponse.model_fields and k not in {"scope", "model"}
            },
            scope=request.scope,
            model=request.model,
            requested_model=MODEL_IDS[request.model],
            overview_used=bool(document.parsed and document.parsed.metadata.abstract),
        )
