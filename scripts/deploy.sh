#!/usr/bin/env bash
# 一键部署：构建镜像 -> 交互式生成 docker-compose.yml -> docker compose up -d -> 等健康检查
#
# 只需本机有 docker（+ compose 插件），不需要 python / node 环境：
# 前端构建和后端依赖都在镜像构建过程中完成。
#
# 用法：
#   scripts/deploy.sh                 # 全新部署 / 重新部署（会问你几个问题）
#   scripts/deploy.sh -y              # 全用现有配置，不问
#   scripts/deploy.sh -t aph:v2       # 换镜像标签
#   scripts/deploy.sh --skip-build    # 不重建镜像，直接用已有的
#   scripts/deploy.sh --no-cache      # 重建镜像且不吃缓存

set -euo pipefail

# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

IMAGE_ARG=""
NO_CACHE=0
ASSUME_YES=0
SKIP_BUILD=0
HEALTH_TIMEOUT=90

usage() {
    cat <<'EOF'
一键部署 Aphrodite（Linux）

用法: scripts/deploy.sh [选项]

选项:
  -t, --tag <镜像名>   本次构建/使用的镜像名（默认沿用 docker-compose.yml 里的，否则 aphrodite:latest）
      --no-cache       重建镜像时不使用构建缓存
      --skip-build     跳过镜像构建，直接用本地已有镜像
  -y, --yes            配置阶段不询问，全部用现有值
  -h, --help           显示本帮助

等价于手动执行:
  scripts/build_image.sh  &&  scripts/config_compose.sh  &&  docker compose up -d
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -t|--tag)
            [ $# -ge 2 ] || die "--tag 需要跟一个镜像名"
            IMAGE_ARG="$2"; shift 2 ;;
        --no-cache) NO_CACHE=1; shift ;;
        --skip-build) SKIP_BUILD=1; shift ;;
        -y|--yes) ASSUME_YES=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1（--help 查看用法）" ;;
    esac
done

# 镜像名统一在一处定：沿用现有 compose 的选择，避免每次部署把标签改回默认
IMAGE="${IMAGE_ARG:-$(yml_value image "$DEFAULT_IMAGE")}"
v_image "$IMAGE" || die "镜像名不合法：$IMAGE"

require_docker
init_compose

printf '%sAphrodite 部署%s  项目目录 %s\n' "$C_BOLD" "$C_END" "$APHRODITE_ROOT"

# ---------- 1/3 构建镜像 ----------

step "1/3 构建镜像 $IMAGE"
if [ "$SKIP_BUILD" -eq 1 ]; then
    if docker image inspect "$IMAGE" >/dev/null 2>&1; then
        info "已跳过构建（--skip-build），使用本地镜像 $IMAGE"
    else
        die "本地没有镜像 $IMAGE，不能 --skip-build；去掉该参数重新构建"
    fi
else
    build_args=(-t "$IMAGE")
    if [ "$NO_CACHE" -eq 1 ]; then
        build_args+=(--no-cache)
    fi
    bash "$SCRIPTS_DIR/build_image.sh" "${build_args[@]}"
fi

# ---------- 2/3 生成 compose ----------

step "2/3 配置 $(basename "$COMPOSE_FILE")"
compose_args=(--image "$IMAGE")
if [ "$ASSUME_YES" -eq 1 ]; then
    compose_args+=(-y)
fi
bash "$SCRIPTS_DIR/config_compose.sh" "${compose_args[@]}"

# ---------- 3/3 启动 ----------

step "3/3 启动容器"
compose up -d

wait_ready() {
    local cid status i=0 has_health
    cid="$(compose ps -q aphrodite 2>/dev/null | head -n1 || true)"
    if [ -z "$cid" ]; then
        warn "没找到 aphrodite 容器，跳过健康检查"
        return 1
    fi
    has_health="$(docker inspect -f '{{if .State.Health}}yes{{end}}' "$cid" 2>/dev/null || true)"
    if [ -z "$has_health" ]; then
        if [ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)" = "true" ]; then
            ok "容器已启动（未配置健康检查）"
            return 0
        fi
        warn "容器未能启动"
        return 1
    fi

    info "等待容器就绪（最多 ${HEALTH_TIMEOUT}s）"
    while [ "$i" -lt "$HEALTH_TIMEOUT" ]; do
        status="$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)"
        case "$status" in
            healthy)   ok "健康检查通过"; return 0 ;;
            unhealthy) warn "健康检查失败"; return 1 ;;
        esac
        sleep 1
        i=$((i + 1))
    done
    warn "等待超时，最后状态：$status"
    return 1
}

if ! wait_ready; then
    printf '\n%s最近 80 行日志%s\n' "$C_BOLD" "$C_END"
    compose logs --tail=80 || true
    die "容器没起来，检查上面的日志（常见原因：端口被占用、数据目录无权限、镜像名写错）"
fi

# ---------- 收尾 ----------

addr="$(compose port aphrodite "$CONTAINER_PORT" 2>/dev/null | head -n1 || true)"
host_port="${addr##*:}"
case "$host_port" in
    ''|*[!0-9]*) host_port="$CONTAINER_PORT" ;;
esac

printf '\n%s部署完成%s\n' "$C_OK" "$C_END"
printf '  访问地址    http://127.0.0.1:%s\n' "$host_port"
printf '  数据目录    %s\n' "$(yml_data_dir)"
printf '  常用命令    %s logs -f aphrodite    %s restart    %s down\n' \
    "${COMPOSE_BIN[*]}" "${COMPOSE_BIN[*]}" "${COMPOSE_BIN[*]}"
