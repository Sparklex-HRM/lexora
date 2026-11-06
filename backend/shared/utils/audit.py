"""Lexora HRM — shared, tamper-evident audit trail.

A single append-only ``audit_events`` table on the shared Base. Any service can
record events (``record_audit``); the compliance service reads and verifies them.

Tamper-evidence: events form a per-org HMAC hash chain. Each row stores
``entry_hash = HMAC-SHA256(JWT_SECRET, prev_hash + canonical(event))``. Altering,
re-ordering or deleting any row breaks every subsequent ``entry_hash``, which the
``/compliance/audit/verify`` walk detects. HMAC (keyed by the server secret) —
not a bare hash — so an attacker without the key cannot forge a valid chain.

Writes happen in the helper's OWN transaction (never the caller's), so an audit
failure can never roll back the business action. Call ``record_audit`` AFTER the
business transaction commits. The per-org ``seq`` has a unique constraint; under
a rare concurrent write one event may be dropped rather than fork the chain —
acceptable for the low-frequency money/compliance events this trail records.
"""
import hashlib
import logging
import hmac
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (BigInteger, DateTime, String, Text, UniqueConstraint,
                        desc, func, select)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared.utils.config import settings
from shared.utils.db import Base, make_engine, make_sessionmaker

_GENESIS = "GENESIS"


def _now():
    return datetime.now(timezone.utc)


class AuditEvent(Base):
    """One append-only, hash-chained audit record."""
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("org_id", "seq", name="audit_events_org_seq_uq"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)          # per-org 1..N
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    actor_email: Mapped[str | None] = mapped_column(String(200))
    actor_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(80), index=True)          # e.g. payroll.run.execute
    entity_type: Mapped[str | None] = mapped_column(String(48))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    branch_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict | None] = mapped_column(JSONB)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), index=True)


def canonical(ev: dict) -> str:
    """Stable JSON of the signed fields (sorted keys, no whitespace drift)."""
    return json.dumps(ev, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(prev_hash: str, payload: dict) -> str:
    msg = f"{prev_hash}{canonical(payload)}".encode()
    return hmac.new(settings.JWT_SECRET.encode(), msg, hashlib.sha256).hexdigest()


def signed_payload(ev) -> dict:
    """The subset of an event that the hash covers (order-independent)."""
    return {
        "org_id": str(ev.org_id), "seq": ev.seq,
        "ts": ev.ts.isoformat() if ev.ts else None,
        "actor_id": ev.actor_id, "actor_email": ev.actor_email, "actor_role": ev.actor_role,
        "action": ev.action, "entity_type": ev.entity_type, "entity_id": ev.entity_id,
        "branch_id": ev.branch_id, "summary": ev.summary, "meta": ev.meta,
    }


_sessionmaker = None


def _sm():
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = make_sessionmaker(make_engine())
    return _sessionmaker


def _hdr(request, *names):
    for n in names:
        v = request.headers.get(n)
        if v:
            return v
    return None


async def record_audit(request, action: str, *, entity_type: str | None = None,
                       entity_id=None, summary: str | None = None,
                       meta: dict | None = None) -> bool:
    """Best-effort tamper-evident audit write in an independent transaction.

    Returns True if written. Never raises — an audit failure must not affect the
    caller's business action (call this only after that action has committed).
    """
    org = _hdr(request, "x-org-id")
    if not org:
        return False
    try:
        meta = jsonable_meta(meta)
        async with _sm()() as s:
            # one appender per org at a time — a concurrent write took the same seq and was silently dropped
            from shared.utils.chain_lock import lock_chain
            await lock_chain(s, "audit_events", org)
            last = (await s.execute(
                select(AuditEvent).where(AuditEvent.org_id == uuid.UUID(org))
                .order_by(desc(AuditEvent.seq)).limit(1))).scalar_one_or_none()
            seq = (last.seq + 1) if last else 1
            prev = last.entry_hash if last else _GENESIS
            ev = AuditEvent(
                org_id=uuid.UUID(org), seq=seq, ts=_now(),
                actor_id=_hdr(request, "x-user-id"),
                actor_email=_hdr(request, "x-user-email"),
                actor_role=_hdr(request, "x-role"),
                action=action, entity_type=entity_type,
                entity_id=str(entity_id) if entity_id is not None else None,
                branch_id=_hdr(request, "x-branch-id"),
                summary=summary, meta=meta, prev_hash=prev,
            )
            ev.entry_hash = compute_hash(prev, signed_payload(ev))
            s.add(ev)
            await s.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — never break the caller, but say so in the log
        logging.getLogger("lexora.audit").warning("audit write failed for %s: %s", action, type(exc).__name__)
        return False


def _json_default(v):
    import decimal
    from datetime import date as _date
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime, _date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (set, frozenset, tuple)):
        return list(v)
    return str(v)


def jsonable_meta(meta: dict | None) -> dict | None:
    """meta as plain JSON (Decimal → float, dates → ISO, UUID → str) so the JSONB write
    cannot fail and the stored value is exactly the value the hash covers."""
    if meta is None:
        return None
    return json.loads(json.dumps(meta, default=_json_default))
