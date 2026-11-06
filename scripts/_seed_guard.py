"""Demo / e2e seed scripts write MAPLE-style demo data. They must never reach a production stack
(go-live 2026-10-24): refuse unless the target gateway is on this machine, and never when this shell
says LEXORA_ENV=production. LEXORA_SEED_ALLOW_REMOTE=1 is the explicit opt-in for a remote DEMO stack.
(Second line of defence: a production auth-service refuses to boot while demo orgs / logins exist.)"""
import os
from urllib.parse import urlsplit

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "api-gateway"}


def refuse_unless_local(gateway_url: str) -> None:
    if (os.getenv("LEXORA_ENV") or "").strip().lower() in ("production", "prod"):
        raise SystemExit("Refusing to seed demo data: LEXORA_ENV=production in this shell.")
    host = (urlsplit(gateway_url).hostname or "").lower()
    if host not in LOCAL_HOSTS and (os.getenv("LEXORA_SEED_ALLOW_REMOTE") or "") != "1":
        raise SystemExit(f"Refusing to seed demo data into a non-local gateway ({host}). "
                         "Demo seeds are for the local stack; set LEXORA_SEED_ALLOW_REMOTE=1 only for a demo server.")
