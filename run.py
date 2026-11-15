"""启动入口：python run.py"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    host = os.environ.get("APP_HOST", "127.0.0.1")
    port = int(os.environ.get("APP_PORT", "8000"))
    print(f" Aphrodite  http://{host}:{port}")
    uvicorn.run("backend.app:app", host=host, port=port, log_level="info")
