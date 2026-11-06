"""Column-encryption tests: round-trip, nonce uniqueness, rotation, blind index, masking.
Run:  cd backend && python -m pytest shared/utils/test_crypto.py -q
"""
import base64
import os

import pytest

from shared.utils import crypto


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    k1 = base64.b64encode(b"\x01" * 32).decode()
    monkeypatch.setenv("LEXORA_DATA_KEY", k1)
    monkeypatch.setenv("LEXORA_DATA_KEY_ID", "k1")
    monkeypatch.delenv("LEXORA_DATA_KEYS", raising=False)
    crypto.reload_keys()
    yield
    crypto.reload_keys()


def test_round_trip_and_format():
    tok = crypto.encrypt("ABCDE1234F")
    assert tok.startswith("enc:v1:k1:")
    assert crypto.is_encrypted(tok)
    assert crypto.decrypt(tok) == "ABCDE1234F"
    assert crypto.status()["configured"] is True


def test_nonce_differs_per_call():
    assert crypto.encrypt("same") != crypto.encrypt("same")


def test_empty_and_none():
    assert crypto.encrypt(None) is None
    assert crypto.encrypt("") is None
    assert crypto.decrypt(None) is None
    assert crypto.decrypt("legacy-plain") == "legacy-plain"      # mixed column read


def test_never_double_encrypts():
    tok = crypto.encrypt("x")
    assert crypto.encrypt(tok) == tok


def test_tamper_detected():
    tok = crypto.encrypt("secret")
    bad = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(bad)


def test_rotation(monkeypatch):
    old = crypto.encrypt("100123456789")
    assert crypto.key_id_of(old) == "k1"
    # New current key k2, old k1 kept in the ring for decrypt.
    k2 = base64.b64encode(b"\x02" * 32).decode()
    k1 = os.environ["LEXORA_DATA_KEY"]
    monkeypatch.setenv("LEXORA_DATA_KEY", k2)
    monkeypatch.setenv("LEXORA_DATA_KEY_ID", "k2")
    monkeypatch.setenv("LEXORA_DATA_KEYS", f"k1:{k1}")
    crypto.reload_keys()
    assert crypto.decrypt(old) == "100123456789"           # old token still readable
    new = crypto.rotate(old)
    assert crypto.key_id_of(new) == "k2" and crypto.decrypt(new) == "100123456789"
    assert crypto.rotate(new) == new                        # no-op when current
    # Drop k1 from the ring → old token unreadable, new fine.
    monkeypatch.delenv("LEXORA_DATA_KEYS")
    crypto.reload_keys()
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(old)
    assert crypto.decrypt(new) == "100123456789"


def test_blind_index_deterministic_and_normalised():
    a = crypto.blind_index("046454286", scope="sin")
    b = crypto.blind_index(" 046-454-286 ", scope="sin")
    assert a == b and len(a) == 64
    assert crypto.blind_index("046454286", scope="passport") != a   # scope separates
    assert crypto.blind_index("", scope="sin") is None


def test_masks():
    assert crypto.mask("100123456789", "generic") == "XXXXXXXX6789"
    assert crypto.mask("123456789", "sin") == "•••-•••-789"
    assert crypto.mask("004-12345-0001234", "generic").endswith("1234")
    assert crypto.mask(None, "sin") is None
    assert crypto.last_n("00482739", 4) == "2739"


def test_dev_fallback_flagged(monkeypatch):
    monkeypatch.delenv("LEXORA_DATA_KEY")
    crypto.reload_keys()
    st = crypto.status()
    assert st["dev_fallback"] is True and st["configured"] is False
    assert crypto.decrypt(crypto.encrypt("still works")) == "still works"
