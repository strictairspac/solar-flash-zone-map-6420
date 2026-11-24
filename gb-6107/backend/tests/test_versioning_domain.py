"""纯领域测试：增量重建只动受影响页，未变页不重读功率。"""
import pytest

from src.domain.columnar import build_pages
from src.domain.predicate import Predicate
from src.domain.versioning import (
    baseline_matches, build_correction_plan, build_version_pages, page_index_of,
    run_predicate,
)


def records():
    # 20 条，页容量 4 → 5 页；含两个缺测。
    return [
        ("D3-0101", 40500), ("D3-0102", 40750), ("D3-0103", None), ("D3-0104", 40900),
        ("D3-0105", 41900), ("D3-0106", 42025), ("D3-0107", 42100), ("D3-0108", 42101),
        ("D3-0109", None), ("D3-0110", None), ("D3-0111", None), ("D3-0112", None),
        ("D3-0113", 42400), ("D3-0114", 42500), ("D3-0115", 42520), ("D3-0116", 42600),
        ("D3-0117", 41899), ("D3-0118", 41999), ("D3-0119", None), ("D3-0120", 42410),
    ]


def test_page_index_mapping_is_stable():
    assert page_index_of(0, 4) == 1
    assert page_index_of(3, 4) == 1
    assert page_index_of(4, 4) == 2
    assert page_index_of(19, 4) == 5


def test_first_version_builds_all_pages():
    rows = records()
    revisions, stats = build_version_pages(rows, 4)
    assert len(revisions) == 5
    assert all(rev.rebuilt for rev in revisions)
    # 20 条里有 6 个缺测，非空读数 14 个。
    assert stats == {"rebuilt_pages": 5, "reused_pages": 0, "reread_values": 14}


def test_correction_only_rebuilds_affected_pages():
    rows = records()
    first, _ = build_version_pages(rows, 4)
    previous = {rev.page_index: rev.page for rev in first}

    # 补录第 3 页（D3-0109，物理偏移 8 → 页号 3）和第 5 页（D3-0119，偏移 18 → 页号 5）。
    plan = build_correction_plan(rows, {"D3-0109": 42000, "D3-0119": 42050}, 4)
    assert plan.affected_page_indexes == frozenset({3, 5})

    revisions, stats = build_version_pages(
        plan.records, 4, previous, plan.affected_page_indexes
    )
    by_index = {rev.page_index: rev for rev in revisions}
    assert by_index[3].rebuilt and by_index[5].rebuilt
    assert [idx for idx, rev in by_index.items() if not rev.rebuilt] == [1, 2, 4]
    # 只重建 2 页，复用 3 页；只统计重建页里的非空功率。
    assert stats["rebuilt_pages"] == 2
    assert stats["reused_pages"] == 3
    # 更正后：第 3 页非空 1 个（D3-0109 补录），第 5 页非空 4 个（含补录的 D3-0119）。
    assert stats["reread_values"] == 1 + 4

    # 复用页与上一版是同一个对象，证明没有重读、没有重建。
    assert by_index[1].page is previous[1]
    assert by_index[4].page is previous[4]


def test_two_corrections_same_page_collapse_to_one_rebuild():
    rows = records()
    first, _ = build_version_pages(rows, 4)
    previous = {rev.page_index: rev.page for rev in first}
    # 同页两块板（D3-0105 偏移4、D3-0106 偏移5 都在第 2 页）。
    plan = build_correction_plan(rows, {"D3-0105": 41950, "D3-0106": 41960}, 4)
    assert plan.affected_page_indexes == frozenset({2})
    revisions, stats = build_version_pages(plan.records, 4, previous, plan.affected_page_indexes)
    assert stats["rebuilt_pages"] == 1 and stats["reused_pages"] == 4


def test_unknown_serial_rejected():
    with pytest.raises(ValueError):
        build_correction_plan(records(), {"GHOST-9": 42000}, 4)


def test_correction_changes_zone_map_and_pruning_result():
    rows = records()
    predicate = Predicate("range", 41900, 42100)
    first_pages = build_pages(rows, 4)
    base_evidence, base_matches, _, _ = run_predicate(first_pages, predicate)
    assert base_matches == baseline_matches(rows, predicate)
    # 初始命中：D3-0105(41900)、D3-0106(42025)、D3-0107(42100)、D3-0118(41999)
    assert base_matches == ["D3-0105", "D3-0106", "D3-0107", "D3-0118"]

    # 补录 D3-0109（在全空第 3 页）为 42000，且把 D3-0117(41899) 复测为 42000。
    plan = build_correction_plan(rows, {"D3-0109": 42000, "D3-0117": 42000}, 4)
    first, _ = build_version_pages(rows, 4)
    previous = {rev.page_index: rev.page for rev in first}
    revisions, _stats = build_version_pages(plan.records, 4, previous, plan.affected_page_indexes)
    new_pages = [rev.page for rev in revisions]
    _ev, new_matches, skipped, reads = run_predicate(new_pages, predicate)
    assert new_matches == baseline_matches(plan.records, predicate)
    assert "D3-0109" in new_matches and "D3-0117" in new_matches
    # 第 3 页原来因全空被跳过，补录后必须被扫描。
    assert any(row["页段"] == 3 and row["动作"] == "扫描读数" for row in _ev)


def test_null_predicate_uses_bitmap_after_correction():
    rows = records()
    plan = build_correction_plan(rows, {"D3-0109": 42000}, 4)  # 填补一个缺测
    null_predicate = Predicate("null")
    missing = baseline_matches(plan.records, null_predicate)
    assert "D3-0109" not in missing
    assert "D3-0103" in missing
    first, _ = build_version_pages(rows, 4)
    previous = {rev.page_index: rev.page for rev in first}
    revisions, _ = build_version_pages(plan.records, 4, previous, plan.affected_page_indexes)
    _ev, matches, _s, _r = run_predicate([rev.page for rev in revisions], null_predicate)
    assert matches == missing
