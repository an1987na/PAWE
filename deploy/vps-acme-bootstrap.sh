#!/bin/sh
set -eu

# One-time bootstrap only. Later certificate renewals use certbot.timer.
for existing in /etc/nginx/sites-available/pawe.conf /etc/nginx/sites-enabled/pawe.conf /etc/nginx/sites-available/pawe-acme.conf /etc/nginx/sites-enabled/pawe-acme.conf; do
    if [ -e "$existing" ] || [ -L "$existing" ]; then
        printf '%s\n' 'PAWE site already exists; refuse bootstrap overwrite' >&2
        exit 1
    fi
done

site=/etc/nginx/sites-available/pawe-acme.conf
enabled=/etc/nginx/sites-enabled/pawe-acme.conf
root=/var/www/pawe-acme
mkdir -p "$root/.well-known/acme-challenge"
chown -R www-data:www-data "$root"
chmod 0755 "$root" "$root/.well-known" "$root/.well-known/acme-challenge"
cat > "$site" <<'NGINX'
server {
    listen 80;
    listen [::]:80;
    server_name pawe.foxerlove.cn;
    location /.well-known/acme-challenge/ {
        root /var/www/pawe-acme;
    }
    location / { return 404; }
}
NGINX
ln -sfn "$site" "$enabled"
nginx -t
systemctl reload nginx
dig +short pawe.foxerlove.cn A
/usr/bin/certbot certonly --webroot -w "$root" -d pawe.foxerlove.cn --non-interactive --agree-tos --register-unsafely-without-email --keep-until-expiring
rm -f "$enabled"
rm -f "$site"
cat > /etc/nginx/sites-available/pawe.conf <<'NGINX'
limit_req_zone $binary_remote_addr zone=pawe_login:1m rate=6r/m;
limit_req_zone $binary_remote_addr zone=pawe_general:1m rate=20r/s;
server {
    listen 80;
    server_name pawe.foxerlove.cn;
    location /.well-known/acme-challenge/ { root /var/www/pawe-acme; }
    location / { return 301 https://pawe.foxerlove.cn$request_uri; }
}
server {
    listen 443 ssl;
    server_name pawe.foxerlove.cn;
    ssl_certificate /etc/letsencrypt/live/pawe.foxerlove.cn/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pawe.foxerlove.cn/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    server_tokens off;
    client_max_body_size 1m;
    client_body_timeout 15s;
    limit_req_status 429;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy same-origin always;
    access_log off;
    error_log /var/log/nginx/pawe-error.log crit;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_cache off;
    proxy_max_temp_file_size 0;
    proxy_http_version 1.1;
    proxy_connect_timeout 5s;
    proxy_read_timeout 180s;
    proxy_set_header Host pawe.foxerlove.cn;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $remote_addr;
    location = /api/v1/auth/login {
        limit_req zone=pawe_login burst=5 nodelay;
        proxy_pass http://127.0.0.1:18443;
    }
    location / {
        limit_req zone=pawe_general burst=40 nodelay;
        proxy_pass http://127.0.0.1:18443;
    }
}
NGINX
ln -sfn /etc/nginx/sites-available/pawe.conf /etc/nginx/sites-enabled/pawe.conf
nginx -t
systemctl reload nginx
printf '%s\n' '== existing site status =='
for host in pokemon.foxerlove.cn duty.foxerlove.cn; do
    curl --silent --show-error --output /dev/null --write-out "$host %{http_code}\n" "https://$host/" || true
done
printf '%s\n' '== pawe placeholder status =='
curl --silent --show-error --output /dev/null --write-out 'pawe %{http_code}\n' https://pawe.foxerlove.cn/ || true
