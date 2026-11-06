"""G3 (2026-10-23) — biometric devices PUSH punches straight into Lexora HRM, live; device health + offline alerts.

Three device protocols, all reaching the time service through the gateway with NO login (a device cannot do
our sign-in), so every route here is tightly scoped:

  ZKTeco / eSSL "ADMS" (iclock push)                 — gateway service "iclock" → this service
      GET  /iclock/cdata?SN=<serial>&options=all      handshake: we answer the upload options
      POST /iclock/cdata?SN=<serial>&table=ATTLOG     punch lines "PIN<TAB>YYYY-MM-DD HH:MM:SS<TAB>status<TAB>verify…"
                                                      → "OK: <n>"; any other table (OPERLOG, ATTPHOTO, BIODATA,
                                                      fingerprint / face templates) is acknowledged and DISCARDED —
                                                      we never store biometric templates or photos.
      GET  /iclock/getrequest?SN=<serial>[&INFO=…]    heartbeat / command poll → "OK" (we never queue commands)
      POST /iclock/devicecmd?SN=<serial>              command result → "OK"
      Same three under /time/device-push/adms/{token}/iclock/… for a device (or a site proxy) that can put a
      path in front of /iclock — then the per-device token is the credential.
  Matrix COSEC "Devices PUSH API" (Third Party Server mode)
      ServerURL on the device = <Lexora HRM>/time/device-push/cosec/{token}
      GET|POST …/{token}/login | poll | getcmd | updatecmd | setevent (+ any other op → "status=1")
      setevent evt_id 101 ("User Allowed") = a punch; every other event is acknowledged and ignored.
  Generic HTTP push (vendor cloud / middleware)
      POST /time/device-push/http/{token}   {"serial", "device_time"?, "punches": [{"emp_code","at","direction"?}]}

Protocol sources (public; no official ZKTeco PDF was reachable, so the ADMS shape follows the published
open-source servers that interoperate with ZKTeco / eSSL terminals — re-check against your firmware):
  * ZKTeco ADMS endpoints, handshake GET /iclock/cdata?SN=&options=all, ATTLOG "PIN⇥time⇥status⇥verify" →
    "OK: 1", getrequest poll, devicecmd — https://packagist.org/packages/tanemrahman/zkteco-adms
    (README, 2026) and https://ecosire.com/apps/odoo/odoo-adms-push-attendance (ZKTeco / eSSL / Anviz share it).
  * Handshake option keys (ATTLOGStamp, ErrorDelay, Delay, TransTimes, TransInterval, TransFlag, Realtime,
    Encrypt) — ZKTeco "Attendance PUSH Communication Protocol" as reproduced by those servers; TransFlag is
    set to "TransData AttLog" so the terminal uploads attendance only (no OPERLOG / user / template data).
  * ZK status codes 0 check-in, 1 check-out, 2 break-out, 3 break-in, 4 OT-in, 5 OT-out (same convention as
    the CSV import in biometric_import.py); 255 / blank = the terminal did not ask → treated as "no direction".
  * Matrix COSEC: "COSEC Devices PUSH API User Guide" v1 (11 Jul 2019),
    https://www.marketing.matrixcomsec.com/wp-content/uploads/2023/08/COSEC_DEVICES_PUSH_API_GUIDE.pdf —
    ch.2 login (device-type, serial-no → poll-interval/poll-duration/poll-count/status/format), poll (cmd-avlbl,
    cnfg-avlbl, status), ch.5 setevent (seq-no, evt_id, date-dd/mm/yyyy, time-hh/mm/ss, field-1..5), appendix
    event 101 "User Allowed" with field-1 = numeric user id and field-5 = alphanumeric user id "if available".
    The guide's Entry/Exit column is not unambiguous in the text, so direction is NOT read from COSEC fields:
    the device's own setting (alternate / always in / always out) decides.

Security (devices are unauthenticated-by-login — user rule "don't compromise on security"):
  * The ORG always comes from our device registration (ta_device_push → ta_biometric_devices), never from the
    request. A serial can be registered for push by ONE organisation only (global unique `serial_key`).
  * ADMS on the plain /iclock path: registered + enabled serial AND the caller's address inside the device's
    IP allow-list (CIDR list). No allow-list → the plain path is refused; the token path must be used.
    Token path / COSEC / HTTP: per-device token (shown once, stored as SHA-256) AND the serial must match;
    the IP allow-list is enforced too when it is set.
  * Unknown / refused callers get the same short "Forbidden" (no hint which check failed); after 30 refusals in
    10 minutes an address is locked out for 10 minutes before any database read. Per-address and per-serial
    rate limits on every call; bodies capped (ATTLOG 4 MB / 20,000 lines, COSEC/HTTP 256 KB / 2,000 punches).
  * No data read-back: answers are "OK", counts, or the protocol's fixed option lines — never a name, an
    employee list, a template or another device's data. We never send device commands.

Live sync: every accepted punch recomputes that employee-day with the module's own `_recompute_day`
(the same path as the time clock and the CSV import), so Attendance / payroll feed are current at once.
Guards: duplicate (same minute + direction, or an exact resend), bounce (another punch by the same person within
`bounce_sec`, default 60 s — a double tap), absurd future (> 24 h ahead of our clock), too old (> `max_backlog_days`),
and out-of-order backlog (accepted, counted, and "auto" directions of that day re-derived in time order).

Device health: heartbeat (`last_seen_at`), last punch, punches today, clock drift (exact from "device_time" on
HTTP push; ESTIMATED from real-time punches on ADMS / COSEC), last address, firmware. A background watcher marks a
device offline after `offline_after_min` (default 30) and notifies the configured admins once (in-app + e-mail
through shared.utils.notify); back online → an in-app note and the alert re-arms.

HR routes:  GET  /time/biometric/health                      the device health panel
            GET  /time/biometric/devices/{id}/push            push settings (token never returned)
            PUT  /time/biometric/devices/{id}/push            push settings (protocol, allow-list, alerts …)
            POST /time/biometric/devices/{id}/push/token      new token — shown ONCE
            POST /time/biometric/health/check                 run the offline check now
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import sys
import time as _time
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import Boolean, Date, DateTime, Integer, String, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from database import Base, get_db  # noqa: E402
from models import Punch  # noqa: E402
from shared.utils.org_time import to_local_date  # noqa: E402
from shared.utils.ratelimit import client_ip, make_limiter, make_login_limiter  # noqa: E402
from shared.utils.entitlements import org_module_refusal  # noqa: E402

from biometric_import import (DEFAULT_TZ, BiometricDevice, BiometricImport, _OrgShim, norm_code,  # noqa: E402
                              parse_datetime, to_utc, webhook_events)

log = logging.getLogger("lexora.time.device_push")

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "hr_staff", "branch_admin"}
PROTOCOLS = ("adms", "cosec", "http")
DIRECTION_MODES = ("device", "alternate", "in", "out")
COSEC_DEVICE_TYPES = {"0", "1", "2", "3", "5", "7"}

ATTLOG_MAX_BYTES = 4 * 1024 * 1024
ATTLOG_MAX_LINES = 20000
SMALL_MAX_BYTES = 256 * 1024
SMALL_MAX_PUNCHES = 2000
FUTURE_REJECT = timedelta(hours=24)
HEARTBEAT_WRITE_EVERY = timedelta(seconds=30)
WATCH_SECONDS = int(os.getenv("LEXORA_DEVICE_WATCH_SECONDS", "60") or 60)

# Rate limits (Redis-shared when REDIS_URL is set). A busy office NAT with ~20 terminals polling every 30 s
# stays well inside the per-address budget; a single serial gets 300 calls / 10 min.
ip_limiter = make_limiter(1200, 600, name="devpush_ip")
serial_limiter = make_limiter(300, 600, name="devpush_serial")
refusals = make_login_limiter(30, 600, 600, name="devpush_refused")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def err(status: int, code: str, message: str) -> HTTPException:
    """Same shape as the other Lexora HRM error codes: {code, message} — code is registered in docs."""
    return HTTPException(status, {"code": code, "message": message})


# ── table ─────────────────────────────────────────────────────────────────────
class DevicePush(Base):
    """Push settings + live health of ONE registered device (1:1 with ta_biometric_devices)."""
    __tablename__ = "ta_device_push"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    device_row_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, nullable=False)
    serial_key: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)   # UPPER(serial) — one org only
    protocol: Mapped[str] = mapped_column(String(10), default="adms")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    token_hint: Mapped[str | None] = mapped_column(String(8))
    allowed_ips: Mapped[list] = mapped_column(JSONB, default=list)
    direction_mode: Mapped[str] = mapped_column(String(10), default="device")
    bounce_sec: Mapped[int] = mapped_column(Integer, default=60)
    max_backlog_days: Mapped[int] = mapped_column(Integer, default=45)
    offline_after_min: Mapped[int] = mapped_column(Integer, default=30)
    alert_user_ids: Mapped[list] = mapped_column(JSONB, default=list)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_punch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))
    firmware: Mapped[str | None] = mapped_column(String(80))
    device_info: Mapped[dict] = mapped_column(JSONB, default=dict)
    clock_drift_sec: Mapped[int | None] = mapped_column(Integer)
    drift_source: Mapped[str | None] = mapped_column(String(10))       # exact | estimate
    drift_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    punches_day: Mapped[date | None] = mapped_column(Date)
    punches_today: Mapped[int] = mapped_column(Integer, default=0)
    last_batch: Mapped[dict] = mapped_column(JSONB, default=dict)
    offline_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_day: Mapped[date | None] = mapped_column(Date)
    rejects_today: Mapped[int] = mapped_column(Integer, default=0)
    last_reject: Mapped[str | None] = mapped_column(String(120))
    last_reject_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ── pure helpers (unit-tested in test_device_push.py) ─────────────────────────
SERIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{2,39}$")
TOKEN_RE = re.compile(r"^dp_[A-Za-z0-9_\-]{20,60}$")


def valid_serial(sn) -> bool:
    return bool(sn) and bool(SERIAL_RE.match(str(sn)))


def serial_key(sn: str) -> str:
    return str(sn or "").strip().upper()


def new_token() -> str:
    return "dp_" + secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()


def token_ok(token: str, stored_hash: str | None) -> bool:
    if not stored_hash or not token or not TOKEN_RE.match(token):
        return False
    return hmac.compare_digest(hash_token(token), stored_hash)


def clean_cidrs(values) -> list[str]:
    """Validate an allow-list (single addresses or CIDR). Raises ValueError on a bad entry."""
    out = []
    for v in values or []:
        s = str(v or "").strip()
        if not s:
            continue
        net = ipaddress.ip_network(s, strict=False)
        if net.prefixlen == 0:
            raise ValueError("0.0.0.0/0 would allow the whole internet")
        out.append(str(net))
    return out[:50]


def ip_allowed(ip: str, cidrs) -> bool:
    if not ip or not cidrs:
        return False
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    for c in cidrs:
        try:
            if addr in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False


ZK_IN, ZK_OUT = {"0", "3", "4"}, {"1", "2", "5"}


def zk_direction(status) -> str | None:
    s = str(status if status is not None else "").strip()
    if s in ZK_IN:
        return "in"
    if s in ZK_OUT:
        return "out"
    return None


def parse_attlog(text: str) -> tuple[list[dict], list[str]]:
    """ADMS ATTLOG body → events [{code, at (naive device-local), direction|None}] + line errors."""
    events, errors = [], []
    for n, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip("\r\n")
        if not line.strip():
            continue
        if n > ATTLOG_MAX_LINES:
            errors.append(f"more than {ATTLOG_MAX_LINES} lines — rest ignored")
            break
        parts = line.split("\t") if "\t" in line else re.split(r"\s{2,}", line.strip())
        if len(parts) < 2:
            errors.append(f"line {n}: unreadable")
            continue
        pin, ts = parts[0].strip(), parts[1].strip()
        at = parse_datetime(ts)
        if not pin or not re.match(r"^[A-Za-z0-9_\-]{1,24}$", pin) or at is None:
            errors.append(f"line {n}: bad PIN or time")
            continue
        events.append({"code": norm_code(pin), "at": at.replace(microsecond=0),
                       "direction": zk_direction(parts[2] if len(parts) > 2 else None)})
    return events, errors


def adms_options(sn: str, attlog_stamp: str | None) -> str:
    """Handshake answer: upload attendance only (TransFlag), real-time, poll every 30 s."""
    return "\n".join([
        f"GET OPTION FROM: {sn}",
        f"ATTLOGStamp={attlog_stamp or 'None'}",
        "OPERLOGStamp=9999",
        "ATTPHOTOStamp=None",
        "ErrorDelay=60",
        "Delay=30",
        "TransTimes=00:00;14:05",
        "TransInterval=1",
        "TransFlag=TransData AttLog",
        "Realtime=1",
        "Encrypt=None",
    ]) + "\n"


def parse_adms_info(info: str | None) -> dict:
    """getrequest INFO= "<firmware>,<users>,<fingerprints>,<punches>,<ip>,…" — keep firmware + counts only."""
    if not info:
        return {}
    parts = [p.strip() for p in str(info).split(",")]
    out: dict = {}
    if parts and parts[0]:
        out["firmware"] = parts[0][:80]
    for i, k in ((1, "users"), (3, "attlogs")):
        if len(parts) > i and parts[i].isdigit():
            out[k] = int(parts[i])
    return out


def parse_cosec_event(params) -> dict | None:
    """setevent query → a punch event, or None for a non-punch event (only 101 "User Allowed" counts)."""
    if str(params.get("evt_id") or params.get("evt-id") or "").strip() != "101":
        return None
    uid = str(params.get("field-5") or "").strip()
    if not uid or not re.match(r"^[A-Za-z0-9_\-]{1,24}$", uid):
        uid = str(params.get("field-1") or "").strip()
    if not uid or uid == "0" or not re.match(r"^[A-Za-z0-9_\-]{1,24}$", uid):
        return None                                         # 0 = exit-switch (REX) / not identified
    try:
        at = datetime(int(params.get("date-yyyy")), int(params.get("date-mm")), int(params.get("date-dd")),
                      int(params.get("time-hh")), int(params.get("time-mm")), int(params.get("time-ss") or 0))
    except (TypeError, ValueError):
        return None
    return {"code": norm_code(uid), "at": at, "direction": None, "seq": str(params.get("seq-no") or "")[:12]}


def http_events(items) -> list[dict]:
    """Generic HTTP push items → events. Same field aliases as the legacy webhook, but a missing direction
    stays None (the device's direction mode decides; nothing is guessed here)."""
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        evs = webhook_events({"punches": [it]})
        if not evs:
            continue
        e = evs[0]
        dr = next((it[k] for k in ("direction", "Status", "status", "InOut", "inout", "type", "Type") if k in it), None)
        from biometric_import import parse_direction
        e["direction"] = parse_direction(dr) if dr is not None else None
        out.append({"code": e["code"], "at": e["at"], "direction": e["direction"]})
    return out


def apply_direction_mode(events: list[dict], mode: str) -> list[dict]:
    """device = keep the terminal's in/out (blank → auto) · alternate = auto · in / out = fixed door."""
    out = []
    for e in events:
        e = dict(e)
        if mode in ("in", "out"):
            e["direction"], e["auto"] = mode, False
        elif mode == "alternate" or not e.get("direction"):
            e["direction"], e["auto"] = None, True
        else:
            e["auto"] = False
        out.append(e)
    return out


def plan_batch(existing: list[tuple[datetime, str]], new: list[dict], bounce_sec: int) -> dict:
    """One employee-day: which of `new` ({at (UTC), direction|None, auto}) to write.
    existing = [(at UTC, direction)] already stored. Returns {accept, duplicates, bounced, out_of_order}."""
    times = [t for t, _ in existing]
    keys = {(t.replace(second=0, microsecond=0), d) for t, d in existing}
    exact = set(times)
    latest = max(times) if times else None
    accept, dup, bounced, ooo = [], 0, 0, 0
    for e in sorted(new, key=lambda x: x["at"]):
        at = e["at"]
        if at in exact or (e.get("direction") and (at.replace(second=0, microsecond=0), e["direction"]) in keys):
            dup += 1
            continue
        if bounce_sec and any(abs((at - t).total_seconds()) < bounce_sec for t in times):
            bounced += 1
            continue
        if latest and at < latest:
            ooo += 1
        accept.append(e)
        times.append(at)
        exact.add(at)
        if e.get("direction"):
            keys.add((at.replace(second=0, microsecond=0), e["direction"]))
        latest = max(latest, at) if latest else at
    return {"accept": accept, "duplicates": dup, "bounced": bounced, "out_of_order": ooo}


def derive_auto(rows: list[dict]) -> list[str]:
    """rows sorted by time: {direction, auto}. Auto rows alternate in/out; a fixed row resets the state."""
    state, out = "out", []
    for r in rows:
        if r.get("auto"):
            d = "in" if state == "out" else "out"
        else:
            d = r.get("direction") or ("in" if state == "out" else "out")
        out.append(d)
        state = "out" if d in ("out", "break_start") else "in"
    return out


def estimate_drift(device_times_utc: list[datetime], received_at: datetime) -> int | None:
    """Seconds the device clock is AHEAD (+) / behind (−), from the newest real-time punch. A punch time
    after our receipt can only be a fast clock; a little before = upload delay or a slow clock (estimate);
    much before (> 15 min) = backlog, not drift → None."""
    if not device_times_utc:
        return None
    delta = (max(device_times_utc) - received_at).total_seconds()
    if delta > FUTURE_REJECT.total_seconds() or delta < -900:
        return None
    return int(round(delta))


def device_status(enabled: bool, last_seen: datetime | None, offline_after_min: int, now: datetime) -> str:
    if not enabled:
        return "disabled"
    if not last_seen:
        return "never"
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return "offline" if now - last_seen > timedelta(minutes=max(int(offline_after_min or 30), 2)) else "online"


def cosec_text(**kv) -> str:
    return " ".join(f"{k.replace('_', '-')}={v}" for k, v in kv.items())


# ── device authentication ────────────────────────────────────────────────────
class Refused(Exception):
    def __init__(self, why: str, push: DevicePush | None = None):
        super().__init__(why)
        self.why, self.push = why, push


def _forbidden(text: bool = True):
    if text:
        return PlainTextResponse("Forbidden", status_code=403)
    return JSONResponse({"detail": "Forbidden"}, status_code=403)


async def _limit(ip: str, sn: str | None):
    """Rate limits + refusal lock-out. Returns a response to send instead, or None."""
    if refusals.retry_after(f"ip|{ip}"):
        return PlainTextResponse("Too many requests", status_code=429, headers={"Retry-After": "600"})
    wait = await ip_limiter.ahit(f"ip|{ip}")
    if not wait and sn:
        wait = await serial_limiter.ahit(f"sn|{serial_key(sn)}")
    if wait:
        return PlainTextResponse("Too many requests", status_code=429, headers={"Retry-After": str(wait)})
    return None


async def _note_refusal(db: AsyncSession, ip: str, r: Refused) -> None:
    refusals.record_failure(f"ip|{ip}")
    log.warning("device push refused: %s (ip=%s)", r.why, ip)
    p = r.push
    if p is None:
        return
    today = _now().date()
    if p.reject_day != today:
        p.reject_day, p.rejects_today = today, 0
    p.rejects_today = (p.rejects_today or 0) + 1
    p.last_reject, p.last_reject_at = r.why[:120], _now()
    try:
        await db.commit()
    except Exception:                                        # noqa: BLE001
        await db.rollback()


async def _device_for(db: AsyncSession, p: DevicePush) -> BiometricDevice:
    dev = (await db.execute(select(BiometricDevice).where(BiometricDevice.id == p.device_row_id))).scalar_one_or_none()
    if not dev or not dev.active or dev.org_id != p.org_id:
        raise Refused("device inactive", p)
    return dev


async def authenticate(db: AsyncSession, *, ip: str, protocol: str, serial: str | None,
                       token: str | None) -> tuple[DevicePush, BiometricDevice]:
    """The ONLY way a device request gets an org. Raises Refused."""
    if token is not None:
        if not TOKEN_RE.match(token or ""):
            raise Refused("bad token shape")
        p = (await db.execute(select(DevicePush).where(DevicePush.token_hash == hash_token(token)))).scalar_one_or_none()
        if not p or not token_ok(token, p.token_hash):
            raise Refused("unknown token")
        if serial is not None and serial_key(serial) != p.serial_key:
            raise Refused("serial does not match the token", p)
    else:
        if not valid_serial(serial):
            raise Refused("bad serial")
        p = (await db.execute(select(DevicePush).where(DevicePush.serial_key == serial_key(serial)))).scalar_one_or_none()
        if not p:
            raise Refused("unknown serial")
        if not p.allowed_ips:
            raise Refused("plain /iclock needs an IP allow-list (or use the token path)", p)
    if not p.enabled:
        raise Refused("push switched off", p)
    if p.protocol != protocol:
        raise Refused(f"device is set to {p.protocol}, called as {protocol}", p)
    if p.allowed_ips and not ip_allowed(ip, p.allowed_ips):
        raise Refused(f"address {ip} not in the allow-list", p)
    dev = await _device_for(db, p)
    # Integration 2026-10-23: no login → the gateway's module paywall never ran. The org bound by the
    # registration must hold the time-attendance module (active, not read-only) before a punch lands.
    why = await org_module_refusal(db, p.org_id, "time")
    if why:
        raise Refused(why, p)
    return p, dev


# ── ingest (live sync) ────────────────────────────────────────────────────────
_EMP_CACHE: dict = {}          # (org, device_id) → (expires, idx)
_HOL_CACHE: dict = {}          # org → (expires, rows)


def _shim(org: str) -> _OrgShim:
    from shared.utils.internal import internal_headers
    return _OrgShim(internal_headers(org, "time_attendance"))


async def _emp_idx(db, org: str, device_id: str) -> dict:
    from biometric_import import _emp_index
    hit = _EMP_CACHE.get((org, device_id))
    if hit and hit[0] > _time.monotonic():
        return hit[1]
    idx, _ = await _emp_index(_shim(org), db, org, device_id)
    if idx:
        _EMP_CACHE[(org, device_id)] = (_time.monotonic() + 60, idx)
    return idx


async def _holidays(org: str) -> list[dict]:
    from routers import _fetch_stat_holidays
    hit = _HOL_CACHE.get(org)
    if hit and hit[0] > _time.monotonic():
        return hit[1]
    rows = await _fetch_stat_holidays(_shim(org))
    _HOL_CACHE[org] = (_time.monotonic() + 600, rows)
    return rows


async def ingest(db: AsyncSession, p: DevicePush, dev: BiometricDevice, events: list[dict], *,
                 received_at: datetime, protocol: str, exact_drift: int | None = None) -> dict:
    """Write accepted punches, re-derive auto directions, recompute every touched employee-day NOW."""
    from routers import _recompute_day, _schedule_map
    org = str(p.org_id)
    tz = dev.timezone or DEFAULT_TZ
    stats = {"received": len(events), "written": 0, "duplicates": 0, "bounced": 0, "out_of_order": 0,
             "unmatched": 0, "future": 0, "too_old": 0, "unmatched_codes": []}
    if not events:
        return stats
    events = apply_direction_mode(events, p.direction_mode or "device")
    idx = await _emp_idx(db, org, dev.device_id)
    oldest = received_at - timedelta(days=max(int(p.max_backlog_days or 45), 1))
    groups: dict = {}
    device_utc: list[datetime] = []
    unmatched: set = set()
    for e in events:
        at = to_utc(e["at"], tz)
        device_utc.append(at)
        emp = idx.get(e["code"])
        if not emp:
            stats["unmatched"] += 1
            unmatched.add(e["code"])
            continue
        if at > received_at + FUTURE_REJECT:
            stats["future"] += 1
            continue
        if at < oldest:
            stats["too_old"] += 1
            continue
        wd = to_local_date(at, tz)
        groups.setdefault((emp["id"], wd), []).append({**e, "at": at})
    stats["unmatched_codes"] = sorted(unmatched)[:50]
    touched: dict = {}
    accepted_at: list[datetime] = []
    tag = f"device {dev.device_id}"[:180]
    for (emp_id, wd), new in groups.items():
        rows = (await db.execute(select(Punch).where(Punch.org_id == p.org_id, Punch.employee_id == uuid.UUID(emp_id),
                                                     Punch.work_date == wd))).scalars().all()
        existing = [(r.at.astimezone(timezone.utc) if r.at.tzinfo else r.at.replace(tzinfo=timezone.utc), r.direction)
                    for r in rows]
        plan = plan_batch(existing, new, int(p.bounce_sec or 0))
        for k in ("duplicates", "bounced", "out_of_order"):
            stats[k] += plan[k]
        if not plan["accept"]:
            continue
        for e in plan["accept"]:
            accepted_at.append(e["at"])
            db.add(Punch(org_id=p.org_id, employee_id=uuid.UUID(emp_id), work_date=wd, at=e["at"],
                         direction=e.get("direction") or "in", source="kiosk",
                         note=f"{tag} {'auto' if e.get('auto') else 'fixed'}", created_by=None))
            stats["written"] += 1
        touched.setdefault(emp_id, set()).add(wd)
    await db.flush()
    # out-of-order backlog or auto directions → re-derive the day's AUTO punches in time order
    for emp_id, days in touched.items():
        for wd in days:
            rows = (await db.execute(select(Punch).where(Punch.org_id == p.org_id, Punch.employee_id == uuid.UUID(emp_id),
                                                         Punch.work_date == wd).order_by(Punch.at))).scalars().all()
            seq = [{"direction": r.direction, "auto": (r.note or "").endswith(" auto")} for r in rows]
            for r, d in zip(rows, derive_auto(seq)):
                if (r.note or "").endswith(" auto") and r.direction != d:
                    r.direction = d
    if touched:
        by_emp, default = await _schedule_map(db, org)
        hol_rows = await _holidays(org)
        by_id = {v["id"]: v for v in idx.values()}
        for emp_id, days in touched.items():
            emp = by_id.get(emp_id)
            if not emp:
                continue
            holidays = {date.fromisoformat(h["holiday_date"][:10]) for h in hol_rows
                        if h.get("holiday_date") and (h.get("province") in (None, emp.get("province_of_employment")))}
            for d in sorted(days):
                await _recompute_day(db, org, emp, by_emp.get(emp_id), default, d, holidays)
    # health counters
    today = to_local_date(received_at, tz)
    if p.punches_day != today:
        p.punches_day, p.punches_today = today, 0
    p.punches_today = (p.punches_today or 0) + stats["written"]
    if accepted_at:
        newest = max(accepted_at)
        if not p.last_punch_at or newest > p.last_punch_at:
            p.last_punch_at = newest
    if exact_drift is not None:
        p.clock_drift_sec, p.drift_source, p.drift_at = exact_drift, "exact", received_at
    else:
        est = estimate_drift(device_utc, received_at)
        if est is not None:
            p.clock_drift_sec, p.drift_source, p.drift_at = est, "estimate", received_at
    p.last_batch = {k: v for k, v in stats.items() if k != "unmatched_codes"} | {"at": received_at.isoformat(),
                                                                                  "protocol": protocol}
    await _log_day(db, p, dev, stats, today)
    return stats


async def _log_day(db, p: DevicePush, dev: BiometricDevice, stats: dict, day: date) -> None:
    """One import-log row per device per day (source 'push'), counters added up — not one row per punch."""
    iso = day.isoformat()
    row = (await db.execute(select(BiometricImport).where(
        BiometricImport.org_id == p.org_id, BiometricImport.device_id == dev.device_id,
        BiometricImport.source == "push", BiometricImport.date_from == iso))).scalars().first()
    if not row:
        row = BiometricImport(org_id=p.org_id, source="push", vendor=dev.vendor, device_id=dev.device_id,
                              rows_total=0, punches_written=0, duplicates=0, unmatched=0, errors=[], unmatched_codes=[],
                              date_from=iso, date_to=iso, status="done", created_by=f"device:{dev.device_id}",
                              note="Live push — counters for the day")
        db.add(row)
    row.rows_total = (row.rows_total or 0) + stats["received"]
    row.punches_written = (row.punches_written or 0) + stats["written"]
    row.duplicates = (row.duplicates or 0) + stats["duplicates"] + stats["bounced"]
    row.unmatched = (row.unmatched or 0) + stats["unmatched"]
    codes = sorted(set(row.unmatched_codes or []) | set(stats["unmatched_codes"]))[:100]
    row.unmatched_codes = codes
    row.status = "partial" if codes else "done"


async def _seen(db, p: DevicePush, ip: str, now: datetime, info: dict | None = None) -> None:
    """Heartbeat bookkeeping (throttled writes) + back-online note."""
    back = p.offline_alerted_at is not None
    if info:
        if info.get("firmware"):
            p.firmware = info["firmware"]
        p.device_info = {**(p.device_info or {}), **{k: v for k, v in info.items() if k != "firmware"}}
    stale = not p.last_seen_at or now - (p.last_seen_at if p.last_seen_at.tzinfo else p.last_seen_at.replace(tzinfo=timezone.utc)) > HEARTBEAT_WRITE_EVERY
    if stale or back or info or p.last_ip != ip:
        p.last_seen_at, p.last_ip, p.updated_at = now, ip[:64], now
    if back:
        p.offline_alerted_at = None
        dev = (await db.execute(select(BiometricDevice).where(BiometricDevice.id == p.device_row_id))).scalar_one_or_none()
        name = dev.name if dev else p.serial_key
        await _alert(p, f"Attendance device back online: {name}",
                     "The device is sending again. Punches it kept while offline arrive on their own.", email=False)


async def _alert(p: DevicePush, title: str, body: str, *, email: bool) -> None:
    from shared.utils.notify import notify
    users = [str(u) for u in (p.alert_user_ids or []) if u]
    await notify(str(p.org_id), user_ids=users or None, roles=None if users else ["org_admin", "hr_admin"],
                 title=title, body=body, link="/biometric-import?tab=health", kind="attendance", email=email)


# ── public device routes ──────────────────────────────────────────────────────
iclock = APIRouter(prefix="/iclock", tags=["device-push"])
push = APIRouter(prefix="/time/device-push", tags=["device-push"])


async def _body(request: Request, cap: int) -> bytes | None:
    try:
        if int(request.headers.get("content-length") or 0) > cap:
            return None
    except ValueError:
        return None
    raw = await request.body()
    return raw if len(raw) <= cap else None


async def _adms(op: str, request: Request, db: AsyncSession, token: str | None):
    ip = client_ip(request)
    q = request.query_params
    sn = q.get("SN") or q.get("sn")
    limited = await _limit(ip, sn)
    if limited is not None:
        return limited
    try:
        p, dev = await authenticate(db, ip=ip, protocol="adms", serial=sn, token=token)
        if token is not None and not valid_serial(sn):
            raise Refused("bad serial", p)
    except Refused as r:
        await _note_refusal(db, ip, r)
        return _forbidden()
    now = _now()
    if op == "cdata" and request.method == "GET":
        await _seen(db, p, ip, now, {"pushver": (q.get("pushver") or "")[:20]} if q.get("pushver") else None)
        await db.commit()
        return PlainTextResponse(adms_options(sn, (p.device_info or {}).get("attlog_stamp")))
    if op == "cdata":
        table = (q.get("table") or "").upper()
        raw = await _body(request, ATTLOG_MAX_BYTES)
        if raw is None:
            return PlainTextResponse("ERROR: too large", status_code=413)
        if table != "ATTLOG":
            # OPERLOG / ATTPHOTO / BIODATA / templates: acknowledged, NOT stored (no biometric data kept)
            await _seen(db, p, ip, now)
            await db.commit()
            return PlainTextResponse("OK")
        events, errors = parse_attlog(raw.decode("utf-8", errors="replace"))
        stats = await ingest(db, p, dev, events, received_at=now, protocol="adms")
        stamp = (q.get("Stamp") or q.get("stamp") or "")[:40]
        if stamp and re.match(r"^\d{1,20}$", stamp):
            p.device_info = {**(p.device_info or {}), "attlog_stamp": stamp}
        if errors:
            p.last_batch = {**(p.last_batch or {}), "line_errors": len(errors)}
        await _seen(db, p, ip, now)
        await db.commit()
        return PlainTextResponse(f"OK: {len(events)}")
    if op == "getrequest":
        await _seen(db, p, ip, now, parse_adms_info(q.get("INFO")) or None)
        await db.commit()
        return PlainTextResponse("OK")
    # devicecmd — we never send commands; acknowledge
    await _body(request, SMALL_MAX_BYTES)
    await _seen(db, p, ip, now)
    await db.commit()
    return PlainTextResponse("OK")


@iclock.get("/cdata")
async def iclock_cdata_get(request: Request, db: AsyncSession = Depends(get_db)):
    return await _adms("cdata", request, db, None)


@iclock.post("/cdata")
async def iclock_cdata_post(request: Request, db: AsyncSession = Depends(get_db)):
    return await _adms("cdata", request, db, None)


@iclock.get("/getrequest")
async def iclock_getrequest(request: Request, db: AsyncSession = Depends(get_db)):
    return await _adms("getrequest", request, db, None)


@iclock.post("/devicecmd")
async def iclock_devicecmd(request: Request, db: AsyncSession = Depends(get_db)):
    return await _adms("devicecmd", request, db, None)


@push.get("/adms/{token}/iclock/cdata")
async def adms_token_cdata_get(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _adms("cdata", request, db, token)


@push.post("/adms/{token}/iclock/cdata")
async def adms_token_cdata_post(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _adms("cdata", request, db, token)


@push.get("/adms/{token}/iclock/getrequest")
async def adms_token_getrequest(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _adms("getrequest", request, db, token)


@push.post("/adms/{token}/iclock/devicecmd")
async def adms_token_devicecmd(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    return await _adms("devicecmd", request, db, token)


async def _cosec(op: str, token: str, request: Request, db: AsyncSession):
    ip = client_ip(request)
    params = dict(request.query_params)
    if request.method == "POST":
        raw = await _body(request, SMALL_MAX_BYTES)
        if raw is None:
            return PlainTextResponse("status=0", status_code=413)
        if raw and b"=" in raw:
            from urllib.parse import parse_qsl
            params.update({k: v for k, v in parse_qsl(raw.decode("utf-8", errors="replace"))[:60]})
    sn = params.get("serial-no")
    limited = await _limit(ip, sn)
    if limited is not None:
        return limited
    try:
        if not valid_serial(sn) or str(params.get("device-type") or "") not in COSEC_DEVICE_TYPES:
            raise Refused("bad serial-no / device-type")
        p, dev = await authenticate(db, ip=ip, protocol="cosec", serial=sn, token=token)
    except Refused as r:
        await _note_refusal(db, ip, r)
        return _forbidden()
    now = _now()
    if op == "login":
        await _seen(db, p, ip, now, {"device_type": params.get("device-type")})
        await db.commit()
        return PlainTextResponse(cosec_text(poll_interval=30, poll_duration=5, poll_count=3, status=1, format=0))
    if op == "poll":
        await _seen(db, p, ip, now)
        await db.commit()
        return PlainTextResponse(cosec_text(cmd_avlbl=0, cnfg_avlbl=0, status=1))
    if op == "setevent":
        ev = parse_cosec_event(params)
        if ev:
            await ingest(db, p, dev, [ev], received_at=now, protocol="cosec")
            if ev.get("seq"):
                p.device_info = {**(p.device_info or {}), "last_seq": ev["seq"]}
        await _seen(db, p, ip, now)
        await db.commit()
        return PlainTextResponse(cosec_text(status=1))
    await _seen(db, p, ip, now)
    await db.commit()
    return PlainTextResponse(cosec_text(cmd_avlbl=0, status=1) if op == "updatecmd" else cosec_text(status=1))


@push.get("/cosec/{token}/{op}")
async def cosec_get(token: str, op: str, request: Request, db: AsyncSession = Depends(get_db)):
    if not re.match(r"^[a-z\-]{3,20}$", op):
        return _forbidden()
    return await _cosec(op, token, request, db)


@push.post("/cosec/{token}/{op}")
async def cosec_post(token: str, op: str, request: Request, db: AsyncSession = Depends(get_db)):
    if not re.match(r"^[a-z\-]{3,20}$", op):
        return _forbidden()
    return await _cosec(op, token, request, db)


@push.post("/http/{token}")
async def http_push(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Generic JSON push. Body {serial, device_time?, punches?:[…]} — no punches = heartbeat."""
    ip = client_ip(request)
    raw = await _body(request, SMALL_MAX_BYTES)
    if raw is None:
        return JSONResponse({"detail": "Too large"}, status_code=413)
    import json as _json
    try:
        payload = _json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = None
    if not isinstance(payload, dict):
        return JSONResponse({"detail": "JSON object expected"}, status_code=400)
    sn = str(payload.get("serial") or "")
    limited = await _limit(ip, sn)
    if limited is not None:
        return limited
    try:
        if not valid_serial(sn):
            raise Refused("bad serial")
        p, dev = await authenticate(db, ip=ip, protocol="http", serial=sn, token=token)
    except Refused as r:
        await _note_refusal(db, ip, r)
        return _forbidden(text=False)
    items = payload.get("punches") or []
    if not isinstance(items, list) or len(items) > SMALL_MAX_PUNCHES:
        return JSONResponse({"detail": f"punches must be a list of at most {SMALL_MAX_PUNCHES}"}, status_code=422)
    now = _now()
    drift = None
    dt = parse_datetime(payload.get("device_time")) if payload.get("device_time") else None
    if dt is not None:
        drift = int(round((to_utc(dt, dev.timezone or DEFAULT_TZ) - now).total_seconds()))
        if abs(drift) > FUTURE_REJECT.total_seconds():
            drift = None
    events = http_events(items)
    stats = await ingest(db, p, dev, events, received_at=now, protocol="http", exact_drift=drift)
    if drift is not None and not events:
        p.clock_drift_sec, p.drift_source, p.drift_at = drift, "exact", now
    await _seen(db, p, ip, now)
    await db.commit()
    return {"ok": True, "received": stats["received"], "written": stats["written"],
            "duplicates": stats["duplicates"] + stats["bounced"], "unmatched": stats["unmatched"],
            "rejected": stats["future"] + stats["too_old"]}


# ── offline watcher ───────────────────────────────────────────────────────────
async def check_offline_once(db: AsyncSession, now: datetime | None = None, org: str | None = None) -> int:
    """Mark devices offline past their threshold and alert ONCE (atomic claim — safe with many replicas)."""
    now = now or _now()
    q = select(DevicePush).where(DevicePush.enabled.is_(True), DevicePush.last_seen_at.isnot(None),
                                 DevicePush.offline_alerted_at.is_(None))
    if org:
        q = q.where(DevicePush.org_id == uuid.UUID(org))
    sent = 0
    for p in (await db.execute(q)).scalars().all():
        if device_status(True, p.last_seen_at, p.offline_after_min, now) != "offline":
            continue
        res = await db.execute(update(DevicePush).where(DevicePush.id == p.id, DevicePush.offline_alerted_at.is_(None))
                               .values(offline_alerted_at=now))
        await db.commit()
        if res.rowcount != 1:
            continue
        dev = (await db.execute(select(BiometricDevice).where(BiometricDevice.id == p.device_row_id))).scalar_one_or_none()
        if dev is None or not dev.active:
            continue                      # switched off by HR: no "offline" alert (integration 2026-10-23)
        name = dev.name
        mins = int((now - (p.last_seen_at if p.last_seen_at.tzinfo else p.last_seen_at.replace(tzinfo=timezone.utc))).total_seconds() // 60)
        await _alert(p, f"Attendance device offline: {name}",
                     f"No contact for {mins} minutes (limit {p.offline_after_min}). Check power, network and the "
                     f"server address on the device. Punches stay on the device and arrive when it reconnects.", email=True)
        sent += 1
    return sent


async def watch_offline() -> None:
    from database import SessionLocal
    while True:
        await asyncio.sleep(WATCH_SECONDS)
        try:
            async with SessionLocal() as db:
                await check_offline_once(db)
        except asyncio.CancelledError:
            raise
        except Exception as e:                               # noqa: BLE001
            log.warning("device offline watch failed: %s", e)


# ── HR routes ─────────────────────────────────────────────────────────────────
admin = APIRouter(prefix="/time/biometric", tags=["device-health"])


def _org(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")


def _hr(request: Request) -> uuid.UUID:
    org = _org(request)
    if (request.headers.get("x-role") or "").lower() not in HR_ROLES:
        raise HTTPException(403, {"code": "TA_DEV_FORBIDDEN", "message": "Only HR can manage attendance devices"})
    return org


async def _dev(db, org: uuid.UUID, did: str) -> BiometricDevice:
    try:
        u = uuid.UUID(did)
    except ValueError:
        raise err(404, "TA_DEV_NOT_FOUND", "Device not found")
    d = (await db.execute(select(BiometricDevice).where(BiometricDevice.id == u, BiometricDevice.org_id == org))).scalar_one_or_none()
    if not d:
        raise err(404, "TA_DEV_NOT_FOUND", "Device not found")
    return d


async def _push_row(db, d: BiometricDevice) -> DevicePush | None:
    return (await db.execute(select(DevicePush).where(DevicePush.device_row_id == d.id))).scalar_one_or_none()


def _paths(p: DevicePush | None, d: BiometricDevice) -> dict:
    base = (os.getenv("LEXORA_PUBLIC_API_URL") or "").rstrip("/")
    tok = "<token>"
    return {"base": base or "(your Lexora HRM API address)",
            "adms_plain": "/iclock/cdata  (device setting: server address + port only)",
            "adms_token": f"/time/device-push/adms/{tok}/iclock/cdata",
            "cosec_server_url": f"/time/device-push/cosec/{tok}",
            "http": f"/time/device-push/http/{tok}"}


def _push_out(p: DevicePush | None, d: BiometricDevice, now: datetime) -> dict:
    # an inactive device is refused at the door, so it is shown as disabled whatever its last contact (integration 2026-10-23)
    st = device_status(bool(p and p.enabled and d.active), p.last_seen_at if p else None, p.offline_after_min if p else 30, now)
    drift = p.clock_drift_sec if p else None
    return {"device_id": str(d.id), "serial": d.device_id, "name": d.name, "vendor": d.vendor, "location": d.location,
            "timezone": d.timezone, "active": d.active, "status": st,
            "push_configured": bool(p), "enabled": bool(p and p.enabled), "protocol": p.protocol if p else None,
            "has_token": bool(p and p.token_hash), "token_hint": p.token_hint if p else None,
            "allowed_ips": list(p.allowed_ips or []) if p else [], "direction_mode": p.direction_mode if p else "device",
            "bounce_sec": p.bounce_sec if p else 60, "max_backlog_days": p.max_backlog_days if p else 45,
            "offline_after_min": p.offline_after_min if p else 30, "alert_user_ids": list(p.alert_user_ids or []) if p else [],
            "last_seen_at": p.last_seen_at.isoformat() if p and p.last_seen_at else None,
            "last_punch_at": p.last_punch_at.isoformat() if p and p.last_punch_at else None,
            "punches_today": (p.punches_today or 0) if p and p.punches_day == to_local_date(now, d.timezone or DEFAULT_TZ) else 0,
            "clock_drift_sec": drift, "drift_source": p.drift_source if p else None,
            "drift_level": None if drift is None else ("ok" if abs(drift) <= 120 else "warn" if abs(drift) <= 600 else "bad"),
            "last_ip": p.last_ip if p else None, "firmware": p.firmware if p else None,
            "last_batch": (p.last_batch or {}) if p else {},
            "offline_alerted_at": p.offline_alerted_at.isoformat() if p and p.offline_alerted_at else None,
            "rejects_today": (p.rejects_today or 0) if p and p.reject_day == now.date() else 0,
            "last_reject": p.last_reject if p else None,
            "last_reject_at": p.last_reject_at.isoformat() if p and p.last_reject_at else None,
            "paths": _paths(p, d)}


@admin.get("/health")
async def health_panel(request: Request, db: AsyncSession = Depends(get_db)):
    org = _hr(request)
    now = _now()
    devs = (await db.execute(select(BiometricDevice).where(BiometricDevice.org_id == org))).scalars().all()
    pushes = {p.device_row_id: p for p in (await db.execute(select(DevicePush).where(DevicePush.org_id == org))).scalars().all()}
    rows = [_push_out(pushes.get(d.id), d, now) for d in devs]
    count = lambda s: sum(1 for r in rows if r["status"] == s)  # noqa: E731
    return {"as_of": now.isoformat(), "devices": rows,
            "summary": {"online": count("online"), "offline": count("offline"), "never": count("never"),
                        "disabled": count("disabled"), "punches_today": sum(r["punches_today"] for r in rows),
                        "drift_warnings": sum(1 for r in rows if r["drift_level"] in ("warn", "bad"))}}


@admin.get("/devices/{did}/push")
async def get_push(did: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _hr(request)
    d = await _dev(db, org, did)
    return _push_out(await _push_row(db, d), d, _now())


class PushBody(BaseModel):
    enabled: bool = False
    protocol: str = "adms"
    allowed_ips: list[str] = []
    direction_mode: str = "device"
    bounce_sec: int = 60
    max_backlog_days: int = 45
    offline_after_min: int = 30
    alert_user_ids: list[str] = []


@admin.put("/devices/{did}/push")
async def put_push(did: str, body: PushBody, request: Request, db: AsyncSession = Depends(get_db)):
    from shared.utils import directory
    from shared.utils.audit import record_audit
    org = _hr(request)
    d = await _dev(db, org, did)
    if body.protocol not in PROTOCOLS:
        raise err(422, "TA_DEV_PROTOCOL", "Protocol must be adms (ZKTeco / eSSL), cosec (Matrix) or http")
    if body.direction_mode not in DIRECTION_MODES:
        raise err(422, "TA_DEV_DIRECTION", "Direction must be device, alternate, in or out")
    if not valid_serial(d.device_id):
        raise err(422, "TA_DEV_SERIAL", "The device id must be its serial number (letters, digits, - _ .; 3–40)")
    try:
        cidrs = clean_cidrs(body.allowed_ips)
    except ValueError as e:
        raise err(422, "TA_DEV_IP_LIST", f"IP allow-list: {e}")
    if not (0 <= body.bounce_sec <= 600 and 1 <= body.max_backlog_days <= 120 and 5 <= body.offline_after_min <= 1440):
        raise err(422, "TA_DEV_LIMITS", "Bounce 0–600 s, backlog 1–120 days, offline after 5–1440 minutes")
    users = {u["id"]: u for u in await directory.org_users(str(org))}
    bad = [u for u in body.alert_user_ids if u not in users]
    if bad:
        raise err(422, "TA_DEV_ALERT_USER", "Pick alert recipients from this organisation's logins")
    p = await _push_row(db, d)
    key = serial_key(d.device_id)
    clash = (await db.execute(select(DevicePush).where(DevicePush.serial_key == key))).scalar_one_or_none()
    if clash and clash.device_row_id != d.id:
        # never say which organisation holds it
        raise err(409, "TA_DEV_SERIAL_TAKEN", "This serial number is already registered for push elsewhere. "
                                               "Contact Lexora HRM support with the device invoice to release it.")
    if body.enabled and body.protocol == "adms" and not cidrs and not (p and p.token_hash):
        raise err(422, "TA_DEV_ADMS_AUTH", "A ZKTeco / eSSL device on the plain /iclock path needs an IP allow-list "
                                            "(your office's fixed public address). Without one, create a token and use "
                                            "the token path.")
    if body.enabled and body.protocol in ("cosec", "http") and not (p and p.token_hash):
        raise err(422, "TA_DEV_TOKEN_FIRST", "Create the device token first (it goes into the device's server URL)")
    if not p:
        p = DevicePush(org_id=org, device_row_id=d.id, serial_key=key)
        db.add(p)
    p.serial_key = key
    p.enabled, p.protocol, p.allowed_ips, p.direction_mode = body.enabled, body.protocol, cidrs, body.direction_mode
    p.bounce_sec, p.max_backlog_days, p.offline_after_min = body.bounce_sec, body.max_backlog_days, body.offline_after_min
    p.alert_user_ids, p.updated_at = list(dict.fromkeys(body.alert_user_ids)), _now()
    await db.commit()
    await record_audit(request, "time.device.push.update", entity_type="biometric_device", entity_id=str(d.id),
                       summary=f"Push settings for {d.name}: {'on' if p.enabled else 'off'}, {p.protocol}, "
                               f"{len(cidrs)} allowed address range(s)",
                       meta={"protocol": p.protocol, "allowed_ips": cidrs, "direction_mode": p.direction_mode})
    return _push_out(p, d, _now())


@admin.post("/devices/{did}/push/token")
async def rotate_token(did: str, request: Request, db: AsyncSession = Depends(get_db)):
    """A new per-device token. Shown in THIS answer only; we keep its SHA-256. The old one stops at once."""
    from shared.utils.audit import record_audit
    org = _hr(request)
    d = await _dev(db, org, did)
    if not valid_serial(d.device_id):
        raise err(422, "TA_DEV_SERIAL", "The device id must be its serial number (letters, digits, - _ .; 3–40)")
    p = await _push_row(db, d)
    key = serial_key(d.device_id)
    if not p:
        clash = (await db.execute(select(DevicePush).where(DevicePush.serial_key == key))).scalar_one_or_none()
        if clash:
            raise err(409, "TA_DEV_SERIAL_TAKEN", "This serial number is already registered for push elsewhere. "
                                                   "Contact Lexora HRM support with the device invoice to release it.")
        p = DevicePush(org_id=org, device_row_id=d.id, serial_key=key, enabled=False)
        db.add(p)
    tok = new_token()
    p.token_hash, p.token_hint, p.updated_at = hash_token(tok), tok[-4:], _now()
    await db.commit()
    await record_audit(request, "time.device.push.token", entity_type="biometric_device", entity_id=str(d.id),
                       summary=f"New push token for {d.name} (…{tok[-4:]}) — the previous token no longer works")
    out = _push_out(p, d, _now())
    out["token"] = tok
    out["paths_with_token"] = {k: v.replace("<token>", tok) for k, v in out["paths"].items()}
    return out


@admin.post("/health/check")
async def run_check(request: Request, db: AsyncSession = Depends(get_db)):
    org = _hr(request)
    return {"alerts_sent": await check_offline_once(db, org=str(org))}
