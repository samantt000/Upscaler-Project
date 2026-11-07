# Caddy с модулем DuckDNS: сертификат выпускается через проверку DNS (запись TXT),
# поэтому входящие порты 80 и 443 не нужны (провайдер их блокирует)
FROM caddy:2.10.2-builder-alpine AS builder
RUN xcaddy build --with github.com/caddy-dns/duckdns@v0.5.0

FROM caddy:2.10.2-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
