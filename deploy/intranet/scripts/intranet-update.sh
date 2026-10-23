#!/usr/bin/env bash
# Run from the copied disc, not from the installation directory.
set -euo pipefail
umask 077
MEDIA=$(cd "$(dirname "$0")/.." && pwd -P)
ACTION=${1:-help}
TARGET=${2:-}
NAME=opencode-agent-demo
NEW_IMAGE=opencode-agent-demo:20261021-intranet-amd64
fail() { echo "错误：$*" >&2; exit 1; }
docker_cli() {
  if command -v docker >/dev/null 2>&1; then docker "$@";
  else "$MEDIA/payload/runtime/docker-28.5.2/docker/docker" "$@"; fi
}
init() {
  [[ -n "$TARGET" && -d "$TARGET" ]] || fail '第二个参数必须是已有部署目录的绝对路径。'
  [[ "$TARGET" == /* ]] || fail '请使用绝对路径。'
  TARGET=$(cd "$TARGET" && pwd -P)
  [[ "$TARGET" != / && "$TARGET" != "$MEDIA" && "$MEDIA/" != "$TARGET/"* && "$TARGET/" != "$MEDIA/"* ]] || fail '光盘副本与部署目录必须相互独立。'
  if [[ "$ACTION" != rollback ]]; then
    [[ -f "$TARGET/deploy.sh" && -f "$TARGET/config/demo.env" ]] || fail '不是已有部署目录（缺 deploy.sh/config/demo.env）。'
  fi
  if ! command -v docker >/dev/null 2>&1; then
    # Extract outside read-only media; never modify a verified payload.
    CLI_TMP=$(mktemp -d)
    tar -xzf "$MEDIA/payload/runtime/docker-28.5.2-linux-x86_64.tgz" -C "$CLI_TMP"
    docker_cli() { "$CLI_TMP/docker/docker" "$@"; }
    trap 'rm -rf "$CLI_TMP"' EXIT
  fi
  docker_cli info >/dev/null || fail 'Docker 不可用；用 sudo 运行并确认原 Docker 服务正常。'
}
owned() {
  [[ $(docker_cli inspect -f '{{index .Config.Labels "io.opencode-demo.offline"}}' "$NAME") == 1 ]] || fail '同名容器不是本部署服务。'
  [[ $(docker_cli inspect -f '{{index .Config.Labels "io.opencode-demo.bundle"}}' "$NAME") == "$TARGET" ]] || fail '容器所属目录与目标不符。使用 inspect 显示的原目录。'
  [[ $(docker_cli inspect -f '{{.HostConfig.NetworkMode}}' "$NAME") == host ]] || fail '原服务不是 host 网络，请先核对部署结构。'
  for pair in '/app/var data/var' '/app/.opencode data/catalog'; do
    read -r dst src <<< "$pair"
    actual=$(docker_cli inspect -f '{{range .Mounts}}{{if eq .Destination "'"$dst"'"}}{{.Source}}{{end}}{{end}}' "$NAME")
    [[ "$actual" == "$TARGET/$src" ]] || fail "持久化挂载不符：$dst -> $actual"
  done
}
verify() {
  (cd "$MEDIA" && sha256sum -c DISC-SHA256SUMS --quiet)
  (cd "$MEDIA/payload" && sha256sum -c RELEASE-SHA256SUMS --quiet)
  echo '光盘副本和累计更新内容校验通过。'
}
check() {
  verify
  [[ $(uname -s)/$(uname -m) == Linux/x86_64 ]] || fail '仅支持 Linux x86_64。'
  owned
  [[ $(docker_cli info -f '{{.OSType}}/{{.Architecture}}') =~ ^linux/(x86_64|amd64)$ ]] || fail 'Docker 架构不符。'
  for p in data data/var data/catalog config config/demo.env backups; do
    [[ ! -L "$TARGET/$p" ]] || fail "请先处理符号链接：$p"
  done
  [[ -d "$TARGET/data/var" && -d "$TARGET/data/catalog" ]] || fail '缺少原持久化数据目录。'
  while IFS= read -r p; do
    [[ "$p" =~ ^[A-Za-z0-9._-]+$ && "$p" != .* && "$p" != data && "$p" != config && "$p" != backups ]] || [[ "$p" == .dockerignore ]] || fail "无效更新路径：$p"
    [[ ! -L "$TARGET/$p" ]] || fail "更新目标是符号链接：$p"
  done < "$MEDIA/MANAGED-PATHS.txt"
  # Config syntax/semantics are checked by the new runtime before init-admin/up.
  if LC_ALL=C grep -q $'\r' "$TARGET/config/demo.env"; then fail '现场配置含 CRLF，请按手册转换。'; fi
  local bytes free need
  bytes=$(du -sb "$TARGET/data/var" "$TARGET/data/catalog" "$TARGET/source" "$TARGET/images" 2>/dev/null | awk '{s+=$1} END {printf "%.0f",s}')
  need=$((bytes * 2 + 3 * 1024 * 1024 * 1024))
  free=$(df -PB1 "$TARGET" | awk 'NR==2 {print $4}')
  ((free > need)) || fail "部署分区至少需 $need 字节可用（含回退与失败现场保留）。"
  echo "部署路径：$TARGET；当前镜像：$(docker_cli inspect -f '{{.Config.Image}}' "$NAME")"
  echo "Docker 存储：$(docker_cli info -f '{{.DockerRootDir}}')；请确认其分区另有至少 3 GiB 空闲。"
  echo '预检查通过；apply 会停服并备份，完成后保持停止，等待初始化和验收。'
}
case "$ACTION" in
  verify) verify ;;
  check) init; check ;;
  apply)
    init; check
    [[ ! -f "$TARGET/RELEASE-SHA256SUMS" ]] || fail '已安装累计版本；不要重复 apply。要重做请先回退。'
    mkdir -p "$TARGET/backups"
    BACKUP=$(mktemp -d "$TARGET/backups/intranet-20261021-XXXXXXXX")
    echo "$TARGET" > "$BACKUP/TARGET"
    cp "$MEDIA/MANAGED-PATHS.txt" "$BACKUP/MANAGED-PATHS.txt"
    docker_cli inspect -f '{{.Image}}' "$NAME" > "$BACKUP/OLD-IMAGE-ID"
    docker_cli inspect -f '{{.Config.Image}}' "$NAME" > "$BACKUP/OLD-IMAGE-TAG"
    docker_cli inspect -f '{{.State.Running}}' "$NAME" > "$BACKUP/WAS-RUNNING"
    echo "本次回退目录（请记下）：$BACKUP"
    echo '保存旧镜像；随后停服备份。'
    old_id=$(cat "$BACKUP/OLD-IMAGE-ID")
    docker_cli save "$old_id" | gzip -1 > "$BACKUP/old-image.tar.gz"
    gzip -t "$BACKUP/old-image.tar.gz"
    docker_cli stop --time 40 "$NAME" >/dev/null
    # No automatic restart after a partially applied database/code upgrade.
    echo '服务已停止。任何后续错误都请保留此目录，按手册恢复，不要直接启动混合版本。'
    (cd "$TARGET" && tar -czf "$BACKUP/state.tar.gz" config/demo.env data/var data/catalog)
    : > "$BACKUP/EXISTED-PATHS.txt"
    while IFS= read -r p; do
      if [[ -e "$TARGET/$p" ]]; then echo "$p" >> "$BACKUP/EXISTED-PATHS.txt"; fi
    done < "$BACKUP/MANAGED-PATHS.txt"
    (cd "$TARGET" && tar -czf "$BACKUP/code.tar.gz" -T "$BACKUP/EXISTED-PATHS.txt")
    gzip -t "$BACKUP/state.tar.gz" "$BACKUP/code.tar.gz"
    (cd "$BACKUP" && sha256sum old-image.tar.gz state.tar.gz code.tar.gz TARGET OLD-IMAGE-ID OLD-IMAGE-TAG WAS-RUNNING MANAGED-PATHS.txt EXISTED-PATHS.txt > BACKUP-SHA256SUMS)
    touch "$BACKUP/BACKUP-COMPLETE"
    echo '备份完整；导入新镜像。'
    gzip -dc "$MEDIA/payload/images/offline-images.tar.gz" | docker_cli load
    docker_cli image inspect "$NEW_IMAGE" >/dev/null
    # Replace complete code trees so removed modules and stale assets cannot survive.
    mkdir "$BACKUP/replaced"
    while IFS= read -r p; do
      [[ ! -e "$TARGET/$p" ]] || mv "$TARGET/$p" "$BACKUP/replaced/$p"
      cp -a "$MEDIA/payload/$p" "$TARGET/$p"
    done < "$BACKUP/MANAGED-PATHS.txt"
    (cd "$TARGET" && bash deploy.sh verify)
    touch "$BACKUP/APPLY-COMPLETE"
    echo "更新文件已安装，服务仍停止。回退目录：$BACKUP"
    echo "下一步：cd '$TARGET'；按手册核对配置，init-admin，然后 up/check。"
    ;;
  rollback)
    init
    BACKUP=${3:-}
    [[ -n "$BACKUP" && -d "$BACKUP" ]] || fail '第三个参数须为本次回退目录绝对路径。'
    BACKUP=$(cd "$BACKUP" && pwd -P)
    [[ "$BACKUP" == "$TARGET/backups/intranet-20261021-"* && -f "$BACKUP/BACKUP-COMPLETE" ]] || fail '不是完整的本次升级备份。'
    (cd "$BACKUP" && sha256sum -c BACKUP-SHA256SUMS --quiet)
    [[ $(cat "$BACKUP/TARGET") == "$TARGET" ]] || fail '备份属于另一部署目录。'
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
      owned
      docker_cli stop --time 40 "$NAME" >/dev/null
      docker_cli rm "$NAME" >/dev/null
    fi
    failed=$(mktemp -d "$BACKUP/failed-state-XXXXXXXX")
    mkdir -p "$failed/data" "$failed/config"
    mv "$TARGET/data/var" "$failed/data/var"
    mv "$TARGET/data/catalog" "$failed/data/catalog"
    mv "$TARGET/config/demo.env" "$failed/config/demo.env"
    while IFS= read -r p; do
      [[ ! -e "$TARGET/$p" ]] || mv "$TARGET/$p" "$failed/$p"
    done < "$BACKUP/MANAGED-PATHS.txt"
    tar -xzf "$BACKUP/code.tar.gz" -C "$TARGET"
    tar -xzf "$BACKUP/state.tar.gz" -C "$TARGET"
    gzip -dc "$BACKUP/old-image.tar.gz" | docker_cli load
    old_tag=$(cat "$BACKUP/OLD-IMAGE-TAG")
    old_id=$(cat "$BACKUP/OLD-IMAGE-ID")
    if [[ "$old_tag" != sha256:* ]]; then docker_cli tag "$old_id" "$old_tag"; fi
    echo "已恢复升级前代码、配置、数据库及旧镜像。升级后的现场保存在：$failed"
    echo "请执行：cd '$TARGET' && sudo bash deploy.sh up && sudo bash deploy.sh check --wait 300"
    ;;
  *) echo '用法：bash scripts/intranet-update.sh verify | check /原部署目录 | apply /原部署目录 | rollback /原部署目录 /本次回退目录' ;;
esac
