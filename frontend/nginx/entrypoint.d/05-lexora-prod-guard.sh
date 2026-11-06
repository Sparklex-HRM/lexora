#!/bin/sh
# Lexora HRM web edge - production boot guard (go-live 2026-10-24). Runs before envsubst.
# LEXORA_ENV=production: refuse a localhost-only public host name; warn loudly when HSTS is off.
set -eu
env_lc=$(printf '%s' "${LEXORA_ENV:-}" | tr 'A-Z' 'a-z')
case "$env_lc" in production|prod) ;; *) exit 0 ;; esac
fail=0
for h in ${LEXORA_PUBLIC_HOST:-}; do
  case "$h" in localhost|127.0.0.1|0.0.0.0|::1) echo "[web] refusing to start: LEXORA_PUBLIC_HOST contains '$h' in production" >&2; fail=1 ;; esac
done
[ -n "${LEXORA_PUBLIC_HOST:-}" ] || { echo "[web] refusing to start: LEXORA_PUBLIC_HOST is empty in production" >&2; fail=1; }
if [ -z "${LEXORA_HSTS:-}" ]; then
  echo "[web] PRODUCTION WARNING: LEXORA_HSTS is empty - no Strict-Transport-Security header (set it once TLS works end to end)" >&2
fi
[ "$fail" -eq 0 ] || exit 78
exit 0
