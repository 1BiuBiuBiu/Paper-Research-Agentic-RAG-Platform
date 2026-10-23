"""Human annotated metrics; missing denominators yield null, never fabricated scores."""

from math import log2

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Heading(Record):
    document_id: str
    text: str
    page: int = Field(ge=1)
    is_real_section_heading: bool
    level: int | None = Field(ge=1, le=4)
    heading_type: str
    predicted_heading: bool
    predicted_level: int | None = Field(ge=1, le=4)

    @model_validator(mode="after")
    def levels(self) -> "Heading":
        if self.is_real_section_heading != (self.level is not None):
            raise ValueError("gold heading and level disagree")
        if self.predicted_heading != (self.predicted_level is not None):
            raise ValueError("predicted heading and level disagree")
        return self


class GoldEvidence(Record):
    evidence_id: str
    document_id: str
    page: int = Field(ge=1)
    element_type: str
    table_label: str | None = None
    evidence_text: str
    relevance: int = Field(ge=1, le=2)


class Retrieved(Record):
    rank: int = Field(ge=1, le=20)
    source_id: str
    relevance: int = Field(ge=0, le=2)
    matched_gold_ids: list[str]


class Retrieval(Record):
    question_id: str
    question: str
    evidence_complete: bool
    gold_evidence: list[GoldEvidence]
    retrieved_evidence: list[Retrieved] = Field(max_length=20)
    ideal_relevances: list[int]

    @model_validator(mode="after")
    def consistent(self) -> "Retrieval":
        ids = [e.evidence_id for e in self.gold_evidence]
        rows = self.retrieved_evidence
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate gold ID")
        if [r.rank for r in rows] != list(range(1, len(rows) + 1)):
            raise ValueError("ranks must be ordered and contiguous")
        if len({r.source_id for r in rows}) != len(rows):
            raise ValueError("duplicate retrieved source")
        if any(not set(r.matched_gold_ids) <= set(ids) for r in rows):
            raise ValueError("unknown matched gold ID")
        direct = {e.evidence_id for e in self.gold_evidence if e.relevance == 2}
        if any(set(r.matched_gold_ids) & direct and r.relevance != 2 for r in rows):
            raise ValueError("direct evidence match requires relevance 2")
        if any(type(v) is not int or v not in (0, 1, 2) for v in self.ideal_relevances):
            raise ValueError("ideal grades must be integers 0..2")
        for grade in (1, 2):
            if self.ideal_relevances.count(grade) < sum(r.relevance == grade for r in rows):
                raise ValueError("ideal pool omits retrieved grades")
        if self.ideal_relevances.count(2) < len(direct):
            raise ValueError("ideal pool omits direct gold evidence")
        return self


class Citation(Record):
    citation_id: str
    supports_answer: bool


class Answer(Record):
    question_id: str
    question: str
    answer: str
    answerable: bool
    answer_correct: bool
    required_components_complete: bool
    citations_support_answer: bool
    scope_leakage: bool
    correct_abstention: bool
    citations: list[Citation]


class Dataset(Record):
    run_id: str = Field(min_length=1)
    provenance: dict[str, str] = Field(min_length=1)
    heading_candidates_complete: bool
    # Retrieval window: defaults to the historical @5, set 10 for the current rerank_top_n.
    top_k: int = Field(default=5, ge=1, le=20)
    headings: list[Heading]
    retrieval: list[Retrieval]
    answers: list[Answer]

    @model_validator(mode="after")
    def inventory(self) -> "Dataset":
        if not (self.headings or self.retrieval or self.answers):
            raise ValueError("empty evaluation dataset")
        if self.headings and not self.heading_candidates_complete:
            raise ValueError("heading inventory must include all gold and predicted candidates")
        for rows in (self.retrieval, self.answers):
            ids = [r.question_id for r in rows]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate question ID")
        for row in self.retrieval:
            if len(row.retrieved_evidence) > self.top_k:
                raise ValueError("retrieved evidence exceeds the evaluation window top_k")
            if any(item.rank > self.top_k for item in row.retrieved_evidence):
                raise ValueError("retrieved rank exceeds the evaluation window top_k")
        keys = [(h.document_id, h.page, h.text) for h in self.headings]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate heading candidate")
        return self


def ratio(n: float, d: float) -> float | None:
    return n / d if d else None


def dcg(grades: list[int], k: int = 5) -> float:
    return sum((2**g - 1) / log2(i + 2) for i, g in enumerate(grades[:k]))


def score(data: Dataset) -> dict:
    k = data.top_k
    h = data.headings
    tp = sum(r.is_real_section_heading and r.predicted_heading for r in h)
    fp = sum(not r.is_real_section_heading and r.predicted_heading for r in h)
    fn = sum(r.is_real_section_heading and not r.predicted_heading for r in h)
    recall, hits, ndcg = [], [], []
    for row in data.retrieval:
        direct = {e.evidence_id for e in row.gold_evidence if e.relevance == 2}
        found = {i for r in row.retrieved_evidence for i in r.matched_gold_ids} & direct
        if direct:
            if row.evidence_complete:
                recall.append(len(found) / len(direct))
            else:
                hits.append(float(bool(found)))
        ideal = dcg(sorted(row.ideal_relevances, reverse=True), k)
        if ideal:
            ndcg.append(dcg([r.relevance for r in row.retrieved_evidence], k) / ideal)
    a = data.answers
    successes = [
        r.answer_correct
        and r.required_components_complete
        and r.citations_support_answer
        and not r.scope_leakage
        and (r.answerable or r.correct_abstention)
        and (not r.answerable or bool(r.citations))
        and all(c.supports_answer for c in r.citations)
        for r in a
    ]
    citations = [c for r in a for c in r.citations]
    return {
        "heading": {
            "f1": ratio(2 * tp, 2 * tp + fp + fn),
            "level_accuracy": ratio(
                sum(
                    r.level == r.predicted_level
                    for r in h
                    if r.is_real_section_heading and r.predicted_heading
                ),
                tp,
            ),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "candidates": len(h),
        },
        "retrieval": {
            "k": k,
            "recall_at_k": ratio(sum(recall), len(recall)),
            "recall_questions": len(recall),
            "hit_at_k": ratio(sum(hits), len(hits)),
            "hit_questions": len(hits),
            "ndcg_at_k": ratio(sum(ndcg), len(ndcg)),
            "ndcg_questions": len(ndcg),
            "zero_direct_gold_questions": sum(
                not any(e.relevance == 2 for e in r.gold_evidence) for r in data.retrieval
            ),
            "zero_idcg_questions": len(data.retrieval) - len(ndcg),
        },
        "answer": {
            "grounded_answer_success_rate": ratio(sum(successes), len(a)),
            "citation_precision": ratio(sum(c.supports_answer for c in citations), len(citations)),
            "scope_leakage_rate": ratio(sum(r.scope_leakage for r in a), len(a)),
            "questions": len(a),
            "citations": len(citations),
        },
    }
