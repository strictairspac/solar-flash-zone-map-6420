from pydantic import BaseModel, Field, field_validator

from src.domain.scenario import CONFIG
from src.utils.serial import canonical_json


class CaseInput(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=420)
    payload: dict
    # 页段容量在质保批登记时固定（1–256），版本之间不变，保证编号到页号的映射稳定。
    page_size: int = Field(default=CONFIG["default_parameter"], ge=1, le=CONFIG["max_parameter"])

    @field_validator("title")
    @classmethod
    def check_title(cls, title):
        if len(title.strip()) < 2:
            raise ValueError("请输入至少两个字符的名称")
        return title.strip()

    @field_validator("payload")
    @classmethod
    def check_size(cls, payload):
        if len(canonical_json(payload)) > 750_000:
            raise ValueError("样本数据不能超过 750 KB")
        return payload


class CaseEntry(CaseInput):
    id: int
    code: str
    created_by: str
