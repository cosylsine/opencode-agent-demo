#!/usr/bin/env bash
# Run after producing SHA256SUMS. Creates ready-to-burn 4 GB parts; no ISO tool required.
set -euo pipefail
cd "$(dirname "$0")/.."
BUNDLE=$(pwd -P)
[[ ! -e dvd ]] || { printf 'dvd/ 已存在；请先移走旧目录再重新制作，避免混入旧分卷。\n' >&2; exit 1; }
bash deploy.sh verify
mkdir -p dvd/.parts
tar --exclude='opencode-agent-demo-deploy/dvd' \
    --exclude='opencode-agent-demo-deploy/delivery' \
    --exclude='opencode-agent-demo-deploy/data' \
    --exclude='opencode-agent-demo-deploy/backups' \
    --exclude='opencode-agent-demo-deploy/.tools' \
    --exclude='opencode-agent-demo-deploy/config/demo.env' \
    --exclude='opencode-agent-demo-deploy/config/vllm.env' \
    -C "$(dirname "$BUNDLE")" -cf - "$(basename "$BUNDLE")" \
  | split -b 4000000000 -d -a 3 - dvd/.parts/bundle.part-
(cd dvd/.parts && sha256sum bundle.part-* > DVD-SHA256SUMS)
cat > dvd/.parts/restore.sh <<'RESTORE'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
DEST=${1:-$HOME}
[[ ! -e "$DEST/opencode-agent-demo-deploy" ]] || { printf '目标已有 opencode-agent-demo-deploy，请先重命名保留，避免覆盖。\n' >&2; exit 1; }
printf '检查所有分卷；缺卷或损坏会停止……\n'
sha256sum -c DVD-SHA256SUMS
mkdir -p "$DEST"
# Names in this manifest are fixed at packaging time and checked before use.
parts=()
while read -r hash name; do
  [[ "$name" =~ ^bundle\.part-[0-9]{3}$ ]] || exit 1
  parts+=("$name")
done < DVD-SHA256SUMS
cat "${parts[@]}" | tar -xf - -C "$DEST"
chmod -R u+rwX "$DEST/opencode-agent-demo-deploy"
cd "$DEST/opencode-agent-demo-deploy"
bash deploy.sh verify
printf '还原成功：%s/opencode-agent-demo-deploy\n接下来打开 README.md，从第 2 步安装 Docker 开始。\n' "$DEST"
RESTORE
cat > dvd/.parts/RESTORE.txt <<'GUIDE'
OpenCode + 备用 vLLM 离线部署包 / DVD 还原说明

每个 disc-XX 目录刻录为一张数据 DVD（建议 UDF）；刻录目录里的文件即可。
光盘不是启动盘，也不要解压 bundle.part-*。
保留文件名，完成刻录并封盘；建议开启刻录软件的写后校验。

在内网 Linux 上：
1. mkdir -p "$HOME/offline-parts"
2. 逐张插盘，将每张盘上的全部文件复制到同一目录：
   cp /media/cdrom/* "$HOME/offline-parts/"
   /media/cdrom 要替换成实际光盘挂载位置。
   restore.sh、RESTORE.txt、DVD-SHA256SUMS 每张盘相同，覆盖即可。
3. 所有盘复制完：
   cd "$HOME/offline-parts"
   bash restore.sh
4. 还原后：
   cd "$HOME/opencode-agent-demo-deploy"
   阅读 README.md，从第 2 步开始操作。

还原文件和导入 vLLM 镜像需要较多空间，建议本地硬盘预留 100 GB（不含权重）。
默认还原到当前用户 HOME；可用 bash restore.sh /其他可写目录 指定目的地。
脚本会校验每一卷并校验还原后的文件；若缺卷、损坏，不要继续安装。
内网访问地址按 config/demo.env 配置，当前模板为 http://10.70.100.220:3000。
应用镜像已包含内网/浏览器和 B 回路补丁；新装按 README 配置和启动。
已有部署仅更新 B 回路时，优先使用 fix-tuning.md 中的单盘更新目录。
模型权重使用内网已有文件，不在盘中。
GUIDE
count=0
for part in dvd/.parts/bundle.part-*; do
  count=$((count+1))
  printf -v disc 'dvd/disc-%02d' "$count"
  mkdir -p "$disc"
  mv "$part" "$disc/"
  cp dvd/.parts/{restore.sh,RESTORE.txt,DVD-SHA256SUMS} "$disc/"
done
rm dvd/.parts/{restore.sh,RESTORE.txt,DVD-SHA256SUMS}
rmdir dvd/.parts
printf '共 %s 张 DVD，每个 disc-XX 子目录刻一张。每卷最多 4,000,000,000 字节。\n每张盘都有还原说明 RESTORE.txt。不要将整个 dvd 目录刻到一张盘。\n' "$count" > dvd/INDEX.txt
cat dvd/INDEX.txt
