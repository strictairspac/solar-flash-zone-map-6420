"""验证 v001 旧库到 v002 的增量升级路径（幂等、可回填、不丢数据）。"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool


# 与 v001_workbench 时代等价的最小旧结构（不含 page_size、版本表）。
V001_DDL = [
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(48), role VARCHAR(16), password_hash VARCHAR(128))",
    "CREATE TABLE cases (id INTEGER PRIMARY KEY, code VARCHAR(48), title VARCHAR(120), "
    "description VARCHAR(420), payload JSON, created_by VARCHAR(48))",
    "CREATE TABLE runs (id INTEGER PRIMARY KEY, case_id INTEGER, status VARCHAR(16), "
    "parameter INTEGER, actor VARCHAR(48), created_at BIGINT, finished_at BIGINT, "
    "result JSON, failure VARCHAR(320), digest VARCHAR(64))",
    "CREATE TABLE reviews (run_id INTEGER PRIMARY KEY, actor VARCHAR(48), note VARCHAR(500), created_at BIGINT)",
    "CREATE TABLE audit_events (id INTEGER PRIMARY KEY, action VARCHAR(32), actor VARCHAR(48), "
    "entity VARCHAR(24), entity_id INTEGER, created_at BIGINT)",
]


def _build_legacy_engine():
    engine = create_engine(
        "sqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    with engine.begin() as db:
        for ddl in V001_DDL:
            db.execute(text(ddl))
        db.execute(text(
            "INSERT INTO cases (code, title, description, payload, created_by) "
            "VALUES ('CASE-OLD', '旧批', '', "
            "'{\"test_records\": [{\"serial\": \"X-1\", \"power_cw\": 100}], "
            "\"predicate\": {\"mode\": \"null\"}}', 'seed')"
        ))
        db.execute(text(
            "INSERT INTO runs (case_id, status, parameter, actor, created_at) "
            "VALUES (1, 'done', 4, 'op', 0)"
        ))
    return engine


def test_upgrade_from_v001_is_idempotent_and_backfillable():
    engine = _build_legacy_engine()
    from migrations.v002_versioned_readings import upgrade
    with engine.begin() as db:
        upgrade(db)
    with engine.begin() as db:
        upgrade(db)  # 第二遍必须无副作用。

    inspector = inspect(engine)
    case_cols = {c["name"] for c in inspector.get_columns("cases")}
    run_cols = {c["name"] for c in inspector.get_columns("runs")}
    assert "page_size" in case_cols
    assert "reading_version_id" in run_cols
    assert {"reading_versions", "reading_pages"} <= set(inspector.get_table_names())
    # 唯一约束（在 SQLite 体现为自动索引）已建立。
    index_names = {idx["name"] for idx in inspector.get_indexes("reading_versions")}
    assert "uq_reading_versions_case_version" in index_names

    with engine.connect() as db:
        assert db.execute(text("SELECT page_size FROM cases WHERE id = 1")).scalar() == 4
        assert db.execute(text("SELECT reading_version_id FROM runs WHERE id = 1")).scalar() is None

    # 回填首版后旧批即可按新版本机制接单。
    import json
    from src.domain import scenario
    from src.services import readings
    conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    case_row = conn.execute(text("SELECT * FROM cases WHERE id = 1")).mappings().first()
    payload = case_row["payload"]
    if isinstance(payload, str):  # SQLite 不原生解析 JSON，PostgreSQL 会返回对象。
        payload = json.loads(payload)
    records, _ = scenario.normalize(payload)
    readings.create_first_version(conn, 1, records, 4, "seed")
    active = readings.get_active_version(conn, 1)
    assert active["version_no"] == 1
    assert len(readings.load_version_pages(conn, active["id"])) == 1
    engine.dispose()
