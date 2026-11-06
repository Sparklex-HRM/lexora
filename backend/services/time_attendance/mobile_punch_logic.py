"""Mobile punch — pure rules (no database, no HTTP). Stream G4.

Everything a phone punch is judged by lives here so it can be unit-tested:
  * distance to a work site and the nearest / preferred geo-fence
  * the org's policy: outside the fence → allowed-with-flag or refused,
    mock location → refused, poor GPS accuracy → flagged or refused,
    selfie optional / required, device binding on / off
  * "synced late": a punch recorded offline carries the DEVICE time; the server
    keeps both times and marks the punch late when they differ by more than the
    org's threshold (default 2 minutes)
  * device fingerprints (a short, stable hash of what the browser reports)

Refusal sentences are plain words for the person holding the phone.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt

OUTSIDE_FENCE = ("flag", "refuse")
SELFIE_MODES = ("off", "optional", "required")

DEFAULT_SETTINGS = {
    "outside_fence": "flag",      # flag | refuse
    "selfie": "optional",         # off | optional | required
    "device_binding": True,       # first phone registers itself; a new phone needs HR approval
    "refuse_mock": True,          # a browser that says the location is mocked is refused
    "max_accuracy_m": 200,        # GPS accuracy worse than this → flagged (0 = no check)
    "refuse_poor_accuracy": False,  # … or refused
    "late_sync_minutes": 2,       # device time vs server time gap that counts as "synced late"
    "selfie_max_kb": 1536,        # selfie size cap (JPG/PNG), stored in the database
}


def merged_settings(stored: dict | None) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for k, v in (stored or {}).items():
        if k in out and v is not None:
            out[k] = v
    if out["outside_fence"] not in OUTSIDE_FENCE:
        out["outside_fence"] = "flag"
    if out["selfie"] not in SELFIE_MODES:
        out["selfie"] = "optional"
    return out


def clean_settings(value: dict) -> dict:
    """Validate an HR save. Raises ValueError with a plain sentence."""
    if not isinstance(value, dict):
        raise ValueError("The settings must be an object")
    out = merged_settings(value)
    if value.get("outside_fence") not in (None, *OUTSIDE_FENCE):
        raise ValueError("Outside the work site must be 'flag' or 'refuse'")
    if value.get("selfie") not in (None, *SELFIE_MODES):
        raise ValueError("Selfie must be off, optional or required")
    for k in ("max_accuracy_m", "late_sync_minutes", "selfie_max_kb"):
        try:
            out[k] = int(value.get(k, out[k]) or 0)
        except (TypeError, ValueError):
            raise ValueError(f"{k.replace('_', ' ')} must be a whole number")
        if out[k] < 0:
            raise ValueError(f"{k.replace('_', ' ')} cannot be negative")
    if out["selfie_max_kb"] > 5120:
        raise ValueError("The selfie limit cannot be more than 5120 KB")
    for k in ("device_binding", "refuse_mock", "refuse_poor_accuracy"):
        out[k] = bool(value.get(k, out[k]))
    return out


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * r * asin(sqrt(a))


def pick_fence(lat: float | None, lng: float | None, fences: list[dict], preferred_name: str | None = None):
    """(fence, distance_m) — the employee's own work site when its name matches a fence,
    otherwise the nearest active fence. (None, None) when there is nothing to compare."""
    active = [f for f in fences if f.get("active", True) and f.get("lat") is not None and f.get("lng") is not None]
    if lat is None or lng is None or not active:
        return None, None
    pref = (preferred_name or "").strip().lower()
    if pref:
        for f in active:
            if (f.get("name") or "").strip().lower() == pref:
                return f, round(haversine_m(lat, lng, float(f["lat"]), float(f["lng"])))
    best, best_d = None, None
    for f in active:
        d = haversine_m(lat, lng, float(f["lat"]), float(f["lng"]))
        if best_d is None or d < best_d:
            best, best_d = f, d
    return best, round(best_d)


def evaluate_punch(settings: dict, *, lat, lng, accuracy_m, mock_location: bool, fence: dict | None,
                   distance_m, device_state: str | None, has_selfie: bool) -> dict:
    """Judge one punch. Returns {allowed, refusal, flags, inside_fence}.

    device_state: "active" (known phone) | "pending" (waiting for HR) | "new" (not seen) |
                  None (binding off / not applicable)
    """
    s = merged_settings(settings)
    flags: list[str] = []
    refusal = None
    inside = None

    if lat is None or lng is None:
        flags.append("no_location")
    else:
        if mock_location:
            if s["refuse_mock"]:
                refusal = "Your phone reports a mock location. Switch mock locations off and try again."
            flags.append("mock_location")
        if s["max_accuracy_m"] and accuracy_m is not None and accuracy_m > s["max_accuracy_m"]:
            flags.append("poor_accuracy")
            if s["refuse_poor_accuracy"] and not refusal:
                refusal = (f"Your location is only accurate to about {int(accuracy_m)} m. "
                           f"Move to open sky or switch on precise location and try again.")
        if fence is not None and distance_m is not None:
            inside = distance_m <= int(fence.get("radius_m") or 0)
            if not inside:
                flags.append("outside_fence")
                if s["outside_fence"] == "refuse" and not refusal:
                    refusal = (f"You are about {int(distance_m)} m from {fence.get('name') or 'the work site'}. "
                               f"Punches are only accepted inside the site area.")
        else:
            flags.append("no_fence")

    if s["device_binding"] and device_state in ("pending", "new") and not refusal:
        refusal = ("This phone is not the one registered for you. HR has been asked to approve it. "
                   "Until then, punch from your registered phone.")
        flags.append("device_pending")

    if s["selfie"] == "required" and not has_selfie and not refusal:
        refusal = "A selfie is required with every punch. Take one and try again."
        flags.append("no_selfie")

    return {"allowed": refusal is None, "refusal": refusal, "flags": flags, "inside_fence": inside}


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def late_sync(device_time: datetime | None, server_time: datetime, threshold_minutes: int) -> tuple[bool, int]:
    """(synced_late, minutes_late). A device time ahead of the server counts as 0."""
    if device_time is None:
        return False, 0
    gap = int((server_time - device_time).total_seconds() // 60)
    if gap < 0:
        gap = 0
    return gap > max(int(threshold_minutes or 0), 0), gap


def device_fingerprint(raw: str | None) -> str:
    """A 24-hex device id from whatever the browser hands over (never stored raw)."""
    return hashlib.sha256((raw or "").encode()).hexdigest()[:24]


def clamp_coord(v, lo: float, hi: float):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f < lo or f > hi:          # NaN or out of range
        return None
    return round(f, 6)


def flag_labels(flags: list[str]) -> list[str]:
    names = {
        "no_location": "No location", "mock_location": "Mock location", "poor_accuracy": "Weak GPS",
        "outside_fence": "Outside site", "no_fence": "No site set", "device_pending": "New phone",
        "no_selfie": "No selfie", "synced_late": "Synced late", "duplicate": "Duplicate",
    }
    return [names.get(f, f) for f in flags]
