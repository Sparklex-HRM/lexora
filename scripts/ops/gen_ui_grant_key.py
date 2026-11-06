"""Make the PRODUCTION signing key of the signed UI grant (ES256 / P-256) and print the env lines.

    python scripts/ops/gen_ui_grant_key.py --kid k1
    python scripts/ops/gen_ui_grant_key.py --kid k2 --previous-pubkeys '{"k1":{"kty":"EC","crv":"P-256","x":"…","y":"…"}}'
    python scripts/ops/gen_ui_grant_key.py --kid k2 --private-out /secure/ui-grant-k2.pem   # file OUTSIDE the repo

Prints (stdout):
    LEXORA_UI_GRANT_KID=<kid>                      -> auth-service secret
    LEXORA_UI_GRANT_PRIVATE_KEY=-----BEGIN…\\n…     -> auth-service secret (one line, "\\n" escapes)
    VITE_UI_GRANT_PUBKEYS={"<kid>":{JWK},…}        -> web BUILD arg (public; current kid + at most ONE previous kid)

The private key is NEVER written into the repository: it goes to stdout (paste it straight into the secret store —
Helm secret / compose .env on the server / vault) or, with --private-out, to a file that must be outside the repo
(mode 600). Rotation (docs/ops/GO_LIVE_CHECKLIST.md "UI-grant signing key"): make kN+1 with --previous-pubkeys =
the current VITE_UI_GRANT_PUBKEYS -> rebuild + deploy the WEB app first (it then accepts both kids) -> switch
auth-service to the new private key / kid -> next release: rebuild the web app with only the new kid.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[2]
KID_RE = re.compile(r"[A-Za-z0-9_-]{1,16}")
B64U_32 = re.compile(r"[A-Za-z0-9_-]{43}")


def b64u(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


def dev_public_xy() -> set[tuple[str, str]]:
    """Known development public keys (backend/shared/utils/ui_grant_key.py) — never accepted as 'previous'."""
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from shared.utils.ui_grant_key import DEV_PUBLIC_JWKS
    except Exception:  # noqa: BLE001
        return set()
    return {(j["x"], j["y"]) for j in DEV_PUBLIC_JWKS.values()}


def parse_previous(raw: str | None, keep: str | None, new_kid: str) -> dict:
    if not raw:
        return {}
    try:
        o = json.loads(raw)
    except ValueError:
        raise SystemExit("--previous-pubkeys: not JSON (pass the current VITE_UI_GRANT_PUBKEYS value)")
    if not isinstance(o, dict) or not o:
        raise SystemExit("--previous-pubkeys: expected {kid: JWK}")
    if keep is None:
        if len(o) != 1:
            raise SystemExit(f"--previous-pubkeys has {len(o)} kids ({', '.join(o)}): name the one still signing "
                             "with --previous-kid (the web build accepts the current kid + ONE previous kid)")
        keep = next(iter(o))
    if keep not in o:
        raise SystemExit(f"--previous-kid {keep!r} is not in --previous-pubkeys")
    if keep == new_kid:
        raise SystemExit("the new kid must differ from the previous kid")
    j = o[keep]
    if not (isinstance(j, dict) and j.get("kty") == "EC" and j.get("crv") == "P-256"
            and B64U_32.fullmatch(str(j.get("x", ""))) and B64U_32.fullmatch(str(j.get("y", "")))):
        raise SystemExit(f"previous key {keep!r} is not a P-256 JWK")
    if keep == "dev" or (j["x"], j["y"]) in dev_public_xy():
        raise SystemExit("the previous key is a DEVELOPMENT key — never pin it in a production build")
    return {keep: {"kty": "EC", "crv": "P-256", "x": j["x"], "y": j["y"]}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kid", required=True, help='key id of the NEW key, e.g. "k1" (never "dev")')
    ap.add_argument("--previous-pubkeys", help="current VITE_UI_GRANT_PUBKEYS JSON (rotation: keep its kid accepted)")
    ap.add_argument("--previous-kid", help="which kid of --previous-pubkeys is still signing (when it holds several)")
    ap.add_argument("--private-out", help="also write the private PEM to this file (must be OUTSIDE the repository)")
    a = ap.parse_args(argv)

    if not KID_RE.fullmatch(a.kid):
        raise SystemExit("--kid: 1-16 letters, digits, - or _")
    if a.kid.lower() == "dev":
        raise SystemExit('--kid dev is the LOCAL development key: use scripts/security/make_ui_grant_key.py --kid dev --env')
    previous = parse_previous(a.previous_pubkeys, a.previous_kid, a.kid)

    out_path = None
    if a.private_out:
        out_path = Path(a.private_out).expanduser().resolve()
        try:
            out_path.relative_to(ROOT)
            raise SystemExit(f"--private-out {out_path} is inside the repository ({ROOT}) — choose a path outside it")
        except ValueError:
            pass
        if out_path.exists():
            raise SystemExit(f"{out_path} exists — refusing to overwrite a key")

    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    nums = k.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "x": b64u(nums.x), "y": b64u(nums.y)}
    pubkeys = {a.kid: jwk, **previous}

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(pem)

    one_line = pem.strip().replace("\n", "\\n")
    print(f"# Lexora HRM - UI-grant signing key {a.kid} made {date.today().isoformat()}"
          f"{' (rotation: previous kid ' + next(iter(previous)) + ' stays accepted)' if previous else ''}")
    print("# --- auth-service SECRET (secret store only: never git, chat, tickets or logs) ---")
    print(f"LEXORA_UI_GRANT_KID={a.kid}")
    if out_path is not None:
        print("# private key written to this file (mode 600); mount it into auth-service at the same path")
        print(f"LEXORA_UI_GRANT_PRIVATE_KEY_FILE={out_path}")
    else:
        print(f"LEXORA_UI_GRANT_PRIVATE_KEY={one_line}")
    print("# --- web BUILD arg (public key(s), safe to commit / pass to `docker build --build-arg`) ---")
    print("VITE_UI_GRANT_PUBKEYS=" + json.dumps(pubkeys, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
