from sqlalchemy import inspect, text

from src.models import audit, cases, readings, reviews, runs, users  # Register the tables.
from src.models.base import metadata


def _has_column(connection, table_name, column_name):
    columns = {column["name"] for column in inspect(connection).get_columns(table_name)}
    return column_name in columns


def _table_index_names(connection, table_name):
    return {entry["name"] for entry in inspect(connection).get_indexes(table_name)}


def upgrade(connection):
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())

    # 全新数据库：一张表都没有，按当前声明一次性建立全部表（含版本表与唯一约束）。
    if not existing_tables:
        metadata.create_all(connection)
        return

    # 以下为旧库增量升级路径。
    if "cases" in existing_tables and not _has_column(connection, "cases", "page_size"):
        connection.execute(text("ALTER TABLE cases ADD COLUMN page_size INTEGER NOT NULL DEFAULT 4"))

    if "runs" in existing_tables and not _has_column(connection, "runs", "reading_version_id"):
        connection.execute(text("ALTER TABLE runs ADD COLUMN reading_version_id INTEGER"))
        connection.execute(text(
            "CREATE INDEX ix_runs_reading_version_id ON runs (reading_version_id)"
        ))

    if "reading_versions" not in existing_tables:
        metadata.create_all(connection, tables=[
            readings.reading_versions, readings.reading_pages,
        ])
    else:
        if "uq_reading_versions_case_version" not in _table_index_names(connection, "reading_versions"):
            connection.execute(text(
                "CREATE UNIQUE INDEX uq_reading_versions_case_version "
                "ON reading_versions (case_id, version_no)"
            ))
        if "uq_reading_pages_version_index" not in _table_index_names(connection, "reading_pages"):
            connection.execute(text(
                "CREATE UNIQUE INDEX uq_reading_pages_version_index "
                "ON reading_pages (version_id, page_index)"
            ))
