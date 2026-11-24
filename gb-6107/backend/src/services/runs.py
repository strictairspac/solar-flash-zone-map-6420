from time import time

from fastapi import HTTPException
from sqlalchemy import insert, select

from src.constants.job_state import JobState
from src.models.cases import cases
from src.models.readings import reading_versions
from src.models.runs import runs
from . import audit, cases as case_service, readings


def query():
    return (
        select(runs, cases.c.title.label("case_title"),
               reading_versions.c.version_no.label("reading_version_no"),
               reading_versions.c.status.label("reading_version_status"))
        .join(cases, cases.c.id == runs.c.case_id)
        .outerjoin(reading_versions, reading_versions.c.id == runs.c.reading_version_id)
    )


def get(db, run_id):
    run = db.execute(query().where(runs.c.id == run_id)).mappings().first()
    if run is None:
        raise HTTPException(404, "执行批次不存在")
    return dict(run)


def list_entries(db):
    return [dict(row) for row in db.execute(query().order_by(runs.c.id.desc()).limit(100)).mappings()]


def enqueue(db, case_id, parameter, actor):
    case = case_service.get(db, case_id)
    # 出货抽检照常接单：以质保批固定页容量为准，并钉住入队时的生效读数版本。
    # 客户端传入的 parameter 仅用于兼容旧入口，实际以批次页容量快照。
    version = readings.get_active_version(db, case_id)
    change = db.execute(insert(runs).values(
        case_id=case_id, reading_version_id=version["id"], status=JobState.QUEUED,
        parameter=case["page_size"], actor=actor, created_at=int(time()),
    ))
    run_id = change.inserted_primary_key[0]
    audit.record(db, "enqueue", actor, "run", run_id)
    return get(db, run_id)
