"""影片管理：快照+后台扫描 / 调度器 / 番号提取 / 重命名 / 删除 / 路径安全。"""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend import db, films
from backend import worker as worker_mod


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(worker_mod.runner, "submit", lambda task_id: None)
    from backend.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_scheduler():
    """每个用例清空调度器状态；结束时等在飞的扫描线程落定，避免串扰。"""
    films._level_cache.clear()
    films._inflight.clear()
    yield
    deadline = time.time() + 5
    while films._inflight and time.time() < deadline:
        time.sleep(0.02)
    films._level_cache.clear()
    films._inflight.clear()


@pytest.fixture()
def film_tree(tmp_path):
    """一个典型的影片目录结构。

    dir1/
      IPX-528.mp4            + IPX-528.jpg（有缩略图、有番号）
      AVOP-201 [x].mkv       无缩略图、文件名含空格番号
      nocode.txt             非视频文件（也列出，缩略图为空）
      .hidden.mp4            隐藏文件（也列出）
      folderA/               单视频文件夹
        movie.mp4 + movie.jpg
      folderB/               多视频文件夹（5 个视频，只有 v1 有缩略图）
        v1.mp4 + v1.jpg, v2.mp4 ... v5.mp4
    """
    dir1 = tmp_path / "films1"
    (dir1 / "folderA").mkdir(parents=True)
    (dir1 / "folderB").mkdir()
    (dir1 / "IPX-528.mp4").write_bytes(b"v")
    (dir1 / "IPX-528.jpg").write_bytes(b"t")
    (dir1 / "AVOP-201 [x].mkv").write_bytes(b"v")
    (dir1 / "nocode.txt").write_bytes(b"x")
    (dir1 / ".hidden.mp4").write_bytes(b"v")
    (dir1 / "folderA" / "movie.mp4").write_bytes(b"v")
    (dir1 / "folderA" / "movie.jpg").write_bytes(b"t")
    for i in range(1, 6):
        (dir1 / "folderB" / f"v{i}.mp4").write_bytes(b"v")
    (dir1 / "folderB" / "v1.jpg").write_bytes(b"t")
    return dir1


def _enable_dirs(monkeypatch, *paths):
    monkeypatch.setattr("backend.app.FILM_DIRS", [str(p) for p in paths])


def _dir_id(client, path: str) -> int:
    res = client.get("/api/films/dirs")
    assert res.status_code == 200
    for d in res.json()["dirs"]:
        if d["path"] == str(path).rstrip("/\\"):
            return d["id"]
    raise AssertionError(f"目录未同步：{path}")


def _new_dir(paths: list[dict], path) -> dict:
    """film_sync_dirs 返回全部目录（会话级共享库），取本次刚同步的那个。"""
    return next(d for d in paths if d["path"] == str(path).rstrip("/\\"))


def _wait_scan(did: int, rel: str, timeout: float = 10.0) -> list[dict]:
    """等后台扫描完成，返回缓存里的完整载荷。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cached = films.get_cached(did, rel)
        if cached is not None:
            return cached
        time.sleep(0.02)
    raise AssertionError(f"后台扫描超时：dir {did} / {rel or '根级'}")


def _wait_entries(did: int, rel: str, names: set[str], timeout: float = 10.0) -> list[dict]:
    """轮询缓存载荷直到包含指定条目名（rename/delete 的 force 扫描推送）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        cached = films.get_cached(did, rel)
        if cached is not None and names <= {e["name"] for e in cached}:
            return cached
        time.sleep(0.02)
    raise AssertionError(f"扫描推送超时，未出现条目：{names}")


# ---------------------------------------------------------------- 单元

def test_norm_rel_rejects_traversal():
    assert films._norm_rel("a/b") == "a/b"
    assert films._norm_rel("") == ""
    assert films._norm_rel("/a//b/") == "a/b"
    with pytest.raises(films.FilmError):
        films._norm_rel("../etc")
    with pytest.raises(films.FilmError):
        films._norm_rel("a/../../b")


def test_resolve_under_root_blocks_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = films.resolve_under_root(root, "sub/x")
    assert str(inside).startswith(str(root.resolve()))
    with pytest.raises(films.FilmError):
        films.resolve_under_root(root, "../outside")


def test_map_to_local(monkeypatch):
    monkeypatch.setattr(films, "FILM_PATH_MAP", [("/data/films", "Z:\\Films")])
    assert films.map_to_local("/data/films") == "Z:\\Films"
    assert films.map_to_local("/data/films/a/b.mp4") == "Z:\\Films\\a\\b.mp4"
    assert films.map_to_local("/data/other/x") is None
    assert films.map_to_local("/data/films2/x") is None  # 最长前缀防误配


# ---------------------------------------------------------------- 快照 + 后台扫描

def test_entries_snapshot_then_scan_push(client, monkeypatch, film_tree):
    """首次访问：空快照（scanning=true）→ 后台扫完 → 二次访问 TTL 命中完整载荷。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)

    res = client.get(f"/api/films/entries?dir_id={did}&path=")
    assert res.status_code == 200
    data = res.json()
    assert data["scanning"] is True
    assert data["entries"] == []  # DB 还没有该级数据，快照为空

    # 后台扫描完成后缓存里有完整载荷（含拼贴）
    cached = _wait_scan(did, "")
    by_name = {e["name"]: e for e in cached}
    assert set(by_name) == {
        "folderA", "folderB", "IPX-528.mp4", "IPX-528.jpg",
        "AVOP-201 [x].mkv", "nocode.txt", ".hidden.mp4",
    }
    assert by_name["folderB"]["video_count"] == 5
    assert by_name["folderB"]["collage"] == ["folderB/v1.jpg"]

    # TTL 内二次访问：直接返回缓存完整载荷，不再置 scanning
    res = client.get(f"/api/films/entries?dir_id={did}&path=")
    assert res.json()["scanning"] is False
    assert {e["name"] for e in res.json()["entries"]} == set(by_name)


def test_scan_root_and_nested(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)

    films.schedule_scan(did, "")
    cached = _wait_scan(did, "") or []
    by_name = {e["name"]: e for e in cached}

    # 文件夹在前、文件在后；隐藏文件与非视频文件都要列出
    assert set(by_name) == {
        "folderA", "folderB", "IPX-528.mp4", "IPX-528.jpg",
        "AVOP-201 [x].mkv", "nocode.txt", ".hidden.mp4",
    }
    assert [e["name"] for e in cached] == [
        "folderA", "folderB",
        ".hidden.mp4", "AVOP-201 [x].mkv", "IPX-528.jpg", "IPX-528.mp4", "nocode.txt",
    ]

    ipx = by_name["IPX-528.mp4"]
    assert ipx["code"] == "IPX-528"
    assert ipx["thumb_rel"] == "IPX-528.jpg"
    assert ipx["kind"] == "video"
    avop = by_name["AVOP-201 [x].mkv"]
    assert avop["code"] == "AVOP-201"
    assert avop["thumb_rel"] is None
    # 图片文件以自身作缩略图；其它文件缩略图为空；两者都不提番号
    jpg = by_name["IPX-528.jpg"]
    assert jpg["kind"] == "image"
    assert jpg["thumb_rel"] == "IPX-528.jpg"
    assert jpg["code"] is None
    txt = by_name["nocode.txt"]
    assert txt["kind"] == "file"
    assert txt["thumb_rel"] is None
    # 隐藏文件同样入库
    hidden = by_name[".hidden.mp4"]
    assert hidden["kind"] == "video"
    assert hidden["is_dir"] is False

    # 单视频文件夹
    fa = by_name["folderA"]
    assert fa["video_count"] == 1
    assert fa["collage"] == ["folderA/movie.jpg"]
    # 多视频文件夹：5 个视频，只有 v1 有缩略图
    fb = by_name["folderB"]
    assert fb["video_count"] == 5
    assert fb["collage"] == ["folderB/v1.jpg"]

    # 翻进子级：快照先回（该级 DB 还没数据），后台扫完后 rel_path 全前缀
    res = client.get(f"/api/films/entries?dir_id={did}&path=folderA")
    assert res.status_code == 200
    nested = _wait_scan(did, "folderA")
    assert [e["rel_path"] for e in nested] == ["folderA/movie.jpg", "folderA/movie.mp4"]
    assert nested[1]["thumb_rel"] == "folderA/movie.jpg"


def test_snapshot_is_db_only(client, monkeypatch, film_tree):
    """快照阶段不做磁盘 IO：磁盘加了文件但没扫描，快照里不该出现。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    _wait_scan(did, "")

    # 磁盘上新增一个文件，但不触发扫描
    (film_tree / "SSIS-100.mp4").write_bytes(b"v")
    res = client.get(f"/api/films/entries?dir_id={did}&path=")
    assert res.json()["scanning"] is False  # TTL 内命中缓存
    # 强制绕过 TTL：快照只含 DB 已知条目
    res = client.get(f"/api/films/entries?dir_id={did}&path=&force=1")
    data = res.json()
    assert data["scanning"] is True
    assert "SSIS-100.mp4" not in {e["name"] for e in data["entries"]}
    # force 触发的后台扫描结束后缓存更新
    cached = _wait_entries(did, "", {"SSIS-100.mp4"})
    assert "SSIS-100.mp4" in {e["name"] for e in cached}


def test_rescan_diffs_with_disk(client, monkeypatch, film_tree):
    """磁盘变化后重扫：新增入库、消失删除、改名重提番号。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    _wait_scan(did, "")

    # 新增一个视频 → force 扫描后推送出现
    (film_tree / "SSIS-100.mp4").write_bytes(b"v")
    films.schedule_scan(did, "", force=True)
    cached = _wait_entries(did, "", {"SSIS-100.mp4"})
    assert "SSIS-100.mp4" in {e["name"] for e in cached}

    # 删除一个视频
    (film_tree / "IPX-528.mp4").unlink()
    deadline = time.time() + 10
    while time.time() < deadline:
        cached = films.scan_and_sync(did, "")
        if "IPX-528.mp4" not in {e["name"] for e in cached}:
            break
        time.sleep(0.02)
    assert "IPX-528.mp4" not in {e["name"] for e in cached}

    # 磁盘上直接改名 → 扫描后按新名字重提番号
    (film_tree / "AVOP-201 [x].mkv").rename(film_tree / "MIDE-900 final.mkv")
    cached = films.scan_and_sync(did, "")
    row = next(e for e in cached if e["name"] == "MIDE-900 final.mkv")
    assert row["code"] == "MIDE-900"
    assert "AVOP-201 [x].mkv" not in {e["name"] for e in cached}


# ---------------------------------------------------------------- 调度器

def test_scheduler_dedup_and_ttl(client, monkeypatch, tmp_path):
    """同级去重 + TTL 复用 + force 强刷。"""
    dir1 = tmp_path / "films"
    dir1.mkdir()
    (dir1 / "IPX-001.mp4").write_bytes(b"v")
    did = _new_dir(db.film_sync_dirs([str(dir1)]), dir1)["id"]

    assert films.schedule_scan(did, "") is True
    _wait_scan(did, "")
    assert films.schedule_scan(did, "") is False  # TTL 内不重扫
    assert films.get_cached(did, "") is not None

    # TTL 过期后重扫
    with films._sched_lock:
        films._level_cache[(did, "")]["ts"] -= films.SCAN_TTL + 1
    assert films.schedule_scan(did, "") is True
    _wait_scan(did, "")

    # force 绕过 TTL
    assert films.schedule_scan(did, "", force=True) is True
    _wait_scan(did, "")


def test_scheduler_dedup_while_inflight(client, monkeypatch, tmp_path):
    """扫描进行中，同级的再次调度（含 force）都不会重复开线程。"""
    dir1 = tmp_path / "films"
    dir1.mkdir()
    did = _new_dir(db.film_sync_dirs([str(dir1)]), dir1)["id"]

    release = threading.Event()
    started = threading.Event()
    real = films.scan_and_sync

    def slow_scan(dir_id, rel):
        started.set()
        release.wait(timeout=10)
        return real(dir_id, rel)

    monkeypatch.setattr(films, "scan_and_sync", slow_scan)
    assert films.schedule_scan(did, "") is True
    assert started.wait(timeout=5) is True
    assert films.schedule_scan(did, "") is False
    assert films.schedule_scan(did, "", force=True) is False  # 在扫的结果马上会推回来
    release.set()


def test_scheduler_broadcasts_to_subscribers(client, monkeypatch, tmp_path):
    q = films.subscribe()
    try:
        dir1 = tmp_path / "films"
        dir1.mkdir()
        (dir1 / "IPX-002.mp4").write_bytes(b"v")
        did = _new_dir(db.film_sync_dirs([str(dir1)]), dir1)["id"]
        films.schedule_scan(did, "")
        evt = q.get(timeout=10)
        assert evt["dir_id"] == did
        assert evt["parent_rel"] == ""
        assert evt["entries"][0]["name"] == "IPX-002.mp4"
    finally:
        films.unsubscribe(q)


def test_scheduler_pushes_error_for_missing_dir(client, monkeypatch, tmp_path):
    q = films.subscribe()
    try:
        missing = tmp_path / "missing"
        d = _new_dir(db.film_sync_dirs([str(missing)]), missing)
        films.schedule_scan(d["id"], "")
        evt = q.get(timeout=10)
        assert evt["entries"] is None
        assert "不可访问" in evt["error"]
    finally:
        films.unsubscribe(q)


def test_stream_endpoint_emits_open(client):
    with client.stream("GET", "/api/films/stream") as res:
        assert res.status_code == 200
        assert "text/event-stream" in res.headers["content-type"]
        chunk = next(res.iter_bytes())
        assert b"event: open" in chunk


# ---------------------------------------------------------------- 重命名 / 删除

def test_rename_video_moves_thumb_and_reextracts_code(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    data = _wait_scan(did, "")
    ipx = next(e for e in data if e["name"] == "IPX-528.mp4")

    # 不带扩展名也会自动补上
    res = client.post(
        "/api/films/rename", json={"entry_id": ipx["id"], "new_name": "NEW-001"}
    )
    assert res.status_code == 200
    assert (film_tree / "NEW-001.mp4").is_file()
    assert not (film_tree / "IPX-528.mp4").exists()
    # 缩略图联动改名
    assert (film_tree / "NEW-001.jpg").is_file()
    assert not (film_tree / "IPX-528.jpg").exists()

    # rename 精准更新 DB 行（快照立刻可见），force 扫描随后推送
    snap = films.level_snapshot(did, "")
    row = next(e for e in snap if e["name"] == "NEW-001.mp4")
    assert row["code"] == "NEW-001"
    assert row["thumb_rel"] == "NEW-001.jpg"
    cached = _wait_entries(did, "", {"NEW-001.mp4"})
    assert next(e for e in cached if e["name"] == "NEW-001.mp4")["thumb_rel"] == "NEW-001.jpg"


def test_rename_dir_allows_dot_in_title(client, monkeypatch, film_tree):
    """文件夹名里标题性的点（vol.2 等）不算扩展名，可以改名；伪装成媒体文件的仍被拒。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    (film_tree / "show vol.2").mkdir()
    (film_tree / "looks.mp4").mkdir()  # 名字后缀是真实媒体扩展名 → 应拒绝
    films.schedule_scan(did, "")
    data = _wait_scan(did, "")
    ok = next(e for e in data if e["name"] == "show vol.2" and e["is_dir"])
    bad = next(e for e in data if e["name"] == "looks.mp4" and e["is_dir"])

    res = client.post(
        "/api/films/rename", json={"entry_id": ok["id"], "new_name": "show vol.3"}
    )
    assert res.status_code == 200
    assert (film_tree / "show vol.3").is_dir()

    res = client.post(
        "/api/films/rename", json={"entry_id": bad["id"], "new_name": "looks2.mkv"}
    )
    assert res.status_code == 400
    assert "扩展名" in res.json()["detail"]


def test_rename_folder_clears_children(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    data = _wait_scan(did, "")
    fa = next(e for e in data if e["name"] == "folderA")

    res = client.post(
        "/api/films/rename", json={"entry_id": fa["id"], "new_name": "folderA2"}
    )
    assert res.status_code == 200
    assert (film_tree / "folderA2" / "movie.mp4").is_file()

    # 旧子级 DB 行已清理，rename 的 force 扫描推回新名字（含拼贴）
    cached = _wait_entries(did, "", {"folderA2"})
    fa2 = next(e for e in cached if e["name"] == "folderA2")
    assert fa2["video_count"] == 1

    films.schedule_scan(did, "folderA2", force=True)
    nested = _wait_scan(did, "folderA2")
    assert [e["rel_path"] for e in nested] == ["folderA2/movie.jpg", "folderA2/movie.mp4"]


def test_rename_conflicts(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    data = _wait_scan(did, "")
    ipx = next(e for e in data if e["name"] == "IPX-528.mp4")
    # 与现有文件同名
    res = client.post(
        "/api/films/rename", json={"entry_id": ipx["id"], "new_name": "AVOP-201 [x].mkv"}
    )
    assert res.status_code == 400
    # 同名不变
    res = client.post(
        "/api/films/rename", json={"entry_id": ipx["id"], "new_name": "IPX-528.mp4"}
    )
    assert res.status_code == 400


def test_rename_image_keeps_self_thumb(client, monkeypatch, film_tree):
    """图片文件以自身作缩略图：改名后 thumb_rel 跟着指向新路径。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    data = _wait_scan(did, "")
    jpg = next(e for e in data if e["name"] == "IPX-528.jpg")

    res = client.post(
        "/api/films/rename", json={"entry_id": jpg["id"], "new_name": "cover.jpg"}
    )
    assert res.status_code == 200
    assert (film_tree / "cover.jpg").is_file()
    snap = films.level_snapshot(did, "")
    row = next(e for e in snap if e["name"] == "cover.jpg")
    assert row["thumb_rel"] == "cover.jpg"
    assert row["kind"] == "image"
    # 原视频的缩略图指向还是旧名（视频没改名），扫描后由 diff 重算
    cached = _wait_entries(did, "", {"cover.jpg"})
    assert "IPX-528.jpg" not in {e["name"] for e in cached}


def test_rename_hidden_file_allowed(client, monkeypatch, film_tree):
    """隐藏文件也允许改名（含改成仍以 . 开头的名字）。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    data = _wait_scan(did, "")
    hidden = next(e for e in data if e["name"] == ".hidden.mp4")

    res = client.post(
        "/api/films/rename", json={"entry_id": hidden["id"], "new_name": ".keep.mp4"}
    )
    assert res.status_code == 200
    assert (film_tree / ".keep.mp4").is_file()


def test_delete_video_and_folder(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    data = _wait_scan(did, "")
    ipx = next(e for e in data if e["name"] == "IPX-528.mp4")
    fb = next(e for e in data if e["name"] == "folderB")

    res = client.post("/api/films/delete", json={"entry_id": ipx["id"]})
    assert res.status_code == 200
    assert not (film_tree / "IPX-528.mp4").exists()

    # 文件夹递归删除
    res = client.post("/api/films/delete", json={"entry_id": fb["id"]})
    assert res.status_code == 200
    assert not (film_tree / "folderB").exists()

    # DB 里该级只剩 folderA 与其余文件（IPX-528.jpg 是缩略图，删视频不连带删）
    snap = films.level_snapshot(did, "")
    assert {e["name"] for e in snap} == {
        "folderA", "AVOP-201 [x].mkv", "IPX-528.jpg", "nocode.txt", ".hidden.mp4",
    }


def test_delete_missing_file_syncs_db(client, monkeypatch, film_tree):
    """磁盘上文件已没了：接口报错提示刷新，同时清掉失效的 DB 行。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    films.schedule_scan(did, "")
    data = _wait_scan(did, "")
    ipx = next(e for e in data if e["name"] == "IPX-528.mp4")
    (film_tree / "IPX-528.mp4").unlink()

    res = client.post("/api/films/delete", json={"entry_id": ipx["id"]})
    assert res.status_code == 400
    assert "不存在" in res.json()["detail"]
    # 该行已从 DB 清理
    assert "IPX-528.mp4" not in {e["name"] for e in films.level_snapshot(did, "")}


# ---------------------------------------------------------------- 媒体服务与安全

def test_film_media_serves_thumb_with_cache(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    res = client.get(f"/film-media/{did}/IPX-528.jpg")
    assert res.status_code == 200
    assert res.content == b"t"
    assert "max-age=86400" in res.headers.get("cache-control", "")
    res = client.get(f"/film-media/{did}/不存在.jpg")
    assert res.status_code == 404


def test_film_media_blocks_escape(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    # 绝对路径 / 穿越路径都被拒绝
    res = client.get(f"/film-media/{did}/..%2F..%2Fetc")
    assert res.status_code == 400
    res = client.get(f"/film-media/{did}/%2e%2e/x")
    assert res.status_code == 400


def test_unknown_dir_404(client):
    assert client.get("/api/films/entries?dir_id=99999&path=").status_code == 404
    assert client.get("/film-media/99999/x.jpg").status_code == 404


def test_handler_scripts_exposed(client):
    res = client.get("/api/films/handler")
    assert res.status_code == 200
    data = res.json()
    assert "aphrodite-play://" in data["bat"]
    assert "install.ps1" in data["ps1"] or "HKCR" in data["ps1"]
    assert "aphrodite-play" in data["readme"]
