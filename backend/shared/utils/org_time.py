"""Lexora HRM — the org's business "today" (Eastern time by default, per province where known).

Stored timestamps stay UTC. Only BUSINESS DATES (attendance day, leave day, "due
today", month-to-date, the current pay month, age/tenure "as of today") follow the
org's wall clock. Without this, a server running in UTC thinks it is already
tomorrow between 19:00 / 20:00 and midnight Eastern time.

    from shared.utils.org_time import org_today, org_now, org_month_bounds, local_today

    today = await org_today(org_id)            # date in the org's time zone
    now   = await org_now(org_id)              # aware datetime in the org's zone
    first, last = await org_month_bounds(org_id)          # this month, org-local
    first, last = month_bounds(some_date)                  # any month (pure)
    today = local_today()                      # sync, no org at hand: America/Toronto
    tz    = province_tz("BC")                  # an employee's province → its IANA zone

The org's zone is the company setting ``locale.timezone`` (default America/Toronto).
Every function here is safe to call anywhere: a missing setting, a bad name, a
database error or a machine without tz data all fall back to Eastern time (a fixed
UTC-05:00 when no tz database is installed) — never to UTC.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timedelta, timezone, tzinfo

log = logging.getLogger("lexora.time")

DEFAULT_TZ = "America/Toronto"
EASTERN_FIXED = timezone(timedelta(hours=-5), "EST")

# Province / territory of employment → the IANA zone of its main business centre. A province
# that spans zones (ON west of 90°W, BC's Peace River, NU, QC Magdalen Islands …) uses the
# zone of most employers; an org sets `locale.timezone` explicitly when that is wrong for it.
PROVINCE_TZ = {
    "NL": "America/St_Johns", "NS": "America/Halifax", "PE": "America/Halifax", "NB": "America/Moncton",
    "QC": "America/Toronto", "ON": "America/Toronto", "MB": "America/Winnipeg", "SK": "America/Regina",
    "AB": "America/Edmonton", "BC": "America/Vancouver", "YT": "America/Whitehorse",
    "NT": "America/Yellowknife", "NU": "America/Iqaluit",
}

_tz_cache: dict[str, tzinfo] = {}


def valid_timezone(name: str | None) -> bool:
    if not name:
        return False
    if name == DEFAULT_TZ:
        return True
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:                                         # noqa: BLE001
        return False


def tz_for(name: str | None = None) -> tzinfo:
    """tzinfo for an IANA name; America/Toronto (or fixed UTC-05:00) when unknown."""
    name = (name or DEFAULT_TZ).strip() or DEFAULT_TZ
    hit = _tz_cache.get(name)
    if hit is not None:
        return hit
    try:
        from zoneinfo import ZoneInfo
        z: tzinfo = ZoneInfo(name)
    except Exception:                                         # noqa: BLE001 — no tzdata / bad name
        if name != DEFAULT_TZ:
            log.warning("unknown time zone %r — using Eastern time", name)
            z = tz_for(DEFAULT_TZ)
        else:
            z = EASTERN_FIXED
    _tz_cache[name] = z
    return z


def province_tz(province: str | None) -> str:
    """IANA zone name for a Canadian province / territory code (default America/Toronto)."""
    return PROVINCE_TZ.get((province or "").strip().upper(), DEFAULT_TZ)


def _utcnow() -> datetime:
    """Single seam for tests (monkeypatch this)."""
    return datetime.now(timezone.utc)


# ── instants: always UTC, always with an offset ─────────────────────────────────
def now_utc() -> datetime:
    """The current instant, timezone-aware UTC. Use for every stored timestamp."""
    return _utcnow()


def as_utc(dt: datetime | None) -> datetime | None:
    """A stored instant as aware UTC. NAIVE values (old rows, SQLite, a client that sent no
    offset) are taken to BE UTC — that is the house rule for every timestamp column."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime | None) -> str | None:
    """ISO-8601 with an explicit +00:00 offset (browsers then show it in the viewer's zone)."""
    d = as_utc(dt)
    return d.isoformat() if d else None


def to_org_tz(dt: datetime | None, tz: str | tzinfo | None = None) -> datetime | None:
    """A UTC instant on the org's wall clock (default Eastern) — for text that must name a local
    time (e-mails, PDFs, file headers). Naive input is treated as UTC."""
    d = as_utc(dt)
    return local_now(tz, at=d) if d else None


def today_for_org(tz: str | tzinfo | None = None, *, at: datetime | None = None) -> date:
    """Sync form of the org's 'today' when the zone name is already at hand (default Eastern)."""
    return local_today(tz, at=at)


_json_installed = False


def install_utc_json() -> None:
    """Make FastAPI's JSON encoder emit every datetime with an offset: naive ones are UTC by the
    house rule, so ``2026-10-25T04:00:00`` goes out as ``2026-10-25T04:00:00+00:00`` and a browser
    never mistakes it for local time. Called once per service from ``shared.ops_runtime.install``.
    (Pydantic response models with naive fields are not covered — keep columns timezone-aware.)"""
    global _json_installed
    if _json_installed:
        return
    try:
        from fastapi import encoders
        encoders.ENCODERS_BY_TYPE[datetime] = lambda d: as_utc(d).isoformat()
        _json_installed = True
    except Exception as e:                                    # noqa: BLE001 — never block boot
        log.warning("install_utc_json skipped: %s", e)


# ── sync: no org needed (default zone) ──────────────────────────────────────────
def local_now(tz: str | tzinfo | None = None, *, at: datetime | None = None) -> datetime:
    """Aware 'now' in ``tz`` (default Eastern). ``at`` = a UTC instant to convert instead."""
    z = tz if isinstance(tz, tzinfo) else tz_for(tz)
    t = at if at is not None else _utcnow()
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(z)


def local_today(tz: str | tzinfo | None = None, *, at: datetime | None = None) -> date:
    """Business date in ``tz`` (default Eastern). Use where no org id is at hand."""
    return local_now(tz, at=at).date()


def to_local_date(ts: datetime | None, tz: str | tzinfo | None = None) -> date | None:
    """The business date a stored UTC timestamp falls on (e.g. a punch at 02:00 UTC = the evening before in Toronto)."""
    if ts is None:
        return None
    return local_today(tz, at=ts)


def month_bounds(d: date | None = None) -> tuple[date, date]:
    """(first day, last day) of the month containing ``d`` (default: today, Eastern)."""
    d = d or local_today()
    return d.replace(day=1), d.replace(day=calendar.monthrange(d.year, d.month)[1])


def day_bounds_utc(d: date, tz: str | tzinfo | None = None) -> tuple[datetime, datetime]:
    """[start, end) of local day ``d`` as UTC instants — for filtering UTC timestamp columns."""
    z = tz if isinstance(tz, tzinfo) else tz_for(tz)
    start = datetime(d.year, d.month, d.day, tzinfo=z)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


# ── async: per-org zone from company settings ───────────────────────────────────
async def org_tz(org_id) -> tzinfo:
    """The org's tzinfo (setting ``locale.timezone``; default America/Toronto). Never raises."""
    if not org_id:
        return tz_for(DEFAULT_TZ)
    try:
        from shared.utils.org_settings import get_setting
        name = await get_setting(str(org_id), "locale.timezone", DEFAULT_TZ)
    except Exception as e:                                    # noqa: BLE001
        log.warning("org_tz(%s) failed: %s — using Eastern time", org_id, e)
        name = DEFAULT_TZ
    return tz_for(name if isinstance(name, str) else DEFAULT_TZ)


async def org_tz_name(org_id) -> str:
    """The org's IANA zone NAME (for SQL ``AT TIME ZONE`` and the browser); America/Toronto if unknown."""
    z = await org_tz(org_id)
    return getattr(z, "key", None) or DEFAULT_TZ


async def org_now(org_id) -> datetime:
    """Aware datetime 'now' on the org's wall clock."""
    return local_now(await org_tz(org_id))


async def org_today(org_id) -> date:
    """The org's business date today."""
    return local_today(await org_tz(org_id))


async def org_month_bounds(org_id, d: date | None = None) -> tuple[date, date]:
    """(first, last) of the month containing ``d`` (default: the org's today)."""
    return month_bounds(d or await org_today(org_id))
