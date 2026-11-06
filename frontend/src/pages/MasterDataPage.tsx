import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  Database, Plus, Trash2, Building2, BadgeCheck, MapPin, CalendarDays, Sparkles,
  Pencil, Check, Power, Coins, CalendarRange, Wallet, HeartPulse, CalendarCheck,
} from "lucide-react";
import { PageHeader, Spinner, Empty } from "../components/ui";
import { mastersApi, MasterKind } from "../api/endpoints";
import { useUrlTab } from "../search/useUrlTab";
import AppModal, { FormGrid, FormSection } from "../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../lib/validators";
import { regionOptions, PROVINCE_NAMES } from "../api/regions";

type FieldType = "text" | "number" | "date" | "select" | "boolean";
type ColType = "boolean" | "date" | "chip" | "money" | "state";
interface Opt { value: string; label: string; }
interface FieldCfg {
  k: string; label: string; type?: FieldType; placeholder?: string; options?: Opt[]; boolDefault?: boolean;
  /** required on save */ req?: boolean;
  /** short helper under the field */ help?: string;
  /** column length in the DB (String(n)) */ maxLen?: number;
  /** numeric bounds */ min?: number; max?: number; int?: boolean;
  /** NOT NULL numeric column: a blank box is saved as 0 */ zeroIfBlank?: boolean;
  /** default for a new row */ def?: any;
  /** label for the empty select option */ emptyLabel?: string;
  /** span the full row */ wide?: boolean;
  /** form section heading */ section?: string;
  /** uppercase on save (codes) */ upper?: boolean;
}
interface ColCfg { k: string; label: string; type?: ColType; }
interface KindCfg {
  key: MasterKind; label: string; single: string; icon: any; hint?: string; fields: FieldCfg[]; cols: ColCfg[];
  /** cross-field checks: { field: message|null } */
  check?: (r: Record<string, any>) => Record<string, string | null>;
}

// Canadian province / territory codes the backend stores in the 2-letter `province` column.
const STATES: Opt[] = regionOptions().map((o) => ({ value: o.value, label: `${o.label} (${o.value})` }));
const HOLIDAY_STATES: Opt[] = STATES;
const ACCRUALS: Opt[] = [
  { value: "annual", label: "Credited once a year" }, { value: "monthly", label: "Credited monthly" }, { value: "per_pay", label: "Credited each pay run" },
];
const PC_KINDS: Opt[] = [
  { value: "earning", label: "Earning" }, { value: "deduction", label: "Deduction" }, { value: "employer", label: "Employer cost" },
];
const CALC_METHODS: Opt[] = [
  { value: "flat", label: "Flat amount" }, { value: "percent", label: "% of base" }, { value: "rate_x_hours", label: "Rate × hours" },
];
const FY_STATUS: Opt[] = [{ value: "open", label: "Open" }, { value: "closed", label: "Closed" }];
const LEAVE_CATEGORY: Opt[] = [{ value: "usable", label: "Usable — taken at will" }, { value: "event", label: "Event leave — shown separately" }];

const NAME = (label = "Name", placeholder = ""): FieldCfg => ({ k: "name", label, placeholder, req: true, maxLen: 150, section: "Basics" });
const CODE = (placeholder = "", help = "Short code shown in reports and imports, e.g. OPS."): FieldCfg =>
  ({ k: "code", label: "Code", placeholder, maxLen: 30, help, section: "Basics", upper: true });

const KINDS: KindCfg[] = [
  { key: "departments", label: "Departments", single: "Department", icon: Building2,
    hint: "Cost-center and GL mapping flow into payroll journal entries.",
    fields: [
      NAME("Name", "Operations"), CODE("OPS"),
      { k: "description", label: "Description", placeholder: "What this department does", maxLen: 300, wide: true, section: "Basics" },
      { k: "cost_center", label: "Cost center", placeholder: "CC-200", maxLen: 30, section: "Accounting", help: "Salary cost of this department is booked to this cost center." },
      { k: "gl_code", label: "GL code", placeholder: "5000", maxLen: 30, section: "Accounting", help: "Ledger account for this department's salary expense." },
    ],
    cols: [{ k: "name", label: "Name" }, { k: "code", label: "Code" }, { k: "cost_center", label: "Cost Center" }, { k: "gl_code", label: "GL Code" }, { k: "description", label: "Description" }] },

  { key: "designations", label: "Job Titles", single: "Job Title", icon: BadgeCheck,
    hint: "Titles marked overtime-exempt skip overtime accrual in the pay run.",
    fields: [
      NAME("Title", "Senior Executive"), CODE("SR-EXEC"),
      { k: "level", label: "Level", placeholder: "Senior", maxLen: 50, section: "Basics", help: "Free text, e.g. Junior / Senior / Lead." },
      { k: "job_family", label: "Job family", placeholder: "Individual Contributor", maxLen: 60, section: "Basics" },
      { k: "overtime_exempt", label: "Overtime-exempt", type: "boolean", section: "Basics", help: "Tick for managerial / supervisory titles that do not earn overtime." },
    ],
    cols: [{ k: "name", label: "Title" }, { k: "code", label: "Code" }, { k: "level", label: "Level" }, { k: "job_family", label: "Job Family" }, { k: "overtime_exempt", label: "OT Exempt", type: "boolean" }] },

  { key: "grades", label: "Grades", single: "Grade", icon: BadgeCheck,
    hint: "Grades / bands picked on the employee form. Level 1 is the most junior.",
    fields: [
      NAME("Grade", "G3"), CODE("G3"),
      { k: "level", label: "Level (sort order)", type: "number", int: true, min: 1, max: 999, placeholder: "3", section: "Basics", help: "1 = most junior. Used to sort grades." },
      { k: "description", label: "Description", placeholder: "Senior executive", maxLen: 300, wide: true, section: "Basics" },
    ],
    cols: [{ k: "name", label: "Grade" }, { k: "code", label: "Code" }, { k: "level", label: "Level" }, { k: "description", label: "Description" }] },

  { key: "locations", label: "Locations", single: "Location", icon: MapPin,
    hint: "Work locations. The province drives the statutory holiday calendar and provincial rules (employment standards, EHT, WCB, provincial tax).",
    fields: [
      NAME("Name", "Toronto HQ"), CODE("TOR"),
      { k: "is_remote", label: "Remote / work from home", type: "boolean", section: "Basics" },
      { k: "address", label: "Address", placeholder: "Street address, suite", maxLen: 300, wide: true, section: "Address" },
      { k: "city", label: "City", placeholder: "Toronto", maxLen: 100, section: "Address" },
      { k: "province", label: "Province / territory", type: "select", options: STATES, section: "Address", help: "Pick the province or territory the people at this location work in." },
      { k: "postal_code", label: "Postal code", placeholder: "M5V 2T6", maxLen: 7, section: "Address" },
      { k: "timezone", label: "Time zone", placeholder: "America/Toronto", def: "America/Toronto", maxLen: 40, section: "Address", help: "IANA name, e.g. America/Toronto, America/Vancouver, America/Halifax." },
    ],
    check: (r) => ({ postal_code: isBlank(r.postal_code) || /^[ABCEGHJ-NPRSTVXY][0-9][ABCEGHJ-NPRSTV-Z] ?[0-9][ABCEGHJ-NPRSTV-Z][0-9]$/.test(String(r.postal_code).trim().toUpperCase()) ? null : "Postal code looks like A1A 1A1" }),
    cols: [{ k: "name", label: "Name" }, { k: "city", label: "City" }, { k: "province", label: "Province", type: "state" }, { k: "postal_code", label: "Postal code" }, { k: "is_remote", label: "Remote", type: "boolean" }] },

  { key: "leave-types", label: "Leave Types", single: "Leave Type", icon: CalendarDays,
    hint: "The leave kinds employees can apply for. Statutory minimums differ by province — check the provincial employment standards (or the Canada Labour Code for federal employers) before setting days.",
    fields: [
      NAME("Name", "Vacation"), CODE("VAC", "Short code, e.g. VAC / SICK / PERS. Used on balances and pay stubs."),
      { k: "paid", label: "Paid leave", type: "boolean", boolDefault: true, section: "Basics", help: "Untick for leave without pay (salary is cut for those days)." },
      { k: "category", label: "Counts as", type: "select", options: LEAVE_CATEGORY, emptyLabel: "Decide from the code", section: "Basics",
        help: "Event leave (maternity, paternity…) is listed apart and never added to “days you can take”." },
      { k: "days_per_year", label: "Days per year", type: "number", min: 0, max: 366, zeroIfBlank: true, placeholder: "e.g. 12", section: "Entitlement",
        help: "Full-year entitlement. 0 = no fixed quota (e.g. comp-off, leave without pay)." },
      { k: "accrual", label: "How it is credited", type: "select", options: ACCRUALS, def: "annual", section: "Entitlement" },
      { k: "carryover_max", label: "Carry-forward cap (days)", type: "number", int: true, min: 0, max: 999, zeroIfBlank: true, placeholder: "0", section: "Entitlement",
        help: "Most unused days that roll into next year. 0 = lapses at year end." },
      { k: "attachment_after_days", label: "Document needed after (days)", type: "number", int: true, min: 1, max: 366, placeholder: "e.g. 2", section: "Rules",
        help: "A request longer than this needs a supporting document (e.g. medical certificate). Blank = never." },
    ],
    cols: [{ k: "name", label: "Name" }, { k: "code", label: "Code" }, { k: "days_per_year", label: "Days / yr" }, { k: "paid", label: "Paid", type: "boolean" }, { k: "accrual", label: "Credited" }, { k: "carryover_max", label: "Carry fwd" }, { k: "category", label: "Counts as", type: "chip" }, { k: "attachment_after_days", label: "Doc after" }] },

  { key: "pay-components", label: "Pay Components", single: "Pay Component", icon: Coins,
    hint: "Codes for earnings and deductions entered on a pay run (bonus, retro pay, recoveries…). CPP/QPP, EI and income tax are computed by payroll; the flags below decide which bases each amount counts toward.",
    fields: [
      NAME("Name", "Performance bonus"), CODE("BONUS", "Code used on pay-run entries and imports — keep it unique."),
      { k: "kind", label: "Type", type: "select", options: PC_KINDS, def: "earning", req: true, section: "Basics" },
      { k: "calc_method", label: "Calculation", type: "select", options: CALC_METHODS, def: "flat", section: "Basics" },
      { k: "taxable", label: "Taxable (income tax)", type: "boolean", boolDefault: true, section: "Treatment" },
      { k: "non_periodic", label: "One-off (bonus / arrears)", type: "boolean", section: "Treatment", help: "Paid occasionally, not every month — tax is worked out on the one-off amount." },
      { k: "pensionable", label: "Pensionable (CPP / QPP)", type: "boolean", boolDefault: true, section: "Treatment" },
      { k: "insurable", label: "Insurable (EI)", type: "boolean", boolDefault: true, section: "Treatment" },
      { k: "vacationable", label: "Counts for vacation pay", type: "boolean", boolDefault: true, section: "Treatment" },
      { k: "gl_account", label: "GL account", placeholder: "6000", maxLen: 30, section: "Accounting", help: "Ledger account this amount posts to in the payroll journal." },
      { k: "t4_box", label: "T4 / RL-1 box", placeholder: "e.g. 40", maxLen: 10, section: "Accounting", help: "The slip box this amount is reported in at year end. Blank = employment income (box 14)." },
    ],
    cols: [{ k: "name", label: "Name" }, { k: "code", label: "Code" }, { k: "kind", label: "Type", type: "chip" }, { k: "calc_method", label: "Calc" }, { k: "taxable", label: "Tax", type: "boolean" }, { k: "non_periodic", label: "One-off", type: "boolean" }, { k: "gl_account", label: "GL" }] },

  { key: "fiscal-years", label: "Fiscal Years", single: "Fiscal Year", icon: CalendarRange,
    hint: "Your books / reporting year. The payroll tax year (T4 / RL-1) is always the calendar year.",
    fields: [
      NAME("Name", "FY 2026"), CODE("2026"),
      { k: "start_date", label: "Starts on", type: "date", req: true, section: "Period" },
      { k: "end_date", label: "Ends on", type: "date", req: true, section: "Period" },
      { k: "status", label: "Status", type: "select", options: FY_STATUS, def: "open", req: true, section: "Period", help: "Closed years stay visible but should not take new postings." },
      { k: "is_current", label: "Current year", type: "boolean", section: "Period", help: "Mark only one year as current." },
    ],
    check: (r) => ({ end_date: v.dateOrder(r.start_date, r.end_date, "End date cannot be before the start date") }),
    cols: [{ k: "name", label: "Name" }, { k: "start_date", label: "Start", type: "date" }, { k: "end_date", label: "End", type: "date" }, { k: "status", label: "Status", type: "chip" }, { k: "is_current", label: "Current", type: "boolean" }] },

  { key: "cost-centers", label: "Cost Centers", single: "Cost Center", icon: Wallet,
    fields: [
      NAME("Name", "Operations"), CODE("CC-200"),
      { k: "gl_segment", label: "GL segment", placeholder: "200", maxLen: 30, section: "Basics", help: "Segment appended to ledger codes for this cost center." },
    ],
    cols: [{ k: "name", label: "Name" }, { k: "code", label: "Code" }, { k: "gl_segment", label: "GL Segment" }] },

  { key: "benefit-plans", label: "Benefit Plans", single: "Benefit Plan", icon: HeartPulse,
    hint: "The employee's share is deducted on the payslip; an employer share marked taxable is added to the employee's taxable pay.",
    fields: [
      NAME("Name", "Extended health and dental"), CODE("EHD"),
      { k: "carrier", label: "Insurer / provider", placeholder: "Insurer name", maxLen: 100, section: "Basics" },
      { k: "ee_cost", label: "Employee cost per pay run ($)", type: "number", min: 0, zeroIfBlank: true, placeholder: "0", section: "Cost" },
      { k: "er_cost", label: "Employer cost per pay run ($)", type: "number", min: 0, zeroIfBlank: true, placeholder: "0", section: "Cost" },
      { k: "taxable_benefit", label: "Employer share is taxable", type: "boolean", section: "Cost", help: "Tick if the employer's share is a taxable benefit (e.g. group term life)." },
    ],
    cols: [{ k: "name", label: "Name" }, { k: "carrier", label: "Provider" }, { k: "ee_cost", label: "Employee Cost", type: "money" }, { k: "er_cost", label: "Employer Cost", type: "money" }, { k: "taxable_benefit", label: "Taxable", type: "boolean" }] },

  { key: "stat-holidays", label: "Holidays", single: "Holiday", icon: CalendarCheck,
    hint: "Statutory holidays by province and year. Leave Province blank for a federal holiday that applies everywhere.",
    fields: [
      { ...NAME("Holiday", "Canada Day"), section: "Holiday" },
      { k: "holiday_date", label: "Date", type: "date", req: true, section: "Holiday" },
      { k: "province", label: "Province / territory", type: "select", options: HOLIDAY_STATES, emptyLabel: "Federal — all provinces", section: "Holiday" },
      { k: "year", label: "Year", type: "number", int: true, min: 2000, max: 2100, placeholder: "2026", section: "Holiday", help: "Filled from the date if left blank." },
      { k: "is_optional", label: "Optional holiday (employees pick)", type: "boolean", boolDefault: false, section: "Holiday",
        help: "Restricted holiday — employees choose a few of these each year." },
    ],
    check: (r) => ({
      year: r.year && r.holiday_date && String(r.holiday_date).slice(0, 4) !== String(r.year) ? "Year does not match the holiday date" : null,
    }),
    cols: [{ k: "name", label: "Holiday" }, { k: "holiday_date", label: "Date", type: "date" }, { k: "province", label: "Province", type: "state" }, { k: "year", label: "Year" }, { k: "is_optional", label: "Optional", type: "boolean" }] },
];

const initRow = (cfg: KindCfg): Record<string, any> => {
  const r: Record<string, any> = {};
  cfg.fields.forEach((f) => {
    if (f.type === "boolean") r[f.k] = f.boolDefault ?? false;
    else if (f.def !== undefined) r[f.k] = f.def;
  });
  return r;
};
const fmtDate = (v: any) => {
  if (!v) return "—";
  const d = new Date(v);
  return isNaN(d.getTime()) ? v : d.toLocaleDateString("en-CA", { year: "numeric", month: "short", day: "numeric" });
};
const CHIP: Record<string, string> = {
  earning: "bg-green-50 text-green-700", deduction: "bg-amber-50 text-amber-700",
  employer: "bg-blue-50 text-blue-700", open: "bg-green-50 text-green-700", closed: "bg-gray-100 text-gray-500",
  usable: "bg-teal-50 text-teal-700", event: "bg-purple-50 text-purple-700",
};
function BoolCell({ v }: { v: any }) {
  return v ? <Check className="w-4 h-4 text-green-500" /> : <span className="text-gray-300">—</span>;
}
const isBlank = (x: any) => x === undefined || x === null || String(x).trim() === "";

/** Field checks driven by the config (required, length, numeric bounds) + the kind's cross-field checks. */
function validate(cfg: KindCfg, r: Record<string, any>): Record<string, string> {
  const checks: Record<string, string | null> = {};
  for (const f of cfg.fields) {
    const val = r[f.k];
    let m: string | null = null;
    if (f.req && f.type !== "boolean") m = v.required(val, f.label);
    if (!m && f.maxLen) m = v.maxLen(val, f.maxLen, f.label);
    if (!m && f.type === "number" && !isBlank(val)) {
      m = v.range(val, f.min ?? 0, f.max, f.label);
      if (!m && f.int && !Number.isInteger(Number(val))) m = `${f.label} must be a whole number`;
    }
    checks[f.k] = m;
  }
  const extra = cfg.check ? cfg.check(r) : {};
  for (const [k, m] of Object.entries(extra)) if (m && !checks[k]) checks[k] = m;
  return collect(checks);
}

/** Only the configured fields go on the wire; blanks are cleared (or 0 for NOT NULL numbers). */
function toPayload(cfg: KindCfg, r: Record<string, any>, editing: boolean): Record<string, any> {
  const out: Record<string, any> = {};
  for (const f of cfg.fields) {
    let val = r[f.k];
    if (typeof val === "string") val = val.trim();
    // codes are upper-cased for NEW rows only — an existing code may already be referenced elsewhere
    if (f.upper && !editing && typeof val === "string") val = val.toUpperCase();
    if (f.type === "number") {
      if (isBlank(val)) { if (f.zeroIfBlank) val = 0; else if (editing) val = null; else continue; }
      else val = Number(val);
    } else if (f.type !== "boolean" && isBlank(val)) {
      if (!editing) continue;
      val = f.type === "date" ? null : f.type === "select" ? null : "";
    }
    out[f.k] = val;
  }
  // a holiday's year follows its date when not typed
  if (cfg.key === "stat-holidays" && isBlank(out.year) && out.holiday_date) out.year = Number(String(out.holiday_date).slice(0, 4));
  return out;
}

export default function MasterDataPage() {
  const qc = useQueryClient();
  const [active, setActive] = useUrlTab<MasterKind>("departments");   // ?tab= (global search deep link)
  const cfg = KINDS.find((k) => k.key === active) || KINDS[0];
  // the add / edit dialog: null = closed; id null = adding
  const [form, setForm] = useState<{ id: string | null; row: Record<string, any> } | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});

  // include inactive so deactivated rows stay visible (and reactivatable)
  const { data, isLoading } = useQuery({ queryKey: ["m", active], queryFn: () => mastersApi.list(active, true) });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["m", active] });
  const sections = useMemo(() => {
    const out: { title: string; fields: FieldCfg[] }[] = [];
    for (const f of cfg.fields) {
      const t = f.section || "Details";
      const s = out.find((x) => x.title === t);
      if (s) s.fields.push(f); else out.push({ title: t, fields: [f] });
    }
    return out;
  }, [cfg]);

  const switchTab = (k: MasterKind) => { setActive(k); setForm(null); setErrs({}); };
  const openAdd = () => { setErrs({}); setForm({ id: null, row: initRow(cfg) }); };
  const openEdit = (r: any) => {
    const row: Record<string, any> = {};
    for (const f of cfg.fields) row[f.k] = f.type === "date" ? (r[f.k] ?? "").slice(0, 10) : r[f.k] ?? (f.type === "boolean" ? false : "");
    setErrs({}); setForm({ id: r.id, row });
  };
  const close = () => { setForm(null); setErrs({}); };

  const add = useMutation({
    mutationFn: (body: Record<string, any>) => mastersApi.create(active, body),
    onSuccess: () => { invalidate(); close(); toast.success(`${cfg.single} added`); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not add"),
  });
  const update = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, any> }) => mastersApi.update(active, id, body),
    onSuccess: () => { invalidate(); close(); toast.success("Saved"); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not save"),
  });
  const toggle = useMutation({
    mutationFn: ({ id, on }: { id: string; on: boolean }) => mastersApi.update(active, id, { is_active: on }),
    onSuccess: () => { invalidate(); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not change status"),
  });
  const del = useMutation({
    mutationFn: (id: string) => mastersApi.remove(active, id),
    onSuccess: () => { invalidate(); toast.success("Deleted"); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not delete"),
  });
  const seed = useMutation({
    mutationFn: () => mastersApi.seedDefaults(),
    onSuccess: () => { KINDS.forEach((k) => qc.invalidateQueries({ queryKey: ["m", k.key] })); toast.success("Starter data loaded"); },
  });

  const save = () => {
    if (!form) return;
    const e = validate(cfg, form.row);
    // duplicate code within this master (case-insensitive)
    const code = String(form.row.code ?? "").trim().toUpperCase();
    if (code && !e.code && (data || []).some((r: any) => r.id !== form.id && String(r.code ?? "").toUpperCase() === code))
      e.code = `Code ${code} is already used by another ${cfg.single.toLowerCase()}`;
    setErrs(e);
    if (Object.keys(e).length) return toast.error(firstError(e));
    const body = toPayload(cfg, form.row, !!form.id);
    if (form.id) update.mutate({ id: form.id, body }); else add.mutate(body);
  };
  const setField = (k: string, val: any) => {
    setForm((s) => (s ? { ...s, row: { ...s.row, [k]: val } } : s));
    if (errs[k]) setErrs((x) => { const n = { ...x }; delete n[k]; return n; });
  };

  // One field control for the dialog.
  const control = (f: FieldCfg, val: any) => {
    const bad = errs[f.k] ? "!border-red-400" : "";
    if (f.type === "boolean")
      return (
        <label className="flex items-center gap-2 text-sm text-gray-700 select-none cursor-pointer min-h-[38px]">
          <input type="checkbox" className="w-4 h-4 accent-primary-600" checked={!!val} onChange={(e) => setField(f.k, e.target.checked)} />
          {f.label}
        </label>
      );
    if (f.type === "select") {
      const opts = f.options || [];
      const known = isBlank(val) || opts.some((o) => o.value === val);
      return (
        <select className={`input ${bad}`} value={val ?? ""} onChange={(e) => setField(f.k, e.target.value)}>
          <option value="">{f.emptyLabel || "—"}</option>
          {!known && <option value={val}>{val} (old value)</option>}
          {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      );
    }
    if (f.type === "date")
      return <input className={`input ${bad}`} type="date" value={(val ?? "").slice(0, 10)} onChange={(e) => setField(f.k, e.target.value)} />;
    return (
      <input className={`input ${bad}`} type={f.type === "number" ? "number" : "text"} placeholder={f.placeholder}
        min={f.type === "number" ? f.min : undefined} max={f.type === "number" ? f.max : undefined}
        step={f.type === "number" ? (f.int ? 1 : "any") : undefined} maxLength={f.maxLen}
        value={val ?? ""} onChange={(e) => setField(f.k, f.type === "number" ? (e.target.value === "" ? "" : e.target.value) : e.target.value)} />
    );
  };

  const cell = (c: ColCfg, r: any) => {
    const val = r[c.k];
    if (c.type === "boolean") return <BoolCell v={val} />;
    if (c.type === "date") return fmtDate(val);
    if (c.type === "money") return Number(val || 0).toLocaleString("en-CA", { style: "currency", currency: "CAD" });
    if (c.type === "state") return isBlank(val) ? "Federal" : (PROVINCE_NAMES[val] || val);
    if (c.type === "chip") return val ? <span className={`px-2 py-0.5 rounded-full text-[11px] font-medium capitalize ${CHIP[val] || "bg-gray-100 text-gray-500"}`}>{val}</span> : "—";
    return isBlank(val) ? "—" : val;
  };

  const saving = add.isPending || update.isPending;

  return (
    <div>
      <PageHeader title="Master Data" icon={Database}
        subtitle="Organization reference data — the dropdowns the rest of the HRM and payroll draw on"
        actions={
          <div className="flex items-center gap-2">
            <button className="btn-secondary text-xs flex items-center gap-1.5" onClick={() => seed.mutate()} disabled={seed.isPending}>
              <Sparkles className="w-3.5 h-3.5" /> Load starter set
            </button>
            <button className="btn-primary text-sm flex items-center gap-1.5" onClick={openAdd} data-testid="master-add">
              <Plus className="w-4 h-4" /> Add {cfg.single.toLowerCase()}
            </button>
          </div>
        } />

      {/* Tabs */}
      <div className="flex gap-1 mb-4 overflow-x-auto">
        {KINDS.map((k) => (
          <button key={k.key} onClick={() => switchTab(k.key)}
            className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${
              active === k.key ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>
            <k.icon className="w-4 h-4" />{k.label}
          </button>
        ))}
      </div>

      {cfg.hint && <p className="text-xs text-gray-500 mb-3 -mt-1">{cfg.hint}</p>}

      {/* List */}
      <div className="card !p-0">
        {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
          : !data || data.length === 0 ? <Empty>No {cfg.label.toLowerCase()} yet — add one or load the starter set.</Empty>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr>
                    {cfg.cols.map((c) => <th key={c.k} className="px-4 py-2.5 font-semibold">{c.label}</th>)}
                    <th className="px-4 py-2.5 font-semibold">Status</th>
                    <th className="px-4 py-2.5 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {data.map((r: any) => (
                    <tr key={r.id} className={`border-t border-surface-border hover:bg-gray-50 ${!r.is_active ? "opacity-55" : ""}`}>
                      {cfg.cols.map((c) => (
                        <td key={c.k} className={`px-4 py-2 text-gray-700 align-middle ${c.k === "description" ? "max-w-[260px] truncate" : ""}`}>{cell(c, r)}</td>
                      ))}
                      <td className="px-4 py-2 align-middle">
                        {r.is_active ? <span className="badge-green">Active</span> : <span className="badge-amber">Inactive</span>}
                      </td>
                      <td className="px-4 py-2 text-right align-middle">
                        <div className="flex items-center justify-end gap-0.5">
                          <button onClick={() => openEdit(r)} title="Edit"
                            className="icon-btn text-gray-300 hover:text-primary-600"><Pencil className="w-4 h-4" /></button>
                          <button onClick={() => toggle.mutate({ id: r.id, on: !r.is_active })}
                            title={r.is_active ? "Deactivate" : "Activate"}
                            className={`icon-btn ${r.is_active ? "text-gray-300 hover:text-amber-500" : "text-green-500 hover:text-green-600"}`}>
                            <Power className="w-4 h-4" /></button>
                          <button onClick={() => { if (window.confirm(`Delete "${r.name}" permanently?`)) del.mutate(r.id); }}
                            title="Delete" className="icon-btn text-gray-300 hover:text-red-500"><Trash2 className="w-4 h-4" /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      {form && (
        <AppModal size="form" onClose={close} testId="master-form"
          title={form.id ? `Edit ${cfg.single.toLowerCase()}` : `Add ${cfg.single.toLowerCase()}`}
          subtitle={cfg.hint}
          footer={<>
            <button className="btn-secondary" onClick={close} disabled={saving}>Cancel</button>
            <button className="btn-primary inline-flex items-center gap-1.5" onClick={save} disabled={saving}>
              <Check className="w-4 h-4" />{saving ? "Saving…" : form.id ? "Save changes" : `Add ${cfg.single.toLowerCase()}`}
            </button>
          </>}>
          {sections.map((s) => (
            <FormSection key={s.title} title={s.title}>
              <FormGrid cols={3}>
                {s.fields.map((f) => (
                  <div key={f.k} className={f.wide ? "sm:col-span-2 lg:col-span-3" : ""}>
                    {f.type !== "boolean" && (
                      <label className="block text-xs font-medium text-gray-600 mb-1">{f.label}{f.req && <Req />}</label>
                    )}
                    {control(f, form.row[f.k])}
                    {f.help && <p className="text-[11px] text-gray-500 mt-0.5">{f.help}</p>}
                    <FieldError msg={errs[f.k]} />
                  </div>
                ))}
              </FormGrid>
            </FormSection>
          ))}
        </AppModal>
      )}
    </div>
  );
}
