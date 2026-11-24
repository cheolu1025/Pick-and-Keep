#!/usr/bin/env bash
# Create or change the site login (HTTP Basic Auth) on EC2.
#   bash deploy/set_password.sh pickandkeep        # asks for the password (not shown)
# Stored hashed in /etc/nginx/pickandkeep.htpasswd (not in Git). Same id again = new password.
set -euo pipefail
ID="${1:?usage: set_password.sh <id>}"
if [ -z "${PK_PASSWORD:-}" ]; then
    read -r -s -p "password for $ID: " PK_PASSWORD; echo
fi
HASH=$(printf '%s' "$PK_PASSWORD" | openssl passwd -apr1 -stdin)
FILE=/etc/nginx/pickandkeep.htpasswd
sudo touch "$FILE"
sudo sed -i "/^$ID:/d" "$FILE"
echo "$ID:$HASH" | sudo tee -a "$FILE" >/dev/null
sudo chown root:www-data "$FILE" && sudo chmod 640 "$FILE"
command -v nginx >/dev/null && sudo systemctl reload nginx 2>/dev/null || true
echo "saved login for $ID"
