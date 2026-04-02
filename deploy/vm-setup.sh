#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# VM bootstrap script — runs once on first boot via Azure cloud-init
# Installs Python, deploys bot code, configures systemd auto-restart
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="/opt/scalping-bot"
APP_USER="botuser"

echo ">>> [1/5] System packages"
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv python3-pip git unzip > /dev/null

echo ">>> [2/5] Create bot user & directories"
useradd -r -m -s /bin/bash "$APP_USER" 2>/dev/null || true
mkdir -p "$APP_DIR"/{data,logs,reports}
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo ">>> [3/5] Deploy bot code"
# Code is uploaded by deploy script into /tmp/bot-code.tar.gz
if [ -f /tmp/bot-code.tar.gz ]; then
    tar -xzf /tmp/bot-code.tar.gz -C "$APP_DIR"
    rm -f /tmp/bot-code.tar.gz
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo ">>> [4/5] Python venv & dependencies"
sudo -u "$APP_USER" python3.12 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

echo ">>> [5/5] Systemd service"
cat > /etc/systemd/system/scalping-bot.service << 'EOF'
[Unit]
Description=ETH/USDT Scalping Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/opt/scalping-bot
EnvironmentFile=/opt/scalping-bot/.env
ExecStart=/opt/scalping-bot/.venv/bin/python src/main.py --mode live
Restart=always
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/scalping-bot/data /opt/scalping-bot/logs /opt/scalping-bot/reports
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable scalping-bot.service

echo ">>> Setup complete. Start with: sudo systemctl start scalping-bot"
