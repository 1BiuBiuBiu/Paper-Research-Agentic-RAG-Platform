"""Synthetic graph contracts; no real LLM, search service or user paper."""

import json

import pytest

from app.core.errors import AppError
from app.domain.agent_flow import WebEvidence
from app.domain.qa import Completion, Evidence, ModelAnswer, TokenUsage
from app.services.agent_messages import context, messages
from app.services.corrective_agent import CorrectiveAgent
from tests.qa_helpers import RecordingReranker


class Scripted:
    """Replays one grading verdict per grade call, then answers with a fixed citation."""

    configured = True

    def __init__(self, grades=None, citation="E1", translation="What about 0.1 and 0.7?"):
        self.grades = list(grades or [])
        self.calls = []
        self.citation = citation
        self.translation = translation

    def complete(self, model, messages):
        self.calls.append(messages)
        prompt = messages[0]["content"]
        if "[stage:translate]" in prompt:
            result = ModelAnswer(answer=self.translation, citations=[], insufficient_evidence=False)
        elif "[stage:grade]" in prompt:
            spec = self.grades.pop(0) if self.grades else {}
            ids = list(spec.get("citations", []))
            result = ModelAnswer(
                answer="判分结论",
                citations=ids,
                insufficient_evidence=spec.get("insufficient", not ids),
                sufficient=spec.get("sufficient", bool(ids)),
                covered=list(spec.get("covered", [])),
                missing=list(spec.get("missing", [])),
                next_query=spec.get("next_query", ""),
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


def row(identifier: str, text: str, source_id: str, document_id: str = "doc") -> Evidence:
    return Evidence(
        id=identifier,
        page=1,
        text=text,
        block_ids=[],
        boxes=[],
        source_id=source_id,
        document_id=document_id,
        title="Fixture",
        filename="fixture.pdf",
        score=0.9,
    )


def execute(
    provider, web, *, allow_web=True, chat=False, first=None, second=None, question="original"
):
    first = [row("chunk", "REJECTED_LOCAL_TEXT", "E1")] if first is None else first
    second = [row("chunk2", "SECOND_ROUND_TEXT", "E1")] if second is None else second
    queries = []

    def retrieve(query):
        queries.append(query)
        return first if len(queries) == 1 else second

    agent = CorrectiveAgent(provider, RecordingReranker(), web, "flash", 12000)
    state = agent.run(
        question, question, retrieve, lambda: None, [], [], allow_web=allow_web, chat=chat
    )
    return agent, state, queries


def payload_of(messages) -> dict:
    return json.loads(messages[-1]["content"])


def test_sufficient_first_round_generates_with_two_calls():
    provider = Scripted([{"citations": ["E1"], "covered": ["问题要点"]}])
    agent, state, queries = execute(provider, Search())
    assert queries == ["original"]
    assert agent.calls == 2
    assert state["sufficient"] and state["next_query"] == ""
    assert [e.source_id for e in state["ledger"]] == ["E1"]
    assert [s.node for s in state["trace"]] == [
        "route_query",
        "retrieve",
        "rerank",
        "grade_documents",
        "generate",
    ]
    final = payload_of(provider.calls[-1])
    assert final["context"]["coverage"] == {"covered": ["问题要点"], "missing": []}
    assert "REJECTED_LOCAL_TEXT" in json.dumps(final)


def test_insufficient_round_targets_one_re_retrieval_then_generates():
    provider = Scripted(
        [
            {"citations": ["E1"], "missing": ["第二要点"], "next_query": "original follow-up"},
            {"citations": ["E1"], "sufficient": True, "covered": ["第二要点"]},
        ]
    )
    agent, state, queries = execute(provider, Search())
    assert queries == ["original", "original follow-up"]
    assert agent.calls == 3
    assert [s.node for s in state["trace"]] == [
        "route_query",
        "retrieve",
        "rerank",
        "grade_documents",
        "targeted_retrieve",
        "rerank",
        "grade_documents",
        "generate",
    ]
    # Both rounds' accepted evidence survives, de-duplicated by (document, chunk).
    assert {e.id for e in state["ledger"]} == {"chunk", "chunk2"}
    final = payload_of(provider.calls[-1])
    assert "REJECTED_LOCAL_TEXT" in json.dumps(final) and "SECOND_ROUND_TEXT" in json.dumps(final)
    reasons = [s.reason for s in state["trace"] if s.node == "grade_documents"]
    assert "定向补检（第 2 轮）" in reasons[0]


def test_second_round_is_judged_against_the_follow_up_question():
    provider = Scripted(
        [
            {"citations": ["E1"], "missing": ["第二要点"], "next_query": "missing part"},
            {"citations": ["E1"], "sufficient": True},
        ]
    )
    execute(provider, Search())
    first_grade = payload_of(provider.calls[0])
    second_grade = payload_of(provider.calls[1])
    assert first_grade["round"] == 1 and first_grade["question"] == "original"
    assert second_grade["round"] == 2 and second_grade["question"] == "missing part"
    assert [e["source_id"] for e in second_grade["evidence"]] == ["E1"]


def test_missing_points_cannot_invent_numbers_or_table_references():
    question = "缺失率 0.1 增加到 0.7 时 Acc2 是多少？"
    provider = Scripted(
        [
            {
                "citations": ["E1"],
                "missing": ["Table 9 的 0.5 数值", "0.7 时的具体数值", "缺失率 0.3 的数值"],
                "next_query": "0.7 时的数值",
            },
            {"citations": ["E1"], "sufficient": True},
        ]
    )
    agent, state, queries = execute(provider, Search(), allow_web=False, question=question)
    assert queries == [question, "0.7 时的数值"]
    # Only the gap stated with the question's own number survives sanitisation.
    reasons = [s.reason for s in state["trace"] if s.node == "grade_documents"]
    assert "0.7 时的具体数值" in reasons[0]
    assert "Table 9" not in reasons[0] and "0.5" not in reasons[0]
    final = payload_of(provider.calls[-1])
    assert "Table 9" not in json.dumps(final) and "0.5" not in json.dumps(final)


def test_repeated_follow_up_query_never_retrieves_again():
    provider = Scripted([{"citations": [], "missing": ["要点"], "next_query": "original"}])
    agent, state, queries = execute(provider, Search())
    assert queries == ["original"]
    assert agent.calls == 2  # grade ① and generation, no second retrieval or grade
    assert state["next_query"] == ""
    # The judged candidate is still available to generation; the judge only named the gap.
    assert [e.id for e in state["ledger"]] == ["chunk"]


def test_no_new_evidence_skips_the_second_grade():
    provider = Scripted([{"citations": ["E1"], "missing": ["要点"], "next_query": "same material"}])
    same = [row("chunk", "REJECTED_LOCAL_TEXT", "E1")]
    agent, state, queries = execute(provider, Search(), first=same, second=same)
    assert queries == ["original", "same material"]
    assert agent.calls == 2  # grade ① + generation; grade ② sees no new chunk
    assert [s.node for s in state["trace"]].count("grade_documents") == 2
    no_new = [s for s in state["trace"] if s.node == "targeted_retrieve"][0]
    assert no_new.reason == "no_new_evidence"
    assert state["ledger"] == [e for e in state["ledger"] if e.id == "chunk"]


def test_refusal_without_evidence_does_not_spend_a_generation_call():
    provider = Scripted([{"citations": [], "missing": ["要点"]}])
    agent, state, _ = execute(provider, Search(), allow_web=False, first=[])
    assert state["completion"] is None and agent.calls == 0
    assert state["web_status"] == "disabled"


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
    provider = Scripted([{"citations": [], "missing": ["要点"]}])
    agent, state, _ = execute(provider, web, allow_web=mode != "disabled", first=[])
    assert state["completion"] is None and agent.calls == 0
    assert state["web_status"] == status


def test_unknown_web_citation_is_rejected():
    provider = Scripted([{"citations": [], "missing": ["要点"]}], citation="W99")
    with pytest.raises(AppError, match="引用"):
        execute(provider, Search(), first=[])


def test_chat_never_retrieves_or_searches():
    provider = Scripted(citation="W1")
    with pytest.raises(AppError):
        execute(provider, Search(), chat=True)
    assert len(provider.calls) == 1


def test_deadline_prevents_any_model_or_search_call():
    agent = CorrectiveAgent(Scripted(), RecordingReranker(), Search(), "flash", 12000, timeout=0)
    with pytest.raises(AppError, match="总时间"):
        agent.run("q", "q", lambda q: [], lambda: None, [], [], allow_web=True, chat=False)
    assert agent.calls == 0


def test_invalid_grading_ids_never_enter_generation():
    provider = Scripted([{"citations": ["E999"], "sufficient": True}])
    with pytest.raises(AppError, match="判分"):
        execute(provider, Search())
    assert len(provider.calls) == 1


def test_sufficient_verdict_requires_supporting_evidence():
    provider = Scripted([{"citations": [], "sufficient": True}])
    with pytest.raises(AppError, match="判分"):
        execute(provider, Search())


def test_every_judged_candidate_reaches_generation_even_when_the_judge_cites_none():
    provider = Scripted(
        [
            {"citations": ["E1"], "missing": ["要点"], "next_query": "other material"},
            {"citations": [], "missing": ["要点"]},
        ]
    )
    agent, state, queries = execute(provider, Search())
    assert queries == ["original", "other material"] and agent.calls == 3
    # Round 2's judged candidates are kept even though the judge cited nothing there.
    assert {e.id for e in state["ledger"]} == {"chunk", "chunk2"}
    final = payload_of(provider.calls[-1])
    assert "REJECTED_LOCAL_TEXT" in json.dumps(final)
    assert "SECOND_ROUND_TEXT" in json.dumps(final)


def test_a_single_citation_does_not_narrow_the_generation_evidence():
    first = [
        row("chunk", "PRIMARY_VALUE_92.1", "E1"),
        row("chunk-b", "BASELINE_VALUE_75.2", "E2"),
    ]
    provider = Scripted([{"citations": ["E1"], "sufficient": True}])
    agent, state, _ = execute(provider, Search(), first=first)
    assert {e.id for e in state["ledger"]} == {"chunk", "chunk-b"}
    final = json.dumps(payload_of(provider.calls[-1]), ensure_ascii=False)
    assert "PRIMARY_VALUE_92.1" in final and "BASELINE_VALUE_75.2" in final


def run_question(question, provider):
    reranker = RecordingReranker()
    found = [[row("chunk", "English table row", "E1")], [row("chunk2", "New table row", "E1")]]

    def retrieve(query):
        return found.pop(0) if len(found) > 1 else found[0]

    agent = CorrectiveAgent(provider, reranker, Search(), "flash", 12000, top_n=3)
    state = agent.run(
        question,
        question,
        retrieve,
        lambda: None,
        [],
        [],
        allow_web=False,
        chat=False,
    )
    return agent, state, reranker


def test_chinese_question_reranks_with_english_translation_and_translation_is_not_counted():
    provider = Scripted(
        [
            {"citations": ["E1"], "missing": ["缺失数值"], "next_query": "0.7 时的数值"},
            {"citations": ["E1"], "sufficient": True},
        ]
    )
    agent, state, reranker = run_question("缺失率 0.1 增加到 0.7 时 Acc2 是多少？", provider)
    assert [s.node for s in state["trace"]].count("translate_query") == 1
    assert reranker.questions[0] == "What about 0.1 and 0.7?"
    assert state["search_query_en"] == "What about 0.1 and 0.7?"
    # Translation is spent outside the three counted calls; grade ① + grade ② + generation.
    assert agent.calls == 3 and len(provider.calls) == 4


def test_translation_losing_numbers_falls_back_to_original_query():
    provider = Scripted(
        [{"citations": ["E1"], "sufficient": True}],
        translation="What about the missing rate?",
    )
    agent, state, reranker = run_question("缺失率 0.1 增加到 0.7 时 Acc2 是多少？", provider)
    assert reranker.questions == ["缺失率 0.1 增加到 0.7 时 Acc2 是多少？"]
    assert state["search_query_en"] == "缺失率 0.1 增加到 0.7 时 Acc2 是多少？"
    reasons = [s.reason for s in state["trace"] if s.node == "translate_query"]
    assert reasons == ["numbers_lost"]
    assert agent.calls == 2 and len(provider.calls) == 3


def test_grading_may_cite_support_and_still_call_the_round_insufficient():
    provider = Scripted(
        [
            {
                "citations": ["E1"],
                "insufficient": True,
                "sufficient": False,
                "missing": ["第二个要点"],
                "next_query": "follow up",
            },
            {"citations": ["E1"], "sufficient": True},
        ]
    )
    agent, state, queries = execute(provider, Search())
    assert queries == ["original", "follow up"] and agent.calls == 3
    # The cited fragment is still accepted into the ledger even though the round fell short.
    assert {e.id for e in state["ledger"]} == {"chunk", "chunk2"}


def test_missing_verdict_falls_back_to_support_and_the_old_flag():
    provider = Scripted(
        [
            {"citations": ["E1"], "insufficient": True, "sufficient": None, "next_query": "more"},
            {"citations": ["E1"], "sufficient": True},
        ]
    )
    agent, state, queries = execute(provider, Search())
    assert queries == ["original", "more"] and agent.calls == 3
    reasons = [step.reason for step in state["trace"] if step.node == "grade_documents"]
    assert reasons[0].startswith("判分：缺") and "定向补检" in reasons[0]


def test_english_question_skips_translation_call():
    provider = Scripted([{"citations": ["E1"], "sufficient": True}])
    agent, state, reranker = run_question("What is Acc2 at 0.1 and 0.7?", provider)
    assert all(s.node != "translate_query" for s in state["trace"])
    assert reranker.questions == ["What is Acc2 at 0.1 and 0.7?"]
    assert agent.calls == 2 and len(provider.calls) == 2


def test_content_budget_keeps_the_configured_value_for_short_turns():
    agent = CorrectiveAgent(Scripted(), RecordingReranker(), Search(), "flash", 50000)
    assert agent.content_budget("What is Acc2?", [], False) == 50000


def test_content_budget_shrinks_a_full_turn_under_the_provider_cap():
    history = [
        {"request": {"question": "问" * 400}, "response": {"answer": "答" * 800}} for _ in range(6)
    ]
    question = "问" * 2000
    agent = CorrectiveAgent(Scripted(), RecordingReranker(), Search(), "flash", 50000)
    budget = agent.content_budget(question, history, False)
    assert budget < 50000
    items = [row(f"c{index}", "甲" * 1200, "E1") for index in range(20)]
    packed, local, _ = context(items, [], [], budget)
    payload = json.dumps(
        {"model": "deepseek-flash", "messages": messages(question, packed, history, False)},
        ensure_ascii=False,
    ).encode()
    assert local and len(payload) < 65536
