import json
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from migrations import v002_versioned_readings
from src.config.resources import engine
from src.config.settings import DEMO_PASSWORD
from src.constants.roles import Role
from src.constants.version_state import VersionState
from src.domain import scenario
from src.domain.scenario import CONFIG, FIXTURES
from src.models.cases import cases
from src.models.readings import reading_versions
from src.models.users import users
from src.services import readings
from src.utils.passwords import hash_password


def insert_new(db, table, key, values):
    dialect = db.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(index_elements=[key])
    elif dialect == "mysql":
        stmt = mysql_insert(table).values(**values).on_duplicate_key_update(**{key: table.c[key]})
    else:  # SQLite 等：先查后插，保持幂等。
        existing = db.execute(select(table.c[key]).where(table.c[key] == values[key])).first()
        if existing is not None:
            return
        stmt = table.insert().values(**values)
    db.execute(stmt)


def setup_schema():
    with engine.begin() as db:
        # v001 声明式建库（全新库即含全部最新表结构），v002 负责旧库增量升级。
        v002_versioned_readings.upgrade(db)


def _ensure_first_version(db, case_row):
    """幂等补建首版：演示资料可能在版本功能上线前就已存在。"""
    existing = db.execute(
        select(func.count()).select_from(reading_versions)
        .where(reading_versions.c.case_id == case_row["id"])
    ).scalar()
    if existing:
        return False
    records, _predicate = scenario.normalize(case_row["payload"])
    readings.create_first_version(
        db, case_row["id"], records, case_row["page_size"], "seed"
    )
    return True


def seed():
    with engine.begin() as db:
        existing_users = set(db.execute(select(users.c.username)).scalars())
        for role in Role:
            if role.value not in existing_users:
                insert_new(db, users, "username", {
                    "username": role.value, "role": role.value, "password_hash": hash_password(DEMO_PASSWORD),
                })
        for fixture in FIXTURES:
            insert_new(db, cases, "code", {
                **fixture, "page_size": CONFIG["default_parameter"], "created_by": "seed",
            })
        seeded_cases = db.execute(select(cases)).mappings().all()
        for case_row in seeded_cases:
            _ensure_first_version(db, case_row)
        return {
            table.name: db.scalar(select(func.count()).select_from(table))
            for table in (users, cases, reading_versions)
        }


if __name__ == "__main__":
    setup_schema()
    print(json.dumps(seed(), sort_keys=True))
