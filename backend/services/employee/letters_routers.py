"""The letter engine — ONE for the whole product (employee service).

PUBLISHED CONTRACT (other streams call these; do not change the shapes)
  POST /employees/{employee_id}/letters
       {template_key, data: {...extra fields}, effective_date?, issue: true|false}
       → {letter_id, number, pdf_url}          (number is null for a draft)
  GET  /employees/{employee_id}/letters        HR: all · the employee: own issued letters
  GET  /letters/{id}/pdf                       the stored PDF, byte for byte

Also here
  GET  /letters                     HR grid of every letter in the org
  GET  /letters/mine                self-service "My letters"
  POST /letters/{id}/issue          draft → issued (gets its number)
  POST /letters/{id}/reissue        NEW letter, NEW number, from the same content
  POST /letters/{id}/void {reason}  mark void — the stored copy stays
  DELETE /letters/{id}              discard a DRAFT only
  POST /letters/preview             a PDF that is NOT stored (template editor)
  GET  /letters/fields              the allow-list of {{fields}}
  GET/POST /letters/templates · PUT /letters/templates/{id} · POST /letters/templates/{id}/reset
  GET/PUT /letters/settings         letterhead, signatory, number prefix

Service-to-service: shared/utils/letters_client.py → issue_letter(...).
"""
import base64
import sys
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.append("/app")
from shared.utils.audit import record_audit  # noqa: E402
from shared.utils.docstore import sniff_image  # noqa: E402
from shared.utils.org_time import local_today, org_today  # noqa: E402

import letters_engine as eng  # noqa: E402
from career_common import (actor_of, full_name, get_extras, is_self, load_employee, my_employee, now,  # noqa: E402
                           org_of, parse_date, require_setup, to_uuid)
from career_common import is_people_hr as is_hr, require_people_hr as require_hr  # noqa: E402  (decision 2026-10-21: HR only, not the pay office)
from comp_models import CompHistory  # noqa: E402
from database import get_db  # noqa: E402
from letters_defaults import DEFAULT_TEMPLATES  # noqa: E402
from letters_models import IssuedLetter, LetterCounter, LetterSettings, LetterTemplate, LetterTemplateVersion  # noqa: E402
from models import Employee  # noqa: E402

router = APIRouter(tags=["letters"])


# ── D1: employee custom fields as merge fields ({{custom.<key>}}) ─────────────
async def _custom_field_defs(db, org: str) -> list[dict]:
    """Active employee custom-field definitions of the org ({key, label, type}); [] when none."""
    try:
        async with db.begin_nested():
            rows = (await db.execute(text(
                "SELECT key, label, type FROM cf_definitions WHERE org_id = :org AND entity = 'employee' AND active IS TRUE "
                "ORDER BY sort_order, label").bindparams(bindparam("org", type_=PG_UUID(as_uuid=True))),
                {"org": to_uuid(org, "org")})).mappings().all()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001  (table not there yet)
        return []


async def _custom_values(db, org: str, employee_id) -> dict:
    """{custom.<key>: value} from employees.custom_data (empty when the column / row is missing)."""
    if not employee_id:
        return {}
    try:
        async with db.begin_nested():
            r = (await db.execute(text("SELECT custom_data FROM employees WHERE id = :id AND org_id = :org").bindparams(
                bindparam("id", type_=PG_UUID(as_uuid=True)), bindparam("org", type_=PG_UUID(as_uuid=True))),
                {"id": employee_id, "org": to_uuid(org, "org")})).first()
        data = r[0] if r else None
        if isinstance(data, str):
            import json
            data = json.loads(data)
        if not isinstance(data, dict):
            return {}
        out = {}
        for k, v in data.items():
            if isinstance(v, list):
                v = ", ".join(map(str, v))
            elif isinstance(v, bool):
                v = "Yes" if v else "No"
            out[f"{eng.CUSTOM_NS}.{k}"] = v
        return out
    except Exception:  # noqa: BLE001
        return {}


def _version_row(t: LetterTemplate, *, state: str, actor: str, note: str | None = None, body: str | None = None,
                 version: int | None = None) -> LetterTemplateVersion:
    return LetterTemplateVersion(org_id=t.org_id, template_id=t.id, version=t.version if version is None else version,
                                 state=state, name=t.name, body=t.body if body is None else body, note=note, saved_by=actor)


async def _draft_row(db, t: LetterTemplate) -> LetterTemplateVersion | None:
    return (await db.execute(select(LetterTemplateVersion).where(
        LetterTemplateVersion.template_id == t.id, LetterTemplateVersion.state == "draft"))).scalar_one_or_none()


async def _ensure_version_history(db, t: LetterTemplate, actor: str) -> None:
    """A template saved before versioning existed gets its current body as its first history row."""
    have = (await db.execute(select(LetterTemplateVersion.id).where(
        LetterTemplateVersion.template_id == t.id, LetterTemplateVersion.state == "published").limit(1))).first()
    if not have:
        db.add(_version_row(t, state="published", actor=t.updated_by or actor, note="Text in use before version history began"))


async def _keep_history(db, t: LetterTemplate, actor: str, *, note: str | None = None, drop_draft: bool = False) -> None:
    """Best-effort version bookkeeping around the legacy PUT: with ``note`` a row for the
    template's CURRENT body is added; without it only the pre-history row. Runs in a
    savepoint so an installation whose history table is not there yet still saves."""
    try:
        async with db.begin_nested():
            await _ensure_version_history(db, t, actor)
            if note:
                db.add(_version_row(t, state="published", actor=actor, note=note))
            if drop_draft:
                d = await _draft_row(db, t)
                if d:
                    await db.delete(d)             # a direct publish supersedes any draft
            await db.flush()
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger("lexora.letters").info("template history not kept: %s", str(e).splitlines()[0][:120])

MAX_IMAGE_BYTES = 400 * 1024
_IMAGE_MIMES = {"image/png", "image/jpeg"}
_seeded: set = set()


# ── templates ─────────────────────────────────────────────────────────────────
async def ensure_defaults(db: AsyncSession, org: str) -> int:
    """Seed the default templates an org does not have yet. Idempotent."""
    if org in _seeded:
        return 0
    oid = to_uuid(org, "org")
    have = {r[0] for r in (await db.execute(select(LetterTemplate.key).where(LetterTemplate.org_id == oid))).all()}
    n = 0
    for t in DEFAULT_TEMPLATES:
        if t["key"] not in have:
            db.add(LetterTemplate(org_id=oid, key=t["key"], name=t["name"], country="", language="en",
                                  body=t["body"], version=1, is_default=True, updated_by="system"))
            n += 1
    if n:
        await db.commit()
    _seeded.add(org)
    return n


def _tpl_out(t: LetterTemplate, with_body: bool = True) -> dict:
    return {"id": str(t.id), "key": t.key, "name": t.name, "country": t.country or "", "language": t.language,
            "version": t.version, "is_default": bool(t.is_default), "active": bool(t.active),
            "updated_by": t.updated_by, "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "fields": eng.find_fields(t.body), **({"body": t.body} if with_body else {})}


async def pick_template(db, org: str, key: str, country: str, language: str = "en") -> LetterTemplate:
    await ensure_defaults(db, org)
    rows = (await db.execute(select(LetterTemplate).where(
        LetterTemplate.org_id == to_uuid(org, "org"), LetterTemplate.key == key,
        LetterTemplate.active.is_(True)))).scalars().all()
    if not rows:
        raise HTTPException(404, f"There is no active letter template '{key}'")
    country = (country or "").upper()

    def score(t):
        return ((2 if (t.country or "") == country else 1 if not t.country else 0),
                (1 if t.language == language else 0))
    best = max(rows, key=score)
    if score(best)[0] == 0:
        raise HTTPException(404, f"The template '{key}' is not set up for {country or 'this country'}")
    return best


class TemplateIn(BaseModel):
    key: str | None = None
    name: str
    body: str
    country: str = ""
    language: str = "en"
    active: bool = True


def _check_body(body: str):
    errs = eng.validate_template(body)
    if errs:
        raise HTTPException(422, {"message": "The template was not saved.", "errors": errs})


@router.get("/letters/fields")
async def fields(request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    return {"fields": eng.catalogue(await _custom_field_defs(db, org_of(request))),
            "syntax": ["{{field}}", "# Heading", "## Sub-heading", "**bold**", "- bullet", "1. numbered",
                       "| table | row |", "---"]}


@router.get("/letters/templates")
async def list_templates(request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    await ensure_defaults(db, org)
    rows = (await db.execute(select(LetterTemplate).where(LetterTemplate.org_id == to_uuid(org, "org"))
                             .order_by(LetterTemplate.name))).scalars().all()
    drafts = {r[0] for r in (await db.execute(select(LetterTemplateVersion.template_id).where(
        LetterTemplateVersion.org_id == to_uuid(org, "org"), LetterTemplateVersion.state == "draft"))).all()}
    return [{**_tpl_out(t), "has_draft": t.id in drafts} for t in rows]


@router.post("/letters/templates", status_code=201)
async def create_template(body: TemplateIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_setup(request)
    org = org_of(request)
    await ensure_defaults(db, org)
    import re
    key = re.sub(r"[^a-z0-9_]", "_", (body.key or body.name).strip().lower())[:60].strip("_")
    if not key:
        raise HTTPException(400, "Give the template a name")
    _check_body(body.body)
    dup = (await db.execute(select(LetterTemplate.id).where(
        LetterTemplate.org_id == to_uuid(org, "org"), LetterTemplate.key == key,
        LetterTemplate.country == (body.country or "").upper(), LetterTemplate.language == body.language))).first()
    if dup:
        raise HTTPException(409, "A template with this key, country and language already exists")
    t = LetterTemplate(org_id=to_uuid(org, "org"), key=key, name=body.name.strip()[:160],
                       country=(body.country or "").upper()[:2], language=(body.language or "en")[:8],
                       body=body.body, version=1, is_default=False, active=body.active, updated_by=actor_of(request))
    db.add(t)
    await db.commit()
    return _tpl_out(t)


async def _template(db, org: str, tid: str) -> LetterTemplate:
    t = (await db.execute(select(LetterTemplate).where(LetterTemplate.id == to_uuid(tid, "template"),
                                                       LetterTemplate.org_id == to_uuid(org, "org")))).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Template not found")
    return t


@router.put("/letters/templates/{template_id}")
async def update_template(template_id: str, body: TemplateIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_setup(request)
    org = org_of(request)
    t = await _template(db, org, template_id)
    _check_body(body.body)
    bumped = body.body != t.body
    if bumped:
        await _keep_history(db, t, actor_of(request))      # the body ABOUT to be replaced, if history is empty
        t.version = (t.version or 1) + 1
        t.is_default = False
    t.name, t.body, t.active, t.updated_by = body.name.strip()[:160], body.body, body.active, actor_of(request)
    if bumped:
        await _keep_history(db, t, actor_of(request), note="Published from the editor", drop_draft=True)
    await db.commit()
    await record_audit(request, "letters.template.save", entity_type="letter_template", entity_id=template_id,
                       summary=f"Letter template '{t.name}' saved (version {t.version})")
    return _tpl_out(t)


# ── D1: draft → publish → version history → rollback ──────────────────────────
class DraftIn(BaseModel):
    body: str
    name: str | None = None
    note: str | None = None          # "what changed" — shown in the Versions list once published


@router.put("/letters/templates/{template_id}/draft")
async def save_draft(template_id: str, body: DraftIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Keep editor text WITHOUT changing the template letters are made from."""
    require_setup(request)
    t = await _template(db, org_of(request), template_id)
    _check_body(body.body)
    d = await _draft_row(db, t)
    if not d:
        d = _version_row(t, state="draft", actor=actor_of(request), body=body.body, version=0)
        db.add(d)
    d.body, d.name, d.saved_by, d.saved_at = body.body, (body.name or t.name).strip()[:160], actor_of(request), now()
    if body.note is not None:
        d.note = body.note.strip()[:300] or None
    await db.commit()
    return {"template_id": str(t.id), "draft": {"body": d.body, "name": d.name, "note": d.note or "", "saved_by": d.saved_by, "saved_at": d.saved_at.isoformat()},
            "published_version": t.version}


@router.get("/letters/templates/{template_id}/versions")
async def list_versions(template_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    t = await _template(db, org_of(request), template_id)
    rows = (await db.execute(select(LetterTemplateVersion).where(LetterTemplateVersion.template_id == t.id)
                             .order_by(LetterTemplateVersion.state, LetterTemplateVersion.version.desc()))).scalars().all()
    out = [{"id": str(v.id), "version": v.version, "state": v.state, "name": v.name, "body": v.body, "note": v.note or "",
            "saved_by": v.saved_by, "saved_at": v.saved_at.isoformat() if v.saved_at else None, "current": v.state == "published" and v.version == t.version}
           for v in rows]
    if not any(v["state"] == "published" for v in out):
        out.append({"id": None, "version": t.version, "state": "published", "name": t.name, "body": t.body,
                    "note": "Text in use (history starts at the next save)", "saved_by": t.updated_by,
                    "saved_at": t.updated_at.isoformat() if t.updated_at else None, "current": True})
    return {"template_id": str(t.id), "published_version": t.version, "versions": out}


@router.post("/letters/templates/{template_id}/publish")
async def publish_draft(template_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """The draft becomes the live template (version + 1). Letters already issued keep their snapshot."""
    require_setup(request)
    t = await _template(db, org_of(request), template_id)
    d = await _draft_row(db, t)
    if not d:
        raise HTTPException(409, "There is no draft to publish")
    _check_body(d.body)
    await _ensure_version_history(db, t, actor_of(request))
    old = t.version
    if d.body != t.body or d.name != t.name:
        t.version = (t.version or 1) + 1
        t.is_default = False
        t.body, t.name, t.updated_by = d.body, d.name, actor_of(request)
        db.add(_version_row(t, state="published", actor=actor_of(request), note=d.note or "Published from a draft"))
    await db.delete(d)
    await db.commit()
    await record_audit(request, "letters.template.publish", entity_type="letter_template", entity_id=template_id,
                       summary=f"Letter template '{t.name}' published as version {t.version} (was {old})")
    return _tpl_out(t)


@router.post("/letters/templates/{template_id}/rollback/{version}")
async def rollback(template_id: str, version: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Make an earlier published body live again — as a NEW version, so history is never rewritten."""
    require_setup(request)
    t = await _template(db, org_of(request), template_id)
    v = (await db.execute(select(LetterTemplateVersion).where(
        LetterTemplateVersion.template_id == t.id, LetterTemplateVersion.state == "published",
        LetterTemplateVersion.version == int(version)))).scalar_one_or_none()
    if not v:
        raise HTTPException(404, f"There is no published version {version} of this template")
    if v.version == t.version and v.body == t.body:
        raise HTTPException(409, f"Version {version} is already the text in use")
    await _ensure_version_history(db, t, actor_of(request))
    old = t.version
    t.version = (t.version or 1) + 1
    t.body, t.name, t.is_default, t.updated_by = v.body, v.name, False, actor_of(request)
    db.add(_version_row(t, state="published", actor=actor_of(request), note=f"Rolled back to version {version}"))
    await db.commit()
    await record_audit(request, "letters.template.rollback", entity_type="letter_template", entity_id=template_id,
                       summary=f"Letter template '{t.name}' rolled back to version {version} text (now version {t.version}, was {old})")
    return _tpl_out(t)


@router.post("/letters/templates/{template_id}/reset")
async def reset_template(template_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_setup(request)
    t = await _template(db, org_of(request), template_id)
    d = next((x for x in DEFAULT_TEMPLATES if x["key"] == t.key), None)
    if not d:
        raise HTTPException(400, "This template has no default text to go back to")
    if t.body != d["body"]:
        t.version = (t.version or 1) + 1
    t.body, t.name, t.is_default, t.updated_by = d["body"], d["name"], True, actor_of(request)
    await db.commit()
    return _tpl_out(t)


# ── letterhead ────────────────────────────────────────────────────────────────
async def _settings_row(db, org: str) -> LetterSettings | None:
    return (await db.execute(select(LetterSettings).where(LetterSettings.org_id == to_uuid(org, "org")))).scalar_one_or_none()


async def _org_names(db, org: str) -> dict:
    """Company name from the organisation record (same database). {} if not readable."""
    try:
        async with db.begin_nested():
            r = (await db.execute(text("SELECT name, legal_name FROM organizations WHERE id = :id").bindparams(
                bindparam("id", type_=PG_UUID(as_uuid=True))), {"id": to_uuid(org, "org")})).mappings().first()
        return dict(r) if r else {}
    except Exception:  # noqa: BLE001
        return {}


async def _entity_of_employee(db, org: str, employee_id) -> str | None:
    """employees.entity_id (D4 multi-entity) — None on an older schema or when unset."""
    if not employee_id:
        return None
    try:
        async with db.begin_nested():
            r = (await db.execute(text("SELECT entity_id FROM employees WHERE id = :id AND org_id = :org").bindparams(
                bindparam("id", type_=PG_UUID(as_uuid=True)), bindparam("org", type_=PG_UUID(as_uuid=True))),
                {"id": to_uuid(str(employee_id), "employee"), "org": to_uuid(org, "org")})).first()
        return str(r[0]) if r and r[0] else None
    except Exception:  # noqa: BLE001
        return None


async def letterhead(db, org: str, entity_id=None) -> dict:
    """The org letterhead — overlaid with the employee's LEGAL ENTITY (D4) when ``entity_id``
    is given: the entity's name becomes the company name, its address / signatory / footer /
    logo win where set. Letter numbering stays the org's (one counter per org)."""
    s = await _settings_row(db, org)
    names = await _org_names(db, org)
    head = {"company_name": (s.company_name if s and s.company_name else names.get("name")) or "",
            "legal_name": names.get("legal_name") or (s.company_name if s else "") or names.get("name") or "",
            "address": (s.address if s else "") or "", "footer_text": (s.footer_text if s else "") or "",
            "signatory_name": (s.signatory_name if s else "") or "",
            "signatory_designation": (s.signatory_designation if s else "") or "",
            "number_prefix": (s.number_prefix if s else "") or "LTR",
            "logo": s.logo if s else None, "signature": s.signature if s else None,
            "entity_name": "", "entity_code": ""}
    ent = None
    if entity_id:
        try:
            from shared import legal_entities as LE
            ent = await LE.entity_by_id(org, entity_id)
        except Exception:  # noqa: BLE001 — older install without the auth table
            ent = None
    if ent:
        head["company_name"] = ent.get("name") or head["company_name"]
        head["legal_name"] = ent.get("name") or head["legal_name"]
        head["entity_name"], head["entity_code"] = ent.get("name") or "", ent.get("short_code") or ""
        line1 = ", ".join(x for x in [ent.get("address_line1"), ent.get("address_line2")] if x)
        line2 = " ".join(str(x) for x in [ent.get("city"), ent.get("state_code"), ent.get("pincode")] if x)
        if line1 or line2:
            head["address"] = "\n".join(x for x in [line1, line2] if x)
        lh, sig = dict(ent.get("letterhead") or {}), dict(ent.get("signatory") or {})
        if lh.get("footer_text"):
            head["footer_text"] = lh["footer_text"]
        if sig.get("name"):
            head["signatory_name"] = sig["name"]
            head["signatory_designation"] = sig.get("designation") or head["signatory_designation"]
        if lh.get("logo_data_url"):
            try:
                head["logo"], _ = _decode_image(lh["logo_data_url"], "entity logo")
            except HTTPException:
                pass
    return head


def _data_url(b: bytes | None, mime: str | None) -> str | None:
    return f"data:{mime or 'image/png'};base64,{base64.b64encode(b).decode()}" if b else None


def _decode_image(data_url: str, what: str) -> tuple[bytes, str]:
    try:
        head, b64 = data_url.split(",", 1)
        mime = head.split(":", 1)[1].split(";", 1)[0].lower()
        raw = base64.b64decode(b64, validate=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, f"The {what} could not be read. Use a PNG or JPG file.")
    if mime not in _IMAGE_MIMES:
        raise HTTPException(400, f"The {what} must be a PNG or JPG file")
    if len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(400, f"The {what} is too large (max 400 KB)")
    sniffed = sniff_image(raw)          # the bytes decide: a data URL that only CLAIMS to be PNG/JPG is refused
    if sniffed not in _IMAGE_MIMES:
        raise HTTPException(400, f"The {what} must be a PNG or JPG file")
    if mime == "image/jpg":
        mime = "image/jpeg"
    if sniffed != mime:
        raise HTTPException(400, f"The {what} is not the image type it claims to be. Use a real PNG or JPG file.")
    return raw, sniffed


class SettingsIn(BaseModel):
    company_name: str | None = None
    address: str | None = None
    footer_text: str | None = None
    signatory_name: str | None = None
    signatory_designation: str | None = None
    number_prefix: str | None = None
    logo: str | None = None            # data URL to set · "" to remove · omit to keep
    signature: str | None = None


def _settings_out(h: dict, s: LetterSettings | None) -> dict:
    return {k: h[k] for k in ("company_name", "address", "footer_text", "signatory_name",
                              "signatory_designation", "number_prefix")} | {
        "logo": _data_url(s.logo, s.logo_mime) if s else None,
        "signature": _data_url(s.signature, s.signature_mime) if s else None,
        "sample_number": eng.format_number(h["number_prefix"], local_today().year, 1)}


@router.get("/letters/settings")
async def get_settings(request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    return _settings_out(await letterhead(db, org), await _settings_row(db, org))


@router.put("/letters/settings")
async def put_settings(body: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_setup(request)
    org = org_of(request)
    s = await _settings_row(db, org)
    if not s:
        s = LetterSettings(org_id=to_uuid(org, "org"))
        db.add(s)
    for k in ("company_name", "address", "footer_text", "signatory_name", "signatory_designation"):
        v = getattr(body, k)
        if v is not None:
            setattr(s, k, v.strip() or None)
    if body.number_prefix is not None:
        s.number_prefix = eng.format_number(body.number_prefix, 2000, 1).split("/")[0]
    for k in ("logo", "signature"):
        v = getattr(body, k)
        if v == "":
            setattr(s, k, None); setattr(s, f"{k}_mime", None)
        elif v:
            raw, mime = _decode_image(v, k)
            setattr(s, k, raw); setattr(s, f"{k}_mime", mime)
    s.updated_by = actor_of(request)
    await db.commit()
    return _settings_out(await letterhead(db, org), s)


# ── values for one letter ─────────────────────────────────────────────────────
def _address(e: Employee) -> str:
    return ", ".join(x for x in [e.address_line1, e.address_line2, e.city, e.postal_code] if x)


async def build_values(db, org: str, e: Employee, head: dict, data: dict | None, effective: date | None,
                       letter_date: date) -> tuple[dict, dict, list[str]]:
    """→ (values {field: text}, tables, refused data keys)."""
    data = dict(data or {})
    comp_names = data.pop("component_names", None) or {}
    components = data.pop("salary_components", None)
    if isinstance(data.get("salary"), dict):
        data["salary"] = dict(data["salary"])
        components = data["salary"].pop("components", None) or components
    # A candidate recipient (no employee row yet) has no extras and no salary history.
    has_row = getattr(e, "id", None) is not None
    extra = (await get_extras(db, org, str(e.id))).get(str(e.id), {}) if has_row else {}
    cur = "CAD"                        # Canada-only product
    last = (await db.execute(select(CompHistory).where(CompHistory.employee_id == e.id, CompHistory.org_id == e.org_id)
                             .order_by(CompHistory.effective_from.desc(), CompHistory.created_at.desc()))).scalars().first() \
        if has_row else None
    annual = float(e.annual_salary or 0)
    raw: dict = {
        "employee.full_name": full_name(e), "employee.first_name": e.first_name, "employee.last_name": e.last_name,
        "employee.employee_number": e.employee_number, "employee.designation": e.job_title,
        "employee.department": e.department, "employee.grade": extra.get("grade") or getattr(e, "grade", None),
        "employee.location": e.work_location, "employee.manager_name": e.manager_name,
        "employee.joining_date": e.hire_date, "employee.employment_type": (e.employment_type or "").replace("_", " "),
        "employee.email": e.work_email or e.email, "employee.address": _address(e),
        "employee.probation_end_date": e.probation_end_date,
        "employee.confirmation_date": extra.get("confirmation_date"),
        "employee.employment_status": extra.get("employment_status"),
        "org.name": head["company_name"], "org.legal_name": head["legal_name"], "org.address": head["address"],
        "org.entity_name": head.get("entity_name") or head["company_name"], "org.entity_code": head.get("entity_code") or None,
        "org.signatory_name": head["signatory_name"], "org.signatory_designation": head["signatory_designation"],
        "letter.date": letter_date, "letter.effective_date": effective or letter_date,
        "salary.annual_ctc": annual or None, "salary.monthly_gross": (annual / 12) if annual else None,
        "salary.currency": cur, "salary.hourly_rate": float(e.hourly_rate or 0) or None,
    }
    if has_row:
        raw.update(await _custom_values(db, org, e.id))        # D1: {{custom.<key>}}
    given, refused = eng.flatten_data(data)
    raw.update({k: v for k, v in given.items() if v not in (None, "")})
    if raw.get("salary.old_annual") and raw.get("salary.new_annual"):
        try:
            old, new = float(raw["salary.old_annual"]), float(raw["salary.new_annual"])
            raw.setdefault("salary.increase_amount", new - old)
            if old:
                raw.setdefault("salary.increase_percent", f"{(new - old) * 100 / old:.2f}%")
        except (TypeError, ValueError):
            pass
    values = {k: eng.display_value(k, v, cur) for k, v in raw.items()}
    # the break-up table: components sent with the request, else the latest history row
    if not components and last and last.components:
        components = last.components
    tables: dict = {}
    if components:
        rows = [["Component", "Per month", "Per year"]]
        total = 0.0
        for code, amt in components.items():
            try:
                m = float(amt or 0)
            except (TypeError, ValueError):
                continue
            total += m
            label = str(comp_names.get(code) or str(code).replace("_", " ").title())
            rows.append([label, eng.fmt_money(m, cur), eng.fmt_money(m * 12, cur)])
        rows.append(["Total", eng.fmt_money(total, cur), eng.fmt_money(total * 12, cur)])
        tables["salary.breakup_table"] = rows
    return values, tables, refused


def _label(field: str) -> str:
    ns, _, key = field.partition(".")
    return eng.ALLOWED.get(ns, {}).get(key) or field


async def _next_number(db, org: str, prefix: str, year: int) -> str:
    """Per org, per year, gap-free: the counter row is bumped inside the same
    transaction that stores the letter."""
    oid = to_uuid(org, "org")
    locked = select(LetterCounter).where(LetterCounter.org_id == oid, LetterCounter.year == year).with_for_update()
    row = (await db.execute(locked)).scalar_one_or_none()
    if not row:
        try:                                   # first letter of the year: two requests may race to create the row
            async with db.begin_nested():
                db.add(LetterCounter(org_id=oid, year=year, last_seq=0))
                await db.flush()
        except IntegrityError:
            pass
        row = (await db.execute(locked)).scalar_one()
    row.last_seq = (row.last_seq or 0) + 1
    return eng.format_number(prefix, year, row.last_seq)


def _render(body: str, values: dict, tables: dict, head: dict, number: str | None, letter_date: date,
            watermark: str | None, title: str) -> tuple[bytes, str, list[str]]:
    vals = {**values, "letter.number": number or ""}
    blocks, missing = eng.build_blocks(body, vals, tables)
    missing = [m for m in missing if not (m == "letter.number" and not number)]
    pdf = eng.render_pdf(blocks, letterhead={**head, "title": title}, number=number,
                         date_text=eng.fmt_date(letter_date), watermark=watermark)
    return pdf, eng.plain_text(blocks), missing


def letter_out(r: IssuedLetter, e: Employee | None = None) -> dict:
    return {"id": str(r.id), "letter_id": str(r.id),
            "employee_id": str(r.employee_id) if r.employee_id else None,
            "employee": full_name(e) if e else r.recipient_name,
            "employee_number": e.employee_number if e else None,
            "recipient_kind": r.recipient_kind or "employee", "recipient_ref": r.recipient_ref,
            "recipient_name": r.recipient_name,
            "template_key": r.template_key, "name": r.template_name, "template_version": r.template_version,
            "number": r.number, "status": r.status,
            "letter_date": r.letter_date.isoformat() if r.letter_date else None,
            "effective_date": r.effective_date.isoformat() if r.effective_date else None,
            "issued_by": r.issued_by, "issued_at": r.issued_at.isoformat() if r.issued_at else None,
            "created_by": r.created_by, "created_at": r.created_at.isoformat() if r.created_at else None,
            "void_reason": r.void_reason, "voided_by": r.voided_by,
            "voided_at": r.voided_at.isoformat() if r.voided_at else None,
            "reissued_from": str(r.reissued_from) if r.reissued_from else None,
            "sha256": r.sha256, "size": len(r.pdf or b""), "visible_to_employee": bool(r.visible_to_employee),
            "pdf_url": f"/letters/{r.id}/pdf", "actions": sorted(eng.allowed_actions(r.status))}


class LetterIn(BaseModel):
    template_key: str
    data: dict | None = None
    effective_date: str | None = None
    issue: bool = True
    allow_blank: bool = False          # issue even if a field in the template is empty
    visible_to_employee: bool = True
    source: str | None = None


async def generate(db, request: Request, org: str, e, body: LetterIn, recipient: dict | None = None) -> IssuedLetter:
    """`e` is an Employee row — or, for a candidate (`recipient` given), a plain
    stand-in carrying the same attribute names and id=None."""
    tpl = await pick_template(db, org, body.template_key, (e.address_country or "CA").upper())
    head = await letterhead(db, org, getattr(e, "entity_id", None))      # D4: the employee's legal entity on the letterhead
    today = await org_today(org)
    eff = parse_date(body.effective_date, "effective_date", required=False)
    values, tables, refused = await build_values(db, org, e, head, body.data, eff, today)
    if refused:
        raise HTTPException(422, {"message": "Some values are not on the list of letter fields.", "errors": refused})
    _, _, missing = _render(tpl.body, values, tables, head, "0", today, None, tpl.name)
    if body.issue and missing and not body.allow_blank:
        raise HTTPException(422, {"message": "The letter was not issued. These details are empty: "
                                             + ", ".join(_label(m) for m in missing) + ".",
                                  "missing_fields": missing})
    number = await _next_number(db, org, head["number_prefix"], today.year) if body.issue else None
    pdf, txt, _ = _render(tpl.body, values, tables, head, number, today, None if body.issue else "DRAFT", tpl.name)
    rec = recipient or {}
    row = IssuedLetter(org_id=e.org_id, employee_id=e.id, template_key=tpl.key, template_name=tpl.name,
                       recipient_kind=rec.get("kind") or "employee", recipient_ref=rec.get("ref"),
                       recipient_name=rec.get("name") or (full_name(e) if e.id else None),
                       recipient_email=rec.get("email"),
                       template_version=tpl.version, body_snapshot=tpl.body, values=values, tables=tables,
                       number=number, status="issued" if body.issue else "draft", letter_date=today,
                       effective_date=eff, pdf=pdf, sha256=eng.sha256_hex(pdf), text_copy=txt,
                       visible_to_employee=body.visible_to_employee, source=(body.source or "")[:40] or None,
                       created_by=actor_of(request), issued_by=actor_of(request) if body.issue else None,
                       issued_at=now() if body.issue else None)
    db.add(row)
    await db.commit()
    row.missing_fields = missing          # not stored — returned to the caller once
    return row


# ── the published contract ────────────────────────────────────────────────────
@router.post("/employees/{employee_id}/letters", status_code=201)
async def create_letter(employee_id: str, body: LetterIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    row = await generate(db, request, org, e, body)
    if row.status == "issued":
        await record_audit(request, "letters.issue", entity_type="letter", entity_id=str(row.id),
                           summary=f"{row.template_name} {row.number} issued to {full_name(e)}",
                           meta={"template": row.template_key, "version": row.template_version, "sha256": row.sha256})
    return {"letter_id": str(row.id), "number": row.number, "pdf_url": f"/letters/{row.id}/pdf",
            "status": row.status, "missing_fields": getattr(row, "missing_fields", [])}


# ── a letter for someone who is NOT an employee yet (candidate / joiner) ──────
# Only these templates make sense before joining; everything else needs the
# employee record (number, confirmation, salary history…).
PRE_JOINING_TEMPLATES = ("offer_letter", "ctc_annexure")


class RecipientIn(BaseModel):
    kind: str = "candidate"            # candidate | joiner
    ref: str                           # the hiring offer id (or onboarding session id) — re-links the letter later
    first_name: str
    last_name: str | None = None
    email: str | None = None
    address: str | None = None
    country: str | None = None         # CA (Canada-only) — picks the template variant
    designation: str | None = None
    department: str | None = None
    location: str | None = None
    grade: str | None = None
    manager_name: str | None = None
    joining_date: str | None = None
    employment_type: str | None = None
    annual_ctc: float | None = None


class RecipientLetterIn(LetterIn):
    recipient: RecipientIn


def _stand_in(org: str, rc: RecipientIn):
    """An Employee-shaped object for the value builder. id=None: it is not a row."""
    from types import SimpleNamespace
    return SimpleNamespace(
        id=None, org_id=to_uuid(org, "org"), first_name=rc.first_name.strip(), last_name=(rc.last_name or "").strip(),
        employee_number=None, job_title=rc.designation, department=rc.department, grade=rc.grade,
        work_location=rc.location, manager_name=rc.manager_name,
        hire_date=parse_date(rc.joining_date, "joining_date", required=False),
        employment_type=rc.employment_type, work_email=None, email=rc.email,
        address_line1=rc.address, address_line2=None, city=None, postal_code=None,
        probation_end_date=None, address_country="CA",
        annual_salary=rc.annual_ctc, hourly_rate=None)


@router.post("/letters/recipient", status_code=201)
async def create_recipient_letter(body: RecipientLetterIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Issue a letter to a person who has no employee record yet. Same template
    allow-list, same numbering and the same immutability as every other letter.
    The letter is re-linked to the employee when the person is activated."""
    require_hr(request)
    org = org_of(request)
    if body.template_key not in PRE_JOINING_TEMPLATES:
        raise HTTPException(422, f"Before joining only these letters can be issued: {', '.join(PRE_JOINING_TEMPLATES)}")
    rc = body.recipient
    if not rc.first_name.strip() or not (rc.ref or "").strip():
        raise HTTPException(422, "The recipient needs a name and a reference")
    # already linked to an employee (activated) → issue against the employee instead
    linked = await _linked_employee(db, org, rc.ref)
    if linked is not None:
        row = await generate(db, request, org, linked, body)
    else:
        row = await generate(db, request, org, _stand_in(org, rc), body,
                             recipient={"kind": rc.kind or "candidate", "ref": rc.ref.strip()[:64],
                                        "name": " ".join(x for x in [rc.first_name, rc.last_name] if x).strip(),
                                        "email": rc.email})
    if row.status == "issued":
        await record_audit(request, "letters.issue", entity_type="letter", entity_id=str(row.id),
                           summary=f"{row.template_name} {row.number} issued to {row.recipient_name} ({row.recipient_kind})",
                           meta={"template": row.template_key, "version": row.template_version, "sha256": row.sha256,
                                 "recipient_ref": row.recipient_ref})
    return {"letter_id": str(row.id), "number": row.number, "pdf_url": f"/letters/{row.id}/pdf",
            "status": row.status, "missing_fields": getattr(row, "missing_fields", []),
            "employee_id": str(row.employee_id) if row.employee_id else None}


async def _linked_employee(db, org: str, ref: str):
    """The employee an earlier recipient letter with this ref was re-linked to, if any."""
    eid = (await db.execute(select(IssuedLetter.employee_id).where(
        IssuedLetter.org_id == to_uuid(org, "org"), IssuedLetter.recipient_ref == (ref or "").strip(),
        IssuedLetter.employee_id.is_not(None)).limit(1))).scalar_one_or_none()
    return await load_employee(db, org, str(eid)) if eid else None


async def relink_recipient_letters(db, org: str, ref: str, employee_id: str) -> int:
    """Attach every letter issued to recipient `ref` to the employee it became.
    Only employee_id is set — number, PDF bytes and hash stay exactly as issued."""
    rows = (await db.execute(select(IssuedLetter).where(
        IssuedLetter.org_id == to_uuid(org, "org"), IssuedLetter.recipient_ref == (ref or "").strip(),
        IssuedLetter.employee_id.is_(None)))).scalars().all()
    for r in rows:
        r.employee_id = to_uuid(employee_id, "employee")
    if rows:
        await db.commit()
    return len(rows)


class RelinkIn(BaseModel):
    ref: str
    employee_id: str


@router.post("/letters/recipient/relink")
async def relink_route(body: RelinkIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    await load_employee(db, org, body.employee_id)          # must exist in this org
    return {"relinked": await relink_recipient_letters(db, org, body.ref, body.employee_id)}


@router.get("/employees/{employee_id}/letters")
async def employee_letters(employee_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    e = await load_employee(db, org, employee_id)
    hr = is_hr(request)
    if not hr and not is_self(request, e):
        raise HTTPException(403, "You can only see your own letters")
    q = select(IssuedLetter).where(IssuedLetter.org_id == e.org_id, IssuedLetter.employee_id == e.id)
    if not hr:
        q = q.where(IssuedLetter.status == "issued", IssuedLetter.visible_to_employee.is_(True))
    rows = (await db.execute(q.order_by(IssuedLetter.created_at.desc()))).scalars().all()
    return [letter_out(r, e) for r in rows]


@router.get("/letters/mine")
async def my_letters(request: Request, db: AsyncSession = Depends(get_db)):
    e = await my_employee(request, db)
    rows = (await db.execute(select(IssuedLetter).where(
        IssuedLetter.org_id == e.org_id, IssuedLetter.employee_id == e.id, IssuedLetter.status == "issued",
        IssuedLetter.visible_to_employee.is_(True)).order_by(IssuedLetter.issued_at.desc()))).scalars().all()
    return [letter_out(r, e) for r in rows]


@router.get("/letters")
async def all_letters(request: Request, status: str | None = None, template_key: str | None = None,
                      db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = to_uuid(org_of(request), "org")
    q = select(IssuedLetter).where(IssuedLetter.org_id == org)
    if status:
        q = q.where(IssuedLetter.status == status)
    if template_key:
        q = q.where(IssuedLetter.template_key == template_key)
    rows = (await db.execute(q.order_by(IssuedLetter.created_at.desc()).limit(2000))).scalars().all()
    emps = {x.id: x for x in (await db.execute(select(Employee).where(Employee.org_id == org))).scalars().all()}
    return [letter_out(r, emps.get(r.employee_id)) for r in rows]


async def _letter(db, org: str, lid: str) -> IssuedLetter:
    r = (await db.execute(select(IssuedLetter).where(IssuedLetter.id == to_uuid(lid, "letter"),
                                                     IssuedLetter.org_id == to_uuid(org, "org")))).scalar_one_or_none()
    if not r:
        raise HTTPException(404, "Letter not found")
    return r


def _guard(status: str, action: str):
    try:
        eng.check_action(status, action)
    except PermissionError as ex:
        raise HTTPException(409, str(ex))


@router.get("/letters/{letter_id}/pdf")
async def letter_pdf(letter_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = org_of(request)
    r = await _letter(db, org, letter_id)
    if not is_hr(request):
        if not r.employee_id:
            raise HTTPException(403, "You can only open your own letters")
        e = await load_employee(db, org, str(r.employee_id))
        if not (is_self(request, e) and r.status == "issued" and r.visible_to_employee):
            raise HTTPException(403, "You can only open your own letters")
    name = (r.number or f"draft-{r.template_key}").replace("/", "-") + ".pdf"
    return Response(content=bytes(r.pdf), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}"', "X-Letter-Sha256": r.sha256})


@router.post("/letters/{letter_id}/issue")
async def issue_draft(letter_id: str, request: Request, allow_blank: bool = False, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    org = org_of(request)
    r = await _letter(db, org, letter_id)
    _guard(r.status, "issue")
    head = await letterhead(db, org, await _entity_of_employee(db, org, r.employee_id))
    today = await org_today(org)
    values = {**(r.values or {}), "letter.date": eng.fmt_date(today)}
    _, _, missing = _render(r.body_snapshot, values, r.tables or {}, head, "0", today, None, r.template_name)
    if missing and not allow_blank:
        raise HTTPException(422, {"message": "The letter was not issued. These details are empty: "
                                             + ", ".join(_label(m) for m in missing) + ".", "missing_fields": missing})
    number = await _next_number(db, org, head["number_prefix"], today.year)
    pdf, txt, _ = _render(r.body_snapshot, values, r.tables or {}, head, number, today, None, r.template_name)
    r.number, r.status, r.letter_date, r.values = number, "issued", today, values
    r.pdf, r.sha256, r.text_copy = pdf, eng.sha256_hex(pdf), txt
    r.issued_by, r.issued_at = actor_of(request), now()
    await db.commit()
    await record_audit(request, "letters.issue", entity_type="letter", entity_id=str(r.id),
                       summary=f"{r.template_name} {r.number} issued", meta={"sha256": r.sha256})
    return letter_out(r)


@router.post("/letters/{letter_id}/reissue", status_code=201)
async def reissue(letter_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """A NEW letter with a NEW number and today's date, from the same template text and
    values. The earlier letter is left exactly as it was — void it separately if needed."""
    require_hr(request)
    org = org_of(request)
    r = await _letter(db, org, letter_id)
    _guard(r.status, "reissue")
    head = await letterhead(db, org, await _entity_of_employee(db, org, r.employee_id))
    today = await org_today(org)
    values = {**(r.values or {}), "letter.date": eng.fmt_date(today)}
    number = await _next_number(db, org, head["number_prefix"], today.year)
    pdf, txt, _ = _render(r.body_snapshot, values, r.tables or {}, head, number, today, None, r.template_name)
    new = IssuedLetter(org_id=r.org_id, employee_id=r.employee_id, template_key=r.template_key,
                       recipient_kind=r.recipient_kind, recipient_ref=r.recipient_ref,
                       recipient_name=r.recipient_name, recipient_email=r.recipient_email,
                       template_name=r.template_name, template_version=r.template_version,
                       body_snapshot=r.body_snapshot, values=values, tables=r.tables or {}, number=number,
                       status="issued", letter_date=today, effective_date=r.effective_date, pdf=pdf,
                       sha256=eng.sha256_hex(pdf), text_copy=txt, visible_to_employee=r.visible_to_employee,
                       source="reissue", reissued_from=r.id, created_by=actor_of(request),
                       issued_by=actor_of(request), issued_at=now())
    db.add(new)
    await db.commit()
    await record_audit(request, "letters.reissue", entity_type="letter", entity_id=str(new.id),
                       summary=f"{new.template_name} issued again as {new.number} (was {r.number})")
    return letter_out(new)


class VoidIn(BaseModel):
    reason: str


@router.post("/letters/{letter_id}/void")
async def void_letter(letter_id: str, body: VoidIn, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    r = await _letter(db, org_of(request), letter_id)
    _guard(r.status, "void")
    if not body.reason.strip():
        raise HTTPException(400, "Give a reason for voiding the letter")
    r.status, r.void_reason, r.voided_by, r.voided_at = "void", body.reason.strip()[:400], actor_of(request), now()
    await db.commit()
    await record_audit(request, "letters.void", entity_type="letter", entity_id=str(r.id),
                       summary=f"{r.template_name} {r.number} voided", meta={"reason": r.void_reason})
    return letter_out(r)


@router.delete("/letters/{letter_id}")
async def discard_draft(letter_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_hr(request)
    r = await _letter(db, org_of(request), letter_id)
    _guard(r.status, "discard")
    await db.delete(r)
    await db.commit()
    return {"ok": True}


class PreviewIn(BaseModel):
    employee_id: str
    template_key: str | None = None
    body: str | None = None            # unsaved template text to try out
    data: dict | None = None
    effective_date: str | None = None


@router.post("/letters/preview")
async def preview(body: PreviewIn, request: Request, db: AsyncSession = Depends(get_db)):
    """A PDF that is NOT stored and has no number. Used by the template editor and the
    'Preview' button before issuing."""
    require_hr(request)
    org = org_of(request)
    e = await load_employee(db, org, body.employee_id)
    if body.body is not None:
        _check_body(body.body)
        text_body, name = body.body, "Preview"
    else:
        tpl = await pick_template(db, org, body.template_key or "", (e.address_country or "CA").upper())
        text_body, name = tpl.body, tpl.name
    head = await letterhead(db, org, getattr(e, "entity_id", None))
    today = await org_today(org)
    values, tables, refused = await build_values(db, org, e, head, body.data,
                                                 parse_date(body.effective_date, "effective_date", required=False),
                                                 today)
    if refused:
        raise HTTPException(422, {"message": "Some values are not on the list of letter fields.", "errors": refused})
    pdf, _, missing = _render(text_body, values, tables, head, None, today, "PREVIEW", name)
    return Response(content=pdf, media_type="application/pdf",
                    headers={"X-Missing-Fields": ",".join(missing)[:900]})


class PreviewTextIn(BaseModel):
    employee_id: str | None = None        # omitted → the first active employee of the org (a sample)
    template_key: str | None = None
    body: str | None = None
    data: dict | None = None
    effective_date: str | None = None


@router.post("/letters/preview-text")
async def preview_text(body: PreviewTextIn, request: Request, db: AsyncSession = Depends(get_db)):
    """D1 live preview for the editor: the filled letter as JSON blocks + plain text (no PDF, nothing stored)."""
    require_hr(request)
    org = org_of(request)
    if body.employee_id:
        e = await load_employee(db, org, body.employee_id)
    else:
        e = (await db.execute(select(Employee).where(Employee.org_id == to_uuid(org, "org"), Employee.is_active.is_(True))
                              .order_by(Employee.employee_number).limit(1))).scalars().first()
        if not e:
            raise HTTPException(404, "Add an employee first — the preview fills the letter with a real record")
    if body.body is not None:
        errs = eng.validate_template(body.body)
        text_body, name = body.body, "Preview"
    else:
        errs = []
        tpl = await pick_template(db, org, body.template_key or "", (e.address_country or "CA").upper())
        text_body, name = tpl.body, tpl.name
    if errs:
        return {"ok": False, "errors": errs, "blocks": [], "text": "", "missing": [], "employee": full_name(e)}
    head = await letterhead(db, org, getattr(e, "entity_id", None))
    today = await org_today(org)
    values, tables, refused = await build_values(db, org, e, head, body.data,
                                                 parse_date(body.effective_date, "effective_date", required=False), today)
    vals = {**values, "letter.number": "LTR/…/…"}
    blocks, missing = eng.build_blocks(text_body, vals, tables)
    return {"ok": True, "errors": [], "blocks": eng.blocks_json(blocks), "text": eng.plain_text(blocks),
            "missing": [{"field": m, "label": _label(m)} for m in missing], "refused": refused,
            "employee": full_name(e), "employee_id": str(e.id), "name": name, "date": eng.fmt_date(today),
            "letterhead": {k: head.get(k) or "" for k in ("company_name", "address", "signatory_name", "signatory_designation", "footer_text")}}
