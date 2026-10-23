#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
BUNDLE=$(pwd -P)
IMAGE=opencode-agent-demo:20261021-intranet-amd64
NAME=opencode-agent-demo
LABEL=io.opencode-demo.offline
CONFIG="$BUNDLE/config/demo.env"
ACTION=${1:-help}
if (($#)); then shift; fi

fail() { printf '错误：%s\n' "$*" >&2; exit 1; }
platform() {
  [[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail '此包仅适用于 Linux x86_64。'
}
docker_cli() {
  if command -v docker >/dev/null 2>&1; then
    docker "$@"
  else
    if [[ ! -x "$BUNDLE/.tools/docker/docker" ]]; then
      mkdir -p "$BUNDLE/.tools"
      tar -xzf "$BUNDLE/runtime/docker-28.5.2-linux-x86_64.tgz" -C "$BUNDLE/.tools"
    fi
    "$BUNDLE/.tools/docker/docker" "$@"
  fi
}
daemon() {
  platform
  docker_cli info >/dev/null 2>&1 || fail 'Docker 不可用。先执行 sudo bash scripts/install-docker.sh；已有 Docker 时用 sudo 运行本命令。'
  [[ $(docker_cli info --format '{{.OSType}}/{{.Architecture}}') =~ ^linux/(x86_64|amd64)$ ]] || fail 'Docker 必须使用 Linux amd64。'
}
config() {
  [[ -f "$CONFIG" ]] || fail '请先运行 bash deploy.sh init，再编辑 config/demo.env。'
  if LC_ALL=C grep -q $'\r' "$CONFIG"; then fail 'config/demo.env 含 Windows CRLF 换行；用 sed -i "s/\r$//" config/demo.env 转成 LF。'; fi
  # --env-file uses literal values, not shell expressions. Reject common copy/paste mistakes.
  if grep -Eq "^[[:space:]]*export |^[A-Z_]+=[\"']" "$CONFIG"; then
    fail '配置请使用 KEY=value，不要写 export 或在值外加引号。'
  fi
}
image_ready() { docker_cli image inspect "$IMAGE" >/dev/null 2>&1 || fail '镜像尚未导入：sudo bash deploy.sh load'; }
owned() {
  [[ $(docker_cli inspect --format '{{index .Config.Labels "io.opencode-demo.offline"}}' "$NAME") == 1 ]] || fail '同名容器不属于本部署包，未作修改。'
  local owner
  owner=$(docker_cli inspect --format '{{index .Config.Labels "io.opencode-demo.bundle"}}' "$NAME")
  [[ "$owner" == "$BUNDLE" ]] || fail "容器属于其他目录：$owner；请从该目录操作。"
}
mounts() {
  mkdir -p "$BUNDLE/data/var" "$BUNDLE/data/catalog"
  chmod 700 "$BUNDLE/data" "$BUNDLE/data/var" "$BUNDLE/data/catalog"
  MOUNTS=(-v "$BUNDLE/data/var:/app/var:Z" -v "$BUNDLE/data/catalog:/app/.opencode:Z")
}
run_tool() {
  daemon; config; image_ready; mounts
  docker_cli run --rm --pull never --network host --env-file "$CONFIG" \
    "${MOUNTS[@]}" "$IMAGE" "$@"
}
up() {
  daemon; config; image_ready; mounts
  [[ -w "$BUNDLE/data/var" ]] || fail '数据目录不可写；请用 sudo 运行。'
  if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
    owned
    printf '应用配置，重新创建容器（保留 data/）……\n'
    docker_cli stop --time 30 "$NAME" >/dev/null
    docker_cli rm "$NAME" >/dev/null
  fi
  docker_cli run --rm --pull never --network host --env-file "$CONFIG" \
    "${MOUNTS[@]}" "$IMAGE" preflight
  docker_cli run -d --name "$NAME" --pull never --network host \
    --restart unless-stopped --stop-timeout 30 \
    --label "$LABEL=1" --label "io.opencode-demo.bundle=$BUNDLE" \
    --log-driver json-file --log-opt max-size=10m --log-opt max-file=3 \
    --env-file "$CONFIG" "${MOUNTS[@]}" "$IMAGE" >/dev/null
  printf '已启动。运行 sudo bash deploy.sh check --wait 300 检查就绪。\n'
}

case "$ACTION" in
  verify)
    if [[ -f RELEASE-SHA256SUMS ]]; then
      sha256sum -c RELEASE-SHA256SUMS --quiet
      printf '20261021 累计更新文件 SHA-256 校验通过。\n'
    elif [[ -f verification/LAN-SYNC-SHA256SUMS ]]; then
      # Keep the original delivery manifest. The explicit patch manifest
      # supersedes only the files changed or added during LAN synchronization.
      awk 'NR == FNR { patched[$2] = 1; next } !($2 in patched)' \
        verification/LAN-SYNC-SHA256SUMS SHA256SUMS | sha256sum -c --quiet -
      sha256sum -c verification/LAN-SYNC-SHA256SUMS --quiet
      printf '原交付文件及内网同步文件 SHA-256 校验通过（原 SHA256SUMS 保留）。\n'
    else
      sha256sum -c SHA256SUMS --quiet
      printf '所有交付文件 SHA-256 校验通过。\n'
    fi
    ;;
  init)
    platform
    if [[ -e "$CONFIG" ]]; then
      printf 'config/demo.env 已存在，保留现有配置。\n'
    else
      cp config/demo.env.example "$CONFIG"
      chmod 600 "$CONFIG"
      printf '已创建 config/demo.env；现在修改 vLLM 地址、模型 ID 和密钥。\n'
    fi
    ;;
  load)
    daemon
    sha256sum -c images/SHA256SUMS --quiet
    gzip -dc images/offline-images.tar.gz | docker_cli load
    image_ready
    printf '已导入部门智能体平台与办公依赖镜像；按 INTRANET-UPDATE-20261021.md 初始化管理员并启动。\n'
    ;;
  up|restart) up ;;
  stop|down)
    daemon
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
      owned; docker_cli stop --time 30 "$NAME"
      if [[ "$ACTION" == down ]]; then docker_cli rm "$NAME"; fi
    fi
    printf '服务已停止；data/ 中的数据保留。\n'
    ;;
  status)
    daemon; owned
    docker_cli inspect --format 'state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}} restarts={{.RestartCount}}' "$NAME"
    ;;
  logs) daemon; owned; docker_cli logs --tail 150 "$@" "$NAME" ;;
  check|probe-agent|smoke|smoke-office)
    daemon; owned
    interactive=()
    if [[ "$ACTION" == smoke* ]]; then
      interactive=(-i)
      if [[ -t 0 && -t 1 ]]; then interactive=(-it); fi
    fi
    docker_cli exec "${interactive[@]}" "$NAME" python /opt/deploy/runtime.py "$ACTION" "$@"
    ;;
  models|probe-model) run_tool "$ACTION" "$@" ;;
  init-admin)
    daemon; config; image_ready; mounts
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
      owned
      [[ $(docker_cli inspect --format '{{.State.Running}}' "$NAME") != true ]] || fail '请先运行 sudo bash deploy.sh stop，再初始化管理员。'
    fi
    interactive=(-i)
    if [[ -t 0 && -t 1 ]]; then interactive=(-it); fi
    docker_cli run --rm "${interactive[@]}" --pull never --network host --env-file "$CONFIG" \
      "${MOUNTS[@]}" "$IMAGE" init-admin
    ;;
  backup)
    daemon; config
    was_running=false
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
      owned
      if [[ $(docker_cli inspect --format '{{.State.Running}}' "$NAME") == true ]]; then
        was_running=true; docker_cli stop --time 30 "$NAME" >/dev/null
      fi
    fi
    restart_after_backup() { if $was_running; then docker_cli start "$NAME" >/dev/null; fi; }
    trap restart_after_backup EXIT
    [[ -d data/var ]] || fail '尚无数据可备份。'
    mkdir -p backups
    archive="backups/backup-$(date +%Y%m%d-%H%M%S).tar.gz"
    umask 077
    # vLLM caches are reproducible and may be in use by an independent service.
    tar -czf "$archive" config/demo.env data/var data/catalog
    printf '备份完成（含配置、密钥、会话、技能、仿真记录）：%s\n' "$archive"
    ;;
  rebuild)
    daemon
    docker_cli image inspect opencode-agent-demo-runtime:20261018-office-amd64 >/dev/null 2>&1 || fail '先运行 load 导入含办公工具的基础依赖镜像。'
    DOCKER_BUILDKIT=0 docker_cli build --pull=false --network none -t "$IMAGE" .
    printf '已断网重建；运行 sudo bash deploy.sh up 应用镜像。\n'
    ;;
  help|*)
    cat <<'HELP'
首次部署：
  bash deploy.sh verify
  sudo bash scripts/install-docker.sh
  sudo bash deploy.sh load
  bash deploy.sh init
  nano config/demo.env
  sudo bash deploy.sh models
  sudo bash deploy.sh probe-model
  sudo bash deploy.sh init-admin
  sudo bash deploy.sh up
  sudo bash deploy.sh check --wait 300
日常操作：status | logs [-f] | stop | up | restart | down | backup
模型排查：models | probe-model | probe-agent
账号初始化（停服执行）：init-admin
完整办公验收（登录后生成测试文件）：smoke-office
可选完整业务验收（会生成仿真记录）：smoke
离线修改源码并重建：rebuild
所有操作都在本地完成；不需要 Compose。
HELP
    ;;
esac
