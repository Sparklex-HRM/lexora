"""HR Desk routes — ``/ai/desk/*`` (stream D, 2026-10-25).

  Policies (HR admin: org_admin + hr family)
    GET    /ai/desk/policies                      list + clause counts + readiness
    POST   /ai/desk/policies                      {key, title, owner}
    GET    /ai/desk/policies/{pid}                policy + every clause version
    PATCH  /ai/desk/policies/{pid}                {title, owner, status}
    POST   /ai/desk/policies/{pid}/import         text/markdown in JSON {text, source_doc, effective_from, scope} OR multipart file
                                                  (.txt / .md / .docx / .pdf) → DRAFT clauses with AI-readiness scores
    POST   /ai/desk/policies/{pid}/clauses        add one draft clause by hand
    PATCH  /ai/desk/clauses/{cid}                 edit a DRAFT (an approved clause is immutable → creates the next draft version)
    POST   /ai/desk/clauses/{cid}/approve         SME sign-off → approved; the previous approved version of the same clause is closed
    POST   /ai/desk/clauses/{cid}/retire          → retired (effective_to = today)
    GET    /ai/desk/policies/{pid}/readiness      scores + notes per clause
  Ask (every signed-in role; answers only about the caller)
    POST   /ai/desk/ask                           {question, session_id?, history?} → engine.handle result
    POST   /ai/desk/feedback                      {session_id, turn_id?, rating up|down, comment?}
  IC (Internal Committee members ONLY — desk.settings.is_ic_member; never HR / org_admin by role)
    GET    /ai/desk/ic-referrals                  sealed referrals (id, employee_id, created_at, channel, acknowledged)
    POST   /ai/desk/ic-referrals/{rid}/acknowledge
    GET    /ai/desk/ic-referrals/access           {ic_member: bool} — the UI uses it to show / hide the inbox
  Metrics (HR)
    GET    /ai/desk/metrics?days=7                deflection / abstain / hand-off / CSAT / top intents with definitions
    GET    /ai/desk/config                        effective desk settings (threshold, SLA, templates; IC member list hidden)
  Settings (org_admin / superadmin — phase 2, P2-C; the generic PUT /settings/{group} refuses this group)
    GET    /ai/desk/settings                      stored + effective ai_desk settings (IC member list included)
    PUT    /ai/desk/settings                      {abstain_threshold, top_k, ic{presiding_officer_*, members[], external_member_contact},
                                                   templates, sla_hours, helpline, disclosure, handoff_*} — merged, audited
  HR Desk agent dashboard (phase 2, P2-C — office roles: org_admin, hr_admin, payroll_admin; superadmin passes)
    GET    /ai/desk/dashboard?days=7              add-on state, metrics with definitions, top questions, abstained questions
                                                  (→ "write a policy clause"), knowledge readiness, value counts, last run / report
    POST   /ai/desk/weekly-report {days}          generate the weekly "top questions" report + notify HR (409 without the add-on)
"""
from __future__ import annotations

import sys
from datetime import date

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

sys.path.append("/app")
from shared.utils import directory  # noqa: E402
from shared.utils import platform_models as pm  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.org_settings import get_setting  # noqa: E402
from shared.utils.org_time import org_today  # noqa: E402

import ai_config as CFG  # noqa: E402
import claude_client as LLM  # noqa: E402
import metering as MT  # noqa: E402
import tools_registry as TR  # noqa: E402

from desk import engine as EN  # noqa: E402
from desk import metrics as MX  # noqa: E402
from desk import policy_store as PS  # noqa: E402
from desk import settings as DS  # noqa: E402
from desk.policy_models import DeskIcReferral, DeskPolicy, DeskPolicyClause  # noqa: E402

router = APIRouter(prefix="/ai/desk", tags=["ai-desk"])
ADMIN = ("org_admin", "superadmin")
MAX_IMPORT_BYTES = 5 * 1024 * 1024


def _ids(request: Request) -> tuple[str, str]:
    org, uid = request.headers.get("x-org-id"), request.headers.get("x-user-id")
    if not org or not uid:
        raise HTTPException(401, "Missing user context")
    return org, uid


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _hr(request: Request) -> str:
    r = _role(request)
    if r in ADMIN or directory.role_in(r, ["hr"]):
        return r
    raise HTTPException(403, "Only HR can manage HR Desk policies")


OFFICE_ROLES = ("org_admin", "superadmin", "hr_admin", "payroll_admin")      # same office as /ai/agents (agentic_routers._office)


def _office(request: Request) -> str:
    r = _role(request)
    if r in OFFICE_ROLES:
        return r
    raise HTTPException(403, "The HR Desk dashboard is for the org admin, HR admin and payroll admin")


def _actor(request: Request) -> str:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or "unknown"


async def _ic(request: Request) -> dict:
    org, uid = _ids(request)
    cfg = await DS.load(org)
    if not DS.is_ic_member(cfg, uid, request.headers.get("x-acting-role")):
        raise HTTPException(404, "Not found")            # sealed: non-members do not learn the inbox exists
    return cfg


def _pol_out(p: DeskPolicy, counts: dict | None = None) -> dict:
    c = counts or {}
    return {"id": p.id, "key": p.key, "title": p.title, "owner": p.owner, "status": p.status, "source_doc": p.source_doc, "created_by": p.created_by,
            "created_at": pm.iso(p.created_at), "updated_at": pm.iso(p.updated_at), "clauses": c.get("total", 0), "approved": c.get("approved", 0),
            "draft": c.get("draft", 0), "avg_ready": c.get("avg_ready")}


# ══════════════════════════ policies ═══════════════════════════════════════════

class PolicyIn(BaseModel):
    key: str = Field(..., min_length=2, max_length=60, pattern=r"^[a-z0-9][a-z0-9_\-]*$")
    title: str = Field(..., min_length=2, max_length=200)
    owner: str = Field("", max_length=200)
    source_doc: str = Field("", max_length=200)


class PolicyPatch(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=200)
    owner: str | None = Field(None, max_length=200)
    status: str | None = Field(None, pattern=r"^(draft|approved|retired)$")


@router.get("/policies")
async def list_policies(request: Request):
    org, _ = _ids(request)
    _hr(request)
    async with pm.session() as s:
        pols = (await s.execute(select(DeskPolicy).where(DeskPolicy.org_id == org).order_by(DeskPolicy.title))).scalars().all()
        rows = (await s.execute(select(DeskPolicyClause.policy_id, DeskPolicyClause.status, func.count(), func.avg(DeskPolicyClause.ai_ready_score))
                                .where(DeskPolicyClause.org_id == org).group_by(DeskPolicyClause.policy_id, DeskPolicyClause.status))).all()
    counts: dict[str, dict] = {}
    for pid, st, n, avg in rows:
        c = counts.setdefault(pid, {"total": 0, "approved": 0, "draft": 0, "_sum": 0.0, "_n": 0})
        c["total"] += n
        c[st] = c.get(st, 0) + n
        c["_sum"] += float(avg or 0) * n
        c["_n"] += n
    for c in counts.values():
        c["avg_ready"] = round(c["_sum"] / c["_n"]) if c["_n"] else None
    return {"items": [_pol_out(p, counts.get(p.id)) for p in pols]}


@router.post("/policies", status_code=201)
async def create_policy(body: PolicyIn, request: Request):
    org, _ = _ids(request)
    _hr(request)
    async with pm.session() as s:
        dup = (await s.execute(select(DeskPolicy).where(DeskPolicy.org_id == org, DeskPolicy.key == body.key))).scalar_one_or_none()
        if dup:
            raise HTTPException(409, f"A policy with key '{body.key}' already exists")
        p = DeskPolicy(org_id=org, key=body.key, title=body.title.strip(), owner=body.owner.strip() or None, source_doc=body.source_doc or None, created_by=_actor(request))
        s.add(p)
        await s.flush()
        out = _pol_out(p)
        await s.commit()
    await record_audit(request, "desk.policy.create", entity_type="desk_policy", entity_id=out["id"], summary=f"HR Desk policy created: {out['title']}")
    return out


async def _policy(s, org: str, pid: str) -> DeskPolicy:
    p = await s.get(DeskPolicy, pid)
    if not p or p.org_id != org:
        raise HTTPException(404, "Policy not found")
    return p


@router.get("/policies/{pid}")
async def get_policy(pid: str, request: Request):
    org, _ = _ids(request)
    _hr(request)
    async with pm.session() as s:
        p = await _policy(s, org, pid)
        cls = (await s.execute(select(DeskPolicyClause).where(DeskPolicyClause.policy_id == pid, DeskPolicyClause.org_id == org)
                               .order_by(DeskPolicyClause.order_no, DeskPolicyClause.clause_id, DeskPolicyClause.version))).scalars().all()
    return {"policy": _pol_out(p), "clauses": [PS.clause_dict(c, p) for c in cls]}


@router.patch("/policies/{pid}")
async def patch_policy(pid: str, body: PolicyPatch, request: Request):
    org, _ = _ids(request)
    _hr(request)
    async with pm.session() as s:
        p = await _policy(s, org, pid)
        for k, v in body.model_dump(exclude_unset=True).items():
            if v is not None:
                setattr(p, k, v.strip() if isinstance(v, str) else v)
        await s.commit()
        out = _pol_out(p)
    await record_audit(request, "desk.policy.update", entity_type="desk_policy", entity_id=pid, summary=f"HR Desk policy updated: {out['title']}", meta=body.model_dump(exclude_unset=True))
    return out


class ImportIn(BaseModel):
    text: str = Field(..., min_length=20, max_length=400_000)
    source_doc: str = Field("", max_length=200)
    effective_from: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    scope: dict = Field(default_factory=dict)
    replace_drafts: bool = True


async def _import_text(request: Request, pid: str, text: str, source_doc: str, effective_from: str | None, scope: dict, replace_drafts: bool) -> dict:
    org, _ = _ids(request)
    parts = PS.split_clauses(text)
    if not parts:
        raise HTTPException(422, "No clauses found — the document needs headings or numbered sections")
    scope = {k: [str(x) for x in (scope.get(k) or [])] for k in ("locations", "grades", "employment_types", "roles")}
    async with pm.session() as s:
        p = await _policy(s, org, pid)
        existing = (await s.execute(select(DeskPolicyClause).where(DeskPolicyClause.policy_id == pid, DeskPolicyClause.org_id == org))).scalars().all()
        if replace_drafts:
            for c in existing:
                if c.status == "draft":
                    await s.delete(c)
            existing = [c for c in existing if c.status != "draft"]
        latest = {}
        for c in existing:
            latest[c.clause_id] = max(latest.get(c.clause_id, 0), int(c.version or 1))
        made = []
        for i, part in enumerate(parts):
            eff = part.get("effective_from") or effective_from
            score, notes = PS.ai_ready_score({**part, "effective_from": eff})
            row = DeskPolicyClause(org_id=org, policy_id=pid, clause_id=part["clause_id"][:40], version=latest.get(part["clause_id"], 0) + 1,
                                   title=part["title"][:200], text=part["text"], effective_from=eff, scope=scope, status="draft",
                                   ai_ready_score=score, ai_ready_notes=notes, source_doc=(source_doc or p.source_doc or None), order_no=i, created_by=_actor(request))
            s.add(row)
            made.append(row)
        if source_doc and not p.source_doc:
            p.source_doc = source_doc[:200]
        await s.flush()
        out = [PS.clause_dict(c, p) for c in made]
        await s.commit()
    await record_audit(request, "desk.policy.import", entity_type="desk_policy", entity_id=pid, summary=f"HR Desk: {len(out)} draft clause(s) imported", meta={"source_doc": source_doc})
    scores = [c["ai_ready_score"] for c in out]
    return {"imported": len(out), "clauses": out, "avg_ready": round(sum(scores) / len(scores)) if scores else None,
            "next_step": "Review each draft, fix the notes, then Approve (SME sign-off). Only approved clauses are used for answers."}


@router.post("/policies/{pid}/import")
async def import_policy(pid: str, request: Request, body: ImportIn | None = None, file: UploadFile | None = File(None)):
    _hr(request)
    if file is not None:
        data = await file.read()
        if len(data) > MAX_IMPORT_BYTES:
            raise HTTPException(413, "The file is over 5 MB")
        try:
            text = PS.extract_text(data, file.filename or "")
        except ValueError as e:
            raise HTTPException(422, str(e))
        form = await request.form()
        return await _import_text(request, pid, text, file.filename or "", str(form.get("effective_from") or "") or None, {}, str(form.get("replace_drafts", "1")) != "0")
    if body is None:
        raise HTTPException(422, "Send {text, …} as JSON or a file as multipart")
    return await _import_text(request, pid, body.text, body.source_doc, body.effective_from, body.scope, body.replace_drafts)


class ClauseIn(BaseModel):
    clause_id: str = Field(..., min_length=1, max_length=40)
    title: str = Field("", max_length=200)
    text: str = Field(..., min_length=5, max_length=20_000)
    effective_from: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_to: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    scope: dict = Field(default_factory=dict)


class ClausePatch(BaseModel):
    title: str | None = Field(None, max_length=200)
    text: str | None = Field(None, min_length=5, max_length=20_000)
    effective_from: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    effective_to: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    scope: dict | None = None
    clause_id: str | None = Field(None, min_length=1, max_length=40)


def _scored(row: DeskPolicyClause) -> None:
    row.ai_ready_score, row.ai_ready_notes = PS.ai_ready_score({"title": row.title, "text": row.text, "effective_from": row.effective_from})


@router.post("/policies/{pid}/clauses", status_code=201)
async def add_clause(pid: str, body: ClauseIn, request: Request):
    org, _ = _ids(request)
    _hr(request)
    async with pm.session() as s:
        p = await _policy(s, org, pid)
        ver = (await s.execute(select(func.max(DeskPolicyClause.version)).where(DeskPolicyClause.policy_id == pid, DeskPolicyClause.clause_id == body.clause_id))).scalar() or 0
        row = DeskPolicyClause(org_id=org, policy_id=pid, clause_id=body.clause_id, version=int(ver) + 1, title=body.title.strip(), text=body.text.strip(),
                               effective_from=body.effective_from, effective_to=body.effective_to, scope=body.scope or {}, status="draft", created_by=_actor(request))
        _scored(row)
        s.add(row)
        await s.flush()
        out = PS.clause_dict(row, p)
        await s.commit()
    return out


async def _clause(s, org: str, cid: str) -> tuple[DeskPolicyClause, DeskPolicy]:
    c = await s.get(DeskPolicyClause, cid)
    if not c or c.org_id != org:
        raise HTTPException(404, "Clause not found")
    return c, await s.get(DeskPolicy, c.policy_id)


@router.patch("/clauses/{cid}")
async def patch_clause(cid: str, body: ClausePatch, request: Request):
    """A draft is edited in place; an approved version is immutable — the edit becomes the next draft version."""
    org, _ = _ids(request)
    _hr(request)
    changes = body.model_dump(exclude_unset=True)
    async with pm.session() as s:
        c, p = await _clause(s, org, cid)
        if c.status == "retired":
            raise HTTPException(409, "A retired clause cannot be edited — add a new version instead")
        if c.status == "approved":
            ver = (await s.execute(select(func.max(DeskPolicyClause.version)).where(DeskPolicyClause.policy_id == c.policy_id, DeskPolicyClause.clause_id == c.clause_id))).scalar() or c.version
            row = DeskPolicyClause(org_id=org, policy_id=c.policy_id, clause_id=changes.get("clause_id") or c.clause_id, version=int(ver) + 1,
                                   title=changes.get("title", c.title), text=changes.get("text", c.text), effective_from=changes.get("effective_from", c.effective_from),
                                   effective_to=changes.get("effective_to", c.effective_to), scope=changes.get("scope") if changes.get("scope") is not None else (c.scope or {}),
                                   status="draft", source_doc=c.source_doc, order_no=c.order_no, created_by=_actor(request))
            _scored(row)
            s.add(row)
            await s.flush()
            out = PS.clause_dict(row, p)
            await s.commit()
            return {**out, "note": f"v{c.version} is approved and unchanged; your edit is draft v{row.version} — approve it to put it in force."}
        for k, v in changes.items():
            setattr(c, k, v.strip() if isinstance(v, str) and k != "clause_id" else v)
        _scored(c)
        await s.commit()
        return PS.clause_dict(c, p)


class ApproveIn(BaseModel):
    effective_from: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    note: str = Field("", max_length=300)


@router.post("/clauses/{cid}/approve")
async def approve_clause(cid: str, body: ApproveIn, request: Request):
    """SME sign-off. Closes the previously approved version of the same clause the day before this one starts."""
    org, uid = _ids(request)
    _hr(request)
    today = await org_today(org)
    async with pm.session() as s:
        c, p = await _clause(s, org, cid)
        if c.status != "draft":
            raise HTTPException(409, f"Only a draft can be approved (this version is {c.status})")
        if body.effective_from:
            c.effective_from = body.effective_from
        if not c.effective_from:
            c.effective_from = today.isoformat()
        if c.created_by and c.created_by == _actor(request) and (c.ai_ready_score or 0) < 40:
            raise HTTPException(422, f"AI-readiness {c.ai_ready_score}/100 — fix the notes before approving: " + "; ".join(c.ai_ready_notes or []))
        prev = (await s.execute(select(DeskPolicyClause).where(DeskPolicyClause.policy_id == c.policy_id, DeskPolicyClause.clause_id == c.clause_id,
                                                              DeskPolicyClause.status == "approved", DeskPolicyClause.id != c.id))).scalars().all()
        from datetime import datetime, timedelta
        day_before = (datetime.strptime(c.effective_from, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
        for old in prev:
            if not old.effective_to or old.effective_to >= c.effective_from:
                old.effective_to = day_before
            if old.effective_from and old.effective_to and old.effective_to < old.effective_from:
                old.status, old.retired_at = "retired", pm.now_utc()     # superseded before it ever applied
        c.status, c.approved_by, c.approved_at = "approved", _actor(request), pm.now_utc()
        if p.status == "draft":
            p.status = "approved"
        await s.commit()
        out = PS.clause_dict(c, p)
    await record_audit(request, "desk.clause.approve", entity_type="desk_policy_clause", entity_id=cid,
                       summary=f"HR Desk clause approved: {p.title} cl. {c.clause_id} v{c.version} effective {c.effective_from}", meta={"note": body.note})
    return out


@router.post("/clauses/{cid}/retire")
async def retire_clause(cid: str, request: Request):
    org, _ = _ids(request)
    _hr(request)
    today = await org_today(org)
    async with pm.session() as s:
        c, p = await _clause(s, org, cid)
        c.status, c.retired_at = "retired", pm.now_utc()
        if c.effective_to is None or c.effective_to > today.isoformat():
            c.effective_to = today.isoformat()
        await s.commit()
        out = PS.clause_dict(c, p)
    await record_audit(request, "desk.clause.retire", entity_type="desk_policy_clause", entity_id=cid, summary=f"HR Desk clause retired: {p.title} cl. {c.clause_id} v{c.version}")
    return out


@router.get("/policies/{pid}/readiness")
async def readiness(pid: str, request: Request):
    org, _ = _ids(request)
    _hr(request)
    async with pm.session() as s:
        p = await _policy(s, org, pid)
        cls = (await s.execute(select(DeskPolicyClause).where(DeskPolicyClause.policy_id == pid, DeskPolicyClause.org_id == org))).scalars().all()
    items = [{"id": c.id, "clause_id": c.clause_id, "version": c.version, "status": c.status, "score": c.ai_ready_score, "notes": list(c.ai_ready_notes or [])} for c in cls]
    scores = [i["score"] for i in items]
    return {"policy": _pol_out(p), "items": items, "avg": round(sum(scores) / len(scores)) if scores else None,
            "approved_avg": (lambda a: round(sum(a) / len(a)) if a else None)([i["score"] for i in items if i["status"] == "approved"])}


# ══════════════════════════ ask / feedback ═════════════════════════════════════

class AskIn(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = None
    history: list[dict] = Field(default_factory=list)


@router.post("/ask")
async def ask(body: AskIn, request: Request):
    org, uid = _ids(request)
    ctx = TR.Ctx(headers={k.lower(): v for k, v in request.headers.items()}, channel="chat")
    cfg = await CFG.load(org)
    key, key_src = CFG.resolve_key(cfg)
    client = LLM.client_for(key) if (key and CFG.sdk_available() and cfg["features"].get("qa", True)) else None
    today = await org_today(org)
    res = await EN.handle(ctx, body.question, session_id=body.session_id, history=body.history, client=client, model=cfg["model"], effort=cfg["effort"],
                          today=today, org_name=await directory.org_name(org))
    from desk import dispatch as DK
    await DK.meter(org, uid, ctx, res, cfg, key_src)
    res.pop("structure", None) if res.get("kind") != "pay_change" else None
    return res


class FeedbackIn(BaseModel):
    session_id: str
    turn_id: str | None = None
    rating: str = Field(..., pattern=r"^(up|down)$")
    comment: str = Field("", max_length=1000)


@router.post("/feedback")
async def feedback(body: FeedbackIn, request: Request):
    _ids(request)
    ctx = TR.Ctx(headers={k.lower(): v for k, v in request.headers.items()}, channel="chat")
    out = await EN.feedback(ctx, body.session_id, body.turn_id, body.rating, body.comment)
    if not out.get("ok"):
        raise HTTPException(404, out.get("error") or "Not found")
    return out


# ══════════════════════════ IC referrals (sealed) ══════════════════════════════

@router.get("/ic-referrals/access")
async def ic_access(request: Request):
    org, uid = _ids(request)
    cfg = await DS.load(org)
    return {"ic_member": DS.is_ic_member(cfg, uid, request.headers.get("x-acting-role"))}


@router.get("/ic-referrals")
async def ic_referrals(request: Request):
    org, uid = _ids(request)
    await _ic(request)
    async with pm.session() as s:
        rows = (await s.execute(select(DeskIcReferral).where(DeskIcReferral.org_id == org).order_by(DeskIcReferral.created_at.desc()).limit(500))).scalars().all()
    await record_audit(request, "desk.ic.view", entity_type="desk_ic_referrals", entity_id=org, summary="Designated recipient viewed the sealed referral inbox")
    return {"items": [{"id": r.id, "employee_id": r.employee_id, "channel": r.channel, "created_at": pm.iso(r.created_at), "acknowledged_by": r.acknowledged_by,
                       "acknowledged_at": pm.iso(r.acknowledged_at)} for r in rows],
            "note": "Each row means: this person asked HR Desk to reach the designated recipient about sexual harassment at this time. No text was stored. Follow the workplace harassment policy's procedure."}


@router.post("/ic-referrals/{rid}/acknowledge")
async def ic_acknowledge(rid: str, request: Request):
    org, uid = _ids(request)
    await _ic(request)
    async with pm.session() as s:
        r = await s.get(DeskIcReferral, rid)
        if not r or r.org_id != org:
            raise HTTPException(404, "Not found")
        if not r.acknowledged_at:
            r.acknowledged_by, r.acknowledged_at = uid, pm.now_utc()
        await s.commit()
        out = {"id": r.id, "acknowledged_by": r.acknowledged_by, "acknowledged_at": pm.iso(r.acknowledged_at)}
    await record_audit(request, "desk.ic.acknowledge", entity_type="desk_ic_referral", entity_id=rid, summary="IC member acknowledged a referral")
    return out


# ══════════════════════════ metrics / config ═══════════════════════════════════

@router.get("/metrics")
async def metrics(request: Request, days: int = 7):
    org, _ = _ids(request)
    _hr(request)
    return await MX.rollup_db(org, days)


@router.get("/config")
async def config(request: Request):
    org, _ = _ids(request)
    _hr(request)
    cfg = await DS.load(org)
    ic = dict(cfg.get("ic") or {})
    ic["members"] = len(ic.get("members") or [])                 # count only — the list is org_admin-only via /settings/ai_desk
    return {"abstain_threshold": cfg.get("abstain_threshold"), "top_k": cfg.get("top_k"), "sla_hours": cfg.get("sla_hours"), "helpline": cfg.get("helpline"),
            "templates_overridden": sorted((cfg.get("templates") or {}).keys()), "ic": ic, "disclosure": cfg.get("disclosure"),
            "handoff": {"unresolved_attempts": cfg.get("handoff_unresolved_attempts"), "abstains": cfg.get("handoff_abstains")},
            "where_to_edit": "Company settings → ai_desk (PUT /settings/ai_desk, org admin). IC members = login ids; or give members the custom role 'ic_member'.",
            "defaults": DS.defaults() | {"ic": {**DS.defaults()["ic"], "members": 0}}}


# ══════════════════════════ desk settings (org admin) — phase 2, P2-C ═══════════
# The generic PUT /settings/{group} (notification service) validates only its own groups and refuses ai_desk,
# so the desk owns its writer: GET/PUT /ai/desk/settings (org_admin / superadmin), same org_settings store + audit.

class DeskSettingsIn(BaseModel):
    abstain_threshold: float | None = Field(None, ge=0.0, le=1.0)
    top_k: int | None = Field(None, ge=1, le=12)
    ic: dict | None = None
    templates: dict | None = None
    sla_hours: dict | None = None
    helpline: dict | None = None
    disclosure: str | None = Field(None, max_length=600)
    handoff_unresolved_attempts: int | None = Field(None, ge=1, le=10)
    handoff_abstains: int | None = Field(None, ge=1, le=10)


def _admin_only(request: Request) -> str:
    r = _role(request)
    if r in ADMIN:
        return r
    raise HTTPException(403, "Only an org admin can change HR Desk settings (the IC member list decides who sees the sealed inbox)")


def _clean_desk_settings(body: dict, current: dict) -> dict:
    out = dict(current)
    for k in ("abstain_threshold", "top_k", "disclosure", "handoff_unresolved_attempts", "handoff_abstains"):
        if body.get(k) is not None:
            out[k] = body[k]
    if body.get("ic") is not None:
        ic = dict(out.get("ic") or {})
        src_ic = body["ic"]
        for k in ("presiding_officer_name", "presiding_officer_contact", "external_member_contact"):
            if k in src_ic:
                ic[k] = str(src_ic.get(k) or "")[:200]
        if "members" in src_ic:
            members = [str(m).strip()[:64] for m in (src_ic.get("members") or []) if str(m).strip()]
            if len(members) > 50:
                raise HTTPException(422, "At most 50 IC member logins")
            ic["members"] = sorted(set(members))
        out["ic"] = ic
    for k in ("templates", "sla_hours", "helpline"):
        if body.get(k) is not None:
            if not isinstance(body[k], dict):
                raise HTTPException(422, f"{k} must be an object")
            if k == "sla_hours":
                try:
                    out[k] = {str(c)[:40]: int(v) for c, v in body[k].items() if int(v) >= 0}
                except (TypeError, ValueError):
                    raise HTTPException(422, "sla_hours values must be whole hours")
            elif k == "templates":
                out[k] = {str(c)[:40]: str(v)[:4000] for c, v in body[k].items() if str(v).strip()}
            else:
                out[k] = {str(c)[:40]: str(v)[:200] for c, v in body[k].items()}
    return out


@router.get("/settings")
async def desk_settings_get(request: Request):
    org, _ = _ids(request)
    _admin_only(request)
    stored = await get_setting(org, DS.GROUP, {}) or {}
    return {"key": DS.GROUP, "value": stored, "effective": await DS.load(org), "defaults": DS.defaults()}


@router.put("/settings")
async def desk_settings_put(body: DeskSettingsIn, request: Request):
    org, uid = _ids(request)
    _admin_only(request)
    from shared.utils.org_settings import invalidate
    from shared.utils.platform_models import OrgSetting, OrgSettingAudit
    stored = await get_setting(org, DS.GROUP, {}) or {}
    value = _clean_desk_settings(body.model_dump(exclude_unset=True), stored if isinstance(stored, dict) else {})
    async with pm.session() as s:
        row = (await s.execute(select(OrgSetting).where(OrgSetting.org_id == str(org), OrgSetting.key == DS.GROUP))).scalar_one_or_none()
        old = row.value if row else {}
        if row is None:
            row = OrgSetting(org_id=str(org), key=DS.GROUP, value=value)
            s.add(row)
        row.value = value
        row.updated_by, row.updated_at = request.headers.get("x-user-email"), pm.now_utc()
        if old != value:
            s.add(OrgSettingAudit(org_id=str(org), key=DS.GROUP, old_value=old, new_value=value, actor_id=uid, actor_email=request.headers.get("x-user-email")))
        await s.commit()
    invalidate(str(org))
    await record_audit(request, "desk.settings.update", entity_type="org_setting", entity_id=DS.GROUP,
                       summary="HR Desk settings changed" + (f" (IC members: {len((value.get('ic') or {}).get('members') or [])})" if body.ic is not None else ""),
                       meta={"fields": sorted(body.model_dump(exclude_unset=True).keys())})
    return {"key": DS.GROUP, "value": value, "effective": await DS.load(org), "saved": True}


# ══════════════════════════ HR Desk agent dashboard (phase 2, P2-C) ════════════

async def knowledge_summary(org: str) -> dict:
    """Approved clauses, drafts awaiting SME sign-off, average readiness — the agent's knowledge base health."""
    async with pm.session() as s:
        rows = (await s.execute(select(DeskPolicyClause.status, func.count(), func.avg(DeskPolicyClause.ai_ready_score))
                                .where(DeskPolicyClause.org_id == org).group_by(DeskPolicyClause.status))).all()
        n_pol = int((await s.execute(select(func.count()).select_from(DeskPolicy).where(DeskPolicy.org_id == org))).scalar() or 0)
        n_pol_ok = int((await s.execute(select(func.count()).select_from(DeskPolicy).where(DeskPolicy.org_id == org, DeskPolicy.status == "approved"))).scalar() or 0)
    by = {st: (int(n), (round(float(avg)) if avg is not None else None)) for st, n, avg in rows}
    return {"policies": n_pol, "policies_approved": n_pol_ok, "approved": by.get("approved", (0, None))[0], "draft": by.get("draft", (0, None))[0],
            "retired": by.get("retired", (0, None))[0], "avg_ready_approved": by.get("approved", (0, None))[1], "avg_ready_draft": by.get("draft", (0, None))[1],
            "ready": by.get("approved", (0, None))[0] > 0}


async def abstained_questions(org: str, days: int = 7, limit: int = 20) -> list[dict]:
    """Shielded questions the desk abstained on (not hard-routed, never the text of a routed turn) — the
    'write a policy clause' queue. Identifiers were replaced by placeholders before storage."""
    from datetime import datetime, timedelta, timezone
    from desk.policy_models import DeskTurn
    since = datetime.now(tz=timezone.utc) - timedelta(days=max(1, min(int(days or 7), 366)))
    async with pm.session() as s:
        rows = (await s.execute(select(DeskTurn).where(DeskTurn.org_id == org, DeskTurn.abstained.is_(True), DeskTurn.hard_routed.is_(False),
                                                        DeskTurn.question_shielded.is_not(None), DeskTurn.created_at >= since)
                                .order_by(DeskTurn.created_at.desc()).limit(max(1, min(int(limit or 20), 100))))).scalars().all()
    return [{"turn_id": t.id, "session_id": t.session_id, "question": t.question_shielded, "intent": t.intent, "confidence": t.confidence,
             "rating": t.rating, "at": pm.iso(t.created_at)} for t in rows]


@router.get("/dashboard")
async def dashboard(request: Request, days: int = 7):
    org, _ = _ids(request)
    _office(request)
    from desk import agent as AG
    st = await AG.state(org)
    days = max(1, min(int(days or 7), 366))
    m = await MX.rollup_db(org, days)
    out = {"state": {k: st.get(k) for k in ("enabled", "mode", "addon_purchased", "paused", "can_go_live", "live_gate_note")}, "days": days, "metrics": m,
           "definitions": {**MX.DEFINITIONS, "top_questions": "turn intents ranked over the window (policy:<key> = the policy the answer cited; tools:<name> = answered from the person's own records; a hard class = routed to a person, count only).",
                           "knowledge_readiness": "approved clauses are the ONLY ones used for answers; the readiness score (0-100) is the importer's heuristic (length, heading, effective date, no dead ends, no placeholders).",
                           "value": "query_resolved = self-solved answers (provisional until the person rates; thumbs-down compensates the row); handoff_created and ic_referral are counts, never $."},
           "top_questions": m.get("top_intents") or [], "abstained_questions": await abstained_questions(org, days), "knowledge": await knowledge_summary(org),
           "value": await AG.value_counts(org, days) if st.get("enabled") else None, "last_run": None, "last_report": None,
           "write_clause_link": "/ai/desk/policies", "chat_hint": "Employees reach the desk through Ask Lexi (the chat button); HR curates clauses under HR Desk policies."}
    try:
        from agentic_runs import latest_runs
        out["last_run"] = (await latest_runs(org)).get("hr_desk")
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from agentic_log import recent
        ev = await recent(org, limit=1, event_type="desk.weekly_report")
        out["last_report"] = {"at": ev[0].get("ts_utc"), "by": ev[0].get("requested_by_user_id")} if ev else None
    except Exception:                                        # noqa: BLE001
        pass
    return out


class ReportIn(BaseModel):
    days: int = Field(7, ge=1, le=366)


@router.post("/weekly-report")
async def weekly_report(request: Request, body: ReportIn | None = None):
    org, uid = _ids(request)
    _office(request)
    from desk import agent as AG
    from desk import report as RP
    st = await AG.state(org)
    if not st.get("enabled"):
        raise HTTPException(409, "The weekly HR Desk report is part of the Agentic operations add-on (Plan & billing → add-ons, or Agents → Settings).")
    out = await RP.send_weekly(org, days=(body.days if body else 7), requested_by=uid)
    await record_audit(request, "desk.report.weekly", entity_type="desk_report", entity_id=org, summary="HR Desk weekly report generated and sent to HR",
                       meta={"days": (body.days if body else 7), "smtp": out.get("smtp_configured")})
    return out
