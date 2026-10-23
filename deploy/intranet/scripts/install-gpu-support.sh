#!/usr/bin/env bash
# CDI uses NVIDIA's two self-contained tools, avoiding distro-specific package dependencies.
set -euo pipefail
cd "$(dirname "$0")/.."
fail() { printf '错误：%s\n' "$*" >&2; exit 1; }
[[ $(id -u) == 0 ]] || fail '请用 sudo 运行本脚本。'
[[ $(uname -s)/$(uname -m) == Linux/x86_64 ]] || fail '仅支持 Linux x86_64。'
command -v nvidia-smi >/dev/null || fail '未检测到 NVIDIA 驱动；请先由管理员确认宿主机 GPU 驱动正常。'
nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv
sha256sum -c runtime/nvidia-cdi/SHA256SUMS --quiet
INSTALL=/opt/opencode-demo-nvidia/1.20.1
mkdir -p "$INSTALL" /etc/cdi
install -m 755 runtime/nvidia-cdi/nvidia-ctk runtime/nvidia-cdi/nvidia-cdi-hook "$INSTALL/"
cat > "$INSTALL/refresh.sh" <<'REFRESH'
#!/usr/bin/env bash
set -euo pipefail
INSTALL=/opt/opencode-demo-nvidia/1.20.1
if command -v nvidia-modprobe >/dev/null; then nvidia-modprobe -u -c=0; fi
if [[ ! -c /dev/nvidia-uvm ]]; then
  "$INSTALL/nvidia-ctk" system create-device-nodes --load-kernel-modules --control-devices
fi
[[ -c /dev/nvidia-uvm ]] || { printf '缺少 /dev/nvidia-uvm；请检查宿主机 NVIDIA UVM 模块。\n' >&2; exit 1; }
# Custom CDI vendor prevents collisions with an existing NVIDIA toolkit installation.
"$INSTALL/nvidia-ctk" cdi generate --vendor=opencode-demo.local \
  --nvidia-cdi-hook-path="$INSTALL/nvidia-cdi-hook" \
  --output=/etc/cdi/opencode-demo.yaml
REFRESH
chmod 755 "$INSTALL/refresh.sh"
bash "$INSTALL/refresh.sh"
if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
  unit=/etc/systemd/system/opencode-demo-gpu-refresh.service
  if [[ -e "$unit" ]] && ! grep -q 'Managed by opencode-agent-demo-deploy' "$unit"; then
    fail '同名服务不属于本包，未覆盖。'
  fi
  cat > "$unit" <<'UNIT'
# Managed by opencode-agent-demo-deploy
[Unit]
Description=Refresh offline demo NVIDIA CDI devices
After=systemd-modules-load.service nvidia-persistenced.service nvidia-fabricmanager.service
Before=opencode-demo-docker.service docker.service
[Service]
Type=oneshot
ExecStart=/opt/opencode-demo-nvidia/1.20.1/refresh.sh
[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable opencode-demo-gpu-refresh.service
fi
"$INSTALL/nvidia-ctk" cdi list
printf 'GPU 容器支持已配置；未替换驱动，也未修改其他 Docker runtime。\n'
printf '驱动或 MIG 配置变更后重新运行本脚本。下一步执行 bash vllm.sh load 和 bash vllm.sh gpu-check。\n'
