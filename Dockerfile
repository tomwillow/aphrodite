# syntax=docker/dockerfile:1
# Aphrodite 单镜像：前端 dist 构建产物 + FastAPI 后端，单端口 8000 对外。
# 注意：镜像本身不含 data/（数据库与图库），运行期由卷挂到 /data。

# ---------- 1. 前端构建 ----------
FROM node:22-alpine AS web
WORKDIR /app/web
# 先只拷 lock 文件，命中缓存层
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- 2. 运行时 ----------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    APHRODITE_DATA=/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py ./
COPY backend ./backend
COPY --from=web /app/web/dist ./web/dist

RUN mkdir -p /data/images/library
VOLUME ["/data"]
EXPOSE 8000

# 直接 docker run 时也能自检；compose 里的 healthcheck 会覆盖它
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)" || exit 1

CMD ["python", "run.py"]
