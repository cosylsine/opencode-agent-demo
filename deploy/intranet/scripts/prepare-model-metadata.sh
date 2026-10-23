#!/usr/bin/env bash
# Optional: add missing metadata for the original Qwen3.8-27B checkpoint.
set -euo pipefail
cd "$(dirname "$0")/.."
target=${1:-}
[[ "$target" == /* && -d "$target" ]] || { printf '用法：sudo bash scripts/prepare-model-metadata.sh /内网模型绝对路径\n' >&2; exit 1; }
[[ -w "$target" ]] || { printf '模型目录不可写；请用 sudo，或复制到可写的本地目录。\n' >&2; exit 1; }
sha256sum -c vllm/MODEL-METADATA-SHA256SUMS --quiet
printf '仅补齐官方原版 Qwen3.8-27B 的配置/分词器；权重需由内网提供。\n'
for source in vllm/model-metadata/*; do
  dest="$target/$(basename "$source")"
  if [[ -e "$dest" || -L "$dest" ]]; then
    printf '保留已有文件：%s\n' "$(basename "$source")"
  else
    cp "$source" "$dest"
    printf '已补齐：%s\n' "$(basename "$source")"
  fi
done
printf '配置文件已检查；请确认权重分片与本目录索引一致，再按 vllm/README.md 启动。\n'
