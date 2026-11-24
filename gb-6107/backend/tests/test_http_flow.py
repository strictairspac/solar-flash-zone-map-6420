"""HTTP 端到端：登录鉴权、补录更正、版本切换、排队快照与回执不可覆盖。"""
import pytest
from fastapi.testclient import TestClient

from src.constants.roles import Role
from src.domain.fixtures import FIXTURES


@pytest.fixture()
def client(engine, monkeypatch):
    # Redis 在测试环境不可用；队列以数据库为准，唤醒失败本就被吞掉。
    monkeypatch.setattr("src.controllers.workbench.cache", _DummyRedis())
    monkeypatch.setattr("src.routes.system.cache", _DummyRedis())

    from src.models.users import users
    from src.utils.passwords import hash_password
    with engine.begin() as db:
        db.execute(users.insert().values(
            username="operator", role=Role.OPERATOR, password_hash=hash_password("pw-2026")))
        db.execute(users.insert().values(
            username="reviewer", role=Role.REVIEWER, password_hash=hash_password("pw-2026")))

    from src.main import app
    with TestClient(app) as test_client:
        yield test_client


class _DummyRedis:
    def lpush(self, *a, **k):
        return 1

    def ltrim(self, *a, **k):
        return True

    def ping(self):
        return True

    def get(self, key):
        return None


def _token(client, username):
    resp = client.post("/auth/login", json={"username": username, "password": "pw-2026"})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_full_flow_correction_pin_and_immutable_receipt(client, engine):
    op = _auth(_token(client, "operator"))
    payload = FIXTURES[0]["payload"]

    # 登记质保批（显式页容量）。
    resp = client.post("/cases", headers=op, json={
        "title": "端到端质保批", "description": "", "payload": payload, "page_size": 4})
    assert resp.status_code == 201, resp.text
    case_id = resp.json()["id"]
    assert resp.json()["page_size"] == 4

    # 版本列表含 v1。
    versions = client.get(f"/cases/{case_id}/versions", headers=op).json()
    assert [v["version_no"] for v in versions] == [1]
    assert versions[0]["status"] == "active"

    # 第一个抽检批次入队，钉住 v1。
    r1 = client.post(f"/cases/{case_id}/runs", headers=op, json={"parameter": 4})
    assert r1.status_code == 202
    run1 = r1.json()
    assert run1["reading_version_no"] == 1

    # 按板件编号补录复测功率：只重建受影响页并切换到 v2。
    correction = client.post(f"/cases/{case_id}/corrections", headers=op, json={
        "readings": [{"serial": "D3-0109", "power_cw": 42000}], "change_note": "补录 D3-0109"})
    assert correction.status_code == 201, correction.text
    v2 = correction.json()
    assert v2["version_no"] == 2 and v2["status"] == "active"
    assert v2["rebuilt_pages"] == 1 and v2["reused_pages"] == 4

    # 第二个批次钉住 v2。
    run2 = client.post(f"/cases/{case_id}/runs", headers=op, json={"parameter": 4}).json()
    assert run2["reading_version_no"] == 2

    # 驱动 worker 把两条都跑完。
    from src.services.execution import service_one
    while service_one():
        pass

    d1 = client.get(f"/runs/{run1['id']}", headers=op).json()
    d2 = client.get(f"/runs/{run2['id']}", headers=op).json()
    assert d1["status"] == "done" and d2["status"] == "done"
    # 排队快照：旧批次 v1 不含补录板件，新批次 v2 含。
    hit1 = {s for row in d1["result"]["rows"] if row["板件"] for s in row["板件"].split("、")}
    hit2 = {s for row in d2["result"]["rows"] if row["板件"] for s in row["板件"].split("、")}
    assert "D3-0109" not in hit1 and "D3-0109" in hit2
    assert d1["result"]["reading_version"]["version_no"] == 1
    assert d2["result"]["reading_version"]["version_no"] == 2

    # 回执不可覆盖：两条独立证据各自可导出，digest 不同且稳定。
    e1 = client.get(f"/runs/{run1['id']}/export", headers=op)
    e2 = client.get(f"/runs/{run2['id']}/export", headers=op)
    assert e1.status_code == 200 and e2.status_code == 200
    assert d1["digest"] != d2["digest"]
    # 再跑一次更正不影响历史回执的固化内容。
    client.post(f"/cases/{case_id}/corrections", headers=op, json={
        "readings": [{"serial": "D3-0103", "power_cw": 41950}]})
    assert client.get(f"/runs/{run1['id']}/export", headers=op).content == e1.content


def test_abort_keeps_serving_old_version(client):
    op = _auth(_token(client, "operator"))
    payload = FIXTURES[0]["payload"]
    case_id = client.post("/cases", headers=op, json={
        "title": "中断质保批", "payload": payload}).json()["id"]

    aborted = client.post(f"/cases/{case_id}/corrections", headers=op, json={
        "readings": [{"serial": "D3-0109", "power_cw": 42000}], "simulate_abort": True})
    assert aborted.status_code == 201
    assert aborted.json()["status"] == "abandoned"

    # 中断后出货抽检照常接单，且仍读到 active 的 v1。
    run = client.post(f"/cases/{case_id}/runs", headers=op, json={"parameter": 4}).json()
    assert run["reading_version_no"] == 1


def test_permissions_and_validation(client):
    reviewer = _auth(_token(client, "reviewer"))
    payload = FIXTURES[0]["payload"]
    # 复核员不能登记资料、不能更正。
    assert client.post("/cases", headers=reviewer, json={
        "title": "越权", "payload": payload}).status_code == 403
    # 未登录 401。
    assert client.post("/cases", json={"title": "匿名", "payload": payload}).status_code == 401
    # 操作员更正不存在的板件编号 → 422。
    op = _auth(_token(client, "operator"))
    case_id = client.post("/cases", headers=op, json={
        "title": "校验批", "payload": payload}).json()["id"]
    bad = client.post(f"/cases/{case_id}/corrections", headers=op, json={
        "readings": [{"serial": "GHOST-1", "power_cw": 42000}]})
    assert bad.status_code == 422
    # 页容量越界 → 422。
    over = client.post("/cases", headers=op, json={
        "title": "越界批", "payload": payload, "page_size": 999})
    assert over.status_code == 422
