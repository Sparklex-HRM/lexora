"""Stream G3 — provider interfaces, events, API keys, chat-ops, WhatsApp, e-sign (pure logic).

Run:  cd backend && python -m pytest -q shared/utils/providers/test_g3_providers.py
"""
import asyncio
import base64
import hashlib
import hmac
import io
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production-0123456789")

from shared.utils import apikeys as K  # noqa: E402
from shared.utils import crypto, events  # noqa: E402
from shared.utils.providers import base, bgv, chatops, esign, store, whatsapp  # noqa: E402
from shared.utils.providers.base import NotConfigured  # noqa: E402


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ── registry + honesty ───────────────────────────────────────────────────────

def test_every_provider_is_registered_and_stubs_say_not_configured():
    keys = {p.key for p in base.all_providers()}
    assert {"esign_internal", "esign_docusign", "bgv", "whatsapp", "slack", "teams"} <= keys
    for p in base.all_providers():
        if p.stub:
            full = {f.key: "x" for m in p.fields for f in p.fields[m]}
            st = run(p.status(next(iter(p.modes), ""), full, True))
            assert st["state"] == "not_configured" and "not built" in st["message"].lower()
            t = run(p.test(next(iter(p.modes), ""), full))
            assert t["ok"] is False and "no call was made" in t["message"].lower()


def test_stub_calls_raise_not_configured_never_fake_results():
    with pytest.raises(NotConfigured):
        run(bgv.order_check({"api_url": "x", "api_key": "k"}, "identity", {}))
    with pytest.raises(NotConfigured):
        esign.provider_can_sign("esign_docusign")
    esign.provider_can_sign("esign_internal")


def test_missing_fields_are_named_on_screen():
    p = base.get("whatsapp")
    st = run(p.status("meta", {"phone_number_id": "1"}, True))
    assert st["state"] == "not_configured" and "access token" in st["message"].lower()
    assert run(p.status("meta", {}, False))["message"] == "Switched off."
    assert run(base.get("bgv").status("manual", {}, True))["state"] == "configured"


# ── secrets at rest: the SIN scheme, masked on the screen ────────────────────

def test_secret_fields_are_encrypted_with_crypto_and_masked_for_the_screen():
    p = base.get("whatsapp")
    enc = store.encrypt_config(p, "meta", {"phone_number_id": "123", "access_token": "tok-secret", "kinds": "leave"})
    assert enc["phone_number_id"] == "123" and enc["kinds"] == "leave"
    assert crypto.is_encrypted(enc["access_token"]) and "tok-secret" not in enc["access_token"]
    dec = store.decrypt_config(p, "meta", enc)
    assert dec["access_token"] == "tok-secret"
    pub = store.public_config(p, "meta", enc)
    assert pub["access_token"] == store.KEEP and pub["phone_number_id"] == "123"
    # sending the mask back keeps the old ciphertext; an empty value drops nothing silently
    again = store.encrypt_config(p, "meta", {"access_token": store.KEEP, "phone_number_id": "456"}, previous=enc)
    assert again["access_token"] == enc["access_token"] and again["phone_number_id"] == "456"
    # a token encrypted for one field cannot be read as another (AAD)
    assert store.decrypt_config(p, "meta", {"app_secret": enc["access_token"]})["app_secret"] is None


# ── events / webhooks ────────────────────────────────────────────────────────

def test_signature_roundtrip_and_tolerance():
    body = events.canonical_body("e1", "employee.created", "org", "2026-10-13T00:00:00+00:00", {"a": 1})
    sig = events.sign("whsec_abc", body, ts=1_000_000)
    assert sig.startswith("t=1000000,v1=")
    assert events.verify("whsec_abc", body, sig, now=1_000_100)
    assert not events.verify("whsec_abc", body, sig, now=1_000_000 + 600)      # too old
    assert not events.verify("whsec_other", body, sig, now=1_000_100)
    assert not events.verify("whsec_abc", body + " ", sig, now=1_000_100)
    assert not events.verify("whsec_abc", body, "garbage", now=1_000_100)
    want = hmac.new(b"whsec_abc", f"1000000.{body}".encode(), hashlib.sha256).hexdigest()
    assert sig.endswith(want)


def test_subscription_and_backoff():
    assert events.subscribed(["employee.created"], "employee.created")
    assert events.subscribed(["employee.*"], "employee.exited")
    assert events.subscribed(["*"], "payroll.run.completed")
    assert not events.subscribed(["leave.approved"], "employee.created")
    assert not events.subscribed(None, "x")
    assert events.backoff(1) == timedelta(seconds=60)
    assert events.backoff(5) == timedelta(seconds=21600)
    assert events.backoff(events.MAX_ATTEMPTS) is None
    assert "employee.created" in events.EVENTS and "payroll.run.completed" in events.EVENTS


# ── API keys ─────────────────────────────────────────────────────────────────

def test_api_key_format_hash_and_scopes():
    raw, prefix, h = K.generate()
    assert raw.startswith("lxk_") and prefix == raw[:12] and h == hashlib.sha256(raw.encode()).hexdigest()
    assert K.looks_like_key(raw) and not K.looks_like_key("eyJhbGciOi...") and not K.looks_like_key("lxk_")
    assert K.clean_scopes(["employees:read", "Leave:WRITE", "auth:write", "x", "*:read", "employees:read"]) == \
        ["*:read", "employees:read", "leave:write"]
    assert K.scope_allows(["employees:read"], "employees", "GET")
    assert not K.scope_allows(["employees:read"], "employees", "POST")
    assert K.scope_allows(["employees:write"], "employees", "GET")
    assert K.scope_allows(["*:read"], "payroll", "GET") and not K.scope_allows(["*:read"], "payroll", "PATCH")
    assert not K.scope_allows(["*:write"], "auth", "GET")          # auth is never key-reachable
    assert not K.scope_allows(["*:write"], "ai", "POST")


def test_api_key_verdict():
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    assert K.verdict(None) == "gone"
    assert K.verdict({"revoked_at": now}) == "revoked"
    assert K.verdict({"expires_at": now - timedelta(days=1)}, now) == "expired"
    assert K.verdict({"expires_at": now + timedelta(days=1)}, now) is None
    assert K.verdict({}) is None


def test_key_cache_reads_once_per_ttl():
    calls = []

    async def loader(raw):
        calls.append(raw)
        return {"id": "k", "org_id": "o", "scopes": [], "role": "employee"}
    t = [100.0]
    c = K.KeyCache(loader=loader, ttl=10, clock=lambda: t[0])
    run(c.get("lxk_a")); run(c.get("lxk_a"))
    assert calls == ["lxk_a"]
    t[0] = 111
    run(c.get("lxk_a"))
    assert len(calls) == 2


def test_gateway_middleware_pieces():
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..", "api-gateway")))
    import apikey_auth as G
    assert G.extract_key([(b"authorization", b"Bearer lxk_12345678_abcdefghijklmnopqrstuvwxyz")]) == "lxk_12345678_abcdefghijklmnopqrstuvwxyz"
    assert G.extract_key([(b"authorization", b"Bearer eyJhbGci.eyJ.sig")]) is None
    assert G.extract_key([(b"x-api-key", b"lxk_12345678_abcdefghijklmnopqrstuvwxyz")]) is not None
    assert G.service_of("/employees/123") == "employees" and G.service_of("/") == ""
    hdrs = dict(G.identity_headers({"id": "k1", "org_id": "org-1", "name": "Zapier", "role": "hr_admin", "scopes": []}))
    assert hdrs[b"x-org-id"] == b"org-1" and hdrs[b"x-role"] == b"hr_admin" and hdrs[b"x-user-id"] == b""
    assert hdrs[b"x-user-email"] == b"apikey:Zapier"


# ── chat-ops ─────────────────────────────────────────────────────────────────

def test_slack_signature_and_timestamp_window():
    body, ts, secret = b"token=x&text=leave+balance", 1_700_000_000, "8f742231b10e8888abcd99yyyzzz85a5"
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    assert chatops.verify_slack_signature(secret, str(ts), body, sig, now=ts + 10)
    assert not chatops.verify_slack_signature(secret, str(ts), body, sig, now=ts + 1000)
    assert not chatops.verify_slack_signature(secret, str(ts), body + b"x", sig, now=ts)
    assert not chatops.verify_slack_signature("", str(ts), body, sig, now=ts)
    assert not chatops.verify_slack_signature(secret, "abc", body, sig, now=ts)


def test_teams_hmac():
    key = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    body = b'{"text":"leave balance"}'
    mac = base64.b64encode(hmac.new(base64.b64decode(key), body, hashlib.sha256).digest()).decode()
    assert chatops.verify_teams_hmac(key, body, "HMAC " + mac)
    assert not chatops.verify_teams_hmac(key, body + b" ", "HMAC " + mac)
    assert not chatops.verify_teams_hmac(key, body, mac)
    assert not chatops.verify_teams_hmac("not-base64!!", body, "HMAC " + mac)


def test_command_parsing_and_payloads():
    assert chatops.parse_command("leave balance") == ("leave_balance", "")
    assert chatops.parse_command("/lexora  Leave Balance ") == ("leave_balance", "")
    assert chatops.parse_command("payslip") == ("payslip", "")
    assert chatops.parse_command("approve 1a2b-3c") == ("approve", "1a2b-3c")
    assert chatops.parse_command("")[0] == "help" and chatops.parse_command("dance")[0] == "unknown"
    assert chatops.parse_command("approve ../x")[0] == "unknown"
    s = chatops.slack_payload("Leave approved", "https://x/leave", "Leave")
    assert s["blocks"][0]["text"]["text"].startswith("*Leave*") and "<https://x/leave|" in s["blocks"][0]["text"]["text"]
    t = chatops.teams_payload("Leave approved", "https://x/leave", "Leave")
    assert t["attachments"][0]["content"]["actions"][0]["url"] == "https://x/leave"
    assert chatops.kinds_opted_in({"kinds": "leave, payroll ,"}) == {"leave", "payroll"}


# ── WhatsApp ─────────────────────────────────────────────────────────────────

def test_whatsapp_signatures_and_status_parsing():
    body = b'{"entry":[]}'
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    assert whatsapp.verify_meta_signature("app-secret", body, sig)
    assert not whatsapp.verify_meta_signature("app-secret", body + b"x", sig)
    assert not whatsapp.verify_meta_signature("", body, sig)
    url, form = "https://x/notifications/whatsapp/webhook", {"MessageSid": "SM1", "MessageStatus": "delivered"}
    s = url + "".join(k + form[k] for k in sorted(form))
    tsig = base64.b64encode(hmac.new(b"tok", s.encode(), hashlib.sha1).digest()).decode()
    assert whatsapp.verify_twilio_signature("tok", url, form, tsig)
    assert not whatsapp.verify_twilio_signature("tok2", url, form, tsig)
    assert whatsapp.parse_twilio_status(form) == ("SM1", "delivered", None)
    assert whatsapp.parse_twilio_status({"MessageSid": "SM2", "MessageStatus": "undelivered", "ErrorCode": "63016"}) == \
        ("SM2", "failed", "Error 63016")
    meta = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.1", "status": "read"}, {"id": "wamid.2", "status": "failed", "errors": [{"code": 131026, "title": "Undeliverable"}]}]}}]}]}
    assert whatsapp.parse_meta_statuses(meta) == [("wamid.1", "read", None), ("wamid.2", "failed", "131026 Undeliverable")]
    assert whatsapp.mask_phone("+91 98765 43210") == "•••••••••3210" and whatsapp.normalise_phone("91 98765-43210") == "+919876543210"


def test_whatsapp_send_without_credentials_is_not_configured():
    with pytest.raises(NotConfigured):
        run(whatsapp.send_template({}, "meta", to="+919876543210", template="t", params=[]))
    with pytest.raises(NotConfigured):
        run(whatsapp.send_template({"phone_number_id": "1", "access_token": "t"}, "meta", to="+91987", template="", params=[]))


# ── e-sign ───────────────────────────────────────────────────────────────────

def _pdf(pages: int = 2) -> bytes:
    from fpdf import FPDF
    p = FPDF()
    for i in range(pages):
        p.add_page()
        p.set_font("Helvetica", size=12)
        p.cell(0, 10, f"Offer letter page {i + 1}")
    return bytes(p.output())


def test_internal_esign_stamps_block_and_appends_certificate():
    from pypdf import PdfReader
    pdf = _pdf(2)
    assert esign.is_pdf(pdf) and not esign.is_pdf(b"hello")
    before = esign.sha256_hex(pdf)
    now = datetime(2026, 10, 13, 9, 30, tzinfo=timezone.utc)
    lines = esign.signature_lines(signer_name="Ravi Kumar", typed_name="Ravi Kumar", verified_via="one-time code to r@x.ca",
                                  ip="10.0.0.9", signed_at=now, doc_sha256=before, envelope_id="env-1")
    out = esign.stamp_and_certify(pdf, block_lines=lines, certificate={
        "title": "Offer letter", "envelope_id": "env-1", "signer_name": "Ravi Kumar", "signer_contact": "r@x.ca",
        "verified_via": "one-time code", "typed_name": "Ravi Kumar", "signed_at": "2026-10-13 09:30:00", "ip": "10.0.0.9",
        "user_agent": "pytest", "original_sha256": before,
        "events": [{"at": "2026-10-13 09:29:00 UTC", "what": "Document opened", "ip": "10.0.0.9"}]})
    r = PdfReader(io.BytesIO(out))
    assert len(r.pages) == 3                                    # 2 original + certificate
    assert esign.sha256_hex(out) != before
    last_text = r.pages[1].extract_text()
    assert "Signed electronically by Ravi Kumar" in last_text and "Offer letter page 2" in last_text
    cert_text = r.pages[2].extract_text()
    assert "Electronic signature certificate" in cert_text and before[:20] in cert_text and "Document opened" in cert_text
    assert "not a qualified" in esign.LEGAL_NOTE


def test_otp_and_link_tokens():
    code = esign.new_otp()
    assert len(code) == 6 and code.isdigit()
    assert esign.hash_code(code, "env-1") != esign.hash_code(code, "env-2")
    raw, h = esign.new_link_token()
    assert h == hashlib.sha256(raw.encode()).hexdigest() and raw not in h


# ── BGV rules ──────────────────────────────────────────────────────────

def test_bgv_transitions():
    assert bgv.valid_transition("requested", "in_progress") and bgv.valid_transition("in_progress", "clear")
    assert not bgv.valid_transition("clear", "requested")
    assert bgv.valid_transition("clear", "cancelled") and not bgv.valid_transition("cancelled", "clear")
    assert not bgv.valid_transition("requested", "bogus")


def test_https_check():
    from shared.utils.providers import http
    assert http.check_https("http://example.com/hook") is not None
    assert http.check_https("https://example.com/hook") is None
    assert http.check_https("http://localhost:9999/hook") is None
