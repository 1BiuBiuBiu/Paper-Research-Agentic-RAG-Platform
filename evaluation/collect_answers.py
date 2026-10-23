"""Explicitly authorized local-only pilot agent run; resumable per question."""

import hashlib
import json
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from app.core.config import Settings
from app.providers.deepseek import DeepSeekProvider
from app.repositories.indices import IndexRepository
from app.repositories.papers import PaperRepository
from app.retrieval.embedding import BGEEmbedder
from app.retrieval.indexing import IndexService
from app.retrieval.reranking import CrossEncoderReranker
from app.retrieval.search import fuse, library_candidates
from app.services.corrective_agent import CorrectiveAgent
from evaluation.collect_pilot import OUT, ROOT, load, save


class NoWeb:
    configured = False

    def search(self, question):
        raise RuntimeError("Network search forbidden for this evaluation")


class RecordingProvider:
    def __init__(self, settings):
        self.provider = DeepSeekProvider(settings)
        self.configured = self.provider.configured
        self.grades = []

    def complete(self, model, messages):
        result = self.provider.complete(model, messages)
        if "[stage:grade]" in messages[0]["content"]:
            self.grades.append(result.result.model_dump())
        return result


def main():
    settings = replace(Settings.from_env(), data_dir=(OUT / "runtime").resolve())
    papers = PaperRepository(settings.data_dir / "library.sqlite3")
    indices = IndexService(papers, IndexRepository(papers), BGEEmbedder(settings))
    summary = json.loads((ROOT / "dataset_summary.json").read_text(encoding="utf-8-sig"))
    mapping = {
        i["document_id"]: str(uuid5(NAMESPACE_URL, i["document_id"]))
        for i in summary["document_breakdown"]
    }
    result_path = OUT / "agent_outputs.json"
    outputs = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else []
    completed = {r["question_id"] for r in outputs}
    for q in load("rag_questions.jsonl"):
        if q["question_id"] in completed:
            continue
        documents = [papers.get(mapping[i]) for i in q["scope_document_ids"]]

        def validate(documents=documents):
            for d in documents:
                if hashlib.sha256((ROOT / d.filename).read_bytes()).hexdigest() != d.sha256:
                    raise ValueError("PDF source changed")
                indices.require(d)

        def search(question, documents=documents):
            parts = [question[i : i + 700] for i in range(0, len(question), 600)]
            vectors = indices.embedder.encode(parts)
            manifests = [indices.require(document) for document in documents]
            candidates = fuse(
                library_candidates(
                    indices,
                    documents,
                    manifests,
                    question,
                    settings.candidates,
                    query_vectors=vectors,
                ),
                settings.candidates,
            )
            return [
                e.model_copy(update={"source_id": f"E{i + 1}"})
                for i, e in enumerate(candidates)
            ]

        provider = RecordingProvider(settings)
        workflow = CorrectiveAgent(
            provider,
            CrossEncoderReranker(settings),
            NoWeb(),
            "flash",
            settings.context_bytes,
            settings.agent_timeout,
        )
        print("Answering " + q["question_id"], flush=True)
        try:
            state = workflow.run(
                q["question"],
                q["question"],
                search,
                validate,
                [
                    {
                        "document_id": d.id,
                        "title": d.parsed.metadata.title or d.filename,
                        "abstract": (d.parsed.metadata.abstract or "")[:3000],
                    }
                    for d in documents
                ],
                [],
                allow_web=False,
                chat=False,
            )
            completion = state["completion"]
            outputs.append(
                dict(
                    question_id=q["question_id"],
                    answer=completion.result.model_dump()
                    if completion
                    else {
                        "answer": "当前范围没有足够原文证据。本轮已关闭联网补充。",
                        "citations": [],
                        "insufficient_evidence": True,
                    },
                    served_model=workflow.served_model,
                    model_calls=workflow.calls,
                    usage=workflow.usage.model_dump(),
                    rewrite_count=state["round"],
                    sufficient=state["sufficient"],
                    covered=state["covered"],
                    missing=state["missing"],
                    ledger=[e.model_dump() for e in state["ledger"]],
                    cited_sources=[e.model_dump() for e in state["accepted"]],
                    trace=[r.model_dump() for r in state["trace"]],
                    used_web=state["web_used"],
                )
            )
        except Exception as exc:
            outputs.append(
                dict(
                    question_id=q["question_id"],
                    error_type=type(exc).__name__,
                    error_code=getattr(exc, "code", "runtime_error"),
                )
            )
        save("agent_outputs.json", outputs)
    print("Agent collection complete", flush=True)


if __name__ == "__main__":
    main()
