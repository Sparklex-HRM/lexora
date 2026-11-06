"""Fixture for frontend/src/lib/__checks__/uiGrant.check.ts: a grant signed by auth-service ui_grant.sign with a
throw-away key (never the dev / production key). Run from Q-HRM:  python scripts/security/make_ui_grant_fixture.py fx.json
"""
import base64, json, os, sys, time
sys.path[:0] = ["backend", "backend/services/auth"]
os.environ["JWT_SECRET"] = "x" * 40
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
k = ec.generate_private_key(ec.SECP256R1())
os.environ["UI_GRANT_PRIVATE_KEY"] = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
os.environ["UI_GRANT_KID"] = "fx"
import ui_grant
from shared.utils.security import create_access_token, decode_token
from shared.utils.rbac_models import access_from
tok = create_access_token({"sub": "u-1", "sid": "s-1", "org_id": "o-1", "role": "employee"})
other = create_access_token({"sub": "u-2", "sid": "s-1", "org_id": "o-1", "role": "employee"})
c = decode_token(tok)
acc = access_from("employee", [{"key": "leave_desk", "name": "Leave desk", "base_role": "hr_manager", "reach": "org", "permissions": ["leave.view"], "status": "active"}])
g = ui_grant.sign(c, acc)
n = k.public_key().public_numbers()
b = lambda v: base64.urlsafe_b64encode(v.to_bytes(32, "big")).rstrip(b"=").decode()
json.dump({"kid": "fx", "jwk": {"kty": "EC", "crv": "P-256", "x": b(n.x), "y": b(n.y)}, "token": tok, "other_token": other, "grant": g, "role": "employee"}, open(sys.argv[1], "w"))
