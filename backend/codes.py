"""番号 + 下载链接解析。

粘贴进来的文本通常一行一个条目，例如：

    ed2k://|file|@IPZ-918_restored.mp4|10174562190|12002AEC...|/

因此按行解析：每行提取番号（XXX-XXX）和下载链接（ed2k / magnet / http / ftp / thunder），
并把「无法识别」「有番号但没链接」「只有链接没番号」的行分类返回，供提交页提示用户核对。

前后端使用同一套规则（前端见 web/src/lib/codes.ts），后端解析结果为准。
"""
from __future__ import annotations

import re
from typing import Any

# 允许的连字符：半角、全角、各种 Unicode 破折号
DASHES = "-‐‑‒–—－"
CODE_RE = re.compile(rf"[A-Za-z]{{2,6}}[{re.escape(DASHES)}]\d{{2,6}}")

# 下载链接：ed2k 优先（内部可能含 | 与空格，用非贪婪匹配到结尾的 |/）
LINK_RE = re.compile(
    r"ed2k://\|[^\r\n]*?\|/"
    r"|magnet:\?[^\s\"'<>]+"
    r"|thunder://[^\s\"'<>]+"
    r"|(?:https?|ftp)://[^\s\"'<>，。；、）】」》]+",
    re.IGNORECASE,
)

# 中文/英文标点常紧跟在 URL 后面，去掉尾部的这些字符
_TRAILING = ".,;:!?)]}>\"'，。；：！？）】》」"


def normalize_code(raw: str) -> str:
    return re.sub(rf"[{re.escape(DASHES)}]", "-", raw).upper()


def find_codes(text: str) -> list[str]:
    """一行内的番号（保留重复，由上层去重）。"""
    return [normalize_code(m) for m in CODE_RE.findall(text or "")]


def find_links(text: str) -> list[str]:
    """一行内的下载链接（去重、去掉尾部标点）。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in LINK_RE.findall(text or ""):
        link = m.rstrip(_TRAILING)
        if link and link not in seen:
            seen.add(link)
            out.append(link)
    return out


def split_lines(text: str) -> list[str]:
    """按行切分，去掉空行与首尾空白。"""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def parse_codes(text: str) -> list[str]:
    """按出现顺序去重解析番号，统一转为大写 + 半角连字符。"""
    seen: set[str] = set()
    result: list[str] = []
    for raw in CODE_RE.findall(text or ""):
        code = normalize_code(raw)
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def parse_text(text: str) -> dict[str, Any]:
    """整段文本 → 结构化识别结果。

    返回:
      entries    [{code, line, link}]  去重后的番号，link 可能为 None
      codes      [code]                兼容旧接口
      count      番号总数
      with_link  带下载链接的番号数
      no_link    有番号但没识别到链接的行
      only_link  只有链接、没有番号的行
      unknown    既没有番号也没有链接的行
    """
    lines: list[dict[str, Any]] = []
    for line in split_lines(text):
        lines.append({"line": line, "codes": find_codes(line), "links": find_links(line)})

    entries: list[dict[str, Any]] = []
    by_code: dict[str, dict[str, Any]] = {}
    for row in lines:
        if not row["codes"]:
            continue
        link = row["links"][0] if row["links"] else None
        for code in row["codes"]:
            entry = by_code.get(code)
            if entry is None:
                entry = {"code": code, "link": link, "line": row["line"]}
                by_code[code] = entry
                entries.append(entry)
            elif entry["link"] is None and link:
                # 同一番号在后面某行带了链接 → 补上
                entry["link"] = link

    no_link: list[dict[str, Any]] = []
    only_link: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for row in lines:
        if row["codes"]:
            if not row["links"] and any(by_code[c]["link"] is None for c in row["codes"]):
                no_link.append({"line": row["line"], "code": row["codes"][0]})
        elif row["links"]:
            only_link.append({"line": row["line"], "link": row["links"][0]})
        else:
            unknown.append({"line": row["line"]})

    return {
        "entries": entries,
        "codes": [e["code"] for e in entries],
        "count": len(entries),
        "with_link": sum(1 for e in entries if e["link"]),
        "no_link": no_link,
        "only_link": only_link,
        "unknown": unknown,
    }
