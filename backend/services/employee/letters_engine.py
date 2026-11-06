"""Letter engine — the pure part (no database, no HTTP).

TEMPLATE SYNTAX (deliberately small and safe)
  {{employee.full_name}}   a named field from the allow-list below. Nothing else is
                           ever evaluated: no expressions, no filters, no loops, no
                           code. `{{extra.anything}}` takes a value the caller passes.
  # Heading   ## Sub-heading
  **bold**
  - bullet            1. numbered item
  | cell | cell |     a table (first row is the header)
  ---                 a horizontal line
  {{salary.breakup_table}} on its own line → the salary break-up as a table
  blank line          new paragraph

The template is parsed into blocks FIRST and values are dropped into the text
afterwards, so a value can never add markup, a field or a table to the letter.
HTML, Jinja tags (`{% %}`, `{# #}`) and unknown fields are refused when the
template is saved.

PDF: fpdf2 with the bundled Unicode font (shared/utils/pdf_fonts.py, Noto Sans,
OFL) — $, curly quotes and accented (French) names print as typed.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date

from fpdf import FPDF

from shared.utils.pdf_fonts import UnicodePDF, pdf_text

MAX_TEMPLATE_CHARS = 20000

# ── the allow-list ────────────────────────────────────────────────────────────
ALLOWED: dict[str, dict[str, str]] = {
    "employee": {
        "full_name": "Full name", "first_name": "First name", "last_name": "Last name",
        "employee_number": "Employee number", "designation": "Designation", "department": "Department",
        "grade": "Grade", "location": "Work location", "manager_name": "Manager",
        "joining_date": "Joining date", "employment_type": "Employment type", "email": "Email",
        "address": "Home address", "probation_end_date": "Probation end date",
        "confirmation_date": "Confirmation date", "employment_status": "Probation / confirmed",
    },
    "org": {
        "name": "Company name", "legal_name": "Legal name", "address": "Company address",
        "signatory_name": "Signatory name", "signatory_designation": "Signatory designation",
        "entity_name": "Legal entity (employer) name", "entity_code": "Legal entity short code",
    },
    "letter": {"number": "Letter number", "date": "Letter date", "effective_date": "Effective date"},
    "salary": {
        "annual_ctc": "Annual salary (current)", "monthly_gross": "Monthly salary (current)",
        "currency": "Currency", "hourly_rate": "Hourly rate", "old_annual": "Annual salary before the change",
        "new_annual": "Annual salary after the change", "increase_amount": "Increase per year",
        "increase_percent": "Increase %", "effective_from": "Salary effective date",
        "breakup_table": "Salary break-up table (own line)",
    },
    "career": {
        "change_type": "Type of change", "effective_date": "Change effective date", "reason": "Reason",
        "old_designation": "Old designation", "new_designation": "New designation",
        "old_department": "Old department", "new_department": "New department",
        "old_grade": "Old grade", "new_grade": "New grade",
        "old_location": "Old location", "new_location": "New location",
        "old_manager": "Old manager", "new_manager": "New manager",
        "old_entity": "Old legal entity", "new_entity": "New legal entity",
    },
    "probation": {"end_date": "Probation end date", "new_end_date": "Extended probation end date",
                  "months": "Probation months", "reason": "Reason"},
    "contract": {"old_end_date": "Previous contract end date", "start_date": "New term starts", "end_date": "New term ends",
                 "term_no": "Term number", "notice_days": "Notice period (days)", "effective_date": "Effective date",
                 "reason": "Reason"},
    "exit": {
        "resignation_date": "Resignation date", "last_working_date": "Last working day",
        "relieving_date": "Relieving date", "reason": "Reason for leaving",
        "notice_period": "Notice period", "service_period": "Period of service",
        "fnf_amount": "Full and final amount", "fnf_date": "Settlement date",
    },
    "offer": {"position": "Position offered", "joining_date": "Proposed joining date",
              "valid_until": "Offer valid until", "annual_ctc": "Offered annual salary",
              "location": "Work location", "reporting_to": "Reports to"},
}
EXTRA_NS = "extra"
CUSTOM_NS = "custom"          # D1: {{custom.<key>}} = the employee's custom field <key> (Settings → Custom fields)
FREE_NS = (EXTRA_NS, CUSTOM_NS)
BLOCK_FIELDS = {"salary.breakup_table"}
MONEY_FIELDS = {"salary.annual_ctc", "salary.monthly_gross", "salary.old_annual", "salary.new_annual",
                "salary.increase_amount", "salary.hourly_rate", "exit.fnf_amount", "offer.annual_ctc"}

_FIELD_RE = re.compile(r"\{\{(.*?)\}\}", re.S)
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}\.[a-z][a-z0-9_]{0,60}$")
_HTML_RE = re.compile(r"<\s*/?\s*[a-zA-Z!]")
_NUM_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")
_SEP_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def is_allowed(name: str) -> bool:
    if not _NAME_RE.match(name or ""):
        return False
    ns, key = name.split(".", 1)
    return ns in FREE_NS or key in ALLOWED.get(ns, {})


def catalogue(custom_fields: list[dict] | None = None) -> list[dict]:
    """The fields a template may use — shown beside the template editor.
    ``custom_fields`` = the org's employee custom-field definitions ({key, label})."""
    out = [{"field": f"{ns}.{k}", "label": label, "group": ns, "block": f"{ns}.{k}" in BLOCK_FIELDS}
           for ns, keys in ALLOWED.items() for k, label in keys.items()]
    for cf in custom_fields or []:
        out.append({"field": f"{CUSTOM_NS}.{cf['key']}", "label": cf.get("label") or cf["key"], "group": CUSTOM_NS, "block": False})
    out.append({"field": "extra.your_name", "label": "Any extra value sent with the letter", "group": "extra", "block": False})
    return out


def find_fields(body: str) -> list[str]:
    seen: list[str] = []
    for m in _FIELD_RE.finditer(body or ""):
        name = m.group(1).strip()
        if name not in seen:
            seen.append(name)
    return seen


def validate_template(body: str) -> list[str]:
    """Every reason this template is refused ([] = fine)."""
    errs: list[str] = []
    body = body or ""
    if not body.strip():
        return ["The template is empty."]
    if len(body) > MAX_TEMPLATE_CHARS:
        errs.append(f"The template is too long (max {MAX_TEMPLATE_CHARS} characters).")
    if "{%" in body or "%}" in body or "{#" in body or "#}" in body:
        errs.append("Template code ({% ... %} or {# ... #}) is not allowed. Use {{field}} only.")
    if _HTML_RE.search(body):
        errs.append("HTML tags are not allowed. Use # headings, **bold**, - lists and | tables |.")
    for name in find_fields(body):
        if not _NAME_RE.match(name):
            errs.append(f"'{{{{{name[:60]}}}}}' is not a field. A field looks like {{{{employee.full_name}}}}.")
        elif not is_allowed(name):
            errs.append(f"Unknown field {{{{{name}}}}}.")
    stripped = _FIELD_RE.sub("", body)
    if "{{" in stripped or "}}" in stripped:
        errs.append("A {{ or }} is not closed.")
    for line in body.splitlines():
        s = line.strip()
        if any(f"{{{{{b}}}}}" in s.replace(" ", "") for b in BLOCK_FIELDS) and _FIELD_RE.sub("", s).strip():
            errs.append("{{salary.breakup_table}} must be on a line of its own.")
    return errs


# ── values ────────────────────────────────────────────────────────────────────
def fmt_money(amount, currency: str = "") -> str:
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return str(amount or "")
    cur = (currency or "").upper()
    if cur == "CAD":                       # en-CA: $1,234.56
        return f"{'-' if v < 0 else ''}${abs(v):,.2f}"
    return f"{cur + ' ' if cur else ''}{v:,.2f}"


def fmt_date(v) -> str:
    if isinstance(v, date):
        d = v
    else:
        try:
            d = date.fromisoformat(str(v)[:10])
        except ValueError:
            return str(v or "")
    return f"{d.day} {d.strftime('%B %Y')}"


def flatten_data(data: dict | None) -> tuple[dict, list[str]]:
    """Caller data → {field: value}. Accepts {"salary": {"new_annual": 1}},
    {"salary.new_annual": 1} and bare keys (→ extra.<key>). Returns (values, refused)."""
    out: dict = {}
    refused: list[str] = []

    def put(name: str, v):
        if is_allowed(name):
            out[name] = v
        else:
            refused.append(name)

    for k, v in (data or {}).items():
        k = str(k).strip()
        if isinstance(v, dict) and "." not in k and k in list(ALLOWED) + list(FREE_NS):
            for kk, vv in v.items():
                put(f"{k}.{str(kk).strip().lower()}", vv)
        elif "." in k:
            put(k.lower(), v)
        else:
            put(f"{EXTRA_NS}.{re.sub(r'[^a-z0-9_]', '_', k.lower())}", v)
    return out, refused


def display_value(name: str, v, currency: str = "") -> str:
    if v is None:
        return ""
    if name in MONEY_FIELDS and isinstance(v, (int, float)):
        return fmt_money(v, currency)
    if isinstance(v, date) or (isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v)
                               and (name.endswith("date") or name.endswith("_from") or name.endswith("_until"))):
        return fmt_date(v)
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"
    return " ".join(str(v).split())            # one line — a value never adds structure


# ── template → blocks ─────────────────────────────────────────────────────────
def _runs(text: str, values: dict, missing: list) -> list[tuple[str, bool]]:
    """[(text, bold)] — bold from the TEMPLATE's ** marks, then values dropped in."""
    def sub(seg: str) -> str:
        def rep(m):
            name = m.group(1).strip()
            val = values.get(name)
            if val in (None, ""):
                if name not in missing:
                    missing.append(name)
                return f"[{name}]"
            return str(val)
        return _FIELD_RE.sub(rep, seg)
    return [(sub(seg), i % 2 == 1) for i, seg in enumerate(text.split("**")) if seg != ""]


def build_blocks(body: str, values: dict, tables: dict | None = None) -> tuple[list[dict], list[str]]:
    """Parse the template into blocks and fill the values. Returns (blocks, missing_fields).
    `tables` = {"salary.breakup_table": [[label, monthly, annual], ...]} (header first)."""
    blocks: list[dict] = []
    missing: list[str] = []
    para: list[str] = []
    table: list[list] = []

    def flush():
        nonlocal para, table
        if para:
            blocks.append({"t": "p", "runs": _runs(" ".join(para), values, missing)})
            para = []
        if table:
            blocks.append({"t": "table", "rows": table})
            table = []

    for raw in (body or "").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        compact = line.replace(" ", "")
        if compact in {f"{{{{{b}}}}}" for b in BLOCK_FIELDS}:
            flush()
            name = compact[2:-2]
            rows = (tables or {}).get(name) or []
            if rows:
                blocks.append({"t": "table", "rows": [[[(str(c), False)] for c in r] for r in rows], "money": True})
            elif name not in missing:
                missing.append(name)
            continue
        if line.startswith("|"):
            if para:
                flush()
            if _SEP_RE.match(line):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            table.append([_runs(c, values, missing) for c in cells])
            continue
        if table:
            flush()
        if not line:
            flush()
        elif line.startswith("## "):
            flush(); blocks.append({"t": "h2", "runs": _runs(line[3:], values, missing)})
        elif line.startswith("# "):
            flush(); blocks.append({"t": "h1", "runs": _runs(line[2:], values, missing)})
        elif line in ("---", "___", "***"):
            flush(); blocks.append({"t": "hr"})
        elif line.startswith("- ") or line.startswith("* "):
            flush(); blocks.append({"t": "li", "mark": "-", "runs": _runs(line[2:], values, missing)})
        elif _NUM_RE.match(line):
            m = _NUM_RE.match(line)
            flush(); blocks.append({"t": "li", "mark": f"{m.group(1)}.", "runs": _runs(m.group(2), values, missing)})
        else:
            para.append(line)
    flush()
    return blocks, missing


def blocks_json(blocks: list[dict]) -> list[dict]:
    """Blocks as plain JSON for the live (HTML) preview: runs → [{text, bold}]."""
    out = []
    for b in blocks:
        if b["t"] == "table":
            out.append({"t": "table", "money": bool(b.get("money")),
                        "rows": [[[{"text": t, "bold": bold} for t, bold in cell] for cell in row] for row in b["rows"]]})
        elif b["t"] == "hr":
            out.append({"t": "hr"})
        else:
            out.append({"t": b["t"], "mark": b.get("mark"), "runs": [{"text": t, "bold": bold} for t, bold in b["runs"]]})
    return out


def plain_text(blocks: list[dict]) -> str:
    """The filled letter as text (stored beside the PDF; used in tests and search)."""
    out = []
    for b in blocks:
        if b["t"] == "table":
            out += [" | ".join("".join(t for t, _ in cell) for cell in row) for row in b["rows"]]
        elif b["t"] == "hr":
            out.append("----")
        else:
            out.append((b.get("mark", "") + " " if b["t"] == "li" else "") + "".join(t for t, _ in b["runs"]))
    return "\n".join(out)


# ── PDF ───────────────────────────────────────────────────────────────────────
INK, MUTED, RULE = (23, 35, 43), (90, 107, 115), (216, 226, 224)


def _t(s) -> str:
    return pdf_text(s)


class _LetterPDF(UnicodePDF):
    def __init__(self, head: dict, number: str | None, watermark: str | None):
        super().__init__(format="A4")
        self.head, self.number, self.watermark = head or {}, number, watermark
        self.set_margins(20, 16, 20)
        self.set_auto_page_break(True, margin=24)
        self.set_title(_t(self.head.get("title") or "Letter"))

    def header(self):
        h = self.head
        top = self.get_y()
        if h.get("logo"):
            try:
                self.image(io.BytesIO(h["logo"]), x=self.l_margin, y=top, h=16)
            except Exception:  # noqa: BLE001 — a bad image must not stop a letter
                pass
        self.set_text_color(*INK)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 7, _t(h.get("company_name") or ""), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 8.5)
        self.set_text_color(*MUTED)
        for ln in str(h.get("address") or "").splitlines()[:4]:
            self.cell(0, 4, _t(ln), align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_y(max(self.get_y(), top + 18) + 2)
        self.set_draw_color(*RULE)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(6)
        if self.watermark:
            self.set_font("Helvetica", "B", 60)
            self.set_text_color(235, 235, 235)
            with self.rotation(35, self.w / 2, self.h / 2):
                self.text(self.w / 2 - 45, self.h / 2, _t(self.watermark))
        self.set_text_color(*INK)

    def footer(self):
        self.set_y(-16)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*MUTED)
        left = " · ".join(x for x in [self.head.get("footer_text") or "", self.number or ""] if x)
        self.cell(0, 4, _t(left.replace("·", "|")), align="L")
        self.cell(0, 4, f"Page {self.page_no()} of {{nb}}", align="R")


def _write_runs(pdf: FPDF, runs, size: float, line_h: float, force_bold: bool = False):
    for text, bold in runs:
        pdf.set_font("Helvetica", "B" if (bold or force_bold) else "", size)
        pdf.write(line_h, _t(text))
    pdf.ln(line_h)


def render_pdf(blocks: list[dict], *, letterhead: dict | None = None, number: str | None = None,
               date_text: str = "", watermark: str | None = None) -> bytes:
    """Blocks → A4 PDF on the org letterhead. Returns the bytes."""
    head = letterhead or {}
    pdf = _LetterPDF(head, number, watermark)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 10)
    if number or date_text:
        pdf.cell(0, 5, _t(f"Ref: {number}" if number else ""), align="L")
        pdf.cell(0, 5, _t(date_text), align="R", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)
    for b in blocks:
        kind = b["t"]
        if kind == "h1":
            pdf.ln(1); _write_runs(pdf, b["runs"], 13, 7, True); pdf.ln(1.5)
        elif kind == "h2":
            pdf.ln(1); _write_runs(pdf, b["runs"], 11, 6, True); pdf.ln(1)
        elif kind == "hr":
            pdf.ln(1); pdf.set_draw_color(*RULE)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y()); pdf.ln(3)
        elif kind == "li":
            pdf.set_font("Helvetica", "", 10)
            pdf.set_x(pdf.l_margin + 4)
            pdf.write(5.2, _t(b.get("mark", "-")) + " ")
            pdf.set_left_margin(pdf.l_margin + 10)
            _write_runs(pdf, b["runs"], 10, 5.2)
            pdf.set_left_margin(pdf.l_margin - 10)
            pdf.set_x(pdf.l_margin)
        elif kind == "table":
            rows = b["rows"]
            if not rows:
                continue
            pdf.ln(1)
            pdf.set_font("Helvetica", "", 9)
            pdf.set_draw_color(*RULE)
            ncols = max(len(r) for r in rows)
            aligns = ["LEFT"] + ["RIGHT"] * (ncols - 1) if b.get("money") else "LEFT"
            with pdf.table(text_align=aligns, line_height=5.5, first_row_as_headings=True) as tbl:
                for r in rows:
                    row = tbl.row()
                    for i in range(ncols):
                        cell = r[i] if i < len(r) else []
                        row.cell(_t("".join(t for t, _ in cell)))
            pdf.ln(3)
        else:
            _write_runs(pdf, b["runs"], 10, 5.2); pdf.ln(2.2)
    # signatory
    pdf.ln(8)
    if pdf.get_y() > pdf.h - 60:
        pdf.add_page()
    if head.get("signature"):
        try:
            pdf.image(io.BytesIO(head["signature"]), x=pdf.l_margin, y=pdf.get_y(), h=14)
        except Exception:  # noqa: BLE001
            pass
    pdf.ln(15)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 5, _t(head.get("signatory_name") or ""), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9.5)
    pdf.cell(0, 5, _t(head.get("signatory_designation") or ""), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 5, _t(head.get("company_name") or ""), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


# ── numbering + immutability rules ────────────────────────────────────────────
def format_number(prefix: str, year: int, seq: int) -> str:
    """Per org, per calendar year: LTR/2026/0001."""
    p = re.sub(r"[^A-Za-z0-9\-]", "", (prefix or "LTR").upper())[:12] or "LTR"
    return f"{p}/{int(year)}/{int(seq):04d}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# what may happen to a letter in each status — the stored PDF of an ISSUED or VOID
# letter is never rewritten by any of these.
_ACTIONS = {
    "draft": {"issue", "discard", "download"},
    "issued": {"download", "void", "reissue"},
    "void": {"download"},
}


def allowed_actions(status: str) -> set:
    return set(_ACTIONS.get(status or "", set()))


def check_action(status: str, action: str) -> None:
    if action not in allowed_actions(status):
        msg = {"issue": "Only a draft can be issued.", "discard": "An issued letter cannot be deleted. Void it with a reason.",
               "void": "Only an issued letter can be voided.", "reissue": "Only an issued letter can be issued again.",
               "edit": "An issued letter cannot be changed. Issue a new one."}.get(action, "That is not allowed for this letter.")
        raise PermissionError(msg)
