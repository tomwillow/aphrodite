"""影片工具任务：整理番号目录 / 补全缩略图。

设计要点（与产品决策一一对应）：
- 任务进统一任务列表：tasks 表加 kind 列（scrape=抓取 / organize=整理 / thumb=补图），
  条目明细放 film_task_items 表，进度用 tasks.total/finished，条目逐个落终态。
- 同类任务拒绝并发（"已有同类任务进行中"）；不同类型可以同时跑。
- 整理任务：识别出番号的视频移动到 [番号][演员][标题] 目录（演员 >3 人取前 3
  加 ...，非法字符清洗，标题截断 60 字符）；主图重命名为与视频同名（同名缩略图
  规则：封面 = 视频 stem + 扩展名，已有同名缩略图时覆盖），详情图 = samples
  全量命名 番号-s2.jpg 起；图片来源优先图库命中直接复制，未命中联网抓（走现有
  crawler 限速），抓到的同时写回图库供以后复用。
  视频的同名缩略图跟随移动（保持原文件名，仍符合同名缩略图规则）。
- 目录本身就是这部电影的家（目录名里的番号与视频番号一致、子树里只有一个带番号
  的视频）时就地改名成 [番号][演员][标题]，不再往下包一层；旧版整理留下的
  「目录/标准目录/视频」套娃也会被拍平（内层文件提上来、删掉空壳目录）。
  影片根目录本身永不改名（否则会把注册的影片目录改掉）。无番号的广告片不计入判定。
  判定命中后同一套流程顺带校验目录名 / 封面与视频同名 / 详图齐全：全合格标
  skipped（不重复下载），有改动标 done。
- 不满足上述条件的已整理目录（[番号] 开头）按老规矩整目录跳过：多视频、或视频
  番号与目录番号对不上的，不归整理任务管。
- 补图任务：ffmpeg 为无同名缩略图、文件名无番号的视频抽帧生成同名 jpg；
  已整理目录（[番号]... 开头）不再下钻，里面的视频归整理任务管。
- 条目级跳过在「处理时」再校验一次（枚举到处理之间磁盘可能变化）。
- 失败可重试：任务列表对已结束且含失败条目的任务显示重试按钮，
  点击生成新任务，只带失败条目（retry_failed）。
- 取消 = 停止调度后续条目，任务标 failed「已取消」，已处理条目保留。
- 一切磁盘操作都走 films.resolve_under_root 白名单，防止路径穿越。
- 移动/生成完成后强制调度受影响级别的后台扫描（force 绕过 TTL）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import db
from . import films
from .codes import find_codes
from .config import IMAGES_DIR, LIBRARY_DIR
from .crawler import CrawlError, NotFound, client
from .films import FilmError, VIDEO_EXTS, resolve_under_root, schedule_scan
from .worker import _safe_name

FFMPEG_TIMEOUT = 180  # 单个视频抽帧超时（秒）
IMAGE_ATTEMPTS = 3
TITLE_LIMIT = 60
ACTRESS_LIMIT = 80
THUMB_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
# 已整理目录：[番号] 开头，番号必须含数字（排除 [xxx-yyy] 这类普通命名）。
# $ 可选兼容：早前版本生成过 [$番号] 格式，容忍其继续被识别为已整理。
STANDARD_PREFIX = re.compile(r"^\[\$?([A-Za-z0-9\-]*\d[A-Za-z0-9\-]*)\]")


class FilmToolError(Exception):
    """给用户的工具任务错误（HTTPException 的 detail 直接显示）。"""


# ---------------------------------------------------------------- 目录命名

def _sanitize(text: str, limit: int) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", text or "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:limit].strip()


def build_dir_name(code: str, actresses: list[str] | None, title: str | None) -> str:
    """[番号][演员][标题]；演员超 3 人取前 3 加 ...；空段省略。"""
    name = f"[{code}]"
    names = [n for n in (actresses or []) if n and n.strip()]
    if names:
        keep = [n for n in (_sanitize(a, 30) for a in names[:3]) if n]
        joined = ",".join(keep)
        if len(names) > 3:
            joined += ",..."
        if joined:
            name += f"[{joined[:ACTRESS_LIMIT].rstrip(', ')}]"
    t = _sanitize(title or "", TITLE_LIMIT)
    if t:
        name += f"[{t}]"
    return name


def is_standard_dir(dirname: str) -> bool:
    """目录名以 [$番号] 开头即视为已整理。"""
    return bool(STANDARD_PREFIX.match(dirname))


# ---------------------------------------------------------------- 枚举

def _walk_videos(base: Path, base_rel: str, recursive: bool,
                 verify_standard: bool = False):
    """遍历目录产出 (rel_path, name)；跳过隐藏项。

    已整理目录（[番号] 开头）默认不下钻；verify_standard=True 时，
    若里面**只有一个视频且番号与目录番号一致**，把它交出去走校验/修复流程
    （校验目录名、封面同名、详图齐全），而不是整目录直接跳过。
    """
    try:
        children = sorted(base.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return
    for child in children:
        name = child.name
        if name.startswith("."):
            continue
        rel = f"{base_rel}/{name}" if base_rel else name
        if child.is_dir():
            if is_standard_dir(name):
                if verify_standard:
                    video = _standard_single_video(child, name)
                    if video is not None:
                        yield (f"{rel}/{video.name}", video.name)
                continue
            if recursive:
                yield from _walk_videos(child, rel, recursive, verify_standard)
        elif child.is_file():
            yield (rel, name)


def _standard_single_video(d: Path, dir_name: str) -> Path | None:
    """已整理目录里唯一的视频，且番号与目录番号一致 → 返回它，否则 None。"""
    m = STANDARD_PREFIX.match(dir_name)
    if not m:
        return None
    dir_code = m.group(1).upper()
    try:
        videos = [
            f for f in d.iterdir()
            if f.is_file() and not f.name.startswith(".") and _is_video(f.name)
        ]
    except OSError:
        return None
    if len(videos) != 1:
        return None
    codes = find_codes(videos[0].name)
    if not codes or codes[0].upper() != dir_code:
        return None
    return videos[0]


def _is_video(name: str) -> bool:
    return Path(name).suffix.lower() in VIDEO_EXTS


def _dir_code_matches(dir_name: str, code: str) -> bool:
    """目录名里出现的番号与视频番号一致（OFJE-041-LTX → OFJE-041）。"""
    return any(c.upper() == code.upper() for c in find_codes(dir_name))


def _dir_coded_video_count(d: Path, limit: int = 2) -> int:
    """递归统计目录里**带番号**的视频数；够 limit 就提前返回（避免遍历大目录）。

    只算带番号的：目录里常混着下载站塞的广告片（无番号），它们不该妨碍
    「这个目录就是这部电影的家」的判定。
    """
    n = 0
    stack = [d]
    while stack:
        cur = stack.pop()
        try:
            children = list(cur.iterdir())
        except OSError:
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            if child.is_dir():
                stack.append(child)
            elif _is_video(child.name) and find_codes(child.name):
                n += 1
                if n >= limit:
                    return n
    return n


def _flat_dir(root: Path, video: Path, code: str) -> Path | None:
    """找出该就地改名的目录（也就是「这部电影的家」），取最外层候选。

    从视频所在目录往上：只要不是影片根、目录名里的番号与视频番号一致、且整棵
    子树里只有一个带番号的视频，就继续往上找。命中意味着不必再包一层——直接把
    该目录改名成 [番号][演员][标题]；旧版整理造出的「目录/标准目录/视频」也会因此
    取到外层那个目录，从而被拍平。无番号的广告片不算数，不影响判定。
    """
    try:
        root_res = root.resolve()
    except OSError:
        return None
    cand: Path | None = None
    cur = video.parent
    while True:
        try:
            cur_res = cur.resolve()
        except OSError:
            break
        if cur_res == root_res or root_res not in cur_res.parents:
            break  # 影片根（及更上层）永不改名
        if not _dir_code_matches(cur.name, code):
            break
        if _dir_coded_video_count(cur, 2) != 1:
            break
        cand = cur
        cur = cur.parent
    return cand


def _pull_up(src: Path, dst: Path) -> None:
    """把内层目录的文件提到外层（同名文件以已存在的为准），并试着删掉空壳目录。"""
    try:
        children = sorted(src.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return
    for f in children:
        if f.name.startswith(".") or not f.is_file():
            continue
        target = dst / f.name
        if target.exists():
            continue
        try:
            shutil.move(str(f), str(target))
        except OSError:
            pass
    try:
        src.rmdir()
    except OSError:
        pass  # 还有没搬走的文件就留着，不硬删


def _enumerate_organize(dir_id: int, base_rel: str, recursive: bool) -> list[dict]:
    d = db.film_get_dir(dir_id)
    if not d or not d["valid"]:
        raise FilmToolError(f"影片目录不可访问：dir {dir_id}")
    base = Path(d["path"]) / base_rel if base_rel else Path(d["path"])
    rows: list[dict] = []
    for rel, name in _walk_videos(base, base_rel, recursive, verify_standard=True):
        if _is_video(name) and find_codes(name):
            rows.append({"dir_id": dir_id, "rel_path": rel, "name": name})
    return rows


def _enumerate_thumbs(dir_id: int, base_rel: str, recursive: bool) -> list[dict]:
    d = db.film_get_dir(dir_id)
    if not d or not d["valid"]:
        raise FilmToolError(f"影片目录不可访问：dir {dir_id}")
    base = Path(d["path"]) / base_rel if base_rel else Path(d["path"])
    rows: list[dict] = []
    for rel, name in _walk_videos(base, base_rel, recursive):
        if not _is_video(name) or find_codes(name):
            continue  # 只补视频；带番号的归整理任务
        parent = base if "/" not in rel else Path(d["path"]) / rel.rsplit("/", 1)[0]
        if has_thumb(parent, Path(name).stem):
            continue
        rows.append({"dir_id": dir_id, "rel_path": rel, "name": name})
    return rows


def has_thumb(dir_path: Path, stem: str) -> bool:
    for ext in THUMB_EXTS:
        if (dir_path / f"{stem}{ext}").exists():
            return True
    return False


def _scope_dirs(scope: str, dir_id: int | None, path: str) -> list[tuple[int, str]]:
    """整理出要枚举的 (dir_id, 起始相对路径) 列表。

    scope=current 时 path 是「当前浏览的目录」相对根的路径：从这里开始
    （recursive 决定是否下钻），而不是从该影片根目录开始——否则当前目录
    会退化成全库。空 path = 该目录的根级。
    """
    if scope == "all":
        out = [(int(d["id"]), "") for d in db.film_list_dirs() if d["valid"]]
        if not out:
            raise FilmToolError("没有可用的影片目录")
        return out
    if scope == "current":
        if not dir_id:
            raise FilmToolError("缺少目录参数")
        try:
            rel = films._norm_rel(path)
        except films.FilmError as exc:
            raise FilmToolError(str(exc))
        d = db.film_get_dir(int(dir_id))
        if not d:
            raise FilmToolError("影片目录不存在")
        if not d["valid"]:
            raise FilmToolError(f"影片目录不可访问：{d['path']}")
        return [(int(d["id"]), rel)]
    raise FilmToolError("未知范围")


def _task_label(kind: str, scope: str, dir_id: int | None, path: str,
                recursive: bool) -> str:
    if scope == "all":
        where = "全库"
    else:
        d = db.film_get_dir(int(dir_id or 0))
        base = (d["label"] if d and d["label"] else str(dir_id)) if d else "?"
        try:
            rel = films._norm_rel(path)
        except films.FilmError:
            rel = ""
        where = f"{base}/{rel}" if rel else base
        where += "（含子目录）" if recursive else "（仅本级）"
    return ("整理番号目录 · " if kind == db.TASK_ORGANIZE else "补全缩略图 · ") + where


# ---------------------------------------------------------------- 图片获取

def _abs_image(rel: str) -> Path | None:
    p = (IMAGES_DIR / rel).resolve()
    root = IMAGES_DIR.resolve()
    if root != p and root not in p.parents:
        return None
    return p if p.exists() else None


def _library_source(code: str) -> dict | None:
    """图库命中：返回 {title, actresses, cover, samples}（绝对路径），未命中返回 None。"""
    lib = db.library_get(code)
    if not lib or not lib["images"]:
        return None
    cover: Path | None = None
    samples: list[Path] = []
    for im in lib["images"]:
        if im["status"] != db.IMG_DONE or not im["path"]:
            continue
        p = _abs_image(im["path"])
        if p is None:
            continue
        if im["kind"] == "cover" and cover is None:
            cover = p
        elif im["kind"] == "sample":
            samples.append(p)
    if cover is None and not samples:
        return None
    return {
        "title": lib.get("title") or "",
        "actresses": lib.get("actresses") or [],
        "cover": cover,
        "samples": sorted(samples),
    }


def _fetch_to_library(code: str) -> dict:
    """联网抓取（走 crawler 全局限速）并写回图库，结构与 _library_source 相同。"""
    c = client()
    try:
        hit = c.search(code)
    except NotFound:
        raise FilmToolError("站内未找到该番号")
    except CrawlError as exc:
        raise FilmToolError(f"搜索失败：{exc}")

    detail_url = hit["detail_url"]
    try:
        detail = c.detail(detail_url)
    except (NotFound, CrawlError) as exc:
        raise FilmToolError(f"详情抓取失败：{exc}")

    target = LIBRARY_DIR / _safe_name(code)
    target.mkdir(parents=True, exist_ok=True)

    def grab(url: str, stem: str) -> Path | None:
        for attempt in range(1, IMAGE_ATTEMPTS + 1):
            try:
                content, ext = c.download(url, referer=detail_url)
            except Exception:  # noqa: BLE001
                if attempt < IMAGE_ATTEMPTS:
                    time.sleep(min(1.2 * attempt, 4.0))
                continue
            p = target / f"{stem}{ext}"
            p.write_bytes(content)
            return p
        return None

    cover = grab(detail["cover"], "cover")
    samples: list[Path] = []
    for i, url in enumerate(detail["samples"], start=1):
        p = grab(url, f"sample-{i:02d}")
        if p:
            samples.append(p)

    rows = []
    if cover:
        rows.append({"seq": 0, "kind": "cover", "url": detail["cover"],
                     "path": cover.relative_to(IMAGES_DIR).as_posix(),
                     "status": db.IMG_DONE})
    for i, p in enumerate(samples, start=1):
        rows.append({"seq": i, "kind": "sample", "url": detail["samples"][i - 1],
                     "path": p.relative_to(IMAGES_DIR).as_posix(),
                     "status": db.IMG_DONE})
    if rows:
        db.library_upsert(code, detail_url, detail.get("title") or "", rows,
                          detail.get("actresses") or [])
    if cover is None and not samples:
        raise FilmToolError("封面与详情图均下载失败")
    return {
        "title": detail.get("title") or hit.get("title") or "",
        "actresses": detail.get("actresses") or [],
        "cover": cover,
        "samples": samples,
    }


def _get_meta(code: str) -> dict:
    src = _library_source(code)
    if src:
        return src
    return _fetch_to_library(code)


# ---------------------------------------------------------------- 条目处理

def _parent_rel(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def _copy_if_needed(src: Path, dst: Path, overwrite: bool = False) -> None:
    try:
        if src.resolve() == dst.resolve():
            return
        if not overwrite and dst.exists():
            return
        shutil.copyfile(src, dst)
    except OSError:
        pass  # 图片复制失败不阻塞整理，视频已就位


def _ensure_cover(meta: dict, target_dir: Path, video_stem: str, code: str,
                  overwrite: bool) -> bool:
    """保证封面存在且与视频同名（同名缩略图规则）。返回是否发生了改动。

    overwrite=True（首次整理）：主图直接以视频同名落盘，覆盖跟过来的旧缩略图。
    overwrite=False（校验已整理目录）：已有同名文件就认可，不做覆盖；否则先尝试
    把旧命名（{番号}.jpg）的封面改名，最后才从图库复制。
    """
    if not overwrite and has_thumb(target_dir, video_stem):
        return False
    if not overwrite:
        for ext in THUMB_EXTS:  # 旧命名按番号的封面：改名即可，不重复下载
            legacy = target_dir / f"{code}{ext}"
            if legacy.is_file():
                legacy.rename(target_dir / f"{video_stem}{ext}")
                return True
    if not meta["cover"]:
        return False
    dst = target_dir / f"{video_stem}{meta['cover'].suffix.lower() or '.jpg'}"
    existed = dst.exists()
    _copy_if_needed(meta["cover"], dst, overwrite=overwrite)
    return not existed or overwrite


def _ensure_samples(meta: dict, target_dir: Path, code: str) -> bool:
    """保证详图齐全（番号-sN 从 s2 起）。返回是否补了缺失的文件。"""
    changed = False
    for i, s in enumerate(meta["samples"], start=1):
        dst = target_dir / f"{code}-s{i + 1}{s.suffix.lower() or '.jpg'}"
        if dst.exists():
            continue
        _copy_if_needed(s, dst)
        if dst.exists():
            changed = True
    return changed


def _rel_dir(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def _organize_flat(item: dict, root: Path, video: Path, code: str,
                   cand: Path) -> tuple[str, str | None]:
    """就地把 cand 目录整理成 [番号][演员][标题]，并补齐封面/详图。

    视频可能还在 cand 的内层子目录里（旧版整理留下的套娃），改名后把文件提上来、
    删掉空壳目录。全部合格时返回 skipped（已验证，未改动）；有改动返回 done。
    """
    meta = _get_meta(code)
    expected = build_dir_name(code, meta["actresses"], meta["title"])
    old = cand
    target = cand
    changed = False
    conflict = False

    if cand.name != expected:
        new_dir = cand.parent / expected
        if new_dir.exists():
            conflict = True  # 目标名已被占用：保留现有层级，只在视频所在目录里补齐
        else:
            # 目录改名：子树 DB 行全部失效，改完靠后台扫描重建
            try:
                db.film_delete_subtree(int(item["dir_id"]), _rel_dir(root, cand))
                cand.rename(new_dir)
                target = new_dir
                changed = True
            except OSError:
                target = old  # 改不动目录就只校验内部文件，不把整条判失败

    # 改名后视频的新位置（还留在内层时先算好路径）
    try:
        inner = video.parent.relative_to(old)
    except ValueError:  # 理论上不会发生：video 一定在 cand 子树里
        inner = Path(".")
    video = target / video.name if inner.as_posix() == "." else target / inner / video.name
    if conflict:
        target = video.parent  # 不拍平，避免把视频提出标准目录
    elif video.parent != target:
        _pull_up(video.parent, target)  # 拍平多出来的那一层
        video = target / video.name

    if _ensure_cover(meta, target, video.stem, code, overwrite=False):
        changed = True
    if _ensure_samples(meta, target, code):
        changed = True

    target_rel = _rel_dir(root, target)
    schedule_scan(int(item["dir_id"]), _rel_dir(root, target.parent), force=True)
    schedule_scan(int(item["dir_id"]), target_rel, force=True)
    return (db.FT_DONE if changed else db.FT_SKIPPED), target_rel


def _organize_one(item: dict) -> tuple[str, str | None]:
    """处理单个整理条目，返回 (终态, 目标目录相对路径)。失败抛 FilmToolError。"""
    dir_row = db.film_get_dir(int(item["dir_id"]))
    if not dir_row or not dir_row["valid"]:
        raise FilmToolError("影片目录不可访问")
    root = Path(dir_row["path"])
    try:
        video = resolve_under_root(root, item["rel_path"])
    except FilmError as exc:
        raise FilmToolError(str(exc))
    if not video.is_file():
        raise FilmToolError("文件不存在（可能已被移动或删除）")

    parent_abs = video.parent
    code_list = find_codes(video.name)
    if not code_list:
        return db.FT_SKIPPED, None  # 文件名变化后番号消失
    code = code_list[0]

    # 目录本身就是这部电影的家：就地改名（顺带拍平旧版套娃），不再往下包一层
    cand = _flat_dir(root, video, code)
    if cand is not None:
        return _organize_flat(item, root, video, code, cand)

    if is_standard_dir(parent_abs.name):
        return db.FT_SKIPPED, None  # 已整理目录里多视频/番号对不上，不归这里管

    meta = _get_meta(code)
    dirname = build_dir_name(code, meta["actresses"], meta["title"])
    target_dir = parent_abs / dirname
    target_dir.mkdir(parents=True, exist_ok=True)
    target_rel = f"{_parent_rel(item['rel_path'])}/{dirname}".lstrip("/")

    dst = target_dir / video.name
    if not dst.exists():
        shutil.move(str(video), str(dst))
    for ext in THUMB_EXTS:  # 同名缩略图跟随移动
        thumb = parent_abs / f"{video.stem}{ext}"
        if thumb.exists():
            shutil.move(str(thumb), str(target_dir / thumb.name))

    # 主图重命名为与视频同名（影片管理的缩略图规则 = 同目录同名文件），
    # 这样文件夹封面立即生效；与跟随移动的同名缩略图重名时以封面为准
    _ensure_cover(meta, target_dir, video.stem, code, overwrite=True)
    _ensure_samples(meta, target_dir, code)

    # 强制重扫旧级（原位置已空）与新目录级
    schedule_scan(int(item["dir_id"]), _parent_rel(item["rel_path"]), force=True)
    schedule_scan(int(item["dir_id"]), target_rel, force=True)
    return db.FT_DONE, target_rel


def _ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FilmToolError("容器内没有 ffmpeg（镜像需包含 ffmpeg）")
    return path


def _thumb_one(item: dict) -> tuple[str, str | None]:
    """为单个视频抽帧生成缩略图，返回 (终态, 缩略图相对路径)。失败抛 FilmToolError。"""
    dir_row = db.film_get_dir(int(item["dir_id"]))
    if not dir_row or not dir_row["valid"]:
        raise FilmToolError("影片目录不可访问")
    root = Path(dir_row["path"])
    try:
        video = resolve_under_root(root, item["rel_path"])
    except FilmError as exc:
        raise FilmToolError(str(exc))
    if not video.is_file():
        raise FilmToolError("文件不存在（可能已被移动或删除）")

    stem = video.stem
    if has_thumb(video.parent, stem):
        return db.FT_SKIPPED, None

    ff = _ffmpeg_bin()
    out = video.parent / f"{stem}.jpg"

    # 取视频时长中点抽帧；ffprobe 拿不到时长就退回首帧
    seek: list[str] = []
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video)],
            capture_output=True, timeout=30, check=True,
        )
        duration = float(probe.stdout.decode().strip() or 0)
        if duration > 0:
            seek = ["-ss", f"{max(0.0, duration / 2):.2f}"]
    except Exception:  # noqa: BLE001
        pass

    cmd = [ff, "-hide_banner", "-loglevel", "error", *seek,
           "-i", str(video), "-frames:v", "1", "-q:v", "2", "-y", str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        out.unlink(missing_ok=True)
        raise FilmToolError(f"ffmpeg 超时（>{FFMPEG_TIMEOUT}s）") from exc
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        tail = (proc.stderr or b"").decode(errors="replace").strip()[-160:]
        raise FilmToolError(f"ffmpeg 失败：{tail or '返回码 ' + str(proc.returncode)}")

    rel = item["rel_path"]
    thumb_rel = f"{_parent_rel(rel)}/{out.name}" if "/" in rel else out.name
    schedule_scan(int(item["dir_id"]), _parent_rel(rel), force=True)
    return db.FT_DONE, thumb_rel


# ---------------------------------------------------------------- 执行器

def _finish(item: dict, result: tuple[str, str | None]) -> None:
    status, target = result
    db.film_finish_item(int(item["id"]), status,
                        None if status != db.FT_FAILED else "处理失败", target)


class ToolRunner:
    """影片工具任务执行器：同类任务互斥，条目串行处理。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, int] = {}  # kind -> task_id
        self._cancelled: set[int] = set()

    def start(self, kind: str, scope: str, dir_id: int | None, path: str,
              recursive: bool) -> int:
        if kind not in (db.TASK_ORGANIZE, db.TASK_THUMB):
            raise FilmToolError("未知任务类型")
        with self._lock:
            if kind in self._running:
                raise FilmToolError("已有同类任务进行中，请等它结束或取消后再试")
        # 枚举与建任务放在锁外（磁盘 IO），登记时二次检查防竞态
        scope_dirs = _scope_dirs(scope, dir_id, path)
        label = _task_label(kind, scope, dir_id, path, recursive)
        task_id = db.create_film_task(kind, label)
        with self._lock:
            if kind in self._running:
                db.delete_task(task_id)
                raise FilmToolError("已有同类任务进行中，请等它结束或取消后再试")
            self._running[kind] = task_id
        self._cancelled.discard(task_id)
        threading.Thread(
            target=self._guarded, args=(task_id, kind, scope, dir_id, path, recursive),
            name=f"filmtool-{kind}{task_id}", daemon=True,
        ).start()
        return task_id

    def cancel(self, task_id: int) -> bool:
        """标记取消；返回是否命中了正在跑的任务。"""
        with self._lock:
            hit = task_id in self._running.values()
        if not hit:
            return False
        self._cancelled.add(task_id)
        return True

    def is_cancelled(self, task_id: int) -> bool:
        return task_id in self._cancelled

    def is_running(self, task_id: int) -> bool:
        with self._lock:
            return task_id in self._running.values()

    # ------------------------------------------------------------ 内部

    def _process_all(self, task_id: int, kind: str, items: list[dict]) -> None:
        fn = _organize_one if kind == db.TASK_ORGANIZE else _thumb_one
        for item in items:
            if self.is_cancelled(task_id):
                db.touch_task(task_id, status=db.TASK_FAILED, error="已取消")
                return
            try:
                result = fn(item)
                _finish(item, result)
            except FilmToolError as exc:
                db.film_finish_item(int(item["id"]), db.FT_FAILED, str(exc))
            except Exception as exc:  # noqa: BLE001
                db.film_finish_item(int(item["id"]), db.FT_FAILED, f"异常: {exc}")
        if self.is_cancelled(task_id):
            # 取消可能落在最后一个条目上：处理完也要如实标成已取消
            db.touch_task(task_id, status=db.TASK_FAILED, error="已取消")
            return
        db.touch_task(task_id, status=db.TASK_COMPLETED, error=None)

    def _run(self, task_id: int, kind: str, scope: str,
             dir_id: int | None, path: str, recursive: bool) -> None:
        if kind == db.TASK_THUMB:
            try:
                _ffmpeg_bin()  # 缺 ffmpeg 整个任务直接失败，不逐条目报错
            except FilmToolError as exc:
                db.touch_task(task_id, status=db.TASK_FAILED, error=str(exc))
                return
        fn = _enumerate_organize if kind == db.TASK_ORGANIZE else _enumerate_thumbs
        try:
            for did, base in _scope_dirs(scope, dir_id, path):
                rows = fn(did, base, recursive)
                if rows:
                    db.film_add_items(task_id, rows)
        except FilmError as exc:
            db.touch_task(task_id, status=db.TASK_FAILED, error=str(exc))
            return
        except FilmToolError as exc:
            db.touch_task(task_id, status=db.TASK_FAILED, error=str(exc))
            return
        self._process_all(task_id, kind, db.film_list_items(task_id))

    def _guarded(self, task_id: int, kind: str, scope: str,
                 dir_id: int | None, path: str, recursive: bool) -> None:
        try:
            self._run(task_id, kind, scope, dir_id, path, recursive)
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            db.touch_task(task_id, status=db.TASK_FAILED, error=f"任务异常: {exc}")
        finally:
            with self._lock:
                for k, tid in list(self._running.items()):
                    if tid == task_id:
                        del self._running[k]
            self._cancelled.discard(task_id)

    def retry_failed(self, task_id: int) -> int:
        """为指定任务生成「仅失败条目」的新任务并启动，返回新任务 id。"""
        old = db.get_task(task_id)
        if not old or old.get("kind") not in (db.TASK_ORGANIZE, db.TASK_THUMB):
            raise FilmToolError("该任务不支持失败重试")
        if self.is_running(task_id):
            raise FilmToolError("任务还在执行中")
        failed = db.film_failed_items(task_id)
        if not failed:
            raise FilmToolError("没有失败条目可重试")
        kind = str(old["kind"])
        new_id = db.create_film_task(kind, f"重试失败条目 · 来自 #{task_id}")
        db.film_add_items(
            new_id,
            [{"dir_id": r["dir_id"], "rel_path": r["rel_path"], "name": r["name"]}
             for r in failed],
        )
        with self._lock:
            if kind in self._running:
                db.delete_task(new_id)
                raise FilmToolError("已有同类任务进行中，请等它结束或取消后再试")
            self._running[kind] = new_id
        threading.Thread(
            target=self._guarded_retry, args=(new_id, kind),
            name=f"filmtool-retry{new_id}", daemon=True,
        ).start()
        return new_id

    def _guarded_retry(self, task_id: int, kind: str) -> None:
        try:
            self._process_all(task_id, kind, db.film_list_items(task_id))
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            db.touch_task(task_id, status=db.TASK_FAILED, error=f"任务异常: {exc}")
        finally:
            with self._lock:
                for k, tid in list(self._running.items()):
                    if tid == task_id:
                        del self._running[k]
            self._cancelled.discard(task_id)


runner = ToolRunner()
