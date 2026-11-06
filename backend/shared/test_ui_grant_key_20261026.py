"""Go-live blocker 2026-10-26: production UI-grant signing key — boot guard, env names, dev-key detection, generator.
Run:  cd backend && python -m pytest shared/test_ui_grant_key_20261026.py -q
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from shared import ops_runtime as ops
from shared.test_ops_runtime import GOOD_ENV
from shared.utils import ui_grant_key as K

ROOT = Path(__file__).resolve().parents[2]


def _pem(curve=None) -> str:
    k = ec.generate_private_key(curve or ec.SECP256R1())
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()


PEM = _pem()


@pytest.fixture
def auth_ships_grant(monkeypatch):
    monkeypatch.setattr(ops, "_ui_grant_present", lambda: True)


def _probs(env):
    return ops.production_problems(dict(GOOD_ENV, **env), "auth")


def test_production_key_passes(auth_ships_grant):
    assert _probs({"LEXORA_UI_GRANT_PRIVATE_KEY": PEM.replace("\n", "\\n"), "LEXORA_UI_GRANT_KID": "k1"}) == []
    assert _probs({"UI_GRANT_PRIVATE_KEY": PEM, "UI_GRANT_KID": "k2"}) == []          # 2026-10-24 names still work


def test_key_file(auth_ships_grant, tmp_path):
    f = tmp_path / "ui-grant.pem"
    f.write_text(PEM, encoding="utf-8")
    assert _probs({"LEXORA_UI_GRANT_PRIVATE_KEY_FILE": str(f), "LEXORA_UI_GRANT_KID": "k1"}) == []
    probs = _probs({"LEXORA_UI_GRANT_PRIVATE_KEY_FILE": str(tmp_path / "missing.pem"), "LEXORA_UI_GRANT_KID": "k1"})
    assert len(probs) == 1 and "cannot be read" in probs[0]


@pytest.mark.parametrize("env,needle", [
    ({}, "unset"),
    ({"LEXORA_UI_GRANT_PRIVATE_KEY": PEM, "LEXORA_UI_GRANT_KID": "dev"}, "development key id"),
    ({"LEXORA_UI_GRANT_PRIVATE_KEY": PEM, "LEXORA_UI_GRANT_KID": "DEV"}, "development key id"),
    ({"LEXORA_UI_GRANT_PRIVATE_KEY": PEM, "LEXORA_UI_GRANT_KID": "k 1;x"}, "1-16 letters"),
    ({"LEXORA_UI_GRANT_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----"}, "PKCS#8"),
    ({"LEXORA_UI_GRANT_PRIVATE_KEY": _pem(ec.SECP384R1())}, "P-256"),
])
def test_unusable_keys_refused(auth_ships_grant, env, needle):
    probs = _probs(env)
    assert any(needle in p for p in probs), probs
    assert not any("BEGIN" in p or "MI" in p[:4] for p in probs)                       # never the key


def test_dev_key_refused_under_any_kid(auth_ships_grant, monkeypatch):
    k = serialization.load_pem_private_key(PEM.encode(), None)
    monkeypatch.setattr(K, "DEV_PUBLIC_JWKS", {"dev": K.public_jwk(k)})                # pretend PEM is the dev key
    probs = _probs({"LEXORA_UI_GRANT_PRIVATE_KEY": PEM, "LEXORA_UI_GRANT_KID": "k7"})
    assert any("DEVELOPMENT key" in p for p in probs), probs
    assert _probs({"LEXORA_UI_GRANT_PRIVATE_KEY": _pem(), "LEXORA_UI_GRANT_KID": "k7"}) == []


def test_lexora_names_win_over_the_short_names():
    env = {"LEXORA_UI_GRANT_KID": "k3", "UI_GRANT_KID": "dev",
           "LEXORA_UI_GRANT_PRIVATE_KEY": PEM, "UI_GRANT_PRIVATE_KEY": "garbage"}
    assert K.kid(env) == "k3" and K.production_problems(env) == []
    assert K.kid({}) == "k1"


def test_boot_guard_exits_78(auth_ships_grant):
    with pytest.raises(ops.ProductionConfigError) as ei:
        ops.enforce_production_config("auth", dict(GOOD_ENV, LEXORA_UI_GRANT_KID="dev", LEXORA_UI_GRANT_PRIVATE_KEY=PEM))
    assert ei.value.code == 78
    ops.enforce_production_config("auth", dict(GOOD_ENV, LEXORA_UI_GRANT_PRIVATE_KEY=PEM))   # ok -> no raise
    ops.enforce_production_config("leave", dict(GOOD_ENV))                                  # other services: no key


def test_dev_public_keys_match_the_frontend_list():
    ts = (ROOT / "frontend" / "src" / "config" / "uiGrantKeySet.ts").read_text(encoding="utf-8")
    block = ts[ts.index("DEV_UI_GRANT_KEYS"):]
    block = block[:block.index("};")]
    found = {m.group(1): {"x": m.group(2), "y": m.group(3)}
             for m in re.finditer(r'(\w+):\s*\{[^}]*x:\s*"([^"]+)",\s*y:\s*"([^"]+)"', block)}
    assert found == {kid: {"x": j["x"], "y": j["y"]} for kid, j in K.DEV_PUBLIC_JWKS.items()}


# ── scripts/ops/gen_ui_grant_key.py ────────────────────────────────────────────

def _gen():
    spec = importlib.util.spec_from_file_location("gen_ui_grant_key", ROOT / "scripts" / "ops" / "gen_ui_grant_key.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _lines(out: str) -> dict:
    return dict(ln.split("=", 1) for ln in out.splitlines() if ln and not ln.startswith("#") and "=" in ln)


def test_generator_prints_env_lines_that_pass_the_guard(capsys, auth_ships_grant):
    assert _gen().main(["--kid", "k1"]) == 0
    v = _lines(capsys.readouterr().out)
    assert v["LEXORA_UI_GRANT_KID"] == "k1"
    assert _probs({"LEXORA_UI_GRANT_PRIVATE_KEY": v["LEXORA_UI_GRANT_PRIVATE_KEY"], "LEXORA_UI_GRANT_KID": "k1"}) == []
    pub = json.loads(v["VITE_UI_GRANT_PUBKEYS"])
    key = K.load_private_key(v["LEXORA_UI_GRANT_PRIVATE_KEY"].replace("\\n", "\n"))
    assert pub == {"k1": K.public_jwk(key)}


def test_generator_rotation_keeps_one_previous_kid(capsys):
    g = _gen()
    g.main(["--kid", "k1"])
    first = _lines(capsys.readouterr().out)["VITE_UI_GRANT_PUBKEYS"]
    g.main(["--kid", "k2", "--previous-pubkeys", first])
    second = json.loads(_lines(capsys.readouterr().out)["VITE_UI_GRANT_PUBKEYS"])
    assert list(second) == ["k2", "k1"] and second["k1"] == json.loads(first)["k1"]
    with pytest.raises(SystemExit, match="previous-kid"):                                # 2 previous kids: name one
        g.main(["--kid", "k3", "--previous-pubkeys", json.dumps(second)])
    g.main(["--kid", "k3", "--previous-pubkeys", json.dumps(second), "--previous-kid", "k2"])
    assert list(json.loads(_lines(capsys.readouterr().out)["VITE_UI_GRANT_PUBKEYS"])) == ["k3", "k2"]


@pytest.mark.parametrize("argv,needle", [
    (["--kid", "dev"], "make_ui_grant_key"),
    (["--kid", "bad kid"], "1-16"),
    (["--kid", "k2", "--previous-pubkeys", json.dumps({"dev": K.DEV_PUBLIC_JWKS["dev"]})], "DEVELOPMENT"),
    (["--kid", "k2", "--previous-pubkeys", json.dumps({"k1": K.DEV_PUBLIC_JWKS["dev"]})], "DEVELOPMENT"),
    (["--kid", "k1", "--previous-pubkeys", json.dumps({"k1": K.DEV_PUBLIC_JWKS["dev"]})], "differ"),
    (["--kid", "k2", "--private-out", str(ROOT / "secrets" / "x.pem")], "inside the repository"),
])
def test_generator_refusals(argv, needle):
    with pytest.raises(SystemExit, match=needle):
        _gen().main(argv)


def test_generator_private_out_outside_repo(capsys, tmp_path):
    out = tmp_path / "k5.pem"
    assert _gen().main(["--kid", "k5", "--private-out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "BEGIN PRIVATE KEY" not in text and "LEXORA_UI_GRANT_PRIVATE_KEY_FILE=" in text
    K.load_private_key(out.read_text(encoding="utf-8"))
    with pytest.raises(SystemExit, match="exists"):
        _gen().main(["--kid", "k5", "--private-out", str(out)])
