# 验收记录 · 闪测页段出货复核（读数版本化）

验收日期：2026-11-24
验收方式：本机无 Docker/PostgreSQL，使用 SQLAlchemy + 临时 SQLite 跑后端纯 Python 测试；
前端以 `node --check` 做语法校验。业务代码仅使用 SQLAlchemy 通用层，PostgreSQL 行为与测试一致，
`FOR UPDATE/SKIP LOCKED` 在 SQLite 被忽略，单连接测试不涉及并发竞争。

## 复演命令

```sh
cd backend
python -m pytest tests/ -q
# 18 passed
```

## 题目硬约束与对应验证

| 题目要求 | 实现落点 | 覆盖测试 |
|---|---|---|
| 同一质保批按板件编号补录复测功率 | `POST /cases/{id}/corrections`，只允许已有编号、不新增板件 | `test_unknown_serial_rejected`、`test_correction_unknown_serial_is_422` |
| 更正只重建受影响页，未变页不重读功率 | 页容量登记后固定，编号→页号映射不变；`build_version_pages` 复用未变页对象 | `test_correction_only_rebuilds_affected_pages`、`test_two_corrections_same_page_collapse_to_one_rebuild` |
| 新版页段齐备后一次性切换 | 单事务：`building` → 全部页落库 → `active`；中断转 `abandoned` 且无页段 | `test_correction_incremental_and_atomic_switch`、`test_aborted_correction_keeps_old_version_serving`、`test_abort_keeps_serving_old_version` |
| 中断续跑期间新批次仍用旧版 | active 旧版不被触碰；版本号全局递增、放弃不复用 | `test_aborted_correction_keeps_old_version_serving` |
| 抽检按排队时读数版本做范围/缺测筛选 | 入队钉住 `reading_version_id`，worker 按该版本 Zone Map/位图执行 | `test_run_pins_version_at_enqueue_and_ignores_later_switch`、`test_null_predicate_uses_bitmap_after_correction` |
| 旧批次晚完成不能盖掉新版结果 | 每批结果与 SHA-256 独立固化，无单一可变结果槽位 | `test_late_old_batch_cannot_overwrite_new_receipt`、`test_full_flow_correction_pin_and_immutable_receipt` |
| 历史回执保留当时命中板件与跳页依据 | 证据 `rows` 含每页动作（扫描/跳过）、位图、命中板件，并附 `reading_version` | HTTP 端到端用例断言两版 digest 不同且历史导出字节不变 |
| 旧库平滑升级 | v001→v002 增量 DDL、加列、建版本表、幂等、可回填首版 | `test_upgrade_from_v001_is_idempotent_and_backfillable` |

## 端到端冒烟（SQLite，脚本驱动）

以演示批 SL-D3-1124（20 板、页容量 4、共 5 页）执行：

1. 首启动建库并 seed，为演示批回填 v1（5 页全部首次构建）。
2. 入队批次 A，钉住 v1。
3. 补录 `D3-0109`（第 3 页）、`D3-0119`（第 5 页）：v2 仅 **重建 2 页、复用 3 页、重读 5 个非空读数**，一次切换 active。
4. 故障注入一次更正：v3 转 `abandoned`、页段数为 0，active 仍是 v2。
5. 入队批次 B，钉住 v2。
6. worker 依次执行：A 的回执基于 v1（不含补录的 D3-0109），B 基于 v2（含 D3-0109）；两份 digest 不同。
7. 再次 seed 幂等，版本数不增长。

结果与预期完全一致（脚本输出 `SMOKE OK`）。

## 测试清单

- `tests/test_versioning_domain.py`：页号映射、首版构建、增量重建、同页合并、未知编号、范围/空值谓词与基线一致性。
- `tests/test_readings_service.py`：首版、原子切换、中断隔离、入队钉版、晚到回执不覆盖、422 校验、null 谓词。
- `tests/test_migration_v002.py`：v001 旧结构增量升级、幂等、唯一约束、回填首版后可接单。
- `tests/test_http_flow.py`：登录鉴权、角色权限（401/403）、更正与版本接口、排队快照、回执导出不可变。

## 范围与限制

- 未改动 PostgreSQL 执行器；页段仍是 Python 教学用内存列页，版本页段持久化在 `reading_pages` 表。
- 前端在本环境仅做语法与逻辑核对，未起浏览器；接口契约由后端 HTTP 测试覆盖。
- 生产部署仍按 README 用 `docker compose up -d --build --wait`，启动时自动执行 v002 迁移并幂等回填。
