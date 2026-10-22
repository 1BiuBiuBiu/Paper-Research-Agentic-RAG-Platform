"""Synthetic graph contracts; no real LLM, search service or user paper."""

import json

import pytest

from app.core.errors import AppError
from app.domain.agent_flow import WebEvidence
from app.domain.qa import Completion, Evidence, ModelAnswer, TokenUsage
from app.services.corrective_agent import CorrectiveAgent
from tests.qa_helpers import RecordingReranker


class Scripted:
    configured = True

    def __init__(self, accept_round=99, citation="W1"):
        self.calls = []
        self.round = 0
        self.accept_round = accept_round
        self.citation = citation

    def complete(self, model, messages):
        self.calls.append(messages)
        prompt = messages[0]["content"]
        if "[stage:grade]" in prompt:
            self.round += 1
            ids = ["E1"] if self.round >= self.accept_round else []
            result = ModelAnswer(answer="判断", citations=ids, insufficient_evidence=not ids)
        elif "[stage:rewrite]" in prompt:
            result = ModelAnswer(
                answer=f"rewritten {self.round}", citations=[], insufficient_evidence=False
            )
        else:
            result = ModelAnswer(
                answer=f"Result [{self.citation}]",
                citations=[self.citation],
                insufficient_evidence=False,
            )
        return Completion(
            result=result, served_model="synthetic", usage=TokenUsage(total_tokens=10)
        )


class Search:
    configured = True

    def __init__(self, failure=False):
        self.calls = []
        self.failure = failure

    def search(self, query):
        self.calls.append(query)
        if self.failure:
            raise AppError("mock", "not exposed", 502)
        return [
            WebEvidence(
                source_id="W1",
                title="Synthetic source",
                url="https://example.org/",
                text="Web snippet",
                query=query,
                retrieved_at="2026-10-22T00:00:00Z",
            )
        ]


def execute(provider, web, *, allow_web=True, chat=False):
    e = Evidence(
        id="chunk",
        page=1,
        text="REJECTED_LOCAL_TEXT",
        block_ids=[],
        boxes=[],
        source_id="E1",
        document_id="doc",
        title="Fixture",
        filename="fixture.pdf",
        score=0.9,
    )
    queries = []

    def retrieve(query):
        queries.append(query)
        return [e]

    agent = CorrectiveAgent(provider, RecordingReranker(), web, "flash", 12000)
    state = agent.run(
        "original", "original", retrieve, lambda: None, [], [], allow_web=allow_web, chat=chat
    )
    return agent, state, queries


def test_two_rewrites_web_fallback_clears_rejected_context_and_preserves_question():
    provider, web = Scripted(), Search()
    agent, state, queries = execute(provider, web)
    assert queries == ["original", "rewritten 1", "rewritten 2"]
    assert web.calls == ["rewritten 2"]
    assert agent.calls == 6 and agent.usage.total_tokens == 60
    assert state["rewrite_count"] == 2 and state["web_status"] == "available"
    assert not state["accepted"] and state["web"][0].source_id == "W1"
    final = json.loads(provider.calls[-1][-1]["content"])
    assert final["question"] == "original" and final["context"]["evidence"] == []
    assert "REJECTED_LOCAL_TEXT" not in json.dumps(final)
    assert [s.node for s in state["trace"]] == [
        "route_query",
        "retrieve",
        "rerank",
        "grade_documents",
        "rewrite_query",
        "retrieve",
        "rerank",
        "grade_documents",
        "rewrite_query",
        "retrieve",
        "rerank",
        "grade_documents",
        "web_search",
        "generate",
    ]
    rewrites = [s for s in state["trace"] if s.node == "rewrite_query"]
    assert [(s.attempt, s.query) for s in rewrites] == [(1, "rewritten 1"), (2, "rewritten 2")]


@pytest.mark.parametrize("accepted", [1, 2, 3])
def test_local_success_after_filter_skips_web(accepted):
    agent, state, queries = execute(Scripted(accepted, "E1"), Search())
    assert state["rewrite_count"] == accepted - 1
    assert state["accepted"][0].source_id == "E1"
    assert state["web_status"] == "not_needed" and len(queries) == accepted
    assert agent.calls == accepted * 2


@pytest.mark.parametrize(
    "mode,status",
    [
        ("disabled", "disabled"),
        ("unconfigured", "unconfigured"),
        ("failed", "failed"),
        ("empty", "empty"),
    ],
)
def test_missing_web_never_generates_without_evidence(mode, status):
    web = Search(failure=mode == "failed")
    if mode == "unconfigured":
        web.configured = False
    if mode == "empty":
        web.search = lambda query: []
    agent, state, _ = execute(Scripted(), web, allow_web=mode != "disabled")
    assert state["completion"] is None and agent.calls == 5
    assert state["web_status"] == status


def test_unknown_web_citation_is_rejected():
    with pytest.raises(AppError, match="引用"):
        execute(Scripted(citation="W99"), Search())


def test_chat_never_retrieves_or_searches():
    provider = Scripted(citation="W1")
    with pytest.raises(AppError):  # Invented chat citation also fails.
        execute(provider, Search(), chat=True)
    assert len(provider.calls) == 1


def test_deadline_prevents_any_model_or_search_call():
    agent = CorrectiveAgent(Scripted(), RecordingReranker(), Search(), "flash", 12000, timeout=0)
    with pytest.raises(AppError, match="总时间"):
        agent.run("q", "q", lambda q: [], lambda: None, [], [], allow_web=True, chat=False)
    assert agent.calls == 0


def test_unchanged_rewrite_stops_without_retrieving_again():
    provider = Scripted()
    original = provider.complete

    def complete(model, messages):
        if "[stage:rewrite]" in messages[0]["content"]:
            result = original(model, messages)
            result.result.answer = "original"
            return result
        return original(model, messages)

    provider.complete = complete
    agent, state, queries = execute(provider, Search())
    assert queries == ["original"] and state["rewrite_count"] == 1
    assert agent.calls == 3


def test_invalid_grading_ids_never_enter_generation():
    provider = Scripted(accept_round=1)
    original = provider.complete

    def complete(model, messages):
        result = original(model, messages)
        result.result.citations = ["E999"]
        return result

    provider.complete = complete
    with pytest.raises(AppError, match="相关性"):
        execute(provider, Search())
    assert len(provider.calls) == 1
