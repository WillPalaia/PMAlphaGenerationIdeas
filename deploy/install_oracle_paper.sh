#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/WillPalaia/PMAlphaGenerationIdeas.git}"
INSTALL_DIR="/opt/pm-alpha"
DATA_DIR="/var/lib/pm-alpha"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/install_oracle_paper.sh"
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates git python3 python3-venv python3-pip

if ! id pmalpha >/dev/null 2>&1; then
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin pmalpha
fi

if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  rm -rf "$INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  git -C "$INSTALL_DIR" pull --ff-only
fi

install -d -o pmalpha -g pmalpha "$DATA_DIR"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"
install -o root -g root -m 0644 "$INSTALL_DIR/deploy/pm-alpha-paper.service" \
  /etc/systemd/system/pm-alpha-paper.service

echo "Edit /etc/systemd/system/pm-alpha-paper.service and replace KALSHI_TICKER_1/2."
echo "Then run: systemctl daemon-reload && systemctl enable --now pm-alpha-paper"
