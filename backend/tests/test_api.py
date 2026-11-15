import pytest
from fastapi.testclient import TestClient

from backend import worker as worker_mod


@pytest.fixture()
def client(monkeypatch):
    # 不真正执行任务（避免测试期联网），只验证 API 行为
    monkeypatch.setattr(worker_mod.runner, "submit", lambda task_id: None)
    from backend.app import app

    with TestClient(app) as c:
        yield c


def test_create_and_get_task(client):
    res = client.post(
        "/api/tasks",
        json={"text": "IPX-528 田中レモン AVOP-201 相沢みなみ ipx-528"},
    )
    assert res.status_code == 200
    data = res.json()
    # 去重后 2 个番号
    assert data["task"]["total"] == 2
    assert [i["code"] for i in data["items"]] == ["IPX-528", "AVOP-201"]

    task_id = data["task"]["id"]
    got = client.get(f"/api/tasks/{task_id}")
    assert got.status_code == 200
    assert got.json()["task"]["id"] == task_id


def test_create_task_empty_text_rejected(client):
    res = client.post("/api/tasks", json={"text": "没有任何番号的文本"})
    assert res.status_code == 400


def test_create_task_over_limit_rejected(client):
    # 回归：大量番号此前被 200 上限拦截，前端表现为“点了没反应”
    text = " ".join(f"AB-{i:04d}" for i in range(2500))
    res = client.post("/api/tasks", json={"text": text})
    assert res.status_code == 400
    assert "2000" in res.json()["detail"]


def test_create_task_just_under_limit_ok(client):
    text = " ".join(f"CD-{i:04d}" for i in range(2000))
    res = client.post("/api/tasks", json={"text": text})
    assert res.status_code == 200
    assert res.json()["task"]["total"] == 2000


def test_task_list_and_404(client):
    assert client.get("/api/tasks").status_code == 200
    assert client.get("/api/tasks/999999").status_code == 404


def test_parse_endpoint(client):
    res = client.post("/api/parse", json={"text": "IPX-528 ipx-528"})
    body = res.json()
    assert body["codes"] == ["IPX-528"]
    assert body["count"] == 1
    # 分类字段齐全，提交页据此提示
    assert body["no_link"] == [{"line": "IPX-528 ipx-528", "code": "IPX-528"}]
    assert body["only_link"] == [] and body["unknown"] == []


def test_create_task_stores_link_per_item(client):
    ed2k = "ed2k://|file|@IPX-528_restored.mp4|10174562190|ABC|/"
    res = client.post("/api/tasks", json={"text": ed2k + "\nAVOP-201"})
    assert res.status_code == 200
    items = res.json()["items"]
    assert items[0]["link"] == ed2k
    assert items[1]["link"] is None


def test_delete_item_endpoint(client):
    task_id = client.post("/api/tasks", json={"text": "DEL-100\nDEL-200"}).json()["task"]["id"]
    item = client.get(f"/api/tasks/{task_id}").json()["items"][0]

    res = client.delete(f"/api/tasks/{task_id}/items/{item['id']}")
    assert res.status_code == 200
    assert res.json()["total"] == 1

    snap = client.get(f"/api/tasks/{task_id}").json()
    assert [i["code"] for i in snap["items"]] == ["DEL-200"]
    assert snap["task"]["total"] == 1
    # 重复删除 → 404
    assert client.delete(f"/api/tasks/{task_id}/items/{item['id']}").status_code == 404


def test_delete_item_other_task_rejected(client):
    t1 = client.post("/api/tasks", json={"text": "OWN-001"}).json()["task"]["id"]
    t2 = client.post("/api/tasks", json={"text": "OWN-002"}).json()["task"]["id"]
    item = client.get(f"/api/tasks/{t1}").json()["items"][0]
    assert client.delete(f"/api/tasks/{t2}/items/{item['id']}").status_code == 404
