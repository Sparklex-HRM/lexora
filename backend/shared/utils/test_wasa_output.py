"""WASA2 #8 / #3 — e-mail HTML escaping, image sniffing, PDF active-content refusal, unsafe links.

Run:  cd backend && python -m pytest -q shared/utils/test_wasa_output.py
"""
import zlib

import pytest

from shared.utils import docstore as ds
from shared.utils.email import Safe, brand_html, dangerous_url, plain, safe_link, strong

XSS_IMG = "<img src=x onerror=alert(1)>"
XSS_A = '<a href="javascript:alert(1)">click</a>'
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<html><script>alert(1)</script></html>"


# ── brand_html ────────────────────────────────────────────────────────────────
def test_brand_html_escapes_title_lines_and_cta_label():
    out = brand_html(f"Welcome {XSS_IMG}", [f"Hello {XSS_IMG},", f"Note: {XSS_A}", ("Org ", XSS_IMG)],
                     cta_label=f"Open {XSS_IMG}", cta_url="https://hr.example.ca/x?a=1&b=2")
    assert "<img" not in out and "<a href=\"javascript" not in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out
    assert "&lt;a href=&quot;javascript:alert(1)&quot;&gt;" in out
    assert 'href="https://hr.example.ca/x?a=1&amp;b=2"' in out          # attribute-escaped


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,<script>alert(1)</script>",
                                 "vbscript:x", "/relative/path", "https:///nohost", "", None,
                                 "https://ok.example\" onmouseover=\"x", "java\nscript:alert(1)"])
def test_cta_button_only_for_real_http_links(url):
    out = brand_html("T", ["x"], cta_label="Go", cta_url=url)
    if url and url.startswith("https://ok.example"):
        # a real host with a quote in it: kept as a button, but the quote cannot break the attribute
        assert 'onmouseover="x' not in out and "&quot;" in out
    else:
        assert "<a " not in out                                            # no button at all


def test_safe_and_strong_keep_intended_formatting_only():
    out = brand_html("Leave approved", [("Your request has been ", strong("approved <script>"), ".")])
    assert "<b>approved &lt;script&gt;</b>" in out                        # bold kept, value escaped
    assert brand_html("t", [Safe("<b>trusted</b>")]).count("<b>trusted</b>") == 1
    assert plain(("Your request has been ", strong("approved"), ".")) == "Your request has been approved."
    assert plain(Safe("<b>x &amp; y</b>")) == "x & y"


def test_safe_link_and_dangerous_url():
    assert safe_link("https://a.example/b") == "https://a.example/b"
    assert safe_link("http://localhost:5173/x") == "http://localhost:5173/x"
    for bad in ("javascript:alert(1)", "data:,x", "ftp://a.example", "//a.example", "https://a.example/\r\nx"):
        assert safe_link(bad) is None
    for bad in ("javascript:alert(1)", " JavaScript:alert(1)", "java\tscript:alert(1)", "\x01javascript:x",
                "data:text/html;base64,PHN2Zz4=", "VBScript:msgbox", "file:///etc/passwd", "blob:https://x"):
        assert dangerous_url(bad), bad
    for ok in ("", None, "https://meet.example/abc", "Room 4: second floor", "www.example.ca", "mailto:a@b.c",
               "tel:+911234", "Conference room B"):
        assert not dangerous_url(ok), ok


# ── image sniffing ────────────────────────────────────────────────────────────
def test_sniff_image_by_magic_bytes():
    assert ds.sniff_image(PNG) == "image/png"
    assert ds.sniff_image(JPEG) == "image/jpeg"
    assert ds.sniff_image(WEBP) == "image/webp"
    assert ds.sniff_image(SVG) is None and ds.sniff_image(HTML) is None and ds.sniff_image(b"") is None


def test_check_image_declared_type_must_match_bytes():
    assert ds.check_image(PNG, "image/png") == "image/png"
    assert ds.check_image(JPEG, "image/jpg") == "image/jpeg"
    with pytest.raises(ds.DocError):
        ds.check_image(SVG, "image/svg+xml")                               # SVG refused
    with pytest.raises(ds.DocError):
        ds.check_image(HTML, "image/png")                                  # HTML declared as PNG
    with pytest.raises(ds.DocError):
        ds.check_image(JPEG, "image/png")                                  # real image, wrong claim


def test_serve_headers_sandbox_and_nosniff():
    h = ds.serve_headers("image/png", 'evil".html')
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in h["Content-Security-Policy"] and "default-src 'none'" in h["Content-Security-Policy"]
    assert h["Content-Disposition"].startswith("inline;") and h["Content-Disposition"].endswith('.png"')


# ── PDF active content ───────────────────────────────────────────────────────
def _pdf(body: bytes) -> bytes:
    return b"%PDF-1.7\n" + body + b"\n%%EOF\n"


def test_plain_pdf_is_accepted():
    pdf = _pdf(b"1 0 obj << /Type /Catalog /Pages 2 0 R /OpenAction [3 0 R /Fit] >> endobj")
    assert ds.pdf_active_content(pdf) is None                              # OpenAction to a page is fine
    assert ds.check_upload("a.pdf", pdf)["content_type"] == "application/pdf"


@pytest.mark.parametrize("body", [
    b"1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >> endobj",
    b"1 0 obj << /AA << /O << /S /JavaScript /JS 5 0 R >> >> >> endobj",
    b"1 0 obj << /S /Launch /F (cmd.exe) >> endobj",
    b"1 0 obj << /Names << /EmbeddedFiles 4 0 R >> >> endobj",
    b"1 0 obj << /Type /EmbeddedFile >> endobj",
    b"1 0 obj << /S /J#61vaScript /J#53 (x) >> endobj",                    # hex-escaped names
])
def test_pdf_with_active_content_is_refused(body):
    pdf = _pdf(body)
    assert ds.pdf_active_content(pdf)
    with pytest.raises(ds.DocError) as e:
        ds.check_upload("evil.pdf", pdf)
    assert e.value.status == 422


def test_pdf_js_hidden_in_compressed_object_stream_is_refused():
    hidden = zlib.compress(b"<< /S /JavaScript /JS (app.alert(1)) >>")
    pdf = _pdf(b"5 0 obj << /Type /ObjStm /Filter /FlateDecode /Length %d >>\nstream\n" % len(hidden)
               + hidden + b"\nendstream endobj")
    assert ds.pdf_active_content(pdf) == "javascript"


def test_images_still_upload_through_docstore():
    assert ds.check_upload("p.png", PNG)["content_type"] == "image/png"
    with pytest.raises(ds.DocError):
        ds.check_upload("x.svg", SVG)
