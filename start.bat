@echo off
cd /d %~dp0

if not exist .venv\Scripts\python.exe (
    echo [Aphrodite] first run: creating venv and installing dependencies...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -q -r requirements.txt
)

.venv\Scripts\python.exe run.py
