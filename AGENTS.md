# AGENTS.md

给在本仓库里干活的 AI Agent（和人类）看的工作手册。**改代码前先读完「硬性约定」一节**。

## 这是什么

Aphrodite：把一段粘贴文本里的番号（`XXX-123` 形式）批量抓成图集。

- 输入：多行文本，每行一个番号 + 可选下载链接（ed2k / magnet / http / ftp / thunder）
- 处理：逐番号搜索 → 抓详情页 → 下载封面 + 详情图 → 落到**统一图库**（按番号索引，跨任务复用）
- 输出：Vue3 单页应用，SSE 实时进度；图片经 `/media` 静态服务
- 技术栈：FastAPI + SQLite(WAL) + requests/bs4 ｜ Vite + Vue3 + TS（无 UI 框架）
- 部署形态：前端构建产物由后端托管，**单端口 8000**

## 常用命令

```bash
# 首次/日常启动（Windows，start.bat 会自动建 venv 装依赖）
./start.bat
# 或手动
.venv/Scripts/python.exe run.py            # APP_HOST/APP_PORT 可覆盖，默认 127.0.0.1:8000

# 前端开发（5173，proxy /api /media 到 8000）
cd web && npm install && npm run dev
# 前端改动要生效到单端口模式，必须重新构建
cd web && npm run build

# 全部测试（后端 pytest + 前端 vitest），提交前必跑
python test.py
# 分开跑
.venv/Scripts/python.exe -m pytest backend/tests -q
cd web && npm test
```

测试规模：后端 ~40 个用例（`backend/tests/`），前端 ~30 个（`web/src/**/*.test.ts`）。**当前基线必须全绿。**

## Docker 部署（Linux）

只有 Docker，不需要装 python / node：前端 dist 与后端依赖都在镜像构建里完成。

```bash
scripts/deploy.sh              # 一键：构建镜像 → 交互式生成 compose → up -d → 等健康检查
scripts/build_image.sh         # 只构建镜像（-t 改标签，--no-cache 重来）
scripts/config_compose.sh      # 只交互式生成/修改 docker-compose.yml（-y 全用现有值）

# 三个脚本跑完后，日常运维就回到原生 compose
docker compose up -d / logs -f / restart / down
```

- 镜像名默认 `aphrodite:latest`，可用 `-t` 或环境变量 `APHRODITE_IMAGE` 改；`deploy.sh` 会把同一个名字传给构建和 compose。
- `docker-compose.yml` **只引用镜像、不写 `build:`**——这是「前两个脚本跑完，`docker compose up -d` 就能起」的前提，别往里加 `build:`。
- `config_compose.sh` 的交互默认值**取自现有 compose 文件**（`scripts/lib/common.sh` 里的 `yml_value/yml_port_spec/yml_data_dir`），所以手工改 compose 不会被覆盖，只会被当作下次的默认值。改生成模板要同时改这两处。
- 容器内端口固定 8000（`run.py` / healthcheck / compose 都按它写），只有宿主端口与监听地址可配。
- 数据全部在卷里：`<宿主机目录>:/data`（`aphrodite.db` + `images/library/`）。**镜像不含任何数据**，换机器只搬这个目录。
- `data/`、`web/node_modules`、`web/dist`、`.venv` 由 `.dockerignore` 排除，不进构建上下文。
- Windows 检出后拷到 Linux 跑，注意行尾：`.gitattributes` 已强制 `*.sh` / `Dockerfile` 用 LF。

## 目录

```
run.py                    启动入口（uvicorn backend.app:app）
backend/
  app.py                  FastAPI 路由：REST + SSE + /media + SPA 兜底
  codes.py                番号 / 下载链接解析（后端为准）
  crawler.py              JavBus 客户端：搜索、详情、图片下载、全局限速
  worker.py               后台执行器 Runner：任务级 + 条目级并发、取消、重试、占位图去重
  db.py                   SQLite 数据层：schema、rev、读写、图库、迁移
  config.py               路径与全部可调参数（读环境变量）
  tests/                  pytest；conftest 把 APHRODITE_DATA 指到临时目录
web/src/
  api.ts                  接口类型 + fetch 封装（类型与后端字段一一对应）
  lib/codes.ts            前端解析规则，必须与 backend/codes.py 保持一致
  lib/status.ts           状态文案 / 时间格式化
  views/                  SubmitView / TaskListView / TaskDetailView
  components/Lightbox.vue 大图查看
  styles.css              全部样式与 CSS 变量（不用 UI 框架）
data/                     运行期数据，已 gitignore
  aphrodite.db            SQLite
  images/library/<番号>/   统一图库（cover.<ext> / sample-NN.<ext>）
  placeholder_hashes.txt  占位图 md5 黑名单（自学习）
Dockerfile                多阶段构建：node 打前端 dist → python:3.12-slim 运行时
.dockerignore             把 data/、node_modules、dist、.venv 挡在构建上下文外
docker-compose.yml        单服务编排；只引用镜像，不含 build（脚本生成，可手改）
scripts/
  build_image.sh          构建镜像
  config_compose.sh       交互式生成/修改 docker-compose.yml
  deploy.sh               一键：构建 → 配置 → up -d → 健康检查
  lib/common.sh           三者共用：日志、docker 探测、交互提问、yml 取值
```

## 数据流

1. `POST /api/tasks` → `codes.parse_text()` 解析 → `db.create_task()` 建 task + items → `runner.submit()`
2. Runner 逐 item 处理：图库命中（`_reuse_from_library`，零联网）→ 否则 `_resolve()` 联网搜索 + 抓详情，建 images 行
3. 每个 item 一个 `ThreadPoolExecutor`（`IMAGE_WORKERS`）并发下载，`_finalize_item()` 收尾并写图库
4. 所有写操作 bump 全局 `rev`；前端 `GET /api/tasks/{id}/stream?rev=N` 按 rev 增量拉 diff，事件 `task / items / images / removed / rev / done`

## 硬性约定

**1. 前后端解析规则是镜像，后端为准**
`backend/codes.py` 和 `web/src/lib/codes.ts` 必须同规则、同输出结构。改任一侧都要同步另一侧，并同时补两边的测试（`test_codes.py` + `codes.test.ts`）。

**2. 状态变更必须走 db 的写函数**
改行一律用 `db.set_item / set_image / touch_task / …`，它们会 `next_rev()`。**直接用 `conn().execute("UPDATE …")` 会让 rev 停滞，SSE 永远推不出去。** 删行必须同时往 `removals` 表写墓碑，否则前端删不掉旧数据（见 `db.delete_item`）。

**3. 所有 SQL 写入都在 `_write_lock` 里 + 用 `conn()`（线程本地连接）**
不要在线程间共享 connection，不要自己 new `sqlite3.connect`。

**4. 删除 / 取消路径必须二次校验**
任何经过网络 IO 的地方（`_resolve`、`_reuse_from_library`、`_download_one` 落盘前），返回后都要重新确认 `db.get_item(item_id)` 还在、`is_item_cancelled(item_id)` / `is_cancelled(task_id)` 为假，否则会出现「用户删了条目，数据又被写回来」。新增联网步骤时照抄这个模式。

**5. 图片路径规则**
落盘固定在 `LIBRARY_DIR/<safe_dir_name(番号)>/`，文件名 `cover.<ext>` / `sample-NN.<ext>`；数据库里存**相对 `IMAGES_DIR` 的 posix 相对路径**（`library/XXX-123/cover.jpg`），前端用 `mediaUrl()` 拼。删除任务/条目**不删图库文件**（共享缓存，别的任务还在复用）。

**6. 重试策略只在 worker**
`crawler.download()` 是单次尝试，失败直接抛。重试循环、退避、`images.attempts` 记账都在 `worker._download_one`；用尽 `MAX_IMAGE_ATTEMPTS` 后置 `failed` 等用户手动重试（`POST /api/tasks/{id}/retry`）。

**7. 占位图识别不要退化**
图床对不存在的详情图返回同一张 "NOW PRINTING"。现有方案：番号内内容 md5 去重（`_DuplicateGuard`）+ 全局自学习黑名单 `data/placeholder_hashes.txt`，命中置 `skipped`。不要改回「按 URL 猜」之类的启发式。

**8. 别乱加依赖**
后端只允许标准库 + `requirements.txt` 里的包；前端只有 `vue / vue-router` + 开发依赖。加新库前先说清理由。前端不引 UI 框架，样式写在 `styles.css`，复用已有 CSS 变量。

**9. 状态常量集中在 `db.py` 顶部**
`TASK_* / ITEM_* / IMG_*` 与前端 `api.ts` 的类型是契约，新增状态要同时改：`db.py` 常量、`app.py`/`worker.py` 逻辑、`api.ts` 联合类型、`status.ts` 文案、相关测试。

**10. 文案用中文**
后端错误文案、前端 UI 文案、注释、提交信息一律中文。`HTTPException` 的 `detail` 会直接显示给用户。

## 可调参数（环境变量，见 config.py）

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `APHRODITE_DATA` | `./data` | 数据根目录（库 + 图片） |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8000` | 监听地址 |
| `JAVBUS_BASE` | `https://www.javbus.com` | 站点地址（镜像站可换） |
| `APHRODITE_PAGE_INTERVAL` | `1.2` | 页面请求最小间隔秒，防风控 |
| `APHRODITE_IMAGE_INTERVAL` | `0.15` | 图片下载最小间隔秒 |
| `APHRODITE_TASK_WORKERS` | `2` | 同时执行的任务数 |
| `APHRODITE_IMAGE_WORKERS` | `5` | 单番号图片并发数 |
| `APHRODITE_MAX_ATTEMPTS` | `5` | 单图最多尝试次数 |
| `BACKEND_ORIGIN`（仅 vite dev） | `http://127.0.0.1:8000` | dev proxy 目标 |

线上限流是**全局**的（`crawler.JavBus._throttle` 加锁），调大并发不会绕过站点风控，只会让线程空等。

## 加功能的推荐路径

- **新接口**：`app.py` 加路由 → `db.py` 加读写函数（记得 rev / removals）→ `api.ts` 加类型和函数 → 对应 view → 补 `backend/tests/test_api.py` 与前端用例。
- **改 schema**：`SCHEMA` 用 `CREATE TABLE IF NOT EXISTS`，已有库靠 `_migrate()` 补列（`ALTER TABLE ADD COLUMN`）。两步都要写，否则老用户的库会缺字段。
- **改抓取解析**：先用真实番号本地跑一遍 `client().search()` / `client().detail()` 确认选择器仍匹配；站点改版时 `CrawlError` 会浮到 `item.error` 上。
- **加测试**：后端数据目录由 `conftest.py` 指向临时目录，测试之间共享 session 级库（需要干净库就手动清表）；联网一律 mock。

## 坑

- Windows 下 `python run.py &` 在 shell 结束时会被杀 —— 起服务用后台任务方式，别用 `&`。
- 前端改完不 `npm run build`，单端口模式看到的还是旧页面；`web/dist` 不存在时后端返回 503 并提示构建命令。
- `data/`、`web/node_modules/`、`web/dist/`、`.venv/` 都已在 `.gitignore`，不要提交。
- 提交页**不逐条渲染**番号 chip，只显示统计数字 + 问题行卡片（设计如此，600+ 番号也不卡）。别改回全量列表渲染。单任务上限 2000 个番号（`app.py`），超出直接 400。
- 任务被中断后重启会自动 resume（`runner.resume()`）；已存在的图片文件按文件名直接复用，不重复下载。

## 工作记录

改动完成后往 `.workbuddy/memory/YYYY-MM-DD.md` 追加一条：做了什么、关键决策、踩到的坑。这是跨会话的唯一记忆来源。
