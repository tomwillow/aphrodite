import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 必须在导入 backend 之前设置：config 在 import 时读取该环境变量
os.environ["APHRODITE_DATA"] = tempfile.mkdtemp(prefix="aphrodite-test-")


@pytest.fixture(scope="session", autouse=True)
def _database():
    from backend import db

    db.init()
    yield
