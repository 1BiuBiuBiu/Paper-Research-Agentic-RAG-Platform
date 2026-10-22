from app.core.errors import AppError
from app.domain.agent import AgentTurnInput
from app.repositories.conversations import ConversationRepository
from app.services.agent_answer import AgentAnswerService


class ConversationService:
    def __init__(self, repository: ConversationRepository, answers: AgentAnswerService) -> None:
        self.repository, self.answers = repository, answers

    def ask(self, identifier: str, request: AgentTurnInput) -> dict:
        cached = self.repository.begin(identifier, request)
        if cached is not None:
            return cached
        try:
            conversation = self.repository.get(identifier)
            result = self.answers.answer(request, conversation["turns"]).model_dump(mode="json")
            self.repository.finish(identifier, str(request.request_id), result, request.question)
            return result
        except AppError as error:
            self.repository.fail(str(request.request_id), error)
            raise
        except Exception:
            self.repository.fail(
                str(request.request_id), AppError("turn_failed", "本轮处理失败，可手动重试。", 500)
            )
            raise
