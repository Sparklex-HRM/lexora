"""WASA2 #3 — org logo upload: PNG / JPEG / WEBP by magic bytes only; SVG and HTML-as-PNG refused;
the served logo carries nosniff + CSP sandbox + inline disposition. Company website: no javascript:.

Run:  cd backend/services/notification && python -m pytest -q test_wasa_logo.py
"""
import asyncio
import base64
import os
import sys

import pytest
from fastapi import HTTPException

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import settings_routers as sr  # noqa: E402

ORG = "11111111-1111-1111-1111-111111111111"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
HTML = b"<html><script>alert(1)</script></html>"


class Req:
    def __init__(self, role="org_admin"):
        self.headers = {"x-org-id": ORG, "x-role": role, "x-user-email": "a@x.example", "x-user-id": "u1"}


class _Res:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class FakeDB:
    def __init__(self, row=None):
        self.row, self.added, self.commits = row, [], 0

    async def execute(self, _stmt):
        return _Res(self.row)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _put(data: bytes, ctype: str, role="org_admin", db=None):
    body = sr.LogoIn(content_type=ctype, data_base64=base64.b64encode(data).decode())
    return asyncio.run(sr.put_logo(body, Req(role), db or FakeDB()))


def test_svg_logo_refused():
    with pytest.raises(HTTPException) as e:
        _put(SVG, "image/svg+xml")
    assert e.value.status_code == 422 and "SVG" in e.value.detail


def test_html_declared_as_png_refused():
    db = FakeDB()
    with pytest.raises(HTTPException) as e:
        _put(HTML, "image/png", db=db)
    assert e.value.status_code == 422 and db.commits == 0


def test_real_png_declared_as_jpeg_refused():
    with pytest.raises(HTTPException) as e:
        _put(PNG, "image/jpeg")
    assert e.value.status_code == 422


def test_png_logo_accepted_for_org_admin_only():
    db = FakeDB()
    assert _put(PNG, "image/png", db=db)["content_type"] == "image/png" and db.commits == 1
    with pytest.raises(HTTPException) as e:
        _put(PNG, "image/png", role="hr_admin")
    assert e.value.status_code == 403                                     # unchanged rule


def test_get_logo_headers():
    class Row:
        content_type, data = "image/png", PNG
    r = asyncio.run(sr.get_logo(Req("employee"), FakeDB(Row())))
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in r.headers["content-security-policy"]
    assert r.headers["content-disposition"] == 'inline; filename="logo.png"'


@pytest.mark.parametrize("site", ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,x", "vbscript:x",
                                  "java\tscript:alert(1)"])
def test_company_website_rejects_script_urls(site):
    with pytest.raises(ValueError):
        sr.clean("company", {"website": site})


@pytest.mark.parametrize("site", ["https://maple.example", "www.maple.example", ""])
def test_company_website_keeps_normal_values(site):
    assert sr.clean("company", {"website": site})["website"] == site
