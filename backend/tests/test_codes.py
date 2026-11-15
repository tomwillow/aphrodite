import pytest

from backend.codes import find_links, parse_codes, parse_text

ED2K = "ed2k://|file|@IPZ-918_restored.mp4|10174562190|12002AEC19A55208154D2683A8585483|/"


def test_basic_example():
    assert parse_codes("IPX-528 田中レモン AVOP-201 相沢みなみ") == ["IPX-528", "AVOP-201"]


def test_dedupe_case_insensitive():
    assert parse_codes("ipx-528 IPX-528 IpX-528") == ["IPX-528"]


def test_fullwidth_and_unicode_dashes():
    assert parse_codes("IPX－528 SSIS–100 MIDE—001") == ["IPX－528".replace("－", "-"), "SSIS-100", "MIDE-001"]


def test_ignores_dates_and_numbers():
    # 日期、纯数字、磁力哈希都不应被识别为番号
    assert parse_codes("2024-11-03 12345678 F91AF5AFD93EBE9D9FDD390AB00E3C1334C8AA69") == []


def test_order_preserved():
    assert parse_codes("ZZZ-01 AAA-999 MID-02 AAA-999") == ["ZZZ-01", "AAA-999", "MID-02"]


def test_empty():
    assert parse_codes("") == []
    assert parse_codes(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------- 链接识别

def test_find_links_ed2k_with_chinese_name():
    line = "ed2k://|file|MEYD-263-破解-中文.mkv|3408696450|A676E092E7C14407E62501499CDFF851|/"
    assert find_links(line) == [line]


def test_find_links_magnet_and_http():
    assert find_links("magnet:?xt=urn:btih:ABC") == ["magnet:?xt=urn:btih:ABC"]
    # 中文标点不应被吃进链接
    assert find_links("见 https://example.com/a.mp4，谢谢") == ["https://example.com/a.mp4"]


def test_find_links_none():
    assert find_links("IPX-528 田中レモン") == []


# ---------------------------------------------------------------- 整段解析

def test_parse_text_entries_with_links():
    text = f"{ED2K}\nIPX-528\n"
    r = parse_text(text)
    assert r["count"] == 2
    assert r["with_link"] == 1
    assert r["codes"] == ["IPZ-918", "IPX-528"]
    assert r["entries"][0]["link"] == ED2K
    assert r["entries"][1]["link"] is None


def test_parse_text_categories():
    text = "\n".join(
        [
            ED2K,  # 正常
            "IPX-528 田中レモン",  # 有番号没链接
            "magnet:?xt=urn:btih:F91AF5AFD93EBE9D9FDD390AB00E3C1334C8AA69",  # 只有链接
            "这一行什么都没有",  # 未识别
        ]
    )
    r = parse_text(text)
    assert r["unknown"] == [{"line": "这一行什么都没有"}]
    assert r["only_link"][0]["link"].startswith("magnet:?")
    assert r["no_link"] == [{"line": "IPX-528 田中レモン", "code": "IPX-528"}]
    assert r["count"] == 2 and r["with_link"] == 1


def test_parse_text_duplicate_code_fills_missing_link():
    # 同一个番号第二次出现且带链接 → 补到第一次的条目上，且不再报「缺链接」
    r = parse_text("IPX-528\n" + ED2K.replace("IPZ-918", "IPX-528"))
    assert r["count"] == 1
    assert r["with_link"] == 1
    assert r["no_link"] == []
    assert r["entries"][0]["link"].endswith("|/")


def test_parse_text_blank_lines_ignored():
    r = parse_text("\n\n  \nIPX-528\n\n")
    assert r["count"] == 1
    assert r["unknown"] == []


def test_parse_text_empty():
    for text in ("", None):
        r = parse_text(text)  # type: ignore[arg-type]
        assert r["count"] == 0
        assert r["entries"] == []
