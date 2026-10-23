#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
BUNDLE=$(pwd -P)
IMAGE=opencode-demo-vllm:0.31.0-cu129
NAME=opencode-demo-vllm
ACTION=${1:-help}
if (($#)); then shift; fi
fail() { printf '错误：%s\n' "$*" >&2; exit 1; }
docker_cli() {
  if command -v docker >/dev/null; then docker "$@";
  elif [[ -x .tools/docker/docker ]]; then "$BUNDLE/.tools/docker/docker" "$@";
  elif [[ -x /opt/opencode-demo-docker/28.5.2/docker ]]; then /opt/opencode-demo-docker/28.5.2/docker "$@";
  else fail '先按 README 安装 Docker 并 load 应用镜像。'; fi
}
daemon() { docker_cli info >/dev/null 2>&1 || fail 'Docker 不可用；请用 sudo 并检查 Docker 服务。'; }
image_ready() { docker_cli image inspect "$IMAGE" >/dev/null 2>&1 || fail '先 sudo bash vllm.sh load'; }
compat() {
  [[ -d vllm/compat/torchcodec ]] || fail '缺少随包的 vllm/compat；请完整复制部署包。'
  sha256sum -c vllm/COMPAT-SHA256SUMS --quiet
  COMPAT_ARGS=(-e PYTHONPATH=/opt/opencode-vllm-compat
    -v "$BUNDLE/vllm/compat:/opt/opencode-vllm-compat:ro,Z")
}
owned() {
  [[ $(docker_cli inspect --format '{{index .Config.Labels "io.opencode-demo.vllm"}}' "$NAME") == 1 ]] || fail '同名容器不属于本包。'
  [[ $(docker_cli inspect --format '{{index .Config.Labels "io.opencode-demo.bundle"}}' "$NAME") == "$BUNDLE" ]] || fail '同名容器属于另一部署目录。'
}
config() {
  [[ -f config/vllm.env ]] || fail '先 bash vllm.sh init 并编辑 config/vllm.env。'
  # Parse as data. Never execute a configuration file with source/eval.
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" != *$'\r'* && "$line" == *=* ]] || fail '配置格式错误，使用 LF 换行和 KEY=value。'
    key=${line%%=*}; value=${line#*=}
    case "$key" in
      MODEL_DIR|GPU_IDS|TENSOR_PARALLEL_SIZE|SERVE_PORT|SERVED_MODEL_NAME|MAX_MODEL_LEN|GPU_MEMORY_UTILIZATION|MAX_NUM_SEQS|API_KEY|VLLM_ENABLE_CUDA_COMPATIBILITY|ENFORCE_EAGER)
        [[ "$value" != \"* && "$value" != \'* ]] || fail '配置值外不要加引号。'
        printf -v "$key" '%s' "$value" ;;
      *) fail "未知配置项：$key" ;;
    esac
  done < config/vllm.env
  [[ ${GPU_IDS:-} =~ ^[0-9]+(,[0-9]+)*$ ]] || fail 'GPU_IDS 示例：0,1'
  [[ ${TENSOR_PARALLEL_SIZE:-} =~ ^[1-8]$ ]] || fail 'TENSOR_PARALLEL_SIZE 应为 1..8。'
  IFS=, read -r -a gpu_list <<< "$GPU_IDS"
  [[ ${#gpu_list[@]} == "$TENSOR_PARALLEL_SIZE" ]] || fail 'GPU 数量必须等于 TENSOR_PARALLEL_SIZE。'
  GPU_ARGS=()
  declare -A seen=()
  for gpu in "${gpu_list[@]}"; do
    [[ -z ${seen[$gpu]:-} ]] || fail 'GPU_IDS 不能重复。'
    seen[$gpu]=1
    GPU_ARGS+=(--device "opencode-demo.local/gpu=$gpu")
  done
  [[ ${SERVE_PORT:-} =~ ^[0-9]+$ ]] && ((SERVE_PORT >= 1024 && SERVE_PORT <= 65535)) || fail 'SERVE_PORT 无效。'
  [[ ${MAX_MODEL_LEN:-} =~ ^[1-9][0-9]*$ && ${MAX_NUM_SEQS:-} =~ ^[1-9][0-9]*$ ]] || fail '上下文和并发数必须是正整数。'
  [[ ${GPU_MEMORY_UTILIZATION:-} =~ ^0\.[0-9]+$ ]] || fail 'GPU_MEMORY_UTILIZATION 应为 0 到 1 之间的小数。'
  [[ ${ENFORCE_EAGER:-} =~ ^[01]$ && ${VLLM_ENABLE_CUDA_COMPATIBILITY:-} =~ ^[01]$ ]] || fail '两个开关请填 0 或 1。'
  [[ -n ${SERVED_MODEL_NAME:-} && -n ${API_KEY:-} ]] || fail '模型服务名和 API_KEY 不能为空；无自定义密钥时用 EMPTY。'
}
gpu_check() {
  daemon; image_ready; config; compat
  [[ -f /etc/cdi/opencode-demo.yaml ]] || fail '先 sudo bash scripts/install-gpu-support.sh'
  # Native CDI and explicit runc avoid injecting GPUs twice on hosts with an existing runtime.
  docker_cli run -i --rm --pull never --network none --runtime runc "${GPU_ARGS[@]}" \
    "${COMPAT_ARGS[@]}" \
    -e NVIDIA_VISIBLE_DEVICES=void \
    -e EXPECTED_GPU_COUNT="${#gpu_list[@]}" \
    -e VLLM_ENABLE_CUDA_COMPATIBILITY="$VLLM_ENABLE_CUDA_COMPATIBILITY" \
    --entrypoint /bin/bash "$IMAGE" -c \
    'set -e; if [ "$VLLM_ENABLE_CUDA_COMPATIBILITY" = 1 ] && [ -d /usr/local/cuda/compat ]; then export LD_LIBRARY_PATH=/usr/local/cuda/compat:${LD_LIBRARY_PATH:-}; fi; nvidia-smi; python3 -' < scripts/check-gpu.py
}
case "$ACTION" in
  init)
    if [[ ! -e config/vllm.env ]]; then cp config/vllm.env.example config/vllm.env; chmod 600 config/vllm.env; fi
    printf '编辑 config/vllm.env，填写本地模型目录、空闲 GPU 和端口。\n' ;;
  load)
    daemon; sha256sum -c vllm/SHA256SUMS --quiet
    gzip -dc vllm/vllm-image.tar.gz | docker_cli load
    image_ready ;;
  gpu-check) gpu_check ;;
  up)
    daemon; image_ready; config
    [[ -f "$MODEL_DIR/config.json" ]] || fail 'MODEL_DIR 必须是包含 config.json 的完整本地模型目录。'
    [[ "$MODEL_DIR" == /* ]] || fail 'MODEL_DIR 必须用绝对路径。'
    [[ "$MODEL_DIR" != *:* ]] || fail '模型路径不能含冒号。'
    if docker_cli container inspect "$NAME" >/dev/null 2>&1; then
      owned
      fail '本包 vLLM 容器已存在。修改配置需先 sudo bash vllm.sh down，再 up；不会自动停止模型服务。'
    fi
    # Use the already bundled app Python to check the host port without requiring host Python.
    docker_cli run --rm --pull never --network host --entrypoint python \
      opencode-agent-demo:20261013-amd64 -c \
      'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(("127.0.0.1",int(sys.argv[1]))); print("vLLM port available")' "$SERVE_PORT"
    gpu_check
    mkdir -p data/vllm-cache
    ARGS=(/model --host 127.0.0.1 --port "$SERVE_PORT"
      --served-model-name "$SERVED_MODEL_NAME" --api-key "$API_KEY"
      --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" --max-model-len "$MAX_MODEL_LEN"
      --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION" --max-num-seqs "$MAX_NUM_SEQS"
      --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser qwen3
      --default-chat-template-kwargs '{"enable_thinking":false}' --attention-backend FLASH_ATTN
      --language-model-only)
    if [[ "$ENFORCE_EAGER" == 1 ]]; then ARGS+=(--enforce-eager); fi
    docker_cli run -d --name "$NAME" --pull never --network host --runtime runc \
      --restart unless-stopped --stop-timeout 60 --shm-size 16g \
      --label io.opencode-demo.vllm=1 --label "io.opencode-demo.bundle=$BUNDLE" \
      --log-driver json-file --log-opt max-size=20m --log-opt max-file=3 \
      "${GPU_ARGS[@]}" -e NVIDIA_VISIBLE_DEVICES=void \
      "${COMPAT_ARGS[@]}" \
      -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 -e HF_DATASETS_OFFLINE=1 \
      -e HF_HUB_DISABLE_TELEMETRY=1 -e VLLM_NO_USAGE_STATS=1 -e DO_NOT_TRACK=1 \
      -e VLLM_USE_FLASHINFER_SAMPLER=0 \
      -e VLLM_ENABLE_CUDA_COMPATIBILITY="$VLLM_ENABLE_CUDA_COMPATIBILITY" \
      -e NO_PROXY='*' -e no_proxy='*' -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= \
      -e HF_HOME=/cache/huggingface -e VLLM_CACHE_ROOT=/cache/vllm -e TRITON_CACHE_DIR=/cache/triton \
      -v "$MODEL_DIR:/model:ro" -v "$BUNDLE/data/vllm-cache:/cache:Z" \
      --entrypoint /bin/bash "$IMAGE" -c \
      'set -e; if [ "$VLLM_ENABLE_CUDA_COMPATIBILITY" = 1 ] && [ -d /usr/local/cuda/compat ]; then export LD_LIBRARY_PATH=/usr/local/cuda/compat:${LD_LIBRARY_PATH:-}; fi; exec vllm serve "$@"' \
      offline-vllm "${ARGS[@]}"
    printf '正在加载模型；sudo bash vllm.sh logs -f 查看日志。加载完成后执行应用的 probe-model。\n'
    ;;
  logs) daemon; owned; docker_cli logs --tail 150 "$@" "$NAME" ;;
  status) daemon; owned; docker_cli inspect --format '{{.State.Status}} restarts={{.RestartCount}}' "$NAME" ;;
  stop) daemon; owned; docker_cli stop --time 60 "$NAME" ;;
  start) daemon; owned; docker_cli start "$NAME" ;;
  down) daemon; owned; docker_cli stop --time 60 "$NAME"; docker_cli rm "$NAME" ;;
  *) printf '备用 vLLM：init | load | gpu-check | up | logs [-f] | status | stop | start | down\n请先阅读 vllm/README.md。\n' ;;
esac
