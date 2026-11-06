#!/usr/bin/env bash
# Lexora HRM — external health probe + alert hook (go-live 2026-10-24).
# Checks the public edge (/healthz, /api/health, /api/ready) and, on a compose host, every container's
# health state. Exit 0 = all good; 1 = something is down (cron / systemd timer / uptime monitor friendly).
#
#   JETHRO_URL=https://hr.example.ca scripts/ops/healthcheck.sh
#   JETHRO_URL=… ALERT_WEBHOOK_URL=https://hooks.slack.com/… scripts/ops/healthcheck.sh   # POST {"text": …} on failure
#   COMPOSE_PROJECT=lexora-prod scripts/ops/healthcheck.sh                              # + container health
#
# Cron every 5 minutes (VM):  */5 * * * * JETHRO_URL=… ALERT_WEBHOOK_URL=… /srv/lexora/scripts/ops/healthcheck.sh >> /var/log/lexora-health.log 2>&1
# The webhook gets only names + HTTP codes — never tokens, hosts' internals or personal data.
set -uo pipefail
URL="${JETHRO_URL:-}"
TIMEOUT="${HEALTH_TIMEOUT_SEC:-10}"
fail=()
ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

probe() {                    # probe <name> <url> [expected-code]
  local code
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time "$TIMEOUT" "$2" || echo 000)"
  if [ "$code" = "${3:-200}" ]; then echo "$(ts) ok   $1 ($code)"; else echo "$(ts) FAIL $1 ($code)"; fail+=("$1=$code"); fi
}

if [ -n "$URL" ]; then
  URL="${URL%/}"
  probe "edge /healthz" "$URL/healthz"
  probe "gateway /api/health" "$URL/api/health"
  probe "gateway /api/ready (db+redis)" "$URL/api/ready"
  probe "web app /" "$URL/"
  # TLS certificate: warn when it expires within 14 days
  if [[ "$URL" == https://* ]] && command -v openssl >/dev/null 2>&1; then
    host="${URL#https://}"; host="${host%%/*}"
    end="$(echo | openssl s_client -servername "$host" -connect "$host:443" 2>/dev/null | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)"
    if [ -n "$end" ]; then
      left=$(( ( $(date -d "$end" +%s 2>/dev/null || echo 0) - $(date +%s) ) / 86400 ))
      if [ "$left" -lt 14 ]; then echo "$(ts) FAIL TLS certificate expires in $left day(s)"; fail+=("tls_days_left=$left"); else echo "$(ts) ok   TLS ($left days left)"; fi
    fi
  fi
fi

if [ -n "${COMPOSE_PROJECT:-}" ] && command -v docker >/dev/null 2>&1; then
  while read -r name state; do
    [ -z "$name" ] && continue
    case "$state" in healthy|"") echo "$(ts) ok   container $name ${state:-running}";;
      *) echo "$(ts) FAIL container $name ($state)"; fail+=("$name=$state");;
    esac
  done < <(docker ps -a --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
            --format '{{.Names}} {{if .State}}{{.State}}{{end}}' | while read -r n s; do
              h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$n" 2>/dev/null)"
              # one-shot jobs (migrate) exit 0 by design
              if [ "$s" = exited ] && [ "$(docker inspect -f '{{.State.ExitCode}}' "$n")" = 0 ]; then continue; fi
              [ "$s" != running ] && h="$s"
              echo "$n $h"; done)
fi

if [ -z "$URL" ] && [ -z "${COMPOSE_PROJECT:-}" ]; then
  echo "set JETHRO_URL and/or COMPOSE_PROJECT"; exit 2
fi

if [ "${#fail[@]}" -gt 0 ]; then
  if [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
    msg="Lexora HRM health check FAILED at $(ts): ${fail[*]}"
    curl -s --max-time 10 -H 'Content-Type: application/json' -d "{\"text\": \"${msg//\"/\'}\"}" "$ALERT_WEBHOOK_URL" >/dev/null || true
  fi
  exit 1
fi
exit 0
