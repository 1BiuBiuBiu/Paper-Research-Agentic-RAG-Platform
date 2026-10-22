import unicodedata
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def normalized(cls, value: str) -> str:
        if any(unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("分类名不允许控制字符")
        value = " ".join(unicodedata.normalize("NFKC", value).split())
        if (
            not value
            or len(value) > 80
            or value.casefold() in {"全部论文", "未分类", "all papers", "uncategorized"}
        ):
            raise ValueError("分类名为空或与系统分类重名")
        return value


class Category(BaseModel):
    id: str
    name: str
    archived: bool
    paper_count: int
    created_at: str
    updated_at: str


class MovePapers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[UUID] = Field(min_length=1, max_length=100)
    category_id: UUID | None  # Explicit null means move back to 未分类.
