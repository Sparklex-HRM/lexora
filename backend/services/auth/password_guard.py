"""Auth service — password checks beyond the shape rules in security_policy.py.

1. Breach list, OFFLINE (no password or hash prefix ever leaves the server):
   * a bundled list of the most common leaked passwords (data/common_passwords.txt),
     compared case-insensitively, also with trailing digits / symbols stripped;
   * optionally a full offline breach corpus: LEXORA_BREACHED_PASSWORDS_FILE
     pointing at a SORTED file of upper-case SHA-1 hex hashes, one per line,
     optionally ``HASH:count`` (the "Have I Been Pwned" Pwned Passwords
     download format, ordered by hash). It is binary-searched on disk, so a
     multi-GB file costs no memory. Not shipped — the operator downloads it.
2. History: the new password may not equal any of the last
   LEXORA_PASSWORD_HISTORY (default 5) passwords of that login.
"""
from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

from shared.utils.security import verify_password

HISTORY_DEPTH = int(os.getenv("LEXORA_PASSWORD_HISTORY", "5"))
_DATA = Path(__file__).with_name("data") / "common_passwords.txt"


@lru_cache(maxsize=1)
def _common() -> frozenset[str]:
    try:
        return frozenset(ln.strip().lower() for ln in _DATA.read_text(encoding="utf-8").splitlines()
                         if ln.strip() and not ln.startswith("#"))
    except OSError:
        return frozenset()


def _variants(pw: str) -> set[str]:
    p = pw.lower()
    core = re.sub(r"[\d\W_]+$", "", p)          # "Password@123" → "password"
    lead = re.sub(r"^[\d\W_]+", "", core)
    return {p, core, lead} - {""}


def _sha1_in_file(path: str, digest: str) -> bool:
    """Binary search a sorted 'HASH[:count]' file for an upper-case SHA-1 hex."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    with open(path, "rb") as f:
        lo, hi = 0, size
        while lo < hi:
            mid = (lo + hi) // 2
            f.seek(mid)
            if mid:
                f.readline()                      # move to the next full line
            line = f.readline()
            if not line:
                hi = mid
                continue
            h = line.split(b":", 1)[0].strip().decode("ascii", "ignore").upper()
            if h == digest:
                return True
            if h < digest:
                lo = mid + 1
            else:
                hi = mid
        # the first line is never examined when mid > 0 — check it directly
        f.seek(0)
        first = f.readline().split(b":", 1)[0].strip().decode("ascii", "ignore").upper()
        return first == digest


def breach_source() -> dict:
    corpus = os.getenv("LEXORA_BREACHED_PASSWORDS_FILE", "").strip()
    return {"bundled_common_list": len(_common()),
            "offline_corpus": corpus if corpus and os.path.exists(corpus) else None,
            "offline_corpus_configured": bool(corpus and os.path.exists(corpus))}


def is_breached(pw: str) -> bool:
    if not pw:
        return False
    common = _common()
    if any(v in common for v in _variants(pw)):
        return True
    corpus = os.getenv("LEXORA_BREACHED_PASSWORDS_FILE", "").strip()
    if corpus and os.path.exists(corpus):
        return _sha1_in_file(corpus, hashlib.sha1(pw.encode("utf-8")).hexdigest().upper())
    return False


def breach_problems(pw: str) -> list[str]:
    return ["not a password found in known data breaches"] if is_breached(pw) else []


def reused(pw: str, old_hashes: list[str]) -> bool:
    """True if `pw` matches any of the given bcrypt hashes (newest first)."""
    return any(verify_password(pw, h) for h in old_hashes[:HISTORY_DEPTH] if h)
