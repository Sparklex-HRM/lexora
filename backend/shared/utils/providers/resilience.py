"""Resilient, audited outbound calls for the provider adapters (calendars, BGV vendors, …).

    res = await call(org_id, "calendar_google", "create_event", "POST", url,
                     json=payload, headers=hdrs, idempotency_key="…", retry_unsafe=False)

What every call gets:
  * a timeout (never hangs a business action);
  * retries with exponential backoff + jitter on connection errors, 429 and 5xx —
    BUT a non-idempotent POST is retried only when (a) the provider honours an
    idempotency key we sent, or (b) the failure happened before the request left
    (connect error). A timed-out create without an idempotency key is reported as
    ``unknown`` so the caller reconciles instead of creating twice;
  * a row in ``int_provider_calls``: method, URL PATH (no query string), status,
    attempts, duration, idempotency key and the request / response bodies with
    every PII field MASKED (``mask_pii``). Headers are never stored (they carry
    keys). The row is written best-effort — an audit failure never fails the call.

Tests swap ``TRANSPORT`` for an ``httpx.MockTransport`` and set ``BACKOFF_BASE = 0``.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("lexora.providers.calls")

TRANSPORT: httpx.AsyncBaseTransport | None = None     # tests inject httpx.MockTransport here
BACKOFF_BASE = 0.5                                     # seconds; 0.5, 1, 2 … (+ jitter)
DEFAULT_RETRIES = 3
DEFAULT_TIMEOUT = 15.0
RETRY_STATUS = {429, 500, 502, 503, 504}

# Keys whose VALUES are personal data. Matched case-insensitively against the key name.
_PII_KEYS = re.compile(
    r"^(account_number|bank_account_number|sin|social_insurance_number|uid|identifier|contact|phone|mobile|email|"
    r"name|full_name|displayname|display_name|careof|care_of|house|street|locality|landmark|"
    r"vtc|district|subdistrict|postoffice|pin|postal_code|postalcode|dateofbirth|date_of_birth|dob|birthyear|"
    r"birth_year|gender|photo|xml|fileurl|file_url|downloadurl|download_url|url|signurl|to|utr|"
    r"maskednumber|masked_number|beneficiary_name|reference_id)$", re.I)
_BLOB_KEYS = re.compile(r"^(document|file|file_data|pdf|content|data_base64)$", re.I)


def _mask_value(v) -> str:
    s = str(v)
    if not s:
        return ""
    if len(s) <= 4:
        return "•" * len(s)
    return "•" * (len(s) - 4) + s[-4:] if len(s) <= 40 else f"•••({len(s)} chars)"


def mask_pii(obj, _depth: int = 0):
    """A deep copy of ``obj`` with every PII value masked and blobs replaced by their size."""
    if _depth > 12:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if _BLOB_KEYS.match(ks) and isinstance(v, (str, bytes)):
                out[k] = f"<{len(v)} bytes>"
            elif _PII_KEYS.match(ks) and not isinstance(v, (dict, list)):
                out[k] = _mask_value(v) if v not in (None, "") else v
            else:
                out[k] = mask_pii(v, _depth + 1)
        return out
    if isinstance(obj, list):
        return [mask_pii(v, _depth + 1) for v in obj[:200]]
    return obj


class CallResult:
    __slots__ = ("ok", "status", "json", "content", "error", "attempts", "outcome", "headers")

    def __init__(self, ok: bool, status: int | None, json=None, content: bytes = b"", error: str | None = None,
                 attempts: int = 1, outcome: str = "", headers: dict | None = None):
        self.ok, self.status, self.json, self.content, self.error = ok, status, json, content, error
        self.attempts, self.outcome, self.headers = attempts, outcome, headers or {}

    def error_text(self, n: int = 200) -> str:
        """Provider error message for the screen ({error:{code,detail}} or {error:{description}} or {message})."""
        if self.error:
            return self.error[:n]
        j = self.json if isinstance(self.json, dict) else {}
        e = j.get("error") if isinstance(j.get("error"), dict) else {}
        msg = e.get("detail") or e.get("description") or e.get("code") or j.get("message") or ""
        return (f"HTTP {self.status}: {msg}" if msg else f"HTTP {self.status}")[:n]


def _backoff(attempt: int) -> float:
    if BACKOFF_BASE <= 0:
        return 0.0
    return BACKOFF_BASE * (2 ** (attempt - 1)) + random.uniform(0, BACKOFF_BASE / 2)


async def call(org_id, provider: str, operation: str, method: str, url: str, *, json=None, data=None,
               files=None, headers: dict | None = None, params: dict | None = None, auth=None,
               idempotency_key: str | None = None, retry_unsafe: bool | None = None,
               retries: int = DEFAULT_RETRIES, timeout: float = DEFAULT_TIMEOUT, audit: bool = True,
               audit_request=None) -> CallResult:
    """One provider call with retries + an audit row. Never raises for HTTP / network problems."""
    method = method.upper()
    safe = method in ("GET", "HEAD", "OPTIONS", "DELETE") or bool(idempotency_key)
    if retry_unsafe is not None:
        safe = retry_unsafe
    started = time.monotonic()
    attempt, res = 0, None
    while True:
        attempt += 1
        sent = False
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=TRANSPORT) as c:
                sent = True
                r = await c.request(method, url, json=json, data=data, files=files, headers=headers,
                                    params=params, auth=auth)
            body = None
            try:
                body = r.json()
            except ValueError:
                body = None
            res = CallResult(200 <= r.status_code < 300, r.status_code, body, r.content, None, attempt,
                             "ok" if 200 <= r.status_code < 300 else "http_error", dict(r.headers))
            retry = r.status_code in RETRY_STATUS and (safe or r.status_code == 429)
        except httpx.ConnectError as ex:
            res = CallResult(False, None, error=f"Could not connect ({type(ex).__name__})", attempts=attempt,
                             outcome="connect_error")
            retry = True                                        # the request never left
        except httpx.TimeoutException:
            res = CallResult(False, None, error=f"Timed out after {timeout:g}s", attempts=attempt,
                             outcome="timeout" if safe else "unknown")
            retry = safe
        except httpx.HTTPError as ex:
            res = CallResult(False, None, error=f"Network error ({type(ex).__name__})", attempts=attempt,
                             outcome="network_error" if safe or not sent else "unknown")
            retry = safe
        if not retry or attempt > retries:
            break
        await asyncio.sleep(_backoff(attempt))
    if audit:
        await _audit(org_id, provider, operation, method, url, res, idempotency_key,
                     audit_request if audit_request is not None else (json if json is not None else data),
                     int((time.monotonic() - started) * 1000))
    return res


async def _audit(org_id, provider, operation, method, url, res: CallResult, idem, req_body, ms: int) -> None:
    try:
        from shared.utils import platform_models as pm
        from shared.utils.providers.models_s5 import ProviderCall
        resp_body = res.json if res.json is not None else (f"<{len(res.content)} bytes>" if res.content else None)
        row = ProviderCall(org_id=str(org_id or ""), provider=provider, operation=operation, method=method,
                           path=urlsplit(url).path[:300], host=(urlsplit(url).hostname or "")[:120],
                           http_status=res.status, ok=bool(res.ok), outcome=res.outcome[:20], attempts=res.attempts,
                           duration_ms=ms, idempotency_key=(idem or None),
                           request_masked=mask_pii(req_body) if isinstance(req_body, (dict, list)) else None,
                           response_masked=mask_pii(resp_body) if isinstance(resp_body, (dict, list)) else
                           ({"body": resp_body} if isinstance(resp_body, str) else None),
                           error=(res.error or (None if res.ok else res.error_text(300))))
        async with pm.session() as s:
            s.add(row)
            await s.commit()
    except Exception as e:                                       # noqa: BLE001
        log.warning("provider call audit failed (%s/%s): %s", provider, operation, e)
