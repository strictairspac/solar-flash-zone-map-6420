from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("case_id", ForeignKey("cases.id"), nullable=False, index=True),
    # 入队时钉住当时的生效读数版本；之后即便有更正切换新版，本批次仍按此版本筛选。
    Column("reading_version_id", ForeignKey("reading_versions.id"), nullable=True, index=True),
    Column("status", String(16), nullable=False, index=True),
    # 入队时快照的页段容量，与 reading_version_id 一起固定本批次的读数视图。
    Column("parameter", Integer, nullable=False),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("finished_at", BigInteger, nullable=True),
    Column("result", JSON(none_as_null=True), nullable=True),
    Column("failure", String(320), nullable=True),
    Column("digest", String(64), nullable=True),
)
