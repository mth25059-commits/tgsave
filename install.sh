#!/usr/bin/env bash
# tgsave installer — Ubuntu / Debian. Run from the repo root:  bash install.sh
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME=tgsave

echo "==> system packages"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3-venv python3-dev build-essential

echo "==> python environment"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" -q install --upgrade pip setuptools wheel
"$APP/venv/bin/pip" -q install -r "$APP/requirements.txt"

if [ ! -f "$APP/config.env" ]; then
    cp "$APP/config.env.example" "$APP/config.env"
    echo "==> wrote config.env — fill it in, then run this script again"
fi

echo "==> systemd unit"
sudo tee "/etc/systemd/system/$NAME.service" >/dev/null <<UNIT
[Unit]
Description=tgsave — private Telegram saver
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$APP
ExecStart=$APP/venv/bin/python -u bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now "$NAME"
sleep 3
systemctl --no-pager --lines=20 status "$NAME" || true

cat <<TIP

done.
  logs     journalctl -u $NAME -f
  restart  sudo systemctl restart $NAME
  stop     sudo systemctl stop $NAME
TIP
