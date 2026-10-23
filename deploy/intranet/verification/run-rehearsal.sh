#!/usr/bin/env bash
# Rehearse the real image with an external TEST model and temporary state.
set -euo pipefail
cd "$(dirname "$0")/.."
BUNDLE=$(pwd -P)
NAME=opencode-demo-rehearsal
IMAGE=opencode-agent-demo:20261018-office-amd64
ACTION=${1:-help}
if (($#)); then shift; fi

fail() { printf '错误：%s\n' "$*" >&2; exit 1; }
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
owned() {
  [[ $(docker_cli inspect --format '{{index .Config.Labels "io.opencode-demo.rehearsal"}}' "$NAME") == "$BUNDLE" ]] \
    || fail '同名容器不属于本项目的模拟验收，未作修改。'
}

case "$ACTION" in
  up)
    [[ -f config/demo.env ]] || fail '缺少 config/demo.env。'
    docker_cli image inspect "$IMAGE" >/dev/null
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
      owned
      fail '模拟容器已存在。先执行本脚本 stop，再重新 up。'
    fi
    docker_cli run -d --name "$NAME" --pull never --network host \
      --stop-timeout 40 --label "io.opencode-demo.rehearsal=$BUNDLE" \
      --log-driver json-file --log-opt max-size=10m --log-opt max-file=2 \
      --tmpfs /app/var:rw,nosuid,size=2g,mode=700 \
      --tmpfs /app/.opencode:rw,nosuid,size=32m,mode=700 \
      --env-file "$BUNDLE/config/demo.env" \
      -e OFFICE_SMOKE_USERNAME=office-test-admin -e OFFICE_SMOKE_PASSWORD=office-protocol-test-password \
      -v "$BUNDLE/verification:/opt/rehearsal:ro" \
      --entrypoint python "$IMAGE" /opt/rehearsal/rehearsal_entry.py
    printf '已启动模拟验收：真实后端/OpenCode/MCP/采样 + TEST 模型；并非真实模型推理。\n'
    printf '端口沿用 config/demo.env；模拟数据只在容器临时内存盘中，未写入项目 data/。\n'
    ;;
  check|smoke)
    owned
    docker_cli exec "$NAME" python /opt/deploy/runtime.py "$ACTION" "$@"
    ;;
  status)
    owned
    docker_cli inspect --format 'state={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}} image={{.Image}}' "$NAME"
    ;;
  logs) owned; docker_cli logs --tail 150 "$@" "$NAME" ;;
  stop)
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
      owned
      docker_cli stop --time 40 "$NAME" >/dev/null
      docker_cli rm "$NAME" >/dev/null
    fi
    printf '模拟容器已清理；项目配置、正式 data/ 和镜像保留。\n'
    ;;
  *)
    printf '模拟验收：bash verification/run-rehearsal.sh up | check --wait 300 | smoke | status | logs [-f] | stop\n'
    ;;
esac
