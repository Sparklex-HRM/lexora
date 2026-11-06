"""Auth service — refusals whose JSON carries more than `detail`.

FastAPI's HTTPException answers ``{"detail": ...}`` only. Two kinds of answer
need a second top-level field (WASA hardening 2026-10-21):

* CAPTCHA refusals: ``{"detail": "<plain sentence>", "code": "captcha_required" | ...}``
  so the web page and our scripts can tell "solve again" from a wrong password;
* provider / unexpected failures: ``{"detail": "<fixed sentence>", "ref": "<8 hex>"}``
  — the real error text is logged next to the reference, never sent to the caller.

``PublicError`` is an HTTPException, so an app without ``install()`` still
answers ``{"detail": "<sentence>"}`` (only the extra fields are lost).
"""
from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("auth.errors")


class PublicError(HTTPException):
    def __init__(self, status_code: int, detail: str, *, headers: dict | None = None, **extra):
        super().__init__(status_code, detail, headers=headers)
        self.extra = extra


def error_ref() -> str:
    try:
        from shared.ops_runtime import error_ref as _ref
        return _ref()
    except Exception:  # noqa: BLE001
        return uuid.uuid4().hex[:8].upper()


def failure(status_code: int, sentence: str, exc: BaseException, where: str) -> PublicError:
    """Log `exc` (with traceback) under a short reference and return the
    refusal to raise: the fixed `sentence` + that reference."""
    ref = error_ref()
    log.error("%s failed ref=%s: %s: %s", where, ref, type(exc).__name__, exc, exc_info=exc)
    return PublicError(status_code, sentence, ref=ref)


async def _render(request: Request, exc: PublicError):
    return JSONResponse({"detail": exc.detail, **exc.extra}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


def install(app) -> None:
    app.add_exception_handler(PublicError, _render)


# ── WASA follow-up 2026-10-24: never str(e) of an exception we did not raise ourselves ─────────────────────
_OUR_CODE = ("/services/auth/", "/shared/", "/svc/")   # matched on a "/"-normalised file name


def _raised_by_us(exc: BaseException) -> bool:
    """True when the exception was RAISED in our own code (its innermost traceback frame is in this service or
    backend/shared) — its message is then one of our sentences. A library's text (SQL, parser, network) is not."""
    tb = exc.__traceback__
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is None:
        return True                       # constructed, never raised by a library
    fn = last.tb_frame.f_code.co_filename.replace("\\", "/")
    return any(m in fn for m in _OUR_CODE) and "site-packages" not in fn


def safe_detail(exc: BaseException, fallback: str = "That request could not be completed.") -> str:
    msg = getattr(exc, "message", None) or str(exc)
    if not msg or not _raised_by_us(exc) or len(msg) > 300:
        return fallback
    return msg


def refusal(status_code: int, exc: BaseException, code: str, *, fallback: str = "That request could not be completed.",
            where: str = "auth") -> PublicError:
    """A refusal built from one of OUR exceptions: its sentence + `code`; anything else -> the fixed `fallback`,
    the real text logged under a reference (never sent)."""
    detail = safe_detail(exc, "")
    if detail:
        return PublicError(status_code, detail, code=code)
    ref = error_ref()
    log.warning("%s refused ref=%s: %s", where, ref, type(exc).__name__)
    return PublicError(status_code, fallback, code=code, ref=ref)
