"""WASA follow-up (2026-10-24): LMS module links are validated by the server."""
import pytest
from fastapi import HTTPException

from engage_lms import _clean_modules, safe_link


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>x</script>",
                                 "file:///etc/passwd", "vbscript:msgbox", "https://", "//evil.example/x",
                                 "https://user:pw@evil.example/", "https://a b.example/", "https://x.example/a" + chr(10) + "b",
                                 "https://x.example/" + "a" * 600, "ftp://files.example/x"])
def test_bad_links_refused(bad):
    with pytest.raises(HTTPException) as e:
        safe_link(bad, "Intro")
    assert e.value.status_code == 422


def test_good_links_kept():
    assert safe_link("https://www.youtube.com/watch?v=abc", "Intro") == "https://www.youtube.com/watch?v=abc"
    assert safe_link("  http://intranet.maple.example/course  ") == "http://intranet.maple.example/course"
    assert safe_link("") is None


def test_modules_use_it_and_pdf_drops_url():
    out = _clean_modules([{"title": "Watch", "kind": "video", "url": "https://v.example/1"},
                          {"title": "Read", "kind": "pdf", "doc_id": "d1", "url": "javascript:alert(1)"}])
    assert out[0]["url"] == "https://v.example/1" and out[1]["url"] is None
    with pytest.raises(HTTPException):
        _clean_modules([{"title": "Bad", "kind": "url", "url": "javascript:alert(1)"}])
