import hashlib
import json
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient
from numpy.typing import NDArray

from app.api import create_app
from app.domain.papers import Block, Box, Document, PageInfo, PaperMetadata, ParsedPaper, utc_now
from app.domain.qa import Completion, ModelAnswer, TokenUsage
from tests.conftest import FixtureParser


class RecordingEmbedder:
    model = "test-embedding"
    revision = "fixed-test-revision"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> NDArray[np.float32]:
        self.calls.append(list(texts))
        vectors = np.array(
            [
                [
                    float(text.lower().count(word))
                    for word in ("aurora", "nebula", "method", "wavelength")
                ]
                for text in texts
            ],
            dtype="float32",
        )
        for vector in vectors:
            if not vector.any():
                vector[2] = 1
        return vectors


class RecordingProvider:
    configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, str]]]] = []
        self.result = ModelAnswer(
            answer="论文记录了观测值。[E1]", citations=["E1"], insufficient_evidence=False
        )

    def complete(self, model, messages) -> Completion:
        self.calls.append((model, messages))
        result = self.result
        if "[stage:grade]" in messages[0]["content"]:
            payload = json.loads(messages[-1]["content"])
            ids = [
                e["source_id"]
                for e in payload["evidence"]
                if not (
                    "nebula" == payload["question"].lower() and "nebula" not in e["text"].lower()
                )
            ]
            result = ModelAnswer(answer="判断", citations=ids, insufficient_evidence=not ids)
        elif "[stage:rewrite]" in messages[0]["content"]:
            result = ModelAnswer(
                answer=json.loads(messages[-1]["content"])["previous_query"],
                citations=[],
                insufficient_evidence=False,
            )
        return Completion(
            result=result,
            served_model="test-only-provider",
            usage=TokenUsage(total_tokens=123),
        )


def seed_document(repository, body: str, abstract: str | None = None) -> Document:
    identifier = str(uuid4())
    paper = ParsedPaper(
        parser="synthetic-domain-fixture",
        pages=[PageInfo(number=1, width=612, height=792, image="none.png")],
        blocks=[
            Block(
                id="original-block",
                page=1,
                order=0,
                kind="text",
                text=body,
                raw_text=body,
                bbox=Box(left=48, top=100, right=550, bottom=180),
            )
        ],
        figures=[],
        metadata=PaperMetadata(title="Synthetic scope test paper", abstract=abstract),
    )
    record = Document(
        id=identifier,
        filename=f"{identifier}.pdf",
        sha256=hashlib.sha256(body.encode()).hexdigest(),
        status="parsed",
        created_at=utc_now(),
        updated_at=utc_now(),
        parsed=paper,
    )
    repository.save(record)
    return record


class RecordingReranker:
    def ready(self):
        return True

    def rank(self, question, candidates, top_n=5):
        return [e.model_copy(update={"rerank_score": 1.0}) for e in candidates[:top_n]]


def qa_client(settings):
    embedder, provider = RecordingEmbedder(), RecordingProvider()
    app = create_app(settings, FixtureParser(), embedder, provider, RecordingReranker())
    return TestClient(app, base_url="http://127.0.0.1"), embedder, provider


def question(document_id: str, model="flash", text="What wavelength did Aurora report?") -> dict:
    return {"question": text, "scope": {"type": "documents", "ids": [document_id]}, "model": model}


def model_response(
    answer="Result [E1]", citations=None, *, insufficient=False, finish="stop"
) -> dict:
    return {
        "model": "deepseek-flash",
        "choices": [
            {
                "finish_reason": finish,
                "message": {
                    "content": json.dumps(
                        {
                            "answer": answer,
                            "citations": citations if citations is not None else ["E1"],
                            "insufficient_evidence": insufficient,
                        }
                    )
                },
            }
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
    }
