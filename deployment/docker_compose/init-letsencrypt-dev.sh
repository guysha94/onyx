#!/bin/bash
# Bootstrap Let's Encrypt for the dev:gpu stack (single domain, no www alias).
#
# Prerequisites (.env.nginx):
#   DOMAIN (required)
#   CERTBOT_CHALLENGE=manual-dns|webroot (default: manual-dns)
#
# manual-dns: certbot prints a TXT record; create it in DNS, then press Enter (nginx not required)
# webroot:    HTTP-01 via nginx; needs public port 80

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

set -o allexport
source .env.nginx
set +o allexport

if [ -z "${DOMAIN:-}" ]; then
  echo "DOMAIN must be set in .env.nginx" >&2
  exit 1
fi

CHALLENGE="${CERTBOT_CHALLENGE:-manual-dns}"

COMPOSE_FILES=(
  -f docker-compose.yml
  -f docker-compose.dev.yml
  -f docker-compose.gpu.yml
  -f docker-compose.dev-ssl.yml
)

docker_compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    echo "Error: docker compose (V2 plugin) or docker-compose is not installed." >&2
    exit 1
  fi
}

COMPOSE_CMD=$(docker_compose_cmd)
WAIT_ARGS=(--wait --wait-timeout 300)
[[ "$COMPOSE_CMD" == "docker-compose" ]] && WAIT_ARGS=()

domains=("$DOMAIN")
rsa_key_size=4096
data_path="../data/certbot"
email="${EMAIL:-}"
staging="${LETSENCRYPT_STAGING:-0}"

if [ -d "$data_path/conf/live/$DOMAIN" ]; then
  read -r -p "Existing certificate found for $DOMAIN. Replace it? (y/N) " decision
  if [ "$decision" != "Y" ] && [ "$decision" != "y" ]; then
    exit 0
  fi
fi

if [ ! -e "$data_path/conf/options-ssl-nginx.conf" ] || [ ! -e "$data_path/conf/ssl-dhparams.pem" ]; then
  echo "### Downloading recommended TLS parameters ..."
  mkdir -p "$data_path/conf"
  curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot-nginx/certbot_nginx/_internal/tls_configs/options-ssl-nginx.conf > "$data_path/conf/options-ssl-nginx.conf"
  curl -s https://raw.githubusercontent.com/certbot/certbot/master/certbot/certbot/ssl-dhparams.pem > "$data_path/conf/ssl-dhparams.pem"
fi

domain_args=""
for domain in "${domains[@]}"; do
  domain_args="$domain_args -d $domain"
done

case "$email" in
  "") email_arg="--register-unsafely-without-email" ;;
  *) email_arg="--email $email" ;;
esac

staging_arg=""
if [ "$staging" != "0" ]; then
  staging_arg="--staging"
fi

case "$CHALLENGE" in
  manual-dns)
    echo "### Deleting existing certificate files for $DOMAIN ..."
    $COMPOSE_CMD "${COMPOSE_FILES[@]}" run --name onyx --rm --entrypoint "\
      rm -Rf /etc/letsencrypt/live/$DOMAIN && \
      rm -Rf /etc/letsencrypt/archive/$DOMAIN && \
      rm -Rf /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

    echo ""
    echo "### Manual DNS-01 for $DOMAIN"
    echo "Certbot will print a TXT record like:"
    echo "  _acme-challenge.$DOMAIN  TXT  <value>"
    echo ""
    echo "1. Create that TXT record in Route 53 (superplay.dev zone)"
    echo "2. Wait for propagation (nslookup -type=TXT _acme-challenge.$DOMAIN)"
    echo "3. Press Enter in the certbot prompt when ready"
    echo ""
    $COMPOSE_CMD "${COMPOSE_FILES[@]}" run --name onyx --rm -it --entrypoint "\
      certbot certonly --manual --preferred-challenges dns \
        $staging_arg \
        $email_arg \
        $domain_args \
        --rsa-key-size $rsa_key_size \
        --agree-tos \
        --force-renewal" certbot
    ;;
  webroot)
    echo "### Creating dummy certificate for $DOMAIN ..."
    path="/etc/letsencrypt/live/$DOMAIN"
    mkdir -p "$data_path/conf/live/$DOMAIN"
    $COMPOSE_CMD "${COMPOSE_FILES[@]}" run --name onyx --rm --entrypoint "\
      openssl req -x509 -nodes -newkey rsa:$rsa_key_size -days 1\
        -keyout '$path/privkey.pem' \
        -out '$path/fullchain.pem' \
        -subj '/CN=localhost'" certbot

    echo "### Starting nginx ..."
    $COMPOSE_CMD "${COMPOSE_FILES[@]}" up --force-recreate -d "${WAIT_ARGS[@]}" nginx

    echo "### Deleting dummy certificate for $DOMAIN ..."
    $COMPOSE_CMD "${COMPOSE_FILES[@]}" run --name onyx --rm --entrypoint "\
      rm -Rf /etc/letsencrypt/live/$DOMAIN && \
      rm -Rf /etc/letsencrypt/archive/$DOMAIN && \
      rm -Rf /etc/letsencrypt/renewal/$DOMAIN.conf" certbot

    echo "### Requesting Let's Encrypt certificate via HTTP-01 (webroot) for $DOMAIN ..."
    $COMPOSE_CMD "${COMPOSE_FILES[@]}" run --name onyx --rm --entrypoint "\
      certbot certonly --webroot -w /var/www/certbot \
        $staging_arg \
        $email_arg \
        $domain_args \
        --rsa-key-size $rsa_key_size \
        --agree-tos \
        --force-renewal \
        --non-interactive" certbot
    ;;
  *)
    echo "Unsupported CERTBOT_CHALLENGE=$CHALLENGE (use manual-dns or webroot)" >&2
    exit 1
    ;;
esac

if [ ! -e "$data_path/conf/live/$DOMAIN/fullchain.pem" ]; then
  echo "### Renaming certificate directory if needed ..."
  $COMPOSE_CMD "${COMPOSE_FILES[@]}" run --name onyx --rm --entrypoint "\
    sh -c 'numbered_dir=\$(find /etc/letsencrypt/live -maxdepth 1 -type d -name \"$DOMAIN-00*\" | sort -r | head -n1); \
      if [ -n \"\$numbered_dir\" ]; then mv \"\$numbered_dir\" /etc/letsencrypt/live/$DOMAIN; fi'" certbot
else
  echo "### Certificate already at live/$DOMAIN (no rename needed)"
fi

if [ "$CHALLENGE" = "manual-dns" ]; then
  echo "### Starting nginx (manual DNS: auto-renew disabled) ..."
  $COMPOSE_CMD "${COMPOSE_FILES[@]}" up --force-recreate -d "${WAIT_ARGS[@]}" nginx
else
  echo "### Reloading nginx + starting certbot renewal sidecar ..."
  $COMPOSE_CMD "${COMPOSE_FILES[@]}" up --force-recreate -d nginx certbot
fi

echo "### Done. Access Onyx at https://$DOMAIN"
