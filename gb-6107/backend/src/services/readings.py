"""读数版本的持久化编排：首版建立、按编号更正的增量重建与原子切换。"""
from time import time

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.constants.version_state import VersionState
from src.domain.columnar import Page, Zone
from src.domain.versioning import build_correction_plan, build_version_pages
from src.models.cases import cases
from src.models.readings import reading_pages, reading_versions
from . import audit


def _page_from_row(row):
    zone = Zone(
        minimum=row["minimum"], maximum=row["maximum"],
        valid_mask=row["valid_mask"], count=row["row_count"],
    )
    return Page(tuple(row["serials"]), tuple(row["power"]), zone)


def _load_pages(db, version_id):
    rows = db.execute(
        select(reading_pages).where(reading_pages.c.version_id == version_id)
        .order_by(reading_pages.c.page_index)
    ).mappings().all()
    return {row["page_index"]: _page_from_row(row) for row in rows}


def get_active_version(db, case_id):
    """返回质保批当前生效版本（版本号最大的 active）。"""
    row = db.execute(
        select(reading_versions)
        .where(reading_versions.c.case_id == case_id,
               reading_versions.c.status == VersionState.ACTIVE)
        .order_by(reading_versions.c.version_no.desc()).limit(1)
    ).mappings().first()
    if row is None:
        raise HTTPException(409, "质保批还没有生效的读数版本")
    return dict(row)


def get_version(db, version_id):
    row = db.execute(
        select(reading_versions).where(reading_versions.c.id == version_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "读数版本不存在")
    return dict(row)


def list_versions(db, case_id):
    rows = db.execute(
        select(reading_versions)
        .where(reading_versions.c.case_id == case_id)
        .order_by(reading_versions.c.version_no.desc())
    ).mappings().all()
    return [dict(row) for row in rows]


def load_version_pages(db, version_id):
    return _load_pages(db, version_id)


def _persist_pages(db, version_id, revisions):
    for revision in revisions:
        page = revision.page
        zone = page.power_zone
        db.execute(reading_pages.insert().values(
            version_id=version_id, page_index=revision.page_index,
            serials=list(page.serials), power=[p for p in page.power],
            minimum=zone.minimum, maximum=zone.maximum,
            valid_mask=zone.valid_mask, row_count=zone.count,
        ))


def create_first_version(db, case_id, records, page_size, actor):
    """登记质保批时建立 v1，全部页段首次构建并直接生效。"""
    now = int(time())
    result = db.execute(reading_versions.insert().values(
        case_id=case_id, version_no=1, status=VersionState.ACTIVE, page_size=page_size,
        records=[dict(serial=s, power_cw=p) for s, p in records],
        change_note="首次闪测交班", created_by=actor, created_at=now, activated_at=now,
    ))
    version_id = result.inserted_primary_key[0]
    revisions, stats = build_version_pages(records, page_size)
    _persist_pages(db, version_id, revisions)
    db.execute(reading_versions.update().where(reading_versions.c.id == version_id).values(**stats))
    return version_id


def _abandon(db, version_id):
    """重建中断：把未完成版本标记为 abandoned，绝不触碰 active 旧版。"""
    db.execute(reading_versions.update().where(reading_versions.c.id == version_id).values(
        status=VersionState.ABANDONED))


def submit_correction(db, case_id, updates, note, actor, simulate_abort=False):
    """按板件编号补录复测功率：只重建受影响页，齐备后一次性切换。

    整段在一个数据库事务内：任何一步失败/中断，事务回滚或版本转 abandoned，
    active 旧版本保持不变，中断后续跑期间新批次仍用旧版。
    """
    case = db.execute(
        select(cases).where(cases.c.id == case_id).with_for_update()
    ).mappings().first()
    if case is None:
        raise HTTPException(404, "资料不存在")
    page_size = case["page_size"]

    # 持有所属质保批的行锁：同一批的更正被串行化，后到者一定在前者提交后才读取，
    # 因此拿到的必是最新 active 版本，不会基于过期页段构建。
    active = get_active_version(db, case_id)
    prior_records = [(row["serial"], row["power_cw"]) for row in active["records"]]
    try:
        plan = build_correction_plan(prior_records, updates, page_size)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    now = int(time())
    # 版本号对该质保批全局递增：即便上一次重建被放弃（abandoned），也不复用其版本号，
    # 避免与历史版本撞号，并保留完整的重建/中断轨迹。
    max_no = db.scalar(
        select(func.max(reading_versions.c.version_no))
        .where(reading_versions.c.case_id == case_id)
    ) or 0
    next_no = max_no + 1
    try:
        result = db.execute(reading_versions.insert().values(
            case_id=case_id, version_no=next_no, status=VersionState.BUILDING,
            page_size=page_size,
            records=[dict(serial=s, power_cw=p) for s, p in plan.records],
            change_note=note or f"按板件编号补录 {len(updates)} 条复测功率",
            created_by=actor, created_at=now,
        ))
    except IntegrityError as exc:
        raise HTTPException(409, "该质保批已有同名版本在重建，请稍后再试") from exc
    new_id = result.inserted_primary_key[0]

    previous_pages = _load_pages(db, active["id"])
    revisions, stats = build_version_pages(
        plan.records, page_size, previous_pages, plan.affected_page_indexes
    )

    if simulate_abort:
        # 教学用故障注入：模拟新版页段尚未齐备时中断，验证旧版仍可接单。
        _abandon(db, new_id)
        audit.record(db, "abort_correction", actor, "reading_version", new_id)
        return get_version(db, new_id)

    try:
        _persist_pages(db, new_id, revisions)
    except Exception:
        _abandon(db, new_id)
        raise

    # 全部新页段落库后才做一次性切换：旧版转历史、新版转 active。
    db.execute(reading_versions.update().where(reading_versions.c.id == new_id).values(
        status=VersionState.ACTIVE, activated_at=int(time()), **stats))
    audit.record(db, "correct_reading", actor, "reading_version", new_id)
    return get_version(db, new_id)
