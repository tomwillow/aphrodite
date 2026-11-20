#!/usr/bin/env bash
# 生成 / 修改 docker-compose.yml（不构建镜像、不启动容器）。
#
# 配置来源：项目根目录的 .env（KEY=value，不入 git），其次是现有 docker-compose.yml，
# 最后是内置默认值。默认不询问，直接按 .env/现有值写 compose；
# 加 -i 走交互式问答，答完把全部答案回写 .env，下次部署就不用再答；
# .env 不存在时也会自动进入交互式（传 -y 可强制跳过）。
#
# 用法：
#   scripts/config_compose.sh                    # 按 .env/现有值生成 compose，不问
#   scripts/config_compose.sh -i                 # 交互式问答，答完写回 .env
#   scripts/config_compose.sh --image foo:v1     # 只把镜像名改掉，其余不动
#   scripts/config_compose.sh -y                 # 强制静默（.env 缺失也不交互）

set -euo pipefail

# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

INTERACTIVE=0
ASSUME_YES=0
IMAGE_OVERRIDE=""

# 影片目录列表校验：空 = 不启用；每项非空且不含冒号（冒号会破坏 volume 语法）
v_film_dirs() {
    [ -z "$1" ] && return 0
    local IFS=','
    for p in $1; do
        [ -n "$p" ] && [[ "$p" != *:* ]] || return 1
    done
    return 0
}

# 路径映射校验：空 = 不启用；每项必须是 容器路径=本机路径
v_film_map() {
    [ -z "$1" ] && return 0
    local IFS=','
    for p in $1; do
        [[ "$p" == *=* ]] || return 1
    done
    return 0
}

usage() {
    cat <<'EOF'
生成 / 修改 docker-compose.yml

用法: scripts/config_compose.sh [选项]

选项:
  --image <镜像名>   覆盖镜像名（其余项不动）
  -i, --interactive  交互式问答，答完把全部配置写回 .env
  -y, --yes          强制静默：.env 缺失也不进入交互式，按内置默认值生成
  -h, --help         显示本帮助

说明:
  · 配置来源：.env > 现有 docker-compose.yml > 内置默认值
  · 容器内端口固定 8000，只配置宿主端口与监听地址
  · 数据目录会挂载到容器 /data（数据库 + 图库），务必持久化
  · 写文件前会把原文件备份成 docker-compose.yml.bak
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --image)
            [ $# -ge 2 ] || die "--image 需要跟一个镜像名"
            IMAGE_OVERRIDE="$2"; shift 2 ;;
        -i|--interactive) INTERACTIVE=1; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1（--help 查看用法）" ;;
    esac
done

# .env 缺失时自动进入交互式（-y 显式跳过）
if [ ! -f "$ENV_FILE" ] && [ "$ASSUME_YES" -eq 0 ]; then
    warn "未找到 .env，自动进入交互式问答（答完写回 .env；传 -y 可强制跳过）"
    INTERACTIVE=1
fi

# ---------- 1. 收集当前值（.env 优先，其次现有 compose，最后内置默认值） ----------

cur_image="$(env_get IMAGE "$(yml_value image "$DEFAULT_IMAGE")")"
if [ -n "$IMAGE_OVERRIDE" ]; then
    cur_image="$IMAGE_OVERRIDE"
fi

# 端口映射：支持 8000:8000 与 127.0.0.1:8000:8000 两种写法
port_spec="$(yml_port_spec)"
port_spec="${port_spec:-${CONTAINER_PORT}:${CONTAINER_PORT}}"
IFS=':' read -r -a port_parts <<< "$port_spec"
yml_bind="0.0.0.0"
yml_port="$CONTAINER_PORT"
case "${#port_parts[@]}" in
    1) yml_port="${port_parts[0]}" ;;
    2) yml_port="${port_parts[0]}" ;;
    3) yml_bind="${port_parts[0]}"; yml_port="${port_parts[1]}" ;;
esac
cur_bind="$(env_get BIND_ADDR "$yml_bind")"
cur_port="$(env_get HOST_PORT "$yml_port")"

cur_container="$(env_get CONTAINER_NAME "$(yml_value container_name "aphrodite")")"
cur_data="$(env_get DATA_DIR "$(yml_data_dir)")"
cur_data="${cur_data:-./data}"
cur_restart="$(env_get RESTART "$(yml_value restart "unless-stopped")")"
cur_tz="$(env_get TZ "$(yml_value TZ "Asia/Shanghai")")"
cur_javbus="$(env_get JAVBUS_BASE "$(yml_value JAVBUS_BASE "https://www.javbus.com")")"
cur_page="$(env_get PAGE_INTERVAL "$(yml_value APHRODITE_PAGE_INTERVAL "1.2")")"
cur_image_int="$(env_get IMAGE_INTERVAL "$(yml_value APHRODITE_IMAGE_INTERVAL "0.15")")"
cur_task_workers="$(env_get TASK_WORKERS "$(yml_value APHRODITE_TASK_WORKERS "2")")"
cur_image_workers="$(env_get IMAGE_WORKERS "$(yml_value APHRODITE_IMAGE_WORKERS "5")")"
cur_max_attempts="$(env_get MAX_ATTEMPTS "$(yml_value APHRODITE_MAX_ATTEMPTS "5")")"
cur_film_dirs="$(env_get FILM_DIRS "$(yml_film_host_dirs)")"
cur_film_map="$(env_get FILM_PATH_MAP "$(yml_value APHRODITE_FILM_PATH_MAP "")")"

# ---------- 2. 交互 ----------

IMAGE="$cur_image"
CONTAINER_NAME="$cur_container"
BIND_ADDR="$cur_bind"
HOST_PORT="$cur_port"
DATA_DIR="$cur_data"
RESTART="$cur_restart"
TZ_VALUE="$cur_tz"
JAVBUS_BASE="$cur_javbus"
PAGE_INTERVAL="$cur_page"
IMAGE_INTERVAL="$cur_image_int"
TASK_WORKERS="$cur_task_workers"
IMAGE_WORKERS="$cur_image_workers"
MAX_ATTEMPTS="$cur_max_attempts"
FILM_DIRS="$cur_film_dirs"
FILM_PATH_MAP="$cur_film_map"

if [ "$INTERACTIVE" -eq 1 ]; then
    step "配置 docker-compose.yml"
    info "直接回车 = 保留方括号里的值；答完会把全部配置写回 .env（下次部署不再询问）"

    printf '\n%s— 基础 —%s\n' "$C_BOLD" "$C_END"
    IMAGE="$(ask_value "镜像名（必须已构建）" "$IMAGE" v_image)"
    CONTAINER_NAME="$(ask_value "容器名" "$CONTAINER_NAME" v_name)"
    BIND_ADDR="$(ask_value "监听地址（0.0.0.0=所有网卡，127.0.0.1=仅本机）" "$BIND_ADDR" v_ip)"
    HOST_PORT="$(ask_value "宿主机端口（容器内固定 $CONTAINER_PORT）" "$HOST_PORT" v_port)"
    DATA_DIR="$(ask_value "数据目录（挂到容器 /data，存数据库+图库）" "$DATA_DIR" v_path)"
    RESTART="$(ask_value "重启策略（unless-stopped / always / no）" "$RESTART" v_word)"
    TZ_VALUE="$(ask_value "时区" "$TZ_VALUE" v_word)"

    printf '\n%s— 抓取参数（影响风控与速度，不确定就回车）—%s\n' "$C_BOLD" "$C_END"
    JAVBUS_BASE="$(ask_value "站点地址 JAVBUS_BASE（有镜像站可换）" "$JAVBUS_BASE" v_url)"
    PAGE_INTERVAL="$(ask_value "页面请求最小间隔（秒）" "$PAGE_INTERVAL" v_num)"
    IMAGE_INTERVAL="$(ask_value "图片下载最小间隔（秒）" "$IMAGE_INTERVAL" v_num)"
    TASK_WORKERS="$(ask_value "同时执行的任务数" "$TASK_WORKERS" v_int)"
    IMAGE_WORKERS="$(ask_value "单个番号的图片并发数" "$IMAGE_WORKERS" v_int)"
    MAX_ATTEMPTS="$(ask_value "单张图片最大尝试次数" "$MAX_ATTEMPTS" v_int)"

    printf '\n%s— 影片管理（不启用直接回车）—%s\n' "$C_BOLD" "$C_END"
    info "影片目录会逐个挂载到容器 /data/films/<目录名> 下，逗号分隔可填多个"
    FILM_DIRS="$(ask_value "影片目录（宿主机路径，逗号分隔）" "$FILM_DIRS" v_film_dirs)"
    FILM_PATH_MAP="$(ask_value "容器→本机路径映射（打开按钮用，如 /data/films=Z:\\Films，逗号分隔）" "$FILM_PATH_MAP" v_film_map)"

    env_write_back \
        "IMAGE=$IMAGE" \
        "CONTAINER_NAME=$CONTAINER_NAME" \
        "BIND_ADDR=$BIND_ADDR" \
        "HOST_PORT=$HOST_PORT" \
        "DATA_DIR=$DATA_DIR" \
        "RESTART=$RESTART" \
        "TZ=$TZ_VALUE" \
        "JAVBUS_BASE=$JAVBUS_BASE" \
        "PAGE_INTERVAL=$PAGE_INTERVAL" \
        "IMAGE_INTERVAL=$IMAGE_INTERVAL" \
        "TASK_WORKERS=$TASK_WORKERS" \
        "IMAGE_WORKERS=$IMAGE_WORKERS" \
        "MAX_ATTEMPTS=$MAX_ATTEMPTS" \
        "FILM_DIRS=$FILM_DIRS" \
        "FILM_PATH_MAP=$FILM_PATH_MAP"
    ok "配置已写回 $ENV_FILE（该文件不入 git）"
else
    if env_exists; then
        info "配置读取自 $(basename "$ENV_FILE")（改配置可直接编辑该文件，或加 -i 交互问答）"
    else
        warn "未找到 .env 且已指定 -y，全部按现有 compose / 内置默认值生成"
    fi
fi

# ---------- 3. 派生与校验 ----------

# 数据目录：展开 ~，相对路径按 compose 文件所在目录（即项目根）解析
case "$DATA_DIR" in
    "~")   DATA_DIR="$HOME" ;;
    "~/"*) DATA_DIR="$HOME/${DATA_DIR#\~/}" ;;
esac

if [[ "$DATA_DIR" = /* ]]; then
    data_abs="$DATA_DIR"
else
    data_abs="$APHRODITE_ROOT/${DATA_DIR#./}"
fi

if ! mkdir -p "$data_abs" 2>/dev/null; then
    warn "无法创建数据目录 $data_abs（容器启动时 docker 会用 root 建，之后注意权限）"
elif [ ! -w "$data_abs" ]; then
    warn "数据目录 $data_abs 当前用户不可写，容器以 root 运行通常没问题，但备份/清理会麻烦"
fi

if [ "$BIND_ADDR" = "0.0.0.0" ]; then
    PORT_MAPPING="${HOST_PORT}:${CONTAINER_PORT}"
else
    PORT_MAPPING="${BIND_ADDR}:${HOST_PORT}:${CONTAINER_PORT}"
fi

[ -w "$(dirname "$COMPOSE_FILE")" ] \
    || die "目录不可写，无法生成 $(basename "$COMPOSE_FILE")：$(dirname "$COMPOSE_FILE")"

# ---------- 影片目录：宿主路径 -> 容器卷 + 环境变量 ----------

FILM_VOLUME_LINES=""
FILM_ENV_LINES=""
if [ -n "$FILM_DIRS" ]; then
    declare -a film_container_paths=()
    idx=0
    used=""
    oldIFS="$IFS"; IFS=','
    for host_dir in $FILM_DIRS; do
        idx=$((idx + 1))
        host_dir="${host_dir#"${host_dir%%[![:space:]]*}"}"   # ltrim
        host_dir="${host_dir%"${host_dir##*[![:space:]]}"}"   # rtrim
        [ -n "$host_dir" ] || continue
        base="$(basename "$host_dir")"
        base="$(printf '%s' "$base" | tr -c 'A-Za-z0-9._-' '_')"
        base="${base:-dir$idx}"
        # 容器内路径不重复：重名追加序号
        container="/data/films/$base"
        n=2
        while printf '%s\n' "$used" | grep -qxF "$container"; do
            container="/data/films/$base$n"
            n=$((n + 1))
        done
        used="$used$container"$'\n'
        film_container_paths+=("$container")
        FILM_VOLUME_LINES+="      - ${host_dir}:${container}"$'\n'
    done
    IFS="$oldIFS"
    joined="$(IFS=,; printf '%s' "${film_container_paths[*]}")"
    FILM_ENV_LINES="      APHRODITE_FILM_DIRS: \"${joined}\""$'\n'
    if [ -n "$FILM_PATH_MAP" ]; then
        FILM_ENV_LINES+="      APHRODITE_FILM_PATH_MAP: \"${FILM_PATH_MAP}\""$'\n'
    fi
fi

# ---------- 4. 写文件 ----------

if [ -f "$COMPOSE_FILE" ]; then
    cp "$COMPOSE_FILE" "$COMPOSE_FILE.bak"
    info "原文件已备份为 $(basename "$COMPOSE_FILE").bak"
fi

cat > "$COMPOSE_FILE" <<EOF
# Aphrodite 容器编排（Linux 部署用）
#
# 本文件由 scripts/config_compose.sh 生成，也可以手工改。
# 部署配置以项目根目录的 .env 为准（scripts/deploy.sh -i 可交互生成/更新它）；
# 本文件里改的值会在下次按 .env 重新生成时被覆盖。
#
# 前置条件：本地已有镜像（先跑 scripts/build_image.sh）。
# 启动：docker compose up -d     查看日志：docker compose logs -f

services:
  aphrodite:
    image: ${IMAGE}
    container_name: ${CONTAINER_NAME}
    restart: ${RESTART}
    ports:
      - "${PORT_MAPPING}"
    environment:
      TZ: ${TZ_VALUE}
      APP_HOST: 0.0.0.0
      APP_PORT: "${CONTAINER_PORT}"
      APHRODITE_DATA: /data
      JAVBUS_BASE: "${JAVBUS_BASE}"
      APHRODITE_PAGE_INTERVAL: "${PAGE_INTERVAL}"
      APHRODITE_IMAGE_INTERVAL: "${IMAGE_INTERVAL}"
      APHRODITE_TASK_WORKERS: "${TASK_WORKERS}"
      APHRODITE_IMAGE_WORKERS: "${IMAGE_WORKERS}"
      APHRODITE_MAX_ATTEMPTS: "${MAX_ATTEMPTS}"
${FILM_ENV_LINES}    volumes:
      - ${DATA_DIR}:/data
${FILM_VOLUME_LINES}    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${CONTAINER_PORT}/api/health', timeout=3).status == 200 else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s
EOF

ok "已写入 $COMPOSE_FILE"

# ---------- 5. 校验 ----------

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    init_compose
    if compose config -q; then
        ok "docker compose config 校验通过"
    else
        die "生成的 compose 文件校验不通过（上方为 compose 报错），已保留备份 $(basename "$COMPOSE_FILE").bak"
    fi
    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
        warn "本地还没有镜像 $IMAGE，先跑 scripts/build_image.sh -t $IMAGE 再 up"
    fi
else
    warn "本机没有可用的 docker，跳过 docker compose config 校验"
fi

# ---------- 6. 摘要 ----------

printf '\n%s配置摘要%s\n' "$C_BOLD" "$C_END"
printf '  镜像        %s\n' "$IMAGE"
printf '  容器名      %s\n' "$CONTAINER_NAME"
printf '  访问地址    http://%s:%s\n' \
    "$([ "$BIND_ADDR" = "0.0.0.0" ] && echo 127.0.0.1 || echo "$BIND_ADDR")" "$HOST_PORT"
printf '  端口映射    %s\n' "$PORT_MAPPING"
printf '  数据目录    %s -> /data\n' "$data_abs"
printf '  时区/重启   %s / %s\n' "$TZ_VALUE" "$RESTART"
printf '  抓取节奏    页面 %ss，图片 %ss，任务并发 %s，图片并发 %s，最多重试 %s 次\n' \
    "$PAGE_INTERVAL" "$IMAGE_INTERVAL" "$TASK_WORKERS" "$IMAGE_WORKERS" "$MAX_ATTEMPTS"
if [ -n "$FILM_DIRS" ]; then
    printf '  影片目录    %s\n' "$FILM_DIRS"
    [ -n "$FILM_PATH_MAP" ] && printf '  路径映射    %s\n' "$FILM_PATH_MAP"
fi

printf '\n下一步：%sdocker compose up -d%s\n' "$C_INFO" "$C_END"
