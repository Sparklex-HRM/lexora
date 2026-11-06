# syntax=docker/dockerfile:1.7
# Lexora HRM — one Dockerfile for every backend image (multi-stage, slim, non-root).
#
#   service image : docker build -f backend/Dockerfile --build-arg SVC=services/leave -t lexora-leave backend
#   gateway image : docker build -f backend/Dockerfile --build-arg SVC=api-gateway   -t lexora-gateway backend
#   migrate image : docker build -f backend/Dockerfile --target migrate             -t lexora-migrate backend
#
# Dependencies are installed from requirements.txt CONSTRAINED by constraints.txt
# (the full pinned set, `pip freeze` of a known-good build) so a rebuild next month
# installs byte-identical versions. Refresh with: scripts/ops/refresh-constraints.sh

ARG PYTHON_IMAGE=python:3.12.8-slim-bookworm

# ── 1. builder: compile wheels into a venv (build tools never reach the runtime) ──
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1 PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=8
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements.txt requirements-migrate.txt constraints.txt /tmp/req/
RUN pip install -r /tmp/req/requirements.txt -c /tmp/req/constraints.txt

FROM builder AS builder-migrate
RUN pip install -r /tmp/req/requirements-migrate.txt -c /tmp/req/constraints.txt

# ── 2. runtime base: slim, non-root, no compilers ─────────────────────────────
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH=/opt/venv/bin:$PATH PYTHONPATH=/app \
    LEXORA_FILES_DIR=/app/data/files
RUN groupadd --system --gid 10001 lexora \
 && useradd --system --uid 10001 --gid lexora --home-dir /app --shell /usr/sbin/nologin lexora \
 && mkdir -p /app/data/files && chown -R lexora:lexora /app/data
WORKDIR /app

# ── 3a. migrate image (target: migrate) — every service's models + Alembic ─────
FROM runtime AS migrate
COPY --from=builder-migrate /opt/venv /opt/venv
COPY --chown=root:root shared /app/shared
COPY --chown=root:root services /app/services
COPY --chown=root:root migrations /app/migrations
USER 10001:10001
ENTRYPOINT ["python", "-m", "migrations.cli"]
CMD ["upgrade"]

# ── 3b. service / gateway image (default, last stage) ─────────────────────────
FROM runtime AS service
COPY --from=builder /opt/venv /opt/venv
COPY --chown=root:root shared /app/shared
# The payroll engine's statutory constants (pure data modules) — the AI Payroll Auditor recomputes CPP/QPP, EI, QPIP
# and the YMPE/YAMPE band from these exact files (agents/payroll_auditor/statutory.py looks in /app/services/payroll).
COPY --chown=root:root services/payroll/constants_ca.py services/payroll/constants_qc.py /app/services/payroll/
ARG SVC
COPY --chown=root:root ${SVC} /app/svc
WORKDIR /app/svc
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"
# --proxy-headers: client IP / scheme from the edge proxy (only trusted from FORWARDED_ALLOW_IPS).
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-server-header --timeout-keep-alive ${UVICORN_KEEPALIVE:-5} --workers ${UVICORN_WORKERS:-1}"]
