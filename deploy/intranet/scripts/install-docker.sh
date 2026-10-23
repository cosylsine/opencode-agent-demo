#!/usr/bin/env bash
# Fresh Linux host installer; never overwrites an existing Docker installation.
set -euo pipefail
cd "$(dirname "$0")/.."
BUNDLE=$(pwd -P)
fail() { printf '错误：%s\n' "$*" >&2; exit 1; }
[[ $(id -u) == 0 ]] || fail '请运行 sudo bash scripts/install-docker.sh，或使用 root 账户。'
[[ $(uname -s)/$(uname -m) == Linux/x86_64 ]] || fail '此安装包只适用 Linux x86_64。'
command -v systemctl >/dev/null || fail '此脚本需要 systemd。请使用 README 的无 systemd 手动启动步骤。'
[[ -d /run/systemd/system ]] || fail 'systemd 尚未运行；参见 README。'
[[ -d /sys/fs/cgroup ]] || fail '系统缺少 cgroup；需要完整 Linux 主机或虚拟机。'
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
  printf '现有 Docker 可用，直接复用；未修改安装或 daemon 配置。\n'
  exit 0
fi
if systemctl is-active --quiet opencode-demo-docker.service; then
  printf '随包 Docker 已运行。\n'; exit 0
fi
if command -v dockerd >/dev/null || [[ -S /var/run/docker.sock ]]; then
  fail '发现已有 Docker 安装但服务不可用。先 sudo systemctl start docker，再运行 sudo docker info；未覆盖已有安装。'
fi
sha256sum -c runtime/SHA256SUMS --quiet
INSTALL=/opt/opencode-demo-docker/28.5.2
DATA_ROOT=${DEMO_DOCKER_DATA_ROOT:-/var/lib/opencode-demo-docker}
[[ "$DATA_ROOT" =~ ^/[A-Za-z0-9_./-]+$ && "$DATA_ROOT" != / ]] || fail 'DEMO_DOCKER_DATA_ROOT 请用绝对路径，仅含字母、数字、下划线、点、横线和斜杠。'
[[ "/$DATA_ROOT/" != */../* ]] || fail '数据目录路径不能含 ..。'
mkdir -p "$DATA_ROOT"
mkdir -p "$INSTALL"
tar -xzf runtime/docker-28.5.2-linux-x86_64.tgz -C "$INSTALL" --strip-components=1
chmod 755 "$INSTALL"/*
if ! command -v docker >/dev/null; then
  [[ ! -e /usr/local/bin/docker ]] || fail '/usr/local/bin/docker 已存在且不可执行，请检查。'
  ln -s "$INSTALL/docker" /usr/local/bin/docker
fi
UNIT=/etc/systemd/system/opencode-demo-docker.service
if [[ -e "$UNIT" ]] && ! grep -q 'Managed by opencode-agent-demo-deploy' "$UNIT"; then
  fail '同名 systemd 服务已存在，未覆盖。'
fi
cat > "$UNIT" <<'UNIT'
# Managed by opencode-agent-demo-deploy
[Unit]
Description=Offline Docker for OpenCode demo (host networking only)
After=network.target
RequiresMountsFor=@DATA_ROOT@

[Service]
Type=notify
Environment=PATH=/opt/opencode-demo-docker/28.5.2:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/opt/opencode-demo-docker/28.5.2/dockerd --config-file=/opt/opencode-demo-docker/28.5.2/daemon.json --host=unix:///var/run/docker.sock --data-root=@DATA_ROOT@ --exec-root=/run/opencode-demo-docker --pidfile=/run/opencode-demo-docker.pid --bridge=none --iptables=false --ip-forward=false --ip-masq=false --storage-driver=overlay2
Restart=on-failure
RestartSec=3
TimeoutStartSec=90
TimeoutStopSec=60
Delegate=yes
KillMode=process
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity

[Install]
WantedBy=multi-user.target
UNIT
sed -i "s|@DATA_ROOT@|$DATA_ROOT|g" "$UNIT"
printf '{}\n' > "$INSTALL/daemon.json"
systemctl daemon-reload
systemctl enable --now opencode-demo-docker.service
if ! "$INSTALL/docker" info >/dev/null 2>&1; then
  journalctl -u opencode-demo-docker.service -n 40 --no-pager
  fail 'Docker 启动失败；请查看上方日志及 README 的故障排查。'
fi
printf 'Docker 28.5.2 已离线安装并设置开机启动。\n'
printf '镜像存储目录：%s\n' "$DATA_ROOT"
printf '此 Docker 按本项目 host 网络配置，不建立网桥、不需要 iptables。\n'
