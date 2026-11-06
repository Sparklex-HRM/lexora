"""Org login codes — a short, uppercase, unique handle each tenant enters at
login (the platform/superadmin operator is exempt). Derived from the org name."""
import re


def base_code(name: str) -> str:
    """A clean, memorable handle from the org name: the first word, uppercased,
    ≤12 chars. 'Northbridge Logistics' -> 'NORTHBRIDGE', 'Acme Test Inc' -> 'ACME'.
    Very short first words borrow the next word so the code stays distinctive."""
    words = re.findall(r"[A-Za-z0-9]+", name or "")
    if not words:
        return "ORG"
    code = words[0].upper()
    if len(code) < 3 and len(words) > 1:
        code = (words[0] + words[1]).upper()
    return code[:12]


def next_code(name: str, taken: set[str]) -> str:
    """A base_code that isn't already in `taken` (uppercase set); append a counter
    on collision. Used by the backfill, which knows the whole taken set up front."""
    base = base_code(name)
    if base not in taken:
        return base
    i = 2
    while f"{base[:10]}{i}" in taken:
        i += 1
    return f"{base[:10]}{i}"
