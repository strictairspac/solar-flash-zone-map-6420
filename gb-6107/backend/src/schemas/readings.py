from pydantic import BaseModel, Field

from src.constants.version_state import VersionState


class ReadingValue(BaseModel):
    serial: str = Field(min_length=1, max_length=32)
    # 复测功率：整数厘瓦，None 表示该编号仍缺测（按空值位图处理）。
    power_cw: int | None = Field(default=None, ge=0, le=500_000)


class CorrectionInput(BaseModel):
    readings: list[ReadingValue] = Field(min_length=1, max_length=4096)
    change_note: str = Field(default="", max_length=320)
    # 教学用故障注入：模拟新版页段未齐备即中断，验证旧版仍可接单。
    simulate_abort: bool = False


class VersionEntry(BaseModel):
    id: int
    case_id: int
    version_no: int
    status: VersionState
    page_size: int
    change_note: str | None
    created_by: str
    created_at: int
    activated_at: int | None
    rebuilt_pages: int
    reused_pages: int
    reread_values: int
