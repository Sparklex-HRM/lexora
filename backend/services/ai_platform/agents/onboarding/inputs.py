"""Onboarding Agent — READ-ONLY collection. SELECT only (``watchdogs.rows``: a missing table = empty result, never a crash).

A CASE is one joiner: an onboarding session (invited → submitted → approved → activated), an employee who joined in the
window without a session (joiner created directly), or an accepted offer whose onboarding never started. Encrypted
identifiers are never decrypted here — only their PRESENCE (and the masked / hashed form) is read.
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import date, datetime, timedelta

sys.path.append("/app")
from shared.utils import platform_models as pm  # noqa: E402

from watchdogs import ORG, rows  # noqa: E402

log = logging.getLogger("lexora.agentic.onboarding")

INSTITUTION_RE = re.compile(r"^[0-9]{3}$")
TRANSIT_RE = re.compile(r"^[0-9]{5}$")
POSTAL_RE = re.compile(r"^[ABCEGHJ-NPRSTVXY][0-9][ABCEGHJ-NPRSTV-Z] ?[0-9][ABCEGHJ-NPRSTV-Z][0-9]$")
ID_DOCS = ("photo_id", "passport", "work_permit")
TEMP_ELIGIBILITY = ("work_permit", "study_permit")
EMP_COLS = ("id, employee_number, first_name, last_name, status, hire_date, manager_id, gender, annual_salary, email, "
            "sin_enc, sin_encrypted, bank_institution, bank_transit, bank_account_last4, bank_account_enc, address_line1, city, postal_code, "
            "td1_federal_claim, td1_provincial_claim, work_eligibility, work_permit_expiry, province_of_employment, address_country")


def _d(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _present(v) -> bool:
    return v is not None and str(v).strip() not in ("", "None", "null")


async def _employees(org: str, since: date, until: date) -> list[dict]:
    return await rows(f"SELECT {EMP_COLS} FROM employees WHERE org_id = {ORG} AND is_active IS TRUE AND hire_date >= :s AND hire_date <= :u",
                      {"org": org, "s": since, "u": until})


def _postal_ok(v) -> bool:
    return _present(v) and bool(POSTAL_RE.match(str(v).strip().upper()))


def _emp_fields(e: dict) -> dict:
    """Presence only — the SIN and the account number are never decrypted here."""
    return {"sin": _present(e.get("sin_enc")) or _present(e.get("sin_encrypted")), "sin_valid": None,
            "bank_account": _present(e.get("bank_account_last4")) or _present(e.get("bank_account_enc")),
            "bank_institution": (str(e.get("bank_institution")).strip() if _present(e.get("bank_institution")) else None),
            "bank_transit": (str(e.get("bank_transit")).strip() if _present(e.get("bank_transit")) else None),
            "address": _present(e.get("address_line1")) and _present(e.get("city")) and _postal_ok(e.get("postal_code")),
            "td1_federal": e.get("td1_federal_claim") not in (None, ""), "td1_provincial": e.get("td1_provincial_claim") not in (None, ""),
            "temporary_resident": (e.get("work_eligibility") or "") in TEMP_ELIGIBILITY, "work_permit_expiry": _d(e.get("work_permit_expiry")),
            "province": e.get("province_of_employment"), "hire_date": _d(e.get("hire_date")), "annual_salary": _f(e.get("annual_salary"))}


def _session_fields(data: dict) -> dict:
    data = data or {}
    return {"sin": _present(data.get("sin")), "sin_valid": None,
            "bank_account": _present(data.get("bank_account")),
            "bank_institution": (str(data.get("bank_institution")).strip() if _present(data.get("bank_institution")) else None),
            "bank_transit": (str(data.get("bank_transit")).strip() if _present(data.get("bank_transit")) else None),
            "address": _present(data.get("address_line1")) and _present(data.get("city")) and _postal_ok(data.get("postal_code")),
            "td1_federal": data.get("td1_federal_claim") not in (None, ""), "td1_provincial": data.get("td1_provincial_claim") not in (None, ""),
            "temporary_resident": (data.get("work_eligibility") or "") in TEMP_ELIGIBILITY, "work_permit_expiry": _d(data.get("work_permit_expiry")),
            "province": data.get("province_of_employment"), "hire_date": _d(data.get("hire_date")), "annual_salary": _f(data.get("annual_salary"))}


async def collect(org: str, *, today: date, lookback_days: int = 45, ahead_days: int = 30) -> dict:
    since, until = today - timedelta(days=int(lookback_days)), today + timedelta(days=int(ahead_days))
    sources: dict = {}
    sessions = await rows(
        "SELECT id, status, employee_number, first_name, last_name, email, employee_id, data, created_at, submitted_at, activated_at "
        f"FROM onboarding_sessions WHERE org_id = {ORG} AND status IN ('invited','submitted','approved','activated') AND created_at >= :c",
        {"org": org, "c": pm.now_utc() - timedelta(days=int(lookback_days) + 60)})
    sources["onboarding_sessions"] = f"{len(sessions)} rows"
    emps = await _employees(org, since, until)
    sources["employees"] = f"{len(emps)} joiners in the window"
    offers = await rows(f"SELECT id, candidate_id, joining_date, designation, department, manager_id, onboarding_session_id, responded_at FROM hire_offers "
                        f"WHERE org_id = {ORG} AND status = 'accepted' AND (joining_date IS NULL OR joining_date >= :s)", {"org": org, "s": since})
    sources["hire_offers"] = f"{len(offers)} accepted"
    doc_cut = pm.now_utc() - timedelta(days=int(lookback_days) + 240)
    docs = await rows(f"SELECT id, employee_id, onboarding_session_id, doc_type, verified FROM employee_documents WHERE org_id = {ORG} AND deleted_at IS NULL "
                      "AND uploaded_at >= :c", {"org": org, "c": doc_cut})
    consents = await rows(f"SELECT session_id, consent_type FROM onboarding_consents WHERE org_id = {ORG}", {"org": org})
    tasks = await rows(f"SELECT id, session_id, title, owner, due_date, done FROM onboarding_tasks WHERE org_id = {ORG} AND done IS NOT TRUE", {"org": org})
    letters = await rows(f"SELECT employee_id, status, id FROM issued_letters WHERE org_id = {ORG} AND template_key = 'appointment_letter' AND status IN ('draft','issued')", {"org": org})
    assets = await rows(f"SELECT employee_id FROM employee_assets WHERE org_id = {ORG} AND status = 'assigned'", {"org": org})
    lms = await rows(f"SELECT employee_id FROM eng_course_assignments WHERE org_id = {ORG}", {"org": org})
    sources.update({"documents": f"{len(docs)} rows", "consents": f"{len(consents)} rows", "letters": f"{len(letters)} appointment letters"})

    def by(rows_, key):
        out: dict = {}
        for r in rows_:
            if r.get(key):
                out.setdefault(str(r[key]), []).append(r)
        return out
    docs_emp, docs_ses = by(docs, "employee_id"), by(docs, "onboarding_session_id")
    cons, tks, lets = by(consents, "session_id"), by(tasks, "session_id"), by(letters, "employee_id")
    ast, lm = by(assets, "employee_id"), by(lms, "employee_id")

    cases: list[dict] = []
    emp_by_id = {str(e["id"]): e for e in emps}
    session_emp: set[str] = set()
    for s in sessions:
        eid = str(s["employee_id"]) if s.get("employee_id") else None
        e = emp_by_id.get(eid) if eid else None
        if eid and not e:
            got = await rows(f"SELECT {EMP_COLS} FROM employees WHERE org_id = {ORG} AND id = CAST(:e AS uuid)", {"org": org, "e": eid})
            e = got[0] if got else None
        if s["status"] == "activated" and e and _d(e.get("hire_date")) and _d(e.get("hire_date")) < since:
            continue                                                  # settled long ago: out of the window
        if eid:
            session_emp.add(eid)
        data = s.get("data") if isinstance(s.get("data"), dict) else {}
        fields = _emp_fields(e) if e else _session_fields(data)
        name = (f"{(e or s).get('first_name') or ''} {(e or s).get('last_name') or ''}").strip()
        sid = str(s["id"])
        dts = (docs_emp.get(eid, []) if eid else []) + docs_ses.get(sid, [])
        cases.append({"case_key": f"ses:{sid}", "kind": "session", "status": s["status"], "session_id": sid,
                      "employee": {"id": eid, "employee_number": (e or s).get("employee_number"), "name": name, "manager_id": str((e or {}).get("manager_id") or data.get("manager_id") or "") or None,
                                   "gender": (e or {}).get("gender") or data.get("gender")},
                      "fields": fields, "docs": {"types": sorted({d["doc_type"] for d in dts}), "unverified": [{"id": str(d["id"]), "doc_type": d["doc_type"]} for d in dts if not d.get("verified")]},
                      "consents": sorted({c["consent_type"] for c in cons.get(sid, [])}), "tasks": tks.get(sid, []),
                      "letter": (lets.get(eid) or [None])[0] if eid else None, "assets": len(ast.get(eid, [])) if eid else 0,
                      "lms": len(lm.get(eid, [])) if eid else 0})
    for e in emps:
        eid = str(e["id"])
        if eid in session_emp:
            continue
        cases.append({"case_key": f"emp:{eid}", "kind": "employee", "status": e.get("status"), "session_id": None,
                      "employee": {"id": eid, "employee_number": e.get("employee_number"), "name": f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(),
                                   "manager_id": str(e["manager_id"]) if e.get("manager_id") else None, "gender": e.get("gender")},
                      "fields": _emp_fields(e), "docs": {"types": sorted({d["doc_type"] for d in docs_emp.get(eid, [])}),
                                                          "unverified": [{"id": str(d["id"]), "doc_type": d["doc_type"]} for d in docs_emp.get(eid, []) if not d.get("verified")]},
                      "consents": None, "tasks": [], "letter": (lets.get(eid) or [None])[0], "assets": len(ast.get(eid, [])), "lms": len(lm.get(eid, []))})
    for o in offers:
        if o.get("onboarding_session_id"):
            continue
        cases.append({"case_key": f"offer:{o['id']}", "kind": "offer", "status": "accepted", "offer": o, "employee": {"id": None, "name": o.get("designation") or "Accepted offer"},
                      "fields": {}, "docs": {"types": [], "unverified": []}, "consents": None, "tasks": [], "letter": None, "assets": 0, "lms": 0})
    return {"today": today, "cases": cases, "sources": sources}
