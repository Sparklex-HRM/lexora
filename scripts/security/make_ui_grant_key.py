"""Make a P-256 key pair for the signed UI grant (docs/security/UI_GRANT.md).

    python scripts/security/make_ui_grant_key.py --kid dev --env       # LOCAL: key into the git-ignored .env
    python scripts/security/make_ui_grant_key.py --kid k1               # PRODUCTION: run on the deploy machine

Writes the PRIVATE key to secrets/ui-grant-<kid>.p8.pem (git-ignored: *.pem) — it is never printed — and prints
the PUBLIC key as the JWK entry to pin in frontend/src/config/uiGrantKeys.ts (or pass at build time with
VITE_UI_GRANT_PUBKEYS='{"<kid>":{...}}'). With --env (local only) it also sets UI_GRANT_PRIVATE_KEY /
UI_GRANT_KID in Q-HRM/.env, which docker compose passes to auth-service. Refuses to overwrite an existing key file
unless --force. Public keys are not secrets; private keys never go into git, chat, tickets or logs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parents[2]
MARK = "# signed UI grant — LOCAL DEV key (scripts/security/make_ui_grant_key.py --kid dev --env)"


def b64u(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kid", required=True, help='key id: "dev" for local, e.g. "k1" for production')
    ap.add_argument("--env", action="store_true", help="also write UI_GRANT_* into Q-HRM/.env (local dev only)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", a.kid):
        raise SystemExit("kid: 1-16 letters, digits, - or _")
    out = ROOT / "secrets" / f"ui-grant-{a.kid}.p8.pem"
    if out.exists() and not a.force:
        raise SystemExit(f"{out} exists — use --force to replace it (then re-pin the public key)")
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()).decode()
    out.parent.mkdir(exist_ok=True)
    out.write_text(pem, encoding="utf-8")
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    nums = k.public_key().public_numbers()
    jwk = {"kty": "EC", "crv": "P-256", "x": b64u(nums.x), "y": b64u(nums.y)}
    print(f"private key -> {out.relative_to(ROOT)}  (git-ignored; never commit / paste it)")
    print("pin this PUBLIC key in frontend/src/config/uiGrantKeys.ts:")
    print(f'  {a.kid}: {json.dumps(jwk)},')
    if a.env:
        if a.kid != "dev":
            raise SystemExit("--env is for the local dev key only (kid dev)")
        envf = ROOT / ".env"
        text = envf.read_text(encoding="utf-8") if envf.exists() else ""
        lines, skip = [], False
        for ln in text.splitlines():
            if ln.startswith(("UI_GRANT_PRIVATE_KEY=", "UI_GRANT_KID=", MARK)):
                skip = ln.startswith("UI_GRANT_PRIVATE_KEY=") and "END PRIVATE KEY" not in ln
                continue
            if skip:                                   # a multi-line value left by an older run
                skip = "END PRIVATE KEY" not in ln
                continue
            lines.append(ln)
        one_line = "".join(ch if ch != "\n" else "\\n" for ch in pem.strip())
        lines += [MARK, "UI_GRANT_PRIVATE_KEY=" + one_line, "UI_GRANT_KID=dev"]
        envf.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("wrote UI_GRANT_PRIVATE_KEY / UI_GRANT_KID=dev into .env (recreate auth-service to pick it up)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
