"""全局配置与路径。"""
from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("APHRODITE_DATA", BASE_DIR / "data"))
# /media 静态根目录
IMAGES_DIR = DATA_DIR / "images"
# 统一图库：下载结果都放在 library/<番号>/ 下，跨任务复用
LIBRARY_DIR = IMAGES_DIR / "library"
DB_PATH = DATA_DIR / "aphrodite.db"
WEB_DIST = BASE_DIR / "web" / "dist"

JAVBUS_BASE = os.environ.get("JAVBUS_BASE", "https://www.javbus.com").rstrip("/")

# ---------------------------------------------------------------- 影片管理
def _split_list(value: str | None) -> list[str]:
    """影片目录列表：逗号 / 分号分隔（不用冒号，Windows 盘符路径里含冒号）。"""
    if not value:
        return []
    return [p.strip() for p in re.split(r"[;,]", value) if p.strip()]


# 影片根目录（容器内路径），如 /data/films/a,/data/films/b
FILM_DIRS: list[Path] = [Path(p) for p in _split_list(os.environ.get("APHRODITE_FILM_DIRS"))]

# 容器路径 → 本机路径 映射，如 /data/films=Z:\Films,/data/films2=D:\Films2
# 「打开」按钮用它把容器内路径换算成浏览器所在机器能访问的路径
FILM_PATH_MAP: list[tuple[str, str]] = []
for pair in _split_list(os.environ.get("APHRODITE_FILM_PATH_MAP")):
    if "=" in pair:
        server, local = pair.split("=", 1)
        server, local = server.strip().rstrip("/"), local.strip().rstrip("\\/")
        if server and local:
            FILM_PATH_MAP.append((server, local))
# 按 prefix 长度降序，换算时命中最长前缀
FILM_PATH_MAP.sort(key=lambda p: len(p[0]), reverse=True)

# 页面请求最小间隔（秒），避免触发站点风控
PAGE_INTERVAL = float(os.environ.get("APHRODITE_PAGE_INTERVAL", "1.2"))
IMAGE_INTERVAL = float(os.environ.get("APHRODITE_IMAGE_INTERVAL", "0.15"))

# 同时执行的任务数
TASK_WORKERS = int(os.environ.get("APHRODITE_TASK_WORKERS", "2"))
# 单个番号的图片并发下载数（跨境网络单张要约 3-4s，串行太慢）
IMAGE_WORKERS = int(os.environ.get("APHRODITE_IMAGE_WORKERS", "5"))
# 单张图片最多尝试次数：失败自动重试，用尽后等待手动重试
MAX_IMAGE_ATTEMPTS = int(os.environ.get("APHRODITE_MAX_ATTEMPTS", "5"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)


def safe_dir_name(name: str) -> str:
    """把番号转成安全的目录名。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "unknown"
