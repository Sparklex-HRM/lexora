"""Lexora HRM — "who changed what" audit rows with before/after, PII masked (stream S4).

Builds on shared.utils.audit.record_audit (the tamper-evident, hash-chained
audit_events table). This module only decides WHAT goes into `meta`:

    from shared.audit_trail import audit_change, snapshot
    before = snapshot(emp)                     # ORM object or dict, BEFORE the change
    … change + commit …
    await audit_change(request, "employee.update", entity_type="employee", entity_id=emp.id,
                       before=before, after=snapshot(emp))

meta = {"op": "create|update|delete", "changes": {field: {"from": x, "to": y}}, …}

Masking rules (values are never written in clear for these):
  * identifiers (SIN, bank account, passport, driver's licence, health card) and any
    *_enc / *_hash / *_masked / password column → "[changed]" / "[set]" / "[cleared]"
  * e-mail → r***@domain, phone → ••••1234, date of birth → year only,
    address lines / postal code / nominee & emergency contact → "[changed]"
  * money (salary, CTC, rates) is kept — the audit must show what a pay change was.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import re
import uuid
from typing import Any, Iterable, Mapping

from shared.utils.audit import record_audit

SECRET_FIELDS = {
    "bank_account", "bank_account_number", "account_number", "bank_transit", "bank_institution",
    "passport", "passport_number", "sin", "social_insurance_number", "drivers_licence", "health_card_number",
    "bank_account_encrypted", "sin_encrypted", "password", "hashed_password", "secret", "token",
    "otp", "mfa_secret", "api_key",
}
SECRET_SUFFIXES = ("_enc", "_hash", "_masked", "_last4", "_secret", "_token", "_password")
CONTACT_FIELDS = {
    "address_line1", "address_line2", "postal_code", "home_phone", "emergency_name", "emergency_phone",
    "emergency_relationship", "nominee_name", "nominee_phone", "nominee_relationship", "father_name",
    "spouse_name", "work_permit_number",
}
IGNORE_FIELDS = {"updated_at", "created_at", "last_login", "enc_key_id", "tokens_valid_after"}


def _plain(v: Any):
    if isinstance(v, (_dt.datetime, _dt.date)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def snapshot(obj: Any, fields: Iterable[str] | None = None) -> dict:
    """JSON-able {column: value} of an ORM object (its table columns) or a mapping."""
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        data = dict(obj)
    else:
        table = getattr(obj, "__table__", None)
        cols = [c.name for c in table.columns] if table is not None else [
            k for k in vars(obj) if not k.startswith("_")]
        data = {c: getattr(obj, c, None) for c in cols}
    if fields is not None:
        keep = set(fields)
        data = {k: v for k, v in data.items() if k in keep}
    return {k: _plain(v) for k, v in data.items()}


def is_secret(field: str) -> bool:
    f = field.lower()
    return f in SECRET_FIELDS or f.endswith(SECRET_SUFFIXES)


def mask_value(field: str, v: Any):
    """Masked representation of one value for the audit trail."""
    if v is None or v == "":
        return None
    f = field.lower()
    if is_secret(f) or f in CONTACT_FIELDS:
        return "[set]"
    if "email" in f and isinstance(v, str) and "@" in v:
        name, _, dom = v.partition("@")
        return f"{name[:1]}***@{dom}"
    if ("phone" in f or "mobile" in f) and isinstance(v, str):
        digits = re.sub(r"\D", "", v)
        return "••••" + digits[-4:] if len(digits) >= 4 else "••••"
    if f in ("date_of_birth", "dob", "birth_date"):
        return str(v)[:4]
    return v


def masked_diff(before: Mapping | None, after: Mapping | None) -> dict:
    """{field: {"from": masked, "to": masked}} for every field whose value changed.
    A secret field reports only that it changed: "[set]" / "[changed]" / "[cleared]"."""
    before, after = dict(before or {}), dict(after or {})
    out = {}
    for k in sorted(set(before) | set(after)):
        if k in IGNORE_FIELDS:
            continue
        b, a = before.get(k), after.get(k)
        if b == a or (b in (None, "") and a in (None, "")):
            continue
        if is_secret(k) or k.lower() in CONTACT_FIELDS:
            out[k] = {"from": "[set]" if b not in (None, "") else None,
                      "to": ("[changed]" if b not in (None, "") else "[set]") if a not in (None, "") else "[cleared]"}
        else:
            out[k] = {"from": mask_value(k, b), "to": mask_value(k, a)}
    return out


def _collapse_secret_columns(diff: dict) -> dict:
    """sin_enc + sin_hash + sin_masked changing together → one 'sin' entry."""
    out, seen = {}, set()
    for k, v in diff.items():
        base = k
        for suf in ("_enc", "_hash", "_masked", "_last4"):
            if k.endswith(suf):
                base = k[: -len(suf)]
                break
        if base != k:
            if base in seen or base in diff:
                continue
            seen.add(base)
            out[base] = {"from": v["from"], "to": v["to"]}
        else:
            out[k] = v
    return out


async def audit_change(request, action: str, *, entity_type: str, entity_id, before: Mapping | None = None,
                       after: Mapping | None = None, summary: str | None = None, extra: dict | None = None) -> bool:
    """One audit row for a create / update / delete with the masked field diff.
    Never raises (record_audit is best-effort); call AFTER the business commit."""
    op = "create" if not before and after else "delete" if before and not after else "update"
    changes = _collapse_secret_columns(masked_diff(before, after))
    if op == "update" and not changes:
        return False                          # nothing actually changed — no noise row
    meta = {"op": op, "changes": changes, "fields": sorted(changes)}
    if extra:
        meta.update(extra)
    if summary is None:
        summary = f"{entity_type} {op}" + (f": {', '.join(sorted(changes))[:200]}" if op == "update" else "")
    return await record_audit(request, action, entity_type=entity_type, entity_id=entity_id,
                              summary=summary, meta=meta)
