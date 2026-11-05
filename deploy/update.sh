#!/usr/bin/env bash
# 后期更新：sudo bash deploy/update.sh
# 只拉取代码、补依赖、重启服务；data/ 里的数据库不受影响
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/lazy-review}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash deploy/update.sh" >&2
  exit 1
fi

cd "$APP_DIR"

if [ -n "$(git status --porcelain)" ]; then
  echo "服务器上有未提交的本地改动，已中止更新（避免覆盖）：" >&2
  git status --short >&2
  exit 1
fi

echo "拉取最新代码..."
git fetch --all --prune
git pull --ff-only

echo "更新依赖..."
.venv/bin/pip install -q -r requirements.txt

echo "重启服务..."
systemctl restart lazy-review
sleep 2
systemctl --no-pager --full status lazy-review | head -n 12 || true

echo ""
echo "更新完成，当前版本：$(git log -1 --format='%h %s')"
