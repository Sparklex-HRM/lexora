"""Auth service — SAML 2.0 service provider, pure stdlib + ``cryptography``.

Adding ``python3-saml`` would pull in ``xmlsec`` (a C library) into the image, so
this module does the SP side by hand:

  * AuthnRequest: built, DEFLATE + base64, HTTP-Redirect binding (unsigned —
    every mainstream IdP accepts an unsigned request; the Response IS verified).
  * Response (HTTP-POST binding): XML-DSig verification with stdlib
    ``xml.etree.ElementTree.canonicalize`` (C14N 2.0 — the same output as
    Exclusive C14N 1.0 for the documents Azure AD / Entra, Okta and Google
    produce) + RSA-SHA256 / RSA-SHA1 via ``cryptography``. The enveloped
    signature may sit on the Response or on the Assertion. On ANY doubt
    (missing signature, digest mismatch, bad time window, wrong audience /
    InResponseTo / Destination) the login is REFUSED — never accepted on trust.

If your IdP's documents fail verification, the fix is the certificate (paste the
one from the IdP metadata) — or deploy python3-saml; the interface here is small.
"""
from __future__ import annotations

import base64
import hashlib
import re
import uuid
import zlib
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

NS = {"samlp": "urn:oasis:names:tc:SAML:2.0:protocol", "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
      "ds": "http://www.w3.org/2000/09/xmldsig#", "md": "urn:oasis:names:tc:SAML:2.0:metadata"}
DIGEST = {"http://www.w3.org/2001/04/xmlenc#sha256": hashlib.sha256,
          "http://www.w3.org/2000/09/xmldsig#sha1": hashlib.sha1,
          "http://www.w3.org/2001/04/xmldsig-more#sha384": hashlib.sha384,
          "http://www.w3.org/2001/04/xmlenc#sha512": hashlib.sha512}
SIGN = {"http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": hashes.SHA256,
        "http://www.w3.org/2000/09/xmldsig#rsa-sha1": hashes.SHA1,
        "http://www.w3.org/2001/04/xmldsig-more#rsa-sha384": hashes.SHA384,
        "http://www.w3.org/2001/04/xmldsig-more#rsa-sha512": hashes.SHA512}


class SamlError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z$", s)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


# ── AuthnRequest ─────────────────────────────────────────────────────────────

def new_request_id() -> str:
    return "_" + uuid.uuid4().hex


def build_authn_request(*, request_id: str, sp_entity_id: str, acs_url: str, idp_sso_url: str) -> str:
    return (f'<samlp:AuthnRequest xmlns:samlp="{NS["samlp"]}" xmlns:saml="{NS["saml"]}" ID="{request_id}" '
            f'Version="2.0" IssueInstant="{_ts(_now())}" Destination="{idp_sso_url}" '
            f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" AssertionConsumerServiceURL="{acs_url}">'
            f'<saml:Issuer>{sp_entity_id}</saml:Issuer>'
            f'<samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" AllowCreate="true"/>'
            f'</samlp:AuthnRequest>')


def redirect_url(idp_sso_url: str, authn_xml: str, relay_state: str) -> str:
    from urllib.parse import urlencode
    raw = zlib.compress(authn_xml.encode())[2:-4]                 # raw DEFLATE
    q = urlencode({"SAMLRequest": base64.b64encode(raw).decode(), "RelayState": relay_state})
    return idp_sso_url + ("&" if "?" in idp_sso_url else "?") + q


def sp_metadata_xml(*, sp_entity_id: str, acs_url: str) -> str:
    return (f'<?xml version="1.0"?><md:EntityDescriptor xmlns:md="{NS["md"]}" entityID="{sp_entity_id}">'
            f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
            f'protocolSupportEnumeration="{NS["samlp"]}">'
            f'<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>'
            f'<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
            f'Location="{acs_url}" index="1" isDefault="true"/></md:SPSSODescriptor></md:EntityDescriptor>')


# ── certificate ──────────────────────────────────────────────────────────────

def load_public_key(cert_pem: str):
    """Accepts a PEM certificate, or the bare base64 body as pasted from IdP metadata."""
    body = (cert_pem or "").strip()
    if "-----BEGIN" not in body:
        body = "-----BEGIN CERTIFICATE-----\n" + re.sub(r"\s+", "", body) + "\n-----END CERTIFICATE-----"
    try:
        cert = x509.load_pem_x509_certificate(body.encode())
    except Exception as e:  # noqa: BLE001
        raise SamlError(f"The IdP certificate could not be read ({type(e).__name__}).")
    key = cert.public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        raise SamlError("Only RSA IdP certificates are supported.")
    return key


def cert_fingerprint(cert_pem: str) -> str | None:
    try:
        body = (cert_pem or "").strip()
        if "-----BEGIN" not in body:
            body = "-----BEGIN CERTIFICATE-----\n" + re.sub(r"\s+", "", body) + "\n-----END CERTIFICATE-----"
        return x509.load_pem_x509_certificate(body.encode()).fingerprint(hashes.SHA256()).hex()
    except Exception:  # noqa: BLE001
        return None


# ── XML helpers ──────────────────────────────────────────────────────────────

def _register_prefixes(xml_text: str) -> None:
    """Keep the IdP's own prefixes (saml2 / samlp / ds …) so canonical forms match."""
    for prefix, uri in re.findall(r'xmlns:([A-Za-z0-9_.\-]+)="([^"]+)"', xml_text):
        try:
            ET.register_namespace(prefix, uri)
        except ValueError:
            pass


def canonicalize(elem: ET.Element) -> bytes:
    return ET.canonicalize(ET.tostring(elem, encoding="unicode"), with_comments=False).encode()


def _find_by_id(root: ET.Element, ref_id: str) -> ET.Element | None:
    for el in root.iter():
        if el.get("ID") == ref_id:
            return el
    return None


def _strip_signature(elem: ET.Element) -> ET.Element:
    import copy
    c = copy.deepcopy(elem)
    for parent in c.iter():
        for child in list(parent):
            if child.tag == f"{{{NS['ds']}}}Signature":
                parent.remove(child)
    return c


def verify_signature(root: ET.Element, signed_elem: ET.Element, public_key) -> None:
    """Verify the enveloped ds:Signature that is a DIRECT child of ``signed_elem``."""
    sig = signed_elem.find("ds:Signature", NS)
    if sig is None:
        raise SamlError("not signed")
    si = sig.find("ds:SignedInfo", NS)
    sv = sig.find("ds:SignatureValue", NS)
    ref = si.find("ds:Reference", NS) if si is not None else None
    if si is None or sv is None or ref is None:
        raise SamlError("The signature is incomplete.")
    ref_uri = (ref.get("URI") or "").lstrip("#")
    if ref_uri and ref_uri != signed_elem.get("ID"):
        raise SamlError("The signature refers to a different element.")
    dm = ref.find("ds:DigestMethod", NS)
    dv = ref.find("ds:DigestValue", NS)
    sm = si.find("ds:SignatureMethod", NS)
    hasher = DIGEST.get(dm.get("Algorithm") if dm is not None else "")
    sign_hash = SIGN.get(sm.get("Algorithm") if sm is not None else "")
    if not hasher or not sign_hash or dv is None:
        raise SamlError("The signature uses an algorithm that is not supported.")
    digest = hasher(canonicalize(_strip_signature(signed_elem))).digest()
    if base64.b64encode(digest).decode() != re.sub(r"\s+", "", dv.text or ""):
        raise SamlError("The document does not match its signature (digest).")
    try:
        public_key.verify(base64.b64decode(re.sub(r"\s+", "", sv.text or "")), canonicalize(si),
                          padding.PKCS1v15(), sign_hash())
    except Exception:  # noqa: BLE001
        raise SamlError("The signature is not valid for the configured IdP certificate.")


# ── Response ─────────────────────────────────────────────────────────────────

def parse_response(saml_response_b64: str, *, public_key, sp_entity_id: str, acs_url: str,
                   expected_request_id: str | None, idp_entity_id: str, now: datetime | None = None,
                   skew: timedelta = timedelta(minutes=3)) -> dict:
    """Verify everything and return {name_id, attributes, session_index}. Raises SamlError."""
    try:
        xml_text = base64.b64decode(saml_response_b64 or "").decode("utf-8")
    except Exception:  # noqa: BLE001
        raise SamlError("The SAML response could not be decoded.")
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        raise SamlError("The SAML response contains a DOCTYPE, which is not allowed.")
    _register_prefixes(xml_text)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise SamlError("The SAML response is not well-formed XML.")
    if root.tag != f"{{{NS['samlp']}}}Response":
        raise SamlError("The document is not a SAML Response.")
    t = now or _now()

    status = root.find("samlp:Status/samlp:StatusCode", NS)
    if status is None or not (status.get("Value") or "").endswith(":Success"):
        raise SamlError("The identity provider refused the sign-in.")
    dest = root.get("Destination")
    if dest and dest != acs_url:
        raise SamlError("The response was meant for a different address.")
    irt = root.get("InResponseTo")
    if expected_request_id and irt and irt != expected_request_id:
        raise SamlError("The response does not belong to this sign-in attempt.")

    if root.find("saml:EncryptedAssertion", NS) is not None:
        raise SamlError("Encrypted assertions are not supported — switch the IdP to signed, unencrypted assertions.")
    assertion = root.find("saml:Assertion", NS)
    if assertion is None:
        raise SamlError("The response holds no assertion.")

    # Signature: on the Response (covers everything) or on the Assertion.
    signed = False
    if root.find("ds:Signature", NS) is not None:
        verify_signature(root, root, public_key)
        signed = True
    if assertion.find("ds:Signature", NS) is not None:
        verify_signature(root, assertion, public_key)
        signed = True
    if not signed:
        raise SamlError("Neither the response nor the assertion is signed.")

    issuer = (assertion.findtext("saml:Issuer", default="", namespaces=NS) or "").strip()
    if idp_entity_id and issuer and issuer != idp_entity_id.strip():
        raise SamlError("The assertion comes from a different identity provider.")
    cond = assertion.find("saml:Conditions", NS)
    if cond is not None:
        nb, na = _parse_ts(cond.get("NotBefore")), _parse_ts(cond.get("NotOnOrAfter"))
        if nb and t + skew < nb:
            raise SamlError("The assertion is not valid yet (check the clocks).")
        if na and t - skew >= na:
            raise SamlError("The assertion has expired.")
        auds = [a.text.strip() for a in cond.findall("saml:AudienceRestriction/saml:Audience", NS) if a.text]
        if auds and sp_entity_id not in auds:
            raise SamlError("The assertion is for a different service provider (audience).")
    sc = assertion.find("saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData", NS)
    if sc is not None:
        na = _parse_ts(sc.get("NotOnOrAfter"))
        if na and t - skew >= na:
            raise SamlError("The assertion has expired.")
        if expected_request_id and sc.get("InResponseTo") and sc.get("InResponseTo") != expected_request_id:
            raise SamlError("The assertion does not belong to this sign-in attempt.")
        if sc.get("Recipient") and sc.get("Recipient") != acs_url:
            raise SamlError("The assertion was meant for a different address.")

    name_id = (assertion.findtext("saml:Subject/saml:NameID", default="", namespaces=NS) or "").strip()
    attrs: dict[str, str] = {}
    for a in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        vals = [v.text.strip() for v in a.findall("saml:AttributeValue", NS) if v.text and v.text.strip()]
        if vals:
            name = a.get("Name") or ""
            attrs[name] = vals[0]
            short = name.rsplit("/", 1)[-1]           # http://schemas.../claims/emailaddress → emailaddress
            attrs.setdefault(short, vals[0])
            if a.get("FriendlyName"):
                attrs.setdefault(a.get("FriendlyName"), vals[0])
    if name_id and "email" not in attrs and "@" in name_id:
        attrs["email"] = name_id
    attrs.setdefault("emailaddress", attrs.get("email", ""))
    auth = assertion.find("saml:AuthnStatement", NS)
    return {"name_id": name_id, "attributes": attrs,
            "session_index": auth.get("SessionIndex") if auth is not None else None}


# ── test helper: sign a response the way an IdP does (for the unit tests only) ─

def sign_element(xml_text: str, elem_id: str, private_key) -> str:
    """Add an enveloped RSA-SHA256 signature to the element with ``ID=elem_id``
    (prepended as its first child after Issuer, as IdPs do). Test use only."""
    _register_prefixes(xml_text)
    root = ET.fromstring(xml_text)
    target = _find_by_id(root, elem_id)
    if target is None:
        raise SamlError("no such element")
    digest = base64.b64encode(hashlib.sha256(canonicalize(_strip_signature(target))).digest()).decode()
    si_xml = (f'<ds:SignedInfo xmlns:ds="{NS["ds"]}">'
              '<ds:CanonicalizationMethod Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/>'
              '<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"/>'
              f'<ds:Reference URI="#{elem_id}"><ds:Transforms>'
              '<ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"/>'
              '<ds:Transform Algorithm="http://www.w3.org/2001/10/xml-exc-c14n#"/></ds:Transforms>'
              '<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/>'
              f'<ds:DigestValue>{digest}</ds:DigestValue></ds:Reference></ds:SignedInfo>')
    si = ET.fromstring(si_xml)
    sig_value = base64.b64encode(private_key.sign(canonicalize(si), padding.PKCS1v15(), hashes.SHA256())).decode()
    sig = ET.Element(f"{{{NS['ds']}}}Signature")
    sig.append(si)
    svel = ET.SubElement(sig, f"{{{NS['ds']}}}SignatureValue")
    svel.text = sig_value
    pos = 1 if len(target) and target[0].tag == f"{{{NS['saml']}}}Issuer" else 0
    target.insert(pos, sig)
    return ET.tostring(root, encoding="unicode")


def pem_of(public_key) -> str:
    return public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
