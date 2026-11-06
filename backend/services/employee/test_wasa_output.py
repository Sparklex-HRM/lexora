"""WASA2 #3 / #8 / unsafe links — employee service: public careers logo headers + no SVG,
interview place validation, letter images sniffed, candidate PDFs without scripts, offer
e-mail escaping, broad-except answers without exception text.

Run:  cd backend/services/employee && PYTHONPATH=../.. python -m pytest -q test_wasa_output.py
"""
import asyncio
import base64
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

_HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(os.path.dirname(_HERE)), _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import hiring_careers as HC  # noqa: E402
import hiring_integrations as HI  # noqa: E402
import hiring_offers as HO  # noqa: E402
import hiring_routers as HR  # noqa: E402
import letters_routers as LR  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
XSS = "<img src=x onerror=alert(1)>"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Rows:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class FakeDB:
    def __init__(self, row):
        self.row = row

    async def execute(self, *_a, **_k):
        return _Rows(self.row)


@pytest.fixture
def public_logo(monkeypatch):
    async def _org(db, code):
        return {"id": "o1", "code": code}, None
    monkeypatch.setattr(HC, "_org", _org)
    monkeypatch.setattr(HC, "_limit", lambda *a, **k: None)
    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="1.2.3.4"))
    return lambda row: run(HC.logo("ACME", req, FakeDB(row)))


# ── public careers logo ─────────────────────────────────────────────────────
def test_public_logo_has_safety_headers(public_logo):
    r = public_logo(("image/png", PNG))
    assert r.media_type == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert r.headers["content-disposition"] == 'inline; filename="logo.png"'


@pytest.mark.parametrize("row", [("image/svg+xml", SVG), ("image/png", SVG), ("image/png", b"<html>x</html>")])
def test_public_logo_never_serves_svg_or_non_image_bytes(public_logo, row):
    with pytest.raises(HTTPException) as e:
        public_logo(row)
    assert e.value.status_code == 404


# ── interview place / meeting link ──────────────────────────────────────────
@pytest.mark.parametrize("bad", ["javascript:alert(1)", " JAVASCRIPT:alert(1)", "data:text/html,<script>x</script>",
                                 "vbscript:msgbox(1)", "java\tscript:alert(1)"])
def test_interview_place_refuses_script_links(bad):
    with pytest.raises(HTTPException) as e:
        HR._clean_place(bad)
    assert e.value.status_code == 422


@pytest.mark.parametrize("ok,out", [("https://meet.example/abc", "https://meet.example/abc"),
                                    ("  Room 4: 2nd floor ", "Room 4: 2nd floor"), ("", None), (None, None)])
def test_interview_place_keeps_links_and_rooms(ok, out):
    assert HR._clean_place(ok) == out


# ── letter images ───────────────────────────────────────────────────────────
def test_letter_image_type_comes_from_the_bytes():
    raw, mime = LR._decode_image("data:image/png;base64," + base64.b64encode(PNG).decode(), "logo")
    assert raw == PNG and mime == "image/png"
    for url in ("data:image/png;base64," + base64.b64encode(SVG).decode(),          # SVG claiming PNG
                "data:image/jpeg;base64," + base64.b64encode(PNG).decode(),         # PNG claiming JPEG
                "data:image/svg+xml;base64," + base64.b64encode(SVG).decode()):
        with pytest.raises(HTTPException):
            LR._decode_image(url, "logo")


# ── candidate documents ─────────────────────────────────────────────────────
def test_candidate_pdf_with_javascript_is_refused():
    pdf = b"%PDF-1.7\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >> endobj\n%%EOF"
    with pytest.raises(HTTPException) as e:
        run(HR.store_document(None, "00000000-0000-0000-0000-000000000001", None, None, "resume", "cv.pdf", pdf, 5, None))
    assert e.value.status_code == 422 and "scripts" in e.value.detail


# ── offer e-mail ────────────────────────────────────────────────────────────
def test_offer_email_escapes_designation_and_names(monkeypatch):
    sent = {}

    async def fake_send(to, subject, body, html=None):
        sent.update(to=to, html=html)
        return True
    monkeypatch.setattr(HO, "send_email", fake_send)
    o = SimpleNamespace(designation=f"Nurse {XSS}", token_expires_at=None)
    c = SimpleNamespace(email="c@x.example", first_name=f"Asha {XSS}")

    async def go():
        await HO._mail_offer(o, c, f"Acme {XSS}", "https://hr.example/offer/t")
        await asyncio.sleep(0)
    run(go())
    h = sent["html"]
    assert "<img" not in h and "&lt;img src=x onerror=alert(1)&gt;" in h
    assert "<b>Nurse &lt;img" in h                                         # bold kept, value escaped


# ── broad except → no exception text in the answer ──────────────────────────
def test_offer_letter_unexpected_error_is_masked(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("psycopg: password authentication failed for user lexora at 10.0.0.5")
    monkeypatch.setattr(HI, "_issue_letter", boom)
    out = run(HI.issue_offer_letter("o1", "e1", {}, {}))
    assert out["ok"] is False and "psycopg" not in out["reason"] and "ref" in out["reason"]
