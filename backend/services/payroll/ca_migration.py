"""Canada migration — normalize + validate (pure) and commit / rollback (DB).

Uses the migration kit's coercion helpers (to_number / to_date / to_month / to_yesno /
to_choice) so the same file formats work. Every write is ledgered in
`ca_import_rows` so "undo last import batch" restores the exact prior state.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select, text

from migration_kit import to_choice, to_date, to_month, to_number, to_text, to_yesno  # noqa: E402

from ca_migration_fields import CA_FILE_TYPES, CA_PROVINCES, ca_fields_of
from ca_models import CaImportRow, CaOpeningYtd
from models import EmployeeYtd
from shared.utils.org_time import local_today  # org-TZ business date (UTC store, local today)

TOL = 0.02


# ───────────────────────────── normalize + validate (pure) ─────────────────────────────

def normalize_rows(file_type: str, mapping: dict, raw_rows: list[dict], overrides: dict | None = None) -> list[dict]:
    fields = ca_fields_of(file_type)
    overrides = overrides or {}
    out = []
    for raw in raw_rows:
        row_no = raw.get("__row")
        data, errors = {}, []
        ov = overrides.get(str(row_no)) or {}
        for f in fields:
            k = f["key"]
            src = mapping.get(k)
            v = ov[k] if k in ov else (raw.get(src) if src else None)
            try:
                if f["kind"] == "number":
                    v = to_number(v)
                elif f["kind"] == "date":
                    v = to_date(v)
                elif f["kind"] == "month":
                    v = to_month(v)
                elif f["kind"] == "yesno":
                    v = to_yesno(v)
                elif f["kind"] == "choice":
                    v = to_choice(v, f["choices"], k)
                else:
                    v = to_text(v)
            except ValueError as e:
                errors.append(f"{f['label']}: {e}")
                v = None
            data[k] = v
        out.append({"row_no": row_no, "data": data, "errors": errors, "warnings": []})
    return out


def _dup(rows, keyfn, what):
    seen = {}
    for r in rows:
        k = keyfn(r["data"])
        if k is None or (isinstance(k, tuple) and any(x is None for x in k)):
            continue
        if k in seen:
            r["errors"].append(f"Duplicate {what} — also on row {seen[k]}")
        else:
            seen[k] = r["row_no"]


def validate_rows(file_type: str, rows: list[dict], ctx: dict) -> dict:
    """ctx: employees (code → employee dict), today, year, leave_types."""
    fields = ca_fields_of(file_type)
    employees = ctx.get("employees") or {}
    today: date = ctx.get("today") or local_today()
    year = int(ctx.get("year") or today.year)
    for r in rows:
        d = r["data"]
        if d.get("employee_code"):
            d["employee_code"] = str(d["employee_code"]).strip().upper()
        for f in fields:
            if f["required"] and d.get(f["key"]) in (None, ""):
                if f["key"] == "first_name" and d.get("full_name"):
                    continue
                r["errors"].append(f"{f['label']} is missing")
    if file_type != "ca_employees":
        for r in rows:
            code = r["data"].get("employee_code")
            if code and code not in employees:
                r["errors"].append(f"Employee '{code}' is not in Lexora HRM yet — import the Employees file first")
            elif code:
                r["data"]["_employee_id"] = employees[code]["id"]
    handler = globals()[f"_validate_{file_type}"]
    summary = handler(rows, ctx, year, today) or {}
    ok = sum(1 for r in rows if not r["errors"])
    summary.update({"rows": len(rows), "ok": ok, "with_errors": len(rows) - ok,
                    "with_warnings": sum(1 for r in rows if r["warnings"] and not r["errors"]), "year": year})
    return summary


def _validate_ca_employees(rows, ctx, year, today):
    _dup(rows, lambda d: d.get("employee_code"), "Employee Code")
    existing = ctx.get("employees") or {}
    new = upd = 0
    for r in rows:
        d = r["data"]
        if not d.get("first_name") and d.get("full_name"):
            parts = d["full_name"].split()
            d["first_name"], d["last_name"] = (" ".join(parts[:-1]) or parts[0]), (parts[-1] if len(parts) > 1 else "")
        p = (d.get("province") or "").strip().upper()
        if p and p not in CA_PROVINCES:
            r["errors"].append(f"Province '{p}' is not a Canadian province/territory code")
        d["province"] = p or None
        if (d.get("pay_type") or "salary") == "hourly":
            if not d.get("hourly_rate"):
                r["errors"].append("Hourly employee needs an hourly rate")
        elif not d.get("annual_salary"):
            r["warnings"].append("No annual salary — payroll cannot run for this person until set")
        for k in ("annual_salary", "hourly_rate", "td1_federal_claim", "td1_provincial_claim", "additional_tax_per_period", "vacation_percent"):
            if d.get(k) is not None and d[k] < 0:
                r["errors"].append(f"{k} cannot be negative")
        if d.get("vacation_percent") is not None and d["vacation_percent"] > 30:
            r["errors"].append("Vacation pay % looks wrong (above 30)")
        if d.get("bank_institution") and not str(d["bank_institution"]).isdigit():
            r["errors"].append("Bank institution must be digits")
        if d.get("bank_transit") and len(str(d["bank_transit"])) != 5:
            r["errors"].append("Bank transit must be 5 digits")
        hd, td, dob = d.get("hire_date"), d.get("termination_date"), d.get("date_of_birth")
        if td and hd and td < hd:
            r["errors"].append("Termination date is before hire date")
        if dob and hd and dob >= hd:
            r["errors"].append("Date of birth must be before hire date")
        if td and not d.get("status"):
            d["status"] = "terminated"
        if d.get("email") and "@" not in d["email"]:
            r["errors"].append("Email does not look right")
        d["_action"] = "update" if d.get("employee_code") in existing else "create"
        upd += d["_action"] == "update"; new += d["_action"] == "create"
    return {"to_create": new, "to_update": upd}


def _validate_ca_td1(rows, ctx, year, today):
    _dup(rows, lambda d: d.get("employee_code"), "Employee Code")
    for r in rows:
        d = r["data"]
        for k in ("td1_federal_claim", "td1_provincial_claim", "additional_tax_per_period"):
            if d.get(k) is not None and d[k] < 0:
                r["errors"].append(f"{k} cannot be negative")
    return {}


def _validate_ca_salary_history(rows, ctx, year, today):
    _dup(rows, lambda d: (d.get("employee_code"), d.get("effective_from")), "employee + effective from")
    for r in rows:
        d = r["data"]
        if (d.get("pay_type") or "salary") == "hourly":
            if not d.get("hourly_rate"):
                r["errors"].append("Hourly row needs an hourly rate")
        elif not d.get("annual_amount"):
            r["errors"].append("Salary row needs an annual amount")
        if d.get("effective_to") and d.get("effective_from") and d["effective_to"] < d["effective_from"]:
            r["errors"].append("Effective to is before effective from")
        if d.get("effective_from") and d["effective_from"] > today:
            r["warnings"].append("Effective from is in the future")
    return {}


def _validate_ca_ytd_monthly(rows, ctx, year, today):
    _dup(rows, lambda d: (d.get("employee_code"), d.get("month")), "employee + month")
    totals: dict[str, dict] = {}
    for r in rows:
        d = r["data"]
        ym = d.get("month")
        if ym:
            if ym[0] != year:
                r["errors"].append(f"{ym[0]}-{ym[1]:02d} is not in {year} — YTD is per calendar year")
            elif ym >= (today.year, today.month):
                r["errors"].append("Only months already paid can be imported")
        for k in ("gross", "cpp", "cpp2", "ei", "qpip", "income_tax", "pensionable", "insurable", "vac_accrued", "vac_paid", "net"):
            if d.get(k) is not None and d[k] < 0:
                r["errors"].append(f"{k} cannot be negative")
        g = d.get("gross")
        if d.get("pensionable") is None:
            d["pensionable"] = g
        if d.get("insurable") is None:
            d["insurable"] = g
        if g is not None and d.get("net") is not None:
            ded = sum(d.get(k) or 0 for k in ("cpp", "cpp2", "ei", "qpip", "income_tax"))
            if d["net"] > g - ded + TOL:
                r["warnings"].append(f"Net {d['net']:,.2f} is more than gross minus statutory deductions ({g - ded:,.2f}) — other deductions assumed")
        if ym and not r["errors"]:
            t = totals.setdefault(f"{ym[0]}-{ym[1]:02d}", {"month": f"{ym[0]}-{ym[1]:02d}", "employees": 0, "gross": 0.0, "cpp": 0.0,
                                                           "cpp2": 0.0, "ei": 0.0, "income_tax": 0.0, "net": 0.0})
            t["employees"] += 1
            for k in ("gross", "cpp", "cpp2", "ei", "income_tax", "net"):
                t[k] = round(t[k] + float(d.get(k) or 0), 2)
    return {"totals_by_month": [totals[k] for k in sorted(totals)]}


def _validate_ca_leave_balances(rows, ctx, year, today):
    _dup(rows, lambda d: (d.get("employee_code"), (d.get("leave_type") or "").upper() or None), "employee + leave type")
    types = {t.upper() for t in (ctx.get("leave_types") or [])}
    for r in rows:
        d = r["data"]
        if d.get("leave_type"):
            d["leave_type"] = d["leave_type"].strip().upper()
            if types and d["leave_type"] not in types:
                r["errors"].append(f"Leave type '{d['leave_type']}' is not set up in Master Data (have: {', '.join(sorted(types))})")
        if (d.get("opening") or 0) < 0 or (d.get("used") or 0) < 0:
            r["errors"].append("Opening / used cannot be negative")
        d["fiscal_year"] = int(d.get("fiscal_year") or year)
    return {}


# ───────────────────────────── commit / rollback (DB) ─────────────────────────────

def _j(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
        return float(v)
    return v


class Ledger:
    def __init__(self, db, org, batch_id):
        self.db, self.org, self.batch_id, self.seq = db, org, batch_id, 0
        self.n_insert = self.n_update = 0

    def add(self, table, target_id, action, prior, employee_id=None, key=None):
        self.seq += 1
        self.db.add(CaImportRow(org_id=self.org, batch_id=self.batch_id, target_table=table, target_id=str(target_id),
                                employee_id=str(employee_id) if employee_id else None, row_key=key, action=action,
                                prior=prior, seq=self.seq))
        if action == "insert":
            self.n_insert += 1
        else:
            self.n_update += 1


def _eid(r, emp_by_code):
    return uuid.UUID(r["data"].get("_employee_id") or emp_by_code[r["data"]["employee_code"]]["id"])


async def commit_batch(db, batch, emp_by_code: dict, emp_client: dict, leave_types: dict) -> dict:
    org = str(batch.org_id)
    led = Ledger(db, org, batch.id)
    rows = [r for r in (batch.rows or []) if not r.get("errors")]
    handler = globals()[f"_commit_{batch.file_type}"]
    extra = await handler(db, led, org, batch, rows, emp_by_code, emp_client, leave_types) or {}
    return {"file_type": batch.file_type, "rows_written": len(rows),
            "skipped_errors": len(batch.rows or []) - len(rows), "inserted": led.n_insert, "updated": led.n_update, **extra}


async def _commit_ca_employees(db, led, org, batch, rows, emp_by_code, emp_client, leave_types):
    payload = []
    for r in rows:
        d = r["data"]
        payload.append({k: v for k, v in {
            "employee_number": d["employee_code"], "first_name": d.get("first_name"), "last_name": d.get("last_name") or "",
            "email": d.get("email"), "date_of_birth": _j(d.get("date_of_birth")), "hire_date": _j(d.get("hire_date")),
            "termination_date": _j(d.get("termination_date")), "province_of_employment": d.get("province"),
            "department": d.get("department"), "job_title": d.get("job_title"), "pay_type": d.get("pay_type"),
            "annual_salary": d.get("annual_salary"), "hourly_rate": d.get("hourly_rate"),
            "standard_hours_per_week": d.get("standard_hours_per_week"), "pay_frequency": d.get("pay_frequency"),
            "td1_federal_claim": d.get("td1_federal_claim"), "td1_provincial_claim": d.get("td1_provincial_claim"),
            "additional_tax_per_period": d.get("additional_tax_per_period"), "cpp_exempt": d.get("cpp_exempt"),
            "ei_exempt": d.get("ei_exempt"), "vacation_percent": d.get("vacation_percent"),
            "bank_institution": d.get("bank_institution"), "bank_transit": d.get("bank_transit"),
            "bank_account": d.get("bank_account"), "status": d.get("status"), "address_country": "CA",
        }.items() if v is not None})
    items = await emp_client["upsert"](payload, str(batch.id))
    for it in items:
        led.add("employees", it["employee_id"], it["action"], it.get("prior"), it["employee_id"], it["employee_number"])
    return {"employees_inserted": sum(1 for i in items if i["action"] == "insert"),
            "employees_updated": sum(1 for i in items if i["action"] == "update")}


async def _commit_ca_td1(db, led, org, batch, rows, emp_by_code, emp_client, leave_types):
    payload = [{k: v for k, v in {"employee_number": r["data"]["employee_code"],
                                  "td1_federal_claim": r["data"].get("td1_federal_claim"),
                                  "td1_provincial_claim": r["data"].get("td1_provincial_claim"),
                                  "additional_tax_per_period": r["data"].get("additional_tax_per_period"),
                                  "cpp_exempt": r["data"].get("cpp_exempt"), "ei_exempt": r["data"].get("ei_exempt")}.items()
                if v is not None} for r in rows]
    items = await emp_client["upsert"](payload, str(batch.id))
    for it in items:
        led.add("employees", it["employee_id"], it["action"], it.get("prior"), it["employee_id"], it["employee_number"])
    return {"employees_updated": len(items)}


async def _commit_ca_salary_history(db, led, org, batch, rows, emp_by_code, emp_client, leave_types):
    payload = [{"employee_id": str(_eid(r, emp_by_code)), "effective_from": _j(r["data"]["effective_from"]),
                "effective_to": _j(r["data"].get("effective_to")), "pay_type": r["data"].get("pay_type") or "salary",
                "annual_amount": r["data"].get("annual_amount"), "hourly_rate": r["data"].get("hourly_rate"),
                "reason": r["data"].get("reason"), "note": r["data"].get("note")} for r in rows]
    items = await emp_client["comp_history"](payload, str(batch.id))
    for it in items:
        led.add("comp_history", it["id"], it["action"], it.get("prior"), it["employee_id"], it["employee_id"])
    return {"salary_rows": len(items)}


async def _commit_ca_ytd_monthly(db, led, org, batch, rows, emp_by_code, emp_client, leave_types):
    touched: set[tuple] = set()
    for r in rows:
        d = r["data"]
        eid = _eid(r, emp_by_code)
        y, m = d["month"]
        ym = f"{y:04d}-{m:02d}"
        vals = {k: float(d.get(k) or 0) for k in ("gross", "cpp", "cpp2", "ei", "qpip", "income_tax", "pensionable",
                                                 "insurable", "vac_accrued", "vac_paid", "net")}
        row = (await db.execute(select(CaOpeningYtd).where(
            CaOpeningYtd.org_id == org, CaOpeningYtd.employee_id == eid, CaOpeningYtd.year == y,
            CaOpeningYtd.month == ym))).scalar_one_or_none()
        key = f"{d['employee_code']}|{ym}"
        if row:
            led.add("ca_opening_ytd", row.id, "update", {k: _j(getattr(row, k)) for k in vals}, eid, key)
            for k, v in vals.items():
                setattr(row, k, v)
            row.source, row.batch_id = f"ytd_monthly:{batch.vendor}"[:60], batch.id
        else:
            row = CaOpeningYtd(org_id=org, employee_id=eid, year=y, month=ym, source=f"ytd_monthly:{batch.vendor}"[:60],
                               batch_id=batch.id, **vals)
            db.add(row)
            await db.flush()
            led.add("ca_opening_ytd", row.id, "insert", None, eid, key)
        touched.add((eid, y))
    # roll the months up into employee_ytd (the T4 ledger): opening = sum of imported months
    for eid, y in touched:
        months = (await db.execute(select(CaOpeningYtd).where(
            CaOpeningYtd.org_id == org, CaOpeningYtd.employee_id == eid, CaOpeningYtd.year == y))).scalars().all()
        sums = {k: round(sum(float(getattr(x, k) or 0) for x in months), 2)
                for k in ("gross", "cpp", "cpp2", "ei", "income_tax", "pensionable", "insurable", "vac_accrued", "vac_paid")}
        ytd = (await db.execute(select(EmployeeYtd).where(
            EmployeeYtd.org_id == org, EmployeeYtd.employee_id == eid, EmployeeYtd.year == y))).scalar_one_or_none()
        if ytd:
            led.add("employee_ytd", ytd.id, "update", {k: _j(getattr(ytd, k)) for k in sums}, eid, f"ytd|{y}")
            for k, v in sums.items():
                setattr(ytd, k, v)
        else:
            ytd = EmployeeYtd(org_id=org, employee_id=eid, year=y, **sums)
            db.add(ytd)
            await db.flush()
            led.add("employee_ytd", ytd.id, "insert", None, eid, f"ytd|{y}")
    return {"employees_rolled_up": len(touched),
            "note": "employee_ytd set to the SUM of imported months — any pay runs already made in Lexora HRM for this year are not added on top; import before the first run."}


async def _commit_ca_leave_balances(db, led, org, batch, rows, emp_by_code, emp_client, leave_types):
    for r in rows:
        d = r["data"]
        eid = _eid(r, emp_by_code)
        lt = leave_types.get(d["leave_type"]) or {}
        year = int(d["fiscal_year"])
        cur = (await db.execute(text(
            "SELECT id, allocated, carried_over, used FROM leave_balances WHERE org_id = :org AND employee_id = :emp "
            "AND fiscal_year = :fy AND leave_type_code = :code LIMIT 1"),
            {"org": org, "emp": str(eid), "fy": year, "code": d["leave_type"]})).mappings().first()
        key = f"{d['employee_code']}|{d['leave_type']}|{year}"
        if cur:
            led.add("leave_balances", cur["id"], "update", {"allocated": _j(cur["allocated"]), "carried_over": _j(cur["carried_over"]),
                                                             "used": _j(cur["used"])}, eid, key)
            await db.execute(text("UPDATE leave_balances SET allocated = :a, carried_over = 0, used = :u, updated_at = now() WHERE id = :id"),
                             {"a": d.get("opening") or 0, "u": d.get("used") or 0, "id": cur["id"]})
        else:
            nid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO leave_balances (id, org_id, employee_id, fiscal_year, leave_type_code, leave_type_name, paid, allocated, "
                "carried_over, used, pending, created_at, updated_at) VALUES (:id, :org, :emp, :fy, :code, :name, :paid, :a, 0, :u, 0, now(), now())"),
                {"id": str(nid), "org": org, "emp": str(eid), "fy": year, "code": d["leave_type"],
                 "name": lt.get("name") or d["leave_type"], "paid": bool(lt.get("paid", True)),
                 "a": d.get("opening") or 0, "u": d.get("used") or 0})
            led.add("leave_balances", nid, "insert", None, eid, key)
    return {}


_ORM = {"ca_opening_ytd": CaOpeningYtd, "employee_ytd": EmployeeYtd}


async def rollback_batch(db, batch, emp_client: dict) -> dict:
    org = str(batch.org_id)
    rows = (await db.execute(select(CaImportRow).where(CaImportRow.batch_id == batch.id, CaImportRow.org_id == org)
                             .order_by(CaImportRow.seq.desc()))).scalars().all()
    counts = {"deleted": 0, "restored": 0, "employees": 0, "salary_rows": 0, "missing": 0}
    emp_items, comp_items = [], []
    for lr in rows:
        if lr.target_table == "employees":
            emp_items.append({"employee_id": lr.target_id, "action": lr.action, "prior": lr.prior}); continue
        if lr.target_table == "comp_history":
            comp_items.append({"id": lr.target_id, "action": lr.action, "prior": lr.prior}); continue
        if lr.target_table == "leave_balances":
            if lr.action == "insert":
                res = await db.execute(text("DELETE FROM leave_balances WHERE id = :id AND org_id = :org"), {"id": lr.target_id, "org": org})
                counts["deleted" if res.rowcount else "missing"] += 1
            else:
                p = lr.prior or {}
                await db.execute(text("UPDATE leave_balances SET allocated = :a, carried_over = :c, used = :u, updated_at = now() "
                                      "WHERE id = :id AND org_id = :org"),
                                 {"a": p.get("allocated") or 0, "c": p.get("carried_over") or 0, "u": p.get("used") or 0,
                                  "id": lr.target_id, "org": org})
                counts["restored"] += 1
            continue
        model = _ORM.get(lr.target_table)
        obj = (await db.execute(select(model).where(model.id == uuid.UUID(lr.target_id), model.org_id == org))).scalar_one_or_none() if model else None
        if not obj:
            counts["missing"] += 1; continue
        if lr.action == "insert":
            await db.delete(obj); counts["deleted"] += 1
        else:
            for k, v in (lr.prior or {}).items():
                setattr(obj, k, v)
            counts["restored"] += 1
    if emp_items:
        merged: dict[str, dict] = {}
        for it in reversed(emp_items):
            cur = merged.get(it["employee_id"])
            if cur is None:
                merged[it["employee_id"]] = {**it, "prior": dict(it.get("prior") or {})}
            elif cur["action"] != "insert":
                for k, v in (it.get("prior") or {}).items():
                    cur["prior"].setdefault(k, v)
        res = await emp_client["restore"](list(merged.values()))
        counts["employees"] = (res or {}).get("deleted", 0) + (res or {}).get("restored", 0)
    if comp_items:
        res = await emp_client["comp_restore"](comp_items)
        counts["salary_rows"] = (res or {}).get("deleted", 0) + (res or {}).get("restored", 0)
    return counts
