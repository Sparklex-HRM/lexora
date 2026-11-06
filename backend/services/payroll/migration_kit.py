"""Migration kit helpers — upload parsing (CSV / XLSX / XLS), value coercion, field specs and
header synonyms. Used by the Canada migration kit (ca_migration.py, ca_migration_fields.py,
migration_routers.py).

A field: key, label, required, kind (text|number|date|month|yesno|choice), example, help, choices.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta

from xlsx_lite import read_workbook, write_workbook  # noqa: F401  (write_workbook re-exported for templates)

MAX_ROWS = 100000
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_EXCEL_EPOCH = date(1899, 12, 30)


class ParseError(ValueError):
    pass


# ───────────────────────────── field specs + header synonyms ─────────────────────────────

def F(key, label, required=False, kind="text", example="", help="", choices=None):
    return {"key": key, "label": label, "required": required, "kind": kind,
            "example": example, "help": help, "choices": choices or []}


def norm_header(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


# Generic register labels (normalized: lowercase alnum only). Canada-specific keys live in
# ca_migration_fields.CA_SYNONYMS and are tried first.
SYNONYMS: dict[str, list[str]] = {
    "employee_code": ["employeecode", "empcode", "employeeno", "employeenumber", "employeeid", "empid", "empno",
                      "code", "staffid", "staffcode", "ecode", "emp"],
    "first_name": ["firstname", "fname", "givenname"],
    "last_name": ["lastname", "lname", "surname", "familyname"],
    "full_name": ["employeename", "name", "fullname", "empname", "nameofemployee"],
    "date_of_birth": ["dateofbirth", "dob", "birthdate", "birthday"],
    "department": ["department", "dept", "departmentname"],
    "email": ["email", "workemail", "emailid", "officialemail", "emailaddress"],
    "status": ["status", "employmentstatus", "employeestatus"],
    "effective_from": ["effectivefrom", "effectivedate", "revisiondate", "fromdate"],
    "month": ["month", "paymonth", "payrollmonth", "period", "payperiod", "salarymonth", "monthyear", "payrollperiod"],
    "gross": ["gross", "grosspay", "grosssalary", "grossearnings", "totalearnings", "totalgross"],
    "net": ["net", "netpay", "netsalary", "takehome", "netamount", "netpayable", "netearnings"],
    "leave_type": ["leavetype", "leavetypecode", "type", "leave", "leavecode"],
    "opening": ["opening", "openingbalance", "balance", "entitled", "entitlement", "credit", "available", "allocated", "granted"],
    "used": ["used", "availed", "taken", "consumed", "leavestaken", "usedsofar"],
    "fiscal_year": ["fiscalyear", "year", "leaveyear"],
    "reason": ["reason", "revisionreason", "changereason"],
    "note": ["note", "notes", "remarks", "comment", "comments"],
}


# ───────────────────────────── coercion ─────────────────────────────

def to_text(v):
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    s = str(v).strip()
    return s or None


def to_number(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("CA$", "").replace("C$", "").replace("CAD", "").replace("$", "").strip()
    if s in ("", "-", "—"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = float(s)
    except ValueError:
        raise ValueError(f"'{v}' is not a number")
    return -n if neg else n


_DATE_FMTS = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y",
              "%b %d, %Y", "%B %d, %Y", "%d-%b-%y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 < float(v) < 80000:                     # Excel serial
            return _EXCEL_EPOCH + timedelta(days=int(v))
        raise ValueError(f"'{v}' is not a date")
    s = str(v).strip()
    for fmt in _DATE_FMTS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{v}' is not a date — use YYYY-MM-DD")


def to_month(v) -> tuple[int, int] | None:
    """→ (year, month). Accepts Jan-2026, Jan 26, January 2026, 2026-01, 01/2026, 1/2026,
    2026-01-15 (any date in the month), Excel serial dates."""
    if v is None or v == "":
        return None
    if isinstance(v, (date, datetime)):
        return (v.year, v.month)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if 20000 < float(v) < 80000:
            d = _EXCEL_EPOCH + timedelta(days=int(v))
            return (d.year, d.month)
        raise ValueError(f"'{v}' is not a month")
    s = str(v).strip().lower().replace(",", " ")
    m = re.match(r"^([a-z]{3})[a-z]*[\s\-/']*(\d{2}|\d{4})$", s)
    if m and m.group(1) in MONTHS:
        y = int(m.group(2))
        return (y + 2000 if y < 100 else y, MONTHS[m.group(1)])
    m = re.match(r"^(\d{4})[\s\-/](\d{1,2})$", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return (y, mo)
    m = re.match(r"^(\d{1,2})[\s\-/](\d{4})$", s)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return (y, mo)
    m = re.match(r"^(\d{4})[\s\-/]([a-z]{3})[a-z]*$", s)
    if m and m.group(2) in MONTHS:
        return (int(m.group(1)), MONTHS[m.group(2)])
    try:
        d = to_date(v)
        return (d.year, d.month)
    except ValueError:
        pass
    raise ValueError(f"'{v}' is not a month — use Jan-2026")


def to_yesno(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("y", "yes", "true", "1", "1.0", "applicable", "eligible", "x", "o", "oui"):
        return True
    if s in ("n", "no", "false", "0", "0.0", "not applicable", "na", "n/a", "not eligible", "-", "non"):
        return False
    raise ValueError(f"'{v}' should be Y or N")


def to_choice(v, choices: list[str], key: str):
    if v is None or v == "":
        return None
    s = str(v).strip().lower().replace(" ", "_").replace("-", "_")
    alias = {"active": "active", "inactive": "terminated", "resigned": "terminated", "left": "terminated",
             "exited": "terminated", "terminated": "terminated", "probation": "active", "working": "active",
             "bi_weekly": "biweekly", "semimonthly": "semi_monthly", "semi_monthly": "semi_monthly",
             "salaried": "salary", "hourly_paid": "hourly"}
    s = alias.get(s, s)
    if s in choices:
        return s
    raise ValueError(f"'{v}' must be one of: {', '.join(choices)}")


# ───────────────────────────── upload parsing ─────────────────────────────

def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v if v != "" else None
    return v


def _rows_from_grid(grid: list[list]) -> tuple[list[str], list[dict]]:
    """First row with ≥2 filled cells is the header; later rows → {header: value}."""
    hdr_i = next((i for i, r in enumerate(grid) if sum(1 for c in r if _clean(c) not in (None, "")) >= 2), None)
    if hdr_i is None:
        raise ParseError("Could not find a header row (need at least 2 column names in one row).")
    headers: list[str] = []
    seen: dict[str, int] = {}
    for c in grid[hdr_i]:
        h = str(_clean(c) or "").strip()
        if isinstance(c, float) and c.is_integer():
            h = str(int(c))
        if h in seen:
            seen[h] += 1
            h = f"{h} ({seen[h]})"
        else:
            seen[h] = 1
        headers.append(h)
    rows: list[dict] = []
    for i, r in enumerate(grid[hdr_i + 1:], start=hdr_i + 2):
        vals = [_clean(c) for c in r]
        if all(v in (None, "") for v in vals):
            continue
        d = {h: (vals[j] if j < len(vals) else None) for j, h in enumerate(headers) if h}
        d["__row"] = i
        rows.append(d)
        if len(rows) >= MAX_ROWS:
            break
    return [h for h in headers if h], rows


def parse_upload(filename: str, data: bytes) -> tuple[list[str], list[dict], str]:
    """→ (headers, rows, sheet_used). CSV, XLSX or old XLS."""
    name = (filename or "").lower()
    if name.endswith(".xls") or data[:8] == bytes.fromhex("d0cf11e0a1b11ae1"):     # Excel 97-2003 (BIFF / OLE2)
        return _parse_xls(data)
    if name.endswith(".zip"):
        raise ParseError("Upload a .xlsx, .xls or .csv file here, not a .zip.")
    if name.endswith(".xlsx") or name.endswith(".xlsm") or data[:2] == b"PK":
        try:
            sheets = read_workbook(data, MAX_ROWS + 50)
        except Exception as e:  # noqa: BLE001
            raise ParseError(f"Could not read the Excel file ({e}). Save it as .xlsx and try again.")
        if not sheets:
            raise ParseError("The workbook has no sheets.")
        pick = "Data" if "Data" in sheets else next((n for n, g in sheets.items() if len(g) > 1), next(iter(sheets)))
        headers, rows = _rows_from_grid(sheets[pick])
        return headers, rows, pick
    text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ParseError("Could not read the file as text — upload a .csv or .xlsx.")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    grid = list(csv.reader(io.StringIO(text), dialect))
    headers, rows = _rows_from_grid(grid)
    return headers, rows, "csv"


def _parse_xls(data: bytes) -> tuple[list[str], list[dict], str]:
    """Old Excel 97-2003 files (some payroll tools still export these)."""
    try:
        import xlrd  # noqa: WPS433
    except ImportError:
        raise ParseError("This server cannot read old .xls files yet — open it in Excel, 'Save as' .xlsx and upload again.")
    try:
        book = xlrd.open_workbook(file_contents=data, on_demand=True)
    except Exception:  # noqa: BLE001
        raise ParseError("Could not read the .xls file. Open it in Excel, 'Save as' .xlsx and try again.")
    names = book.sheet_names()
    pick = "Data" if "Data" in names else None
    grids = {}
    for n in names:
        sh = book.sheet_by_name(n)
        grid = []
        for i in range(min(sh.nrows, MAX_ROWS + 50)):
            row = []
            for c in sh.row(i):
                if c.ctype == xlrd.XL_CELL_DATE:
                    try:
                        row.append(xlrd.xldate.xldate_as_datetime(c.value, book.datemode).date().isoformat())
                    except Exception:  # noqa: BLE001
                        row.append(c.value)
                elif c.ctype == xlrd.XL_CELL_BOOLEAN:
                    row.append(bool(c.value))
                elif c.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    row.append(None)
                else:
                    row.append(c.value)
            grid.append(row)
        grids[n] = grid
        if pick is None and len(grid) > 1:
            pick = n
    pick = pick or names[0]
    headers, rows = _rows_from_grid(grids[pick])
    return headers, rows, pick
