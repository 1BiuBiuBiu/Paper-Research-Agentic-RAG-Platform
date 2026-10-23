from app.domain.agent_flow import AgentStep
from app.domain.qa import Completion, Evidence, ModelAnswer, TokenUsage
from app.services.corrective_agent import CorrectiveAgent
from app.services.progress import ProgressRegistry
from tests.qa_helpers import RecordingReranker, qa_client


def test_registry_collects_events_and_status():
    registry = ProgressRegistry()
    registry.start("req")
    registry.publish("req", {"node": "retrieve", "label": "召回候选", "detail": "12 条"})
    snapshot = registry.snapshot("req")
    assert snapshot["status"] == "running" and snapshot["events"][0]["detail"] == "12 条"
    registry.finish("req", "done")
    assert registry.snapshot("req")["status"] == "done"
    assert registry.snapshot("missing") == {"status": "unknown", "events": []}


def test_registry_expires_finished_turns():
    registry = ProgressRegistry(ttl=-1)
    registry.start("req")
    registry.finish("req")
    assert registry.snapshot("req")["status"] == "unknown"


def test_agent_reports_each_step_to_the_callback():
    class Provider:
        configured = True

        def complete(self, model, messages):
            prompt = messages[0]["content"]
            if "[stage:grade]" in prompt:
                answer = ModelAnswer(answer="判断", citations=["E1"], insufficient_evidence=False)
            else:
                answer = ModelAnswer(
                    answer="ok [E1]", citations=["E1"], insufficient_evidence=False
                )
            return Completion(result=answer, served_model="synthetic", usage=TokenUsage())

    row = Evidence(
        id="chunk",
        page=1,
        text="table row",
        block_ids=[],
        boxes=[],
        source_id="E1",
        document_id="doc",
        title="Fixture",
        filename="fixture.pdf",
        score=0.9,
    )
    steps: list[AgentStep] = []
    agent = CorrectiveAgent(
        Provider(), RecordingReranker(), object(), "flash", 12000, on_step=steps.append
    )
    agent.run(
        "How many?",
        "How many?",
        lambda query: [row],
        lambda: None,
        [],
        [],
        allow_web=False,
        chat=False,
    )
    assert [step.node for step in steps] == [
        "route_query",
        "retrieve",
        "rerank",
        "grade_documents",
        "generate",
    ]


def test_progress_endpoint_is_available_without_a_turn(settings):
    client, _, _ = qa_client(settings)
    with client:
        response = client.get("/agent/progress/unknown-request")
        assert response.status_code == 200
        assert response.json() == {"status": "unknown", "events": []}
