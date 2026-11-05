#!/usr/bin/env bash
# 首次部署：sudo bash deploy/install.sh
# 可覆盖变量：APP_DIR（默认 /opt/lazy-review）、REPO（仓库地址）、SERVICE_USER
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/lazy-review}"
REPO="${REPO:-https://github.com/yihaochen0428/lazy-review.git}"
SERVICE_USER="${SERVICE_USER:-lazy}"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash deploy/install.sh" >&2
  exit 1
fi

PIP_INDEX="${PIP_INDEX:-https://mirrors.aliyun.com/pypi/simple/}"

# 选择 Python：优先 3.12/3.11/3.10，系统自带的 3.6 不够用
PY_BIN=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 \
     && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PY_BIN="$candidate"
    break
  fi
done

# 阿里云 Linux 3 / RHEL 8 默认只有 Python 3.6，源里带 python3.11
if [ -z "$PY_BIN" ]; then
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y python3.11 python3.11-pip
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y python3.11 python3.11-venv
  fi
  command -v python3.11 >/dev/null 2>&1 && PY_BIN=python3.11
fi
[ -n "$PY_BIN" ] || { echo "需要 Python 3.10+，请先安装" >&2; exit 1; }
echo "使用 $($PY_BIN -V)"

# 补装 git / venv（Debian 系与 RHEL 系）
if ! command -v git >/dev/null 2>&1 || ! "$PY_BIN" -m venv --help >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y git python3-venv
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y git python3-pip
  elif command -v yum >/dev/null 2>&1; then
    yum install -y git
  else
    echo "请手动安装 git 与 python3-venv" >&2
    exit 1
  fi
fi

# 运行账号
getent group "$SERVICE_USER" >/dev/null 2>&1 || groupadd --system "$SERVICE_USER"
id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --gid "$SERVICE_USER" \
  --home-dir "$APP_DIR" --no-create-home --shell /sbin/nologin "$SERVICE_USER"

# 代码（已有仓库则直接更新，保留 data/ 里的数据库）
if [ -d "$APP_DIR/.git" ]; then
  echo "检测到已有代码，执行 git pull"
  git -C "$APP_DIR" pull --ff-only
else
  git clone "$REPO" "$APP_DIR"
fi

# 依赖
"$PY_BIN" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip -i "$PIP_INDEX"
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt" -i "$PIP_INDEX"

# 代码保持 root 所有（方便 git pull 更新），只有 data/ 交给运行账号
mkdir -p "$APP_DIR/data"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/data"
chmod -R a+rX "$APP_DIR"

# 环境变量（大模型 Key 等，可选）
if [ ! -f /etc/lazy-review.env ]; then
  install -m 640 -o root -g "$SERVICE_USER" \
    "$APP_DIR/deploy/lazy-review.env.example" /etc/lazy-review.env
  echo "已生成 /etc/lazy-review.env，可填入 LAZY_LLM_API_KEY"
fi

# systemd 守护
install -m 644 "$APP_DIR/deploy/lazy-review.service" /etc/systemd/system/lazy-review.service
systemctl daemon-reload
systemctl enable lazy-review
systemctl restart lazy-review

sleep 2
systemctl --no-pager --full status lazy-review || true
echo ""
echo "部署完成：http://<服务器公网IP>:8000"
echo "记得在云控制台安全组放行 8000 端口"
