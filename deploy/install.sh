#!/usr/bin/env bash
# Installation / mise à jour sur Debian 12+ ou Ubuntu 22.04+ (container LXC, VM ou serveur).
# Installation / update on Debian 12+ or Ubuntu 22.04+ (LXC container, VM or server).
#
#   sudo bash deploy/install.sh
#
# - code dans /opt/childs-rewards, données dans /var/lib/childs-rewards
# - service systemd « childs-rewards » écoutant sur 127.0.0.1:8000 (ou CR_BIND / CR_PORT)
# - sauvegarde quotidienne de la base dans /var/backups/childs-rewards
set -euo pipefail

APP_DIR=/opt/childs-rewards
DATA_DIR=/var/lib/childs-rewards
BACKUP_DIR=/var/backups/childs-rewards
APP_USER=childs-rewards
BIND="${CR_BIND:-127.0.0.1}"
PORT="${CR_PORT:-8000}"
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "Lancer avec sudo / run with sudo" >&2
  exit 1
fi

apt-get update -qq
apt-get install -y -qq python3 python3-venv rsync >/dev/null

id -u "$APP_USER" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
mkdir -p "$APP_DIR" "$DATA_DIR" "$BACKUP_DIR"

rsync -a --delete --exclude '.git' --exclude '.venv' --exclude 'data' --exclude 'docs' \
  "$SRC_DIR"/ "$APP_DIR"/
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

chown -R "$APP_USER:$APP_USER" "$DATA_DIR" "$BACKUP_DIR"
chmod 700 "$DATA_DIR" "$BACKUP_DIR"

sed -e "s#@BIND@#$BIND#; s#@PORT@#$PORT#" "$APP_DIR/deploy/childs-rewards.service" \
  > /etc/systemd/system/childs-rewards.service
install -m 644 "$APP_DIR/deploy/childs-rewards-backup.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/childs-rewards-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now childs-rewards.service childs-rewards-backup.timer
systemctl restart childs-rewards.service

echo
echo "✅ Points Famille tourne sur http://$BIND:$PORT  (systemctl status childs-rewards)"
echo "   Étape suivante / next step : reverse proxy HTTPS (voir deploy/nginx.conf)"
