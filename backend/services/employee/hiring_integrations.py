"""Hiring / performance — the calls into OTHER streams' contracts, in one place.

  payroll     POST /payroll/revisions/bulk-draft               (stream B)
  onboarding  POST /onboarding/sessions, PATCH, GET            (stream A)
  letters     shared.utils.letters_client.issue_letter         (stream B)
  notify      shared.utils.notify.notify                       (stream E)

Every helper here is best-effort or returns a plain reason on failure: a
missing helper never breaks the hiring action itself.
"""
import logging
import sys
import uuid

import httpx
from sqlalchemy import text

sys.path.append("/app")
from shared.utils.config import settings  # noqa: E402

log = logging.getLogger("lexora.hiring")

# Guarded imports — these files belong to other streams and may not be on disk.
try:
    from shared.utils.letters_client import issue_letter as _issue_letter  # type: ignore
except Exception:                                    # noqa: BLE001
    _issue_letter = None
try:
    from shared.utils.notify import notify as _notify  # type: ignore
except Exception:                                    # noqa: BLE001
    _notify = None

LETTERS_AVAILABLE = _issue_letter is not None
NOTIFY_AVAILABLE = _notify is not None


def fwd_headers(org: str, user_id: str = "", role: str = "", email: str = "") -> dict:
    """Trusted service-to-service identity (the same headers the gateway stamps)."""
    return {"X-Org-Id": str(org), "X-User-Id": user_id or "", "X-Role": role or "",
            "X-User-Email": email or "", "Content-Type": "application/json"}


async def org_by_code(db, code: str) -> dict | None:
    """Public careers page: org code -> {id, name, country}. Read-only."""
    row = (await db.execute(text(
        "SELECT id, name, country FROM organizations WHERE upper(code) = upper(:c) AND is_active = true"),
        {"c": (code or "").strip()})).first()
    return {"id": str(row[0]), "name": row[1], "country": row[2]} if row else None


async def org_by_id(db, org_id: str) -> dict:
    try:
        row = (await db.execute(text("SELECT name, code, country FROM organizations WHERE id = :i"),
                                {"i": str(org_id)})).first()
    except Exception:                                # noqa: BLE001
        await db.rollback()
        row = None
    return {"name": row[0], "code": row[1], "country": row[2]} if row else {"name": "", "code": None, "country": None}


async def create_onboarding_session(org: str, payload: dict, extra: dict, user_id: str, role: str) -> dict:
    """Create the joiner's onboarding session through stream A's existing create
    route (it also e-mails the joiner invite). `extra` carries the fields the
    create schema may not know yet (grade, manager id, phone, offer reference);
    they are merged into the session's collected data with the existing PATCH."""
    h = fwd_headers(org, user_id, role)
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(f"{settings.ONBOARDING_URL}/onboarding/sessions", json=payload, headers=h)
            if r.status_code >= 300:
                return {"ok": False, "reason": f"Onboarding answered {r.status_code}: {r.text[:200]}"}
            sess = r.json()
            sid = sess.get("id")
            if sid and extra:
                try:
                    await c.patch(f"{settings.ONBOARDING_URL}/onboarding/sessions/{sid}",
                                  json={"data": extra}, headers=h)
                except httpx.HTTPError:
                    pass
            return {"ok": True, "session_id": sid, "invited": bool(sess.get("invited_at") or sess.get("invite_token"))}
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": f"The onboarding service could not be reached ({exc.__class__.__name__})."}


async def onboarding_sessions(org: str, user_id: str = "", role: str = "") -> list[dict] | None:
    """All sessions of the org (id, status, employee_id). None = could not ask."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{settings.ONBOARDING_URL}/onboarding/sessions", headers=fwd_headers(org, user_id, role))
    except httpx.HTTPError:
        return None
    if r.status_code >= 300:
        return None
    d = r.json()
    return d if isinstance(d, list) else (d.get("items") or d.get("sessions") or [])


OFFER_TEMPLATE_KEY = "offer_letter"          # stream B's seeded key (its client docstring lists the keys)
try:
    from shared.utils.letters_client import issue_recipient_letter as _issue_recipient_letter  # type: ignore
except Exception:                                    # noqa: BLE001
    _issue_recipient_letter = None


async def issue_offer_letter(org: str, employee_id: str | None, data: dict, actor: dict,
                             recipient: dict | None = None) -> dict:
    """Offer letter through the letter engine.

    Before joining there is no employee row: the letter is issued to the CANDIDATE
    (`recipient`: name, address, offered role, CTC passed as data) with the same
    numbering and immutability, and re-linked to the employee after activation.
    Once the person is an employee it is issued against the employee row."""
    if _issue_letter is None:
        return {"ok": False, "reason": "The letter engine is not installed yet (shared/utils/letters_client.py)."}
    if not employee_id and not recipient:
        return {"ok": False, "reason": "No employee and no candidate details to address the offer letter to."}
    try:
        if employee_id:
            res = await _issue_letter(org, employee_id, OFFER_TEMPLATE_KEY, data, actor, source="hiring_offer")
        else:
            res = await _issue_recipient_letter(org, recipient, OFFER_TEMPLATE_KEY, data, actor, source="hiring_offer")
    except Exception as exc:                         # noqa: BLE001  (LetterError carries the engine's message)
        if type(exc).__name__ == "LetterError":      # domain error: its plain message is meant for the user
            log.warning("offer letter failed: %s", exc)
            return {"ok": False, "reason": f"The letter engine did not issue the offer letter: {str(exc)[:250]}"}
        ref = uuid.uuid4().hex[:8].upper()           # anything else: log it, answer a fixed sentence + ref
        log.exception("offer letter failed (ref %s)", ref)
        return {"ok": False, "reason": f"The letter engine did not issue the offer letter (ref {ref})."}
    return {"ok": True, "letter": res if isinstance(res, dict) else {"ref": str(res)}}


async def notify(org: str, *, user_ids: list | None = None, roles: list | None = None,
                 title: str, body: str = "", link: str = "", kind: str = "info") -> bool:
    """Best-effort. Tries the published keyword form first."""
    if _notify is None:
        return False
    try:
        kw = {"title": title, "body": body, "link": link, "kind": kind}
        if user_ids:
            kw["user_ids"] = [str(u) for u in user_ids if u]
        if roles:
            kw["roles"] = list(roles)
        await _notify(org, **kw)
        return True
    except Exception as exc:                         # noqa: BLE001
        log.warning("notify failed: %s", exc)
        return False


async def user_ids_for_employees(db, org: str, employee_ids: list) -> list[str]:
    """Logins linked to these employees (read-only on the shared users table)."""
    ids = [str(e) for e in employee_ids if e]
    if not ids:
        return []
    try:
        rows = (await db.execute(text(
            "SELECT id FROM users WHERE org_id = :o AND CAST(employee_id AS text) = ANY(:ids)"),
            {"o": str(org), "ids": ids})).all()
        return [str(r[0]) for r in rows]
    except Exception:                                # noqa: BLE001
        await db.rollback()
        return []


async def bulk_draft_revisions(org: str, payload: dict, user_id: str, role: str, email: str = "") -> dict:
    """Hand an increment proposal to stream B. B owns review, approval and
    applying — nothing here changes a salary."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{settings.PAYROLL_URL}/payroll/revisions/bulk-draft", json=payload,
                             headers=fwd_headers(org, user_id, role, email))
    except httpx.HTTPError as exc:
        return {"ok": False, "reason": f"The payroll service could not be reached ({exc.__class__.__name__})."}
    if r.status_code == 404:
        return {"ok": False, "reason": "Salary revisions are not available in payroll yet (bulk-draft not found)."}
    if r.status_code >= 300:
        return {"ok": False, "reason": f"Payroll refused the draft ({r.status_code}): {r.text[:300]}"}
    d = r.json() if r.content else {}
    batch = d.get("batch_id") or d.get("id") or d.get("batch") if isinstance(d, dict) else d
    if isinstance(batch, dict):
        batch = batch.get("id")
    return {"ok": True, "batch_id": str(batch) if batch is not None else None, "response": d}
