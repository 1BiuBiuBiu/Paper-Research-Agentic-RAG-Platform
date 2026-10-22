from app.core.errors import AppError
from app.domain.papers import (
    Document,
    JournalMetric,
    MetadataPatch,
    MetricInput,
    Provenance,
    utc_now,
)
from app.repositories.papers import PaperRepository


def update_metadata(repo: PaperRepository, document_id: str, patch: MetadataPatch) -> Document:
    def mutate(document: Document) -> None:
        if document.parsed is None:
            raise AppError("not_parsed", "论文尚未成功解析。", 409)
        paper = document.parsed
        before = (paper.metadata.venue_type, paper.metadata.venue, paper.metadata.issn)
        for field, value in patch.model_dump(exclude_unset=True).items():
            if isinstance(value, str):
                value = value.strip() or None
            setattr(paper.metadata, field, value)
            paper.metadata.provenance[field] = Provenance(source="manual", status="confirmed")
        after = (paper.metadata.venue_type, paper.metadata.venue, paper.metadata.issn)
        if before != after:
            # Do not carry an old journal's metric into a different venue.
            paper.metric = JournalMetric(
                status="not_applicable" if after[0] in {"conference", "preprint"} else "missing"
            )

    return repo.update(document_id, mutate)


def update_metric(repo: PaperRepository, document_id: str, patch: MetricInput) -> Document:
    def mutate(document: Document) -> None:
        if document.parsed is None:
            raise AppError("not_parsed", "论文尚未成功解析。", 409)
        metadata = document.parsed.metadata
        if metadata.venue_type != "journal" or not metadata.venue:
            raise AppError("metric_not_applicable", "请先确认期刊名称和出版类型。", 409)
        if not patch.source.strip():
            raise AppError("metric_source_required", "请填写指标来源。")
        document.parsed.metric = JournalMetric(
            year=patch.year,
            value=patch.value,
            source=patch.source.strip(),
            venue=metadata.venue,
            issn=metadata.issn,
            status="available",
            retrieved_at=utc_now(),
        )

    return repo.update(document_id, mutate)
