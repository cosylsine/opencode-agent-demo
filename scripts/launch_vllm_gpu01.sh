#!/usr/bin/env bash
set -euo pipefail

# Override VLLM_BIN to use a different vLLM environment.
VLLM_BIN=${VLLM_BIN:-/data9/whp/.conda/envs/openhands-py2c/bin/vllm}
MODEL_DIR=/data9/HF-model/Qwen3.6-35B-A3B
MODEL_ID=qwen3.6-35b-a3b
PORT=8000

if [[ ! -f "$MODEL_DIR/config.json" ]]; then
  echo "Model config not found: $MODEL_DIR/config.json" >&2
  exit 1
fi

for utility in ss nvidia-smi; do
  if ! command -v "$utility" >/dev/null 2>&1; then
    echo "Required command not found: $utility" >&2
    exit 1
  fi
done

if ! listeners=$(ss -H -ltn "sport = :$PORT"); then
  echo "Cannot check port $PORT; refusing to start." >&2
  exit 1
fi
if [[ -n "$listeners" ]]; then
  echo "Port $PORT is already listening; stop the existing service before starting this one." >&2
  exit 1
fi

for gpu in 0 1; do
  if ! processes=$(nvidia-smi --id="$gpu" --query-compute-apps=pid --format=csv,noheader,nounits); then
    echo "Cannot check GPU $gpu; refusing to start." >&2
    exit 1
  fi
  if [[ -n "$processes" ]]; then
    echo "GPU $gpu still has compute processes ($processes); refusing to start." >&2
    exit 1
  fi
done

if ! command -v "$VLLM_BIN" >/dev/null 2>&1; then
  echo "vLLM executable not found: $VLLM_BIN; set VLLM_BIN to your conda environment's bin/vllm." >&2
  exit 1
fi

if [[ "$VLLM_BIN" == */bin/vllm ]]; then
  VLLM_ENV=${VLLM_BIN%/bin/vllm}
  if [[ -d "$VLLM_ENV/lib" ]]; then
    for library in libcrypto.so.3 libssl.so.3; do
      if [[ -e "$VLLM_ENV/lib/$library" && ! -r "$VLLM_ENV/lib/$library" ]]; then
        echo "Cannot read $VLLM_ENV/lib/$library; use a vLLM environment accessible to this user." >&2
        exit 1
      fi
    done
    export LD_LIBRARY_PATH="$VLLM_ENV/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
  export PATH="$VLLM_ENV/bin:$PATH"
fi

if ! command -v ninja >/dev/null 2>&1; then
  echo "ninja is not available on PATH; vLLM needs it to compile FlashInfer kernels." >&2
  exit 1
fi

export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES=0,1
echo "Starting $MODEL_ID on GPUs $CUDA_VISIBLE_DEVICES at http://127.0.0.1:$PORT/v1" >&2
exec "$VLLM_BIN" serve "$MODEL_DIR" \
  --served-model-name "$MODEL_ID" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --tensor-parallel-size 2 \
  --max-model-len 65536 \
  --gpu-memory-utilization 0.90 \
  --enable-prefix-caching \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --default-chat-template-kwargs '{"enable_thinking": false}'
