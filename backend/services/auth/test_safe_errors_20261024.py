"""WASA follow-up (2026-10-24): auth refusals never carry a library's exception text."""
import json

import http_errors as H
from shared.utils import identity


def _lib_error():
    try:
        json.loads("{not json")                 # raised inside the standard library
    except ValueError as e:
        return e


def _our_error():
    try:
        identity._uuid("not-a-uuid")            # raised in backend/shared (our sentence)
    except identity.IdentityError as e:
        return e


def test_our_sentence_passes_with_a_code():
    r = H.refusal(422, _our_error(), "AUTH_X")
    assert r.status_code == 422 and r.detail == "That record id is not valid" and r.extra == {"code": "AUTH_X"}


def test_library_text_is_masked_with_a_ref():
    e = _lib_error()
    r = H.refusal(422, e, "AUTH_Y", fallback="Fixed sentence.")
    assert r.detail == "Fixed sentence." and r.extra["code"] == "AUTH_Y" and r.extra["ref"]
    assert "Expecting" not in r.detail


def test_safe_detail_length_cap():
    class E(Exception):
        pass
    try:
        raise E("x" * 400)
    except E as e:
        assert H.safe_detail(e, "fb") == "fb"
