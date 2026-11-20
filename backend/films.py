"""影片管理：本地影片目录的扫描、同步与文件操作。

设计要点（与产品决策一一对应）：
- 「翻到哪扫哪」：每次请求某一级目录时同步 scandir 该级并与 DB diff，
  绝不做全库扫描。
- 番号提取复用 codes.find_codes()，文件名匹配多个时取第一个；
  扫描入库时提取，磁盘与 DB 不一致（改名/内容变化）时重提。
- 缩略图 = 视频同目录下的同名文件（换扩展名），无则显示空。
  thumb_rel 存相对影片根目录的完整 posix 路径。
- 多视频文件夹的拼贴信息（前 4 个有缩略图的视频）在响应时现场统计，
  只做子目录的一次性只读 peek，不写子级 DB。
- 删除 = 真删磁盘文件；执行前校验文件仍在，目录为递归删除。
- 一切磁盘操作都限制在影片根目录白名单之下，防止路径穿越。
"""
from __future__ import annotations

import shutil
import stat
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


def _find_thumb(dir_path: Path, video_stem: str) -> str | None:
    """视频同目录同名缩略图（不同扩展名），返回文件名（不含目录部分）。"""
    try:
        for f in dir_path.iterdir():
            if f.is_file() and f.suffix.lower() in THUMB_EXTS and f.stem == video_stem:
                return f.name
    except OSError:
        pass
    return None


def _read_level(abs_dir: Path, rel: str) -> list[dict]:
    """scandir 一级，产出文件夹 + 视频文件的磁盘状态（跳过隐藏项）。

    rel 为该级相对根目录的 posix 路径（'' 为根级），rel_path / thumb_rel
    都带全前缀。
    """
    rows: list[dict] = []
    try:
        items = sorted(abs_dir.iterdir(), key=lambda f: f.name.lower())
    except FileNotFoundError:
        raise FilmError("目录不存在")
    except PermissionError:
        raise FilmError("目录无权限访问")
    for f in items:
        name = f.name
        if name.startswith("."):
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        prefix = f"{rel}/{name}" if rel else name
        if stat.S_ISDIR(st.st_mode):
            rows.append({
                "rel_path": prefix, "name": name, "is_dir": 1,
                "size": 0, "mtime": st.st_mtime, "code": None, "thumb_rel": None,
            })
        elif f.suffix.lower() in VIDEO_EXTS:
            thumb = _find_thumb(abs_dir, f.stem)
            rows.append({
                "rel_path": prefix, "name": name, "is_dir": 0,
                "size": st.st_size, "mtime": st.st_mtime,
                "code": _extract_code(name),
                "thumb_rel": (f"{rel}/{thumb}" if rel else thumb) if thumb else None,
            })
    return rows


def _dir_info(root: Path, rel: str) -> dict:
    """文件夹的只读 peek：视频数量、拼贴缩略图（相对根目录的完整路径）。

    只读不写——子级的 DB 同步留给用户真正翻进去的那次请求。
    """
    abs_dir = resolve_under_root(root, rel)
    videos: list[tuple[str, str | None]] = []  # (stem, thumb 文件名)
    try:
        for f in abs_dir.iterdir():
            if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                videos.append((f.stem, _find_thumb(abs_dir, f.stem)))
    except OSError:
        pass
    videos.sort(key=lambda v: v[0].lower())
    collage = []
    for _, thumb in videos[:COLLAGE_MAX]:
        if thumb:
            collage.append(f"{rel}/{thumb}" if rel else thumb)
    return {"video_count": len(videos), "collage": collage}


def scan_and_sync(dir_id: int, parent_rel: str) -> list[dict]:
    """同步扫描某级并 diff 更新 DB，返回该级条目（含文件夹拼贴信息）。"""
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
    rows = _read_level(abs_dir, rel)
    entries = db.film_replace_level(dir_id, rel, rows)

    out: list[dict] = []
    for e in entries:
        item = {
            "id": e["id"],
            "name": e["name"],
            "rel_path": e["rel_path"],
            "parent_rel": e["parent_rel"],
            "is_dir": bool(e["is_dir"]),
            "size": e["size"],
            "mtime": e["mtime"],
            "code": e["code"],
            "thumb_rel": e["thumb_rel"],
            "local_path": map_to_local(str(resolve_under_root(root, e["rel_path"]))),
        }
        if e["is_dir"]:
            info = _dir_info(root, e["rel_path"])
            item["video_count"] = info["video_count"]
            item["collage"] = info["collage"]
        out.append(item)
    return out


# ---------------------------------------------------------------- 重命名 / 删除

def _validate_new_name(entry: dict, new_name: str) -> str:
    new_name = (new_name or "").strip()
    if not new_name or "/" in new_name or "\\" in new_name:
        raise FilmError("文件名不能为空，且不能包含路径分隔符")
    if new_name in (".", "..") or new_name.startswith("."):
        raise FilmError("非法文件名")
    old_ext = PurePosixPath(entry["name"]).suffix
    new_ext = PurePosixPath(new_name).suffix
    if entry["is_dir"]:
        if new_ext:
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

    # 同名缩略图一起改名（保持原缩略图扩展名）
    old_thumb_name = PurePosixPath(entry["thumb_rel"]).name if entry["thumb_rel"] else None
    new_thumb_name: str | None = None
    if old_thumb_name:
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
        thumb = None
        size, mtime = 0, st.st_mtime
        db.film_delete_subtree(int(entry["dir_id"]), rel)  # 后代行全部失效，重进时重建
        db.film_delete_entry(entry_id)
        db.film_replace_level(int(entry["dir_id"]), entry["parent_rel"], _read_level(parent, entry["parent_rel"]))
    else:
        thumb = (f"{entry['parent_rel']}/{new_thumb_name}" if entry["parent_rel"] else new_thumb_name) \
            if new_thumb_name else _find_thumb(parent, PurePosixPath(new_name).stem)
        if thumb and entry["parent_rel"]:
            thumb = f"{entry['parent_rel']}/{PurePosixPath(thumb).name}"
        size, mtime = st.st_size, st.st_mtime
        db.film_rename_entry(entry_id, new_rel, new_name, code, thumb, mtime, size)
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
