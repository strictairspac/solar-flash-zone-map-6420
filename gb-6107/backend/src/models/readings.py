from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table, UniqueConstraint
from .base import metadata


reading_versions = Table(
    "reading_versions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
    Column("version_no", Integer, nullable=False),
    Column("status", String(16), nullable=False, index=True),
    Column("page_size", Integer, nullable=False),
    # 该版本完整的板件读数（serial -> 厘瓦或 None），作为逐行基线和下一次更正的起点。
    Column("records", JSON(none_as_null=True), nullable=False),
    Column("change_note", String(320), nullable=True),
    Column("created_by", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("activated_at", BigInteger, nullable=True),
    Column("rebuilt_pages", Integer, nullable=False, server_default="0"),
    Column("reused_pages", Integer, nullable=False, server_default="0"),
    Column("reread_values", Integer, nullable=False, server_default="0"),
    UniqueConstraint("case_id", "version_no", name="uq_reading_versions_case_version"),
)


reading_pages = Table(
    "reading_pages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # 页段属于某个不可变版本；切换版本后旧版页段原样保留，供历史回执复演。
    Column("version_id", ForeignKey("reading_versions.id"), nullable=False, index=True),
    Column("page_index", Integer, nullable=False),
    Column("serials", JSON(none_as_null=True), nullable=False),
    Column("power", JSON(none_as_null=True), nullable=False),
    Column("minimum", Integer, nullable=True),
    Column("maximum", Integer, nullable=True),
    Column("valid_mask", BigInteger, nullable=False),
    Column("row_count", Integer, nullable=False),
    UniqueConstraint("version_id", "page_index", name="uq_reading_pages_version_index"),
)
