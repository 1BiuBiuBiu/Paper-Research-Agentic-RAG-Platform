from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Box(BaseModel):
    """PDF points, top-left origin, x increases right and y increases down."""

    left: float
    top: float
    right: float
    bottom: float


class Block(BaseModel):
    id: str
    page: int
    order: int
    kind: str
    bbox: Box
    raw_text: str
    text: str
    excluded_reason: str | None = None
    source_ref: str | None = None
    parent_ref: str | None = None
    heading_level: int | None = None


class PageInfo(BaseModel):
    number: int
    width: float
    height: float
    image: str


class Provenance(BaseModel):
    source: Literal["pdf", "manual", "crossref"] = "pdf"
    block_ids: list[str] = Field(default_factory=list)
    status: Literal["candidate", "confirmed", "missing"] = "candidate"
    updated_at: str = Field(default_factory=utc_now)


class PaperMetadata(BaseModel):
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    publication_year: int | None = None
    doi: str | None = None
    venue_type: Literal["journal", "conference", "preprint", "unknown"] = "unknown"
    venue: str | None = None
    issn: str | None = None
    abstract: str | None = None
    provenance: dict[str, Provenance] = Field(default_factory=dict)


class JournalMetric(BaseModel):
    name: Literal["JIF"] = "JIF"
    year: int | None = Field(default=None, ge=1900, le=2100)
    value: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    source: str | None = None
    venue: str | None = None
    issn: str | None = None
    status: Literal["missing", "available", "not_applicable"] = "missing"
    retrieved_at: str | None = None


class Figure(BaseModel):
    id: str
    page: int
    bbox: Box
    image: str
    label: str | None = None
    caption: str = ""
    caption_block_ids: list[str] = Field(default_factory=list)
    mention_block_ids: list[str] = Field(default_factory=list)
    quality: str = "extracted"
    role: Literal["research", "decoration", "unclassified"] = "unclassified"
    classification_reason: str = "尚未核对图注与版面位置"
    source_ref: str | None = None


class TableCell(BaseModel):
    text: str
    row_start: int
    row_end: int
    col_start: int
    col_end: int
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False
    bbox: Box | None = None


class PaperTable(BaseModel):
    id: str
    source_ref: str
    page: int
    bbox: Box
    image: str | None = None
    label: str | None = None
    caption: str = ""
    caption_block_ids: list[str] = Field(default_factory=list)
    mention_block_ids: list[str] = Field(default_factory=list)
    rows: int
    columns: int
    cells: list[TableCell] = Field(default_factory=list)
    quality: Literal["structured_unverified", "region_only"] = "region_only"
    structure_warnings: list[str] = Field(default_factory=list)
    footnote_block_ids: list[str] = Field(default_factory=list)


class Equation(BaseModel):
    id: str
    block_id: str
    source_ref: str | None = None
    page: int
    bbox: Box
    image: str
    text: str
    latex: str | None = None
    quality: str = "原区域已保留；文字未做数学语义校验，未启用 LaTeX 模型"


class ReadingElement(BaseModel):
    kind: Literal["block", "table", "figure"]
    id: str
    source_ref: str
    page: int


class ParsedPaper(BaseModel):
    parser: str
    pages: list[PageInfo]
    blocks: list[Block]
    figures: list[Figure]
    metadata: PaperMetadata
    metric: JournalMetric = Field(default_factory=JournalMetric)
    warnings: list[str] = Field(default_factory=list)
    structure_version: int = 0
    tables: list[PaperTable] = Field(default_factory=list)
    equations: list[Equation] = Field(default_factory=list)
    reading_order: list[ReadingElement] = Field(default_factory=list)
    exports: dict[str, str] = Field(default_factory=dict)


class Document(BaseModel):
    id: str
    filename: str
    sha256: str
    status: Literal["parsing", "parsed", "failed"]
    created_at: str
    updated_at: str
    error_code: str | None = None
    error_message: str | None = None
    parsed: ParsedPaper | None = None
    is_sample: bool = False
    category_id: str | None = None


class DocumentSummary(BaseModel):
    id: str
    filename: str
    title: str | None
    status: str
    created_at: str
    pages: int
    venue: str | None
    year: int | None
    error_message: str | None
    is_sample: bool = False
    category_id: str | None = None
    authors: list[str] = Field(default_factory=list)


class MetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=1000)
    authors: list[str] = Field(default_factory=list, max_length=100)
    publication_year: int | None = Field(default=None, ge=1500, le=2100)
    doi: str | None = Field(default=None, pattern=r"^10\.\d{4,9}/\S+$", max_length=300)
    venue_type: Literal["journal", "conference", "preprint", "unknown"] = "unknown"
    venue: str | None = Field(default=None, max_length=1000)
    issn: str | None = Field(default=None, pattern=r"^\d{4}-\d{3}[\dXx]$")
    abstract: str | None = Field(default=None, max_length=30000)


class MetricInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int = Field(ge=1900, le=2100)
    value: float = Field(ge=0, le=10000, allow_inf_nan=False)
    source: str = Field(min_length=1, max_length=1000)
