"""javbus 爬虫：搜索番号 → 详情页 → 主图 / 详情图。

单进程内共享一个 Session（带 cookie），并对页面请求做全局限速，
避免短时间高频请求被站点风控拦截。
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import IMAGE_INTERVAL, JAVBUS_BASE, PAGE_INTERVAL, USER_AGENT


class CrawlError(Exception):
    """站点异常（被封禁 / 页面结构不符）。"""


class NotFound(Exception):
    """站内搜不到该番号。"""


class JavBus:
    def __init__(self) -> None:
        self.base = JAVBUS_BASE
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": self.base + "/",
            }
        )
        self._page_lock = threading.Lock()
        self._page_last = 0.0
        self._img_lock = threading.Lock()
        self._img_last = 0.0
        self._warmed = False

    # ------------------------------------------------------------ 限速
    def _throttle(self, lock: threading.Lock, last_attr: str, interval: float) -> None:
        with lock:
            wait = interval - (time.time() - getattr(self, last_attr))
            if wait > 0:
                time.sleep(wait)
            setattr(self, last_attr, time.time())

    def _throttle_page(self) -> None:
        self._throttle(self._page_lock, "_page_last", PAGE_INTERVAL)

    def _throttle_image(self) -> None:
        self._throttle(self._img_lock, "_img_last", IMAGE_INTERVAL)

    # ------------------------------------------------------------ 页面
    def _get_html(self, url: str, retries: int = 2) -> str:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            self._throttle_page()
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException as e:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 404:
                raise NotFound(url)
            if r.status_code >= 500:
                last_err = CrawlError(f"HTTP {r.status_code}")
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise CrawlError(f"HTTP {r.status_code}")
            r.encoding = "utf-8"
            html = r.text
            if "Age Verification" in html[:4000]:
                # 站点年龄校验：写入 cookie 后重试一次
                self.session.cookies.set("existmag", "mag", domain=re.sub(r"^https?://", "", self.base))
                last_err = CrawlError("被年龄校验拦截")
                time.sleep(1.0)
                continue
            return html
        raise CrawlError(f"请求失败: {last_err}") from last_err

    def warmup(self) -> None:
        if self._warmed:
            return
        self._warmed = True
        try:
            self._get_html(self.base + "/")
        except Exception:
            pass

    # ------------------------------------------------------------ 搜索
    def search(self, code: str) -> dict[str, Any]:
        """返回 {detail_url, title, matched}。"""
        self.warmup()
        url = f"{self.base}/search/{code}"
        try:
            html = self._get_html(url)
        except NotFound as e:
            raise NotFound(code) from e

        soup = BeautifulSoup(html, "lxml")
        candidates: list[dict[str, str]] = []
        for a in soup.select("a.movie-box[href]"):
            href = a.get("href", "")
            if "/forum/" in href:
                continue
            slug = href.rstrip("/").rsplit("/", 1)[-1]
            date_el = a.select_one("date")
            film_code = date_el.get_text(strip=True) if date_el else slug
            img = a.select_one("img")
            candidates.append(
                {
                    "detail_url": urljoin(self.base + "/", href),
                    "code": film_code.upper(),
                    "slug": slug.upper(),
                    "title": (img.get("title") if img else "") or "",
                }
            )

        if not candidates:
            raise NotFound(code)

        for c in candidates:
            if c["slug"] == code or c["code"] == code:
                return c
        if len(candidates) == 1:
            return candidates[0]
        raise NotFound(code)

    # ------------------------------------------------------------ 详情
    def detail(self, detail_url: str) -> dict[str, Any]:
        html = self._get_html(detail_url)
        soup = BeautifulSoup(html, "lxml")

        cover = ""
        a_big = soup.select_one("a.bigImage[href]")
        if a_big:
            cover = urljoin(self.base + "/", a_big["href"])
        else:
            img_big = soup.select_one("img#bigImage")
            if img_big and img_big.get("src"):
                cover = urljoin(self.base + "/", img_big["src"])

        if not cover:
            raise CrawlError("详情页未找到主图")

        samples: list[str] = []
        seen: set[str] = set()
        for a in soup.select("a.sample-box[href]"):
            href = a.get("href", "").strip()
            if not href or href.startswith("javascript"):
                continue
            full = urljoin(self.base + "/", href)
            if full in seen:
                continue
            seen.add(full)
            samples.append(full)

        h3 = soup.select_one("h3")
        title = h3.get_text(" ", strip=True) if h3 else ""

        actresses: list[str] = []
        for a in soup.select("a[href*='/actress']"):
            name = a.get_text(strip=True)
            if name and name not in actresses:
                actresses.append(name)

        return {"cover": cover, "samples": samples, "title": title, "actresses": actresses}

    # ------------------------------------------------------------ 图片
    def download(self, url: str, referer: str | None = None) -> tuple[bytes, str]:
        """单次下载尝试；重试策略由 worker 统一控制（默认 5 次）。"""
        self._throttle_image()
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": referer or (self.base + "/"),
            "Accept": "image/*,*/*;q=0.8",
        }
        try:
            # 图片是静态资源，用独立请求避免多线程共享 Session 的 cookie 竞争
            r = requests.get(url, headers=headers, timeout=40)
        except requests.RequestException as e:
            raise CrawlError(f"网络错误: {e.__class__.__name__}") from e
        if r.status_code != 200:
            raise CrawlError(f"HTTP {r.status_code}")
        ctype = (r.headers.get("content-type") or "").lower()
        if "image" not in ctype:
            raise CrawlError(f"非图片响应: {ctype or 'unknown'}")
        if len(r.content) < 1024:
            raise CrawlError("图片内容过小")
        ext = ".jpg"
        if "png" in ctype:
            ext = ".png"
        elif "webp" in ctype:
            ext = ".webp"
        elif "gif" in ctype:
            ext = ".gif"
        return r.content, ext


_client: JavBus | None = None
_client_lock = threading.Lock()


def client() -> JavBus:
    global _client
    with _client_lock:
        if _client is None:
            _client = JavBus()
        return _client
