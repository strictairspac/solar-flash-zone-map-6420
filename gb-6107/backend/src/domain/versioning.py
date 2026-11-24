"""读数版本化与页段增量重建。

业务口径（与出货复核题目对应）：

* 同一质保批按板件编号补录/复测功率，不新增板件，只改读数。
* 更正只重建受影响的页段：页段容量在批次生命周期内固定，板件物理次序不变，
  因此同一编号永远落在同一页号；未变页段直接复用上一版本，不重读其功率。
* 新版页段齐备后一次性切换为 active；重建中断则放弃新版本，active 旧版不动，
  中断后续跑期间新批次仍读取旧版（由 services.readings 的单事务保证）。
* 出货抽检在入队时钉住当时的 active 版本，按该版本的 Zone Map / 空值位图筛选。
"""
from dataclasses import dataclass

from .columnar import Page, build_page
from .predicate import Predicate


@dataclass(frozen=True)
class PageRevision:
    page_index: int            # 从 1 开始的物理页号
    page: Page
    rebuilt: bool              # True=本次更正重建并重读；False=沿用上版页段


@dataclass(frozen=True)
class CorrectionPlan:
    records: tuple[tuple[str, int | None], ...]   # 更正后的完整记录，保持原物理次序
    affected_page_indexes: frozenset[int]
    updates: dict[str, int | None]


def page_index_of(row_offset, page_size):
    """物理行偏移（从 0 起）对应的页号（从 1 起）。"""
    return row_offset // page_size + 1


def build_correction_plan(records, updates, page_size):
    """根据按编号的复测读数生成更正计划，并标出受影响页号。

    records : 上一版本的完整记录 [(serial, power_cw), ...]，物理次序即板件次序。
    updates : {serial: 新读数或 None}，只允许补录已有编号，不能新增板件。
    """
    if not isinstance(updates, dict) or not updates:
        raise ValueError("至少提供一条按板件编号的复测读数")
    position = {}
    for offset, (serial, _power) in enumerate(records):
        position[serial] = offset
    unknown = sorted(serial for serial in updates if serial not in position)
    if unknown:
        raise ValueError(f"板件编号不属于本质保批：{'、'.join(unknown)}")

    affected = set()
    next_records = list(records)
    for serial, reading in updates.items():
        offset = position[serial]
        next_records[offset] = (serial, reading)
        affected.add(page_index_of(offset, page_size))
    return CorrectionPlan(tuple(next_records), frozenset(affected), dict(updates))


def build_version_pages(records, page_size, previous_pages=None, affected_indexes=None):
    """构建一个版本的全部页段，未受影响页直接复用，不重读其功率。

    previous_pages    : 上一版本 {页号: Page}；为空（首版）时全部新建。
    affected_indexes  : 本次受影响页号集合；首版传 None 表示全部重建。
    返回 (PageRevision 列表, 统计)。
    """
    total = len(records)
    revisions = []
    rebuilt = reused = reread = 0
    first = previous_pages is None or affected_indexes is None
    affected = None if first else set(affected_indexes)
    for page_no, start in enumerate(range(0, total, page_size), start=1):
        if not first and page_no not in affected and page_no in previous_pages:
            page = previous_pages[page_no]
            reused += 1
            revisions.append(PageRevision(page_no, page, rebuilt=False))
            continue
        page = build_page(records[start:start + page_size])
        rebuilt += 1
        # 只对重建页重读功率；复用页完全不读。统计重建页内的有效（非空）读数，
        # 空值走位图、不计入读取读数，与谓词试跑的口径一致。
        reread += page.power_zone.valid_mask.bit_count()
        revisions.append(PageRevision(page_no, page, rebuilt=True))
    stats = {"rebuilt_pages": rebuilt, "reused_pages": reused, "reread_values": reread}
    return revisions, stats


def run_predicate(pages, predicate):
    """对一个不可变版本的页段执行谓词试跑，返回与旧 scenario.run 同构的证据。"""
    evidence = []
    matches = []
    skipped = reads = 0
    for page_index, page in enumerate(pages, 1):
        zone = page.power_zone
        may_match = predicate.page_may_match(zone)
        offsets = predicate.select_offsets(page.power, zone) if may_match else []
        if not may_match:
            skipped += 1
        if may_match and predicate.mode == "range":
            reads += zone.valid_mask.bit_count()
        serials = [page.serials[offset] for offset in offsets]
        matches.extend(serials)
        evidence.append({
            "页段": page_index, "最小厘瓦": zone.minimum, "最大厘瓦": zone.maximum,
            "空值行": zone.nulls, "有效位图": format(zone.valid_mask, f"0{zone.count}b"),
            "动作": ("扫描读数" if predicate.mode == "range" else "扫描位图") if may_match else "跳过",
            "命中数": len(offsets), "板件": "、".join(serials),
        })
    return evidence, matches, skipped, reads


def baseline_matches(records, predicate):
    return [serial for serial, reading in records if predicate.matches_row(reading)]
