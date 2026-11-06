"""Signed UI grant — production key handling + kid rotation (go-live blocker closed 2026-10-26).

Run:  cd backend/services/auth && PYTHONPATH=../../ python -m pytest test_ui_grant_rotation_20261026.py -q
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

import ui_grant
from shared.utils import ui_grant_key as K


def _pem():
    k = ec.generate_private_key(ec.SECP256R1())
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode(), k.public_key()


K1, PUB1 = _pem()
K2, PUB2 = _pem()
CLAIMS = {"sub": "u-1", "sid": "s-1", "org_id": "o-1", "exp": int(time.time()) + 600, "iat": int(time.time())}
ACCESS = {"role": "hr_admin", "roles": ["hr_admin"], "ui_roles": ["hr_admin"], "perms": ["people.view"]}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for n in K.PEM_VARS + K.FILE_VARS + K.KID_VARS:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("LEXORA_ENV", "test")
    ui_grant.reset_for_tests()
    yield
    ui_grant.reset_for_tests()


def _sign_with(monkeypatch, pem, kid):
    monkeypatch.setenv("LEXORA_UI_GRANT_PRIVATE_KEY", pem.replace("\n", "\\n"))
    monkeypatch.setenv("LEXORA_UI_GRANT_KID", kid)
    ui_grant.reset_for_tests()
    tok = ui_grant.sign(CLAIMS, ACCESS)
    assert tok and jwt.get_unverified_header(tok)["kid"] == kid
    return tok


def test_rotation_current_and_previous_kid_verify(monkeypatch):
    pinned = {"k2": PUB2, "k1": PUB1}                      # the web build during a rotation: current + previous
    old = _sign_with(monkeypatch, K1, "k1")                # grants issued before auth switched keys
    new = _sign_with(monkeypatch, K2, "k2")                # after the switch
    assert ui_grant.verify(old, pinned)["role"] == "hr_admin"
    assert ui_grant.verify(new, pinned)["role"] == "hr_admin"
    with pytest.raises(ValueError, match="unknown kid"):    # next release drops k1
        ui_grant.verify(old, {"k2": PUB2})
    forged = _sign_with(monkeypatch, K2, "k1")             # k1 header, k2 key -> signature check fails
    with pytest.raises(ValueError, match="bad signature"):
        ui_grant.verify(forged, pinned)


def test_lexora_names_are_read_and_win(monkeypatch):
    monkeypatch.setenv("UI_GRANT_PRIVATE_KEY", "garbage")
    monkeypatch.setenv("UI_GRANT_KID", "dev")
    tok = _sign_with(monkeypatch, K1, "k1")
    assert ui_grant.verify(tok, {"k1": PUB1})["sub"] == "u-1"


def test_key_file(monkeypatch, tmp_path):
    f = tmp_path / "k.pem"
    f.write_text(K2, encoding="utf-8")
    monkeypatch.setenv("LEXORA_UI_GRANT_PRIVATE_KEY_FILE", str(f))
    monkeypatch.setenv("LEXORA_UI_GRANT_KID", "k2")
    assert ui_grant.verify(ui_grant.sign(CLAIMS, ACCESS), {"k2": PUB2})["role"] == "hr_admin"
    monkeypatch.setenv("LEXORA_UI_GRANT_PRIVATE_KEY_FILE", str(tmp_path / "gone.pem"))
    ui_grant.reset_for_tests()
    assert ui_grant.sign(CLAIMS, ACCESS) is None


def test_production_refuses_a_dev_key_under_any_kid(monkeypatch):
    k = serialization.load_pem_private_key(K1.encode(), None)
    monkeypatch.setattr(K, "DEV_PUBLIC_JWKS", {"dev": K.public_jwk(k)})
    monkeypatch.setenv("LEXORA_UI_GRANT_PRIVATE_KEY", K1)
    monkeypatch.setenv("LEXORA_UI_GRANT_KID", "k9")
    monkeypatch.setenv("LEXORA_ENV", "production")
    ui_grant.reset_for_tests()
    assert not ui_grant.signer_ready() and ui_grant.sign(CLAIMS, ACCESS) is None
    monkeypatch.setenv("LEXORA_ENV", "test")               # a dev machine may sign with it
    ui_grant.reset_for_tests()
    assert ui_grant.signer_ready()
    monkeypatch.setenv("LEXORA_ENV", "production")         # a production key signs in production
    monkeypatch.setenv("LEXORA_UI_GRANT_PRIVATE_KEY", K2)
    ui_grant.reset_for_tests()
    assert ui_grant.verify(ui_grant.sign(CLAIMS, ACCESS), {"k9": PUB2})["role"] == "hr_admin"
