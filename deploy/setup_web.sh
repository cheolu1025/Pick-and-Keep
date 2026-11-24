#!/usr/bin/env bash
# Web deploy on EC2: nginx (port 80, password) -> gunicorn (systemd) -> app.py
# Run on EC2:  cd ~/Pick-and-Keep && bash deploy/setup_web.sh
# Safe to run again (e.g. after changing the config files in deploy/).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"       # folder with the config files
APP=/home/ubuntu/Pick-and-Keep

echo "== 1. nginx"
command -v nginx >/dev/null || { sudo apt-get update -qq && sudo apt-get install -y -qq nginx; }

echo "== 2. FLASK_SECRET_KEY in .env (value is generated here and never printed)"
if ! grep -q '^FLASK_SECRET_KEY=.' "$APP/.env"; then
    [ -n "$(tail -c1 "$APP/.env")" ] && echo >> "$APP/.env"
    sed -i '/^FLASK_SECRET_KEY=/d' "$APP/.env"
    echo "FLASK_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> "$APP/.env"
    echo "   added"
else
    echo "   already set"
fi

echo "== 3. gunicorn service (systemd)"
sudo cp "$SRC/pickandkeep.service" /etc/systemd/system/pickandkeep.service
sudo systemctl daemon-reload
sudo systemctl enable --now pickandkeep
sudo systemctl restart pickandkeep

echo "== 4. nginx site"
sudo cp "$SRC/nginx_pickandkeep.conf" /etc/nginx/sites-available/pickandkeep
sudo ln -sf /etc/nginx/sites-available/pickandkeep /etc/nginx/sites-enabled/pickandkeep
sudo rm -f /etc/nginx/sites-enabled/default
if [ ! -s /etc/nginx/pickandkeep.htpasswd ]; then
    echo "   !! no password file yet: run  bash deploy/set_password.sh <id>  then this script again"
    exit 1
fi
sudo nginx -t
sudo systemctl reload nginx

echo "== done: $(systemctl is-active pickandkeep) / nginx $(systemctl is-active nginx)"
