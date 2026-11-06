"""Stream G3 — SSO (OIDC state / code, SAML verification), SCIM mapping, break-glass rule.

Run:  cd backend/services/auth && PYTHONPATH=../../ python -m pytest -q test_g3_platform.py
(conftest.py points the shared config at a throw-away SQLite file.)
"""
import base64
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

import scim as S  # noqa: E402
import sso  # noqa: E402
import sso_saml as SAML  # noqa: E402
from shared.utils.providers.models import SsoConfig  # noqa: E402


# ── state + one-time code ────────────────────────────────────────────────────

def test_state_is_signed_and_protocol_bound():
    st = sso.make_state("org-1", "oidc", {"nonce": "n1", "cv": "v"})
    got = sso.read_state(st, "oidc")
    assert got["org_id"] == "org-1" and got["nonce"] == "n1"
    with pytest.raises(sso.SsoError):
        sso.read_state(st, "saml")
    with pytest.raises(sso.SsoError):
        sso.read_state(st[:-3] + "abc", "oidc")


def test_exchange_code_is_single_use_and_short_lived():
    code = sso.make_code("user-1")
    assert sso.consume_code(code) == "user-1"
    with pytest.raises(sso.SsoError):
        sso.consume_code(code)                                # second use refused
    with pytest.raises(sso.SsoError):
        sso.consume_code(sso.make_state("o", "oidc"))         # a state token is not a code
    old = sso.make_code("user-2")
    with pytest.raises(sso.SsoError):
        sso.consume_code(old, now=time.time() + 120)          # expired


def test_pkce_and_claims():
    v, c = sso.pkce_pair()
    import hashlib
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    assert sso.claim({"email": " A@B.c "}, "email") == "A@B.c"
    assert sso.claim({"ext": {"emp": "E-7"}}, "ext.emp") == "E-7"
    assert sso.claim({"groups": ["hr", "x"]}, "groups") == "hr" and sso.claim({}, "nope") is None


def test_break_glass_rule():
    c = SsoConfig(org_id="o", enabled=True, required=True)
    assert sso.password_login_allowed(c, "org_admin")
    assert not sso.password_login_allowed(c, "employee") and not sso.password_login_allowed(c, "hr_admin")
    c.required = False
    assert sso.password_login_allowed(c, "employee")
    assert sso.password_login_allowed(None, "employee")
    assert sso.missing_for(SsoConfig(org_id="o", protocol="oidc", discovery_url="https://x")) == ["Client id", "Client secret"]
    info = sso.login_page_info(SsoConfig(org_id="o", enabled=True, required=True, display_name="Okta"), "Acme")
    assert info == {"sso": True, "required": True, "display_name": "Okta", "org_name": "Acme"}
    assert sso.login_page_info(SsoConfig(org_id="o", enabled=False), "Acme")["sso"] is False


def test_oidc_authorize_url():
    d = {"authorization_endpoint": "https://idp/authorize", "issuer": "https://idp", "token_endpoint": "x", "jwks_uri": "y"}
    url = sso.oidc_authorize_url(d, client_id="cid", state="st", nonce="nn", challenge="ch")
    assert url.startswith("https://idp/authorize?response_type=code&client_id=cid")
    assert "code_challenge_method=S256" in url and "scope=openid+email+profile" in url


# ── SAML ─────────────────────────────────────────────────────────────────────

def _keys():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from datetime import datetime, timedelta, timezone
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-idp")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1)).sign(key, hashes.SHA256()))
    from cryptography.hazmat.primitives import serialization
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


def _response(rid="_req1", sp="https://app/api/auth/sso/saml/ACME/metadata", acs="https://app/api/auth/sso/callback/saml",
              issuer="https://idp.example/entity", email="ravi@acme.ca", not_after="2099-01-01T00:00:00Z"):
    return (f'<samlp:Response xmlns:samlp="{SAML.NS["samlp"]}" xmlns:saml="{SAML.NS["saml"]}" ID="_resp1" Version="2.0" '
            f'IssueInstant="2026-10-13T09:00:00Z" Destination="{acs}" InResponseTo="{rid}">'
            f'<saml:Issuer>{issuer}</saml:Issuer>'
            f'<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
            f'<saml:Assertion ID="_a1" Version="2.0" IssueInstant="2026-10-13T09:00:00Z">'
            f'<saml:Issuer>{issuer}</saml:Issuer>'
            f'<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">{email}</saml:NameID>'
            f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
            f'<saml:SubjectConfirmationData NotOnOrAfter="{not_after}" Recipient="{acs}" InResponseTo="{rid}"/>'
            f'</saml:SubjectConfirmation></saml:Subject>'
            f'<saml:Conditions NotBefore="2026-01-01T00:00:00Z" NotOnOrAfter="{not_after}">'
            f'<saml:AudienceRestriction><saml:Audience>{sp}</saml:Audience></saml:AudienceRestriction></saml:Conditions>'
            f'<saml:AttributeStatement>'
            f'<saml:Attribute Name="http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress">'
            f'<saml:AttributeValue>{email}</saml:AttributeValue></saml:Attribute>'
            f'<saml:Attribute Name="employeeNumber"><saml:AttributeValue>EMP-0007</saml:AttributeValue></saml:Attribute>'
            f'</saml:AttributeStatement>'
            f'<saml:AuthnStatement AuthnInstant="2026-10-13T09:00:00Z" SessionIndex="s1"/>'
            f'</saml:Assertion></samlp:Response>')


def test_saml_signed_assertion_verifies_and_tampering_is_refused():
    key, pem = _keys()
    pub = SAML.load_public_key(pem)
    signed = SAML.sign_element(_response(), "_a1", key)
    b64 = base64.b64encode(signed.encode()).decode()
    res = SAML.parse_response(b64, public_key=pub, sp_entity_id="https://app/api/auth/sso/saml/ACME/metadata",
                              acs_url="https://app/api/auth/sso/callback/saml", expected_request_id="_req1",
                              idp_entity_id="https://idp.example/entity")
    assert res["name_id"] == "ravi@acme.ca" and res["attributes"]["emailaddress"] == "ravi@acme.ca"
    assert res["attributes"]["employeeNumber"] == "EMP-0007" and res["session_index"] == "s1"
    # tamper with the e-mail inside the signed assertion → digest mismatch
    tampered = base64.b64encode(signed.replace("ravi@acme.ca", "evil@acme.ca").encode()).decode()
    with pytest.raises(SAML.SamlError):
        SAML.parse_response(tampered, public_key=pub, sp_entity_id="https://app/api/auth/sso/saml/ACME/metadata",
                            acs_url="https://app/api/auth/sso/callback/saml", expected_request_id="_req1", idp_entity_id="")
    # unsigned → refused; signed by another key → refused
    with pytest.raises(SAML.SamlError, match="signed"):
        SAML.parse_response(base64.b64encode(_response().encode()).decode(), public_key=pub,
                            sp_entity_id="x", acs_url="https://app/api/auth/sso/callback/saml", expected_request_id=None, idp_entity_id="")
    other, _ = _keys()
    with pytest.raises(SAML.SamlError):
        SAML.parse_response(base64.b64encode(SAML.sign_element(_response(), "_a1", other).encode()).decode(), public_key=pub,
                            sp_entity_id="https://app/api/auth/sso/saml/ACME/metadata",
                            acs_url="https://app/api/auth/sso/callback/saml", expected_request_id="_req1", idp_entity_id="")


def test_saml_response_level_signature_and_condition_checks():
    key, pem = _keys()
    pub = SAML.load_public_key(pem)
    kw = dict(public_key=pub, sp_entity_id="https://app/api/auth/sso/saml/ACME/metadata",
              acs_url="https://app/api/auth/sso/callback/saml", expected_request_id="_req1", idp_entity_id="https://idp.example/entity")
    ok = base64.b64encode(SAML.sign_element(_response(), "_resp1", key).encode()).decode()
    assert SAML.parse_response(ok, **kw)["attributes"]["email"] == "ravi@acme.ca"
    for bad, why in ((_response(rid="_other"), "attempt"), (_response(sp="https://someone-else"), "audience"),
                     (_response(not_after="2020-01-01T00:00:00Z"), "expired"),
                     (_response(issuer="https://other-idp"), "different identity provider")):
        with pytest.raises(SAML.SamlError, match=why):
            SAML.parse_response(base64.b64encode(SAML.sign_element(bad, "_resp1", key).encode()).decode(), **kw)
    with pytest.raises(SAML.SamlError, match="DOCTYPE"):
        SAML.parse_response(base64.b64encode(b"<!DOCTYPE x><samlp:Response/>").decode(), **kw)


def test_saml_request_and_metadata():
    xml = SAML.build_authn_request(request_id="_r", sp_entity_id="sp", acs_url="https://a/acs", idp_sso_url="https://idp/sso")
    assert 'ID="_r"' in xml and "AssertionConsumerServiceURL=\"https://a/acs\"" in xml
    url = SAML.redirect_url("https://idp/sso", xml, "state-x")
    assert url.startswith("https://idp/sso?SAMLRequest=") and "RelayState=state-x" in url
    md = SAML.sp_metadata_xml(sp_entity_id="sp", acs_url="https://a/acs")
    assert 'entityID="sp"' in md and 'Location="https://a/acs"' in md
    _, pem = _keys()
    assert SAML.cert_fingerprint(pem) and SAML.cert_fingerprint("junk") is None
    body = pem.replace("-----BEGIN CERTIFICATE-----", "").replace("-----END CERTIFICATE-----", "")
    assert SAML.cert_fingerprint(body) == SAML.cert_fingerprint(pem)       # bare base64 as pasted from metadata


# ── SCIM ─────────────────────────────────────────────────────────────────────

def test_scim_filter_parse_and_match():
    assert S.parse_filter('userName eq "a@b.c"') == ("email", "eq", "a@b.c")
    assert S.parse_filter('emails.value co "acme"') == ("email", "co", "acme")
    assert S.parse_filter(f'{S.SCHEMA_ENTERPRISE}:employeeNumber eq "EMP-1"') == ("employee_number", "eq", "EMP-1")
    assert S.parse_filter(None) is None
    with pytest.raises(S.ScimError):
        S.parse_filter('userName eq "a" and active eq true')
    with pytest.raises(S.ScimError):
        S.parse_filter('password eq "x"')
    row = {"email": "A@b.c", "is_active": True, "active": True, "employee_number": "EMP-1"}
    assert S.matches(row, ("email", "eq", "a@B.c")) and S.matches(row, ("active", "eq", "True"))
    assert not S.matches(row, ("email", "sw", "z"))


def test_scim_user_mapping_both_ways():
    body = {"userName": "Ravi@Acme.ca", "name": {"givenName": "Ravi", "familyName": "Kumar"}, "active": True,
            "emails": [{"value": "x@y", "primary": False}], "externalId": "okta-1",
            S.SCHEMA_ENTERPRISE: {"employeeNumber": "EMP-0007"}}
    d = S.from_scim_user(body)
    assert d == {"email": "ravi@acme.ca", "first_name": "Ravi", "last_name": "Kumar", "employee_number": "EMP-0007",
                 "active": True, "external_id": "okta-1", "display_name": None}
    u = {"id": "u1", "email": "ravi@acme.ca", "first_name": "Ravi", "last_name": "Kumar", "name": "Ravi Kumar",
         "role": "manager", "is_active": True, "created_at": "2026-10-13T00:00:00+00:00"}
    out = S.to_scim_user(u, base="https://x/auth/scim/v2", employee={"employee_number": "EMP-0007", "department": "Ops"})
    assert out["userName"] == "ravi@acme.ca" and out["active"] is True and out["groups"][0]["value"] == "manager"
    assert out[S.SCHEMA_ENTERPRISE]["employeeNumber"] == "EMP-0007" and out["meta"]["location"].endswith("/Users/u1")
    assert "password" not in str(out).lower()


def test_scim_patch_ops():
    cur = {"email": "a@b.c", "first_name": "A", "last_name": "B", "active": True, "employee_number": None}
    out = S.apply_patch(cur, {"Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
    assert out["active"] is False
    out = S.apply_patch(cur, {"Operations": [{"op": "replace", "value": {"active": False, "name": {"givenName": "Anu"}}}]})
    assert out["active"] is False and out["first_name"] == "Anu" and out["last_name"] == "B"
    out = S.apply_patch(cur, {"Operations": [{"op": "replace", "path": "name.familyName", "value": "Rao"},
                                             {"op": "replace", "path": "userName", "value": "New@B.c"},
                                             {"op": "replace", "path": "title", "value": "ignored"}]})
    assert out["last_name"] == "Rao" and out["email"] == "new@b.c"
    with pytest.raises(S.ScimError):
        S.apply_patch(cur, {"Operations": [{"op": "move", "path": "active", "value": 1}]})
    added, removed = S.group_patch_members({"Operations": [
        {"op": "add", "path": "members", "value": [{"value": "u1"}, {"value": "u2"}]},
        {"op": "remove", "path": 'members[value eq "u3"]'}]})
    assert added == ["u1", "u2"] and removed == ["u3"]


def test_scim_tokens_and_errors():
    raw, h = S.new_token()
    assert raw.startswith("scim_") and S.hash_token(raw) == h
    e = S.ScimError(409, "dup", "uniqueness").body()
    assert e["status"] == "409" and e["scimType"] == "uniqueness" and S.SCHEMA_ERROR in e["schemas"]
    assert "org_admin" not in S.GROUP_ROLES
