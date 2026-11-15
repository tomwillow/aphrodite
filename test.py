"""一键跑全部测试：python test.py（后端 pytest + 前端 vitest）"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def venv_python() -> str:
    candidate = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else sys.executable


def run(title: str, cmd: str, cwd: Path) -> bool:
    print(f"\n{'=' * 8} {title} {'=' * 8}")
    proc = subprocess.run(cmd, cwd=cwd, shell=True)
    return proc.returncode == 0


def main() -> int:
    ok_backend = run(
        "后端 pytest",
        f'"{venv_python()}" -m pytest backend/tests -q',
        ROOT,
    )
    ok_frontend = run("前端 vitest", "npm test", ROOT / "web")
    print(f"\n结果: 后端 {'通过' if ok_backend else '失败'} / 前端 {'通过' if ok_frontend else '失败'}")
    return 0 if ok_backend and ok_frontend else 1


if __name__ == "__main__":
    sys.exit(main())
