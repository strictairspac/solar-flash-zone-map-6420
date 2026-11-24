from sqlalchemy import JSON, Column, Integer, String, Table
from .base import metadata


cases = Table(
    "cases", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(48), unique=True, nullable=False),
    Column("title", String(120), nullable=False),
    Column("description", String(420), nullable=False),
    Column("payload", JSON, nullable=False),
    # 页段容量（每页闪测条数）在质保批登记时固定，版本之间保持一致，
    # 这样同一板件编号在每个版本里都落在相同页号，更正才能只重建受影响页。
    Column("page_size", Integer, nullable=False, server_default="4"),
    Column("created_by", String(48), nullable=False),
)
