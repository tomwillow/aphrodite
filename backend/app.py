"""FastAPI 应用：REST + SSE + 静态资源 + 前端 SPA。"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .codes import parse_text
from .config import IMAGES_DIR, WEB_DIST, ensure_dirs
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
    return {"tasks": db.list_tasks()}


@app.get("/api/tasks/{task_id}")
def api_task(task_id: int) -> dict[str, Any]:
    snap = db.snapshot(task_id)
    if not snap["task"]:
        raise HTTPException(status_code=404, detail="任务不存在")
    return snap


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int) -> dict[str, Any]:
    if not db.get_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    # 先打取消标记，执行线程会在下一个检查点退出并清理残留
    runner.cancel(task_id)
    db.delete_task(task_id)
    return {"ok": True}


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
