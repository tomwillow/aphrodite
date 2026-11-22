"""FastAPI 应用：REST + SSE + 静态资源 + 前端 SPA。"""
from __future__ import annotations

import asyncio
import json
import queue
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, filmtools, films
from .codes import parse_text
from .config import FILM_DIRS, FILM_PATH_MAP, IMAGES_DIR, WEB_DIST, ensure_dirs
from .worker import runner


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    ensure_dirs()
    db.init()
    runner.resume()
    yield
    runner.shutdown()


app = FastAPI(title="Aphrodite", lifespan=lifespan)

ensure_dirs()
app.mount("/media", StaticFiles(directory=str(IMAGES_DIR)), name="media")


class CreateTaskIn(BaseModel):
    text: str = ""
    codes: list[str] | None = None


class ParseIn(BaseModel):
    text: str = ""


class FilmRenameIn(BaseModel):
    entry_id: int
    new_name: str = ""


class FilmDeleteIn(BaseModel):
    entry_id: int


class FilmToolIn(BaseModel):
    scope: str = "all"          # all | current
    dir_id: int | None = None   # scope=current 时必填
    path: str = ""              # scope=current 时：从哪个相对路径开始（空 = 该目录根级）
    recursive: bool = True      # 仅 scope=current 有意义：是否遍历子目录


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "time": time.time()}


@app.post("/api/parse")
def api_parse(payload: ParseIn) -> dict[str, Any]:
    return parse_text(payload.text)


@app.post("/api/tasks")
def create_task(payload: CreateTaskIn) -> dict[str, Any]:
    parsed = parse_text(payload.text)
    entries = parsed["entries"]
    if not entries:
        raise HTTPException(status_code=400, detail="没有识别到任何 XXX-XXX 形式的番号")
    if len(entries) > 2000:
        raise HTTPException(status_code=400, detail="单个任务最多 2000 个番号，请分批提交")
    task_id = db.create_task(payload.text, entries)
    runner.submit(task_id)
    return db.snapshot(task_id)


@app.get("/api/tasks")
def api_tasks() -> dict[str, Any]:
    tasks = db.list_tasks()
    for t in tasks:
        if t.get("kind") in (db.TASK_ORGANIZE, db.TASK_THUMB):
            t["summary"] = db.film_task_summary(int(t["id"]))
    return {"tasks": tasks}


@app.get("/api/tasks/{task_id}")
def api_task(task_id: int) -> dict[str, Any]:
    snap = db.snapshot(task_id)
    if not snap["task"]:
        raise HTTPException(status_code=404, detail="任务不存在")
    t = snap["task"]
    if t.get("kind") in (db.TASK_ORGANIZE, db.TASK_THUMB):
        t["summary"] = db.film_task_summary(task_id)
        t["film_items"] = db.film_list_items(task_id)
    return snap


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int) -> dict[str, Any]:
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    # 先打取消标记，执行线程会在下一个检查点退出并清理残留
    if task.get("kind") in (db.TASK_ORGANIZE, db.TASK_THUMB):
        filmtools.runner.cancel(task_id)
    else:
        runner.cancel(task_id)
    db.delete_task(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: int) -> dict[str, Any]:
    """取消影片工具任务（番号抓取沿用删除即取消的语义，不提供单独取消）。"""
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.get("kind") not in (db.TASK_ORGANIZE, db.TASK_THUMB):
        raise HTTPException(status_code=400, detail="番号抓取任务请直接删除")
    if not filmtools.runner.cancel(task_id):
        raise HTTPException(status_code=400, detail="任务不在执行中")
    return {"ok": True}


@app.post("/api/tasks/{task_id}/retry-failed")
def retry_failed_items(task_id: int) -> dict[str, Any]:
    """整理/补图任务失败条目重试：生成新任务，只带失败条目。"""
    try:
        new_id = filmtools.runner.retry_failed(task_id)
    except filmtools.FilmToolError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "task_id": new_id}


@app.delete("/api/tasks/{task_id}/items/{item_id}")
def delete_item(task_id: int, item_id: int) -> dict[str, Any]:
    item = db.get_item(item_id)
    if not item or int(item["task_id"]) != task_id:
        raise HTTPException(status_code=404, detail="条目不存在")
    # 先取消该条目正在进行的图片下载，再删数据行
    runner.cancel_item(item_id)
    removed_images = db.delete_item(task_id, item_id)
    finished, total, _ = db.refresh_counts(task_id)
    db.touch_task(task_id, finished=finished, total=total)
    return {"ok": True, "images": removed_images, "total": total}


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: int) -> dict[str, Any]:
    if not db.get_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    reset = db.reset_for_retry(task_id)
    finished, total, _ = db.refresh_counts(task_id)
    # 还有未处理完的番号（例如任务被中断）也一并恢复
    unfinished = sum(
        1 for i in db.list_items(task_id) if i["status"] not in db.ITEM_TERMINAL
    )
    db.touch_task(
        task_id, status=db.TASK_PENDING, finished=finished, total=total, error=None
    )
    started = runner.submit(task_id) if (reset or unfinished) else False
    return {"reset": reset, "unfinished": unfinished, "started": started}


@app.get("/api/tasks/{task_id}/stream")
async def api_stream(task_id: int, request: Request, rev: int = 0) -> StreamingResponse:
    if not db.get_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")

    async def gen() -> AsyncIterator[bytes]:
        last = rev
        yield b"retry: 2000\n\n"
        idle = 0.0
        while True:
            if await request.is_disconnected():
                break
            data = db.changes_since(task_id, last)
            task = data["task"]
            if task is None:
                # 任务已被删除
                yield _sse("done", {"status": "deleted"})
                break
            if task["rev"] > last:
                yield _sse("task", task)
            if data["items"]:
                yield _sse("items", data["items"])
            if data["images"]:
                yield _sse("images", data["images"])
            if data["removed"]["items"] or data["removed"]["images"]:
                yield _sse("removed", data["removed"])
            if data["rev"] > last:
                last = data["rev"]
                yield _sse("rev", {"rev": last})
                idle = 0.0
            else:
                idle += 0.35
                if idle >= 15:
                    idle = 0.0
                    yield b": ping\n\n"

            if task and task["status"] in (db.TASK_COMPLETED, db.TASK_FAILED):
                yield _sse("done", {"status": task["status"]})
                break
            await asyncio.sleep(0.35)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


# ---------------------------------------------------------------- 影片管理

def _film_error(e: FilmError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _film_tool_error(e: filmtools.FilmToolError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


@app.post("/api/films/tools/organize")
def api_film_tool_organize(payload: FilmToolIn) -> dict[str, Any]:
    """启动「整理番号目录」后台任务，返回任务 id（同类任务并发时 409）。"""
    try:
        task_id = filmtools.runner.start(
            db.TASK_ORGANIZE, payload.scope, payload.dir_id, payload.path,
            payload.recursive
        )
    except filmtools.FilmToolError as e:
        raise _film_tool_error(e)
    return {"ok": True, "task_id": task_id}


@app.post("/api/films/tools/thumbs")
def api_film_tool_thumbs(payload: FilmToolIn) -> dict[str, Any]:
    """启动「补全缩略图」后台任务，返回任务 id（同类任务并发时 409）。"""
    try:
        task_id = filmtools.runner.start(
            db.TASK_THUMB, payload.scope, payload.dir_id, payload.path,
            payload.recursive
        )
    except filmtools.FilmToolError as e:
        raise _film_tool_error(e)
    return {"ok": True, "task_id": task_id}


@app.get("/api/films/dirs")
def api_film_dirs() -> dict[str, Any]:
    dirs = db.film_sync_dirs([str(p) for p in FILM_DIRS])
    return {"dirs": dirs, "mapped": bool(FILM_PATH_MAP or films.FILM_PATH_MAP)}


@app.get("/api/films/entries")
def api_film_entries(dir_id: int, path: str = "", force: int = 0) -> dict[str, Any]:
    """目录条目：TTL 内返回缓存的完整载荷；否则 DB 快照（毫秒级）+ 调度后台扫描。

    前端拿到 scanning=true 的快照先渲染，扫描完成后经 /api/films/stream 推送替换。
    force=1（右上角刷新按钮）绕过 TTL 强制读盘。
    """
    d = db.film_get_dir(dir_id)
    if not d:
        raise HTTPException(status_code=404, detail="影片目录不存在")
    if not d["valid"]:
        raise HTTPException(status_code=400, detail=f"影片目录不可访问：{d['path']}")
    try:
        rel = films._norm_rel(path)
        if not force:
            cached = films.get_cached(dir_id, rel)
            if cached is not None:
                return {"dir": d, "path": rel, "entries": cached, "scanning": False}
        films.schedule_scan(dir_id, rel, force=bool(force))
        snapshot = films.level_snapshot(dir_id, rel)
    except films.FilmError as e:
        raise _film_error(e)
    return {
        "dir": d,
        "path": rel,
        "entries": snapshot,
        "scanning": True,
    }


@app.get("/api/films/stream")
async def api_film_stream(request: Request) -> StreamingResponse:
    """影片扫描结果推送（SSE）：后台扫描完成后推 scan 事件，载荷为该级完整条目。"""
    sub = films.subscribe()

    async def gen() -> AsyncIterator[bytes]:
        try:
            yield _sse("open", {"ok": True})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = sub.get_nowait()
                except queue.Empty:
                    evt = None
                if evt is not None:
                    yield _sse("scan", evt)
                else:
                    await asyncio.sleep(0.35)
        finally:
            films.unsubscribe(sub)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/films/rename")
def api_film_rename(payload: FilmRenameIn) -> dict[str, Any]:
    try:
        return films.rename_entry(payload.entry_id, payload.new_name)
    except films.FilmError as e:
        raise _film_error(e)


@app.post("/api/films/delete")
def api_film_delete(payload: FilmDeleteIn) -> dict[str, Any]:
    try:
        films.delete_entry(payload.entry_id)
    except films.FilmError as e:
        raise _film_error(e)
    return {"ok": True}


@app.get("/api/films/handler")
def api_film_handler() -> dict[str, str]:
    """本机播放协议处理器的脚本内容，供安装提示浮层下载/复制。"""
    return {
        "bat": films.HANDLER_BAT,
        "ps1": films.HANDLER_INSTALL_PS1,
        "readme": films.HANDLER_README,
    }


@app.get("/film-media/{dir_id}/{rel_path:path}")
def film_media(dir_id: int, rel_path: str) -> FileResponse:
    """影片缩略图静态服务：路径必须在影片根目录白名单之下。"""
    d = db.film_get_dir(dir_id)
    if not d:
        raise HTTPException(status_code=404, detail="影片目录不存在")
    try:
        target = films.resolve_under_root(Path(d["path"]), films._norm_rel(rel_path))
    except films.FilmError as e:
        raise _film_error(e)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    # 缩略图基本不变：让浏览器缓存一天，翻页/重进不再从磁盘重读。
    # URL 含完整路径与文件名，改名即新 URL，删除后 404，缓存不会张冠李戴。
    return FileResponse(
        target, headers={"Cache-Control": "public, max-age=86400"}
    )



# ---------------------------------------------------------------- 前端
if (WEB_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="assets")


@app.get("/{full_path:path}")
def spa(full_path: str) -> Any:
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    index = WEB_DIST / "index.html"
    if not index.exists():
        return JSONResponse(
            {
                "detail": "前端尚未构建，请先执行 cd web && npm install && npm run build",
            },
            status_code=503,
        )
    candidate = (WEB_DIST / full_path).resolve()
    if full_path and candidate.is_file() and str(candidate).startswith(str(WEB_DIST.resolve())):
        return FileResponse(candidate)
    return FileResponse(index)
