import hashlib

from backend import worker
from backend.worker import _DuplicateGuard, _load_placeholders, _remember_placeholder


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def test_duplicate_guard_within_item():
    g = _DuplicateGuard()
    ok1, d1 = g.check(b"photo-1")
    ok2, _ = g.check(b"photo-1")
    ok3, d3 = g.check(b"photo-2")
    assert ok1 is True
    assert ok2 is False
    assert ok3 is True
    assert d1 == _md5(b"photo-1")


def test_placeholder_blacklist_learned(tmp_path, monkeypatch):
    # 占位图黑名单：发现重复内容后记录哈希，之后的任务/番号直接跳过
    monkeypatch.setattr(worker, "_PLACEHOLDER_FILE", tmp_path / "ph.txt")
    monkeypatch.setattr(worker, "_placeholder_hashes", None)

    placeholder = b"NOW PRINTING placeholder jpeg bytes"
    g = _DuplicateGuard()
    ok, digest = g.check(placeholder)
    assert ok is True
    # 同一内容再次出现 → 判定为占位图，并记入黑名单
    _remember_placeholder(digest)

    # 模拟重启 / 新任务：重新加载黑名单
    monkeypatch.setattr(worker, "_placeholder_hashes", None)
    g2 = _DuplicateGuard()
    assert g2.check(placeholder)[0] is False
    assert g2.check(b"real-photo-bytes")[0] is True

    assert (tmp_path / "ph.txt").read_text().strip() == digest


def test_load_placeholders_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "_PLACEHOLDER_FILE", tmp_path / "nope.txt")
    monkeypatch.setattr(worker, "_placeholder_hashes", None)
    assert _load_placeholders() == set()
