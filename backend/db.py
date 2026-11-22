"""SQLite 数据层。

单进程多线程：每个线程一条连接，写操作串行化。
所有行带一个全局递增的 rev，用于 SSE 增量推送。
"""
from __future__ import annotations

import itertools
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH, IMAGES_DIR, LIBRARY_DIR, ensure_dirs, safe_dir_name

_local = threading.local()
_write_lock = threading.Lock()
_rev_lock = threading.Lock()
_rev_counter = itertools.count(1)

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"

ITEM_PENDING = "pending"
ITEM_SEARCHING = "searching"
ITEM_DOWNLOADING = "downloading"
ITEM_DONE = "done"
ITEM_NOTFOUND = "notfound"
ITEM_FAILED = "failed"

IMG_PENDING = "pending"
IMG_DOWNLOADING = "downloading"
IMG_DONE = "done"
IMG_FAILED = "failed"
IMG_SKIPPED = "skipped"  # 站点返回的占位图（多张内容完全相同），跳过不展示

ITEM_TERMINAL = (ITEM_DONE, ITEM_NOTFOUND, ITEM_FAILED)

# 任务类型：scrape=番号抓取（默认），organize=整理番号目录，thumb=补全缩略图
TASK_SCRAPER = "scrape"
TASK_ORGANIZE = "organize"
TASK_THUMB = "thumb"

# 影片工具任务的条目状态
FT_PENDING = "pending"
FT_DONE = "done"
FT_SKIPPED = "skipped"
FT_FAILED = "failed"
FT_TERMINAL = (FT_DONE, FT_SKIPPED, FT_FAILED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text    TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'pending',
    total       INTEGER NOT NULL DEFAULT 0,
    finished    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    rev         INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    seq         INTEGER NOT NULL DEFAULT 0,
    code        TEXT    NOT NULL,
    link        TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',
    detail_url  TEXT,
    title       TEXT,
    error       TEXT,
    cached      INTEGER NOT NULL DEFAULT 0,
    rev         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_task ON items(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_items_code ON items(code);

CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    item_id     INTEGER NOT NULL,
    seq         INTEGER NOT NULL DEFAULT 0,
    kind        TEXT    NOT NULL,
    url         TEXT,
    path        TEXT,
    status      TEXT    NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    rev         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_images_task ON images(task_id, rev);
CREATE INDEX IF NOT EXISTS idx_images_item ON images(item_id, seq);

-- 统一图库：按番号索引，跨任务复用，避免重复下载
CREATE TABLE IF NOT EXISTS library (
    code        TEXT PRIMARY KEY,
    detail_url  TEXT,
    title       TEXT,
    actresses   TEXT,               -- JSON 数组字符串，演员列表
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS library_images (
    code        TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    url         TEXT,
    path        TEXT,
    status      TEXT    NOT NULL,
    PRIMARY KEY (code, seq)
);

-- 删除日志：SSE 只能推送"变更"，删掉的行需要一个墓碑告知前端移除
CREATE TABLE IF NOT EXISTS removals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    ref_id      INTEGER NOT NULL,
    rev         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_removals_task ON removals(task_id, rev);

-- 影片管理：影片根目录（来自 APHRODITE_FILM_DIRS，启动时同步）
CREATE TABLE IF NOT EXISTS film_dirs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    path    TEXT    NOT NULL UNIQUE,
    label   TEXT    NOT NULL,
    valid   INTEGER NOT NULL DEFAULT 1
);

-- 影片管理：文件条目，只存文件夹与视频文件（缩略图等附属文件不入库）
CREATE TABLE IF NOT EXISTS film_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dir_id     INTEGER NOT NULL,
    parent_rel TEXT    NOT NULL DEFAULT '',  -- 所在目录的 posix 相对路径，'' 表示根级
    rel_path   TEXT    NOT NULL,             -- 自身 posix 相对路径（含文件名）
    name       TEXT    NOT NULL,
    is_dir     INTEGER NOT NULL DEFAULT 0,
    size       INTEGER NOT NULL DEFAULT 0,
    mtime      REAL    NOT NULL DEFAULT 0,
    code       TEXT,                         -- 从文件名提取的番号
    thumb_rel  TEXT,                         -- 同目录同名缩略图的相对路径
    rev        INTEGER NOT NULL DEFAULT 0,
    created_at REAL    NOT NULL,
    UNIQUE(dir_id, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_film_entries_level ON film_entries(dir_id, parent_rel);

-- 影片工具任务（整理目录 / 补缩略图）的条目明细
CREATE TABLE IF NOT EXISTS film_task_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL,
    dir_id      INTEGER NOT NULL,          -- 所属影片根目录
    rel_path    TEXT    NOT NULL,          -- 视频的 posix 相对路径
    name        TEXT    NOT NULL,          -- 视频文件名
    target      TEXT    NOT NULL DEFAULT '', -- 整理：目标目录名；补图：生成的缩略图路径
    status      TEXT    NOT NULL DEFAULT 'pending', -- pending/done/skipped/failed
    error       TEXT,
    rev         INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_film_items_task ON film_task_items(task_id, status);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        c = sqlite3.connect(str(DB_PATH), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _local.c = c
    return c


def next_rev() -> int:
    with _rev_lock:
        return next(_rev_counter)


def init() -> None:
    ensure_dirs()
    with _write_lock, conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)
        row = c.execute(
            "SELECT MAX(r) AS m FROM ("
            " SELECT MAX(rev) AS r FROM tasks"
            " UNION ALL SELECT MAX(rev) FROM items"
            " UNION ALL SELECT MAX(rev) FROM images"
            " UNION ALL SELECT MAX(rev) FROM film_entries"
            " UNION ALL SELECT MAX(rev) FROM film_task_items)"
        ).fetchone()
        start = (row["m"] or 0) + 1
    global _rev_counter
    with _rev_lock:
        _rev_counter = itertools.count(start)
    backfilled = library_backfill()
    if backfilled:
        print(f"[db] 已将 {backfilled} 个番号的历史下载结果归入统一图库")
    moved = migrate_files_to_library()
    if moved:
        print(f"[db] 已把 {moved} 个文件移动到统一图库 data/images/library/")


def _migrate(c: sqlite3.Connection) -> None:
    """老库补列（CREATE TABLE IF NOT EXISTS 不会新增列）。"""
    item_cols = {r["name"] for r in c.execute("PRAGMA table_info(items)")}
    if "cached" not in item_cols:
        c.execute("ALTER TABLE items ADD COLUMN cached INTEGER NOT NULL DEFAULT 0")
    if "link" not in item_cols:
        c.execute("ALTER TABLE items ADD COLUMN link TEXT")
    image_cols = {r["name"] for r in c.execute("PRAGMA table_info(images)")}
    if "attempts" not in image_cols:
        c.execute("ALTER TABLE images ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
    task_cols = {r["name"] for r in c.execute("PRAGMA table_info(tasks)")}
    if "kind" not in task_cols:
        c.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'scrape'")
    lib_cols = {r["name"] for r in c.execute("PRAGMA table_info(library)")}
    if "actresses" not in lib_cols:
        c.execute("ALTER TABLE library ADD COLUMN actresses TEXT")
    c.commit()


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


# ---------------------------------------------------------------- 写入

def create_task(raw_text: str, entries: Iterable[Any]) -> int:
    """建任务。

    entries 支持三种写法：纯番号字符串 / (code, link) 元组 / {"code","link"} 字典，
    便于老调用方（只传番号）继续工作。
    """
    rows: list[tuple[str, str | None]] = []
    for e in entries:
        if isinstance(e, str):
            rows.append((e, None))
        elif isinstance(e, dict):
            rows.append((str(e["code"]), e.get("link")))
        else:
            code, link = e  # type: ignore[misc]
            rows.append((str(code), link))

    now = time.time()
    with _write_lock, conn() as c:
        cur = c.execute(
            "INSERT INTO tasks(raw_text, status, total, finished, rev, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (raw_text, TASK_PENDING, len(rows), 0, next_rev(), now, now),
        )
        task_id = int(cur.lastrowid)
        for i, (code, link) in enumerate(rows):
            c.execute(
                "INSERT INTO items(task_id, seq, code, link, status, rev) VALUES(?,?,?,?,?,?)",
                (task_id, i, code, link, ITEM_PENDING, next_rev()),
            )
        c.commit()
    return task_id


def add_images(task_id: int, item_id: int, images: list[tuple[int, str, str]]) -> list[int]:
    """images: [(seq, kind, url), ...]"""
    ids: list[int] = []
    with _write_lock, conn() as c:
        for seq, kind, url in images:
            cur = c.execute(
                "INSERT INTO images(task_id, item_id, seq, kind, url, status, rev)"
                " VALUES(?,?,?,?,?,?,?)",
                (task_id, item_id, seq, kind, url, IMG_PENDING, next_rev()),
            )
            ids.append(int(cur.lastrowid))
        c.commit()
    return ids


def clear_images(item_id: int) -> None:
    with _write_lock, conn() as c:
        c.execute("DELETE FROM images WHERE item_id=?", (item_id,))
        c.commit()


def touch_task(task_id: int, **fields: Any) -> None:
    fields["rev"] = next_rev()
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _write_lock, conn() as c:
        c.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*fields.values(), task_id))
        c.commit()


def set_item(item_id: int, **fields: Any) -> None:
    fields["rev"] = next_rev()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _write_lock, conn() as c:
        c.execute(f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id))
        c.commit()


def set_image(image_id: int, **fields: Any) -> None:
    fields["rev"] = next_rev()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _write_lock, conn() as c:
        c.execute(f"UPDATE images SET {sets} WHERE id=?", (*fields.values(), image_id))
        c.commit()


def refresh_counts(task_id: int) -> tuple[int, int, bool]:
    """统计已完成数量，返回 (finished, total, all_terminal)。"""
    c = conn()
    rows = c.execute("SELECT status FROM items WHERE task_id=?", (task_id,)).fetchall()
    total = len(rows)
    finished = sum(1 for r in rows if r["status"] in ITEM_TERMINAL)
    with _write_lock, conn() as cc:
        cc.execute(
            "UPDATE tasks SET finished=?, total=? WHERE id=?", (finished, total, task_id)
        )
        cc.commit()
    return finished, total, finished >= total and total > 0


# ---------------------------------------------------------------- 读取

def get_task(task_id: int) -> dict[str, Any] | None:
    return _row(conn().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


def get_item(item_id: int) -> dict[str, Any] | None:
    return _row(conn().execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def list_tasks(limit: int = 200) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_items(task_id: int) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM items WHERE task_id=? ORDER BY seq, id", (task_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_images(task_id: int) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM images WHERE task_id=? ORDER BY item_id, seq, id", (task_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def snapshot(task_id: int) -> dict[str, Any]:
    task = get_task(task_id)
    return {
        "task": task,
        "items": list_items(task_id),
        "images": list_images(task_id),
    }


def changes_since(task_id: int, rev: int) -> dict[str, Any]:
    c = conn()
    task = _row(c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())
    items = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM items WHERE task_id=? AND rev>? ORDER BY rev", (task_id, rev)
        ).fetchall()
    ]
    images = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM images WHERE task_id=? AND rev>? ORDER BY rev LIMIT 5000",
            (task_id, rev),
        ).fetchall()
    ]
    removed = {"items": [], "images": []}
    for r in c.execute(
        "SELECT kind, ref_id, rev FROM removals WHERE task_id=? AND rev>? ORDER BY rev",
        (task_id, rev),
    ).fetchall():
        bucket = removed["items"] if r["kind"] == "item" else removed["images"]
        bucket.append(int(r["ref_id"]))
    new_rev = rev
    if task:
        new_rev = max(new_rev, task["rev"])
    for r in items + images:
        new_rev = max(new_rev, r["rev"])
    max_removal = c.execute(
        "SELECT MAX(rev) AS m FROM removals WHERE task_id=?", (task_id,)
    ).fetchone()
    new_rev = max(new_rev, int(max_removal["m"] or 0))
    return {"task": task, "items": items, "images": images, "removed": removed, "rev": new_rev}


def resumable_tasks() -> list[int]:
    rows = conn().execute(
        "SELECT id FROM tasks WHERE status IN (?,?) ORDER BY id", (TASK_PENDING, TASK_RUNNING)
    ).fetchall()
    return [int(r["id"]) for r in rows]


def item_images(item_id: int) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM images WHERE item_id=? ORDER BY seq, id", (item_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def add_images_full(
    task_id: int, item_id: int, rows: list[tuple[int, str, str, str | None, str]]
) -> list[int]:
    """rows: [(seq, kind, url, path, status), ...]"""
    ids: list[int] = []
    with _write_lock, conn() as c:
        for seq, kind, url, path, status in rows:
            cur = c.execute(
                "INSERT INTO images(task_id, item_id, seq, kind, url, path, status, rev)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (task_id, item_id, seq, kind, url, path, status, next_rev()),
            )
            ids.append(int(cur.lastrowid))
        c.commit()
    return ids


# ---------------------------------------------------------------- 删除 / 重试

def delete_task(task_id: int) -> None:
    with _write_lock, conn() as c:
        c.execute("DELETE FROM images WHERE task_id=?", (task_id,))
        c.execute("DELETE FROM items WHERE task_id=?", (task_id,))
        c.execute("DELETE FROM removals WHERE task_id=?", (task_id,))
        c.execute("DELETE FROM film_task_items WHERE task_id=?", (task_id,))
        c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        c.commit()


def delete_item(task_id: int, item_id: int) -> int:
    """删除单个条目及其图片行，返回删除的图片数。

    只删任务内的记录，本地图库文件保留（其它任务还可能复用）。
    """
    with _write_lock, conn() as c:
        row = c.execute(
            "SELECT id FROM items WHERE id=? AND task_id=?", (item_id, task_id)
        ).fetchone()
        if not row:
            return 0
        image_ids = [
            int(r["id"]) for r in c.execute("SELECT id FROM images WHERE item_id=?", (item_id,))
        ]
        c.execute("DELETE FROM images WHERE item_id=?", (item_id,))
        c.execute("DELETE FROM items WHERE id=?", (item_id,))
        for image_id in image_ids:
            c.execute(
                "INSERT INTO removals(task_id, kind, ref_id, rev) VALUES(?,?,?,?)",
                (task_id, "image", image_id, next_rev()),
            )
        c.execute(
            "INSERT INTO removals(task_id, kind, ref_id, rev) VALUES(?,?,?,?)",
            (task_id, "item", item_id, next_rev()),
        )
        c.commit()
    return len(image_ids)


def reset_for_retry(task_id: int) -> int:
    """把失败图片重置为待下载并清空重试次数，返回重置的图片数。"""
    rev = next_rev()
    with _write_lock, conn() as c:
        cur = c.execute(
            "UPDATE images SET status=?, attempts=0, error=NULL, rev=?"
            " WHERE task_id=? AND status=?",
            (IMG_PENDING, rev, task_id, IMG_FAILED),
        )
        reset_images = cur.rowcount
        c.execute(
            "UPDATE items SET status=?, error=NULL, rev=? WHERE task_id=? AND status=?",
            (ITEM_PENDING, rev, task_id, ITEM_FAILED),
        )
        c.execute(
            "UPDATE items SET status=?, error=NULL, rev=? WHERE task_id=? AND id IN"
            " (SELECT DISTINCT item_id FROM images WHERE task_id=? AND status=?)",
            (ITEM_PENDING, rev, task_id, task_id, IMG_PENDING),
        )
        c.commit()
    return reset_images


# ---------------------------------------------------------------- 影片管理

def film_sync_dirs(paths: list[str]) -> list[dict[str, Any]]:
    """把环境变量里的影片目录同步进 film_dirs：新路径插入，消失的路径标记失效。"""
    with _write_lock, conn() as c:
        existing = {r["path"]: r for r in c.execute("SELECT * FROM film_dirs").fetchall()}
        for p in paths:
            norm = p.rstrip("/\\")
            if norm in existing:
                if existing[norm]["valid"] != 1:
                    c.execute("UPDATE film_dirs SET valid=1 WHERE path=?", (norm,))
            else:
                c.execute(
                    "INSERT INTO film_dirs(path, label) VALUES(?,?)",
                    (norm, Path(norm).name or norm),
                )
        known = {p.rstrip("/\\") for p in paths}
        for p in existing:
            if p not in known:
                c.execute("UPDATE film_dirs SET valid=0 WHERE path=?", (p,))
        c.commit()
    return film_list_dirs()


def film_list_dirs() -> list[dict[str, Any]]:
    rows = conn().execute("SELECT * FROM film_dirs ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def film_get_dir(dir_id: int) -> dict[str, Any] | None:
    return _row(conn().execute("SELECT * FROM film_dirs WHERE id=?", (dir_id,)).fetchone())


def film_set_dir_valid(dir_id: int, valid: int) -> None:
    with _write_lock, conn() as c:
        c.execute("UPDATE film_dirs SET valid=? WHERE id=?", (valid, dir_id))
        c.commit()


def film_get_entry(entry_id: int) -> dict[str, Any] | None:
    return _row(conn().execute("SELECT * FROM film_entries WHERE id=?", (entry_id,)).fetchone())


def film_list_level(dir_id: int, parent_rel: str) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM film_entries WHERE dir_id=? AND parent_rel=?"
        " ORDER BY is_dir DESC, name COLLATE NOCASE",
        (dir_id, parent_rel),
    ).fetchall()
    return [dict(r) for r in rows]


def film_replace_level(
    dir_id: int, parent_rel: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """把磁盘扫描结果与该级 DB 行 diff：新增插入、消失删除、变化更新。

    rows 由 films.scan_level() 产出：[{rel_path, name, is_dir, size, mtime, code, thumb_rel}]。
    返回该级最新的 DB 行（与 film_list_level 同序）。
    """
    now = time.time()
    with _write_lock, conn() as c:
        current = {
            r["rel_path"]: r
            for r in c.execute(
                "SELECT * FROM film_entries WHERE dir_id=? AND parent_rel=?",
                (dir_id, parent_rel),
            ).fetchall()
        }
        disk = {r["rel_path"]: r for r in rows}
        for rel in current:
            if rel not in disk:
                c.execute(
                    "DELETE FROM film_entries WHERE id=?",
                    (int(current[rel]["id"]),),
                )
        for rel, row in disk.items():
            old = current.get(rel)
            if old is None:
                c.execute(
                    "INSERT INTO film_entries(dir_id, parent_rel, rel_path, name, is_dir,"
                    " size, mtime, code, thumb_rel, rev, created_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        dir_id,
                        parent_rel,
                        rel,
                        row["name"],
                        row["is_dir"],
                        row["size"],
                        row["mtime"],
                        row["code"],
                        row["thumb_rel"],
                        next_rev(),
                        now,
                    ),
                )
            elif (
                old["name"] != row["name"]
                or old["size"] != row["size"]
                or old["mtime"] != row["mtime"]
                or old["code"] != row["code"]
                or old["thumb_rel"] != row["thumb_rel"]
            ):
                c.execute(
                    "UPDATE film_entries SET name=?, size=?, mtime=?, code=?, thumb_rel=?, rev=?"
                    " WHERE id=?",
                    (
                        row["name"],
                        row["size"],
                        row["mtime"],
                        row["code"],
                        row["thumb_rel"],
                        next_rev(),
                        int(old["id"]),
                    ),
                )
        c.commit()
    return film_list_level(dir_id, parent_rel)


def film_rename_entry(entry_id: int, rel_path: str, name: str, code: str | None,
                      thumb_rel: str | None, mtime: float, size: int) -> None:
    with _write_lock, conn() as c:
        c.execute(
            "UPDATE film_entries SET rel_path=?, name=?, code=?, thumb_rel=?, mtime=?, size=?, rev=?"
            " WHERE id=?",
            (rel_path, name, code, thumb_rel, mtime, size, next_rev(), entry_id),
        )
        c.commit()


def film_delete_entry(entry_id: int) -> None:
    with _write_lock, conn() as c:
        c.execute("DELETE FROM film_entries WHERE id=?", (entry_id,))
        c.commit()


def film_delete_subtree(dir_id: int, rel_path: str) -> int:
    """删掉条目本身及其所有后代行（目录被删除时清理残留），返回删除的行数。"""
    prefix = rel_path + "/"
    with _write_lock, conn() as c:
        cur = c.execute(
            "DELETE FROM film_entries WHERE dir_id=? AND (rel_path=? OR rel_path LIKE ?)",
            (dir_id, rel_path, prefix + "%"),
        )
        n = cur.rowcount
        c.commit()
    return n


# ------------------------------------------------------- 影片工具任务明细

def create_film_task(kind: str, label: str) -> int:
    """建影片工具任务（整理目录 / 补缩略图），条目随后动态追加。"""
    now = time.time()
    with _write_lock, conn() as c:
        cur = c.execute(
            "INSERT INTO tasks(raw_text, kind, status, total, finished, rev, created_at, updated_at)"
            " VALUES(?,?,?,?,0,?,?,?)",
            (label, kind, TASK_RUNNING, 0, next_rev(), now, now),
        )
        task_id = int(cur.lastrowid)
        c.commit()
    return task_id


def film_add_items(task_id: int, rows: Iterable[dict[str, Any]]) -> int:
    """给影片工具任务追加条目，返回追加数；total 同步增加。"""
    values = [
        (
            task_id,
            int(r["dir_id"]),
            r["rel_path"],
            r["name"],
            r.get("target") or "",
            FT_PENDING,
            next_rev(),
            time.time(),
        )
        for r in rows
    ]
    if not values:
        return 0
    with _write_lock, conn() as c:
        c.executemany(
            "INSERT INTO film_task_items"
            "(task_id, dir_id, rel_path, name, target, status, rev, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            values,
        )
        c.execute("UPDATE tasks SET total=total+? WHERE id=?", (len(values), task_id))
        c.commit()
    return len(values)


def film_list_items(task_id: int) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM film_task_items WHERE task_id=? ORDER BY id", (task_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def film_next_pending(task_id: int) -> dict[str, Any] | None:
    """取下一个待处理条目并置为 running 语义（这里用 pending 占位，靠返回即认领）。"""
    with _write_lock, conn() as c:
        row = c.execute(
            "SELECT * FROM film_task_items WHERE task_id=? AND status=?"
            " ORDER BY id LIMIT 1",
            (task_id, FT_PENDING),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        c.commit()
    return item


def film_touch_item(item_id: int, **fields: Any) -> None:
    fields["rev"] = next_rev()
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k}=?" for k in fields)
    with _write_lock, conn() as c:
        c.execute(f"UPDATE film_task_items SET {sets} WHERE id=?", (*fields.values(), item_id))
        c.commit()


def film_finish_item(item_id: int, status: str, error: str | None = None,
                     target: str | None = None) -> None:
    """条目落终态 + 任务 finished 计数 +1。"""
    now = time.time()
    with _write_lock, conn() as c:
        task_id = c.execute(
            "SELECT task_id FROM film_task_items WHERE id=?", (item_id,)
        ).fetchone()["task_id"]
        c.execute(
            "UPDATE film_task_items SET status=?, error=?, target=COALESCE(?,target),"
            " rev=?, updated_at=? WHERE id=?",
            (status, error, target, next_rev(), now, item_id),
        )
        c.execute("UPDATE tasks SET finished=finished+1 WHERE id=?", (task_id,))
        c.commit()


def film_task_summary(task_id: int) -> dict[str, int]:
    c = conn()
    counts = {k: 0 for k in (FT_PENDING, FT_DONE, FT_SKIPPED, FT_FAILED)}
    for r in c.execute(
        "SELECT status, COUNT(*) AS n FROM film_task_items WHERE task_id=?"
        " GROUP BY status",
        (task_id,),
    ):
        counts[r["status"]] = r["n"]
    return counts


def film_failed_items(task_id: int) -> list[dict[str, Any]]:
    rows = conn().execute(
        "SELECT * FROM film_task_items WHERE task_id=? AND status=? ORDER BY id",
        (task_id, FT_FAILED),
    ).fetchall()
    return [dict(r) for r in rows]


def film_running_task(kind: str) -> dict[str, Any] | None:
    row = conn().execute(
        "SELECT * FROM tasks WHERE kind=? AND status IN (?,?) ORDER BY id DESC LIMIT 1",
        (kind, TASK_PENDING, TASK_RUNNING),
    ).fetchone()
    return _row(row)


def film_set_task_running(task_id: int, running: bool) -> None:
    """标记任务状态：false 时若还有条目则 completed，否则视调用方处理。"""
    with _write_lock, conn() as c:
        c.execute(
            "UPDATE tasks SET status=?, rev=?, updated_at=? WHERE id=?",
            (TASK_RUNNING if running else TASK_COMPLETED, next_rev(), time.time(), task_id),
        )
        c.commit()


# ---------------------------------------------------------------- 统一图库

def library_get(code: str) -> dict[str, Any] | None:
    c = conn()
    row = c.execute("SELECT * FROM library WHERE code=?", (code,)).fetchone()
    if not row:
        return None
    data = dict(row)
    if data.get("actresses"):
        try:
            data["actresses"] = json.loads(data["actresses"])
        except ValueError:
            data["actresses"] = []
    else:
        data["actresses"] = []
    data["images"] = [
        dict(r)
        for r in c.execute(
            "SELECT * FROM library_images WHERE code=? ORDER BY seq", (code,)
        ).fetchall()
    ]
    return data


def library_upsert(
    code: str,
    detail_url: str | None,
    title: str | None,
    images: list[dict[str, Any]],
    actresses: list[str] | None = None,
) -> None:
    """把某个番号的最终结果写入图库（pending 状态不写，避免脏数据）。"""
    keep = [i for i in images if i["status"] != IMG_PENDING]
    if not keep:
        return
    actress_json = json.dumps(actresses or [], ensure_ascii=False)
    with _write_lock, conn() as c:
        c.execute(
            "INSERT INTO library(code, detail_url, title, actresses, updated_at)"
            " VALUES(?,?,?,?,?)"
            " ON CONFLICT(code) DO UPDATE SET detail_url=excluded.detail_url,"
            " title=excluded.title, actresses=excluded.actresses,"
            " updated_at=excluded.updated_at",
            (code, detail_url, title, actress_json, time.time()),
        )
        c.execute("DELETE FROM library_images WHERE code=?", (code,))
        c.executemany(
            "INSERT INTO library_images(code, seq, kind, url, path, status)"
            " VALUES(?,?,?,?,?,?)",
            [(code, i["seq"], i["kind"], i["url"], i["path"], i["status"]) for i in keep],
        )
        c.commit()


def library_backfill() -> int:
    """把历史上已下载完成的结果补进图库（只跑一次，图库非空则跳过）。"""
    c = conn()
    if c.execute("SELECT COUNT(*) AS n FROM library").fetchone()["n"]:
        return 0
    rows = c.execute(
        "SELECT i.code, i.detail_url, i.title, im.seq, im.kind, im.url, im.path, im.status"
        " FROM items i JOIN images im ON im.item_id = i.id"
        " WHERE i.status = ? AND im.status = ? AND im.path IS NOT NULL"
        " ORDER BY i.id, im.seq",
        (ITEM_DONE, IMG_DONE),
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        entry = grouped.setdefault(
            r["code"],
            {"code": r["code"], "detail_url": r["detail_url"], "title": r["title"], "images": []},
        )
        entry["images"].append(
            {
                "seq": r["seq"],
                "kind": r["kind"],
                "url": r["url"],
                "path": r["path"],
                "status": r["status"],
            }
        )
    with _write_lock, conn() as wc:
        for entry in grouped.values():
            wc.execute(
                "INSERT OR IGNORE INTO library(code, detail_url, title, updated_at)"
                " VALUES(?,?,?,?)",
                (entry["code"], entry["detail_url"], entry["title"], time.time()),
            )
            wc.executemany(
                "INSERT OR IGNORE INTO library_images(code, seq, kind, url, path, status)"
                " VALUES(?,?,?,?,?,?)",
                [
                    (entry["code"], i["seq"], i["kind"], i["url"], i["path"], i["status"])
                    for i in entry["images"]
                ],
            )
        wc.commit()
    return len(grouped)


def migrate_files_to_library() -> int:
    """把历史遗留的 {任务id}/{番号}/xxx.jpg 迁移到 library/{番号}/，并改写库中路径。

    按文件系统扫描而不是按数据库行，这样连"数据库行被清掉但文件还在"的
    孤儿文件也能归位。
    """
    moved = 0
    try:
        task_dirs = [d for d in IMAGES_DIR.iterdir() if d.is_dir() and d.name.isdigit()]
    except OSError:
        return 0

    for task_dir in sorted(task_dirs):
        try:
            code_dirs = [d for d in task_dir.iterdir() if d.is_dir()]
        except OSError:
            continue
        for code_dir in sorted(code_dirs):
            dst_dir = LIBRARY_DIR / safe_dir_name(code_dir.name)
            try:
                files = [f for f in code_dir.iterdir() if f.is_file()]
            except OSError:
                continue
            for f in sorted(files):
                dst = dst_dir / f.name
                old_rel = f.relative_to(IMAGES_DIR).as_posix()
                try:
                    if dst.exists():
                        f.unlink()  # 图库里已有同名文件，删掉旧副本
                    else:
                        dst_dir.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(f), str(dst))
                except OSError:
                    continue
                new_rel = dst.relative_to(IMAGES_DIR).as_posix()
                with _write_lock, conn() as wc:
                    wc.execute("UPDATE images SET path=? WHERE path=?", (new_rel, old_rel))
                    wc.execute(
                        "UPDATE library_images SET path=? WHERE path=?", (new_rel, old_rel)
                    )
                    wc.commit()
                moved += 1
            try:
                if not any(code_dir.iterdir()):
                    code_dir.rmdir()
            except OSError:
                pass
        try:
            if not any(task_dir.iterdir()):
                task_dir.rmdir()
        except OSError:
            pass
    return moved
