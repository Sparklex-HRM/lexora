"""Stream G6 — Canada migration kit, all under /payroll/migration/*.

  canada/catalog · canada/templates/{ft} · canada/upload · canada/batches[/{id}] · validate · commit · rollback
  presets                                  mapping presets (generic Canadian register — unverified, editable)
  parallel-run  POST upload · POST {id}/compare · POST {id}/sign · GET list/{id} · GET components
  checklist     GET · POST tick
"""
from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.config import settings  # noqa: E402

import ca_migration as CM  # noqa: E402
import migration_checklist as MC  # noqa: E402
import migration_reconcile as MR  # noqa: E402
from ca_migration_fields import (CA_FILE_TYPES, CA_FILE_TYPE_ORDER, NOT_BUILT, PRESETS, ca_auto_map,  # noqa: E402
                                 ca_fields_of, preset_mapping, presets_summary)
from ca_models import (CaContractor, CaImportBatch, CaOpeningYtd, CaOrgSettings, MigrationChecklistStep,  # noqa: E402
                       MigrationImportPreset, MigrationParallelRun)
from database import get_db  # noqa: E402
from migration_kit import ParseError, parse_upload  # noqa: E402
from xlsx_lite import write_workbook  # noqa: E402
from models import PayRun, PayStub, RuleVerification  # noqa: E402
import routers as R  # noqa: E402
from shared.utils.org_time import local_today  # org-TZ business date (UTC store, local today)

router = APIRouter(prefix="/payroll/migration", tags=["migration-kit"])
_ADMIN = {"superadmin", "org_admin", "hr_admin", "hr_manager", "hr_staff", "branch_admin", "payroll_admin"}
MAX_UPLOAD = 15 * 1024 * 1024


def _guard(request: Request) -> str:
    role = (request.headers.get("x-role") or "").lower()
    if role not in _ADMIN:
        raise HTTPException(403, "Only HR / payroll admins can run data migration")
    return request.headers.get("x-user-email") or request.headers.get("x-user-id") or role


def _guard_dep(request: Request) -> None:
    _guard(request)


# WASA 2026-10-21: the READS (batches, catalog, parallel runs, checklist …) are the migration
# team's too — every route of this router passes _guard (routes below are added after this line).
router.dependencies.append(Depends(_guard_dep))


def _fwd(request: Request) -> dict:
    return {k: request.headers.get(k, "") for k in ("x-org-id", "x-user-id", "x-user-email", "x-role", "x-branch-id") if request.headers.get(k)}


async def _emp_get(request: Request, path: str):
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{settings.EMPLOYEE_URL}{path}", headers=_fwd(request))
            if r.status_code == 200:
                return r.json()
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    return None


async def _emps_by_code(request: Request) -> dict:
    rows = await _emp_get(request, "/employees") or []
    return {(e.get("employee_number") or "").strip().upper(): e for e in rows if e.get("employee_number")}


def _emp_client(request: Request) -> dict:
    async def _post(path, body, what):
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(f"{settings.EMPLOYEE_URL}{path}", json=body, headers=_fwd(request))
        if r.status_code != 200:
            detail = r.json().get("detail") if r.headers.get("content-type", "").startswith("application/json") else r.text[:300]
            raise HTTPException(502, f"Employee service refused the {what}: {detail}")
        return r.json()

    return {"upsert": _wrap(_post, "/employees/import-canada/bulk-upsert", "rows", "items"),
            "restore": lambda items: _post("/employees/import-canada/bulk-restore", {"items": items}, "restore"),
            "comp_history": _wrap(_post, "/employees/import-canada/comp-history", "salary rows", "items"),
            "comp_restore": lambda items: _post("/employees/import-canada/comp-history/restore", {"items": items}, "salary restore")}


def _wrap(post, path, what, key):
    async def call(rows, bid=None):
        res = await post(path, {"batch_id": bid, "rows": rows}, what)
        return res[key]
    return call


def _j(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, tuple):
        return list(v)
    return v


def _rows_jsonable(rows):
    return [{"row_no": r["row_no"], "errors": r["errors"], "warnings": r["warnings"],
             "data": {k: _j(v) for k, v in r["data"].items()}} for r in rows]


def _batch_out(b: CaImportBatch, full=False) -> dict:
    out = {"id": str(b.id), "file_type": b.file_type, "file_type_label": CA_FILE_TYPES.get(b.file_type, {}).get("label", b.file_type),
           "vendor": b.vendor, "file_name": b.file_name, "status": b.status, "row_count": b.row_count, "error_count": b.error_count,
           "summary": b.summary or {}, "result": b.result or {}, "created_by": b.created_by,
           "created_at": b.created_at.isoformat() if b.created_at else None,
           "committed_at": b.committed_at.isoformat() if b.committed_at else None,
           "rolled_back_at": b.rolled_back_at.isoformat() if b.rolled_back_at else None, "country": "CA"}
    if full:
        out.update({"headers": b.headers or [], "mapping": b.mapping or {}, "rows": b.rows or [], "sample": (b.raw_rows or [])[:5]})
    return out


async def _batch(db, org, bid) -> CaImportBatch:
    try:
        b = (await db.execute(select(CaImportBatch).where(CaImportBatch.id == uuid.UUID(bid), CaImportBatch.org_id == org))).scalar_one_or_none()
    except ValueError:
        b = None
    if not b:
        raise HTTPException(404, "Import batch not found")
    return b


# ───────────────────────────── Canada catalog / templates / batches ─────────────────────────────

@router.get("/canada/catalog")
async def ca_catalog(request: Request):
    R._org(request)
    return {"file_types": [{"key": k, "label": CA_FILE_TYPES[k]["label"], "help": CA_FILE_TYPES[k]["help"], "fields": ca_fields_of(k)}
                           for k in CA_FILE_TYPE_ORDER],
            "vendors": [{"code": "lexora", "name": "Lexora HRM template", "verified": True, "file_types": []}] + presets_summary(),
            "not_built": NOT_BUILT, "year": local_today().year}


@router.get("/canada/templates/{file_type}")
async def ca_template(file_type: str, request: Request, format: str = "xlsx"):
    R._org(request)
    if file_type not in CA_FILE_TYPES:
        raise HTTPException(404, "Unknown file type")
    fields = ca_fields_of(file_type)
    labels = [f["label"] for f in fields]
    sample = [f["example"] for f in fields]
    if format == "csv":
        import csv as _csv
        import io
        buf = io.StringIO()
        w = _csv.writer(buf, lineterminator="\n"); w.writerow(labels); w.writerow(sample)
        return Response(content=buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="lexora_{file_type}_template.csv"'})
    how = [["HOW TO FILL — " + CA_FILE_TYPES[file_type]["label"]], [CA_FILE_TYPES[file_type]["help"]], [],
           ["Column", "Must fill?", "Type", "Example", "What to put"]]
    for f in fields:
        how.append([f["label"], "YES" if f["required"] else "optional", f["kind"], f["example"],
                    (f["help"] + (" Values: " + " / ".join(f["choices"]) if f["choices"] else "")).strip()])
    data = write_workbook([("Data", [labels, sample], [max(14, min(40, len(l) + 4)) for l in labels]), ("How to fill", how, [34, 12, 16, 22, 90])])
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="lexora_{file_type}_template.xlsx"'})


@router.post("/canada/upload", status_code=201)
async def ca_upload(request: Request, file: UploadFile = File(...), file_type: str = Form(...), vendor: str = Form("lexora"),
                    db: AsyncSession = Depends(get_db)):
    who = _guard(request)
    org = R._org(request)
    if file_type not in CA_FILE_TYPES:
        raise HTTPException(400, f"Unknown file type '{file_type}'")
    data = await file.read()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is bigger than 15 MB")
    try:
        headers, rows, _sheet = parse_upload(file.filename or "", data)
    except ParseError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, "The file has a header but no data rows")
    mapping = ca_auto_map(headers, file_type)
    norm = {h.strip().lower(): h for h in headers}
    for k, src in preset_mapping(vendor, file_type).items():
        if src and src.strip().lower() in norm:
            mapping[k] = norm[src.strip().lower()]
    saved = (await db.execute(select(MigrationImportPreset).where(MigrationImportPreset.org_id == org, MigrationImportPreset.vendor == vendor,
                                                              MigrationImportPreset.file_type == file_type))).scalar_one_or_none()
    for k, src in ((saved.mapping if saved else {}) or {}).items():
        if src and src.strip().lower() in norm:
            mapping[k] = norm[src.strip().lower()]
    b = CaImportBatch(org_id=uuid.UUID(org), file_type=file_type, vendor=vendor or "lexora", file_name=file.filename, status="uploaded",
                      headers=headers, raw_rows=[{k: _j(v) for k, v in r.items()} for r in rows], mapping=mapping,
                      row_count=len(rows), created_by=who)
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return {**_batch_out(b, True),
            "missing_required": [f["label"] for f in ca_fields_of(file_type) if f["required"] and not mapping.get(f["key"])
                                 and not (f["key"] == "first_name" and mapping.get("full_name"))],
            "preset_note": (PRESETS.get(vendor) or {}).get("note")}


class ValidateIn(BaseModel):
    mapping: dict | None = None
    overrides: dict | None = None
    save_preset: bool = False


async def _ctx(request: Request, file_type: str) -> dict:
    ctx = {"employees": await _emps_by_code(request), "today": local_today(), "year": local_today().year, "lts": {}, "leave_types": []}
    if file_type == "ca_leave_balances":
        lts = await _emp_get(request, "/masters/leave-types") or []
        ctx["lts"] = {(t.get("code") or "").upper(): t for t in lts if t.get("code")}
        ctx["leave_types"] = list(ctx["lts"].keys())
    return ctx


@router.post("/canada/batches/{batch_id}/validate")
async def ca_validate(batch_id: str, body: ValidateIn, request: Request, db: AsyncSession = Depends(get_db)):
    _guard(request)
    org = R._org(request)
    b = await _batch(db, org, batch_id)
    if b.status in ("committed", "rolled_back"):
        raise HTTPException(409, f"This batch is already {b.status.replace('_', ' ')} — upload again")
    mapping = {k: (v or None) for k, v in (body.mapping or b.mapping or {}).items()}
    missing = [f["label"] for f in ca_fields_of(b.file_type) if f["required"] and not mapping.get(f["key"])
               and not (f["key"] == "first_name" and mapping.get("full_name"))]
    if missing:
        raise HTTPException(400, f"Map these required columns first: {', '.join(missing)}")
    typed = CM.normalize_rows(b.file_type, mapping, b.raw_rows or [], body.overrides)
    summary = CM.validate_rows(b.file_type, typed, await _ctx(request, b.file_type))
    b.mapping, b.rows, b.summary, b.error_count, b.status = mapping, _rows_jsonable(typed), summary, summary["with_errors"], "validated"
    if body.save_preset and b.vendor and b.vendor != "lexora":
        p = (await db.execute(select(MigrationImportPreset).where(MigrationImportPreset.org_id == org, MigrationImportPreset.vendor == b.vendor,
                                                              MigrationImportPreset.file_type == b.file_type))).scalar_one_or_none()
        if not p:
            p = MigrationImportPreset(org_id=org, vendor=b.vendor, file_type=b.file_type)
            db.add(p)
        p.name, p.mapping = f"{b.vendor} · {CA_FILE_TYPES[b.file_type]['label']}", {k: v for k, v in mapping.items() if v}
    await db.commit()
    await db.refresh(b)
    return _batch_out(b, True)


@router.get("/canada/batches")
async def ca_batches(request: Request, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    rows = (await db.execute(select(CaImportBatch).where(CaImportBatch.org_id == org).order_by(CaImportBatch.created_at.desc()).limit(300))).scalars().all()
    return [_batch_out(b) for b in rows]


@router.get("/canada/batches/{batch_id}")
async def ca_batch(batch_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    return _batch_out(await _batch(db, R._org(request), batch_id), True)


class CommitIn(BaseModel):
    allow_partial: bool = False


@router.post("/canada/batches/{batch_id}/commit")
async def ca_commit(batch_id: str, body: CommitIn, request: Request, db: AsyncSession = Depends(get_db)):
    _guard(request)
    org = R._org(request)
    b = await _batch(db, org, batch_id)
    if b.status != "validated":
        raise HTTPException(409, "Validate the file first" if b.status == "uploaded" else f"This batch is already {b.status.replace('_', ' ')}")
    ctx = await _ctx(request, b.file_type)
    typed = CM.normalize_rows(b.file_type, b.mapping or {}, b.raw_rows or [], None)
    summary = CM.validate_rows(b.file_type, typed, ctx)
    b.rows, b.summary, b.error_count = _rows_jsonable(typed), summary, summary["with_errors"]
    if b.error_count and not body.allow_partial:
        await db.commit()
        raise HTTPException(409, f"{b.error_count} row(s) still have problems — fix them, or tick 'import the good rows only'")
    work = SimpleNamespace(id=b.id, org_id=b.org_id, file_type=b.file_type, vendor=b.vendor, summary=b.summary, rows=typed)
    try:
        result = await CM.commit_batch(db, work, ctx["employees"], _emp_client(request), ctx["lts"])
        b.status, b.committed_at, b.result = "committed", datetime.now(timezone.utc), result
        await db.commit()
    except HTTPException:
        await db.rollback(); raise
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        from shared.ops_runtime import error_ref as _error_ref  # WASA 2026-10-21: no exception text in answers
        _ref = _error_ref()
        __import__("logging").getLogger("lexora.payroll.migration").exception("Import failed and nothing was saved ref=%s", _ref)
        raise HTTPException(500, f"Import failed and nothing was saved. Please try again; if it keeps happening, give support the reference {_ref}.")
    await record_audit(request, "migration.ca.commit", entity_type="import_batch", entity_id=str(b.id),
                       summary=f"Imported {result.get('rows_written', 0)} {CA_FILE_TYPES[b.file_type]['label']} rows from {b.file_name}")
    await db.refresh(b)
    return _batch_out(b)


@router.post("/canada/batches/{batch_id}/rollback")
async def ca_rollback(batch_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Undo the batch — every ledgered row goes back to its prior state (batch-tagged)."""
    _guard(request)
    org = R._org(request)
    b = await _batch(db, org, batch_id)
    if b.status != "committed":
        raise HTTPException(409, "Only a committed batch can be undone")
    try:
        counts = await CM.rollback_batch(db, b, _emp_client(request))
        b.status, b.rolled_back_at, b.result = "rolled_back", datetime.now(timezone.utc), {**(b.result or {}), "rollback": counts}
        await db.commit()
    except HTTPException:
        await db.rollback(); raise
    except Exception as e:  # noqa: BLE001
        await db.rollback()
        from shared.ops_runtime import error_ref as _error_ref  # WASA 2026-10-21: no exception text in answers
        _ref = _error_ref()
        __import__("logging").getLogger("lexora.payroll.migration").exception("Undo failed and nothing was changed ref=%s", _ref)
        raise HTTPException(500, f"Undo failed and nothing was changed. Please try again; if it keeps happening, give support the reference {_ref}.")
    await record_audit(request, "migration.ca.rollback", entity_type="import_batch", entity_id=str(b.id),
                       summary=f"Undid {CA_FILE_TYPES[b.file_type]['label']} import {b.file_name}", meta=counts)
    await db.refresh(b)
    return _batch_out(b)


@router.delete("/canada/batches/{batch_id}")
async def ca_discard(batch_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _guard(request)
    b = await _batch(db, R._org(request), batch_id)
    if b.status == "committed":
        raise HTTPException(409, "Committed batches cannot be discarded — undo it instead")
    await db.delete(b)
    await db.commit()
    return {"ok": True}


@router.get("/last-batch")
async def last_batch(request: Request, db: AsyncSession = Depends(get_db)):
    """The most recent committed import batch → the 'Undo last import' button."""
    org = R._org(request)
    ca = (await db.execute(select(CaImportBatch).where(CaImportBatch.org_id == org, CaImportBatch.status == "committed")
                           .order_by(CaImportBatch.committed_at.desc()).limit(1))).scalar_one_or_none()
    cands = []
    if ca:
        cands.append({"country": "CA", "id": str(ca.id), "file_type": ca.file_type, "file_name": ca.file_name,
                      "committed_at": ca.committed_at.isoformat() if ca.committed_at else None, "rows": ca.row_count,
                      "undo_url": f"/payroll/migration/canada/batches/{ca.id}/rollback"})
    cands.sort(key=lambda x: x["committed_at"] or "", reverse=True)
    return {"last": cands[0] if cands else None}


@router.get("/presets")
async def presets(request: Request):
    R._org(request)
    return {"presets": presets_summary(), "mappings": {c: v["mappings"] for c, v in PRESETS.items()}}


# ───────────────────────────── parallel-run reconciliation ─────────────────────────────

def _pr_out(p: MigrationParallelRun, full=False) -> dict:
    out = {"id": str(p.id), "country": p.country, "month": p.month, "vendor": p.vendor, "file_name": p.file_name,
           "tolerance": float(p.tolerance or 0), "status": p.status, "signed_by": p.signed_by,
           "signed_at": p.signed_at.isoformat() if p.signed_at else None, "sign_note": p.sign_note,
           "created_by": p.created_by, "created_at": p.created_at.isoformat() if p.created_at else None,
           "summary": (p.result or {}).get("summary")}
    if full:
        out.update({"headers": p.headers or [], "mapping": p.mapping or {}, "sample": (p.raw_rows or [])[:3], "result": p.result or {}})
    return out


@router.get("/parallel-run/components")
async def pr_components(request: Request, country: str = "CA"):
    R._org(request)
    return {"components": MR.component_catalog(country), "presets": {c: v["mappings"].get("parallel_run") for c, v in PRESETS.items()}}


@router.post("/parallel-run", status_code=201)
async def pr_upload(request: Request, file: UploadFile = File(...), month: str = Form(...), country: str = Form("CA"),
                    vendor: str = Form("custom"), tolerance: float = Form(1.0), db: AsyncSession = Depends(get_db)):
    who = _guard(request)
    org = R._org(request)
    if len(month) != 7 or month[4] != "-":
        raise HTTPException(400, "month must be YYYY-MM")
    data = await file.read()
    try:
        headers, rows, _ = parse_upload(file.filename or "", data)
    except ParseError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, "No data rows in the register")
    norm = {h.strip().lower(): h for h in headers}
    mapping = {}
    for k, src in preset_mapping(vendor, "parallel_run").items():
        if src.strip().lower() in norm:
            mapping[k] = norm[src.strip().lower()]
    # generic auto-match on component keys / labels
    for c in MR.component_catalog(country):
        if c["key"] in mapping:
            continue
        for h in headers:
            hn = h.strip().lower().replace(" ", "").replace("_", "")
            if hn in (c["key"].replace("_", ""), c["label"].lower().replace(" ", "")):
                mapping[c["key"]] = h; break
    if "employee_code" not in mapping:
        for h in headers:
            if h.strip().lower().replace(" ", "").replace("_", "") in ("employeecode", "employeeid", "employeenumber", "empcode", "empid", "code"):
                mapping["employee_code"] = h; break
    p = MigrationParallelRun(org_id=uuid.UUID(org), country=country.upper(), month=month, vendor=vendor, file_name=file.filename,
                             headers=headers, raw_rows=[{k: _j(v) for k, v in r.items()} for r in rows], mapping=mapping,
                             tolerance=tolerance, status="uploaded", created_by=who)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _pr_out(p, True)


class CompareIn(BaseModel):
    mapping: dict | None = None
    tolerance: float | None = None


@router.post("/parallel-run/{pr_id}/compare")
async def pr_compare(pr_id: str, body: CompareIn, request: Request, db: AsyncSession = Depends(get_db)):
    _guard(request)
    org = R._org(request)
    p = (await db.execute(select(MigrationParallelRun).where(MigrationParallelRun.id == uuid.UUID(pr_id), MigrationParallelRun.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Parallel run not found")
    mapping = {k: v for k, v in (body.mapping or p.mapping or {}).items() if v}
    if not mapping.get("employee_code"):
        raise HTTPException(400, "Map the Employee Code column first")
    if body.tolerance is not None:
        p.tolerance = body.tolerance
    emps = {e["id"]: e for e in await R._fetch_employees(request)}
    y, m = int(p.month[:4]), int(p.month[5:7])
    rows = (await db.execute(select(PayStub, PayRun.pay_date).join(PayRun, PayStub.pay_run_id == PayRun.id).where(PayStub.org_id == org))).all()
    ours = []
    for s, pd in rows:
        if (pd.year, pd.month) != (y, m):
            continue
        e = emps.get(str(s.employee_id)) or {}
        ours.append(MR.stub_to_row({**(s.detail or {}), "gross": float(s.gross), "net_pay": float(s.net_pay)}, e.get("employee_number") or ""))
    cur = "CA$"
    res = MR.compare(p.raw_rows or [], mapping, ours, float(p.tolerance or 0), cur)
    res["lexora_stubs_in_month"] = len(ours)
    if not ours:
        res["summary"]["headline"] = f"No Lexora HRM pay stubs have a pay date in {p.month} — run (or test-run) that month first."
        res["summary"]["matched_within_tolerance"] = False
    p.mapping, p.result, p.status = mapping, res, "compared"
    await db.commit()
    await db.refresh(p)
    return _pr_out(p, True)


class SignIn(BaseModel):
    note: str = ""


@router.post("/parallel-run/{pr_id}/sign")
async def pr_sign(pr_id: str, body: SignIn, request: Request, db: AsyncSession = Depends(get_db)):
    who = _guard(request)
    org = R._org(request)
    p = (await db.execute(select(MigrationParallelRun).where(MigrationParallelRun.id == uuid.UUID(pr_id), MigrationParallelRun.org_id == org))).scalar_one_or_none()
    if not p or p.status not in ("compared", "signed"):
        raise HTTPException(409, "Compare first")
    summ = (p.result or {}).get("summary") or {}
    if not summ.get("matched_within_tolerance"):
        raise HTTPException(409, "Cannot sign — the run did not match within tolerance. Fix the differences and compare again.")
    cur = "CA$"
    p.status, p.signed_by, p.signed_at = "signed", who, datetime.now(timezone.utc)
    p.sign_note = f"Parallel run {p.month} matched within {cur}{float(p.tolerance):,.2f} — {summ.get('lines_matched')} lines, " \
                  f"{summ.get('employees_compared')} employees. {body.note}".strip()
    await db.commit()
    await record_audit(request, "migration.parallel_run.sign", entity_type="parallel_run", entity_id=str(p.id), summary=p.sign_note)
    return _pr_out(p)


@router.get("/parallel-run")
async def pr_list(request: Request, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    rows = (await db.execute(select(MigrationParallelRun).where(MigrationParallelRun.org_id == org)
                             .order_by(MigrationParallelRun.created_at.desc()).limit(100))).scalars().all()
    return [_pr_out(p) for p in rows]


@router.get("/parallel-run/{pr_id}")
async def pr_get(pr_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    p = (await db.execute(select(MigrationParallelRun).where(MigrationParallelRun.id == uuid.UUID(pr_id), MigrationParallelRun.org_id == org))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Parallel run not found")
    return _pr_out(p, True)


# ───────────────────────────── checklist ─────────────────────────────

async def _facts(db, org: str, request: Request, country: str) -> dict:
    async def cnt(stmt):
        return int((await db.execute(stmt)).scalar_one() or 0)

    emps = await R._fetch_employees(request) or []
    f: dict = {}
    ca_emps = list(emps)
    f["ca_employees"] = len(ca_emps)
    f["ca_td1"] = sum(1 for e in ca_emps if e.get("td1_federal_claim") is not None)
    s = (await db.execute(select(CaOrgSettings).where(CaOrgSettings.org_id == org))).scalar_one_or_none()
    f["ca_settings_done"] = bool(s and s.remitter_type)
    ver = await cnt(select(func.count()).select_from(RuleVerification).where(
        RuleVerification.org_id == org, RuleVerification.rule_key.like("ca.%"), RuleVerification.status == "verified"))
    f["ca_values_verified"] = ver
    f["ca_salary_history"] = await cnt(select(func.count()).select_from(CaImportBatch).where(
        CaImportBatch.org_id == org, CaImportBatch.file_type == "ca_salary_history", CaImportBatch.status == "committed"))
    f["ca_ytd"] = await cnt(select(func.count(func.distinct(CaOpeningYtd.employee_id))).where(CaOpeningYtd.org_id == org))
    f["ca_leave"] = await cnt(select(func.count()).select_from(CaImportBatch).where(
        CaImportBatch.org_id == org, CaImportBatch.file_type == "ca_leave_balances", CaImportBatch.status == "committed"))
    f["ca_contractors"] = await cnt(select(func.count()).select_from(CaContractor).where(CaContractor.org_id == org))
    f["parallel_signed"] = await cnt(select(func.count()).select_from(MigrationParallelRun).where(
        MigrationParallelRun.org_id == org, MigrationParallelRun.country == country, MigrationParallelRun.status == "signed"))
    return f


@router.get("/checklist")
async def checklist(request: Request, country: str = "CA", db: AsyncSession = Depends(get_db)):
    org = R._org(request)
    c = "CA"
    ticks = {t.step_key: {"done": t.done, "note": t.note, "done_by": t.done_by, "done_at": t.done_at.isoformat() if t.done_at else None}
             for t in (await db.execute(select(MigrationChecklistStep).where(
                 MigrationChecklistStep.org_id == org, MigrationChecklistStep.country == c))).scalars().all()}
    return MC.progress(c, await _facts(db, org, request, c), ticks)


class TickIn(BaseModel):
    country: str = "CA"
    step_key: str
    done: bool = True
    note: str = ""


@router.post("/checklist/tick")
async def tick(body: TickIn, request: Request, db: AsyncSession = Depends(get_db)):
    who = _guard(request)
    org = R._org(request)
    c = "CA"
    if body.step_key not in {s["key"] for s in MC.STEPS[c]}:
        raise HTTPException(404, "Unknown step")
    row = (await db.execute(select(MigrationChecklistStep).where(
        MigrationChecklistStep.org_id == org, MigrationChecklistStep.country == c, MigrationChecklistStep.step_key == body.step_key))).scalar_one_or_none()
    if not row:
        row = MigrationChecklistStep(org_id=uuid.UUID(org), country=c, step_key=body.step_key)
        db.add(row)
    row.done, row.note, row.done_by, row.done_at = body.done, body.note, who, datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}
