"""Employee service — form options, auto employee number, documents (stream A).

Mounted INSIDE the /employees router (see routers.py) before `/{employee_id}`,
so the static paths below are never mistaken for an employee id.

    GET    /employees/form-options                       one call for the add / edit form
    GET    /employees/next-number                        preview of the next auto number
    GET    /employees/{id}/documents                     list (no file bytes)
    POST   /employees/{id}/documents                     multipart: file + doc_type
    GET    /employees/{id}/documents/{doc_id}/download   the file
    POST   /employees/{id}/documents/{doc_id}/verify     HR ticks "checked"
    DELETE /employees/{id}/documents/{doc_id}            soft delete

Documents: HR / payroll / admin for anyone; an employee for their own record.
A manager does NOT get a report's documents.
"""
import sys
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils import docstore, identity  # noqa: E402
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.docstore import StoredDocument  # noqa: E402
from shared.utils.employee_fixes import next_free_number  # noqa: E402
from shared.utils.enums import EMPLOYMENT_TYPES  # noqa: E402
from ca_identity import CA_PROVINCES  # noqa: E402

import access  # noqa: E402
from database import get_db  # noqa: E402
from masters_models import Grade  # noqa: E402
from models import Employee, EmployeeNumberCounter  # noqa: E402

sub = APIRouter(tags=["employees"])


def org_uuid(request: Request) -> uuid.UUID:
    org = request.headers.get("x-org-id")
    if not org:
        raise HTTPException(401, "Missing organization context")
    try:
        return uuid.UUID(org)
    except ValueError:
        raise HTTPException(401, "Missing organization context")


def as_uuid(value, what: str = "record") -> uuid.UUID:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(404, f"{what.capitalize()} not found")


# ── employee numbers ──────────────────────────────────────────────────────────
async def _number_format(org) -> tuple[str, int]:
    """Prefix + zero padding from company settings (Settings → Numbering)."""
    prefix, pad = "EMP", 4
    try:
        from shared.utils.org_settings import get_setting
        prefix = await get_setting(org, "numbering.employee_prefix", "EMP")
        pad = await get_setting(org, "numbering.padding", 4)
    except Exception:
        pass
    try:
        pad = int(pad)
    except (TypeError, ValueError):
        pad = 4
    return str(prefix or "").strip(), pad


async def _taken_numbers(db: AsyncSession, org: uuid.UUID) -> set[str]:
    return {n for (n,) in (await db.execute(
        select(Employee.employee_number).where(Employee.org_id == org))).all() if n}


async def _entity_prefix(org, entity_id) -> str:
    """D4: a legal entity's own number prefix (Settings → Legal entities → numbering prefix), or ""
    when the entity has none / is unknown — then the org prefix applies unchanged. The org-wide counter
    is shared, so numbers stay unique across entities."""
    if not entity_id:
        return ""
    try:
        from shared import legal_entities as LE
        ent = await LE.entity_by_id(org, entity_id)
    except Exception:  # noqa: BLE001 — no entity table → org prefix
        return ""
    return str((ent or {}).get("numbering_prefix") or "").strip()


async def preview_number(db: AsyncSession, org: uuid.UUID, entity_id=None) -> dict:
    prefix, pad = await _number_format(org)
    prefix = (await _entity_prefix(org, entity_id)) or prefix
    row = (await db.execute(select(EmployeeNumberCounter).where(
        EmployeeNumberCounter.org_id == org))).scalar_one_or_none()
    number, _ = next_free_number(prefix, pad, row.next_number if row else None, await _taken_numbers(db, org))
    return {"next_number": number, "prefix": prefix, "padding": pad}


async def assign_number(db: AsyncSession, org: uuid.UUID, entity_id=None) -> str:
    """Take the next free number and move the counter (same transaction as the insert).
    D4: the employee's legal entity prefix wins over the org prefix when the entity has one."""
    prefix, pad = await _number_format(org)
    prefix = (await _entity_prefix(org, entity_id)) or prefix
    row = (await db.execute(select(EmployeeNumberCounter).where(
        EmployeeNumberCounter.org_id == org).with_for_update())).scalar_one_or_none()
    number, nxt = next_free_number(prefix, pad, row.next_number if row else None, await _taken_numbers(db, org))
    if row:
        row.next_number = nxt
    else:
        db.add(EmployeeNumberCounter(org_id=org, next_number=nxt))
    return number


async def number_in_use(db: AsyncSession, org: uuid.UUID, number: str, except_id=None) -> bool:
    from sqlalchemy import func
    q = select(Employee.id).where(Employee.org_id == org, Employee.is_active.is_(True),
                                  func.upper(Employee.employee_number) == number.strip().upper())
    if except_id:
        q = q.where(Employee.id != except_id)
    return (await db.execute(q)).first() is not None


# ── form options ──────────────────────────────────────────────────────────────
@sub.get("/next-number")
async def next_number(request: Request, entity_id: str | None = None, db: AsyncSession = Depends(get_db)):
    access.require_hr_write(request, "see the next employee number")
    return await preview_number(db, org_uuid(request), entity_id)


@sub.get("/form-options")
async def form_options(request: Request, db: AsyncSession = Depends(get_db)):
    """Everything the add / edit form needs in one call."""
    access.require_edit(request)
    org = org_uuid(request)
    info = await identity.org_info(org) or {}
    emps = (await db.execute(select(Employee).where(
        Employee.org_id == org, Employee.is_active.is_(True)))).scalars().all()
    grades = (await db.execute(select(Grade).where(
        Grade.org_id == org, Grade.is_active.is_(True)).order_by(Grade.level, Grade.name))).scalars().all()
    return {
        "org_country": "CA",
        "org_region": (info.get("province") or "").upper() or None,
        "countries": [{"value": "CA", "label": "Canada"}],
        "regions": {"CA": list(CA_PROVINCES)},
        "employment_types": list(EMPLOYMENT_TYPES),
        "grades": [{"name": g.name, "code": g.code} for g in grades],
        "managers": sorted(({"id": str(e.id), "name": f"{e.first_name} {e.last_name}".strip(),
                             "employee_number": e.employee_number, "job_title": e.job_title,
                             "department": e.department}
                            for e in emps if e.status != "terminated"), key=lambda m: m["name"].lower()),
        "doc_types": {c: [{"value": k, "label": v} for k, v in items] for c, items in docstore.DOC_TYPES.items()},
        "doc_rules": {"allowed": docstore.ALLOWED_LABEL, "max_mb": docstore.MAX_BYTES // (1024 * 1024)},
        **(await preview_number(db, org)),
    }


# ── documents ─────────────────────────────────────────────────────────────────
async def _doc_access(employee_id: str, request: Request, db: AsyncSession, write: bool = False) -> Employee:
    org, eid = org_uuid(request), as_uuid(employee_id, "employee")
    e = (await db.execute(select(Employee).where(Employee.id == eid, Employee.org_id == org))).scalar_one_or_none()
    if not e:
        raise HTTPException(404, "Employee not found")
    v = await access.viewer(request, db)
    is_self = bool(v.get("self_id")) and str(v["self_id"]) == str(e.id)
    if v["scope"] != access.ALL and not is_self:
        raise HTTPException(404, "Employee not found")       # someone else's record is invisible
    if access.names_only(request) and not is_self:
        raise HTTPException(404, "Employee not found")       # accountant: names-only directory, no documents
    return e


def _who(request: Request) -> tuple[str | None, str | None]:
    return request.headers.get("x-user-id"), request.headers.get("x-user-email")


@sub.get("/{employee_id}/documents")
async def list_documents(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    e = await _doc_access(employee_id, request, db)
    rows = (await db.execute(select(StoredDocument).where(
        StoredDocument.org_id == e.org_id, StoredDocument.employee_id == e.id,
        StoredDocument.deleted_at.is_(None)).order_by(StoredDocument.uploaded_at.desc()))).scalars().all()
    return [docstore.doc_out(d) for d in rows]


@sub.post("/{employee_id}/documents", status_code=201)
async def upload_document(employee_id: str, request: Request, file: UploadFile = File(...),
                          doc_type: str = Form("other"), db: AsyncSession = Depends(get_db)):
    e = await _doc_access(employee_id, request, db, write=True)
    data = await file.read(docstore.MAX_BYTES + 1)
    try:
        cols = docstore.check_upload(file.filename, data)
    except docstore.DocError as exc:
        raise HTTPException(exc.status, str(exc))
    uid, email = _who(request)
    d = StoredDocument(org_id=e.org_id, employee_id=e.id, doc_type=(doc_type or "other")[:40],
                       uploaded_by=uid, uploaded_by_name=email, **cols)
    db.add(d)
    await db.commit()
    await db.refresh(d)
    await record_audit(request, "employee.document.upload", entity_type="employee", entity_id=str(e.id),
                       summary=f"Document uploaded: {d.doc_type} ({d.file_name})",
                       meta={"document_id": str(d.id), "content_type": d.content_type, "size": d.size_bytes})
    return docstore.doc_out(d)


async def _doc(e: Employee, doc_id: str, db: AsyncSession) -> StoredDocument:
    d = (await db.execute(select(StoredDocument).where(
        StoredDocument.id == as_uuid(doc_id, "document"), StoredDocument.org_id == e.org_id,
        StoredDocument.employee_id == e.id, StoredDocument.deleted_at.is_(None)))).scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Document not found")
    return d


@sub.get("/{employee_id}/documents/{doc_id}/download")
async def download_document(employee_id: str, doc_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    e = await _doc_access(employee_id, request, db)
    d = await _doc(e, doc_id, db)
    data = (await db.execute(select(StoredDocument.data).where(StoredDocument.id == d.id))).scalar_one()
    await record_audit(request, "employee.document.read", entity_type="employee", entity_id=str(e.id),
                       summary=f"Document opened: {d.doc_type} ({d.file_name})", meta={"document_id": str(d.id)})
    return Response(content=bytes(data), media_type=d.content_type, headers={
        "Content-Disposition": f'attachment; filename="{d.file_name}"', "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox"})


@sub.post("/{employee_id}/documents/{doc_id}/verify")
async def verify_document(employee_id: str, doc_id: str, request: Request, body: dict | None = None,
                          db: AsyncSession = Depends(get_db)):
    access.require_hr_write(request, "mark a document as checked")
    e = await _doc_access(employee_id, request, db, write=True)
    d = await _doc(e, doc_id, db)
    d.verified = bool((body or {}).get("verified", True))
    d.verified_by = _who(request)[1] if d.verified else None
    d.verified_at = datetime.now(timezone.utc) if d.verified else None
    await db.commit()
    return docstore.doc_out(d)


@sub.delete("/{employee_id}/documents/{doc_id}")
async def delete_document(employee_id: str, doc_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    access.require_hr_write(request, "remove a document")
    e = await _doc_access(employee_id, request, db, write=True)
    d = await _doc(e, doc_id, db)
    d.deleted_at = datetime.now(timezone.utc)
    d.deleted_by = _who(request)[1] or _who(request)[0]
    await db.commit()
    await record_audit(request, "employee.document.delete", entity_type="employee", entity_id=str(e.id),
                       summary=f"Document removed: {d.doc_type} ({d.file_name})", meta={"document_id": str(d.id)})
    return {"ok": True, "id": str(d.id)}
