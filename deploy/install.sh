#!/usr/bin/env bash
# Installation / mise à jour sur Debian 12+ ou Ubuntu 22.04+ (container LXC, VM ou serveur).
# Installation / update on Debian 12+ or Ubuntu 22.04+ (LXC container, VM or server).
#
#   sudo bash deploy/install.sh                       # installation ou mise à jour
#   sudo CR_BIND=0.0.0.0 bash deploy/install.sh       # reverse proxy sur une autre machine
#
# - code dans /opt/childs-rewards (remplacé à chaque mise à jour)
# - données dans /var/lib/childs-rewards (jamais touchées par ce script)
# - réglages du service dans /etc/childs-rewards.env : créés à la première installation (ou repris d'une
#   installation précédente), puis conservés ; seules les variables passées à la commande les modifient
# - sauvegarde quotidienne de la base dans /var/backups/childs-rewards
set -euo pipefail

APP_DIR=/opt/childs-rewards
DATA_DIR=/var/lib/childs-rewards
BACKUP_DIR=/var/backups/childs-rewards
ENV_FILE=/etc/childs-rewards.env
SERVICE_FILE=/etc/systemd/system/childs-rewards.service
APP_USER=childs-rewards
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

# --- Réglages du service : conservés d'une mise à jour à l'autre ---------------------------------
set_var() {  # set_var NOM valeur : remplace ou ajoute la ligne NOM=valeur dans $ENV_FILE
  if grep -q "^$1=" "$ENV_FILE"; then sed -i "s#^$1=.*#$1=$2#" "$ENV_FILE"; else echo "$1=$2" >> "$ENV_FILE"; fi
}

if [[ ! -f "$ENV_FILE" ]]; then
  # Reprise d'une ancienne installation (réglages écrits directement dans le fichier du service)
  old_bind="" old_port="" old_fwd=""
  if [[ -f "$SERVICE_FILE" ]]; then
    old_bind="$(grep -oP -- '--host \K[^ $]+' "$SERVICE_FILE" | grep -v '^\$' || true)"
    old_port="$(grep -oP -- '--port \K[0-9]+' "$SERVICE_FILE" || true)"
    old_fwd="$(grep -oP -- '--forwarded-allow-ips \K[^ ]+' "$SERVICE_FILE" | grep -v '^\$' || true)"
  fi
  cat > "$ENV_FILE" <<EOF
# Réglages de Points Famille (conservés lors des mises à jour).
# Après modification : sudo systemctl restart childs-rewards
CR_BIND=${CR_BIND:-${old_bind:-127.0.0.1}}
CR_PORT=${CR_PORT:-${old_port:-8000}}
# IP du reverse proxy autorisée à transmettre l'IP réelle et le protocole (HTTPS) des visiteurs
CR_FORWARDED_ALLOW_IPS=${CR_FORWARDED_ALLOW_IPS:-${old_fwd:-127.0.0.1}}
CR_COOKIE_SECURE=${CR_COOKIE_SECURE:-auto}
CR_TIMEZONE=${CR_TIMEZONE:-Europe/Paris}
EOF
  chmod 644 "$ENV_FILE"
else
  for var in CR_BIND CR_PORT CR_FORWARDED_ALLOW_IPS CR_COOKIE_SECURE CR_TIMEZONE; do
    if [[ -n "${!var:-}" ]]; then set_var "$var" "${!var}"; fi
  done
fi

install -m 644 "$APP_DIR/deploy/childs-rewards.service" "$SERVICE_FILE"
install -m 644 "$APP_DIR/deploy/childs-rewards-backup.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/childs-rewards-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now childs-rewards.service childs-rewards-backup.timer
systemctl restart childs-rewards.service

# shellcheck disable=SC1090
source "$ENV_FILE"
echo
echo "✅ Points Famille tourne sur http://$CR_BIND:$CR_PORT  (systemctl status childs-rewards)"
echo "   Réglages : $ENV_FILE"
echo "   Reverse proxy HTTPS : voir deploy/nginx.conf / see deploy/nginx.conf"
