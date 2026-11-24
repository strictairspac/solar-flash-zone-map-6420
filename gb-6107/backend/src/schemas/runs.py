from pydantic import BaseModel, Field
from src.constants.job_state import JobState


class RunInput(BaseModel):
    # 兼容旧入口；实际页容量以质保批登记的 page_size 快照为准，入队时由服务端钉住。
    parameter: int = Field(default=4, strict=True, ge=1)


class RunEntry(BaseModel):
    id: int
    case_id: int
    case_title: str
    reading_version_id: int | None
    # 入队时命中的读数版本号与当时状态，用于回执展示与历史追溯。
    reading_version_no: int | None = None
    reading_version_status: str | None = None
    status: JobState
    parameter: int
    actor: str
    created_at: int
    finished_at: int | None
    result: dict | None
    failure: str | None
    digest: str | None
