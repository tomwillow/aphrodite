"""条目删除 / 取消：删掉一行时正在下载的图片要停下，且不会被写回。"""
import pytest
from fastapi.testclient import TestClient

from backend import db, worker


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(worker.runner, "submit", lambda task_id: True)
    from backend.app import app

    with TestClient(app) as c:
        yield c


def _new_item(code: str) -> tuple[int, dict]:
    task_id = db.create_task(code, [(code, None)])
    return task_id, db.list_items(task_id)[0]


def test_delete_item_removes_rows_and_reports_removal():
    task_id, item = _new_item("ITEM-001")
    ids = db.add_images(
        task_id, item["id"], [(0, "cover", "http://x/c.jpg"), (1, "sample", "http://x/s.jpg")]
    )

    rev = 0
    assert db.delete_item(task_id, item["id"]) == 2

    changes = db.changes_since(task_id, rev)
    assert changes["removed"]["items"] == [item["id"]]
    assert sorted(changes["removed"]["images"]) == sorted(ids)
    assert changes["images"] == []  # 删掉的图片不会再被推送回来

    finished, total, _ = db.refresh_counts(task_id)
    assert (finished, total) == (0, 0)


def test_delete_item_keeps_library_cache():
    task_id, item = _new_item("CACHE-001")
    db.library_upsert(
        "CACHE-001",
        None,
        "标题",
        [{"seq": 0, "kind": "cover", "url": "u", "path": "library/CACHE-001/cover.jpg",
          "status": db.IMG_DONE}],
    )
    db.delete_item(task_id, item["id"])
    assert db.library_get("CACHE-001") is not None


def test_cancelled_item_does_not_download(tmp_path, monkeypatch):
    task_id, item = _new_item("CANC-001")
    db.add_images(task_id, item["id"], [(0, "cover", "http://x/c.jpg")])
    row = db.item_images(item["id"])[0]

    called = {"n": 0}

    class Boom:
        def download(self, url, referer=None):
            called["n"] += 1
            raise AssertionError("取消后不应再发起下载")

    monkeypatch.setattr(worker, "client", lambda: Boom())

    worker.runner.cancel_item(item["id"])
    try:
        ok = worker.runner._download_one(
            task_id, row, tmp_path, None, worker._DuplicateGuard()
        )
        assert ok is False
        assert called["n"] == 0
    finally:
        worker.runner._cancelled_items.discard(item["id"])


def test_deleted_item_is_not_resurrected(monkeypatch):
    task_id, item = _new_item("GONE-001")
    db.delete_item(task_id, item["id"])

    class Boom:
        def search(self, code):
            raise AssertionError("条目已删除，不应联网")

        def detail(self, url):
            raise AssertionError("条目已删除，不应联网")

    monkeypatch.setattr(worker, "client", lambda: Boom())
    worker.runner._process_item(task_id, item, [])
    assert db.item_images(item["id"]) == []
    assert db.get_item(item["id"]) is None


def test_api_delete_item_cancels_download(client, monkeypatch):
    cancelled: list[int] = []
    monkeypatch.setattr(worker.runner, "cancel_item", lambda iid: cancelled.append(iid))

    task_id = client.post("/api/tasks", json={"text": "API-001"}).json()["task"]["id"]
    item = client.get(f"/api/tasks/{task_id}").json()["items"][0]

    assert client.delete(f"/api/tasks/{task_id}/items/{item['id']}").status_code == 200
    assert cancelled == [item["id"]]
