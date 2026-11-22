"""影片管理：本地影片目录的扫描、同步与文件操作。

设计要点（与产品决策一一对应）：
- 「先出快照，后台扫描」：entries 接口只读 DB（毫秒级），同时调度一次后台扫描；
  扫描完成后把该级完整条目（含拼贴）通过 SSE 推给前端。
- 扫描节流：某级扫描结果缓存 60 秒（SCAN_TTL），期间再访问直接复用缓存，不碰盘；
  force=1（右上角刷新按钮）绕过 TTL 与去重，强制读盘。
- 每级扫描列出该级**全部**条目：文件夹在前、文件在后（同类按名字），
  包含隐藏项（`.` 开头）与非视频文件；图片文件以自身作缩略图，
  其余文件缩略图为空。视频文件提取番号，其余文件不提取。
- 番号提取复用 codes.find_codes()，文件名匹配多个时取第一个；
  扫描入库时提取，磁盘与 DB 不一致（改名/内容变化）时重提。
- 缩略图 = 视频同目录下的同名文件（换扩展名），无则显示空。
  thumb_rel 存相对影片根目录的完整 posix 路径。
  目录扫描时一遍建「stem → 缩略图」索引，杜绝逐视频重扫目录的平方级开销。
- 多视频文件夹的拼贴信息（前 4 个有缩略图的视频）只在后台扫描时现场统计，
  只做子目录的一次性只读 peek，不写子级 DB；快照阶段不带拼贴，等扫描推送。
- 删除 = 真删磁盘文件；执行前校验文件仍在，目录为递归删除。
  重命名/删除后只精准更新 DB 行，再强制调度该级后台扫描（不内嵌重扫）。
- 一切磁盘操作都限制在影片根目录白名单之下，防止路径穿越。
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import stat
import threading
import time
from pathlib import Path, PurePosixPath

from . import db
from .codes import find_codes
from .config import FILM_PATH_MAP

# 视频文件扩展名
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".m4v",
    ".flv", ".ts", ".rm", ".rmvb", ".webm",
}
# 缩略图扩展名（视频同目录同名文件）
THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
# 多视频文件夹拼贴最多取几个视频的缩略图
COLLAGE_MAX = 4
# 文件夹名不允许出现的「真实文件扩展名」（防止把文件夹起成媒体文件的样子）；
# 标题里的点不算：vol.2 / FILE. / 「第 3.5 话」都是合法目录名
DIR_FORBIDDEN_EXTS = VIDEO_EXTS | THUMB_EXTS | {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".exe", ".msi",
    ".pdf", ".txt", ".nfo", ".srt", ".ass", ".sub", ".doc", ".docx",
}
# 某级扫描结果的有效期（秒）：期内再访问直接复用缓存，不碰磁盘
SCAN_TTL = 60.0


class FilmError(Exception):
    """影片操作的预期错误，message 直接展示给用户。"""


# ---------------------------------------------------------------- 路径安全

def _norm_rel(rel: str) -> str:
    """把用户传入的相对路径规整成 posix 相对路径；非法（穿越/绝对路径）直接拒绝。"""
    rel = (rel or "").replace("\\", "/").strip("/")
    pure = PurePosixPath(rel)
    if not rel or str(pure) == ".":
        return ""
    if pure.is_absolute() or any(part in ("..", "") for part in pure.parts):
        raise FilmError("非法路径")
    return "/".join(pure.parts)


def resolve_under_root(root: Path, rel: str) -> Path:
    """root + rel 的绝对路径，保证解析后仍在 root 之内（防穿越）。"""
    root_abs = root.resolve()
    target = (root_abs / rel).resolve()
    if target != root_abs:
        try:
            target.relative_to(root_abs)
        except ValueError:
            raise FilmError("非法路径")
    return target


def map_to_local(server_path: str) -> str | None:
    """容器路径 → 浏览器所在机器的本机路径；未配置映射返回 None。

    映射按最长前缀匹配（config.FILM_PATH_MAP 已按前缀长度降序排好）。
    """
    p = server_path.replace("\\", "/").rstrip("/")
    for server, local in FILM_PATH_MAP:
        if p == server or p.startswith(server + "/"):
            tail = p[len(server):].lstrip("/")
            return local + ("\\" + tail.replace("/", "\\") if tail else "")
    return None


# ---------------------------------------------------------------- 扫描与同步

def _extract_code(name: str) -> str | None:
    codes = find_codes(name)
    return codes[0] if codes else None


def entry_kind(name: str, is_dir: bool) -> str:
    """条目分类，供前端决定图标与展示方式：dir / video / image / file。"""
    if is_dir:
        return "dir"
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in THUMB_EXTS:
        return "image"
    return "file"


def _find_thumb(dir_path: Path, video_stem: str) -> str | None:
    """视频同目录同名缩略图（不同扩展名），返回文件名（不含目录部分）。

    单次调用场景（重命名后查新名缩略图）：一次目录枚举逐项比对。
    """
    try:
        for f in dir_path.iterdir():
            if f.is_file() and f.suffix.lower() in THUMB_EXTS and f.stem == video_stem:
                return f.name
    except OSError:
        pass
    return None


def _read_level(abs_dir: Path, rel: str) -> list[dict]:
    """scandir 一级，产出该级**全部**条目的磁盘状态（含隐藏文件/文件夹）。

    排序：文件夹在前，文件在后，同类按名字（不区分大小写）。
    只做一次目录枚举：先 stat 一遍拿到类型与时间，缩略图索引从同一份结果里建，
    查表 O(1)。rel 为该级相对根目录的 posix 路径（'' 为根级），
    rel_path / thumb_rel 都带全前缀。
    """
    try:
        scanned: list[tuple[Path, os.stat_result]] = []
        for f in abs_dir.iterdir():
            try:
                scanned.append((f, f.stat()))
            except OSError:
                continue  # 断链、权限不足等：跳过这一项，不影响整级
    except FileNotFoundError:
        raise FilmError("目录不存在")
    except PermissionError:
        raise FilmError("目录无权限访问")

    def is_dir_of(item: tuple[Path, os.stat_result]) -> bool:
        return stat.S_ISDIR(item[1].st_mode)

    scanned.sort(key=lambda it: (not is_dir_of(it), it[0].name.lower()))

    thumbs: dict[str, str] = {}
    for f, st in scanned:
        if stat.S_ISREG(st.st_mode) and f.suffix.lower() in THUMB_EXTS:
            thumbs.setdefault(f.stem, f.name)

    rows: list[dict] = []
    for f, st in scanned:
        name = f.name
        prefix = f"{rel}/{name}" if rel else name
        suffix = f.suffix.lower()
        if stat.S_ISDIR(st.st_mode):
            rows.append({
                "rel_path": prefix, "name": name, "is_dir": 1,
                "size": 0, "mtime": st.st_mtime, "code": None, "thumb_rel": None,
            })
            continue
        if suffix in VIDEO_EXTS:
            thumb = thumbs.get(f.stem)
            rows.append({
                "rel_path": prefix, "name": name, "is_dir": 0,
                "size": st.st_size, "mtime": st.st_mtime,
                "code": _extract_code(name),
                "thumb_rel": (f"{rel}/{thumb}" if rel else thumb) if thumb else None,
            })
        elif suffix in THUMB_EXTS:
            # 图片文件以自身作缩略图，卡片上直接显示这张图
            rows.append({
                "rel_path": prefix, "name": name, "is_dir": 0,
                "size": st.st_size, "mtime": st.st_mtime,
                "code": None, "thumb_rel": prefix,
            })
        else:
            # 其余文件（nfo/txt/srt/隐藏文件等）也列出，缩略图为空
            rows.append({
                "rel_path": prefix, "name": name, "is_dir": 0,
                "size": st.st_size, "mtime": st.st_mtime,
                "code": None, "thumb_rel": None,
            })
    return rows


def _dir_info(root: Path, rel: str) -> dict:
    """文件夹的只读 peek：视频数量、拼贴缩略图（相对根目录的完整路径）。

    只做一次子目录枚举（一遍同时收集视频与缩略图索引），只读不写。
    """
    abs_dir = resolve_under_root(root, rel)
    video_stems: list[str] = []
    thumbs: dict[str, str] = {}
    try:
        for f in abs_dir.iterdir():
            suffix = f.suffix.lower()
            if suffix in THUMB_EXTS:
                thumbs.setdefault(f.stem, f.name)
            elif suffix in VIDEO_EXTS:
                video_stems.append(f.stem)
    except OSError:
        pass
    video_stems.sort(key=str.lower)
    collage = []
    for stem in video_stems[:COLLAGE_MAX]:
        thumb = thumbs.get(stem)
        if thumb:
            collage.append(f"{rel}/{thumb}" if rel else thumb)
    return {"video_count": len(video_stems), "collage": collage}


def _assemble(rows: list[dict], root_text: str, peek: dict[str, dict] | None) -> list[dict]:
    """把 DB 行组装成响应条目。peek 为 None 时（快照阶段）不带拼贴信息。

    本机路径用「根路径 + 相对路径」纯文本拼接（rel_path 入库前已消毒），
    不做 Path.resolve()，避免对每个条目发起真实路径解析的系统调用。
    """
    out: list[dict] = []
    for e in rows:
        rel_path = e["rel_path"]
        item = {
            "id": e["id"],
            "name": e["name"],
            "rel_path": rel_path,
            "parent_rel": e["parent_rel"],
            "is_dir": bool(e["is_dir"]),
            "size": e["size"],
            "mtime": e["mtime"],
            "code": e["code"],
            "thumb_rel": e["thumb_rel"],
            "kind": entry_kind(e["name"], bool(e["is_dir"])),
            "local_path": map_to_local(f"{root_text}/{rel_path}"),
        }
        if e["is_dir"] and peek is not None:
            info = peek.get(rel_path)
            if info:
                item["video_count"] = info["video_count"]
                item["collage"] = info["collage"]
        out.append(item)
    return out


def level_snapshot(dir_id: int, parent_rel: str) -> list[dict]:
    """纯 DB 快照（零磁盘 IO，毫秒级）：不含文件夹拼贴信息，等扫描推送补齐。"""
    d = db.film_get_dir(dir_id)
    if not d:
        raise FilmError("影片目录不存在")
    rel = _norm_rel(parent_rel)
    rows = db.film_list_level(dir_id, rel)
    return _assemble(rows, d["path"], None)


def scan_and_sync(dir_id: int, parent_rel: str) -> list[dict]:
    """扫描某级磁盘并与 DB diff，返回该级完整条目（含文件夹拼贴信息）。"""
    d = db.film_get_dir(dir_id)
    if not d:
        raise FilmError("影片目录不存在")
    root = Path(d["path"])
    if not root.is_dir():
        db.film_set_dir_valid(dir_id, 0)
        raise FilmError(f"影片目录不可访问：{d['path']}")
    db.film_set_dir_valid(dir_id, 1)

    rel = _norm_rel(parent_rel)
    abs_dir = resolve_under_root(root, rel)
    t0 = time.monotonic()
    rows = _read_level(abs_dir, rel)
    t_read = time.monotonic()
    entries = db.film_replace_level(dir_id, rel, rows)
    t_db = time.monotonic()

    peek: dict[str, dict] = {}
    for e in entries:
        if e["is_dir"]:
            peek[e["rel_path"]] = _dir_info(root, e["rel_path"])
    t_peek = time.monotonic()

    out = _assemble(entries, d["path"], peek)
    print(
        f"[films] 扫描 {d['label']}/{rel or '根级'}：条目 {len(entries)}，"
        f"本级读取 {int((t_read - t0) * 1000)}ms，写库 {int((t_db - t_read) * 1000)}ms，"
        f"拼贴 peek（{len(peek)} 个文件夹）{int((t_peek - t_db) * 1000)}ms，"
        f"总计 {int((t_peek - t0) * 1000)}ms",
        flush=True,
    )
    return out


# ---------------------------------------------------------------- 后台扫描调度

# 调度器状态（单进程内共享，全部由 _sched_lock 保护）
_sched_lock = threading.Lock()
# key -> {"ts": 完成时间, "entries": 完整载荷}；TTL 内再访问直接复用
_level_cache: dict[tuple[int, str], dict] = {}
# 正在扫描的级别（同级去重，force 也不例外——在扫的结果马上会推回来）
_inflight: set[tuple[int, str]] = set()
# SSE 订阅者：每个客户端一条队列
_subscribers: list[queue.Queue] = []


def subscribe() -> queue.Queue:
    """注册一个 SSE 订阅队列，扫描完成后会收到 {dir_id, parent_rel, entries, error}。"""
    q: queue.Queue = queue.Queue()
    with _sched_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _sched_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def _broadcast(event: dict) -> None:
    with _sched_lock:
        subs = list(_subscribers)
    for q in subs:
        q.put(event)


def get_cached(dir_id: int, parent_rel: str) -> list[dict] | None:
    """TTL 内的扫描结果（完整载荷，含拼贴）；过期或没有返回 None。"""
    key = (dir_id, parent_rel)
    with _sched_lock:
        hit = _level_cache.get(key)
    if hit and time.monotonic() - hit["ts"] < SCAN_TTL:
        return hit["entries"]
    return None


def schedule_scan(dir_id: int, parent_rel: str, force: bool = False) -> bool:
    """调度一次后台扫描；返回是否真的启动了扫描。

    - 同级扫描进行中：不重复开（force 也不例外，在扫的结果马上会推回来）
    - TTL 内已有结果且非 force：不扫
    - force：绕过 TTL，强制读盘
    """
    key = (dir_id, parent_rel)
    with _sched_lock:
        if key in _inflight:
            return False
        if not force:
            hit = _level_cache.get(key)
            if hit and time.monotonic() - hit["ts"] < SCAN_TTL:
                return False
        _inflight.add(key)
    threading.Thread(target=_scan_job, args=(dir_id, parent_rel, key), daemon=True).start()
    return True


def _scan_job(dir_id: int, parent_rel: str, key: tuple[int, str]) -> None:
    try:
        entries = scan_and_sync(dir_id, parent_rel)
        with _sched_lock:
            _level_cache[key] = {"ts": time.monotonic(), "entries": entries}
        _broadcast({"dir_id": dir_id, "parent_rel": parent_rel, "entries": entries})
    except FilmError as e:
        print(f"[films] 扫描 {key[0]}/{key[1] or '根级'} 失败：{e}", flush=True)
        _broadcast({"dir_id": dir_id, "parent_rel": parent_rel, "entries": None,
                    "error": str(e)})
    finally:
        with _sched_lock:
            _inflight.discard(key)


# ---------------------------------------------------------------- 重命名 / 删除

def _validate_new_name(entry: dict, new_name: str) -> str:
    new_name = (new_name or "").strip()
    if not new_name or "/" in new_name or "\\" in new_name:
        raise FilmError("文件名不能为空，且不能包含路径分隔符")
    if new_name in (".", ".."):
        raise FilmError("非法文件名")
    old_ext = PurePosixPath(entry["name"]).suffix
    new_ext = PurePosixPath(new_name).suffix
    if entry["is_dir"]:
        # 只拒绝后缀是真实文件扩展名的名字（如 movie.mp4）——防止把文件夹起成
        # 媒体文件的样子；标题里的点不算：vol.2 / FILE. / 「第 3.5 话」都是合法目录名
        if (new_ext or "").lower() in DIR_FORBIDDEN_EXTS:
            raise FilmError("文件夹名不能包含扩展名")
    elif not new_ext and old_ext:
        new_name += old_ext  # 未带扩展名时自动补上原扩展名
    return new_name


def rename_entry(entry_id: int, new_name: str) -> dict:
    entry = db.film_get_entry(entry_id)
    if not entry:
        raise FilmError("条目不存在")
    d = db.film_get_dir(int(entry["dir_id"]))
    if not d:
        raise FilmError("影片目录不存在")
    root = Path(d["path"])
    rel = _norm_rel(entry["rel_path"])
    abs_path = resolve_under_root(root, rel)
    if not abs_path.exists():
        db.film_delete_entry(entry_id)
        raise FilmError("文件已不存在，请刷新后重试")

    new_name = _validate_new_name(entry, new_name)
    if new_name == entry["name"]:
        raise FilmError("新名称与原名称相同")

    parent = abs_path.parent
    new_abs = parent / new_name
    if new_abs.exists():
        raise FilmError(f"同名文件已存在：{new_name}")

    try:
        abs_path.rename(new_abs)
    except OSError as e:
        raise FilmError(f"重命名失败：{e}")

    # 同名缩略图一起改名（保持原缩略图扩展名）；图片文件以自身为缩略图，无需另改
    old_thumb_name = PurePosixPath(entry["thumb_rel"]).name if entry["thumb_rel"] else None
    thumb_is_self = bool(entry["thumb_rel"]) and entry["thumb_rel"] == rel
    new_thumb_name: str | None = None
    if old_thumb_name and not thumb_is_self:
        new_thumb_name = PurePosixPath(new_name).stem + PurePosixPath(old_thumb_name).suffix.lower()
        if new_thumb_name != old_thumb_name:
            old_thumb = parent / old_thumb_name
            if old_thumb.is_file():
                try:
                    old_thumb.rename(parent / new_thumb_name)
                except OSError:
                    new_thumb_name = old_thumb_name  # 改不动就保留原名
        else:
            new_thumb_name = old_thumb_name

    st = new_abs.stat()
    new_rel = f"{entry['parent_rel']}/{new_name}" if entry["parent_rel"] else new_name
    code = _extract_code(new_name)
    if entry["is_dir"]:
        db.film_delete_subtree(int(entry["dir_id"]), rel)  # 后代行全部失效，重进时重建
        db.film_rename_entry(entry_id, new_rel, new_name, code, None, st.st_mtime, 0)
    else:
        thumb = None
        if thumb_is_self:
            thumb = new_rel  # 图片文件：缩略图就是它自己
        elif new_thumb_name:
            thumb = (f"{entry['parent_rel']}/{new_thumb_name}"
                     if entry["parent_rel"] else new_thumb_name)
        else:
            found = _find_thumb(parent, PurePosixPath(new_name).stem)
            if found:
                thumb = (f"{entry['parent_rel']}/{found}"
                         if entry["parent_rel"] else found)
        db.film_rename_entry(entry_id, new_rel, new_name, code, thumb,
                             st.st_mtime, st.st_size)

    # 不内嵌重扫：强制调度该级后台扫描，结果经 SSE 推回前端
    schedule_scan(int(entry["dir_id"]), entry["parent_rel"], force=True)
    return {"ok": True}


def delete_entry(entry_id: int) -> None:
    entry = db.film_get_entry(entry_id)
    if not entry:
        raise FilmError("条目不存在")
    d = db.film_get_dir(int(entry["dir_id"]))
    if not d:
        raise FilmError("影片目录不存在")
    root = Path(d["path"])
    rel = _norm_rel(entry["rel_path"])
    abs_path = resolve_under_root(root, rel)
    if not abs_path.exists():
        db.film_delete_entry(entry_id)
        raise FilmError("文件已不存在，列表已同步，请刷新")
    try:
        if entry["is_dir"]:
            shutil.rmtree(abs_path)
        else:
            abs_path.unlink()
    except OSError as e:
        raise FilmError(f"删除失败：{e}")
    db.film_delete_subtree(int(entry["dir_id"]), rel)
    schedule_scan(int(entry["dir_id"]), entry["parent_rel"], force=True)


# ---------------------------------------------------------------- 协议处理器脚本

HANDLER_BAT = """@echo off
rem Aphrodite protocol handler: open the path after aphrodite-play://
rem with the system default player.
set "url=%~1"
set "path=%url:aphrodite-play://=%"
if "%path%"=="" exit /b 1
start "" "%path%"
"""

HANDLER_INSTALL_PS1 = r"""# Aphrodite protocol handler installer (run with PowerShell)
# Registers aphrodite-play:// to open files with the system default player.
$ErrorActionPreference = "Stop"

$dir = Join-Path $env:LOCALAPPDATA "AphroditePlay"
New-Item -ItemType Directory -Path $dir -Force | Out-Null

$bat = @'
@echo off
set "url=%~1"
set "path=%url:aphrodite-play://=%"
if "%path%"=="" exit /b 1
start "" "%path%"
'@
$batPath = Join-Path $dir "aphrodite-play.bat"
Set-Content -Path $batPath -Value $bat -Encoding ascii

New-PSDrive -Name HKCR -PSProvider Registry -Root HKEY_CLASSES_ROOT | Out-Null
New-Item -Path "HKCR:\aphrodite-play" -Force | Out-Null
Set-ItemProperty -Path "HKCR:\aphrodite-play" -Name "(Default)" -Value "URL:Aphrodite Play Protocol"
Set-ItemProperty -Path "HKCR:\aphrodite-play" -Name "URL Protocol" -Value ""
New-Item -Path "HKCR:\aphrodite-play\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "HKCR:\aphrodite-play\shell\open\command" -Name "(Default)" `
    -Value "`"$batPath`" `"%1`""

Write-Host "Done. aphrodite-play:// is now registered."
Write-Host "Handler: $batPath"
"""

HANDLER_README = """安装步骤
========
1. 下载 install.ps1（或复制内容保存为 install.ps1）
2. 在 PowerShell 里执行：
   powershell -ExecutionPolicy Bypass -File install.ps1
3. 完成后 aphrodite-play:// 协议即注册成功，回到页面点「打开」
   即可调起系统默认播放器。

工作原理
========
- 注册 aphrodite-play:// 协议，处理器脚本位于
  %LOCALAPPDATA%\\AphroditePlay\\aphrodite-play.bat
- 脚本收到 aphrodite-play://<路径> 后，剥掉协议前缀，
  用 start "" 交给系统默认播放器打开。
- 卸载：删除 %LOCALAPPDATA%\\AphroditePlay 目录，
  并删除注册表项 HKEY_CLASSES_ROOT\\aphrodite-play。
"""
