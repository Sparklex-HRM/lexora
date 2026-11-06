"""Hiring D2 — employee referrals and the referral bonus.

ESS (any employee with a linked login):
  GET  /hiring/referrals/jobs        open jobs + the bonus rule per job
  POST /hiring/referrals             refer someone (multipart: details + optional resume)
  GET  /hiring/referrals/mine        my referrals through the stages + bonus status
HR:
  GET  /hiring/referrals             every referral
  GET  /hiring/requisitions/{id}/referral-rule  · PUT (HR head)  {bonus_amount, payable_after_days, active}
  POST /hiring/referral-bonuses/sync            find bonuses that became payable (hire joined + N days)
  GET  /hiring/referral-bonuses                 the bonuses due / exported / paid
  POST /hiring/referral-bonuses/{id}/{mark-exported|mark-paid|cancel}
  GET  /hiring/referral-bonuses/for-payroll?month=YYYY-MM   payroll pulls them (engine `entries` shape)
  GET  /hiring/referral-bonuses/export.csv

PAYROLL HAND-OFF: none in Lexora HRM — a due referral bonus is paid as an off-cycle BONUS earning
by the pay office (the bonus list shows what is due). That table has NO write
API for other services and this service never writes payroll tables. So a payable bonus is
kept in ``hire_referral_bonuses_due`` and EXPOSED to payroll through /for-payroll (engine
shape, code REFBONUS, taxable earning) and the CSV; payroll marks it exported / paid here.
"""
import logging
import sys
from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import hiring_d2_logic as D  # noqa: E402
import hiring_integrations as ext  # noqa: E402
import hiring_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from hiring_common import emp_name, fail, get_actor, now, num, one, ser, uid  # noqa: E402
from hiring_d2_models import HireReferral, HireReferralBonusDue, HireReferralRule  # noqa: E402
from hiring_models import HireApplication, HireCandidate, HirePosting, HireRequisition, HireStageHistory  # noqa: E402
from hiring_routers import _all_candidates, load_settings, read_capped, store_document  # noqa: E402
from models import Employee  # noqa: E402

log = logging.getLogger("lexora.hiring.referrals")
router = APIRouter(prefix="/hiring", tags=["hiring-referrals"])
BONUS_CODE = "REFBONUS"
PAYROLL_ROLES = {"payroll", "payroll_admin", "finance", "finance_admin"}


async def rules_for(db: AsyncSession, org) -> dict[str, HireReferralRule]:
    rows = (await db.execute(select(HireReferralRule).where(HireReferralRule.org_id == org))).scalars().all()
    return {str(r.requisition_id): r for r in rows}


def rule_out(rule: HireReferralRule | None, s: dict) -> dict:
    if rule:
        return {"bonus_amount": float(rule.bonus_amount or 0), "payable_after_days": int(rule.payable_after_days or 0),
                "active": bool(rule.active), "source": "requisition"}
    return {"bonus_amount": float(s["referral_default_bonus"] or 0), "payable_after_days": int(s["referral_default_days"] or 0),
            "active": float(s["referral_default_bonus"] or 0) > 0, "source": "default"}


@router.get("/referrals/jobs")
async def referral_jobs(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    s = await load_settings(db, actor.org)
    rules = await rules_for(db, actor.org)
    org = await ext.org_by_id(db, actor.org)
    rows = (await db.execute(select(HirePosting, HireRequisition).join(HireRequisition, HireRequisition.id == HirePosting.requisition_id)
                             .where(HirePosting.org_id == actor.org, HirePosting.is_published.is_(True), HireRequisition.status == "open")
                             .order_by(HirePosting.published_at.desc()))).all()
    jobs = []
    for p, r in rows:
        ru = rule_out(rules.get(str(r.id)), s)
        jobs.append({"posting_id": str(p.id), "requisition_id": str(r.id), "title": p.title, "department": r.department,
                     "location": r.location, "employment_type": r.employment_type, "description": p.description,
                     "requirements": p.requirements, "published_at": p.published_at.isoformat() if p.published_at else None,
                     "bonus": ru})
    from hiring_boards import careers_url
    return {"linked": bool(actor.employee_id), "jobs": jobs,
            "careers_url": careers_url(org.get("code")), "share_hint": "?src=referral",
            "note": ("Refer someone below. HR sees who referred whom; the bonus, where a job has one, is paid through payroll "
                     "after the person joins and stays the required number of days.")}


def _referral_out(ref: HireReferral, app: HireApplication | None, today, hide_contact: bool = False) -> dict:
    d = ser(ref)
    if hide_contact:
        d["email"], d["phone"] = D.mask_contact(ref.email), D.mask_contact(ref.phone)
    stage = app.stage if app else None
    d["stage"] = stage
    d["stage_label"] = L.STAGE_LABEL.get(stage, stage) if stage else None
    joined = bool(app and app.stage == "joined" and app.joined_at)
    due = D.bonus_due_on(app.joined_at, ref.bonus_payable_after_days) if joined else None
    live = D.bonus_state(joined, due, today, ref.bonus_amount)
    d["bonus_state"] = ref.bonus_status if ref.bonus_status in ("exported", "paid", "void") else live
    d["bonus_due_on"] = due.isoformat() if due else None
    return d


@router.post("/referrals", status_code=201)
async def refer(request: Request, requisition_id: str = Form(...), first_name: str = Form(...), last_name: str = Form(""),
                email: str = Form(""), phone: str = Form(""), link_url: str = Form(""), note: str = Form(""),
                current_employer: str = Form(""), city: str = Form(""),
                resume: UploadFile | None = File(None), db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.employee_id:
        raise HTTPException(403, "Your login is not linked to an employee record, so a referral cannot be recorded under your name.")
    r = await one(db, HireRequisition, requisition_id, actor.org, "Requisition")
    if r.status != "open":
        raise HTTPException(409, "This job is not open any more.")
    try:
        clean = D.clean_referral({"first_name": first_name, "last_name": last_name, "email": email, "phone": phone,
                                  "link_url": link_url, "note": note})
    except D.RuleError as e:
        fail(e)
    s = await load_settings(db, actor.org)
    rule = (await rules_for(db, actor.org)).get(str(r.id))
    ru = rule_out(rule, s)
    posting = (await db.execute(select(HirePosting).where(HirePosting.org_id == actor.org, HirePosting.requisition_id == r.id,
                                                          HirePosting.is_published.is_(True)))).scalars().first()
    ref = HireReferral(org_id=uid(actor.org), referrer_employee_id=uid(actor.employee_id), referrer_name=actor.name,
                       requisition_id=r.id, posting_id=posting.id if posting else None, **clean,
                       bonus_amount=ru["bonus_amount"] if ru["active"] else None,
                       bonus_payable_after_days=ru["payable_after_days"] if ru["active"] else None,
                       bonus_status="waiting_join" if ru["active"] and ru["bonus_amount"] > 0 else "none")
    # Duplicate check: same e-mail / phone already on file AND already applied to this job → no second application.
    cands = await _all_candidates(db, actor.org)
    slim = [{"id": str(c.id), "email_norm": c.email_norm, "phone_norm": c.phone_norm, "name": f"{c.first_name} {c.last_name or ''}".strip()} for c in cands]
    dups = L.find_duplicates(clean["email"], clean["phone"], slim)
    dup_ids = {uid(d["id"]) for d in dups}
    already = None
    if dup_ids:
        already = (await db.execute(select(HireApplication).where(
            HireApplication.org_id == actor.org, HireApplication.requisition_id == r.id,
            HireApplication.candidate_id.in_(dup_ids)))).scalars().first()
    if already is not None:
        ref.status, ref.status_note = "duplicate", "This person had already applied for this job before your referral."
        ref.candidate_id, ref.application_id, ref.bonus_status = already.candidate_id, already.id, "void"
        db.add(ref)
        await db.commit()
        return {**_referral_out(ref, already, await org_today(actor.org)), "message": ref.status_note}
    c = HireCandidate(org_id=uid(actor.org), first_name=clean["first_name"], last_name=clean["last_name"], email=clean["email"],
                      email_norm=L.norm_email(clean["email"]), phone=clean["phone"], phone_norm=L.norm_phone(clean["phone"]),
                      tags=["referral"], created_by=actor.user_id,
                      **D.clean_referral_candidate({"current_employer": current_employer, "city": city}))
    db.add(c)
    await db.flush()
    a = HireApplication(org_id=c.org_id, candidate_id=c.id, requisition_id=r.id, posting_id=posting.id if posting else None,
                        source_type="referral", source_detail="employee portal", referrer_employee_id=uid(actor.employee_id),
                        referrer_name=actor.name, stage="applied", created_by=actor.user_id)
    db.add(a)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "This person already has an application for this job.")
    if resume is not None and resume.filename:
        content = await read_capped(resume, s["resume_max_mb"])
        await store_document(db, actor.org, c.id, a.id, "resume", resume.filename, content, s["resume_max_mb"], actor.user_id)
    db.add(HireStageHistory(org_id=c.org_id, application_id=a.id, from_stage=None, to_stage="applied",
                            reason=f"Referred by {actor.name}", by_user=actor.user_id, by_name=actor.name))
    # The referred person has not agreed to anything yet: no automatic e-mails until HR records consent.
    from hiring_comms import set_prefs
    await set_prefs(db, actor.org, c.id, email_opt_in=False, whatsapp_opt_in=False)
    ref.candidate_id, ref.application_id, ref.status = c.id, a.id, "in_process"
    db.add(ref)
    await db.commit()
    await record_audit(request, "hiring.referral.create", entity_type="hire_referral", entity_id=str(ref.id),
                       summary=f"Referral for {r.designation}", meta={"possible_duplicates": len(dups)})
    await ext.notify(actor.org, roles=["hr"], title="New employee referral", body=f"{r.designation} · referred by {actor.name}",
                     link="/hiring/referrals", kind="hiring")
    return {**_referral_out(ref, a, await org_today(actor.org)),
            "message": "Thank you. HR has been told and will contact the person." + (" (Someone with the same e-mail or phone is already on file; HR will check.)" if dups else "")}


async def _apps_for(db, org, refs) -> dict:
    ids = {r.application_id for r in refs if r.application_id}
    if not ids:
        return {}
    return {str(a.id): a for a in (await db.execute(select(HireApplication).where(
        HireApplication.org_id == org, HireApplication.id.in_(ids)))).scalars().all()}


@router.get("/referrals/mine")
async def my_referrals(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.employee_id:
        return {"linked": False, "items": []}
    refs = (await db.execute(select(HireReferral).where(
        HireReferral.org_id == actor.org, HireReferral.referrer_employee_id == uid(actor.employee_id))
        .order_by(HireReferral.created_at.desc()))).scalars().all()
    apps = await _apps_for(db, actor.org, refs)
    reqs = {str(r.id): r for r in (await db.execute(select(HireRequisition).where(HireRequisition.org_id == actor.org))).scalars().all()}
    today = await org_today(actor.org)
    out = []
    for ref in refs:
        d = _referral_out(ref, apps.get(str(ref.application_id)), today)
        rq = reqs.get(str(ref.requisition_id))
        d["designation"] = rq.designation if rq else None
        out.append(d)
    return {"linked": True, "items": out}


@router.get("/referrals")
async def all_referrals(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr:
        raise HTTPException(403, "Only HR can see all referrals.")
    refs = (await db.execute(select(HireReferral).where(HireReferral.org_id == actor.org)
                             .order_by(HireReferral.created_at.desc()))).scalars().all()
    apps = await _apps_for(db, actor.org, refs)
    reqs = {str(r.id): r for r in (await db.execute(select(HireRequisition).where(HireRequisition.org_id == actor.org))).scalars().all()}
    today = await org_today(actor.org)
    out = []
    for ref in refs:
        d = _referral_out(ref, apps.get(str(ref.application_id)), today)
        rq = reqs.get(str(ref.requisition_id))
        d["designation"], d["req_number"] = (rq.designation if rq else None), (rq.req_number if rq else None)
        out.append(d)
    return out


@router.get("/requisitions/{req_id}/referral-rule")
async def get_rule(req_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    r = await one(db, HireRequisition, req_id, actor.org, "Requisition")
    s = await load_settings(db, actor.org)
    return rule_out((await rules_for(db, actor.org)).get(str(r.id)), s)


@router.put("/requisitions/{req_id}/referral-rule")
async def put_rule(req_id: str, body: dict, request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not actor.is_hr_head:
        raise HTTPException(403, "Only an HR head or an org admin can set a referral bonus.")
    r = await one(db, HireRequisition, req_id, actor.org, "Requisition")
    amount = num(body.get("bonus_amount"), "Bonus amount", required=True, lo=0)
    days = int(num(body.get("payable_after_days"), "Payable after (days)", required=True, lo=0, hi=365))
    rule = (await rules_for(db, actor.org)).get(str(r.id))
    if not rule:
        rule = HireReferralRule(org_id=uid(actor.org), requisition_id=r.id)
        db.add(rule)
    rule.bonus_amount, rule.payable_after_days = amount, days
    rule.active = bool(body.get("active", amount > 0))
    rule.updated_by, rule.updated_at = actor.user_id, now()
    await db.commit()
    await record_audit(request, "hiring.referral.rule", entity_type="hire_requisition", entity_id=str(r.id),
                       summary=f"Referral bonus for {r.req_number}", meta={"bonus_amount": amount, "payable_after_days": days, "active": rule.active})
    s = await load_settings(db, actor.org)
    return rule_out(rule, s)


# ── bonus lifecycle ─────────────────────────────────────────────────────────
async def sync_bonuses(db: AsyncSession, org, today) -> dict:
    """Referrals whose hire joined: mark waiting_days / due; create the due row once the day arrives."""
    refs = (await db.execute(select(HireReferral).where(
        HireReferral.org_id == org, HireReferral.bonus_status.in_(("waiting_join", "waiting_days", "due"))))).scalars().all()
    apps = await _apps_for(db, org, refs)
    existing = {str(x[0]) for x in (await db.execute(select(HireReferralBonusDue.referral_id).where(
        HireReferralBonusDue.org_id == org))).all()}
    created = 0
    for ref in refs:
        a = apps.get(str(ref.application_id))
        if not a:
            continue
        if a.stage in ("rejected", "withdrawn"):
            ref.status, ref.bonus_status, ref.updated_at = "closed", "void", now()
            continue
        if a.stage != "joined" or not a.joined_at:
            continue
        due = D.bonus_due_on(a.joined_at, ref.bonus_payable_after_days)
        ref.status, ref.bonus_due_on = "hired", due
        state = D.bonus_state(True, due, today, ref.bonus_amount)
        ref.bonus_status, ref.updated_at = state, now()
        if state == "due" and str(ref.id) not in existing:
            emp = (await db.execute(select(Employee).where(Employee.id == ref.referrer_employee_id))).scalar_one_or_none()
            pay_month = D.pay_month_for(max(due, today))
            db.add(HireReferralBonusDue(org_id=uid(org), referral_id=ref.id, employee_id=ref.referrer_employee_id,
                                        employee_name=emp_name(emp) if emp else ref.referrer_name,
                                        hired_name=f"{ref.first_name} {ref.last_name or ''}".strip(),
                                        amount=float(ref.bonus_amount or 0), due_on=due, pay_month=pay_month, status="due",
                                        note=f"Referral bonus: {ref.first_name} joined on {D.bonus_due_on(a.joined_at, 0):%d %b %Y}; payable after "
                                             f"{ref.bonus_payable_after_days} days."))
            existing.add(str(ref.id))
            created += 1
    await db.commit()
    return {"checked": len(refs), "new_due": created}


@router.post("/referral-bonuses/sync")
async def sync_route(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not (actor.is_hr or actor.role in PAYROLL_ROLES):
        raise HTTPException(403, "Only HR or payroll can refresh referral bonuses.")
    return await sync_bonuses(db, actor.org, await org_today(actor.org))


@router.get("/referral-bonuses")
async def list_bonuses(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not (actor.is_hr or actor.role in PAYROLL_ROLES):
        raise HTTPException(403, "Only HR or payroll can see referral bonuses.")
    await sync_bonuses(db, actor.org, await org_today(actor.org))
    rows = (await db.execute(select(HireReferralBonusDue).where(HireReferralBonusDue.org_id == actor.org)
                             .order_by(HireReferralBonusDue.due_on.desc()))).scalars().all()
    return {"items": [ser(r) for r in rows], "code": BONUS_CODE,
            "payroll_note": ("Payroll pulls these as a taxable earning (code REFBONUS) for the pay month through "
                             "GET /hiring/referral-bonuses/for-payroll?month=YYYY-MM, or from the CSV. Mark them exported, then paid.")}


@router.post("/referral-bonuses/{bonus_id}/{action}")
async def bonus_action(bonus_id: str, action: str, request: Request, body: dict | None = None, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not (actor.is_hr or actor.role in PAYROLL_ROLES):
        raise HTTPException(403, "Only HR or payroll can update referral bonuses.")
    b = await one(db, HireReferralBonusDue, bonus_id, actor.org, "Referral bonus")
    to = {"mark-exported": "exported", "mark-paid": "paid", "cancel": "cancelled"}.get(action)
    if not to:
        raise HTTPException(404, "Unknown action")
    allowed = {"exported": ("due",), "paid": ("due", "exported"), "cancelled": ("due", "exported")}[to]
    if b.status not in allowed:
        raise HTTPException(409, f"A bonus that is {b.status} cannot be marked {to}.")
    b.status, b.updated_at = to, now()
    if (body or {}).get("ref"):
        b.pay_entry_ref = str(body["ref"])[:80]
    if (body or {}).get("note"):
        b.note = str(body["note"])[:400]
    ref = (await db.execute(select(HireReferral).where(HireReferral.id == b.referral_id))).scalar_one_or_none()
    if ref:
        ref.bonus_status = {"exported": "exported", "paid": "paid", "cancelled": "void"}[to]
    await db.commit()
    await record_audit(request, f"hiring.referral_bonus.{to}", entity_type="hire_referral_bonus", entity_id=str(b.id),
                       summary=f"Referral bonus {to}: {b.employee_name}", meta={"amount": float(b.amount), "pay_month": b.pay_month})
    return ser(b)


@router.get("/referral-bonuses/for-payroll")
async def for_payroll(request: Request, month: str, db: AsyncSession = Depends(get_db)):
    """Engine `entries` shape for one pay month: {employee_id: {"earnings": [{code, amount, source, source_ref}]}}."""
    actor = await get_actor(request, db)
    if not (actor.is_hr or actor.role in PAYROLL_ROLES):
        raise HTTPException(403, "Only HR or payroll can read referral bonuses.")
    rows = (await db.execute(select(HireReferralBonusDue).where(
        HireReferralBonusDue.org_id == actor.org, HireReferralBonusDue.pay_month == month[:7],
        HireReferralBonusDue.status.in_(("due", "exported"))))).scalars().all()
    entries: dict = defaultdict(lambda: {"earnings": []})
    for b in rows:
        entries[str(b.employee_id)]["earnings"].append({"code": BONUS_CODE, "amount": float(b.amount), "taxable": True,
                                                        "source": "referral_bonus", "source_ref": str(b.id),
                                                        "name": f"Referral bonus ({b.hired_name})"})
    return {"month": month[:7], "entries": dict(entries), "count": len(rows)}


@router.get("/referral-bonuses/export.csv")
async def bonuses_csv(request: Request, db: AsyncSession = Depends(get_db)):
    actor = await get_actor(request, db)
    if not (actor.is_hr or actor.role in PAYROLL_ROLES):
        raise HTTPException(403, "Only HR or payroll can export referral bonuses.")
    rows = (await db.execute(select(HireReferralBonusDue).where(HireReferralBonusDue.org_id == actor.org)
                             .order_by(HireReferralBonusDue.pay_month, HireReferralBonusDue.employee_name))).scalars().all()
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["bonus_id", "employee_id", "employee_name", "hired_name", "code", "amount", "due_on", "pay_month", "status", "pay_entry_ref"])
    for b in rows:
        w.writerow([b.id, b.employee_id, b.employee_name, b.hired_name, BONUS_CODE, f"{float(b.amount):.2f}", b.due_on, b.pay_month, b.status, b.pay_entry_ref or ""])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="referral-bonuses.csv"'})
