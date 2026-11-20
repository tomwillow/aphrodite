#!/usr/bin/env bash
# scripts/lib/common.sh —— 部署脚本共用的工具函数（日志、docker 探测、交互提问、yml 取值、.env 读写）
# 不单独执行，由 build_image.sh / config_compose.sh / deploy.sh source。

set -euo pipefail

# 项目根目录（scripts/lib/../..）
APHRODITE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPTS_DIR="$APHRODITE_ROOT/scripts"

# 默认镜像名，可用环境变量 APHRODITE_IMAGE 覆盖
DEFAULT_IMAGE="${APHRODITE_IMAGE:-aphrodite:latest}"
# compose 文件位置，可用环境变量 APHRODITE_COMPOSE_FILE 覆盖
COMPOSE_FILE="${APHRODITE_COMPOSE_FILE:-$APHRODITE_ROOT/docker-compose.yml}"
# 部署配置文件（KEY=value），可用环境变量 APHRODITE_ENV_FILE 覆盖；不入 git
ENV_FILE="${APHRODITE_ENV_FILE:-$APHRODITE_ROOT/.env}"
# 容器内端口固定 8000（run.py 与 compose 都按它写），只有宿主端口可改
CONTAINER_PORT=8000

# 彩色输出：输出被重定向或设置了 NO_COLOR 时自动关闭
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_INFO=$'\033[36m'
    C_OK=$'\033[32m'
    C_WARN=$'\033[33m'
    C_ERR=$'\033[31m'
    C_BOLD=$'\033[1m'
    C_END=$'\033[0m'
else
    C_INFO=''; C_OK=''; C_WARN=''; C_ERR=''; C_BOLD=''; C_END=''
fi

info() { printf '%s[信息]%s %s\n' "$C_INFO" "$C_END" "$*"; }
ok()   { printf '%s[完成]%s %s\n' "$C_OK" "$C_END" "$*"; }
warn() { printf '%s[警告]%s %s\n' "$C_WARN" "$C_END" "$*" >&2; }
die()  { printf '%s[错误]%s %s\n' "$C_ERR" "$C_END" "$*" >&2; exit 1; }
step() { printf '\n%s==> %s%s\n' "$C_BOLD" "$*" "$C_END"; }

# ---------- docker / compose ----------

require_docker() {
    command -v docker >/dev/null 2>&1 \
        || die "未找到 docker 命令，请先安装 Docker Engine（https://docs.docker.com/engine/install/）"
    docker info >/dev/null 2>&1 \
        || die "连不上 docker 守护进程：确认 dockerd 已启动，且当前用户对 /var/run/docker.sock 有权限（sudo usermod -aG docker \$USER 后重新登录）"
}

COMPOSE_BIN=()

# 探测 compose：优先 v2 插件（docker compose），退化为 v1 独立命令
init_compose() {
    if docker compose version >/dev/null 2>&1; then
        COMPOSE_BIN=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_BIN=(docker-compose)
    else
        die "未找到 docker compose，请安装 compose 插件（docker-compose-plugin）"
    fi
}

# 等价于 docker compose -f <COMPOSE_FILE> ...
compose() {
    "${COMPOSE_BIN[@]}" -f "$COMPOSE_FILE" "$@"
}

# ---------- 交互 ----------

# ask <提示> [默认值] -> stdout 输出用户输入，空输入取默认值
# 刻意从 /dev/tty 读：被 deploy.sh 调用、或 stdin 被管道占用时依然能交互
ask() {
    local prompt="$1" default="${2-}" ans=""
    if [ -n "$default" ]; then
        printf '%s%s%s [%s%s%s]: ' "$C_BOLD" "$prompt" "$C_END" "$C_INFO" "$default" "$C_END" >&2
    else
        printf '%s%s%s: ' "$C_BOLD" "$prompt" "$C_END" >&2
    fi
    if ! read -r ans < /dev/tty 2>/dev/null; then
        ans=""
    fi
    printf '%s' "${ans:-$default}"
}

# ask_value <提示> <默认值> [校验函数名] -> 校验不过就重问
ask_value() {
    local prompt="$1" default="$2" validator="${3:-}" ans
    while :; do
        ans="$(ask "$prompt" "$default")"
        if [ -z "$validator" ] || "$validator" "$ans"; then
            printf '%s' "$ans"
            return 0
        fi
        warn "「$ans」不合法，请重新输入"
    done
}

# ---------- 校验 ----------

v_image() { [ -n "$1" ] && [[ "$1" != *[[:space:]]* ]]; }
v_word()  { [ -n "$1" ] && [[ "$1" != *[[:space:]]* ]]; }
v_name()  { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; }
v_port()  { [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ] && [ "$1" -le 65535 ]; }
v_int()   { [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -ge 1 ]; }
v_num()   { [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]; }
v_ip()    {
    [ "$1" = "0.0.0.0" ] || [ "$1" = "::" ] \
        || [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]
}
v_path()  { [ -n "$1" ] && [[ "$1" != *:* ]]; }
v_url()   { [[ "$1" =~ ^https?://[^[:space:]]+$ ]]; }

# ---------- 从已有 docker-compose.yml 取值（当交互默认值） ----------

# yml_value <key> [默认值]：读 "key: value" 形式的行
yml_value() {
    local key="$1" default="${2-}" value=""
    if [ -f "$COMPOSE_FILE" ]; then
        value="$(sed -n "s/^[[:space:]]*${key}:[[:space:]]*//p" "$COMPOSE_FILE" | head -n1 | sed 's/[[:space:]]*$//')"
        value="${value%\"}"
        value="${value#\"}"
    fi
    printf '%s' "${value:-$default}"
}

# 端口映射（compose 里第一处带引号的列表项），形如 8000:8000 / 127.0.0.1:8000:8000
yml_port_spec() {
    [ -f "$COMPOSE_FILE" ] || return 0
    sed -n 's/^[[:space:]]*-[[:space:]]*"\([^"]*\)".*$/\1/p' "$COMPOSE_FILE" | head -n1
}

# 数据卷的宿主机路径（形如 ./data:/data）
yml_data_dir() {
    [ -f "$COMPOSE_FILE" ] || return 0
    sed -n 's/^[[:space:]]*-[[:space:]]*\(.*\):\/data$/\1/p' "$COMPOSE_FILE" | head -n1
}

# 影片目录卷的宿主机路径列表（形如 /mnt/films/a:/data/films/a），逗号连接
yml_film_host_dirs() {
    [ -f "$COMPOSE_FILE" ] || return 0
    sed -n 's/^[[:space:]]*-[[:space:]]*\(.*\):\/data\/films\/.*$/\1/p' "$COMPOSE_FILE" \
        | paste -sd,
}

# ---------- .env 部署配置（KEY=value，不入 git） ----------

env_exists() { [ -f "$ENV_FILE" ]; }

# env_get <KEY> [默认值]：从 .env 读 KEY=value，值两侧引号去掉
env_get() {
    local key="$1" default="${2-}" line=""
    if [ -f "$ENV_FILE" ]; then
        line="$(sed -n "s/^${key}=//p" "$ENV_FILE" | head -n1)"
        line="${line%\"}"; line="${line#\"}"
        line="${line%\'}"; line="${line#\'}"
    fi
    printf '%s' "${line:-$default}"
}

# env_write_back <KEY=value>...：把键值写回 .env
# 保留注释与其他未知行；已有键原地更新，没有的键追加到末尾
# 值里可以再带 =（如 FILM_PATH_MAP=/data/films=Z:\Films），按第一个 = 切分
env_write_back() {
    [ $# -ge 1 ] || return 0
    local -a keys=() values=()
    local kv key value i out="" found
    for kv in "$@"; do
        keys+=("${kv%%=*}")
        values+=("${kv#*=}")
    done
    if [ -f "$ENV_FILE" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            case "$line" in
                [A-Za-z_]*=*)
                    key="${line%%=*}"
                    found=0
                    for i in "${!keys[@]}"; do
                        if [ "${keys[$i]}" = "$key" ]; then
                            out+="${keys[$i]}=${values[$i]}"$'\n'
                            found=1
                            break
                        fi
                    done
                    [ "$found" -eq 1 ] || out+="$line"$'\n'
                    ;;
                *) out+="$line"$'\n' ;;
            esac
        done < "$ENV_FILE"
    fi
    for i in "${!keys[@]}"; do
        if ! printf '%s' "$out" | grep -q "^${keys[$i]}="; then
            out+="${keys[$i]}=${values[$i]}"$'\n'
        fi
    done
    printf '%s' "$out" > "$ENV_FILE"
}
