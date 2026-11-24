"""测试基座：用临时文件型 SQLite 替换 PostgreSQL，注入必需环境变量。

必须在任何 src 模块导入前设置 DATABASE_URL，因为各服务模块以
``from src.config.resources import engine`` 在导入时绑定引擎对象。
文件型 SQLite 跨连接共享同一份数据，worker 另开连接也能看到已提交内容。
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
DB_PATH = _db_file.name
_db_file.close()
os.unlink(DB_PATH)  # 仅借用文件名，建表逻辑负责创建。

for key, default in {
    "DATABASE_URL": f"sqlite:///{DB_PATH}",
    "JWT_SECRET": "test-secret",
    "DEMO_PASSWORD": "Test-Password-2026",
    "SEED_DEMO": "false",
}.items():
    os.environ.setdefault(key, default)


@pytest.fixture()
def engine():
    from src.config.resources import engine as test_engine
    from src.models.base import metadata
    from migrations.v002_versioned_readings import upgrade
    # 每个用例从空库开始，保证增量升级与全新建库两条路径都被覆盖。
    metadata.drop_all(test_engine)
    with test_engine.begin() as db:
        upgrade(db)
    yield test_engine


@pytest.fixture()
def db(engine):
    # AUTOCOMMIT 连接：服务函数自身负责事务边界，worker 另开连接也能读到已提交数据。
    connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    yield connection
    connection.close()


@pytest.fixture()
def case_payload():
    from src.domain.fixtures import FIXTURES
    return FIXTURES[0]["payload"]
