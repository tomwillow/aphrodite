"""影片管理：扫描同步 / 番号提取 / 重命名 / 删除 / 路径安全。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import films
from backend import worker as worker_mod


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(worker_mod.runner, "submit", lambda task_id: None)
    from backend.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def film_tree(tmp_path):
    """一个典型的影片目录结构。

    dir1/
      IPX-528.mp4            + IPX-528.jpg（有缩略图、有番号）
      AVOP-201 [x].mkv       无缩略图、文件名含空格番号
      nocode.txt             非视频，不入库
      .hidden.mp4            隐藏文件，跳过
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


# ---------------------------------------------------------------- 扫描与同步

def test_scan_root_and_nested(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)

    res = client.get(f"/api/films/entries?dir_id={did}&path=")
    assert res.status_code == 200
    data = res.json()
    by_name = {e["name"]: e for e in data["entries"]}

    # 文件夹在前、视频在后；txt 与隐藏文件不入库
    assert set(by_name) == {"folderA", "folderB", "IPX-528.mp4", "AVOP-201 [x].mkv"}
    assert [e["is_dir"] for e in data["entries"][:2]] == [True, True]

    ipx = by_name["IPX-528.mp4"]
    assert ipx["code"] == "IPX-528"
    assert ipx["thumb_rel"] == "IPX-528.jpg"
    avop = by_name["AVOP-201 [x].mkv"]
    assert avop["code"] == "AVOP-201"
    assert avop["thumb_rel"] is None

    # 单视频文件夹
    fa = by_name["folderA"]
    assert fa["video_count"] == 1
    assert fa["collage"] == ["folderA/movie.jpg"]
    # 多视频文件夹：5 个视频，只有 v1 有缩略图
    fb = by_name["folderB"]
    assert fb["video_count"] == 5
    assert fb["collage"] == ["folderB/v1.jpg"]

    # 翻进子级：rel_path 全前缀
    res = client.get(f"/api/films/entries?dir_id={did}&path=folderA")
    assert res.status_code == 200
    nested = res.json()["entries"]
    assert [e["rel_path"] for e in nested] == ["folderA/movie.mp4"]
    assert nested[0]["thumb_rel"] == "folderA/movie.jpg"


def test_rescan_diffs_with_disk(client, monkeypatch, film_tree):
    """磁盘变化后重扫：新增入库、消失删除、改名重提番号。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    client.get(f"/api/films/entries?dir_id={did}&path=")

    # 新增一个视频
    (film_tree / "SSIS-100.mp4").write_bytes(b"v")
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    assert "SSIS-100.mp4" in {e["name"] for e in data["entries"]}

    # 删除一个视频
    (film_tree / "IPX-528.mp4").unlink()
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    assert "IPX-528.mp4" not in {e["name"] for e in data["entries"]}

    # 磁盘上直接改名 → 扫描后按新名字重提番号
    (film_tree / "AVOP-201 [x].mkv").rename(film_tree / "MIDE-900 final.mkv")
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    row = next(e for e in data["entries"] if e["name"] == "MIDE-900 final.mkv")
    assert row["code"] == "MIDE-900"
    assert "AVOP-201 [x].mkv" not in {e["name"] for e in data["entries"]}


def test_invalid_dir_marked_and_rejected(client, monkeypatch, tmp_path):
    _enable_dirs(monkeypatch, tmp_path / "missing")
    res = client.get("/api/films/dirs")
    did = res.json()["dirs"][0]["id"]
    res = client.get(f"/api/films/entries?dir_id={did}&path=")
    assert res.status_code == 400
    assert "不可访问" in res.json()["detail"]
    # dirs 里被标记为失效
    res = client.get("/api/films/dirs")
    assert res.json()["dirs"][0]["valid"] == 0


# ---------------------------------------------------------------- 重命名 / 删除

def test_rename_video_moves_thumb_and_reextracts_code(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    ipx = next(e for e in data["entries"] if e["name"] == "IPX-528.mp4")

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

    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    row = next(e for e in data["entries"] if e["name"] == "NEW-001.mp4")
    assert row["code"] == "NEW-001"
    assert row["thumb_rel"] == "NEW-001.jpg"


def test_rename_folder_clears_children(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    fa = next(e for e in data["entries"] if e["name"] == "folderA")

    res = client.post(
        "/api/films/rename", json={"entry_id": fa["id"], "new_name": "folderA2"}
    )
    assert res.status_code == 200
    assert (film_tree / "folderA2" / "movie.mp4").is_file()

    # 旧子级 DB 行已清理，新路径下重新扫描
    data = client.get(f"/api/films/entries?dir_id={did}&path=folderA2").json()
    assert [e["rel_path"] for e in data["entries"]] == ["folderA2/movie.mp4"]


def test_rename_conflicts(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    ipx = next(e for e in data["entries"] if e["name"] == "IPX-528.mp4")
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


def test_delete_video_and_folder(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    ipx = next(e for e in data["entries"] if e["name"] == "IPX-528.mp4")
    fb = next(e for e in data["entries"] if e["name"] == "folderB")

    res = client.post("/api/films/delete", json={"entry_id": ipx["id"]})
    assert res.status_code == 200
    assert not (film_tree / "IPX-528.mp4").exists()

    # 文件夹递归删除
    res = client.post("/api/films/delete", json={"entry_id": fb["id"]})
    assert res.status_code == 200
    assert not (film_tree / "folderB").exists()

    # DB 里该级只剩 folderA 与 AVOP-201
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    assert {e["name"] for e in data["entries"]} == {"folderA", "AVOP-201 [x].mkv"}


def test_delete_missing_file_syncs_db(client, monkeypatch, film_tree):
    """磁盘上文件已没了：接口报错提示刷新，同时清掉失效的 DB 行。"""
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    ipx = next(e for e in data["entries"] if e["name"] == "IPX-528.mp4")
    (film_tree / "IPX-528.mp4").unlink()

    res = client.post("/api/films/delete", json={"entry_id": ipx["id"]})
    assert res.status_code == 400
    assert "不存在" in res.json()["detail"]
    # 该行已从 DB 清理，重扫不再出现
    data = client.get(f"/api/films/entries?dir_id={did}&path=").json()
    assert "IPX-528.mp4" not in {e["name"] for e in data["entries"]}


# ---------------------------------------------------------------- 媒体服务与安全

def test_film_media_serves_thumb(client, monkeypatch, film_tree):
    _enable_dirs(monkeypatch, film_tree)
    did = _dir_id(client, film_tree)
    res = client.get(f"/film-media/{did}/IPX-528.jpg")
    assert res.status_code == 200
    assert res.content == b"t"
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
