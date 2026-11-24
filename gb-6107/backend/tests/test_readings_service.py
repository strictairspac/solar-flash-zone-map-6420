"""服务层集成测试：版本原子切换、中断隔离、排队快照、回执不可覆盖。"""
import pytest

from src.constants.job_state import JobState
from src.constants.version_state import VersionState
from src.domain import scenario
from src.services import cases as case_service
from src.services import readings, runs as run_service
from src.services.execution import service_one


def _make_case(db, payload, page_size=4, actor="operator"):
    from src.schemas.cases import CaseInput
    form = CaseInput(title="测试质保批", payload=payload, page_size=page_size)
    return case_service.create(db, form, actor)


def _enqueue_and_drain():
    # worker 每次开新事务；service_one 内部用全局 engine（测试已替换为 SQLite）。
    while service_one():
        pass


def test_create_case_builds_active_v1(db, case_payload):
    case = _make_case(db, case_payload)
    version = readings.get_active_version(db, case["id"])
    assert version["version_no"] == 1
    assert version["status"] == VersionState.ACTIVE
    pages = readings.load_version_pages(db, version["id"])
    assert sorted(pages) == [1, 2, 3, 4, 5]
    assert version["reused_pages"] == 0 and version["rebuilt_pages"] == 5


def test_correction_incremental_and_atomic_switch(db, case_payload):
    case = _make_case(db, case_payload)
    v1 = readings.get_active_version(db, case["id"])

    version = readings.submit_correction(
        db, case["id"], {"D3-0109": 42000, "D3-0119": 42050}, "补录复测", "operator"
    )
    assert version["version_no"] == 2
    assert version["status"] == VersionState.ACTIVE
    assert version["rebuilt_pages"] == 2
    assert version["reused_pages"] == 3

    active = readings.get_active_version(db, case["id"])
    assert active["id"] == version["id"]
    # v1 原样保留为历史，页段不被删除。
    old = readings.get_version(db, v1["id"])
    assert old["status"] == VersionState.ACTIVE  # v1 行状态不再变更，仅不再是最新
    assert len(readings.load_version_pages(db, v1["id"])) == 5
    # 最新 active 取的是版本号最大者，因此 v1 不会再被选为当前版本。
    listing = readings.list_versions(db, case["id"])
    assert [v["version_no"] for v in listing] == [2, 1]


def test_aborted_correction_keeps_old_version_serving(db, case_payload):
    case = _make_case(db, case_payload)
    v1 = readings.get_active_version(db, case["id"])

    aborted = readings.submit_correction(
        db, case["id"], {"D3-0109": 42000}, "中断演练", "operator", simulate_abort=True
    )
    assert aborted["status"] == VersionState.ABANDONED
    # 中断后当前生效版本仍是 v1，新批次继续用旧版。
    active = readings.get_active_version(db, case["id"])
    assert active["id"] == v1["id"]
    # abandoned 版本没有页段（未齐备），不能被抽检取用。
    assert readings.load_version_pages(db, aborted["id"]) == {}

    # 中断后仍可正常补一次成功的更正；被放弃的 v2 版本号不复用，新版是 v3。
    v3 = readings.submit_correction(db, case["id"], {"D3-0109": 42000}, "重新补录", "operator")
    assert v3["version_no"] == 3 and v3["status"] == VersionState.ACTIVE
    assert readings.get_active_version(db, case["id"])["id"] == v3["id"]
    # 版本轨迹完整保留：v1 生效、v2 放弃、v3 生效。
    assert [v["version_no"] for v in readings.list_versions(db, case["id"])] == [3, 2, 1]


def test_run_pins_version_at_enqueue_and_ignores_later_switch(db, engine, case_payload):
    case = _make_case(db, case_payload)
    case_id = case["id"]

    # 1) 先入队一个批次（钉住 v1），但暂不执行。
    run_old = run_service.enqueue(db, case_id, 4, "operator")
    assert run_old["reading_version_id"] == readings.get_active_version(db, case_id)["id"]
    assert run_old["reading_version_no"] == 1

    # 2) 在它排队期间更正并切换到 v2。
    readings.submit_correction(db, case_id, {"D3-0109": 42000}, "排队期间补录", "operator")
    v2 = readings.get_active_version(db, case_id)
    assert v2["version_no"] == 2

    # 3) 再入队一个批次，应钉住 v2。
    run_new = run_service.enqueue(db, case_id, 4, "operator")
    assert run_new["reading_version_id"] == v2["id"]
    assert run_new["reading_version_no"] == 2

    # 4) worker 依次处理：旧批次仍按 v1 筛选（不含补录的 D3-0109）。
    _enqueue_and_drain()

    with engine.connect() as check:
        old_done = run_service.get(check, run_old["id"])
        new_done = run_service.get(check, run_new["id"])
        assert old_done["status"] == JobState.DONE and new_done["status"] == JobState.DONE
        old_serials = {s for row in old_done["result"]["rows"] for s in (row["板件"].split("、") if row["板件"] else [])}
        new_serials = {s for row in new_done["result"]["rows"] for s in (row["板件"].split("、") if row["板件"] else [])}
        assert "D3-0109" not in old_serials          # 旧批次用排队时的旧版读数
        assert "D3-0109" in new_serials              # 新批次用新版读数
        # 回执里固化了命中时的版本依据，晚完成也不会被改写。
        assert old_done["result"]["reading_version"]["version_no"] == 1
        assert new_done["result"]["reading_version"]["version_no"] == 2
        assert old_done["digest"] != new_done["digest"]


def test_late_old_batch_cannot_overwrite_new_receipt(db, engine, case_payload):
    case_id = _make_case(db, case_payload)["id"]
    first = run_service.enqueue(db, case_id, 4, "operator")     # v1
    readings.submit_correction(db, case_id, {"D3-0109": 42000}, "切换新版", "operator")
    second = run_service.enqueue(db, case_id, 4, "operator")    # v2
    _enqueue_and_drain()

    with engine.connect() as check:
        r1, r2 = run_service.get(check, first["id"]), run_service.get(check, second["id"])
        # 两条回执各自独立、不可变；不存在“当前结果被晚到批次覆盖”的单一可变槽位。
        assert r1["id"] != r2["id"]
        assert r1["reading_version_no"] == 1 and r2["reading_version_no"] == 2
        assert r1["result"]["metrics"]["读数版本"] == 1
        assert r2["result"]["metrics"]["读数版本"] == 2
        # 列表里两条都保留，按时间倒序，历史回执可随时回看。
        entries = run_service.list_entries(check)
        ids = {entry["id"] for entry in entries}
        assert {first["id"], second["id"]} <= ids


def test_chained_corrections_build_on_latest_version(db, case_payload):
    """连续两次更正：v3 必须基于 v2 构建，未变页沿用上一版。"""
    case_id = _make_case(db, case_payload)["id"]
    v2 = readings.submit_correction(db, case_id, {"D3-0109": 42000}, "第一次", "operator")
    v3 = readings.submit_correction(db, case_id, {"D3-0103": 41950}, "第二次", "operator")
    assert v3["version_no"] == 3 and v3["status"] == VersionState.ACTIVE
    # 两次更正分别落在第 3 页和第 1 页；v3 只重建第 1 页，其余（含 v2 重建过的第 3 页）复用。
    assert v3["rebuilt_pages"] == 1 and v3["reused_pages"] == 4
    active = readings.get_active_version(db, case_id)
    assert active["id"] == v3["id"]
    # v3 的记录同时包含两次补录，证明它基于 v2 而非 v1。
    records = {row["serial"]: row["power_cw"] for row in active["records"]}
    assert records["D3-0109"] == 42000 and records["D3-0103"] == 41950


def test_correction_unknown_serial_is_422(db, case_payload):
    from fastapi import HTTPException
    case_id = _make_case(db, case_payload)["id"]
    with pytest.raises(HTTPException) as exc:
        readings.submit_correction(db, case_id, {"NOPE-1": 42000}, "", "operator")
    assert exc.value.status_code == 422


def test_range_and_null_both_validate_against_baseline(db, engine):
    from src.domain.fixtures import FIXTURES
    payload = FIXTURES[1]["payload"]   # null 谓词样本
    case = _make_case(db, payload)
    run = run_service.enqueue(db, case["id"], 4, "operator")
    _enqueue_and_drain()
    with engine.connect() as check:
        done = run_service.get(check, run["id"])
        assert done["status"] == JobState.DONE
        null_rows = [row for row in done["result"]["rows"] if row["命中数"]]
        hit = {s for row in null_rows for s in row["板件"].split("、")}
        assert hit == {"A2-0105", "A2-0107"}   # 两个缺测；零读数 A2-0103 不算缺测
