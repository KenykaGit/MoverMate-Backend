#!/bin/sh
# Production entrypoint of nginx (docs/adr/0009): starts nginx and reloads it whenever the
# certbot service installs a new certificate into /etc/nginx/tls. Run as `sh <this file>`,
# so the file needs no exec bit after `tsh scp`.
set -eu

cert=/etc/nginx/tls/fullchain.pem

# certbot puts at least a self-signed placeholder there before nginx starts (depends_on),
# the wait only covers a manual `docker compose up nginx` on a fresh volume.
i=0
until [ -s "$cert" ]; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
        echo "tls-entrypoint: no certificate in $cert, is the certbot service running?" >&2
        exit 1
    fi
    sleep 1
done

# certbot writes privkey.pem first and fullchain.pem last (atomic rename), so a changed
# fullchain.pem means the pair is complete. `nginx -s reload` keeps the old config if the
# new one fails to load.
(
    seen=$(md5sum "$cert")
    while sleep 60; do
        now=$(md5sum "$cert" 2>/dev/null) || continue
        if [ "$now" != "$seen" ]; then
            seen=$now
            echo "tls-entrypoint: certificate changed, reloading nginx" >&2
            nginx -s reload || true
        fi
    done
) &

exec nginx -g 'daemon off;'
