import pytest
from fastapi.testclient import TestClient

from backend import db, worker
from backend.config import IMAGES_DIR


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(worker.runner, "submit", lambda task_id: True)
    from backend.app import app

    with TestClient(app) as c:
        yield c


def _new_item(code: str) -> tuple[int, dict]:
    task_id = db.create_task(code, [code])
    return task_id, db.list_items(task_id)[0]


# ---------------------------------------------------------------- 统一图库

def test_library_upsert_and_get():
    db.library_upsert(
        "LIB-001",
        "https://www.javbus.com/LIB-001",
        "标题",
        [
            {"seq": 0, "kind": "cover", "url": "u0", "path": "library/LIB-001/cover.jpg",
             "status": db.IMG_DONE},
            {"seq": 1, "kind": "sample", "url": "u1", "path": "library/LIB-001/sample-01.jpg",
             "status": db.IMG_DONE},
        ],
    )
    lib = db.library_get("LIB-001")
    assert lib is not None
    assert lib["title"] == "标题"
    assert [i["seq"] for i in lib["images"]] == [0, 1]
    assert db.library_get("NOSUCH-001") is None


def test_library_upsert_skips_pending():
    db.library_upsert(
        "LIB-002",
        None,
        None,
        [{"seq": 0, "kind": "cover", "url": "u", "path": None, "status": db.IMG_PENDING}],
    )
    assert db.library_get("LIB-002") is None


def test_reuse_from_library_skips_network(tmp_path):
    task_id, item = _new_item("REUSE-001")
    lib_dir = IMAGES_DIR / "library" / "REUSE-001"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "cover.jpg").write_bytes(b"x" * 4096)
    rel = (lib_dir / "cover.jpg").relative_to(IMAGES_DIR).as_posix()

    db.library_upsert(
        "REUSE-001",
        "https://www.javbus.com/REUSE-001",
        "复用标题",
        [
            {"seq": 0, "kind": "cover", "url": "u0", "path": rel, "status": db.IMG_DONE},
            {"seq": 1, "kind": "sample", "url": "u1", "path": None, "status": db.IMG_FAILED},
        ],
    )

    assert worker.runner._reuse_from_library(task_id, item) is True
    images = db.item_images(item["id"])
    assert images[0]["status"] == db.IMG_DONE
    assert images[0]["path"] == rel
    # 图库里失败的那张重新排队，再次获得下载机会
    assert images[1]["status"] == db.IMG_PENDING
    row = db.get_item(item["id"])
    assert row["cached"] == 1
    assert row["detail_url"] == "https://www.javbus.com/REUSE-001"


def test_reuse_from_library_missing_file_falls_back():
    task_id, item = _new_item("REUSE-404")
    db.library_upsert(
        "REUSE-404",
        None,
        None,
        [{"seq": 0, "kind": "cover", "url": "u", "path": "library/REUSE-404/cover.jpg",
          "status": db.IMG_DONE}],
    )
    # 文件不存在 → 不复用，走联网流程
    assert worker.runner._reuse_from_library(task_id, item) is False


# ---------------------------------------------------------------- 下载重试

def test_download_retries_five_times_then_gives_up(tmp_path, monkeypatch):
    task_id, item = _new_item("RETRY-001")
    db.add_images(task_id, item["id"], [(0, "cover", "http://example.invalid/c.jpg")])
    row = db.item_images(item["id"])[0]

    calls = {"n": 0}

    class FakeClient:
        def download(self, url, referer=None):
            calls["n"] += 1
            raise RuntimeError("boom")

    monkeypatch.setattr(worker, "client", lambda: FakeClient())
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    ok = worker.runner._download_one(
        task_id, row, tmp_path, None, worker._DuplicateGuard()
    )

    assert ok is False
    assert calls["n"] == worker.MAX_IMAGE_ATTEMPTS == 5
    saved = db.item_images(item["id"])[0]
    assert saved["status"] == db.IMG_FAILED
    assert saved["attempts"] == 5


def test_download_succeeds_on_third_attempt(tmp_path, monkeypatch):
    task_id, item = _new_item("RETRY-002")
    db.add_images(task_id, item["id"], [(0, "cover", "http://example.invalid/c.jpg")])
    row = db.item_images(item["id"])[0]

    calls = {"n": 0}

    class FlakyClient:
        def download(self, url, referer=None):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("flaky")
            return b"y" * 4096, ".jpg"

    monkeypatch.setattr(worker, "client", lambda: FlakyClient())
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)

    target_dir = IMAGES_DIR / "library" / "RETRY-002"
    target_dir.mkdir(parents=True, exist_ok=True)
    ok = worker.runner._download_one(
        task_id, row, target_dir, None, worker._DuplicateGuard()
    )
    assert ok is True
    saved = db.item_images(item["id"])[0]
    assert saved["status"] == db.IMG_DONE
    assert saved["attempts"] == 3


def test_reset_for_retry_clears_attempts():
    task_id, item = _new_item("RETRY-003")
    ids = db.add_images(task_id, item["id"], [(0, "cover", "http://x/c.jpg")])
    db.set_image(ids[0], status=db.IMG_FAILED, attempts=5, error="boom")
    db.set_item(item["id"], status=db.ITEM_FAILED, error="所有图片下载失败")

    assert db.reset_for_retry(task_id) == 1
    img = db.item_images(item["id"])[0]
    assert img["status"] == db.IMG_PENDING
    assert img["attempts"] == 0
    assert img["error"] is None
    assert db.get_item(item["id"])["status"] == db.ITEM_PENDING


# ---------------------------------------------------------------- API

def test_delete_task_endpoint(client, monkeypatch):
    cancelled: list[int] = []
    monkeypatch.setattr(worker.runner, "cancel", lambda tid: cancelled.append(tid))

    task_id = client.post("/api/tasks", json={"text": "DEL-001"}).json()["task"]["id"]
    assert client.delete(f"/api/tasks/{task_id}").status_code == 200
    assert cancelled == [task_id]
    assert client.get(f"/api/tasks/{task_id}").status_code == 404
    assert client.delete(f"/api/tasks/{task_id}").status_code == 404


def test_retry_endpoint(client):
    task_id = client.post("/api/tasks", json={"text": "RTY-001"}).json()["task"]["id"]
    item = db.list_items(task_id)[0]
    ids = db.add_images(task_id, item["id"], [(0, "cover", "http://x/c.jpg")])
    db.set_image(ids[0], status=db.IMG_FAILED, attempts=5, error="boom")

    res = client.post(f"/api/tasks/{task_id}/retry")
    assert res.status_code == 200
    assert res.json()["reset"] == 1
    assert db.item_images(item["id"])[0]["status"] == db.IMG_PENDING
