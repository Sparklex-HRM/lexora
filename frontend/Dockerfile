# syntax=docker/dockerfile:1.7
# Lexora HRM web — production build (vite build) served by unprivileged nginx (non-root, :8080).
#   docker build -t lexora-web frontend
# The SPA calls same-origin /api; nginx proxies /api/* to the gateway (LEXORA_GATEWAY_UPSTREAM).

FROM node:20.18.1-bookworm-slim AS build
WORKDIR /src
ENV CI=1 NODE_OPTIONS=--max-old-space-size=3072
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Signed UI grant (go-live 2026-10-26): the PRODUCTION public key(s) are pinned into the bundle at build time —
# current kid + at most one previous kid (scripts/ops/gen_ui_grant_key.py prints the value). A customer image is
# built with UI_GRANT_REQUIRE_KEYS=1: the build FAILS without a valid production key (docker-compose.prod.yml).
ARG VITE_UI_GRANT_PUBKEYS=""
ARG UI_GRANT_REQUIRE_KEYS=0
ENV VITE_UI_GRANT_PUBKEYS=${VITE_UI_GRANT_PUBKEYS} UI_GRANT_REQUIRE_KEYS=${UI_GRANT_REQUIRE_KEYS}
RUN npx tsc --noEmit -p tsconfig.json && npx vite build --mode production
# CSP: hash every inline <script> of the built index.html so script-src needs no 'unsafe-inline'.
RUN node nginx/csp-hash.mjs dist/index.html nginx/security-headers.conf > /tmp/security-headers.conf

FROM nginxinc/nginx-unprivileged:1.27.3-alpine AS web
# Runtime knobs. Empty LEXORA_HSTS = no HSTS header (plain-http local runs); production sets
# LEXORA_HSTS="max-age=31536000; includeSubDomains" once TLS is confirmed end to end.
# LEXORA_PUBLIC_HOST = the host name(s) the app answers for (space-separated); any other Host gets
# the connection closed (444). Default = local runs only; production sets the public domain.
ENV LEXORA_GATEWAY_UPSTREAM_HOSTPORT=api-gateway:8000 \
    LEXORA_PUBLIC_HOST="localhost 127.0.0.1" \
    LEXORA_MAX_BODY_MB=25 \
    LEXORA_PROXY_TIMEOUT=60 \
    LEXORA_HSTS="" \
    NGINX_ENVSUBST_FILTER="^LEXORA_"
COPY --from=build /src/dist /usr/share/nginx/html
COPY --from=build /tmp/security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY nginx/errors /usr/share/nginx/__errors
# go-live 2026-10-24: LEXORA_ENV=production refuses a localhost host name, warns when HSTS is off
COPY --chmod=0755 nginx/entrypoint.d/05-lexora-prod-guard.sh /docker-entrypoint.d/05-lexora-prod-guard.sh
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
