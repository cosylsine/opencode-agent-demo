#!/usr/bin/env bash
# Separate ports and tmpfs data; does not touch an existing deployment.
set -euo pipefail
cd "$(dirname "$0")/.."
BUNDLE=$(pwd -P)
NAME=opencode-office-v1-rehearsal
IMAGE=opencode-agent-demo:20261018-office-amd64
ACTION=${1:-help}
if (($#)); then shift; fi
docker_cli() {
  if command -v docker >/dev/null 2>&1; then docker "$@"; else
    if [[ ! -x .tools/docker/docker ]]; then
      mkdir -p .tools
      tar -xzf runtime/docker-28.5.2-linux-x86_64.tgz -C .tools
    fi
    .tools/docker/docker "$@"
  fi
}
owned() {
  [[ $(docker_cli inspect --format '{{index .Config.Labels "io.opencode-demo.office-rehearsal"}}' "$NAME") == "$BUNDLE" ]] || { echo '容器归属不匹配'; exit 1; }
}
case "$ACTION" in
  up)
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then echo '测试容器已存在，请先 stop。'; exit 1; fi
    docker_cli run -d --name "$NAME" --pull never --network host --stop-timeout 40 \
      --label "io.opencode-demo.office-rehearsal=$BUNDLE" \
      --tmpfs /app/var:rw,nosuid,size=2g,mode=700 --tmpfs /app/.opencode:rw,nosuid,size=32m,mode=700 \
      -e APP_HOST=127.0.0.1 -e APP_PORT=18101 -e OPENCODE_PORT=14101 \
      -e DEMO_ORIGIN=http://127.0.0.1:18101 -e VLLM_BASE_URL=http://127.0.0.1:4641/v1 \
      -e VLLM_MODEL=Qwen3.8-27B -e VLLM_API_KEY=EMPTY -e VLLM_CONTEXT=32768 \
      -e OFFICE_SMOKE_USERNAME=office-test-admin -e OFFICE_SMOKE_PASSWORD=office-protocol-test-password \
      -v "$BUNDLE/verification:/opt/rehearsal:ro" \
      --entrypoint python "$IMAGE" /opt/rehearsal/rehearsal_entry.py
    echo '已启动独立 TEST 协议验收，页面 127.0.0.1:18101；不包含真实 Qwen 推理。'
    ;;
  check) owned; docker_cli exec "$NAME" python /opt/deploy/runtime.py check "$@" ;;
  smoke)
    owned
    docker_cli exec "$NAME" python scripts/smoke_office.py --base http://127.0.0.1:18101 --protocol-only --output /app/var/backend/office-acceptance.json
    ;;
  export)
    owned
    mkdir -p verification/office-v1
    # Read through exec: some Docker hosts cannot archive files from tmpfs via cp.
    docker_cli exec "$NAME" python -c 'import json; from pathlib import Path; print(json.dumps(json.loads(Path("/app/var/backend/office-acceptance.json").read_text()), ensure_ascii=False, indent=2))' \
      > verification/office-v1/protocol-acceptance.json.tmp
    mv verification/office-v1/protocol-acceptance.json.tmp verification/office-v1/protocol-acceptance.json
    ;;
  logs) owned; docker_cli logs --tail 120 "$@" "$NAME" ;;
  status) owned; docker_cli inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$NAME" ;;
  stop)
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
      owned; docker_cli stop --time 40 "$NAME" >/dev/null; docker_cli rm "$NAME" >/dev/null
    fi
    ;;
  *) echo 'up | check --wait 300 | smoke | export | logs | status | stop' ;;
esac
