"""Hiring — pure rules (no DB, no HTTP), so every rule is unit-tested.

State machines for the requisition / application / offer, who may act,
duplicate-candidate detection, the single-use offer token, upload sniffing,
the public rate limiter and the report maths.
"""
from __future__ import annotations

import hashlib
import io
import re
import secrets
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

HR_ROLES = {"superadmin", "org_admin", "hr_admin", "hr_manager"}
HR_HEAD_ROLES = {"superadmin", "org_admin", "hr_admin"}        # "HR head / admin"


class RuleError(Exception):
    """A business rule said no. `status` is the HTTP status the router returns."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Actor:
    org: str = ""
    user_id: str = ""
    role: str = ""
    employee_id: str = ""
    email: str = ""
    name: str = ""

    @property
    def is_hr(self) -> bool:
        return self.role in HR_ROLES

    @property
    def is_hr_head(self) -> bool:
        return self.role in HR_HEAD_ROLES


# ── Settings ────────────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    # manager_then_hr | hr_only | none
    "requisition_approval": "manager_then_hr",
    # An offer above the requisition's budgeted maximum needs a second, different approver.
    "offer_second_approver_above_budget": True,
    # Interview rounds that must be complete before a candidate can be marked Selected.
    "required_interview_rounds": 1,
    "offer_valid_days": 7,
    "careers_page_enabled": False,
    "privacy_text": ("We use the details you give here only to assess your application for this job. "
                     "We keep them for up to 12 months and you may ask us to delete them at any time."),
    "resume_max_mb": 5,
    # ── D2: careers page branding + recruiting depth ──
    "careers_tagline": "",                      # one line under the company name
    "careers_about": "",                        # a paragraph about the company (plain text)
    "careers_primary_color": "#6522d6",         # buttons / accents on the public page
    "careers_whatsapp_text": ("You may also send me updates about this application on WhatsApp. "
                              "I can opt out at any time by replying STOP."),
    "scorecard_hide_names": False,              # bias guard: interviewers see no candidate name
    "interview_slot_link_days": 5,              # how long a self-schedule link stays open
    "interview_reminder_hours": 24,             # reminder to panel + candidate before the round
    "referral_default_bonus": 0,                # $, used when a requisition has no rule of its own
    "referral_default_days": 90,                # payable this many days after the hire joins
    "offer_approval_chain": True,               # route offers through the approval chain (D1 registry / default)
    # ── G6 (2026-10-23): AI assist + partner job-board feeds ──
    "ai_assist": True,                          # use the AI platform (when it has a key) on top of the rules; rules always run
    "linkedin_company_id": "",                  # numeric LinkedIn Page id (LinkedIn feed: mandatory for an ATS)
    "linkedin_poster_email": "",                # recruiting mailbox LinkedIn verifies the poster with (no personal e-mail)
}
_REQ_MODES = ("manager_then_hr", "hr_only", "none")
_D2_INT = {"interview_slot_link_days": (1, 30), "interview_reminder_hours": (1, 168), "referral_default_days": (0, 365)}
_D2_BOOL = ("scorecard_hide_names", "offer_approval_chain")
_D2_TEXT = ("careers_tagline", "careers_about", "careers_whatsapp_text")
_G6_BOOL = ("ai_assist",)


def merge_settings(saved: dict | None) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for k, v in (saved or {}).items():
        if k in out and v is not None:
            out[k] = v
    return out


def clean_settings(body: dict) -> dict:
    """Validate a settings PATCH. Unknown keys are dropped."""
    out = {}
    for k, v in (body or {}).items():
        if k not in DEFAULT_SETTINGS:
            continue
        if k == "requisition_approval":
            if v not in _REQ_MODES:
                raise RuleError("Requisition approval must be one of: " + ", ".join(_REQ_MODES), 422)
        elif k in ("required_interview_rounds", "offer_valid_days", "resume_max_mb"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise RuleError(f"{k} must be a whole number", 422)
            lo, hi = {"required_interview_rounds": (0, 10), "offer_valid_days": (1, 90),
                      "resume_max_mb": (1, 10)}[k]
            if not lo <= v <= hi:
                raise RuleError(f"{k} must be between {lo} and {hi}", 422)
        elif k in ("offer_second_approver_above_budget", "careers_page_enabled"):
            v = bool(v)
        elif k == "privacy_text":
            v = str(v or "").strip()
            if not v:
                raise RuleError("The privacy text cannot be empty", 422)
        elif k in _D2_INT:
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise RuleError(f"{k} must be a whole number", 422)
            lo, hi = _D2_INT[k]
            if not lo <= v <= hi:
                raise RuleError(f"{k} must be between {lo} and {hi}", 422)
        elif k in _D2_BOOL:
            v = bool(v)
        elif k in _G6_BOOL:
            v = bool(v)
        elif k == "linkedin_company_id":
            v = str(v or "").strip()
            if v and not re.fullmatch(r"\d{1,15}", v):
                raise RuleError("The LinkedIn company id is the number in your LinkedIn Page admin URL", 422)
        elif k == "linkedin_poster_email":
            v = str(v or "").strip().lower()[:200]
            if v and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", v):
                raise RuleError("The poster e-mail does not look like an e-mail address", 422)
        elif k in _D2_TEXT:
            v = str(v or "").strip()[:2000]
        elif k == "careers_primary_color":
            v = str(v or "").strip().lower()
            if not re.fullmatch(r"#[0-9a-f]{6}", v):
                raise RuleError("The colour must look like #6522d6", 422)
        elif k == "referral_default_bonus":
            try:
                v = round(float(v or 0), 2)
            except (TypeError, ValueError):
                raise RuleError("The referral bonus must be a number", 422)
            if v < 0:
                raise RuleError("The referral bonus cannot be negative", 422)
        out[k] = v
    return out


# ── Requisition state machine ───────────────────────────────────────────────
REQ_STATUSES = ("draft", "pending_approval", "open", "on_hold", "filled", "cancelled")
REQ_TRANSITIONS = {
    # action: (allowed from, to)
    "submit": (("draft",), "pending_approval"),
    "approve": (("pending_approval",), "open"),
    "reject": (("pending_approval",), "draft"),
    "hold": (("open",), "on_hold"),
    "resume": (("on_hold",), "open"),
    "cancel": (("draft", "pending_approval", "open", "on_hold"), "cancelled"),
    "fill": (("open",), "filled"),          # system: joined count reached the openings
    "reopen": (("filled",), "open"),
}
REQ_REASON_REQUIRED = {"reject", "hold", "cancel", "reopen"}


def req_steps(mode: str) -> list[str]:
    """The approval steps a requisition goes through, in order."""
    return {"manager_then_hr": ["manager", "hr"], "hr_only": ["hr"], "none": []}.get(mode, ["manager", "hr"])


def req_next(status: str, action: str) -> str:
    if action not in REQ_TRANSITIONS:
        raise RuleError(f"Unknown action '{action}'", 422)
    allowed, to = REQ_TRANSITIONS[action]
    if status not in allowed:
        raise RuleError(f"A requisition that is {status.replace('_', ' ')} cannot be {_past(action)}.")
    return to


def _past(action: str) -> str:
    return {"submit": "submitted", "approve": "approved", "reject": "rejected", "hold": "put on hold",
            "resume": "resumed", "cancel": "cancelled", "fill": "filled", "reopen": "reopened",
            "send": "sent", "accept": "accepted", "decline": "declined", "withdraw": "withdrawn",
            "expire": "expired", "edit": "edited"}.get(action, action)


def req_can_act(action: str, actor: Actor, req: dict) -> bool:
    """Who may do what on a requisition.
    `req` needs: created_by, hiring_manager_id, approval_step."""
    owner = bool(actor.user_id) and str(req.get("created_by") or "") == actor.user_id
    is_hm = bool(actor.employee_id) and str(req.get("hiring_manager_id") or "") == actor.employee_id
    if action in ("edit", "submit"):
        return actor.is_hr or owner or is_hm
    if action in ("approve", "reject"):
        step = req.get("approval_step")
        if step == "manager":
            return is_hm or actor.is_hr_head          # an HR head may act for an absent manager
        return actor.is_hr_head
    if action == "cancel":
        return actor.is_hr or ((owner or is_hm) and req.get("status") in ("draft", "pending_approval"))
    if action in ("hold", "resume", "reopen"):
        return actor.is_hr
    return False                                      # "fill" is system-only


def req_submit(mode: str, actor: Actor, req: dict) -> tuple[str, str | None]:
    """Status + approval step right after Submit. A hiring manager who submits
    their own requisition has already said yes, so their step is skipped."""
    steps = req_steps(mode)
    is_hm = bool(actor.employee_id) and str(req.get("hiring_manager_id") or "") == actor.employee_id
    if steps and steps[0] == "manager" and (is_hm or not req.get("hiring_manager_id")):
        steps = steps[1:]
    if not steps:
        return "open", None
    return "pending_approval", steps[0]


def req_approve(mode: str, step: str | None) -> tuple[str, str | None]:
    """Status + next step after an approval at `step`."""
    steps = req_steps(mode)
    if step in steps and steps.index(step) + 1 < len(steps):
        return "pending_approval", steps[steps.index(step) + 1]
    return "open", None


# ── Application pipeline ────────────────────────────────────────────────────
PIPELINE = ("applied", "screening", "interview", "selected", "offer", "joined")
CLOSED_STAGES = ("rejected", "withdrawn")
STAGES = PIPELINE + CLOSED_STAGES + ("on_hold",)
STAGE_LABEL = {"applied": "Applied", "screening": "Screening", "interview": "Interview",
               "selected": "Selected", "offer": "Offer", "joined": "Joined", "rejected": "Rejected",
               "withdrawn": "Withdrawn", "on_hold": "On hold"}
MANUAL_STAGES = ("applied", "screening", "interview", "selected")


def return_stage(prev: str | None) -> str:
    """Where a held / rejected / withdrawn application comes back to."""
    if prev in MANUAL_STAGES:
        return prev
    return "selected" if prev == "offer" else "applied"


def app_move(frm: str, to: str, *, prev: str | None = None, reason: str | None = None,
             rounds_done: int = 0, rounds_required: int = 0, system: bool = False) -> dict:
    """Check one stage move. Returns {"stage", "prev_stage"} or raises RuleError."""
    if to not in STAGES:
        raise RuleError(f"Unknown stage '{to}'", 422)
    if frm == to:
        raise RuleError(f"The candidate is already in {STAGE_LABEL[to]}.")
    if frm == "joined":
        raise RuleError("This person has joined. The application is closed.")
    if to in ("offer", "joined") and not system:
        raise RuleError("Offer and Joined are set by the offer and by onboarding, not by hand."
                        if to == "joined" else "Create an offer to move a candidate to Offer.")
    if frm in CLOSED_STAGES or frm == "on_hold":
        # Only way forward again: back to where the candidate was.
        back = return_stage(prev)
        if to == back:
            return {"stage": to, "prev_stage": None}
        if frm == "on_hold" and to in CLOSED_STAGES:
            if to == "rejected" and not (reason or "").strip():
                raise RuleError("Give a reason for the rejection.", 422)
            return {"stage": to, "prev_stage": prev}
        if frm == "on_hold":
            raise RuleError(f"Bring the candidate back to {STAGE_LABEL[back]} first.")
        raise RuleError(f"A {STAGE_LABEL[frm].lower()} application can only be reopened to {STAGE_LABEL[back]}.")
    if frm == "offer" and not system:
        raise RuleError("This candidate has an offer. Withdraw the offer before moving the candidate.")
    if to == "rejected" and not (reason or "").strip():
        raise RuleError("Give a reason for the rejection.", 422)
    if to in CLOSED_STAGES or to == "on_hold":
        return {"stage": to, "prev_stage": frm}
    if to == "selected" and not system and rounds_done < rounds_required:
        raise RuleError(f"{rounds_required} interview round(s) with all feedback are needed before "
                        f"selecting. Done so far: {rounds_done}.")
    return {"stage": to, "prev_stage": None}


def rounds_done(interviews: list[dict], feedback: list[dict]) -> int:
    """A round is done when it is not cancelled, has a panel, and EVERY panel
    member has submitted feedback."""
    by_iv: dict[str, set] = defaultdict(set)
    for f in feedback:
        by_iv[str(f["interview_id"])].add(str(f["interviewer_employee_id"]))
    done = 0
    for iv in interviews:
        if iv.get("status") == "cancelled":
            continue
        panel = {str(p.get("employee_id")) for p in (iv.get("panel") or []) if p.get("employee_id")}
        if panel and panel <= by_iv[str(iv["id"])]:
            done += 1
    return done


def is_panel_member(interview: dict, employee_id: str) -> bool:
    return bool(employee_id) and interview.get("status") != "cancelled" and any(
        str(p.get("employee_id")) == str(employee_id) for p in (interview.get("panel") or []))


def interviewer_application_ids(interviews: list[dict], employee_id: str) -> set[str]:
    """The applications a panel member may look at — only the ones they interview."""
    return {str(iv["application_id"]) for iv in interviews if is_panel_member(iv, employee_id)}


def clean_feedback(body: dict) -> dict:
    try:
        rating = int(body.get("rating"))
    except (TypeError, ValueError):
        raise RuleError("Give a rating from 1 to 5.", 422)
    if not 1 <= rating <= 5:
        raise RuleError("Give a rating from 1 to 5.", 422)
    rec = body.get("recommendation")
    if rec not in ("hire", "no_hire", "hold"):
        raise RuleError("Choose Hire, No hire or Hold.", 422)
    return {"rating": rating, "recommendation": rec,
            "strengths": (body.get("strengths") or "").strip() or None,
            "concerns": (body.get("concerns") or "").strip() or None}


# ── Duplicate candidates ────────────────────────────────────────────────────
def norm_email(email: str | None) -> str | None:
    e = (email or "").strip().lower()
    return e or None


def norm_phone(phone: str | None) -> str | None:
    """Digits only, last 10 — so +91 98765 43210 and 09876543210 compare equal."""
    d = re.sub(r"\D", "", phone or "")
    if len(d) < 7:
        return None
    return d[-10:]


def find_duplicates(email: str | None, phone: str | None, others: list[dict],
                    exclude_id: str | None = None) -> list[dict]:
    """Possible duplicates of a person among `others` ({id, email_norm, phone_norm, ...}).
    Returns [{id, name, match: ["email"|"phone"]}]. This only REPORTS — the caller
    never merges on its own."""
    e, p = norm_email(email), norm_phone(phone)
    out = []
    for o in others:
        if exclude_id and str(o.get("id")) == str(exclude_id):
            continue
        match = []
        if e and o.get("email_norm") == e:
            match.append("email")
        if p and o.get("phone_norm") == p:
            match.append("phone")
        if match:
            out.append({"id": str(o.get("id")), "name": o.get("name"), "match": match})
    return out


# ── Offer ───────────────────────────────────────────────────────────────────
OFFER_STATUSES = ("draft", "approved", "sent", "accepted", "declined", "expired", "withdrawn")
OFFER_TRANSITIONS = {
    "approve": (("draft",), "approved"),
    "send": (("approved",), "sent"),
    "accept": (("sent",), "accepted"),
    "decline": (("sent",), "declined"),
    "expire": (("sent",), "expired"),
    "withdraw": (("draft", "approved", "sent"), "withdrawn"),
}


def offer_next(status: str, action: str) -> str:
    if action not in OFFER_TRANSITIONS:
        raise RuleError(f"Unknown action '{action}'", 422)
    allowed, to = OFFER_TRANSITIONS[action]
    if status not in allowed:
        raise RuleError(f"An offer that is {status} cannot be {_past(action)}.")
    return to


def offer_above_budget(annual_ctc: float, budget_max: float | None) -> bool:
    return bool(budget_max) and float(annual_ctc or 0) > float(budget_max)


def offer_approvals_required(above_budget: bool, settings: dict) -> int:
    return 2 if (above_budget and settings.get("offer_second_approver_above_budget", True)) else 1


def offer_can_act(action: str, actor: Actor) -> bool:
    if action == "approve":
        return actor.is_hr_head
    return actor.is_hr                      # create / edit / send / withdraw / record response


def offer_approve(actor: Actor, approvals: list[dict], required: int) -> tuple[list[dict], str]:
    """Add this actor's approval. Returns (approvals, status).
    The second approver must be a different person from the first."""
    if not offer_can_act("approve", actor):
        raise RuleError("Only an HR head or an org admin may approve an offer.", 403)
    if any(str(a.get("by")) == actor.user_id for a in approvals):
        raise RuleError("You have already approved this offer. A second, different approver is needed.")
    out = list(approvals) + [{"by": actor.user_id, "name": actor.name or actor.email, "role": actor.role,
                              "at": datetime.now(timezone.utc).isoformat()}]
    return out, ("approved" if len(out) >= required else "draft")


def new_token() -> tuple[str, str]:
    """(raw token for the link, hash to store). Only the hash is kept."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256((raw or "").encode()).hexdigest()


def token_expiry(now: datetime, valid_days: int) -> datetime:
    return now + timedelta(days=int(valid_days or 7))


def token_state(status: str, expires_at: datetime | None, used_at: datetime | None,
                now: datetime | None = None) -> str:
    """ok | used | expired | closed — what a tokenised offer link may still do."""
    now = now or datetime.now(timezone.utc)
    if used_at is not None or status in ("accepted", "declined"):
        return "used"
    if status in ("expired",) or (status == "sent" and expires_at is not None and expires_at < now):
        return "expired"
    if status != "sent":
        return "closed"
    return "ok"


# ── Uploads ─────────────────────────────────────────────────────────────────
# What a candidate document is (hire_documents.kind, String(20)). Anything else is stored as "document".
DOC_KINDS = {"resume": "Résumé", "id_proof": "ID proof", "education": "Education certificate",
             "experience": "Experience / relieving letter", "payslip": "Payslip", "document": "Other document"}


def doc_kind(kind: str | None) -> str:
    k = str(kind or "").strip().lower()
    return k if k in DOC_KINDS else "document"


ALLOWED_UPLOADS = {
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def sniff_upload(content: bytes) -> tuple[str, str] | None:
    """Decide the file type from its first bytes — never from the name or the
    browser's content type. Returns (ext, content_type) or None when not allowed."""
    if not content:
        return None
    if content[:5] == b"%PDF-":
        return "pdf", ALLOWED_UPLOADS["pdf"]
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", ALLOWED_UPLOADS["png"]
    if content[:3] == b"\xff\xd8\xff":
        return "jpg", ALLOWED_UPLOADS["jpg"]
    if content[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                names = set(z.namelist())
        except (zipfile.BadZipFile, ValueError, OSError):
            return None
        if "word/document.xml" in names and "[Content_Types].xml" in names \
                and not any(n.lower().endswith("vbaproject.bin") for n in names):
            return "docx", ALLOWED_UPLOADS["docx"]
    return None


def check_upload(content: bytes, max_mb: int) -> tuple[str, str]:
    if not content:
        raise RuleError("The file is empty.", 422)
    if len(content) > int(max_mb) * 1024 * 1024:
        raise RuleError(f"The file is too large. The limit is {int(max_mb)} MB.", 413)
    kind = sniff_upload(content)
    if not kind:
        raise RuleError("Only PDF, DOCX, JPG or PNG files are accepted.", 415)
    return kind


def safe_file_name(name: str | None, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", (name or "file").rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    base = base.rsplit(".", 1)[0][:80].strip(" .") or "file"
    return f"{base}.{ext}"


# ── Public rate limit ───────────────────────────────────────────────────────
class RateLimiter:
    """Sliding window, in memory (per process). Enough to stop a script hammering
    the public apply form; a multi-replica deployment needs a shared store."""

    def __init__(self, max_calls: int, window_seconds: int):
        self.max, self.window = max_calls, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, now: float) -> bool:
        q = self._hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.max:
            return False
        q.append(now)
        if len(self._hits) > 20000:                # keep memory bounded
            for k in [k for k, v in self._hits.items() if not v][:5000]:
                self._hits.pop(k, None)
        return True


# ── Reports ─────────────────────────────────────────────────────────────────
def _d(v):
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v)) if v else None


def _days(a, b) -> float | None:
    a, b = _d(a), _d(b)
    if not a or not b:
        return None
    return round((b - a).total_seconds() / 86400, 1)


def _avg(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def filled_counts(applications: list[dict], offers: list[dict]) -> dict[str, dict]:
    """Per requisition: offers accepted and people joined (joined = activated as
    an employee). Both come from real rows, never typed in."""
    out: dict[str, dict] = defaultdict(lambda: {"accepted": 0, "joined": 0})
    for o in offers:
        if o.get("status") == "accepted":
            out[str(o["requisition_id"])]["accepted"] += 1
    for a in applications:
        if a.get("stage") == "joined":
            out[str(a["requisition_id"])]["joined"] += 1
    return out


def report(requisitions: list[dict], applications: list[dict], offers: list[dict],
           history: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    counts = filled_counts(applications, offers)
    # openings vs filled + pipeline by requisition
    by_req_stage: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for a in applications:
        by_req_stage[str(a["requisition_id"])][a["stage"]] += 1
    openings = []
    for r in requisitions:
        rid = str(r["id"])
        c = counts.get(rid, {"accepted": 0, "joined": 0})
        openings.append({
            "requisition_id": rid, "req_number": r.get("req_number"), "designation": r.get("designation"),
            "department": r.get("department"), "status": r.get("status"), "openings": int(r.get("openings") or 0),
            "accepted": c["accepted"], "joined": c["joined"],
            "remaining": max(int(r.get("openings") or 0) - c["joined"], 0),
            "pipeline": {s: by_req_stage[rid].get(s, 0) for s in STAGES},
            "days_open": _days(r.get("opened_at"), r.get("filled_at") or now) if r.get("opened_at") else None,
        })
    # time to fill: opened → filled, filled requisitions only
    ttf = [_days(r.get("opened_at"), r.get("filled_at")) for r in requisitions
           if r.get("status") == "filled" and r.get("opened_at") and r.get("filled_at")]
    # time in stage: from the history, each stay = entry → next move (or now)
    by_app: dict[str, list] = defaultdict(list)
    for h in history:
        by_app[str(h["application_id"])].append(h)
    stays: dict[str, list] = defaultdict(list)
    for rows in by_app.values():
        rows.sort(key=lambda h: _d(h["at"]))
        for i, h in enumerate(rows):
            end = rows[i + 1]["at"] if i + 1 < len(rows) else (None if h["to_stage"] in CLOSED_STAGES + ("joined",) else now)
            d = _days(h["at"], end) if end else None
            if d is not None:
                stays[h["to_stage"]].append(d)
    time_in_stage = [{"stage": s, "label": STAGE_LABEL[s], "avg_days": _avg(stays.get(s, [])),
                      "moves": len(stays.get(s, []))} for s in STAGES if s not in CLOSED_STAGES + ("joined",)]
    # source mix
    src: dict[str, dict] = defaultdict(lambda: {"applications": 0, "joined": 0})
    for a in applications:
        k = a.get("source_type") or "walk_in"
        src[k]["applications"] += 1
        if a.get("stage") == "joined":
            src[k]["joined"] += 1
    total = len(applications)
    source_mix = [{"source": k, **v, "share_pct": round(100 * v["applications"] / total, 1) if total else 0}
                  for k, v in sorted(src.items(), key=lambda kv: -kv[1]["applications"])]
    # offer acceptance: accepted / (accepted + declined + expired) — offers that got an answer or lapsed
    acc = sum(1 for o in offers if o.get("status") == "accepted")
    dec = sum(1 for o in offers if o.get("status") == "declined")
    exp = sum(1 for o in offers if o.get("status") == "expired")
    decided = acc + dec + exp
    return {
        "openings": openings,
        "totals": {"openings": sum(o["openings"] for o in openings), "accepted": sum(o["accepted"] for o in openings),
                   "joined": sum(o["joined"] for o in openings), "applications": total},
        "time_to_fill": {"avg_days": _avg(ttf), "filled_requisitions": len(ttf)},
        "time_in_stage": time_in_stage,
        "source_mix": source_mix,
        "offer_acceptance": {"accepted": acc, "declined": dec, "expired": exp, "decided": decided,
                             "pending": sum(1 for o in offers if o.get("status") == "sent"),
                             "rate_pct": round(100 * acc / decided, 1) if decided else None},
    }
