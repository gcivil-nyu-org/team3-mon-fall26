#!/bin/bash
set -euo pipefail
tls_domain="$(/opt/elasticbeanstalk/bin/get-config environment -k DJANGO_TLS_DOMAIN 2>/dev/null || true)"
if [ -z "$tls_domain" ]; then
    exit 0
fi
cat > /etc/systemd/system/nyc-active-cert-renew.service <<'UNIT'
[Unit]
Description=Renew NYC Active origin TLS certificate
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/nyc-active-certbot/bin/certbot renew --cert-name nyc-active-origin --quiet --deploy-hook "/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx"
UNIT
cat > /etc/systemd/system/nyc-active-cert-renew.timer <<'UNIT'
[Unit]
Description=Check NYC Active TLS renewal twice daily

[Timer]
OnCalendar=*-*-* 03,15:00:00
RandomizedDelaySec=600
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now nyc-active-cert-renew.timer
systemctl is-active --quiet nyc-active-cert-renew.timer
