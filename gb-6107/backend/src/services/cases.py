from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import insert, select

from src.domain import scenario
from src.models.cases import cases
from . import readings
from .audit import record


def get(db, case_id):
    entry = db.execute(select(cases).where(cases.c.id == case_id)).mappings().first()
    if entry is None:
        raise HTTPException(404, "资料不存在")
    return dict(entry)


def list_entries(db):
    return [dict(row) for row in db.execute(select(cases).order_by(cases.c.id.desc()).limit(100)).mappings()]


def create(db, data, actor):
    validator = getattr(scenario, "validate", None)
    if validator is not None:
        try:
            validator(data.payload)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    records, _predicate = scenario.normalize(data.payload)
    page_size = data.page_size
    change = db.execute(insert(cases).values(
        title=data.title, description=data.description, payload=data.payload,
        code=f"CASE-{uuid4().hex[:12].upper()}", page_size=page_size, created_by=actor,
    ))
    case_id = change.inserted_primary_key[0]
    # 首次闪测交班即建立 v1 页段版本，后续抽检和更正都基于它。
    readings.create_first_version(db, case_id, records, page_size, actor)
    record(db, "create_case", actor, "case", case_id)
    return get(db, case_id)
