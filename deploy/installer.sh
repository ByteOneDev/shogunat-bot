#!/usr/bin/env bash
# Installation sur un serveur Ubuntu (Oracle Cloud Free Tier) — à lancer avec sudo depuis le dossier du projet.
#   sudo bash deploy/installer.sh 152-70-12-34.sslip.io
set -euo pipefail
ADRESSE="${1:-}"
if [ -z "$ADRESSE" ]; then echo "Usage : sudo bash deploy/installer.sh 152-70-12-34.sslip.io"; exit 1; fi
CIBLE=/opt/shogunat-bot

apt-get update -q
apt-get install -y -q python3-venv rsync debian-keyring debian-archive-keyring apt-transport-https curl
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

id shogunat >/dev/null 2>&1 || useradd --system --home "$CIBLE" --shell /usr/sbin/nologin shogunat
mkdir -p "$CIBLE"
rsync -a --exclude .venv --exclude '*.db' --exclude __pycache__ ./ "$CIBLE/"
[ -f "$CIBLE/.env" ] || { echo "Il manque $CIBLE/.env : copie .env.example en .env et remplis-le."; exit 1; }
python3 -m venv "$CIBLE/.venv"
"$CIBLE/.venv/bin/pip" install -q -r "$CIBLE/requirements.txt"
chown -R shogunat:shogunat "$CIBLE"
chmod 600 "$CIBLE/.env"

sed "s/ADRESSE_DU_PANNEAU/$ADRESSE/" deploy/Caddyfile > /etc/caddy/Caddyfile
# Pare-feu d'Ubuntu sur Oracle : ouvrir le web (en plus de la « Security List » dans la console Oracle)
iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
command -v netfilter-persistent >/dev/null && netfilter-persistent save || true

cp deploy/shogunat-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now shogunat-bot
systemctl restart caddy
echo "✔ Panneau : https://$ADRESSE  (journal du bot : journalctl -u shogunat-bot -f)"
