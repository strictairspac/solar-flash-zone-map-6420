from enum import StrEnum


class VersionState(StrEnum):
    # 新版页段正在重建，期间出货抽检仍读取 active 旧版。
    BUILDING = "building"
    # 新版页段齐备并一次性切换为当前生效版本。
    ACTIVE = "active"
    # 重建中断后放弃，从未生效；出货抽检继续使用此前的 active 版本。
    ABANDONED = "abandoned"
