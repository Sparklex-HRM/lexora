"""Lexora HRM — employee documents stored IN the database.

There is no object store to rely on locally, so the file bytes live in a
`bytea` column of `employee_documents`. Used by the employee service (documents
on a profile) and the onboarding service (documents a new hire uploads before
the employee record exists — those rows carry `onboarding_session_id` and get
their `employee_id` when the joiner is approved).

Safety rules, all enforced by `check_upload` before anything is stored:
  * type is decided by the file's first bytes ("magic bytes"), never by the
    name or the browser-supplied content type
  * allow-list: JPG, PNG, PDF; PDFs with JavaScript / Launch / embedded files are refused
  * size cap (default 5 MB, env LEXORA_DOC_MAX_MB)
Rows are never hard-deleted by the API: `deleted_at` hides them.
"""
import hashlib
import os
import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column

from shared.utils.db import Base

MAX_BYTES = int(float(os.getenv("LEXORA_DOC_MAX_MB", "5")) * 1024 * 1024)

# (content type, extension, leading bytes). Signatures are the published ones:
# JPEG starts FF D8 FF; PNG starts 89 'PNG' CR LF 1A LF; PDF starts '%PDF-'.
_SIGNATURES = (
    ("image/jpeg", "jpg", b"\xff\xd8\xff"),
    ("image/png", "png", b"\x89PNG\r\n\x1a\n"),
    ("application/pdf", "pdf", b"%PDF-"),
)
ALLOWED_LABEL = "JPG, PNG or PDF"

DOC_TYPES = {
    "CA": [("sin_proof", "SIN document"), ("work_permit", "Work / study permit"), ("void_cheque", "Void cheque"),
           ("photo_id", "Photo ID"), ("td1_federal", "TD1 (federal)"), ("td1_provincial", "TD1 (provincial)"),
           ("credential", "Credential / licence"), ("education", "Education certificate"),
           ("experience", "Previous employer letter"), ("other", "Other")],
}


class DocError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc)


class StoredDocument(Base):
    __tablename__ = "employee_documents"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    onboarding_session_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), index=True)
    doc_type: Mapped[str] = mapped_column(String(40), default="other")
    file_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))          # sniffed, not client-supplied
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    # Deferred: listing documents must never drag the file bytes along.
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary, nullable=False))
    uploaded_by: Mapped[str | None] = mapped_column(String(64))      # user id, or 'new-hire'
    uploaded_by_name: Mapped[str | None] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[str | None] = mapped_column(String(200))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_by: Mapped[str | None] = mapped_column(String(200))


def sniff(data: bytes) -> tuple[str, str] | None:
    """(content_type, extension) from the leading bytes, or None if not allowed."""
    head = bytes(data[:16])
    for ctype, ext, sig in _SIGNATURES:
        if head.startswith(sig):
            return ctype, ext
    return None


# ── images for logos / letter pictures: PNG, JPEG, WEBP by magic bytes only (never SVG) ──
IMAGE_LABEL = "PNG, JPG or WEBP"
_IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def sniff_image(data: bytes) -> str | None:
    """'image/png' | 'image/jpeg' | 'image/webp' from the leading bytes, else None (SVG, HTML, … → None)."""
    head = bytes(data[:16])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_ext(ctype: str) -> str:
    return _IMAGE_EXT.get(ctype, "bin")


def check_image(data: bytes, declared: str | None = None) -> str:
    """The sniffed image type, or DocError(422). If the client declared a type it must match the bytes
    (image/jpg is accepted as image/jpeg)."""
    kind = sniff_image(data or b"")
    if not kind:
        raise DocError(f"The image must be a {IMAGE_LABEL} file (SVG and other formats are not accepted)")
    if declared:
        d = declared.strip().lower().split(";")[0]
        d = "image/jpeg" if d in ("image/jpg", "image/pjpeg") else d
        if d != kind:
            raise DocError(f"The file is not the image type it claims to be. Upload a real {IMAGE_LABEL} file.")
    return kind


SANDBOX_CSP = "default-src 'none'; sandbox"


def serve_headers(content_type: str, file_name: str | None = None, inline: bool = True) -> dict:
    """Headers for any stored file we send back: never sniffed into HTML, never able to run script."""
    ext = image_ext(content_type) if content_type in _IMAGE_EXT else ("pdf" if content_type == "application/pdf" else "bin")
    name = safe_name(file_name, ext) if file_name else f"file.{ext}"
    return {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": SANDBOX_CSP,
            "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{name}"'}


# ── PDF active content: refused at upload (applies to every docstore caller) ──
_PDF_ACTIVE = {b"javascript", b"js", b"launch", b"embeddedfile", b"embeddedfiles", b"richmedia"}
_PDF_NAME = re.compile(rb"/([^\s/<>\[\]()%{}]+)")
_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
_PDF_INFLATE_CAP = 64 * 1024 * 1024


def _pdf_names(buf: bytes):
    for m in _PDF_NAME.finditer(buf):
        raw = m.group(1)
        if b"#" in raw:   # PDF name escapes: /J#61vaScript == /JavaScript
            raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda h: bytes([int(h.group(1), 16)]), raw)
        yield raw.lower()


def pdf_active_content(data: bytes) -> str | None:
    """The first active-content name found (JavaScript, JS, Launch, EmbeddedFile, RichMedia) in the
    PDF body or in any Flate-compressed stream (object streams hide dictionaries), else None."""
    import zlib
    for n in _pdf_names(data):
        if n in _PDF_ACTIVE:
            return n.decode("latin-1")
    budget = _PDF_INFLATE_CAP
    for m in _PDF_STREAM.finditer(data):
        if budget <= 0:
            break
        try:
            d = zlib.decompressobj()
            out = d.decompress(m.group(1), budget)
        except zlib.error:
            continue
        budget -= len(out)
        for n in _pdf_names(out):
            if n in _PDF_ACTIVE:
                return n.decode("latin-1")
    return None


def safe_name(name: str | None, ext: str) -> str:
    """A display-safe file name whose extension matches the REAL type."""
    base = os.path.basename((name or "").replace("\\", "/")).strip() or "document"
    base = re.sub(r"[^A-Za-z0-9 ._()\-]", "_", base)
    stem = base.rsplit(".", 1)[0][:120] if "." in base else base[:120]
    return f"{stem or 'document'}.{ext}"


def check_upload(file_name: str | None, data: bytes) -> dict:
    """Validate an upload. Returns the columns to store; raises DocError."""
    if not data:
        raise DocError("The file is empty")
    if len(data) > MAX_BYTES:
        raise DocError(f"The file is too large. The limit is {MAX_BYTES // (1024 * 1024)} MB.", 413)
    kind = sniff(data)
    if not kind:
        raise DocError(f"Only {ALLOWED_LABEL} files can be uploaded")
    ctype, ext = kind
    if ctype == "application/pdf" and pdf_active_content(bytes(data)):
        raise DocError("This PDF contains scripts, launch actions or embedded files and cannot be uploaded. "
                       "Print it to a plain PDF and upload that.")
    return {"file_name": safe_name(file_name, ext), "content_type": ctype, "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "data": bytes(data)}


def doc_out(d: StoredDocument) -> dict:
    return {"id": str(d.id), "doc_type": d.doc_type, "file_name": d.file_name,
            "content_type": d.content_type, "size_bytes": d.size_bytes,
            "uploaded_by_name": d.uploaded_by_name,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
            "verified": bool(d.verified), "verified_by": d.verified_by,
            "verified_at": d.verified_at.isoformat() if d.verified_at else None}
