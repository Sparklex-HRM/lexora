"""Lexora HRM — production runtime for every service and the gateway (stream S3).

One line at the end of each ``main.py`` wires it in::

    from shared.ops_runtime import install as _ops_install; _ops_install(app, "leave")

What it does
------------
* **Production secret gate** — with ``LEXORA_ENV=production`` the process refuses to
  start (``SystemExit(78)``) while any secret is a default / dev / test value: JWT
  secret, internal service token, data-encryption key, the Postgres password, an
  unauthenticated Redis, a ``*`` or plain-http CORS origin. Only the NAMES of the
  failing settings are logged, never values.
* ``LEXORA_DEV_SHOW_LINKS`` (on-screen invite / e-sign codes for test machines) is
  forced OFF in production — removed from the environment before any route reads it.
  ``LEXORA_MFA_ENFORCE=0`` is ignored in production too (two-step sign-in stays mandatory).
* **Liveness / readiness** — ``GET /health`` (process is up; existing handlers are
  kept) and ``GET /ready`` (Postgres ``SELECT 1`` + Redis ``PING``; 503 otherwise).
  Both are moved to the FRONT of the route table so the gateway's ``/{service}``
  catch-all can never swallow them.
* **Structured logs** — one JSON object per line on stdout, with the request id.
  A ``PIIRedactingFilter`` on the root handler masks SIN, bank account / transit,
  salary-like and credential fields before anything is written.
* **Request id** — ``X-Request-Id`` is accepted (if well-formed) or generated, put
  back into the ASGI scope so the gateway forwards it to the service, echoed on the
  response and stamped on every log line.
* **Body-size limit** — ``LEXORA_MAX_BODY_MB`` (default 25) on the gateway; 413 above it.
* **Go-live 2026-10-24** — the production gate also refuses dev-only switches (CAPTCHA_MODE=off,
  LEXORA_DIRECT_REGISTER, DEBUG / reload, any LEXORA_*DEV* / *INSECURE* setting, a missing / dev UI-grant
  key on auth, an empty / '*' / localhost LEXORA_ALLOWED_HOSTS, LEXORA_GATEWAY_RPM=0 and
  LEXORA_RBAC_ENFORCE=off on the gateway), prints PRODUCTION WARNING lines for allowed-but-risky settings
  (public sign-up, TRUST_FORWARDED_FOR off, no SMTP, DEBUG logs), refuses to boot auth while the database
  holds demo / e2e orgs or logins (LEXORA_ALLOW_DEMO_DATA=1 = explicit opt-out), and adds API security
  headers (nosniff, DENY framing, strict CSP on JSON, no-referrer, no-store, HSTS from LEXORA_HSTS) on
  every gateway answer.

Nothing here changes business behaviour. ``LEXORA_ENV`` unset (= development) keeps
today's local stack working exactly as before, except for the JSON log format
(``LEXORA_LOG_FORMAT=text`` restores plain lines).
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import sys
import time
import uuid
from urllib.parse import urlsplit

# ── environment ───────────────────────────────────────────────────────────────

PROD_NAMES = ("production", "prod")


def is_production() -> bool:
    return (os.getenv("LEXORA_ENV") or "").strip().lower() in PROD_NAMES


# Values that have ever shipped as defaults in this repo, or are obviously placeholders.
KNOWN_WEAK = {
    "", "dev-only-insecure-key-change-me", "test-secret-not-for-production-0123456789",
    "changeme", "change-me", "change_me", "secret", "password", "postgres", "admin",
    "lexora", "example", "placeholder", "your-secret", "xxx",
}
_PLACEHOLDER_RE = re.compile(r"(change[-_ ]?me|example|placeholder|dev[-_]only|not[-_]for[-_]production|insecure|<.*>)", re.I)


def _weak(value: str | None, min_len: int = 32) -> bool:
    v = (value or "").strip()
    return v.lower() in KNOWN_WEAK or len(v) < min_len or bool(_PLACEHOLDER_RE.search(v))


def production_problems(env: dict | None = None, service: str | None = None) -> list[str]:
    """Return the list of problems (setting NAMES + reason, never values) that make this
    process unsafe to run in production. Empty list = OK. Pure function — tested."""
    e = os.environ if env is None else env
    out: list[str] = []
    if _weak(e.get("JWT_SECRET")):
        out.append("JWT_SECRET is unset, a known dev/test default, or shorter than 32 characters")
    tok = e.get("LEXORA_INTERNAL_TOKEN")
    if _weak(tok):
        out.append("LEXORA_INTERNAL_TOKEN (service-to-service secret) is unset, weak or shorter than 32 characters")
    elif tok == e.get("JWT_SECRET"):
        out.append("LEXORA_INTERNAL_TOKEN must differ from JWT_SECRET")
    if not (e.get("LEXORA_DATA_KEY") or "").strip():
        out.append("LEXORA_DATA_KEY (field encryption key for SIN / bank) is unset — dev-derived key would be used")

    db = e.get("DATABASE_URL") or ""
    try:
        pw = urlsplit(db.replace("+asyncpg", "")).password
    except ValueError:
        pw = None
    if not db:
        out.append("DATABASE_URL is unset")
    elif _weak(pw, min_len=16):
        out.append("DATABASE_URL uses an empty, default ('postgres') or short (<16) database password")

    rurl = e.get("REDIS_URL") or ""
    try:
        rpw = urlsplit(rurl).password
    except ValueError:
        rpw = None
    if not rurl:
        out.append("REDIS_URL is unset")
    elif _weak(rpw, min_len=16):
        out.append("REDIS_URL has no password (or a weak / short one) — Redis must require auth")

    origins = [o.strip() for o in (e.get("CORS_ORIGINS") or "").split(",") if o.strip()]
    if not origins:
        out.append("CORS_ORIGINS is empty — set the https:// origin(s) of the web app")
    for o in origins:
        if o == "*" or "*" in o:
            out.append("CORS_ORIGINS contains a wildcard")
        elif not o.lower().startswith("https://"):
            out.append("CORS_ORIGINS contains a non-https origin")
        elif re.search(r"//(localhost|127\.0\.0\.1|0\.0\.0\.0)\b", o):
            out.append("CORS_ORIGINS contains a localhost origin")
    if (e.get("APP_BASE_URL") or "").strip() and not e["APP_BASE_URL"].strip().lower().startswith("https://"):
        out.append("APP_BASE_URL must be an https:// URL (links in e-mails)")
    out.extend(_unsafe_switches(e, service))
    return sorted(set(out))


# ── go-live 2026-10-24: dev-only / unsafe switches refused in production ──────

_TRUE = ("1", "true", "yes", "on")
# Dev-only switches that are handled elsewhere (forced off / ignored) and therefore not refused here.
_FORCED_OFF = ("LEXORA_DEV_SHOW_LINKS",)


def _truthy(v) -> bool:
    return (str(v or "")).strip().lower() in _TRUE


def _ui_grant_present() -> bool:
    """True when the auth service ships the signed-UI-grant module (checked by file, never imported)."""
    import importlib.util
    try:
        return importlib.util.find_spec("ui_grant") is not None
    except (ImportError, ValueError):
        return False


def _unsafe_switches(e, service: str | None) -> list[str]:
    """Settings that are legitimate on a developer machine but turn a security control off.
    Names + reason only (never values)."""
    out: list[str] = []
    if (e.get("CAPTCHA_MODE") or "pow").strip().lower() in ("off", "0", "false", "none", "disabled"):
        out.append("CAPTCHA_MODE is off — the sign-in / forgot-password / sign-up CAPTCHA must stay on (pow)")
    if _truthy(e.get("LEXORA_DIRECT_REGISTER")):
        out.append("LEXORA_DIRECT_REGISTER is on — instant org creation without an e-mail check is for local / e2e only")
    for k in ("DEBUG", "LEXORA_DEBUG", "FASTAPI_DEBUG", "UVICORN_RELOAD"):
        if _truthy(e.get(k)):
            out.append(f"{k} is on — debug / auto-reload is never allowed in production")
    # Any other LEXORA_*DEV* / *INSECURE* switch (e.g. a development signing key for the UI grant, a dev
    # bypass) must not be set at all in production — whatever its value.
    for k in sorted(e):
        ku = k.upper()
        if not ku.startswith("LEXORA_") or ku in _FORCED_OFF or not str(e.get(k) or "").strip():
            continue
        parts = ku.split("_")
        if "DEV" in parts or "INSECURE" in parts or "DEVKEY" in parts or ku.endswith("_DEV_KEY"):
            out.append(f"{k} is a development-only setting and must not be set in production")
    if service == "auth" and _ui_grant_present():
        # RBAC stream 2026-10-24: the browser takes the role ONLY from a server-signed ES256 ui_grant
        # (services/auth/ui_grant.py). No key = nobody can use the web app; the dev key is public.
        # 2026-10-26: the key itself is loaded and checked (P-256, readable, not a known DEV key under any kid).
        from shared.utils import ui_grant_key
        out.extend(ui_grant_key.production_problems(e))
    if service == "api-gateway":
        hosts = [h.strip().lower() for h in (e.get("LEXORA_ALLOWED_HOSTS") or "").split(",") if h.strip()]
        if not hosts or "*" in hosts:
            out.append("LEXORA_ALLOWED_HOSTS is empty or '*' — set the public host name(s) (Host-header allow-list)")
        elif any(h in ("localhost", "127.0.0.1", "0.0.0.0", "::1") for h in hosts):
            out.append("LEXORA_ALLOWED_HOSTS contains a localhost name")
        if (e.get("LEXORA_RBAC_ENFORCE") or "all").strip().lower() == "off":
            out.append("LEXORA_RBAC_ENFORCE=off switches the gateway permission-catalogue check off")
        rpm = (e.get("LEXORA_GATEWAY_RPM") or "").strip()
        if rpm in ("0", "-1"):
            out.append("LEXORA_GATEWAY_RPM=0 switches the gateway flood guard off")
    return out


def production_warnings(env: dict | None = None, service: str | None = None) -> list[str]:
    """Allowed in production but worth a loud line in the boot log (business decisions / missing
    optional providers). Names only, never values."""
    e = os.environ if env is None else env
    out: list[str] = []
    if _truthy(e.get("LEXORA_PUBLIC_SIGNUP")) and service in (None, "auth"):
        out.append("LEXORA_PUBLIC_SIGNUP is ON — anyone can create a trial org (decision 2026-10-21: off; "
                   "operator provisions tenants)")
    if _truthy(e.get("LEXORA_API_DOCS")):
        out.append("LEXORA_API_DOCS is set — ignored in production (API docs stay off)")
    if (e.get("LOG_LEVEL") or "").strip().upper() == "DEBUG":
        out.append("LOG_LEVEL=DEBUG — verbose logs in production; use INFO")
    if service == "api-gateway" and not _truthy(e.get("TRUST_FORWARDED_FOR")):
        out.append("TRUST_FORWARDED_FOR is off — behind a proxy every client shares the proxy's address "
                   "(rate limits / lock-outs per proxy, not per user)")
    if service in ("auth", "notification") and not (e.get("SMTP_HOST") or "").strip():
        out.append("SMTP_HOST is empty — invites, password resets and notices cannot be e-mailed")
    if _truthy(e.get("LEXORA_ALLOW_DEMO_DATA")) and service in (None, "auth"):
        out.append("LEXORA_ALLOW_DEMO_DATA is on — demo / test organisations are allowed in this database")
    return out


class ProductionConfigError(SystemExit):
    pass


def enforce_production_config(service: str, env: dict | None = None) -> None:
    """Refuse to start with dev secrets in production; force dev-only switches off."""
    if not is_production() and env is None:
        return
    e = os.environ if env is None else env
    if (e.get("LEXORA_DEV_SHOW_LINKS") or "").strip():
        e.pop("LEXORA_DEV_SHOW_LINKS", None)
        logging.getLogger("lexora.ops").warning("LEXORA_DEV_SHOW_LINKS ignored in production (forced off)",
                                                extra={"service": service})
    # Decision 2026-10-21: two-step sign-in is mandatory for real customers — a production
    # process never runs with it switched off (only the local demo stack uses 0).
    if (e.get("LEXORA_MFA_ENFORCE") or "").strip().lower() in ("0", "false", "no", "off"):
        e.pop("LEXORA_MFA_ENFORCE", None)
        logging.getLogger("lexora.ops").warning("LEXORA_MFA_ENFORCE=0 ignored in production (MFA stays mandatory)",
                                                extra={"service": service})
    for w in production_warnings(e, service):
        logging.getLogger("lexora.ops").warning("PRODUCTION WARNING: %s", w, extra={"service": service})
        sys.stderr.write(f"[{service}] PRODUCTION WARNING: {w}\n")
    problems = production_problems(e, service)
    if problems:
        log = logging.getLogger("lexora.ops")
        for p in problems:
            log.critical("refusing to start: %s", p, extra={"service": service})
        sys.stderr.write(f"[{service}] LEXORA_ENV=production but {len(problems)} setting(s) are unsafe:\n"
                         + "".join(f"  - {p}\n" for p in problems))
        sys.stderr.flush()
        raise ProductionConfigError(78)            # EX_CONFIG


# ── go-live 2026-10-24: a production database starts with NO demo data ────────
# Markers of the demo / e2e / probe data the developer stack carries. `.example` is a reserved TLD
# (RFC 2606) — no real customer login can have it. Org names are the exact demo names; codes are not
# used (a real customer "Maple Foods" would legitimately get the code MAPLE).
DEMO_EMAILS = ("superadmin@lexora.io", "lexora@lexora.io", "demo-admin@northbridge.ca")
DEMO_EMAIL_DOMAINS = ("northbridge.ca", "contoso-demo.ca", "acme-test.ca", "maple.example")
DEMO_ORG_NAMES = ("Maple Logistics Inc.", "Northbridge Logistics", "Acme Test Inc", "Contoso Demo Inc")
DEMO_ORG_NAME_PREFIXES = ("E2E ", "Probe ", "WASA")

DEMO_SQL = {
    "demo_logins": (
        "SELECT count(*) FROM users WHERE lower(email) LIKE '%.example' OR lower(email) = ANY($1::text[]) "
        "OR split_part(lower(email), '@', 2) = ANY($2::text[])",
        lambda: [list(DEMO_EMAILS), list(DEMO_EMAIL_DOMAINS)]),
    "demo_orgs": (
        "SELECT count(*) FROM organizations WHERE name = ANY($1::text[]) "
        "OR EXISTS (SELECT 1 FROM unnest($2::text[]) p WHERE organizations.name LIKE p || '%')",
        lambda: [list(DEMO_ORG_NAMES), list(DEMO_ORG_NAME_PREFIXES)]),
}


async def demo_data_markers(url: str | None = None, timeout: float = 5.0) -> dict[str, int]:
    """Count demo / test rows in the identity tables ({} when the DB is not Postgres or the tables do not
    exist yet). Read-only."""
    url = url if url is not None else (os.getenv("DATABASE_URL") or "")
    if not url.startswith("postgresql"):
        return {}
    import asyncpg
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"), timeout=timeout)
    out: dict[str, int] = {}
    try:
        for name, (sql, args) in DEMO_SQL.items():
            try:
                n = int(await conn.fetchval(sql, *args()) or 0)
            except asyncpg.UndefinedTableError:
                continue
            if n:
                out[name] = n
    finally:
        await conn.close()
    return out


async def enforce_no_demo_data(service: str, url: str | None = None) -> None:
    """Production only: refuse to start while the database holds demo / e2e organisations or logins
    (a dev database restored into production, a seed script pointed at it). LEXORA_ALLOW_DEMO_DATA=1
    is the explicit, logged opt-out for a demo / staging deployment that runs in production mode."""
    if _truthy(os.getenv("LEXORA_ALLOW_DEMO_DATA")):
        return
    found = await demo_data_markers(url)
    if found:
        msg = ", ".join(f"{k}={v}" for k, v in sorted(found.items()))
        logging.getLogger("lexora.ops").critical("refusing to start: demo / test data in the production database (%s)",
                                                 msg, extra={"service": service})
        sys.stderr.write(f"[{service}] LEXORA_ENV=production but the database holds demo / test data ({msg}). "
                         "Start production from an empty database (scripts/ops/migrate.sh upgrade + "
                         "platform_admin.py), or set LEXORA_ALLOW_DEMO_DATA=1 for a demo deployment.\n")
        sys.stderr.flush()
        raise ProductionConfigError(78)


def _install_demo_data_guard(app, service: str) -> None:
    """Run enforce_no_demo_data AFTER the service's own startup (tables exist), inside its lifespan."""
    from contextlib import asynccontextmanager

    original = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan(a):
        async with original(a) as state:
            await enforce_no_demo_data(service)
            yield state

    app.router.lifespan_context = _lifespan


# ── API response security headers (gateway) ──────────────────────────────────

API_SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), geolocation=(), microphone=(), payment=(), usb=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
)
# JSON / text answers never need to run script or be framed. Files (PDF, images, xlsx) get no extra CSP
# so browser viewers keep working; a service that sets its own CSP (uploaded files: sandbox) keeps it.
API_CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
# The public e-sign page shows the document in an <iframe> of OUR web app → same-origin framing only.
FRAMEABLE_PREFIXES = ("/auth/esign/public/",)


class SecurityHeadersMiddleware:
    """Pure ASGI: security headers on every API answer (incl. 401/403/413/429/500 made by middleware).
    Headers the route already set win. Cache-Control: no-store unless the route set one (payslips, PII).
    HSTS from LEXORA_HSTS (production: "max-age=31536000; includeSubDomains") — same knob as the web edge."""

    def __init__(self, app, hsts: str | None = None):
        self.app = app
        h = (os.getenv("LEXORA_HSTS") if hsts is None else hsts) or ""
        self.hsts = h.strip().encode("latin-1") if h.strip() else b""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        frameable = path.startswith(FRAMEABLE_PREFIXES)

        async def _send(msg):
            if msg["type"] == "http.response.start":
                hdrs = list(msg.get("headers", []))
                have = {k.lower() for k, _ in hdrs}
                for k, v in API_SECURITY_HEADERS:
                    if k not in have:
                        hdrs.append((k, v))
                if b"x-frame-options" not in have:
                    hdrs.append((b"x-frame-options", b"SAMEORIGIN" if frameable else b"DENY"))
                ctype = next((v for k, v in hdrs if k.lower() == b"content-type"), b"").lower()
                textual = not ctype or ctype.startswith((b"application/json", b"text/", b"application/problem"))
                if b"content-security-policy" not in have:
                    if frameable:
                        hdrs.append((b"content-security-policy", b"frame-ancestors 'self'"))
                    elif textual:
                        hdrs.append((b"content-security-policy", API_CSP))
                if b"cache-control" not in have:
                    hdrs.append((b"cache-control", b"no-store"))
                if self.hsts and b"strict-transport-security" not in have:
                    hdrs.append((b"strict-transport-security", self.hsts))
                hdrs = [(k, v) for k, v in hdrs if k.lower() not in (b"server", b"x-powered-by")]
                msg["headers"] = hdrs
            await send(msg)

        return await self.app(scope, receive, _send)


# ── PII redaction ─────────────────────────────────────────────────────────────

_SENSITIVE_KEYS = (
    r"sin|sin_number|social_insurance_number|bank_?account|account_?(?:no|number)|acct_?no|transit|institution"
    r"|salary|wage|gross|net_?pay|base_?pay|take_?home|monthly_?(?:salary|pay)|annual_?(?:salary|pay)|hourly_?rate"
    r"|password|passwd|secret|token|api_?key|authorization|otp"
)
# key=value, key: value, "key": value, 'key': 'value'
_KV_RE = re.compile(
    r"""(?P<k>["']?\b(?:%s)\b["']?\s*[:=]\s*)(?P<v>"[^"]*"|'[^']*'|[^\s,;&}\]]+)""" % _SENSITIVE_KEYS, re.I)
_SIN_RE = re.compile(r"\b[0-9]{3}[ -][0-9]{3}[ -][0-9]{3}\b")           # SIN written 046 454 286 / 046-454-286
_LONG_DIGITS_RE = re.compile(r"(?<![\w.:-])[0-9]{7,18}(?![\w.:-])")       # SIN, bank a/c, phone
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


def redact(text: str) -> str:
    """Mask Canadian personal identifiers (SIN, bank account) and money/credential fields in a log string."""
    if not text:
        return text
    # protect UUIDs (ids are needed to debug and are not personal data on their own)
    uuids: list[str] = []

    def _keep(m):
        uuids.append(m.group(0))
        return f"\x00{len(uuids) - 1}\x00"

    s = _UUID_RE.sub(_keep, text)
    s = _BEARER_RE.sub("Bearer ***", s)
    s = _JWT_RE.sub("***jwt***", s)
    s = _KV_RE.sub(lambda m: m.group("k") + "***", s)
    s = _SIN_RE.sub("***SIN***", s)
    s = _LONG_DIGITS_RE.sub("***NUM***", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: uuids[int(m.group(1))], s)


class PIIRedactingFilter(logging.Filter):
    """Rewrites record.msg with args already merged, so a formatter never sees raw PII."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:                                   # bad format args — keep the raw template
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


# ── JSON logs + request id ────────────────────────────────────────────────────

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("lexora_request_id", default="")
_RID_OK = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_STD_ATTRS = set(vars(logging.LogRecord("x", 0, "x", 0, "x", None, None))) | {"message", "asctime", "color_message"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            out["request_id"] = rid
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and not k.startswith("_") and k not in out:
                out[k] = v if isinstance(v, (int, float, bool)) or v is None else redact(str(v))
        if record.exc_text:
            out["exc"] = record.exc_text
        return json.dumps(out, ensure_ascii=False, default=str)


def configure_logging(service: str) -> None:
    level = (os.getenv("LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    if (os.getenv("LEXORA_LOG_FORMAT") or "json").lower() == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    else:
        handler.setFormatter(JsonFormatter(service))
    handler.addFilter(PIIRedactingFilter())
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_lexora", False):
            root.removeHandler(h)
    handler._lexora = True                                  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn's own handlers would print unredacted, non-JSON lines; route them through root.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = name != "uvicorn.access"            # we write our own access line
    logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs incl. query strings


class RequestContextMiddleware:
    """Pure ASGI: request id in/out + one JSON access line per request (path only — no
    query string, which can carry a SIN / names from search boxes)."""

    def __init__(self, app, service: str):
        self.app = app
        self.service = service
        self.log = logging.getLogger("lexora.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = ""
        for k, v in scope.get("headers", []):
            if k == b"x-request-id":
                cand = v.decode("latin-1").strip()
                rid = cand if _RID_OK.match(cand) else ""
                break
        rid = rid or uuid.uuid4().hex
        scope["headers"] = [(k, v) for k, v in scope.get("headers", []) if k != b"x-request-id"] \
            + [(b"x-request-id", rid.encode())]
        token = request_id_var.set(rid)
        started = time.perf_counter()
        status = {"code": 500}

        async def _send(msg):
            if msg["type"] == "http.response.start":
                status["code"] = msg["status"]
                hdrs = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"x-request-id"]
                hdrs.append((b"x-request-id", rid.encode()))
                msg["headers"] = hdrs
            await send(msg)

        try:
            await self.app(scope, receive, _send)
        finally:
            path = scope.get("path", "")
            if path not in ("/health", "/ready"):
                self.log.info("%s %s %s", scope.get("method"), path, status["code"],
                              extra={"method": scope.get("method"), "path": path, "status": status["code"],
                                     "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            request_id_var.reset(token)


class BodySizeLimitMiddleware:
    """413 for a request body above ``max_bytes`` — by Content-Length up front, and by
    counting streamed chunks for chunked uploads."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        return await _send_413(send, self.max_bytes)
                except ValueError:
                    return await _send_json(send, 400, {"detail": "Bad Content-Length"})
        seen = {"n": 0, "over": False}

        async def _receive():
            msg = await receive()
            if msg["type"] == "http.request":
                seen["n"] += len(msg.get("body", b""))
                if seen["n"] > self.max_bytes:
                    seen["over"] = True
                    raise _TooLarge()
            return msg

        started = {"v": False}

        async def _send(msg):
            if msg["type"] == "http.response.start":
                started["v"] = True
            await send(msg)

        try:
            await self.app(scope, _receive, _send)
        except _TooLarge:
            if not started["v"]:
                await _send_413(send, self.max_bytes)


class _TooLarge(Exception):
    pass


async def _send_json(send, status: int, body: dict):
    raw = json.dumps(body).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]})
    await send({"type": "http.response.body", "body": raw})


async def _send_413(send, max_bytes: int):
    await _send_json(send, 413, {"detail": f"Request body too large (limit {max_bytes // (1024 * 1024)} MB)"})


# ── readiness ────────────────────────────────────────────────────────────────

async def _check_db(timeout: float) -> str:
    url = os.getenv("DATABASE_URL") or ""
    if not url.startswith("postgresql"):
        return "skipped"                                   # sqlite in unit tests
    import asyncpg                                         # in requirements
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"), timeout=timeout)
    try:
        await conn.fetchval("SELECT 1")
    finally:
        await conn.close()
    return "ok"


async def _check_redis(timeout: float) -> str:
    url = os.getenv("REDIS_URL") or ""
    if not url:
        return "skipped"
    import redis.asyncio as aioredis                       # in requirements
    r = aioredis.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout)
    try:
        await r.ping()
    finally:
        await r.aclose()
    return "ok"


DOC_PATHS = ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json")


def api_docs_enabled() -> bool:
    """Swagger / ReDoc / openapi.json: OFF by default (WASA "API endpoint disclosure").
    LEXORA_API_DOCS=1 turns them on for a developer machine; never in production."""
    if is_production():
        return False
    return (os.getenv("LEXORA_API_DOCS") or "").strip().lower() in ("1", "true", "yes", "on")


def drop_api_docs(app) -> None:
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in DOC_PATHS]
    app.openapi_url = app.docs_url = app.redoc_url = None


def error_ref() -> str:
    """Short reference shown to the user and written to the log next to the real error."""
    rid = request_id_var.get()
    return (rid[:8] if rid else uuid.uuid4().hex[:8]).upper()


_UUID_TEXT = ("badly formed hexadecimal uuid string", "invalid uuid", "invalid input syntax for type uuid",
              "invalid input for query argument")


def is_client_input_error(exc: BaseException) -> bool:
    """True for errors caused by a malformed value the caller sent: uuid.UUID("not-a-uuid"), a database DataError
    (SQLSTATE class 22: invalid text representation, numeric out of range, bad date …) or asyncpg refusing to
    encode a query argument. Never true for integrity / connection / programming errors."""
    seen = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        name = type(e).__name__
        if name == "DataError":                                   # sqlalchemy.exc.DataError / asyncpg DataError
            return True
        state = str(getattr(e, "sqlstate", "") or getattr(e, "pgcode", "") or "")
        if state.startswith("22"):
            return True
        if isinstance(e, ValueError) and any(t in str(e).lower() for t in _UUID_TEXT):
            return True
        e = getattr(e, "orig", None) or e.__cause__ or e.__context__
    return False


def install_error_masking(app, service: str) -> None:
    """Any unhandled exception → 500 {"detail": fixed sentence, "ref": "<8 chars>"}; the traceback goes to
    the log under the same ref. No class name, message, SQL or file path ever reaches the client."""
    from fastapi.responses import JSONResponse

    log = logging.getLogger(f"lexora.{service}.errors")

    async def _masked(request, exc):  # noqa: ANN001
        ref = error_ref()
        if is_client_input_error(exc):
            # WASA follow-up (2026-10-24): a malformed id / value the database cannot take is the CALLER's mistake —
            # 422 with a code, never a 500 (and never the database text).
            log.info("bad input %s on %s %s ref=%s", type(exc).__name__, request.method, request.url.path, ref)
            return JSONResponse({"detail": "A value in this request is not valid (for example a malformed id).",
                                 "code": "INVALID_INPUT", "ref": ref}, status_code=422)
        log.error("unhandled %s on %s %s ref=%s", type(exc).__name__, request.method, request.url.path, ref,
                  exc_info=exc)
        return JSONResponse({"detail": "Something went wrong on our side. Please try again; if it keeps "
                                       "happening, give this reference to support.", "ref": ref},
                            status_code=500)

    app.add_exception_handler(Exception, _masked)


def _front(app, path: str) -> None:
    """Move the route(s) for `path` to the front so catch-alls (gateway) can't shadow them."""
    routes = app.router.routes
    mine = [r for r in routes if getattr(r, "path", None) == path]
    for r in mine:
        routes.remove(r)
    routes[0:0] = mine


def install(app, service: str, *, check_db: bool = True, check_redis: bool = True) -> None:
    """Wire production runtime into a FastAPI app. Call once, at the end of main.py."""
    configure_logging(service)
    enforce_production_config(service)

    from fastapi.responses import JSONResponse

    if not any(getattr(r, "path", None) == "/health" for r in app.router.routes):
        @app.get("/health", include_in_schema=False)
        async def _health():
            return {"status": "ok", "service": service}

    timeout = float(os.getenv("LEXORA_READY_TIMEOUT_SEC", "2"))

    @app.get("/ready", include_in_schema=False)
    async def _ready():
        checks: dict[str, str] = {}
        ok = True
        for name, enabled, fn in (("db", check_db, _check_db), ("redis", check_redis, _check_redis)):
            if not enabled:
                continue
            try:
                checks[name] = await fn(timeout)
            except Exception as exc:                        # noqa: BLE001 — report, don't crash the probe
                ok = False
                checks[name] = "down"
                logging.getLogger("lexora.ops").warning("readiness: %s unreachable (%s)", name, type(exc).__name__)
        return JSONResponse({"status": "ready" if ok else "not_ready", "service": service, "checks": checks},
                            status_code=200 if ok else 503)

    _front(app, "/ready")
    _front(app, "/health")

    # WASA hardening 2026-10-21: no API documentation on a running stack unless asked for
    # (LEXORA_API_DOCS=1, development only), and no exception text in a 500 answer.
    if not api_docs_enabled():
        drop_api_docs(app)
    install_error_masking(app, service)
    # 2026-10-25 time-zone rule: instants leave the API as UTC WITH an offset (naive = UTC).
    from shared.utils.org_time import install_utc_json
    install_utc_json()

    if service == "api-gateway":
        mb = float(os.getenv("LEXORA_MAX_BODY_MB", "25"))
        app.add_middleware(BodySizeLimitMiddleware, max_bytes=int(mb * 1024 * 1024))
        # go-live 2026-10-24: security headers on every API answer (the edge adds its own too).
        app.add_middleware(SecurityHeadersMiddleware)
    if service == "auth" and is_production():
        _install_demo_data_guard(app, service)
    # added last = outermost: every response (incl. 413 / CORS / auth errors) gets the id.
    app.add_middleware(RequestContextMiddleware, service=service)
