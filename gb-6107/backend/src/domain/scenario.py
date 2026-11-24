import re

from .fixtures import FIXTURES
from .predicate import Predicate
from .versioning import baseline_matches, run_predicate

CONFIG = {
    "title": "炙川光伏·闪测页段出货复核",
    "team": "炙川光伏 10 人闪测质保与出货验收班",
    "description": "用页段瓦数范围和空值位图抽检待出货序号，交班时说明跳过哪些数据页。",
    "direction": "列式页段裁剪与谓词下推",
    "mechanism": "精确整数瓦数列、Zone Map 极值和空值位图裁剪，结果与逐行基线核对。",
    "modules": ["闪测交班", "试板质保批", "谓词试跑", "裁剪回执"],
    "unit": "闪测质保批",
    "max_parameter": 256,
    "parameter_label": "每页闪测条数（1–256）",
    "default_parameter": 4,
    "facts": ["跳过页段", "命中试板", "读取读数"],
    "palette": 7,
    "fixture": "SL-D3-1124 / 419.00–421.00 W",
}

SERIAL_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")


def _check_reading(reading):
    if reading is not None and (type(reading) is not int or not 0 <= reading <= 500000):
        raise ValueError("功率需为空或在 0 至 500000 之间的整数厘瓦")


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError("输入需为对象")
    records = payload.get("test_records")
    if not isinstance(records, list) or not 1 <= len(records) <= 4096:
        raise ValueError("test_records 需有 1 至 4096 条闪测记录")
    rows = []
    seen = set()
    for row in records:
        if not isinstance(row, dict) or not {"serial", "power_cw"}.issubset(row):
            raise ValueError("每条闪测记录需要 serial 和 power_cw")
        serial, reading = row["serial"], row["power_cw"]
        if not isinstance(serial, str) or not re.fullmatch(SERIAL_PATTERN, serial):
            raise ValueError("板件编号须为 1 至 32 位字母、数字、下划线或连字符")
        if serial in seen:
            raise ValueError(f"板件重复：{serial}")
        _check_reading(reading)
        seen.add(serial)
        rows.append((serial, reading))
    config = payload.get("predicate")
    if not isinstance(config, dict) or config.get("mode") not in ("range", "null"):
        raise ValueError("predicate.mode 需要 range 或 null")
    if config["mode"] == "null":
        return rows, Predicate("null")
    low, high = config.get("low"), config.get("high")
    if type(low) is not int or type(high) is not int or not 0 <= low <= high <= 500000:
        raise ValueError("需要 0 ≤ low ≤ high ≤ 500000 的整数厘瓦范围")
    return rows, Predicate("range", low, high)


def normalize_correction(payload):
    """校验按板件编号补录复测功率的输入，返回 {serial: power_cw}。"""
    if not isinstance(payload, dict):
        raise ValueError("输入需为对象")
    changes = payload.get("readings")
    if not isinstance(changes, list) or not 1 <= len(changes) <= 4096:
        raise ValueError("readings 需有 1 至 4096 条复测读数")
    updates = {}
    for row in changes:
        if not isinstance(row, dict) or not {"serial", "power_cw"}.issubset(row):
            raise ValueError("每条复测读数需要 serial 和 power_cw")
        serial, reading = row["serial"], row["power_cw"]
        if not isinstance(serial, str) or not re.fullmatch(SERIAL_PATTERN, serial):
            raise ValueError("板件编号须为 1 至 32 位字母、数字、下划线或连字符")
        if serial in updates:
            raise ValueError(f"复测板件重复：{serial}")
        _check_reading(reading)
        updates[serial] = reading
    note = payload.get("change_note", "")
    if note is not None and not isinstance(note, str):
        raise ValueError("change_note 需为字符串")
    return updates, (note or "")[:320]


def validate(payload):
    normalize(payload)


def run_version(records, pages, predicate, version_no):
    """对某个不可变读数版本执行抽检，结果与逐行基线核对。"""
    evidence, matches, skipped, reads = run_predicate(pages, predicate)
    baseline = baseline_matches(records, predicate)
    if matches != baseline:
        raise RuntimeError("裁剪结果与逐行结果不同")
    return {
        "summary": (
            f"按读数版本 v{version_no}：跳过 {skipped}/{len(pages)} 个页段，"
            f"命中 {len(matches)} 块试板，与逐行校核一致。"
        ),
        "metrics": {"跳过页段": skipped, "命中试板": len(matches), "读取读数": reads,
                    "基线行数": len(records), "总页段": len(pages), "读数版本": version_no},
        "rows": evidence,
    }
