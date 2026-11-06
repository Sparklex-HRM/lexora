"""Employee portal batch 3 (2026-10-16) — document centre: company policies with acknowledgement.

  GET    /employees/ess/documents/summary          header counts for the Document centre (anyone; own counts)
  GET    /employees/ess/policies                   published policies + MY status        (anyone)
                                                   ?manage=1 → HR: every policy incl. archived + ack counts
  POST   /employees/ess/policies/files             HR: upload a policy PDF → {id, file_name}
  POST   /employees/ess/policies                   HR: publish a policy
  GET    /employees/ess/policies/{pid}             one policy (employee: published only)
  PATCH  /employees/ess/policies/{pid}             HR: fix title / category (no new version)
  POST   /employees/ess/policies/{pid}/versions    HR: publish a NEW version → everyone acknowledges again
  POST   /employees/ess/policies/{pid}/archive     HR: unpublish
  POST   /employees/ess/policies/{pid}/restore     HR: publish again (same version)
  GET    /employees/ess/policies/{pid}/file        the PDF (employee: published only)
  POST   /employees/ess/policies/{pid}/acknowledge "I have read and accept" — the caller, for themselves
  GET    /employees/ess/policies/{pid}/acks        HR: who acknowledged / who is pending (current version)
  GET    /employees/ess/policies/{pid}/acks.csv    HR: the same as CSV
  POST   /employees/ess/policies/{pid}/remind      HR: in-app reminder to everyone pending (no e-mail)

HR = superadmin · org_admin · hr_admin · hr_manager. Mount BEFORE routers.router (/employees/{id} catch-all).
"""
import sys
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import docstore  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.docstore import StoredDocument  # noqa: E402
from shared.utils.notify import notify  # noqa: E402

import access  # noqa: E402
import ess_docs_logic as L  # noqa: E402
from database import get_db  # noqa: E402
from ess_docs_models import EmpPolicyAck, EmpPolicyDoc  # noqa: E402
from letters_models import IssuedLetter  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(prefix="/employees/ess/policies", tags=["employee-documents"])
summary_router = APIRouter(prefix="/employees/ess/documents", tags=["employee-documents"])
POLICY_DOC_TYPE = "policy"


def _now():
    return datetime.now(timezone.utc)


def _org(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(request.headers.get("x-org-id") or "")
    except ValueError:
        raise HTTPException(401, "Missing organization context")


def _role(request: Request) -> str:
    return (request.headers.get("x-role") or "").lower()


def _who(request: Request) -> str | None:
    return request.headers.get("x-user-email") or request.headers.get("x-user-id")


def _require_hr(request: Request, what: str):
    if not L.is_hr(_role(request)):
        raise HTTPException(403, f"Only HR can {what}")


def _uuid(v, what="policy") -> uuid.UUID:
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except (ValueError, TypeError):
        raise HTTPException(404, f"{what.capitalize()} not found")


async def _self_id(request: Request, db) -> str | None:
    try:
        return await access.self_employee_id(request, db)
    except Exception:  # noqa: BLE001 — identity lookup unavailable → treat as not linked
        return (request.headers.get("x-employee-id") or "").strip() or None


async def _policy(db, org: uuid.UUID, pid: str, request: Request) -> EmpPolicyDoc:
    p = (await db.execute(select(EmpPolicyDoc).where(
        EmpPolicyDoc.id == _uuid(pid), EmpPolicyDoc.org_id == org))).scalar_one_or_none()
    if not p or (p.status != "published" and not L.is_hr(_role(request))):
        raise HTTPException(404, "Policy not found")
    return p


async def _people(db, org: uuid.UUID) -> list:
    """Everyone a policy applies to: current staff (not terminated)."""
    return (await db.execute(select(Employee).where(
        Employee.org_id == org, Employee.is_active.is_(True),
        or_(Employee.status.is_(None), Employee.status != "terminated")))).scalars().all()


async def _acks(db, org: uuid.UUID, pid: uuid.UUID | None = None) -> list:
    q = select(EmpPolicyAck).where(EmpPolicyAck.org_id == org)
    if pid:
        q = q.where(EmpPolicyAck.policy_id == pid)
    return (await db.execute(q)).scalars().all()


def _out(p: EmpPolicyDoc, *, acked_at=None, with_body=False) -> dict:
    out = {"id": str(p.id), "title": p.title, "category": p.category, "version_no": p.version_no,
           "version_label": p.version_label, "effective_date": p.effective_date.isoformat() if p.effective_date else None,
           "has_body": bool(p.body), "has_file": bool(p.document_id), "file_name": p.file_name,
           "ack_required": bool(p.ack_required), "status": p.status,
           "published_at": p.published_at.isoformat() if p.published_at else None, "published_by": p.published_by,
           "archived_at": p.archived_at.isoformat() if p.archived_at else None,
           "last_reminded_at": p.last_reminded_at.isoformat() if p.last_reminded_at else None,
           "my_status": L.my_status(p, acked_at is not None),
           "my_acknowledged_at": acked_at.isoformat() if acked_at else None,
           "previous_versions": [{k: h.get(k) for k in ("version_no", "version_label", "effective_date", "published_at")}
                                 for h in (p.history or [])]}
    if with_body:
        out["body"] = p.body
    return out


async def _check_pdf(db, org: uuid.UUID, document_id) -> StoredDocument | None:
    if not document_id:
        return None
    d = (await db.execute(select(StoredDocument).where(
        StoredDocument.id == _uuid(document_id, "file"), StoredDocument.org_id == org,
        StoredDocument.doc_type == POLICY_DOC_TYPE, StoredDocument.deleted_at.is_(None)))).scalar_one_or_none()
    if not d:
        raise HTTPException(422, "The attached file was not found — upload it again")
    return d


# ══════════════════════════ summary ════════════════════════════════════════════

@summary_router.get("/summary")
async def documents_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Counts for the Document centre header — the caller's own, plus HR totals for HR. No amounts."""
    org = _org(request)
    me = await _self_id(request, db)
    my_docs = letters = 0
    if me:
        mid = _uuid(me, "employee")
        my_docs = (await db.execute(select(func.count()).select_from(StoredDocument).where(
            StoredDocument.org_id == org, StoredDocument.employee_id == mid,
            StoredDocument.deleted_at.is_(None)))).scalar() or 0
        letters = (await db.execute(select(func.count()).select_from(IssuedLetter).where(
            IssuedLetter.org_id == org, IssuedLetter.employee_id == mid, IssuedLetter.status == "issued",
            IssuedLetter.visible_to_employee.is_(True)))).scalar() or 0
    pols = (await db.execute(select(EmpPolicyDoc).where(
        EmpPolicyDoc.org_id == org, EmpPolicyDoc.status == "published"))).scalars().all()
    acks = await _acks(db, org)
    mine = {(str(a.policy_id), int(a.version_no)) for a in acks if me and str(a.employee_id) == str(me)}
    need = [p for p in pols if p.ack_required]
    out = {"my_documents": int(my_docs), "letters": int(letters), "policies": len(pols),
           "policies_to_acknowledge": sum(1 for p in need if (str(p.id), int(p.version_no)) not in mine) if me else 0}
    if L.is_hr(_role(request)):
        staff = len(await _people(db, org))
        cur = {}
        for a in acks:
            cur.setdefault((str(a.policy_id), int(a.version_no)), set()).add(str(a.employee_id))
        pending = sum(max(staff - len(cur.get((str(p.id), int(p.version_no)), set())), 0) for p in need)
        out["hr"] = {"policies_needing_ack": len(need), "staff": staff, "pending_acknowledgements": pending}
    return out


# ══════════════════════════ policies — read ════════════════════════════════════

@router.get("")
async def list_policies(request: Request, manage: bool = False, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    if manage:
        _require_hr(request, "manage company policies")
    q = select(EmpPolicyDoc).where(EmpPolicyDoc.org_id == org)
    if not manage:
        q = q.where(EmpPolicyDoc.status == "published")
    rows = (await db.execute(q.order_by(EmpPolicyDoc.title))).scalars().all()
    acks = await _acks(db, org)
    me = await _self_id(request, db)
    mine = {(str(a.policy_id), int(a.version_no)): a.acknowledged_at for a in acks if me and str(a.employee_id) == str(me)}
    out = [_out(p, acked_at=mine.get((str(p.id), int(p.version_no)))) for p in rows]
    if manage:
        staff = await _people(db, org)
        ids = {str(e.id) for e in staff}
        for o, p in zip(out, rows):
            done = {str(a.employee_id) for a in acks if str(a.policy_id) == str(p.id)
                    and int(a.version_no) == int(p.version_no) and str(a.employee_id) in ids}
            o["acks"] = L.counts(len(staff), len(done)) if p.ack_required else None
    return out


@router.get("/{pid}")
async def get_policy(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    p = await _policy(db, org, pid, request)
    me = await _self_id(request, db)
    a = None
    if me:
        a = (await db.execute(select(EmpPolicyAck).where(
            EmpPolicyAck.policy_id == p.id, EmpPolicyAck.version_no == p.version_no,
            EmpPolicyAck.employee_id == _uuid(me, "employee")))).scalars().first()
    return _out(p, acked_at=a.acknowledged_at if a else None, with_body=True)


@router.get("/{pid}/file")
async def policy_file(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    p = await _policy(db, org, pid, request)
    if not p.document_id:
        raise HTTPException(404, "This policy has no PDF")
    d = await _check_pdf(db, org, p.document_id)
    data = (await db.execute(select(StoredDocument.data).where(StoredDocument.id == d.id))).scalar_one()
    return Response(content=bytes(data), media_type=d.content_type, headers={
        "Content-Disposition": f'inline; filename="{d.file_name}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


# ══════════════════════════ policies — HR publish ══════════════════════════════

class PolicyIn(BaseModel):
    title: str = Field(max_length=L.MAX_TITLE)
    category: str | None = Field(default=None, max_length=L.MAX_CATEGORY)
    version_label: str = Field(default="v1", max_length=L.MAX_LABEL)
    effective_date: date | None = None
    body: str | None = None
    document_id: str | None = None
    ack_required: bool = True


class VersionIn(BaseModel):
    version_label: str = Field(max_length=L.MAX_LABEL)
    effective_date: date | None = None
    body: str | None = None
    document_id: str | None = None
    ack_required: bool | None = None
    keep_file: bool = False          # reuse the current PDF when no new one is attached


class PolicyPatch(BaseModel):
    title: str | None = Field(default=None, max_length=L.MAX_TITLE)
    category: str | None = Field(default=None, max_length=L.MAX_CATEGORY)


@router.post("/files", status_code=201)
async def upload_policy_file(request: Request, file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    _require_hr(request, "upload a policy document")
    org = _org(request)
    data = await file.read(docstore.MAX_BYTES + 1)
    try:
        cols = docstore.check_upload(file.filename, data)
    except docstore.DocError as exc:
        raise HTTPException(exc.status, str(exc))
    if cols["content_type"] != "application/pdf":
        raise HTTPException(422, "A policy document must be a PDF")
    d = StoredDocument(org_id=org, employee_id=None, doc_type=POLICY_DOC_TYPE, uploaded_by=request.headers.get("x-user-id"),
                       uploaded_by_name=request.headers.get("x-user-email"), **cols)
    db.add(d)
    await db.commit()
    return {"id": str(d.id), "file_name": d.file_name, "size_bytes": d.size_bytes}


@router.post("", status_code=201)
async def publish_policy(body: PolicyIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "publish a company policy")
    org = _org(request)
    problem = L.validate_policy(body.title, body.category, body.version_label, body.effective_date, body.body, body.document_id)
    if problem:
        raise HTTPException(422, problem)
    title = L.clean(body.title, L.MAX_TITLE)
    dup = (await db.execute(select(EmpPolicyDoc).where(
        EmpPolicyDoc.org_id == org, func.lower(EmpPolicyDoc.title) == title.lower()))).scalars().first()
    if dup:
        raise HTTPException(409, f"A policy called “{dup.title}” already exists — publish a new version of it instead")
    d = await _check_pdf(db, org, body.document_id)
    p = EmpPolicyDoc(org_id=org, title=title, category=L.clean(body.category, L.MAX_CATEGORY), version_no=1,
                     version_label=L.clean(body.version_label, L.MAX_LABEL), effective_date=body.effective_date,
                     body=L.clean(body.body, L.MAX_BODY), document_id=d.id if d else None,
                     file_name=d.file_name if d else None, ack_required=bool(body.ack_required),
                     status="published", history=[], published_at=_now(), published_by=_who(request))
    db.add(p)
    await db.commit()
    await db.refresh(p)
    await record_audit(request, "employee.policy.publish", entity_type="policy", entity_id=str(p.id),
                       summary=f"Policy published: {p.title} ({p.version_label})",
                       meta={"ack_required": p.ack_required, "version_no": p.version_no})
    if p.ack_required:
        await notify(str(org), roles=["employee", "manager"], title=f"New policy to read: {p.title}",
                     body=f"{p.title} ({p.version_label}) needs your acknowledgement.", link="/documents",
                     kind="general", email=False)
    return _out(p, with_body=True)


@router.patch("/{pid}")
async def edit_policy(pid: str, body: PolicyPatch, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "change a company policy")
    org = _org(request)
    p = await _policy(db, org, pid, request)
    if body.title is not None:
        t = L.clean(body.title, L.MAX_TITLE)
        if not t:
            raise HTTPException(422, "Give the policy a title")
        p.title = t
    if body.category is not None:
        p.category = L.clean(body.category, L.MAX_CATEGORY)
    await db.commit()
    return _out(p)


@router.post("/{pid}/versions")
async def new_version(pid: str, body: VersionIn, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "publish a new version of a policy")
    org = _org(request)
    p = await _policy(db, org, pid, request)
    doc_id = body.document_id or (str(p.document_id) if body.keep_file and p.document_id else None)
    problem = L.validate_version(body.version_label, body.effective_date, body.body, doc_id)
    if problem:
        raise HTTPException(422, problem)
    if L.label_taken(p, body.version_label):
        raise HTTPException(409, f"Version “{body.version_label.strip()}” was already published — use a new label")
    d = await _check_pdf(db, org, doc_id)
    p.history = list(p.history or []) + [L.history_entry(p)]
    p.version_no = int(p.version_no or 1) + 1
    p.version_label = L.clean(body.version_label, L.MAX_LABEL)
    p.effective_date, p.body = body.effective_date, L.clean(body.body, L.MAX_BODY)
    p.document_id, p.file_name = (d.id, d.file_name) if d else (None, None)
    if body.ack_required is not None:
        p.ack_required = bool(body.ack_required)
    p.status, p.archived_at, p.archived_by = "published", None, None
    p.published_at, p.published_by, p.last_reminded_at = _now(), _who(request), None
    await db.commit()
    await db.refresh(p)
    await record_audit(request, "employee.policy.version", entity_type="policy", entity_id=str(p.id),
                       summary=f"New policy version: {p.title} ({p.version_label})", meta={"version_no": p.version_no})
    if p.ack_required:
        await notify(str(org), roles=["employee", "manager"], title=f"Policy updated: {p.title}",
                     body=f"{p.title} has a new version ({p.version_label}). Please read and accept it again.",
                     link="/documents", kind="general", email=False)
    return _out(p, with_body=True)


@router.post("/{pid}/archive")
async def archive_policy(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "archive a company policy")
    p = await _policy(db, _org(request), pid, request)
    p.status, p.archived_at, p.archived_by = "archived", _now(), _who(request)
    await db.commit()
    await record_audit(request, "employee.policy.archive", entity_type="policy", entity_id=str(p.id),
                       summary=f"Policy archived: {p.title}")
    return _out(p)


@router.post("/{pid}/restore")
async def restore_policy(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_hr(request, "publish a company policy")
    p = await _policy(db, _org(request), pid, request)
    p.status, p.archived_at, p.archived_by = "published", None, None
    await db.commit()
    return _out(p)


# ══════════════════════════ acknowledgement ════════════════════════════════════

class AckIn(BaseModel):
    version_no: int | None = None
    employee_id: str | None = None      # ignored unless it differs from the caller → 403


@router.post("/{pid}/acknowledge")
async def acknowledge(pid: str, request: Request, body: AckIn | None = None, db: AsyncSession = Depends(get_db)):
    org = _org(request)
    body = body or AckIn()
    p = await _policy(db, org, pid, request)
    me = await _self_id(request, db)
    refused = L.ack_check(p, self_id=me, body_employee_id=body.employee_id, version_no=body.version_no)
    if refused:
        raise HTTPException(*refused)
    mid = _uuid(me, "employee")
    existing = (await db.execute(select(EmpPolicyAck).where(
        EmpPolicyAck.policy_id == p.id, EmpPolicyAck.version_no == p.version_no,
        EmpPolicyAck.employee_id == mid))).scalars().first()
    if existing:
        return _out(p, acked_at=existing.acknowledged_at)
    e = (await db.execute(select(Employee).where(Employee.id == mid, Employee.org_id == org))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Your employee record was not found")
    a = EmpPolicyAck(org_id=org, policy_id=p.id, version_no=p.version_no, version_label=p.version_label,
                     employee_id=mid, employee_name=L.emp_name(e), user_id=request.headers.get("x-user-id"),
                     user_agent=L.clean(request.headers.get("user-agent"), L.MAX_UA), acknowledged_at=_now())
    db.add(a)
    try:
        await db.commit()
    except IntegrityError:            # a double click raced us — the first one stands
        await db.rollback()
        a = (await db.execute(select(EmpPolicyAck).where(
            EmpPolicyAck.policy_id == p.id, EmpPolicyAck.version_no == p.version_no,
            EmpPolicyAck.employee_id == mid))).scalars().first()
    await record_audit(request, "employee.policy.acknowledge", entity_type="policy", entity_id=str(p.id),
                       summary=f"Policy acknowledged: {p.title} ({p.version_label})",
                       meta={"employee_id": str(mid), "version_no": p.version_no})
    return _out(p, acked_at=a.acknowledged_at if a else _now())


# ══════════════════════════ HR tracking ════════════════════════════════════════

async def _register(request: Request, db, pid: str) -> tuple[EmpPolicyDoc, dict]:
    _require_hr(request, "see who has acknowledged a policy")
    org = _org(request)
    p = await _policy(db, org, pid, request)
    return p, L.register(p, await _people(db, org), await _acks(db, org, p.id))


@router.get("/{pid}/acks")
async def ack_register(pid: str, request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    p, reg = await _register(request, db, pid)
    rows = [r for r in reg["rows"] if not status or r["status"] == status]
    return {"policy": _out(p), "total": reg["total"], "acknowledged": reg["acknowledged"],
            "pending": reg["pending"], "percent": reg["percent"], "rows": rows}


@router.get("/{pid}/acks.csv")
async def ack_register_csv(pid: str, request: Request, status: str | None = None, db: AsyncSession = Depends(get_db)):
    p, reg = await _register(request, db, pid)
    rows = [r for r in reg["rows"] if not status or r["status"] == status]
    safe = "".join(c if c.isalnum() else "-" for c in p.title).strip("-")[:60] or "policy"
    return Response(content=L.register_csv(p, rows), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{safe}-{p.version_label}-acknowledgements.csv"'})


@router.post("/{pid}/remind")
async def remind_pending(pid: str, request: Request, db: AsyncSession = Depends(get_db)):
    p, reg = await _register(request, db, pid)
    if p.status != "published" or not p.ack_required:
        raise HTTPException(409, "Only a published policy that needs acknowledgement can be reminded")
    pending = [r["employee_id"] for r in reg["rows"] if r["status"] == "pending"]
    if pending:
        title, text = L.remind_text(p)
        await notify(str(p.org_id), employee_ids=pending, title=title, body=text, link="/documents",
                     kind="reminder", email=False)
        p.last_reminded_at = _now()
        await db.commit()
        await record_audit(request, "employee.policy.remind", entity_type="policy", entity_id=str(p.id),
                           summary=f"Reminder sent for {p.title} ({p.version_label}) to {len(pending)} people")
    return {"reminded": len(pending), "last_reminded_at": p.last_reminded_at.isoformat() if p.last_reminded_at else None}
