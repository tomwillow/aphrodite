#!/usr/bin/env bash
# 构建 Aphrodite 的本地 docker 镜像（多阶段：前端 dist + Python 运行时，单镜像单端口）。
#
# 用法：
#   scripts/build_image.sh                    # 构建 aphrodite:latest
#   scripts/build_image.sh -t aphrodite:1.0   # 自定义镜像名
#   scripts/build_image.sh --no-cache         # 不吃构建缓存，完全重建
#   scripts/build_image.sh --platform linux/amd64
#
# 环境变量 APHRODITE_IMAGE 也能改默认镜像名（deploy.sh 会把 -t 透传过来）。

set -euo pipefail

# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

IMAGE="$DEFAULT_IMAGE"
NO_CACHE=0
PLATFORM=""
DOCKERFILE="$APHRODITE_ROOT/Dockerfile"

usage() {
    cat <<'EOF'
构建 Aphrodite 本地镜像

用法: scripts/build_image.sh [选项]

选项:
  -t, --tag <镜像名>     镜像名:标签，默认 aphrodite:latest（也可用环境变量 APHRODITE_IMAGE）
      --no-cache         不使用构建缓存
      --platform <平台>  指定目标平台，如 linux/amd64、linux/arm64（默认跟随本机）
  -h, --help             显示本帮助

示例:
  scripts/build_image.sh -t aphrodite:1.0
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        -t|--tag)
            [ $# -ge 2 ] || die "--tag 需要跟一个镜像名"
            IMAGE="$2"; shift 2 ;;
        --no-cache) NO_CACHE=1; shift ;;
        --platform)
            [ $# -ge 2 ] || die "--platform 需要跟一个平台名"
            PLATFORM="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "未知参数：$1（--help 查看用法）" ;;
    esac
done

v_image "$IMAGE" || die "镜像名不合法：$IMAGE"

require_docker
[ -f "$DOCKERFILE" ] || die "找不到 Dockerfile：$DOCKERFILE"
[ -f "$APHRODITE_ROOT/requirements.txt" ] || die "找不到 requirements.txt"
[ -f "$APHRODITE_ROOT/web/package-lock.json" ] \
    || die "找不到 web/package-lock.json：前端构建用 npm ci，必须有 lock 文件"

step "构建镜像 $IMAGE"
info "构建上下文：$APHRODITE_ROOT"

build_args=(build -f "$DOCKERFILE" -t "$IMAGE")
if [ "$NO_CACHE" -eq 1 ]; then
    build_args+=(--no-cache)
fi
if [ -n "$PLATFORM" ]; then
    build_args+=(--platform "$PLATFORM")
fi
build_args+=("$APHRODITE_ROOT")

if ! docker "${build_args[@]}"; then
    die "镜像构建失败，请查看上方 docker 输出"
fi

image_id="$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null | cut -c8-19 || true)"
image_size="$(docker image inspect --format '{{.Size}}' "$IMAGE" 2>/dev/null || echo 0)"
ok "镜像已就绪：$IMAGE（$(awk -v s="${image_size:-0}" 'BEGIN{printf "%.0f MB", s/1024/1024}')，id ${image_id:-未知}）"

# 如果 compose 里引用的是别的镜像名，提醒同步，避免 up 时拉不到镜像
compose_image="$(yml_value image '')"
if [ -n "$compose_image" ] && [ "$compose_image" != "$IMAGE" ]; then
    warn "$(basename "$COMPOSE_FILE") 里写的是 image: $compose_image，与本次构建的 $IMAGE 不一致"
    warn "跑一次 scripts/config_compose.sh --image $IMAGE 同步，否则 docker compose up -d 会去找 $compose_image"
fi

printf '\n下一步：%s./scripts/config_compose.sh%s（生成/修改编排文件），然后 %sdocker compose up -d%s\n' \
    "$C_INFO" "$C_END" "$C_INFO" "$C_END"
