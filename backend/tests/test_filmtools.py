"""影片工具任务（filmtools）测试：目录命名、枚举、执行器、重试。

复用 conftest 的临时 DB（session 级）；全部在临时目录 + TestClient 里跑，
不碰真实影片盘、不联网（crawler 相关逻辑走图库命中或 mock）。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from backend import db, filmtools, films


@pytest.fixture()
def client(monkeypatch):
    from backend import worker as worker_mod

    monkeypatch.setattr(worker_mod.runner, "submit", lambda task_id: None)
    from backend.app import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_state():
    """清空 films 调度器与 filmtools 执行器状态，避免用例间串扰。"""
    films._level_cache.clear()
    films._inflight.clear()
    filmtools.runner._running.clear()
    filmtools.runner._cancelled.clear()
    yield
    deadline = time.time() + 5
    while films._inflight and time.time() < deadline:
        time.sleep(0.02)
    films._level_cache.clear()
    films._inflight.clear()
    filmtools.runner._running.clear()
    filmtools.runner._cancelled.clear()


@pytest.fixture()
def film_tree(tmp_path):
    """影片目录结构：
    films1/
      IPX-528.mp4                带番号、无同名缩略图
      plain.mp4                  无番号、无缩略图（补图目标）
      [$IPX-001][actress][t]/    已整理目录（walk 不下钻）
        inner.mp4                带番号但应被跳过
    """
    dir1 = tmp_path / "films1"
    dir1.mkdir()
    (dir1 / "IPX-528.mp4").write_bytes(b"v")
    (dir1 / "plain.mp4").write_bytes(b"v")
    std = dir1 / "[$IPX-001][actress][t]"
    std.mkdir()
    (std / "inner.mp4").write_bytes(b"v")
    d = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )
    return d["id"], dir1


# ---------------------------------------------------------------- 命名

def test_build_dir_name():
    assert filmtools.build_dir_name("IPX-528", [], None) == "[IPX-528]"
    assert (
        filmtools.build_dir_name("IPX-528", ["葵つかさ"], "タイトル / x")
        == "[IPX-528][葵つかさ][タイトル x]"
    )
    long = filmtools.build_dir_name("AB-1", ["a1", "a2", "a3", "a4", "a5"], None)
    assert long == "[AB-1][a1,a2,a3,...]"
    t = filmtools.build_dir_name("AB-2", None, "A" * 100)
    assert t == f"[AB-2][{'A' * 60}]"
    assert filmtools.build_dir_name("AB-3", ["bad/name"], None) == "[AB-3][bad name]"


def test_is_standard_dir():
    assert filmtools.is_standard_dir("[$IPX-528][actress][t]")
    assert filmtools.is_standard_dir("[IPX-528]")
    assert not filmtools.is_standard_dir("movies")
    assert not filmtools.is_standard_dir("[not-a-code]")


# ---------------------------------------------------------------- 枚举

def test_enumerate_organize_skips_standard(client, film_tree):
    did, root = film_tree
    rows = filmtools._enumerate_organize(did, "", True)
    assert {r["name"] for r in rows} == {"IPX-528.mp4"}


def test_enumerate_thumbs(client, film_tree):
    did, root = film_tree
    rows = filmtools._enumerate_thumbs(did, "", True)
    assert {r["name"] for r in rows} == {"plain.mp4"}


def test_enumerate_non_recursive(client, tmp_path):
    dir1 = tmp_path / "filmsx"
    dir1.mkdir()
    (dir1 / "a.mp4").write_bytes(b"v")
    (dir1 / "sub").mkdir()
    (dir1 / "sub" / "b.mp4").write_bytes(b"v")
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]
    assert {r["name"] for r in filmtools._enumerate_thumbs(did, "", True)} == {"a.mp4", "b.mp4"}
    assert {r["name"] for r in filmtools._enumerate_thumbs(did, "", False)} == {"a.mp4"}


def test_scope_current_starts_at_path(client, monkeypatch, tmp_path):
    """scope=current 带 path：只从该子目录开始枚举，根级其他文件不受影响。"""
    # mock ffmpeg（本机/CI 可能没有），让补图任务能真正跑完
    monkeypatch.setattr(filmtools.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(cmd, capture_output=True, timeout=None, check=True):
        if cmd[0].endswith("ffprobe"):
            return mock.Mock(stdout=b"12.0\n", returncode=0)
        Path(cmd[-1]).write_bytes(b"fake-jpeg")
        return mock.Mock(returncode=0, stderr=b"")

    monkeypatch.setattr(filmtools.subprocess, "run", fake_run)
    dir1 = tmp_path / "filmsp"
    (dir1 / "keep").mkdir(parents=True)
    (dir1 / "keep" / "KEEP-001.mp4").write_bytes(b"v")
    (dir1 / "sub").mkdir()
    (dir1 / "sub" / "SUB-001.mp4").write_bytes(b"v")   # 带番号 → 整理目标
    (dir1 / "sub" / "nosub.mp4").write_bytes(b"v")     # 无番号 → 补图目标
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]

    # 预置图库，整理任务不联网
    lib_dir = Path(db.LIBRARY_DIR) / "SUB-001"
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "cover.jpg").write_bytes(b"cover-bytes")
    rel = lib_dir.relative_to(db.IMAGES_DIR).as_posix()
    db.library_upsert(
        "SUB-001", "http://x/detail", "タイトル",
        [{"seq": 0, "kind": "cover", "url": "u0", "path": f"{rel}/cover.jpg",
          "status": db.IMG_DONE}],
        ["葵つかさ"],
    )

    cases = [
        ("organize", db.TASK_ORGANIZE, "SUB-001.mp4"),
        ("thumbs", db.TASK_THUMB, "nosub.mp4"),
    ]
    for endpoint, kind, want_name in cases:
        films._level_cache.clear()
        r = client.post(
            f"/api/films/tools/{endpoint}",
            json={"scope": "current", "dir_id": did, "path": "sub", "recursive": True},
        )
        assert r.status_code == 200
        t = _wait_task_done(r.json()["task_id"])
        assert t["status"] == db.TASK_COMPLETED
        assert t["total"] == 1, f"{kind} 应只枚举 sub 下的 1 个文件"
        items = db.film_list_items(r.json()["task_id"])
        assert items[0]["name"] == want_name

    # 根级的文件没被动过
    assert (dir1 / "keep" / "KEEP-001.mp4").exists()

    # 非法 path（穿越）→ 409 且任务没建
    r = client.post(
        "/api/films/tools/organize",
        json={"scope": "current", "dir_id": did, "path": "../escape", "recursive": True},
    )
    assert r.status_code == 409
    assert "非法路径" in r.json()["detail"]


# ---------------------------------------------------------------- 执行

def _wait_task_done(task_id: int, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = db.get_task(task_id)
        if t and t["status"] in (db.TASK_COMPLETED, db.TASK_FAILED):
            return t
        time.sleep(0.02)
    raise AssertionError(f"任务 {task_id} 未在期限内结束")


def test_start_rejects_same_kind_concurrent(client, monkeypatch, film_tree):
    """同类任务进行中再次启动 → 409；不同类型不受影响。"""
    did, root = film_tree
    gate = threading.Event()

    def slow_one(item):
        gate.wait(timeout=5)
        return db.FT_SKIPPED, None

    monkeypatch.setattr(filmtools, "_organize_one", slow_one)
    r = client.post(
        "/api/films/tools/organize",
        json={"scope": "current", "dir_id": did, "recursive": True},
    )
    assert r.status_code == 200
    first = r.json()["task_id"]

    r2 = client.post(
        "/api/films/tools/organize",
        json={"scope": "current", "dir_id": did, "recursive": True},
    )
    assert r2.status_code == 409
    assert "已有同类任务" in r2.json()["detail"]

    gate.set()
    t = _wait_task_done(first)
    assert t["status"] == db.TASK_COMPLETED


def test_organize_flow_with_library_hit(client, film_tree):
    """图库命中：复制 cover/samples、移动视频与同名缩略图、建标准目录。"""
    did, root = film_tree
    (root / "IPX-528.jpg").write_bytes(b"thumb")
    lib_dir = Path(db.LIBRARY_DIR) / "IPX-528"
    lib_dir.mkdir(parents=True, exist_ok=True)
    cover = lib_dir / "cover.jpg"
    cover.write_bytes(b"cover-bytes")
    sample = lib_dir / "sample-01.jpg"
    sample.write_bytes(b"sample-bytes")
    rel = lib_dir.relative_to(db.IMAGES_DIR).as_posix()
    db.library_upsert(
        "IPX-528", "http://x/detail", "タイトル",
        [
            {"seq": 0, "kind": "cover", "url": "u0", "path": f"{rel}/cover.jpg",
             "status": db.IMG_DONE},
            {"seq": 1, "kind": "sample", "url": "u1", "path": f"{rel}/sample-01.jpg",
             "status": db.IMG_DONE},
        ],
        ["葵つかさ", "another", "third", "fourth"],
    )

    r = client.post(
        "/api/films/tools/organize",
        json={"scope": "current", "dir_id": did, "recursive": True},
    )
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    t = _wait_task_done(task_id)

    assert t["status"] == db.TASK_COMPLETED
    assert t["total"] == 1 and t["finished"] == 1
    items = db.film_list_items(task_id)
    assert items[0]["status"] == db.FT_DONE

    new_dir = root / "[IPX-528][葵つかさ,another,third,...][タイトル]"
    assert (new_dir / "IPX-528.mp4").read_bytes() == b"v"
    # 主图与视频同名（视频 stem = IPX-528），内容是图库封面而非原缩略图
    assert (new_dir / "IPX-528.jpg").read_bytes() == b"cover-bytes"
    assert (new_dir / "IPX-528-s2.jpg").read_bytes() == b"sample-bytes"
    assert not (root / "IPX-528.mp4").exists()


def test_organize_cover_renamed_to_video_stem(client, film_tree):
    """视频名 ≠ 番号时，主图也要重命名为与视频同名（文件夹封面才能命中）。"""
    did, root = film_tree
    (root / "ABC-123-c.mp4").write_bytes(b"v2")
    lib_dir = Path(db.LIBRARY_DIR) / "ABC-123"
    lib_dir.mkdir(parents=True, exist_ok=True)
    cover = lib_dir / "cover.jpg"
    cover.write_bytes(b"cover-bytes")
    rel = lib_dir.relative_to(db.IMAGES_DIR).as_posix()
    db.library_upsert(
        "ABC-123", "http://x/detail", "タイトル",
        [{"seq": 0, "kind": "cover", "url": "u0", "path": f"{rel}/cover.jpg",
          "status": db.IMG_DONE}],
        ["葵つかさ"],
    )

    r = client.post(
        "/api/films/tools/organize",
        json={"scope": "current", "dir_id": did, "recursive": True},
    )
    assert r.status_code == 200
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED

    new_dir = root / "[ABC-123][葵つかさ][タイトル]"
    assert (new_dir / "ABC-123-c.jpg").read_bytes() == b"cover-bytes"
    assert not (new_dir / "ABC-123.jpg").exists()


def _seed_library(code: str, actresses: list[str], title: str, n_samples: int) -> Path:
    """在临时图库里放 cover + n_samples 张详图，返回图库目录。"""
    lib_dir = Path(db.LIBRARY_DIR) / code
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / "cover.jpg").write_bytes(b"cover-bytes")
    rows = [{"seq": 0, "kind": "cover", "url": "u0",
             "path": f"{lib_dir.relative_to(db.IMAGES_DIR).as_posix()}/cover.jpg",
             "status": db.IMG_DONE}]
    for i in range(1, n_samples + 1):
        name = f"sample-{i:02d}.jpg"
        (lib_dir / name).write_bytes(b"sample-bytes")
        rows.append({"seq": i, "kind": "sample", "url": f"u{i}",
                     "path": f"{lib_dir.relative_to(db.IMAGES_DIR).as_posix()}/{name}",
                     "status": db.IMG_DONE})
    db.library_upsert(code, "http://x/detail", title, rows, actresses)
    return lib_dir


def test_verify_organized_single_video_repairs_cover_and_samples(client, tmp_path):
    """已整理目录里单视频且番号对得上：不跳过，校验封面同名 + 补齐详图，再跑一次则 skipped。"""
    dir1 = tmp_path / "filmsv"
    std = dir1 / "[ABC-123][葵つかさ][タイトル]"
    std.mkdir(parents=True)
    (std / "zzz@ABC-123.mp4").write_bytes(b"v")
    (std / "ABC-123.jpg").write_bytes(b"legacy-cover")   # 旧命名封面（不与视频同名）
    (std / "ABC-123-s2.jpg").write_bytes(b"old-sample")  # 已存在的详图不该被覆盖
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]
    _seed_library("ABC-123", ["葵つかさ"], "タイトル", 2)

    # 枚举阶段就该看到它（而不是因为目录以 [番号] 开头被整目录跳过）
    rows = filmtools._enumerate_organize(did, "", True)
    assert [r["name"] for r in rows] == ["zzz@ABC-123.mp4"]

    r = client.post("/api/films/tools/organize",
                    json={"scope": "current", "dir_id": did, "recursive": True})
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED
    items = db.film_list_items(r.json()["task_id"])
    assert len(items) == 1 and items[0]["status"] == db.FT_DONE

    # 旧命名的封面直接改名成与视频同名（内容保留，不重复下载）
    assert (std / "zzz@ABC-123.jpg").read_bytes() == b"legacy-cover"
    assert not (std / "ABC-123.jpg").exists()
    assert (std / "ABC-123-s2.jpg").read_bytes() == b"old-sample"    # 已有详图不覆盖
    assert (std / "ABC-123-s3.jpg").read_bytes() == b"sample-bytes"  # 缺的详图补齐

    # 再跑一遍：全部合格 → skipped（不重复下载）
    r = client.post("/api/films/tools/organize",
                    json={"scope": "current", "dir_id": did, "recursive": True})
    t = _wait_task_done(r.json()["task_id"])
    items = db.film_list_items(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED
    assert len(items) == 1 and items[0]["status"] == db.FT_SKIPPED


def test_verify_organized_renames_dir(client, tmp_path):
    """目录名不完整（缺演员/标题）→ 校验阶段直接改成 [番号][演员][标题]。"""
    dir1 = tmp_path / "filmsr"
    std = dir1 / "[ABC-124]"
    std.mkdir(parents=True)
    (std / "ABC-124.mp4").write_bytes(b"v")
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]
    _seed_library("ABC-124", ["葵つかさ"], "タイトル", 0)

    r = client.post("/api/films/tools/organize",
                    json={"scope": "current", "dir_id": did, "recursive": True})
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED
    new_dir = dir1 / "[ABC-124][葵つかさ][タイトル]"
    assert (new_dir / "ABC-124.mp4").exists()
    assert (new_dir / "ABC-124.jpg").read_bytes() == b"cover-bytes"
    assert not std.exists()


def test_single_video_dir_renamed_in_place(client, tmp_path):
    """目录名里番号与视频一致、且只有一个视频 → 目录就地改名，不再往下包一层。"""
    dir1 = tmp_path / "filmsi"
    src = dir1 / "LTX" / "ABC-126"
    src.mkdir(parents=True)
    (src / "ABC-126.mp4").write_bytes(b"v")
    (src / "ABC-126.jpg").write_bytes(b"legacy-thumb")
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]
    _seed_library("ABC-126", ["葵つかさ"], "タイトル", 1)

    r = client.post("/api/films/tools/organize",
                    json={"scope": "current", "dir_id": did, "path": "LTX/ABC-126",
                          "recursive": True})
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED

    new_dir = dir1 / "LTX" / "[ABC-126][葵つかさ][タイトル]"
    assert (new_dir / "ABC-126.mp4").read_bytes() == b"v"
    assert (new_dir / "ABC-126.jpg").read_bytes() == b"legacy-thumb"  # 同名封面沿用，不重下
    assert (new_dir / "ABC-126-s2.jpg").read_bytes() == b"sample-bytes"
    assert not src.exists()
    assert not (new_dir / "[ABC-126][葵つかさ][タイトル]").exists()  # 没有多包一层
    items = db.film_list_items(r.json()["task_id"])
    assert items[0]["target"] == "LTX/[ABC-126][葵つかさ][タイトル]"


def test_old_style_nested_dir_flattened(client, tmp_path):
    """旧版整理造出的「目录/标准目录/视频」会被拍平成「标准目录/视频」。"""
    dir1 = tmp_path / "filmsn"
    wrap = dir1 / "LTX" / "ABC-127"
    inner = wrap / "[ABC-127][葵つかさ][タイトル]"
    inner.mkdir(parents=True)
    (inner / "zzz@ABC-127.mp4").write_bytes(b"v")
    (inner / "zzz@ABC-127.jpg").write_bytes(b"legacy-thumb")
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]
    _seed_library("ABC-127", ["葵つかさ"], "タイトル", 1)

    r = client.post("/api/films/tools/organize",
                    json={"scope": "current", "dir_id": did, "path": "LTX/ABC-127",
                          "recursive": True})
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED

    new_dir = dir1 / "LTX" / "[ABC-127][葵つかさ][タイトル]"
    assert (new_dir / "zzz@ABC-127.mp4").read_bytes() == b"v"
    assert (new_dir / "zzz@ABC-127.jpg").read_bytes() == b"legacy-thumb"
    assert (new_dir / "ABC-127-s2.jpg").exists()
    assert not wrap.exists()                       # 外层空壳目录没了
    assert not (new_dir / inner.name).exists()     # 内层套娃也被拍平


def test_film_root_never_renamed(client, tmp_path):
    """视频直接放在影片根目录：仍按老规矩建标准子目录，根目录名绝不动。"""
    root = tmp_path / "ABC-128"  # 名字里正好有番号，也不能被改名
    root.mkdir()
    (root / "ABC-128.mp4").write_bytes(b"v")
    did = next(
        d for d in db.film_sync_dirs([str(root)]) if d["path"].rstrip("/\\") == str(root)
    )["id"]
    _seed_library("ABC-128", ["葵つかさ"], "タイトル", 0)

    r = client.post("/api/films/tools/organize",
                    json={"scope": "current", "dir_id": did, "recursive": True})
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED
    assert root.is_dir()
    assert (root / "[ABC-128][葵つかさ][タイトル]" / "ABC-128.mp4").exists()


def test_dir_without_code_ads_still_renamed(client, tmp_path):
    """目录里混着无番号广告片，只要带番号的视频只有一个 → 仍就地改名。"""
    dir1 = tmp_path / "filmsad"
    src = dir1 / "LTX" / "ABC-130"
    src.mkdir(parents=True)
    (src / "ABC-130.mp4").write_bytes(b"v")
    (src / "广告片.mp4").write_bytes(b"ad")      # 无番号，不该妨碍判定
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]
    _seed_library("ABC-130", ["葵つかさ"], "タイトル", 0)

    r = client.post("/api/films/tools/organize",
                    json={"scope": "current", "dir_id": did, "path": "LTX/ABC-130",
                          "recursive": True})
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED
    new_dir = dir1 / "LTX" / "[ABC-130][葵つかさ][タイトル]"
    assert (new_dir / "ABC-130.mp4").exists()
    assert (new_dir / "广告片.mp4").exists()   # 广告片跟着目录一起搬过去
    assert not src.exists()


def test_multi_video_dir_not_renamed(client, tmp_path):
    """同名目录里有 2 个视频 → 不判为「这部电影的家」，仍建标准子目录收纳。"""
    dir1 = tmp_path / "filmsmm"
    src = dir1 / "ABC-129"
    src.mkdir(parents=True)
    (src / "ABC-129.mp4").write_bytes(b"v1")
    (src / "ABC-129-cd2.mp4").write_bytes(b"v2")
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]
    _seed_library("ABC-129", ["葵つかさ"], "タイトル", 0)

    r = client.post("/api/films/tools/organize",
                    json={"scope": "current", "dir_id": did, "path": "ABC-129",
                          "recursive": True})
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_COMPLETED
    std = src / "[ABC-129][葵つかさ][タイトル]"
    assert src.is_dir()  # 原目录保留（多视频不就地改名）
    assert {p.name for p in std.glob("*.mp4")} == {"ABC-129.mp4", "ABC-129-cd2.mp4"}


def test_standard_dir_with_other_code_not_enumerated(client, tmp_path):
    """目录番号与视频番号对不上 → 仍按已整理目录跳过，不枚举。"""
    dir1 = tmp_path / "filmsm"
    std = dir1 / "[ABC-125][x][y]"
    std.mkdir(parents=True)
    (std / "XYZ-999.mp4").write_bytes(b"v")
    did = next(
        d for d in db.film_sync_dirs([str(dir1)]) if d["path"].rstrip("/\\") == str(dir1)
    )["id"]
    assert filmtools._enumerate_organize(did, "", True) == []


def test_thumb_task_generates_jpg(client, monkeypatch, film_tree):
    """ffmpeg mock：抽帧命令执行成功 → 生成同名 jpg。"""
    did, root = film_tree
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output=True, timeout=None, check=True):
        cmd = list(cmd)
        if cmd[0].endswith("ffprobe"):
            return mock.Mock(stdout=b"12.0\n", returncode=0)
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"fake-jpeg")
        return mock.Mock(returncode=0, stderr=b"")

    monkeypatch.setattr(filmtools.subprocess, "run", fake_run)
    monkeypatch.setattr(filmtools.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    r = client.post(
        "/api/films/tools/thumbs",
        json={"scope": "current", "dir_id": did, "recursive": True},
    )
    task_id = r.json()["task_id"]
    t = _wait_task_done(task_id)

    assert t["status"] == db.TASK_COMPLETED
    items = db.film_list_items(task_id)
    assert items[0]["status"] == db.FT_DONE
    assert (root / "plain.jpg").exists()
    assert calls and calls[0][0] == "/usr/bin/ffmpeg"
    # 用了中点 seek（时长 12s → -ss 6.00）
    assert "6.00" in calls[0]


def test_no_ffmpeg_marks_task_failed(client, monkeypatch, film_tree):
    did, root = film_tree
    monkeypatch.setattr(filmtools.shutil, "which", lambda name: None)
    r = client.post(
        "/api/films/tools/thumbs",
        json={"scope": "current", "dir_id": did, "recursive": True},
    )
    t = _wait_task_done(r.json()["task_id"])
    assert t["status"] == db.TASK_FAILED
    assert "ffmpeg" in t["error"]


def test_retry_failed_creates_new_task(client, film_tree):
    """失败条目重试：新任务只带失败条目。"""
    did, root = film_tree
    task_id = db.create_film_task(db.TASK_ORGANIZE, "整理番号目录 · 全库")
    db.film_add_items(task_id, [
        {"dir_id": did, "rel_path": "IPX-528.mp4", "name": "IPX-528.mp4"},
        {"dir_id": did, "rel_path": "plain.mp4", "name": "plain.mp4"},
    ])
    rows = db.film_list_items(task_id)
    db.film_finish_item(
        next(i["id"] for i in rows if i["name"] == "IPX-528.mp4"),
        db.FT_FAILED, "mock 失败",
    )
    db.film_finish_item(
        next(i["id"] for i in rows if i["name"] == "plain.mp4"),
        db.FT_SKIPPED, None,
    )
    db.touch_task(task_id, status=db.TASK_COMPLETED)

    r = client.post(f"/api/tasks/{task_id}/retry-failed")
    assert r.status_code == 200
    new_id = r.json()["task_id"]
    t = _wait_task_done(new_id)
    items = db.film_list_items(new_id)
    assert len(items) == 1 and items[0]["name"] == "IPX-528.mp4"
    assert t["total"] == 1


def test_delete_film_task_cleans_items(client, film_tree):
    did, root = film_tree
    task_id = db.create_film_task(db.TASK_THUMB, "补全缩略图 · 全库")
    db.film_add_items(task_id, [{"dir_id": did, "rel_path": "plain.mp4", "name": "plain.mp4"}])
    r = client.delete(f"/api/tasks/{task_id}")
    assert r.status_code == 200
    assert db.film_list_items(task_id) == []
    assert db.get_task(task_id) is None


def test_cancel_running_film_task(client, monkeypatch, film_tree):
    did, root = film_tree
    gate = threading.Event()

    def slow_one(item):
        gate.wait(timeout=5)
        raise filmtools.FilmToolError("mock 失败")

    monkeypatch.setattr(filmtools, "_organize_one", slow_one)
    r = client.post(
        "/api/films/tools/organize",
        json={"scope": "current", "dir_id": did, "recursive": True},
    )
    task_id = r.json()["task_id"]
    deadline = time.time() + 5
    while time.time() < deadline and not filmtools.runner.is_running(task_id):
        time.sleep(0.02)
    r = client.post(f"/api/tasks/{task_id}/cancel")
    assert r.status_code == 200
    gate.set()
    t = _wait_task_done(task_id)
    assert t["status"] == db.TASK_FAILED
    assert t["error"] == "已取消"


def test_task_list_includes_kind_and_summary(client, film_tree):
    did, root = film_tree
    task_id = db.create_film_task(db.TASK_ORGANIZE, "整理番号目录 · 全库")
    db.film_add_items(task_id, [
        {"dir_id": did, "rel_path": "a.mp4", "name": "a.mp4"},
    ])
    db.film_finish_item(db.film_list_items(task_id)[0]["id"], db.FT_FAILED, "x")
    tasks = client.get("/api/tasks").json()["tasks"]
    mine = next(t for t in tasks if t["id"] == task_id)
    assert mine["kind"] == "organize"
    assert mine["summary"]["failed"] == 1
