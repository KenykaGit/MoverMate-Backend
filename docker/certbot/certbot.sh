#!/bin/sh
# Obtains and renews the Let's Encrypt certificate for $DOMAIN (docs/adr/0009).
# Run as `sh <this file>` by the certbot service of docker-compose.prod.yml.
#
# Volumes: /etc/letsencrypt (certbot state, private to this container), /var/www/certbot
# (ACME webroot, served by nginx on :80), /tls (the pair nginx reads: fullchain.pem,
# privkey.pem). nginx never reads /etc/letsencrypt: archive/ is root-only there, and a
# self-signed placeholder in live/ would make certbot refuse to issue.
set -eu

: "${DOMAIN:?DOMAIN is required}"
tls=/tls
webroot=/var/www/certbot
live=/etc/letsencrypt/live/app

log() { echo "certbot.sh: $*" >&2; }

# Copy the current certificate to nginx when it differs. Key first, chain last and both via
# rename: nginx reloads when fullchain.pem changes and must find a matching key by then.
install_cert() {
    if cmp -s "$live/fullchain.pem" "$tls/fullchain.pem"; then
        return 0
    fi
    cp "$live/privkey.pem" "$tls/.privkey.pem" && mv "$tls/.privkey.pem" "$tls/privkey.pem"
    cp "$live/fullchain.pem" "$tls/.fullchain.pem" && mv "$tls/.fullchain.pem" "$tls/fullchain.pem"
    log "installed the certificate for $DOMAIN"
}

# The webroot stays world-readable for the nginx workers; private keys are 0600 anyway
# (openssl writes them so, cp keeps the mode of certbot's key), the /tls dir is root-only.
mkdir -p "$webroot/.well-known/acme-challenge"
mkdir -p "$tls" && chmod 700 "$tls"

# nginx cannot start `listen 443 ssl` without a certificate: give it a placeholder that
# lives until the first issuance. The healthcheck of this service waits for the file.
if [ ! -s "$tls/fullchain.pem" ]; then
    if [ -s "$live/fullchain.pem" ]; then
        install_cert
    else
        log "no certificate yet, writing a self-signed placeholder"
        openssl req -x509 -nodes -newkey rsa:2048 -days 7 -subj "/CN=$DOMAIN" \
            -keyout "$tls/privkey.pem" -out "$tls/fullchain.pem" 2>/dev/null
    fi
fi

if [ -n "${CERTBOT_EMAIL:-}" ]; then
    set -- --email "$CERTBOT_EMAIL"
else
    set -- --register-unsafely-without-email
fi

# Let's Encrypt allows only a few failed validations per hour, so do not ask it before the
# webroot is reachable through nginx (it starts after this service turns healthy).
probe="$webroot/.well-known/acme-challenge/probe"
echo ok > "$probe"
chmod 644 "$probe"
until wget -qO /dev/null "http://nginx/.well-known/acme-challenge/probe"; do
    log "waiting for nginx to serve the ACME webroot"
    sleep 5
done
rm -f "$probe"

# `certonly --keep-until-expiring` issues on the first run, renews within 30 days of expiry
# and is a no-op otherwise; --cert-name keeps the path stable when DOMAIN changes.
while :; do
    if certbot certonly --webroot -w "$webroot" --cert-name app -d "$DOMAIN" \
        --non-interactive --agree-tos --keep-until-expiring "$@"; then
        install_cert
        sleep 12h
    else
        log "issuance failed (DNS of $DOMAIN, port 80 reachable?), retrying in 1h"
        sleep 1h
    fi
done
