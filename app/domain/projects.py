from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.agent import ConversationInput


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("项目名称无效")
        return value


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    project_id: UUID | None = None

    @model_validator(mode="after")
    def explicit_update(self) -> "ConversationUpdate":
        if not self.model_fields_set:
            raise ValueError("请选择修改内容")
        if "title" in self.model_fields_set:
            if self.title is None:
                raise ValueError("对话名称不能为空")
            self.title = ConversationInput(title=self.title).title
        return self


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=160)
