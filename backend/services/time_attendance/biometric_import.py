"""Biometric attendance import (eSSL / ZKTeco / Matrix exports) + push-device webhook.

CSV/XLSX upload → punches in the EXISTING ta_punches model → attendance days
recomputed with the module's own `_recompute_day`. Duplicate-safe: a punch is
skipped when the same employee already has a punch at the same minute+direction.

Devices (HR):   GET/POST/PATCH /time/biometric/devices[/{id}]
Code map (HR):  GET/PUT        /time/biometric/map      (device employee code → employee)
Upload (HR):    POST /time/biometric/import/preview   (multipart file, vendor?)  → parsed rows + matches
                POST /time/biometric/import           (multipart file, vendor?)  → punches written + log
Logs (HR):      GET  /time/biometric/imports
Webhook:        POST /time/biometric/webhook/{device_id}   header X-Device-Secret (no JWT — org comes from the device)
                body: {"punches":[{"emp_code","at","direction"?}]}  or a vendor list [{UserID|EmpCode, PunchTime|DateTime, Status|InOut}]

Parsing lives in pure functions (parse_table / normalize_rows) so it is unit-tested
without a DB (test_biometric_import.py).
"""
import csv
import hashlib
import io
import re
import secrets
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy import Boolean, DateTime, Integer, String, Text, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

sys.path.append("/app")
from database import Base, get_db  # noqa: E402
from models import Punch  # noqa: E402
from shared.utils.org_time import to_local_date  # noqa: E402
from shared.utils.entitlements import org_module_refusal  # noqa: E402

router = APIRouter(prefix="/time/biometric", tags=["biometric"])

# Decision 2026-10-21 (least privilege): raw device punches are HR's. The pay office reads the attendance
# SUMMARIES it needs for pay (/time/payroll-feed, /time/readiness), not the biometric logs.
ADMIN_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager", "hr_staff", "branch_admin"}
DEFAULT_TZ = "America/Toronto"


def _now():
    return datetime.now(timezone.utc)


# ── Tables ────────────────────────────────────────────────────────────────────
class BiometricDevice(Base):
    __tablename__ = "ta_biometric_devices"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    device_id: Mapped[str] = mapped_column(String(60), index=True)      # serial / id the device sends
    name: Mapped[str] = mapped_column(String(120))
    vendor: Mapped[str] = mapped_column(String(20), default="essl")     # essl|zkteco|matrix|other
    location: Mapped[str | None] = mapped_column(String(120))
    timezone: Mapped[str] = mapped_column(String(40), default=DEFAULT_TZ)
    # webhook shared secret — stored as "sha256:<hex>" (integration 2026-10-23); the plain value is shown ONCE
    # on create / rotate. Rows written before that hold the plain value and are hashed on boot / first use.
    secret: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BiometricEmpMap(Base):
    """device employee code → employee. device_id NULL = applies to every device."""
    __tablename__ = "ta_biometric_emp_map"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(60))
    device_emp_code: Mapped[str] = mapped_column(String(40), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class BiometricImport(Base):
    __tablename__ = "ta_biometric_imports"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="upload")   # upload|webhook
    filename: Mapped[str | None] = mapped_column(String(200))
    vendor: Mapped[str | None] = mapped_column(String(20))
    device_id: Mapped[str | None] = mapped_column(String(60))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    punches_written: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    unmatched: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list | None] = mapped_column(JSONB, default=list)
    unmatched_codes: Mapped[list | None] = mapped_column(JSONB, default=list)
    date_from: Mapped[str | None] = mapped_column(String(10))
    date_to: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12), default="done")      # done|partial|failed
    created_by: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ── Pure parsing ──────────────────────────────────────────────────────────────
# header aliases (lower-cased, non-alphanumerics stripped)
H_EMP = {"empcode", "employeecode", "empid", "employeeid", "userid", "enrollno", "enrollnumber", "enrolno", "pin",
         "badgenumber", "cardno", "empno", "employeeno", "personnelno", "staffcode", "code", "id", "emp"}
H_NAME = {"name", "empname", "employeename", "username", "firstname"}
H_DATE = {"date", "attdate", "attendancedate", "logdate", "punchdate", "workdate", "day"}
H_IN = {"intime", "in", "punchin", "firstin", "timein", "firstpunch", "checkin", "clockin", "entry", "entrytime"}
H_OUT = {"outtime", "out", "punchout", "lastout", "timeout", "lastpunch", "checkout", "clockout", "exit", "exittime"}
H_DT = {"datetime", "punchtime", "logtime", "logdatetime", "time", "timestamp", "attendancetime", "punchdatetime",
        "eventtime", "transactiontime", "checktime", "punch"}
H_DIR = {"direction", "inout", "status", "type", "punchtype", "io", "mode", "state", "event", "verifymode", "checktype", "inouttype"}
H_DEVICE = {"deviceid", "device", "machine", "machineno", "terminal", "terminalid", "sn", "serial", "devicename"}

IN_WORDS = {"in", "i", "0", "c/in", "checkin", "check-in", "entry", "punchin", "in1", "1-in", "0-in", "cin"}
OUT_WORDS = {"out", "o", "1", "c/out", "checkout", "check-out", "exit", "punchout", "out1", "1-out", "cout"}

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y",
                "%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%b %d, %Y"]
TIME_FORMATS = ["%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%I:%M:%S%p", "%I:%M%p", "%H.%M", "%H%M"]


def _norm_header(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def parse_date(s) -> date | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    t = str(s).strip()
    if not t:
        return None
    t = t.split(" ")[0] if re.match(r"^\d{4}-\d{2}-\d{2}[ T]", t) else t
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(t, f).date()
        except ValueError:
            continue
    return None


def parse_time(s) -> time | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.time().replace(microsecond=0)
    if isinstance(s, time):
        return s.replace(microsecond=0)
    t = str(s).strip().upper()
    if not t or t in ("-", "--", "NA", "N/A", "NULL", "NONE", "0", "00:00:00", "00:00"):
        return None
    t = re.sub(r"\s+", " ", t)
    for f in TIME_FORMATS:
        try:
            return datetime.strptime(t, f).time()
        except ValueError:
            continue
    return None


def parse_datetime(s) -> datetime | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s.replace(microsecond=0)
    t = str(s).strip()
    if not t:
        return None
    for f in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
              "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M",
              "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p", "%Y/%m/%d %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
              "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"]:
        try:
            return datetime.strptime(t, f)
        except ValueError:
            continue
    parts = t.split(" ", 1)
    if len(parts) == 2:
        d, tm = parse_date(parts[0]), parse_time(parts[1])
        if d and tm:
            return datetime.combine(d, tm)
    return None


def parse_direction(s) -> str | None:
    if s is None:
        return None
    t = str(s).strip().lower()
    if t in IN_WORDS or t.startswith("in") or "check-in" in t or t.endswith("in"):
        return "in"
    if t in OUT_WORDS or t.startswith("out") or "check-out" in t or t.endswith("out"):
        return "out"
    return None


def norm_code(c) -> str:
    """Device codes come as 0001 / 1 / 1.0 / EMP001 — compare on a normalised form."""
    if c is None:
        return ""
    s = str(c).strip()
    if re.match(r"^\d+\.0$", s):
        s = s[:-2]
    s = s.upper()
    m = re.match(r"^([A-Z]*)0*(\d+)$", s)
    if m:
        return f"{m.group(1)}{int(m.group(2))}"
    return s


def read_table(content: bytes, filename: str = "") -> list[dict]:
    """CSV / TSV / XLSX → list of {header: value}. Header row is auto-detected
    (first row that contains an employee-code column)."""
    name = (filename or "").lower()
    rows: list[list] = []
    if name.endswith(".xlsx") or name.endswith(".xlsm") or content[:2] == b"PK":
        try:
            import openpyxl
        except ImportError:
            raise HTTPException(400, "Excel upload needs openpyxl on the server — export CSV instead")
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        for r in ws.iter_rows(values_only=True):
            rows.append(list(r))
    else:
        text = content.decode("utf-8-sig", errors="replace")
        # delimiter = the candidate that appears most across the first non-empty lines
        lines = [ln for ln in text.splitlines()[:40] if ln.strip()]
        counts = {d: sum(ln.count(d) for ln in lines) for d in (",", ";", "\t", "|")}
        delim = max(counts, key=counts.get) if any(counts.values()) else ","
        for r in csv.reader(io.StringIO(text), delimiter=delim):
            rows.append(r)
    # find header row
    hdr_idx = None
    for i, r in enumerate(rows[:30]):
        norm = {_norm_header(c) for c in r}
        if norm & H_EMP and (norm & (H_DATE | H_DT | H_IN)):
            hdr_idx = i; break
    if hdr_idx is None:
        for i, r in enumerate(rows[:30]):
            if len([c for c in r if str(c or "").strip()]) >= 3:
                hdr_idx = i; break
    if hdr_idx is None:
        return []
    headers = [str(h or "").strip() for h in rows[hdr_idx]]
    out = []
    for j, r in enumerate(rows[hdr_idx + 1:]):
        if not any(str(c or "").strip() for c in r):
            continue
        row = {headers[i] if i < len(headers) else f"col{i}": r[i] for i in range(len(r))}
        row["__line__"] = hdr_idx + 2 + j          # 1-based line in the file (for error messages)
        out.append(row)
    return out


def detect_columns(headers: list[str]) -> dict:
    found: dict = {}
    for h in headers:
        n = _norm_header(h)
        if n in H_EMP and "emp" not in found:
            found["emp"] = h
        elif n in H_NAME and "name" not in found:
            found["name"] = h
        elif n in H_DT and "dt" not in found:
            found["dt"] = h
        elif n in H_DATE and "date" not in found:
            found["date"] = h
        elif n in H_IN and "in" not in found:
            found["in"] = h
        elif n in H_OUT and "out" not in found:
            found["out"] = h
        elif n in H_DIR and "dir" not in found:
            found["dir"] = h
        elif n in H_DEVICE and "device" not in found:
            found["device"] = h
    # ZKTeco "Time" is a datetime when there is no separate date column
    if "dt" not in found and "date" not in found:
        for h in headers:
            if _norm_header(h) in ("time", "datetime"):
                found["dt"] = h
    return found


def normalize_rows(table: list[dict]) -> tuple[list[dict], list[str], dict]:
    """Table rows → punch events [{code, name, at(naive local), direction, row}] + errors.
    Handles (a) summary rows EmpCode/Date/InTime/OutTime and (b) punch logs
    EmpCode/DateTime[/Direction]. Rows without a direction alternate in/out per
    employee per day in time order."""
    if not table:
        return [], ["No rows found"], {}
    cols = detect_columns([h for h in table[0].keys() if h != "__line__"])
    errors: list[str] = []
    if "emp" not in cols:
        return [], ["Could not find an employee code column (EmpCode / Employee ID / UserID)"], cols
    events: list[dict] = []
    if "in" in cols or "out" in cols:
        for k, r in enumerate(table, start=2):
            i = r.get("__line__", k)
            code = norm_code(r.get(cols["emp"]))
            d = parse_date(r.get(cols.get("date"))) if cols.get("date") else None
            if not code:
                continue
            if not d:
                errors.append(f"Row {i}: bad or missing date"); continue
            tin = parse_time(r.get(cols["in"])) if "in" in cols else None
            tout = parse_time(r.get(cols["out"])) if "out" in cols else None
            if tin:
                events.append({"code": code, "name": r.get(cols.get("name")), "at": datetime.combine(d, tin), "direction": "in", "row": i})
            if tout:
                at = datetime.combine(d, tout)
                if tin and tout < tin:
                    at += timedelta(days=1)      # night shift crossing midnight
                events.append({"code": code, "name": r.get(cols.get("name")), "at": at, "direction": "out", "row": i})
            if not tin and not tout:
                errors.append(f"Row {i}: no in/out time")
    elif "dt" in cols or "date" in cols:
        raw = []
        for k, r in enumerate(table, start=2):
            i = r.get("__line__", k)
            code = norm_code(r.get(cols["emp"]))
            if not code:
                continue
            at = None
            if "dt" in cols:
                at = parse_datetime(r.get(cols["dt"]))
                if at is None and "date" in cols:
                    d, tm = parse_date(r.get(cols["date"])), parse_time(r.get(cols["dt"]))
                    at = datetime.combine(d, tm) if d and tm else None
            if at is None:
                errors.append(f"Row {i}: bad or missing punch time"); continue
            direction = parse_direction(r.get(cols["dir"])) if "dir" in cols else None
            raw.append({"code": code, "name": r.get(cols.get("name")), "at": at.replace(microsecond=0), "direction": direction, "row": i})
        events = _alternate_directions(raw)
    else:
        return [], ["Need either Date + InTime/OutTime columns or a DateTime column"], cols
    # de-dupe identical rows inside the file
    seen = set(); uniq = []
    for e in events:
        k = (e["code"], e["at"], e["direction"])
        if k in seen:
            continue
        seen.add(k); uniq.append(e)
    return uniq, errors, cols


def _alternate_directions(raw: list[dict]) -> list[dict]:
    """Punch logs without a direction column: drop exact duplicate rows, then alternate
    in/out per employee per day in time order (explicit directions reset the state)."""
    seen = set(); uniq = []
    for e in raw:
        k = (e["code"], e["at"], e["direction"])
        if k in seen:
            continue
        seen.add(k); uniq.append(e)
    uniq.sort(key=lambda e: (e["code"], e["at"]))
    state: dict = {}
    for e in uniq:
        k = (e["code"], e["at"].date())
        if e["direction"] is None:
            e["direction"] = "in" if state.get(k, "out") == "out" else "out"
        state[k] = e["direction"]
    return uniq


def parse_table(content: bytes, filename: str = "") -> tuple[list[dict], list[str], dict]:
    return normalize_rows(read_table(content, filename))


def to_utc(naive: datetime, tzname: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return naive.replace(tzinfo=ZoneInfo(tzname or DEFAULT_TZ)).astimezone(timezone.utc)
    except Exception:
        return naive.replace(tzinfo=timezone.utc)


# ── Helpers (route-level; lazy-import routers to avoid import cycles) ─────────
class _OrgShim:
    """Request look-alike for server-to-server calls (webhook): only `.headers`."""
    def __init__(self, headers: dict):
        self.headers = headers


def _org(request: Request) -> str:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    return org


def _require_admin(request: Request):
    if (request.headers.get("x-role") or "").lower() not in ADMIN_ROLES:
        raise HTTPException(403, "HR role required")


def _actor(request: Request) -> str | None:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id")


async def _emp_index(request: Request, db, org: str, device_id: str | None) -> tuple[dict, dict]:
    """(code → employee dict, employee_id → employee dict). Codes come from the map
    table first (device-specific beats global), then employee_number."""
    from routers import _fetch_employees
    emps = await _fetch_employees(request)
    by_id = {e["id"]: e for e in emps}
    idx: dict = {}
    for e in emps:
        if e.get("employee_number"):
            idx[norm_code(e["employee_number"])] = e
        if e.get("biometric_code"):
            idx[norm_code(e["biometric_code"])] = e
    maps = (await db.execute(select(BiometricEmpMap).where(BiometricEmpMap.org_id == uuid.UUID(org)))).scalars().all()
    for m in sorted(maps, key=lambda m: 0 if m.device_id is None else 1):
        if m.device_id and device_id and m.device_id != device_id:
            continue
        e = by_id.get(str(m.employee_id))
        if e:
            idx[norm_code(m.device_emp_code)] = e
    return idx, by_id


async def _write_punches(request: Request, db, org: str, events: list[dict], idx: dict, tzname: str,
                         source_tag: str, actor: str | None) -> dict:
    """Write new punches (skip duplicates), recompute the touched days."""
    from routers import _fetch_stat_holidays, _recompute_day, _schedule_map
    written = dup = unmatched = 0
    unmatched_codes: set = set()
    touched: dict = {}
    # existing punches for the (employee, date) pairs in play
    pairs = set()
    for e in events:
        emp = idx.get(e["code"])
        if not emp:
            unmatched += 1; unmatched_codes.add(e["code"]); continue
        pairs.add((emp["id"], to_local_date(to_utc(e["at"], tzname), tzname)))
    existing: dict = {}
    if pairs:
        emp_ids = {uuid.UUID(p[0]) for p in pairs}
        dates = {p[1] for p in pairs}
        rows = (await db.execute(select(Punch).where(Punch.org_id == uuid.UUID(org), Punch.employee_id.in_(emp_ids),
                                                     Punch.work_date.in_(dates)))).scalars().all()
        for p in rows:
            existing.setdefault((str(p.employee_id), p.work_date), set()).add((p.at.astimezone(timezone.utc).replace(second=0, microsecond=0), p.direction))
    for e in events:
        emp = idx.get(e["code"])
        if not emp:
            continue
        at = to_utc(e["at"], tzname)
        key = (at.replace(second=0, microsecond=0), e["direction"])
        wd = to_local_date(at, tzname)            # the device's local day, not the UTC date
        slot = existing.setdefault((emp["id"], wd), set())
        if key in slot:
            dup += 1; continue
        slot.add(key)
        db.add(Punch(org_id=org, employee_id=uuid.UUID(emp["id"]), work_date=wd, at=at, direction=e["direction"],
                     source="kiosk", note=source_tag[:200], created_by=None))
        touched.setdefault(emp["id"], set()).add(wd)
        written += 1
    await db.flush()
    if touched:
        by_emp, default = await _schedule_map(db, org)
        hol_rows = await _fetch_stat_holidays(request)
        for emp_id, days in touched.items():
            emp = next((v for v in idx.values() if v["id"] == emp_id), None)
            if not emp:
                continue
            holidays = {date.fromisoformat(h["holiday_date"][:10]) for h in hol_rows
                        if h.get("holiday_date") and (h.get("province") in (None, emp.get("province_of_employment")))}
            for d in sorted(days):
                await _recompute_day(db, org, emp, by_emp.get(emp_id), default, d, holidays)
    return {"written": written, "duplicates": dup, "unmatched": unmatched, "unmatched_codes": sorted(unmatched_codes),
            "employees": len(touched), "days": sum(len(v) for v in touched.values())}


# ── Devices ───────────────────────────────────────────────────────────────────
SECRET_PREFIX = "sha256:"


def hash_secret(plain: str) -> str:
    return SECRET_PREFIX + hashlib.sha256((plain or "").encode()).hexdigest()


def secret_ok(given: str, stored: str | None) -> bool:
    """Constant-time check against the stored hash (or a legacy plain value)."""
    if not given or not stored:
        return False
    if stored.startswith(SECRET_PREFIX):
        return secrets.compare_digest(hash_secret(given), stored)
    return secrets.compare_digest(given, stored)


def _new_secret(d: BiometricDevice) -> str:
    plain = secrets.token_urlsafe(24)
    d.secret = hash_secret(plain)
    return plain


async def hash_legacy_secrets(db: AsyncSession) -> int:
    """Boot step: hash any plain secret left from before 2026-10-23 (devices keep working — same value)."""
    rows = (await db.execute(select(BiometricDevice).where(~BiometricDevice.secret.startswith(SECRET_PREFIX)))).scalars().all()
    for d in rows:
        d.secret = hash_secret(d.secret)
    if rows:
        await db.commit()
    return len(rows)


def _dev_out(d: BiometricDevice, plain_secret: str | None = None) -> dict:
    # The stored secret is never returned. `secret` carries the plain value only in the create / rotate response.
    return {"id": str(d.id), "device_id": d.device_id, "name": d.name, "vendor": d.vendor, "location": d.location,
            "timezone": d.timezone, "active": d.active, "secret": plain_secret,
            "secret_set": bool(d.secret), "secret_masked": "••••••••" if d.secret else None,
            "webhook_path": f"/time/biometric/webhook/{d.device_id}",
            "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None}


@router.get("/devices")
async def devices(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    rows = (await db.execute(select(BiometricDevice).where(BiometricDevice.org_id == uuid.UUID(_org(request))))).scalars().all()
    return [_dev_out(d) for d in rows]


VENDORS = ("essl", "zkteco", "matrix", "other")


def valid_tz(name: str | None) -> bool:
    """True for a real IANA zone. to_utc() silently falls back to UTC on a bad name, which would
    shift every punch by several hours (5h for an Ontario site), so the device form and the import refuse it."""
    if not name:
        return False
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(str(name))
        return True
    except Exception:  # noqa: BLE001
        return False


class DeviceBody(BaseModel):
    device_id: str
    name: str
    vendor: str = "essl"
    location: str | None = None
    timezone: str = DEFAULT_TZ
    active: bool = True
    rotate_secret: bool = False

    @field_validator("device_id", "name")
    @classmethod
    def _not_blank(cls, v: str, info):
        v = (v or "").strip()
        limit = 60 if info.field_name == "device_id" else 120
        if not v:
            raise ValueError(f"{info.field_name.replace('_', ' ')} is required")
        if len(v) > limit:
            raise ValueError(f"{info.field_name.replace('_', ' ')} can be at most {limit} characters")
        return v

    @field_validator("vendor")
    @classmethod
    def _vendor(cls, v: str):
        if v not in VENDORS:
            raise ValueError("vendor must be essl, zkteco, matrix or other")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str):
        v = (v or DEFAULT_TZ).strip()
        if not valid_tz(v):
            raise ValueError("timezone must be a valid zone name such as America/Toronto")
        return v


@router.post("/devices", status_code=201)
async def create_device(body: DeviceBody, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    org = _org(request)
    dup = (await db.execute(select(BiometricDevice).where(BiometricDevice.org_id == uuid.UUID(org),
                                                          BiometricDevice.device_id == body.device_id.strip()))).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "A device with that id already exists")
    d = BiometricDevice(org_id=uuid.UUID(org), device_id=body.device_id.strip(), name=body.name.strip(), vendor=body.vendor,
                        location=body.location, timezone=body.timezone or DEFAULT_TZ, active=body.active)
    plain = _new_secret(d)
    db.add(d); await db.commit()
    return _dev_out(d, plain)


@router.patch("/devices/{did}")
async def update_device(did: str, body: DeviceBody, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    d = (await db.execute(select(BiometricDevice).where(BiometricDevice.id == uuid.UUID(did),
                                                        BiometricDevice.org_id == uuid.UUID(_org(request))))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Device not found")
    d.device_id = body.device_id.strip(); d.name = body.name.strip(); d.vendor = body.vendor; d.location = body.location
    d.timezone = body.timezone or DEFAULT_TZ; d.active = body.active
    plain = _new_secret(d) if body.rotate_secret else None
    await db.commit()
    return _dev_out(d, plain)


# ── Code map ──────────────────────────────────────────────────────────────────
@router.get("/map")
async def get_map(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    rows = (await db.execute(select(BiometricEmpMap).where(BiometricEmpMap.org_id == uuid.UUID(_org(request))))).scalars().all()
    return [{"id": str(m.id), "device_id": m.device_id, "device_emp_code": m.device_emp_code, "employee_id": str(m.employee_id)} for m in rows]


class MapBody(BaseModel):
    rows: list[dict]     # [{device_id?, device_emp_code, employee_id}]


@router.put("/map")
async def put_map(body: MapBody, request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    org = _org(request)
    existing = (await db.execute(select(BiometricEmpMap).where(BiometricEmpMap.org_id == uuid.UUID(org)))).scalars().all()
    for m in existing:
        await db.delete(m)
    seen = set()
    for r in body.rows:
        code = str(r.get("device_emp_code") or "").strip()
        emp = str(r.get("employee_id") or "").strip()
        if not code or not emp:
            continue
        k = (r.get("device_id") or None, code)
        if k in seen:
            continue
        seen.add(k)
        db.add(BiometricEmpMap(org_id=uuid.UUID(org), device_id=r.get("device_id") or None, device_emp_code=code, employee_id=uuid.UUID(emp)))
    await db.commit()
    return {"saved": len(seen)}


# ── Upload ────────────────────────────────────────────────────────────────────
@router.post("/import/preview")
async def import_preview(request: Request, file: UploadFile = File(...), vendor: str | None = Form(None),
                         device_id: str | None = Form(None), db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    org = _org(request)
    content = await file.read()
    events, errors, cols = parse_table(content, file.filename or "")
    idx, _ = await _emp_index(request, db, org, device_id)
    matched = sum(1 for e in events if e["code"] in idx)
    codes = {}
    for e in events:
        c = codes.setdefault(e["code"], {"code": e["code"], "name": e.get("name"), "punches": 0, "employee": None, "employee_id": None})
        c["punches"] += 1
        emp = idx.get(e["code"])
        if emp:
            c["employee"] = f"{emp.get('first_name', '')} {emp.get('last_name', '')}".strip(); c["employee_id"] = emp["id"]
    dates = sorted({e["at"].date() for e in events})
    return {"filename": file.filename, "columns": cols, "rows": len(events), "matched": matched, "unmatched": len(events) - matched,
            "date_from": dates[0].isoformat() if dates else None, "date_to": dates[-1].isoformat() if dates else None,
            "errors": errors[:50], "codes": sorted(codes.values(), key=lambda c: (c["employee"] is not None, c["code"])),
            "sample": [{"code": e["code"], "at": e["at"].isoformat(), "direction": e["direction"]} for e in events[:20]]}


@router.post("/import")
async def import_file(request: Request, file: UploadFile = File(...), vendor: str | None = Form(None),
                      device_id: str | None = Form(None), timezone_name: str | None = Form(None),
                      db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    org = _org(request)
    content = await file.read()
    sha = hashlib.sha256(content).hexdigest()
    prior = (await db.execute(select(BiometricImport).where(BiometricImport.org_id == uuid.UUID(org), BiometricImport.file_sha256 == sha,
                                                            BiometricImport.status != "failed"))).scalar_one_or_none()
    events, errors, cols = parse_table(content, file.filename or "")
    log = BiometricImport(org_id=uuid.UUID(org), source="upload", filename=file.filename, vendor=vendor, device_id=device_id,
                          file_sha256=sha, rows_total=len(events), errors=errors[:100], created_by=_actor(request))
    if not events:
        log.status = "failed"; log.note = "; ".join(errors[:3]) or "No punches found"
        db.add(log); await db.commit()
        raise HTTPException(400, log.note)
    tzname = timezone_name or DEFAULT_TZ
    if not valid_tz(tzname):
        raise HTTPException(422, "timezone_name must be a valid zone name such as America/Toronto")
    idx, _ = await _emp_index(request, db, org, device_id)
    res = await _write_punches(request, db, org, events, idx, tzname, f"biometric import {file.filename}", _actor(request))
    dates = sorted({e["at"].date() for e in events})
    log.punches_written = res["written"]; log.duplicates = res["duplicates"]; log.unmatched = res["unmatched"]
    log.unmatched_codes = res["unmatched_codes"]; log.date_from = dates[0].isoformat(); log.date_to = dates[-1].isoformat()
    log.status = "partial" if (res["unmatched"] or errors) else "done"
    if prior:
        log.note = "Same file was imported before — only new punches were added"
    db.add(log); await db.commit()
    return {"id": str(log.id), **res, "rows": len(events), "errors": errors[:50], "status": log.status, "note": log.note}


@router.get("/imports")
async def imports(request: Request, db: AsyncSession = Depends(get_db)):
    _require_admin(request)
    rows = (await db.execute(select(BiometricImport).where(BiometricImport.org_id == uuid.UUID(_org(request)))
                             .order_by(BiometricImport.created_at.desc()).limit(200))).scalars().all()
    return [{"id": str(r.id), "source": r.source, "filename": r.filename, "vendor": r.vendor, "device_id": r.device_id,
             "rows_total": r.rows_total, "punches_written": r.punches_written, "duplicates": r.duplicates, "unmatched": r.unmatched,
             "unmatched_codes": r.unmatched_codes or [], "errors": r.errors or [], "date_from": r.date_from, "date_to": r.date_to,
             "status": r.status, "created_by": r.created_by, "note": r.note,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]


# ── Webhook (push devices / ADMS / vendor cloud) ──────────────────────────────
def webhook_events(payload) -> list[dict]:
    """Accepts {"punches":[...]} or a bare list. Each item: emp_code|EmpCode|UserID|pin,
    at|PunchTime|DateTime|time, direction|Status|InOut (optional)."""
    items = payload.get("punches") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        code = next((it[k] for k in ("emp_code", "EmpCode", "empcode", "UserID", "userid", "user_id", "pin", "PIN", "EnrollNo", "code") if k in it), None)
        at = next((it[k] for k in ("at", "PunchTime", "punch_time", "DateTime", "datetime", "time", "Time", "timestamp", "LogTime") if k in it), None)
        dr = next((it[k] for k in ("direction", "Status", "status", "InOut", "inout", "type", "Type") if k in it), None)
        dt = parse_datetime(at)
        if dt is None and isinstance(at, (int, float)):
            dt = datetime.fromtimestamp(float(at), tz=timezone.utc).replace(tzinfo=None)
        if not code or dt is None:
            continue
        out.append({"code": norm_code(code), "name": None, "at": dt.replace(microsecond=0), "direction": parse_direction(dr), "row": 0})
    return _alternate_directions(out)


@router.post("/webhook/{device_id}")
async def webhook(device_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    secret = request.headers.get("x-device-secret") or request.query_params.get("secret") or ""
    d = (await db.execute(select(BiometricDevice).where(BiometricDevice.device_id == device_id, BiometricDevice.active == True))).scalar_one_or_none()  # noqa: E712
    if not d or not secret_ok(secret, d.secret):
        raise HTTPException(401, "Unknown device or bad secret")
    if not d.secret.startswith(SECRET_PREFIX):
        d.secret = hash_secret(d.secret)
    # Integration 2026-10-23: no login → the gateway module paywall never ran; the device's org must hold "time".
    if await org_module_refusal(db, d.org_id, "time"):
        raise HTTPException(403, "Forbidden")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "JSON body expected")
    events = webhook_events(payload)
    org = str(d.org_id)
    # The webhook carries no user identity — the roster/holiday lookups only need the
    # forwarded org headers, so hand them a minimal request-like shim.
    shim = _OrgShim({"x-org-id": org, "x-role": "org_admin"})
    idx, _ = await _emp_index(shim, db, org, device_id)
    res = await _write_punches(shim, db, org, events, idx, d.timezone or DEFAULT_TZ, f"device {device_id}", None) if events else \
        {"written": 0, "duplicates": 0, "unmatched": 0, "unmatched_codes": [], "employees": 0, "days": 0}
    d.last_seen_at = _now()
    if events:
        dates = sorted({e["at"].date() for e in events})
        db.add(BiometricImport(org_id=d.org_id, source="webhook", vendor=d.vendor, device_id=device_id, rows_total=len(events),
                               punches_written=res["written"], duplicates=res["duplicates"], unmatched=res["unmatched"],
                               unmatched_codes=res["unmatched_codes"], date_from=dates[0].isoformat(), date_to=dates[-1].isoformat(),
                               status="partial" if res["unmatched"] else "done", created_by=f"device:{device_id}"))
    await db.commit()
    return {"ok": True, "received": len(events), **res}
