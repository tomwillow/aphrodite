"""后台任务执行器：逐番号搜索 → 抓详情 → 下载图片。

- 图片统一落在 data/images/library/<番号>/，已在图库中的番号直接复用，不再联网。
- 每张图最多尝试 MAX_IMAGE_ATTEMPTS 次，失败后等待用户手动重试。
- 支持取消：任务被删除时，执行线程在下一个检查点退出并清理残留数据。
"""
from __future__ import annotations

import hashlib
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import db
from .config import (
    DATA_DIR,
    IMAGE_WORKERS,
    IMAGES_DIR,
    LIBRARY_DIR,
    MAX_IMAGE_ATTEMPTS,
    TASK_WORKERS,
    safe_dir_name,
)
from .crawler import CrawlError, NotFound, client


def _safe_name(code: str) -> str:
    return safe_dir_name(code)


def _existing(target_dir: Path, stem: str) -> Path | None:
    for ext in (".jpg", ".png", ".webp", ".gif"):
        p = target_dir / f"{stem}{ext}"
        if p.exists() and p.stat().st_size > 1024:
            return p
    return None


# ---------------------------------------------------------------- 占位图黑名单
# 图床对不存在的图返回同一张 "NOW PRINTING" 占位图。番号内重复 → 必为占位图，
# 记录其内容哈希，之后任何任务遇到同内容图片直接跳过（自学习）。
_PLACEHOLDER_FILE = DATA_DIR / "placeholder_hashes.txt"
_ph_lock = threading.Lock()
_placeholder_hashes: set[str] | None = None


def _load_placeholders() -> set[str]:
    global _placeholder_hashes
    with _ph_lock:
        if _placeholder_hashes is None:
            hashes: set[str] = set()
            try:
                hashes = {
                    line.strip().lower()
                    for line in _PLACEHOLDER_FILE.read_text().splitlines()
                    if line.strip()
                }
            except OSError:
                pass
            _placeholder_hashes = hashes
        return _placeholder_hashes


def _remember_placeholder(digest: str) -> None:
    hashes = _load_placeholders()
    with _ph_lock:
        if digest in hashes:
            return
        hashes.add(digest)
    try:
        with _PLACEHOLDER_FILE.open("a", encoding="utf-8") as f:
            f.write(digest + "\n")
    except OSError:
        pass


class _DuplicateGuard:
    """同一个番号下内容完全相同的图片只保留第一张。

    check() 返回 (是否为新图, 内容md5)。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: set[str] = set()

    def check(self, content: bytes) -> tuple[bool, str]:
        digest = hashlib.md5(content).hexdigest()
        if digest in _load_placeholders():
            return False, digest
        with self._lock:
            if digest in self._seen:
                return False, digest
            self._seen.add(digest)
            return True, digest


class Runner:
    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=TASK_WORKERS, thread_name_prefix="task")
        self._running: set[int] = set()
        self._cancelled: set[int] = set()
        # 条目级取消：删掉某一行时，正在下载的图片要立刻停下
        self._cancelled_items: set[int] = set()
        self._item_pools: dict[int, ThreadPoolExecutor] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 调度
    def submit(self, task_id: int) -> bool:
        """提交执行；已在执行中的任务返回 False。"""
        with self._lock:
            if task_id in self._running:
                return False
            self._cancelled.discard(task_id)
            self._running.add(task_id)
        self._pool.submit(self._run_guarded, task_id)
        return True

    def cancel(self, task_id: int) -> None:
        with self._lock:
            self._cancelled.add(task_id)

    def is_cancelled(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._cancelled

    def cancel_item(self, item_id: int) -> None:
        """取消某个条目的下载：置标记 + 丢弃该条目排队中的图片任务。"""
        with self._lock:
            self._cancelled_items.add(item_id)
            pool = self._item_pools.pop(item_id, None)
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def is_item_cancelled(self, item_id: int) -> bool:
        with self._lock:
            return item_id in self._cancelled_items

    def resume(self) -> None:
        for task_id in db.resumable_tasks():
            self.submit(task_id)

    def _run_guarded(self, task_id: int) -> None:
        cancelled = False
        try:
            self._run(task_id)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            if not self.is_cancelled(task_id):
                db.touch_task(task_id, status=db.TASK_FAILED, error=f"任务异常: {exc}")
        finally:
            cancelled = self.is_cancelled(task_id)
            with self._lock:
                self._running.discard(task_id)
                self._cancelled.discard(task_id)
            if cancelled:
                # 清理删除瞬间可能写入的残留行
                db.delete_task(task_id)

    # ------------------------------------------------------------ 主流程
    def _run(self, task_id: int) -> None:
        if not db.get_task(task_id):
            return
        db.touch_task(task_id, status=db.TASK_RUNNING, error=None)

        for item in db.list_items(task_id):
            if self.is_cancelled(task_id):
                return
            item_id = int(item["id"])
            if self.is_item_cancelled(item_id) or not db.get_item(item_id):
                continue  # 条目已被用户在页面上删除
            images = db.item_images(item_id)
            pending = [i for i in images if i["status"] == db.IMG_PENDING]
            if item["status"] == db.ITEM_NOTFOUND:
                continue
            if images and not pending:
                # 已处理完（含失败的图片，等用户手动重试）
                continue
            self._process_item(task_id, item, images)
            finished, total, _ = db.refresh_counts(task_id)
            db.touch_task(task_id, status=db.TASK_RUNNING, finished=finished, total=total)

        if self.is_cancelled(task_id):
            return

        finished, total, _ = db.refresh_counts(task_id)
        items = db.list_items(task_id)
        images = db.list_images(task_id)
        notfound = sum(1 for i in items if i["status"] == db.ITEM_NOTFOUND)
        failed_items = sum(1 for i in items if i["status"] == db.ITEM_FAILED)
        failed_images = sum(1 for i in images if i["status"] == db.IMG_FAILED)
        notes = []
        if notfound:
            notes.append(f"{notfound} 个番号站内未找到")
        if failed_items:
            notes.append(f"{failed_items} 个番号处理失败")
        if failed_images:
            notes.append(f"{failed_images} 张图片下载失败")
        db.touch_task(
            task_id,
            status=db.TASK_COMPLETED,
            finished=finished,
            total=total,
            error="，".join(notes) or None,
        )

    # ------------------------------------------------------------ 单个番号
    def _process_item(self, task_id: int, item: dict, images: list[dict]) -> None:
        item_id = int(item["id"])
        code = str(item["code"])
        actresses: list[str] | None = None
        if self.is_item_cancelled(item_id) or not db.get_item(item_id):
            return

        if not images:
            if self._reuse_from_library(task_id, item):
                images = db.item_images(item_id)
            elif self._resolve(task_id, item):
                images = db.item_images(item_id)
                lib = db.library_get(code)
                actresses = lib["actresses"] if lib else None
            else:
                return

        pending = [i for i in images if i["status"] == db.IMG_PENDING]
        if not pending:
            lib = db.library_get(code)
            self._finalize_item(item_id, code, lib["actresses"] if lib else None)
            return

        db.set_item(item_id, status=db.ITEM_DOWNLOADING, error=None)
        fresh = db.get_item(item_id) or item
        target_dir = LIBRARY_DIR / _safe_name(code)
        target_dir.mkdir(parents=True, exist_ok=True)

        referer = fresh.get("detail_url")
        guard = _DuplicateGuard()
        pool = ThreadPoolExecutor(max_workers=IMAGE_WORKERS, thread_name_prefix=f"img{code}")
        with self._lock:
            self._item_pools[item_id] = pool
        try:
            for row in pending:
                pool.submit(self._download_one, task_id, row, target_dir, referer, guard)
        finally:
            with self._lock:
                self._item_pools.pop(item_id, None)
            cancelled = self.is_item_cancelled(item_id) or self.is_cancelled(task_id)
            pool.shutdown(wait=not cancelled)

        if self.is_item_cancelled(item_id) or not db.get_item(item_id):
            return
        self._finalize_item(item_id, code, actresses)

    def _reuse_from_library(self, task_id: int, item: dict) -> bool:
        """图库里已有该番号 → 直接建好图片行，完全不联网。"""
        lib = db.library_get(str(item["code"]))
        if not lib or not lib["images"]:
            return False

        rows: list[tuple[int, str, str | None, str | None, str]] = []
        usable = 0
        for im in lib["images"]:
            path = im["path"]
            if im["status"] == db.IMG_DONE and path and (IMAGES_DIR / path).exists():
                rows.append((im["seq"], im["kind"], im["url"], path, db.IMG_DONE))
                usable += 1
            else:
                rows.append((im["seq"], im["kind"], im["url"], None, db.IMG_PENDING))
        if not usable:
            return False

        if self.is_item_cancelled(int(item["id"])) or not db.get_item(int(item["id"])):
            return False
        db.clear_images(int(item["id"]))
        db.add_images_full(task_id, int(item["id"]), rows)
        db.set_item(
            int(item["id"]),
            status=db.ITEM_DOWNLOADING,
            cached=1,
            detail_url=lib["detail_url"],
            title=lib["title"],
            error=None,
        )
        return True

    def _resolve(self, task_id: int, item: dict) -> bool:
        """联网搜索番号 + 抓详情页，建立图片行。"""
        item_id = int(item["id"])
        code = str(item["code"])

        db.set_item(item_id, status=db.ITEM_SEARCHING, error=None, cached=0)
        try:
            hit = client().search(code)
        except NotFound:
            db.set_item(item_id, status=db.ITEM_NOTFOUND, error="站内未找到该番号")
            return False
        except CrawlError as exc:
            db.set_item(item_id, status=db.ITEM_FAILED, error=str(exc))
            return False

        detail_url = hit["detail_url"]
        try:
            detail = client().detail(detail_url)
        except NotFound:
            db.set_item(
                item_id, status=db.ITEM_NOTFOUND, error="详情页不存在", detail_url=detail_url
            )
            return False
        except CrawlError as exc:
            db.set_item(item_id, status=db.ITEM_FAILED, error=str(exc), detail_url=detail_url)
            return False

        remote: list[tuple[int, str, str]] = [(0, "cover", detail["cover"])]
        for i, url in enumerate(detail["samples"], start=1):
            remote.append((i, "sample", url))
        actresses: list[str] = detail.get("actresses") or []

        # 搜索/详情页请求期间条目可能已被删除，别再把图片行写回去
        if self.is_item_cancelled(item_id) or not db.get_item(item_id):
            return False

        db.clear_images(item_id)
        db.add_images(task_id, item_id, remote)
        db.set_item(
            item_id,
            status=db.ITEM_DOWNLOADING,
            detail_url=detail_url,
            title=detail["title"] or hit.get("title") or "",
            error=None,
        )
        return True

    def _finalize_item(self, item_id: int, code: str, actresses: list[str] | None = None) -> None:
        row = db.get_item(item_id)
        if not row or self.is_item_cancelled(item_id):
            return  # 条目已被删除
        images = db.item_images(item_id)
        if any(i["status"] == db.IMG_PENDING for i in images):
            return
        ok = any(i["status"] in (db.IMG_DONE, db.IMG_SKIPPED) for i in images)
        db.set_item(
            item_id,
            status=db.ITEM_DONE if ok else db.ITEM_FAILED,
            error=None if ok else "所有图片下载失败",
        )
        db.library_upsert(code, row.get("detail_url"), row.get("title"), images, actresses)

    # ------------------------------------------------------------ 单张图片
    def _download_one(
        self,
        task_id: int,
        row: dict,
        target_dir: Path,
        referer: str | None,
        guard: _DuplicateGuard,
    ) -> bool:
        image_id = int(row["id"])
        item_id = int(row["item_id"])
        seq = int(row["seq"])
        kind = str(row["kind"])
        url = row.get("url")
        if not url:
            db.set_image(image_id, status=db.IMG_FAILED, error="缺少图片地址")
            return False

        stem = "cover" if kind == "cover" else f"sample-{seq:02d}"
        cached = _existing(target_dir, stem)
        if cached is not None:
            db.set_image(
                image_id,
                status=db.IMG_DONE,
                path=cached.relative_to(IMAGES_DIR).as_posix(),
                error=None,
            )
            return True

        attempt = int(row.get("attempts") or 0)
        last_err = "未知错误"
        while attempt < MAX_IMAGE_ATTEMPTS:
            if self.is_cancelled(task_id) or self.is_item_cancelled(item_id):
                return False
            attempt += 1
            db.set_image(image_id, status=db.IMG_DOWNLOADING, attempts=attempt, error=None)
            try:
                content, ext = client().download(url, referer=referer)
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)[:200] or exc.__class__.__name__
                if attempt < MAX_IMAGE_ATTEMPTS:
                    time.sleep(min(1.2 * attempt, 4.0))
                continue

            if self.is_item_cancelled(item_id):
                return False  # 条目已被删除，图片不再落盘

            is_new, digest = guard.check(content)
            if not is_new:
                _remember_placeholder(digest)
                db.set_image(
                    image_id,
                    status=db.IMG_SKIPPED,
                    error="站点无此详情图（返回占位图），已跳过",
                )
                return False

            path = target_dir / f"{stem}{ext}"
            try:
                path.write_bytes(content)
            except OSError as exc:
                db.set_image(
                    image_id,
                    status=db.IMG_FAILED,
                    attempts=attempt,
                    error=f"写入失败: {exc}",
                )
                return False

            db.set_image(
                image_id,
                status=db.IMG_DONE,
                path=path.relative_to(IMAGES_DIR).as_posix(),
                attempts=attempt,
                error=None,
            )
            return True

        db.set_image(
            image_id,
            status=db.IMG_FAILED,
            attempts=attempt,
            error=f"{last_err}（已尝试 {attempt} 次）",
        )
        return False

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


runner = Runner()
