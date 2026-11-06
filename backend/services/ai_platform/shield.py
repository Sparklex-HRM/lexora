"""De-identify shield — nothing personal or financial reaches the language model.

Every prompt, every piece of chat history and every tool result passes through a
``Shield`` BEFORE it is sent to the LLM. The shield swaps identifiers and money for
stable placeholders such as ``[[SIN_1]]`` or ``[[MONEY_3]]``; the model reasons over
the placeholders; the reply is passed back through ``restore()`` so the person sees the
real values. The map from placeholder to value lives only in memory for one request.

What is tokenised
  * SIN (123 456 789 / 123-456-789), Canadian postal codes, bank-account-looking numbers
  * e-mail addresses and North American phone numbers ((416) 555-0142, +1 416 555 0142, 4165550142)
  * any money amount (currency symbol or code followed by a number)
  * any grouped number (120,000.00) and any bare number of 5+ digits
  * in structured tool results: every value under a money-like key, and every
    numeric value of 1,000 or more (salary, gross, net, balances, rates …)

What is left alone: short numbers (days, hours, counts, percentages), dates, names
of leave types, statuses and plain words. Dates like 2026-10-13 are kept so the
model can reason about periods.

Pure Python — no I/O — so it is unit-tested directly.
"""
import re

# Order matters: the longest / most specific first so a 10-digit phone is not cut into a
# 9-digit SIN. Each entry: (label, compiled pattern)
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("MONEY", re.compile(r"(?:\$|€|£|\b(?:CAD|USD|C\$)\s?)\s?\d[\d,]*(?:\.\d+)?", re.IGNORECASE)),
    ("PHONE", re.compile(r"(?:\+?1[ .-]?)?(?:\(\d{3}\)|\b\d{3})[ .-]?\d{3}[ .-]?\d{4}\b")),
    ("SIN", re.compile(r"\b\d{3}[ -]?\d{3}[ -]?\d{3}\b")),
    ("POSTAL", re.compile(r"\b[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z] ?\d[ABCEGHJ-NPRSTV-Z]\d\b")),
    ("NUMBER", re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{5,}(?:\.\d+)?\b")),
]

# Keys in a tool result whose values are money (always masked, whatever the size).
MONEY_KEYS = {"gross", "net", "net_pay", "amount", "salary", "annual_salary", "hourly_rate", "allowance", "pay", "tax",
              "income_tax", "federal_tax", "provincial_tax", "cpp", "cpp2", "qpp", "qpp2", "ei", "qpip", "eht", "wcb",
              "vacation_pay", "rrsp", "union_dues", "cost", "rate", "total", "total_gross", "total_net", "old_amount",
              "new_amount", "deduction", "earning", "balance_amount", "wages", "bonus", "arrears", "retro", "value"}
# Keys whose values are identifiers (always masked).
ID_KEYS = {"sin", "bank_account", "account", "account_number", "bank_transit", "bank_institution", "email",
           "work_email", "phone", "home_phone", "emergency_phone", "postal_code", "work_permit_number", "passport"}
# Dates are kept — the model needs them to reason about months and periods.
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Record ids are kept too (see Shield.protect); parked behind a digit-free placeholder while the patterns run.
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_PARKED = re.compile("\x00([a-j]+)\x00")


def _letters(n: int) -> str:
    return "".join("abcdefghij"[int(c)] for c in str(n))


def _from_letters(s: str) -> int:
    return int("".join(str("abcdefghij".index(c)) for c in s))
NUMBER_MASK_FROM = 1000


def _money_key(key: str | None) -> bool:
    if not key:
        return False
    k = key.lower()
    if k in MONEY_KEYS:
        return True
    return any(k.endswith("_" + m) or k.startswith(m + "_") for m in ("amount", "gross", "net", "salary", "pay",
                                                                      "tax", "cost", "wage", "wages", "rate"))


def _id_key(key: str | None) -> bool:
    return bool(key) and key.lower() in ID_KEYS


class Shield:
    """One shield per request. ``protect`` masks, ``restore`` unmasks the model's reply."""

    def __init__(self):
        self._by_value: dict[str, str] = {}      # original -> token
        self._by_token: dict[str, str] = {}      # token -> original
        self._counts: dict[str, int] = {}

    # ── core ────────────────────────────────────────────────────────────────
    def token_for(self, label: str, value: str) -> str:
        value = str(value)
        if value in self._by_value:
            return self._by_value[value]
        n = self._counts.get(label, 0) + 1
        self._counts[label] = n
        tok = f"[[{label}_{n}]]"
        self._by_value[value] = tok
        self._by_token[tok] = value
        return tok

    def protect(self, text: str | None) -> str:
        """Mask everything that looks personal or financial in free text.

        Record ids (UUIDs) are kept as they are: they are not personal data, and an all-digit
        middle run such as ``…-9755-4672-8936-…`` would otherwise read as a phone or SIN and
        corrupt the id (an approval card's filing_id / a deep link stopped resolving — 2026-10-25)."""
        out = text or ""
        kept: list[str] = []

        def _park(m):
            kept.append(m.group(0))
            return f"\x00{_letters(len(kept) - 1)}\x00"

        out = _UUID.sub(_park, out)
        for label, rx in PATTERNS:
            out = rx.sub(lambda m, label=label: self.token_for(label, m.group(0)), out)
        if kept:
            out = _PARKED.sub(lambda m: kept[_from_letters(m.group(1))], out)
        return out

    def protect_value(self, obj, key: str | None = None):
        """Mask a tool result (dict / list / scalar) recursively."""
        if isinstance(obj, dict):
            return {k: self.protect_value(v, k) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.protect_value(v, key) for v in obj]
        if isinstance(obj, bool) or obj is None:
            return obj
        if isinstance(obj, (int, float)):
            if _money_key(key) or _id_key(key) or abs(obj) >= NUMBER_MASK_FROM:
                return self.token_for("MONEY" if _money_key(key) or not _id_key(key) else "ID", _num(obj))
            return obj
        if isinstance(obj, str):
            if _id_key(key) and obj.strip():
                return self.token_for("ID", obj)
            if _money_key(key) and obj.strip() and _looks_numeric(obj):
                return self.token_for("MONEY", obj)
            if _DATE.match(obj):
                return obj
            return self.protect(obj)
        return obj

    def restore(self, text: str | None) -> str:
        """Put the real values back into the model's reply."""
        out = text or ""
        if not self._by_token:
            return out
        # longest tokens first so [[MONEY_10]] is not hit by [[MONEY_1]]
        for tok in sorted(self._by_token, key=len, reverse=True):
            out = out.replace(tok, self._by_token[tok])
        return out

    def restore_value(self, obj):
        """Unmask the model's tool-call arguments (they may echo a token back)."""
        if isinstance(obj, dict):
            return {k: self.restore_value(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.restore_value(v) for v in obj]
        if isinstance(obj, str):
            return self.restore(obj)
        return obj

    @property
    def masked(self) -> int:
        return len(self._by_token)

    def summary(self) -> dict:
        return dict(self._counts)


def _num(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _looks_numeric(s: str) -> bool:
    try:
        float(s.replace(",", ""))
        return True
    except ValueError:
        return False


def leaks(text: str) -> list[str]:
    """What in ``text`` still looks like an identifier or an amount (used by tests and
    the audit row — the stored prompt must come back empty here)."""
    found = []
    for label, rx in PATTERNS:
        for m in rx.finditer(text or ""):
            found.append(f"{label}:{m.group(0)}")
    return found
