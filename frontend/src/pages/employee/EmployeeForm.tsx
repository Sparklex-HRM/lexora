import { useMemo, useRef, useState, ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  X, User, MapPin, Briefcase, Wallet, Landmark, HeartPulse, BadgeCheck, FileText, KeyRound, LucideIcon,
  Upload, Trash2, Eye, CheckCircle2, Loader2, Send, RotateCcw, Ban, History,
} from "lucide-react";
import { Link } from "react-router-dom";
import EmployeeLifecyclePanel from "../../components/lifecycle/EmployeeLifecyclePanel";
import {
  employeeApi, mastersApi, opsApi, Employee, PAY_FREQUENCIES, PAY_TYPES,
} from "../../api/endpoints";
import {
  employeeFormApi, usersApi, FormOptions, StoredDoc, LinkResult, errText, openBlob,
} from "../../api/identity";
import { regionOptions, COUNTRY_NAME } from "../../api/regions";
import { entitiesApi } from "../../api/entities";   // D4: legal entity on the form
import { LinkBox } from "../UsersPage";
import { useAuthStore, ADMIN_ROLES } from "../../store/authStore";
import { CustomFieldsSection, saveCustomValues, useCustomFieldDefs } from "../../components/CustomFieldsSection";   // D1
import { ListPlus } from "lucide-react";
import { useModalLayer } from "../../components/Modal";   // Esc closes (top-most only), scroll lock
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1";
type Form = Record<string, any>;

/* Fields sent to the API. Identity / bank numbers are sent only when typed. */
const PLAIN = [
  "employee_number", "first_name", "last_name", "middle_name", "preferred_name", "email", "work_email", "date_of_birth",
  "gender", "marital_status", "phone", "address_line1", "address_line2", "city",
  "postal_code", "emergency_name", "emergency_relationship", "emergency_phone", "hire_date", "employment_type",
  "province_of_employment", "job_title", "department", "work_location", "branch_id", "branch_name", "entity_id", "grade",
  "manager_id", "status", "pay_type", "annual_salary", "hourly_rate", "standard_hours_per_week", "pay_frequency",
  "td1_federal_claim", "td1_provincial_claim", "additional_tax_per_period", "bank_institution", "bank_transit",
  "work_eligibility", "work_permit_expiry", "address_country",
  "contract_start_date", "contract_end_date", "contract_notice_days", "contract_ref",   // 2026-10-22 fixed-term term
  // 2026-10-24 forms wrap-up: columns the employee record already stores
  "home_phone", "province_of_residence", "probation_end_date", "termination_date", "division", "cost_center",
  "fte_percent", "overtime_eligible", "nominee_name", "nominee_relationship", "nominee_phone", "passport_expiry",
];
const SECRET = ["sin", "bank_account", "passport"];
const DATES = ["date_of_birth", "hire_date", "work_permit_expiry", "contract_start_date", "contract_end_date", "contract_notice_days",
  "probation_end_date", "termination_date", "passport_expiry"];
/* Which tab shows a field — Save jumps to the tab of the first error. */
const SECTION_OF: Record<string, string> = {
  first_name: "personal", last_name: "personal", middle_name: "personal", preferred_name: "personal", date_of_birth: "personal",
  email: "personal", work_email: "personal", address_country: "personal",
  sin: "statutory", passport: "statutory", passport_expiry: "statutory",
  phone: "contact", home_phone: "contact", postal_code: "contact", address_line1: "contact",
  emergency_phone: "emergency", emergency_name: "emergency", nominee_phone: "emergency", nominee_name: "emergency",
  hire_date: "employment", province_of_employment: "employment", contract_end_date: "employment", contract_notice_days: "employment",
  probation_end_date: "employment", termination_date: "employment", fte_percent: "employment",
  annual_salary: "compensation", hourly_rate: "compensation", standard_hours_per_week: "compensation",
  bank_account: "banking", bank_institution: "banking", bank_transit: "banking",
};

function blank(opts?: FormOptions): Form {
  return {
    employee_number: "", first_name: "", last_name: "", status: "active", employment_type: "full_time",
    address_country: "CA", province_of_employment: opts?.org_region || "",
    pay_type: "salary", annual_salary: "", hourly_rate: "", standard_hours_per_week: 40,
    pay_frequency: "biweekly", hire_date: todayISO(),
  };
}

/* Canadian format checks (SIN Luhn, postal code, bank numbers) — kept local to this form. */
const blankish = (s: unknown) => s === undefined || s === null || String(s).trim() === "";
function sinError(s: unknown): string | null {
  if (blankish(s)) return null;
  const d = String(s).replace(/[\s-]/g, "");
  if (!/^\d{9}$/.test(d)) return "SIN is 9 digits";
  let sum = 0;
  for (let i = 0; i < 9; i++) { let n = Number(d[i]) * (i % 2 ? 2 : 1); if (n > 9) n -= 9; sum += n; }
  return sum % 10 === 0 ? null : "This SIN fails the check-digit test — re-check the number";
}
const postalError = (s: unknown) => (blankish(s) || /^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ -]?\d[ABCEGHJ-NPRSTV-Z]\d$/i.test(String(s).trim()) ? null : "Postal code looks like A1A 1A1");
const bankAccountError = (s: unknown) => (blankish(s) || /^\d{7,12}$/.test(String(s).replace(/[\s-]/g, "")) ? null : "Account number is 7 to 12 digits");
const digitsError = (s: unknown, n: number, label: string) => (blankish(s) || new RegExp(`^\\d{${n}}$`).test(String(s).trim()) ? null : `${label} is ${n} digits`);

function Field({ label, children, wide, hint, req, err }: { label: string; children: ReactNode; wide?: boolean; hint?: string; req?: boolean; err?: string }) {
  return (
    <div className={wide ? "sm:col-span-2" : ""}>
      <label className={LABEL}>{label}{req && <Req />}</label>{children}
      <FieldError msg={err} />
      {hint && !err && <p className="text-[11px] text-gray-400 mt-1">{hint}</p>}
    </div>
  );
}

function SubHead({ children }: { children: ReactNode }) {
  return <h3 className="col-span-full mt-2 pb-1 border-b border-surface-border text-[11px] font-semibold uppercase tracking-wide text-primary-700">{children}</h3>;
}

export default function EmployeeForm({ employee, onClose }: { employee: Employee | null; onClose: () => void }) {
  useModalLayer(onClose);
  const qc = useQueryClient();
  const { data: opts } = useQuery({ queryKey: ["employees", "form-options"], queryFn: employeeFormApi.options, staleTime: 0 });
  const { data: depts } = useQuery({ queryKey: ["m", "departments"], queryFn: () => mastersApi.list("departments") });
  const { data: desigs } = useQuery({ queryKey: ["m", "designations"], queryFn: () => mastersApi.list("designations") });
  const { data: locs } = useQuery({ queryKey: ["m", "locations"], queryFn: () => mastersApi.list("locations") });
  const { data: branches } = useQuery({ queryKey: ["ops", "branches"], queryFn: () => opsApi.list("branches") });
  const { data: entData } = useQuery({ queryKey: ["entities", "headcount"], queryFn: entitiesApi.headcount, staleTime: 60_000 });   // D4
  const entities = (entData?.entities || []).filter((x) => x.is_active);

  const [form, setForm] = useState<Form | null>(employee ? { ...(employee as any), ...Object.fromEntries(SECRET.map((k) => [k, ""])) } : null);
  const [section, setSection] = useState("personal");
  // D1 custom fields: an existing employee saves them on its own section; a new one collects them and saves after create
  const cfDefs = useCustomFieldDefs("employee");
  const hasCustom = (cfDefs.data?.items || []).some((d) => d.active);
  const [cfValues, setCfValues] = useState<Record<string, any>>({});
  if (!form && opts) setForm(blank(opts));          // a NEW employee starts from the organization's country
  const f = form || blank(opts);
  const set = (k: string, v: any) => setForm((p) => ({ ...(p || blank(opts)), [k]: v }));
  const country = "CA";   // Lexora HRM is Canada-only
  const sym = "$";

  const SECTIONS: { key: string; label: string; icon: LucideIcon; editOnly?: boolean }[] = [
    { key: "personal", label: "Personal", icon: User },
    { key: "statutory", label: "SIN and tax", icon: BadgeCheck },
    { key: "contact", label: "Contact", icon: MapPin },
    { key: "emergency", label: "Emergency", icon: HeartPulse },
    { key: "employment", label: "Job", icon: Briefcase },
    { key: "compensation", label: "Pay", icon: Wallet },
    { key: "banking", label: "Bank account", icon: Landmark },
    ...(hasCustom ? [{ key: "custom", label: "Custom fields", icon: ListPlus }] : []),
    { key: "documents", label: "Documents", icon: FileText, editOnly: true },
    { key: "login", label: "Login", icon: KeyRound, editOnly: true },
    { key: "history", label: "History", icon: History, editOnly: true },
  ].filter((s) => !s.editOnly || !!employee);

  const save = useMutation({
    mutationFn: () => {
      const body: Form = {};
      for (const k of PLAIN) {
        let v = f[k];
        if (v === undefined) continue;
        if (v === "" && (DATES.includes(k) || k === "manager_id" || k === "branch_id")) v = null;
        if (k === "contract_notice_days" && v !== null && v !== "") v = Number(v);
        if (["annual_salary", "hourly_rate", "td1_federal_claim", "td1_provincial_claim", "additional_tax_per_period", "fte_percent"].includes(k))
          v = v === "" || v === null ? null : Number(v);
        if (!employee && (v === "" || v === null)) continue;      // create: let the server defaults apply
        // edit: send only what changed (an untouched "Reports to" must not wipe an old typed name)
        if (employee && String(v ?? "") === String((employee as any)[k] ?? "")) continue;
        body[k] = v;
      }
      for (const k of SECRET) if (String(f[k] || "").trim()) body[k] = String(f[k]).trim();
      if (!employee) { if (body.pay_type === "hourly") delete body.annual_salary; else delete body.hourly_rate; }
      if (employee && !Object.keys(body).length) return Promise.resolve(employee);
      return employee ? employeeApi.update(employee.id, body) : employeeApi.create(body as any);
    },
    onSuccess: async (e: Employee) => {
      qc.invalidateQueries({ queryKey: ["employees"] });
      if (!employee && Object.keys(cfValues).length) {
        const problem = await saveCustomValues("employee", e.id, cfValues, cfDefs.data?.items || []);
        if (problem) toast.error(`Employee added, but the custom fields were not saved: ${problem}`, { duration: 8000 });
        qc.invalidateQueries({ queryKey: ["cf"] });
      }
      toast.success(employee ? "Employee saved" : `Employee added — ${e.employee_number}`);
      onClose();
    },
    onError: (e: any) => toast.error(errText(e, "Could not save the employee")),
  });

  const [errs, setErrs] = useState<Record<string, string>>({});
  const submit = () => {
    const today = todayISO();
    // Format checks look only at what was typed now, so an old saved value never blocks an unrelated edit.
    const nv = (k: string) => (employee && String(f[k] ?? "") === String((employee as any)[k] ?? "") ? "" : f[k]);
    const found = collect({
      first_name: v.required(f.first_name, "First name") || v.maxLen(f.first_name, 100, "First name"),
      last_name: v.required(f.last_name, "Last name") || v.maxLen(f.last_name, 100, "Last name"),
      province_of_employment: f.province_of_employment ? null : "Choose the province of employment",
      date_of_birth: v.notFuture(f.date_of_birth, "Date of birth") || v.dateOrder(f.date_of_birth, f.hire_date, "Date of birth must be before the date of joining"),
      email: v.email(nv("email")), work_email: v.email(nv("work_email")),
      sin: sinError(f.sin),
      passport_expiry: f.passport_expiry && f.passport_expiry < today && String(f.passport || e?.passport_masked || "") ? "This passport has already expired" : null,
      home_phone: v.maxLen(f.home_phone, 30, "Home phone"),
      postal_code: postalError(nv("postal_code")),
      contract_end_date: v.dateOrder(f.contract_start_date || f.hire_date, f.contract_end_date, "Contract end cannot be before its start"),
      contract_notice_days: v.range(f.contract_notice_days, 0, 365, "Notice days"),
      probation_end_date: v.dateOrder(f.hire_date, f.probation_end_date, "Probation cannot end before the date of joining"),
      termination_date: v.dateOrder(f.hire_date, f.termination_date, "Last working day cannot be before the date of joining"),
      fte_percent: v.range(f.fte_percent, 1, 100, "Working-time share"),
      annual_salary: v.range(f.annual_salary, 0, undefined, "Yearly pay"), hourly_rate: v.range(f.hourly_rate, 0, undefined, "Hourly rate"),
      standard_hours_per_week: v.range(f.standard_hours_per_week, 0, 168, "Hours per week"),
      bank_account: bankAccountError(f.bank_account),
      bank_institution: digitsError(nv("bank_institution"), 3, "Institution number"), bank_transit: digitsError(nv("bank_transit"), 5, "Transit number"),
    });
    setErrs(found);
    if (Object.keys(found).length) {
      setSection(SECTION_OF[Object.keys(found)[0]] || section);
      return toast.error(firstError(found));
    }
    save.mutate();
  };

  const text = (k: string, { upper, ...props }: Record<string, any> = {}) =>
    <input {...props} className="input" value={f[k] ?? ""} onChange={(e) => set(k, upper ? e.target.value.toUpperCase() : e.target.value)} />;
  /* A saved number is shown masked; the box stays empty until HR types a new one. */
  const secret = (k: string, mask: string | null | undefined, props: Record<string, any> = {}) =>
    <input {...props} className="input" autoComplete="off" value={f[k] ?? ""} onChange={(e) => set(k, e.target.value)}
      placeholder={mask ? `Saved: ${mask} — type to change` : props.placeholder} />;
  const e = employee as any;
  const managers = useMemo(() => (opts?.managers || []).filter((m) => m.id !== employee?.id), [opts, employee]);

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-2 sm:p-4">
      <div role="dialog" aria-modal="true" aria-label={employee ? "Edit employee" : "Add employee"} className="bg-white rounded-2xl shadow-2xl w-full max-w-[min(110rem,96vw)] h-[94vh] sm:h-[90vh] overflow-hidden flex flex-col">
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-4 sm:px-5 py-3 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h2 className="font-display font-semibold text-gray-800 truncate">{employee ? "Edit employee" : "Add employee"}</h2>
            <p className="text-[11px] text-gray-500 truncate">
              {employee ? `${employee.employee_number} · ${employee.first_name} ${employee.last_name}` : "Fill what you have now. The rest can be added later."}
              {` · ${COUNTRY_NAME[country]}`}
            </p>
          </div>
          <button onClick={onClose} className="icon-btn text-gray-400"><X className="w-4 h-4" /></button>
        </div>

        <div className="flex flex-1 overflow-hidden">
          <div className="hidden md:flex flex-col w-44 border-r border-surface-border bg-surface p-3 gap-1 shrink-0 overflow-y-auto">
            {SECTIONS.map((s) => (
              <button key={s.key} onClick={() => setSection(s.key)}
                className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium text-left ${section === s.key ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>
                <s.icon className="w-4 h-4 shrink-0" />{s.label}
              </button>
            ))}
          </div>
          <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
            <div className="md:hidden flex gap-1 overflow-x-auto border-b border-surface-border p-2 shrink-0">
              {SECTIONS.map((s) => (
                <button key={s.key} onClick={() => setSection(s.key)}
                  className={`px-2.5 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ${section === s.key ? "bg-primary-50 text-primary-600" : "text-gray-500"}`}>{s.label}</button>
              ))}
            </div>

            <div className="flex-1 overflow-y-auto p-4 sm:p-5">
              {section === "personal" && (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
                  <Field label="Work country" hint="Lexora HRM runs Canadian payroll only.">
                    <input className="input bg-surface" value={COUNTRY_NAME.CA} readOnly />
                  </Field>
                  <Field label="Employee number" hint={employee ? undefined : `Leave empty to use the next number: ${opts?.next_number || "…"}`}>
                    {text("employee_number", { placeholder: employee ? "" : opts?.next_number || "Automatic" })}
                  </Field>
                  <Field label="Status">
                    <select className="input" value={f.status || "active"} onChange={(ev) => set("status", ev.target.value)}>
                      <option value="onboarding">Onboarding</option><option value="active">Active</option>
                      <option value="on_leave">On leave</option><option value="terminated">Left</option>
                    </select>
                  </Field>
                  <Field label="Date of birth" err={errs.date_of_birth}>{text("date_of_birth", { type: "date", max: todayISO() })}</Field>
                  <Field label="First name" req err={errs.first_name}>{text("first_name", { maxLength: 100 })}</Field>
                  <Field label="Middle name">{text("middle_name", { maxLength: 100 })}</Field>
                  <Field label="Last name" req err={errs.last_name}>{text("last_name", { maxLength: 100 })}</Field>
                  <Field label="Preferred name" hint="Shown in the directory and greetings; the legal name stays on payslips.">{text("preferred_name", { maxLength: 100 })}</Field>
                  <Field label="Gender">
                    <select className="input" value={f.gender || ""} onChange={(ev) => set("gender", ev.target.value)}>
                      <option value="">—</option>{["Female", "Male", "Non-binary", "Prefer not to say"].map((g) => <option key={g}>{g}</option>)}
                    </select>
                  </Field>
                  <Field label="Marital status">
                    <select className="input" value={f.marital_status || ""} onChange={(ev) => set("marital_status", ev.target.value)}>
                      <option value="">—</option><option value="single">Single</option><option value="married">Married</option>
                      <option value="divorced">Divorced</option><option value="widowed">Widowed</option>
                    </select>
                  </Field>
                  <Field label="Work e-mail" err={errs.work_email} hint="Used for the login.">{text("work_email", { type: "email" })}</Field>
                  <Field label="Personal e-mail" err={errs.email}>{text("email", { type: "email" })}</Field>
                </div>
              )}

              {section === "statutory" && (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                  <Field label="SIN" err={errs.sin} hint="9 digits. Stored encrypted, shown masked.">{secret("sin", e?.sin_masked, { maxLength: 11 })}</Field>
                  <Field label="Work eligibility">
                    <select className="input" value={f.work_eligibility || ""} onChange={(ev) => set("work_eligibility", ev.target.value)}>
                      <option value="">—</option><option value="citizen">Canadian citizen</option><option value="permanent_resident">Permanent resident</option>
                      <option value="work_permit">Work permit</option><option value="study_permit">Study permit</option>
                    </select>
                  </Field>
                  <Field label="Permit expiry">{text("work_permit_expiry", { type: "date", disabled: !["work_permit", "study_permit"].includes(f.work_eligibility) })}</Field>
                  <Field label="TD1 federal claim ($)" hint="The total from the employee's federal TD1.">{text("td1_federal_claim", { type: "number" })}</Field>
                  <Field label="TD1 provincial claim ($)" hint="The total from the provincial TD1.">{text("td1_provincial_claim", { type: "number" })}</Field>
                  <Field label="Extra tax per pay ($)">{text("additional_tax_per_period", { type: "number" })}</Field>
                </div>
              )}

              {section === "contact" && (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
                  <Field label="Mobile" err={errs.phone}>{text("phone", { inputMode: "tel", maxLength: 15 })}</Field>
                  <Field label="Home phone" err={errs.home_phone}>{text("home_phone", { inputMode: "tel", maxLength: 30 })}</Field>
                  <Field label="Address" wide>{text("address_line1", { maxLength: 200 })}</Field>
                  <Field label="Address line 2">{text("address_line2", { maxLength: 200 })}</Field>
                  <Field label="City">{text("city", { maxLength: 100 })}</Field>
                  <Field label="Province of residence" hint="Where the employee lives, if different from the province of work.">
                    <select className="input" value={f.province_of_residence || ""} onChange={(ev) => set("province_of_residence", ev.target.value)}>
                      <option value="">— Same as work —</option>
                      {regionOptions(country).map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Postal code" err={errs.postal_code}>{text("postal_code", { maxLength: 7, upper: true, placeholder: "M5V 2T6" })}</Field>
                </div>
              )}

              {section === "emergency" && (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <SubHead>Emergency contact</SubHead>
                  <Field label="Contact name">{text("emergency_name", { maxLength: 200 })}</Field>
                  <Field label="Relationship">{text("emergency_relationship", { maxLength: 60 })}</Field>
                  <Field label="Phone" err={errs.emergency_phone}>{text("emergency_phone", { inputMode: "tel", maxLength: 15 })}</Field>
                  <SubHead>Data nominee</SubHead>
                  <p className="col-span-full -mt-1 text-[11px] text-gray-400">The person who may act on the employee's personal-information requests if they cannot (for example after death or incapacity). This is not a benefits or pension beneficiary.</p>
                  <Field label="Nominee name">{text("nominee_name", { maxLength: 200 })}</Field>
                  <Field label="Relationship">{text("nominee_relationship", { maxLength: 60 })}</Field>
                  <Field label="Phone" err={errs.nominee_phone}>{text("nominee_phone", { inputMode: "tel", maxLength: 15 })}</Field>
                </div>
              )}

              {section === "employment" && employee && (
                <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900">
                  Changes here to designation, department, grade, reports to, work location, division, cost centre or
                  probation end date are saved at once and recorded in <b>Career Changes</b> as
                  "Edited on the employee record" — no approval and no letter. For a promotion, transfer or
                  re-designation that needs approval or a letter, use{" "}
                  <Link to="/career-changes" className="underline font-medium" onClick={onClose}>People → Career Changes</Link>.
                </div>
              )}
              {section === "employment" && (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
                  <Field label="Date of joining" req err={errs.hire_date}>{text("hire_date", { type: "date" })}</Field>
                  <Field label="Employment type">
                    <select className="input" value={f.employment_type || "full_time"} onChange={(ev) => set("employment_type", ev.target.value)}>
                      {(opts?.employment_types || ["full_time", "part_time", "contract", "fixed_term", "seasonal"]).map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
                    </select>
                  </Field>
                  {["contract", "fixed_term"].includes(f.employment_type) && <>
                    <Field label="Contract term starts" hint="Blank = date of joining.">{text("contract_start_date", { type: "date" })}</Field>
                    <Field label="Contract term ends" err={errs.contract_end_date} hint="Renew / make permanent later from People → Fixed-term contracts.">{text("contract_end_date", { type: "date", min: f.contract_start_date || f.hire_date || undefined })}</Field>
                    <Field label="Notice days in the contract" err={errs.contract_notice_days}>{text("contract_notice_days", { type: "number", min: 0, max: 365 })}</Field>
                    <Field label="Contract reference">{text("contract_ref", { maxLength: 80 })}</Field>
                  </>}
                  <Field label="Province of employment" req err={errs.province_of_employment} hint="Where the employee reports for work — decides provincial tax, employment standards and stat holidays.">
                    <select className="input" value={f.province_of_employment || ""} onChange={(ev) => set("province_of_employment", ev.target.value)}>
                      <option value="">Choose…</option>
                      {regionOptions(country).map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Grade" hint="From Master Data → Grades.">
                    <select className="input" value={f.grade || ""} onChange={(ev) => set("grade", ev.target.value)}>
                      <option value="">—</option>
                      {f.grade && !(opts?.grades || []).some((g) => g.name === f.grade) && <option value={f.grade}>{f.grade}</option>}
                      {(opts?.grades || []).map((g) => <option key={g.name} value={g.name}>{g.name}</option>)}
                    </select>
                  </Field>
                  <Field label="Job title">
                    <input className="input" list="lx-desigs" value={f.job_title || ""} onChange={(ev) => set("job_title", ev.target.value)} placeholder="Choose or type…" />
                    <datalist id="lx-desigs">{(desigs || []).map((d) => <option key={d.id} value={d.name} />)}</datalist>
                  </Field>
                  <Field label="Department">
                    <input className="input" list="lx-depts" value={f.department || ""} onChange={(ev) => set("department", ev.target.value)} placeholder="Choose or type…" />
                    <datalist id="lx-depts">{(depts || []).map((d) => <option key={d.id} value={d.name} />)}</datalist>
                  </Field>
                  <Field label="Work location">
                    <input className="input" list="lx-locs" value={f.work_location || ""} onChange={(ev) => set("work_location", ev.target.value)} placeholder="Choose or type…" />
                    <datalist id="lx-locs">{(locs || []).map((d) => <option key={d.id} value={d.name} />)}</datalist>
                  </Field>
                  <Field label="Branch">
                    <select className="input" value={f.branch_id || ""} onChange={(ev) => {
                      const b = (branches || []).find((x: any) => String(x.id) === ev.target.value);
                      set("branch_id", ev.target.value || ""); set("branch_name", b?.name || null);
                    }}>
                      <option value="">— No branch —</option>
                      {(branches || []).map((b: any) => <option key={b.id} value={b.id}>{b.name}{b.is_hq ? " (HQ)" : ""}</option>)}
                    </select>
                  </Field>
                  {entities.length > 1 && (
                    <Field label="Legal entity" hint={employee ? "Changes only through a dated transfer on the profile (final pay and ROE under the old entity)." : "Which group company employs this person — its CRA payroll account goes on every T4 and remittance."}>
                      <select className="input" value={f.entity_id || entData?.default_id || ""} disabled={!!employee} onChange={(ev) => set("entity_id", ev.target.value)}>
                        {entities.map((x) => <option key={x.id} value={x.id}>{x.short_code} · {x.name}{x.is_default ? " (default)" : ""}</option>)}
                      </select>
                    </Field>
                  )}
                  <Field label="Division">{text("division", { maxLength: 120 })}</Field>
                  <Field label="Cost centre" hint="Salary cost is posted to this cost centre in the books.">{text("cost_center", { maxLength: 60 })}</Field>
                  <Field label="Probation ends" err={errs.probation_end_date} hint="Blank = no probation, or set from People → Probation.">{text("probation_end_date", { type: "date", min: f.hire_date || undefined })}</Field>
                  {(f.employment_type === "part_time" || f.fte_percent) && (
                    <Field label="Working-time share (%)" err={errs.fte_percent} hint="Part-time share of a full week, e.g. 50.">{text("fte_percent", { type: "number", min: 1, max: 100, step: "0.5" })}</Field>
                  )}
                  {employee && (f.status === "terminated" || f.termination_date) && (
                    <Field label="Last working day" err={errs.termination_date} hint="Normally set by the exit case. Edit here only to correct a date.">{text("termination_date", { type: "date", min: f.hire_date || undefined })}</Field>
                  )}
                  <label className="flex items-center gap-2 text-xs text-gray-600 sm:pt-5">
                    <input type="checkbox" checked={f.overtime_eligible !== false} onChange={(ev) => set("overtime_eligible", ev.target.checked)} />
                    Eligible for overtime pay
                  </label>
                  <Field label="Reports to" wide hint={e?.manager_name && !f.manager_id ? `Typed earlier: "${e.manager_name}". Pick the person so approvals reach them.` : "Leave and other approvals go to this person."}>
                    <select className="input" value={f.manager_id || ""} onChange={(ev) => set("manager_id", ev.target.value)}>
                      <option value="">— No manager —</option>
                      {managers.map((m) => <option key={m.id} value={m.id}>
                        {m.name}{m.employee_number ? ` · ${m.employee_number}` : ""}{m.job_title ? ` · ${m.job_title}` : ""}</option>)}
                    </select>
                  </Field>
                </div>
              )}

              {section === "compensation" && (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
                  <Field label="Pay type">
                    <select className="input capitalize" value={f.pay_type || "salary"} onChange={(ev) => set("pay_type", ev.target.value)}>
                      {PAY_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </Field>
                  {f.pay_type === "hourly"
                    ? <Field label={`Hourly rate (${sym})`} err={errs.hourly_rate}>{text("hourly_rate", { type: "number", step: "0.25", min: 0 })}</Field>
                    : <Field label={`Yearly pay (${sym})`} err={errs.annual_salary} >{text("annual_salary", { type: "number", min: 0 })}</Field>}
                  <Field label="Pay frequency">
                    <select className="input" value={f.pay_frequency || ""} onChange={(ev) => set("pay_frequency", ev.target.value)}>
                      {PAY_FREQUENCIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                    </select>
                  </Field>
                  <Field label="Hours per week" err={errs.standard_hours_per_week}>{text("standard_hours_per_week", { type: "number", step: "0.5", min: 0, max: 168 })}</Field>
                </div>
              )}

              {section === "banking" && (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                  <Field label="Institution number" err={errs.bank_institution} hint="3 digits.">{text("bank_institution", { maxLength: 3, inputMode: "numeric" })}</Field>
                  <Field label="Transit number" err={errs.bank_transit} hint="5 digits.">{text("bank_transit", { maxLength: 5, inputMode: "numeric" })}</Field>
                  <Field label="Account number" err={errs.bank_account} hint="7 to 12 digits. Stored encrypted, shown masked.">{secret("bank_account", e?.bank_account_masked, { maxLength: 20, inputMode: "numeric" })}</Field>
                </div>
              )}

              {section === "custom" && (employee
                ? <CustomFieldsSection entity="employee" id={employee.id} title="Custom fields" />
                : <CustomFieldsSection entity="employee" values={cfValues} onChange={setCfValues} title="Custom fields (saved with the new employee)" />)}
              {section === "documents" && employee && <DocumentsPanel employee={employee} opts={opts} />}
              {section === "login" && employee && <LoginPanel employee={employee} />}
              {section === "history" && employee && <EmployeeLifecyclePanel employeeId={employee.id} />}
            </div>
          </div>
        </div>

        <div className="border-t border-surface-border px-4 sm:px-5 py-3 flex items-center justify-end gap-2 bg-surface">
          <button onClick={onClose} className="btn-secondary text-xs">Cancel</button>
          <button onClick={submit} disabled={save.isPending} className="btn-primary text-xs disabled:opacity-60">
            {save.isPending ? "Saving…" : employee ? "Save changes" : "Add employee"}</button>
        </div>
      </div>
    </div>
  );
}

/* ── Documents on the profile (files live in the database) ── */
function DocumentsPanel({ employee, opts }: { employee: Employee; opts?: FormOptions }) {
  const qc = useQueryClient();
  const key = ["employees", employee.id, "documents"];
  const { data: docs, isLoading } = useQuery({ queryKey: key, queryFn: () => employeeFormApi.docs(employee.id) });
  const types = opts?.doc_types?.CA || [{ value: "other", label: "Other" }];
  const [docType, setDocType] = useState(types[0].value);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: key });
  const label = (v: string) => Object.values(opts?.doc_types || {}).flat().find((d) => d.value === v)?.label || v.replace(/_/g, " ");

  const pick = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    try { await employeeFormApi.uploadDoc(employee.id, file, docType); toast.success("Uploaded"); refresh(); }
    catch (e) { toast.error(errText(e, "Could not upload the file")); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  const open = async (d: StoredDoc) => {
    try { openBlob(await employeeFormApi.downloadDoc(employee.id, d.id)); } catch (e) { toast.error(errText(e, "Could not open the file")); }
  };
  const verify = async (d: StoredDoc) => { await employeeFormApi.verifyDoc(employee.id, d.id, !d.verified); refresh(); };
  const remove = async (d: StoredDoc) => {
    if (!window.confirm(`Remove "${d.file_name}"?`)) return;
    try { await employeeFormApi.removeDoc(employee.id, d.id); refresh(); } catch (e) { toast.error(errText(e, "Could not remove the file")); }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-col sm:flex-row sm:items-end gap-2">
        <div className="sm:w-72"><label className={LABEL}>Document type</label>
          <select className="input" value={docType} onChange={(e) => setDocType(e.target.value)}>
            {types.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}</select></div>
        <input ref={input} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf" onChange={(e) => pick(e.target.files?.[0])} />
        <button className="btn-primary flex items-center justify-center gap-1.5 text-xs disabled:opacity-60" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />} Upload file</button>
        <span className="text-[11px] text-gray-400 sm:ml-2">{opts?.doc_rules.allowed || "JPG, PNG or PDF"} · up to {opts?.doc_rules.max_mb || 5} MB</span>
      </div>
      <div className="overflow-x-auto border border-surface-border rounded-lg">
        <table className="w-full text-sm min-w-[640px]">
          <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
            <tr><th className="px-3 py-2">Type</th><th className="px-3 py-2">File</th><th className="px-3 py-2">Size</th>
              <th className="px-3 py-2">Uploaded by</th><th className="px-3 py-2">On</th><th className="px-3 py-2">Checked</th><th className="px-3 py-2 text-right">Actions</th></tr>
          </thead>
          <tbody>
            {isLoading ? <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">Loading…</td></tr>
              : !(docs || []).length ? <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400">No documents yet.</td></tr>
              : (docs || []).map((d) => (
                <tr key={d.id} className="border-t border-surface-border">
                  <td className="px-3 py-2 capitalize">{label(d.doc_type)}</td>
                  <td className="px-3 py-2 text-gray-700">{d.file_name}</td>
                  <td className="px-3 py-2 tabular-nums">{Math.max(1, Math.round(d.size_bytes / 1024))} KB</td>
                  <td className="px-3 py-2">{d.uploaded_by_name || "—"}</td>
                  <td className="px-3 py-2">{d.uploaded_at ? new Date(d.uploaded_at).toLocaleDateString() : "—"}</td>
                  <td className="px-3 py-2">{d.verified ? <span className="badge-green">Checked</span> : <span className="badge-amber">Not yet</span>}</td>
                  <td className="px-3 py-2"><div className="flex justify-end gap-1">
                    <button className="icon-btn text-gray-400 hover:text-primary-600" title="Open" onClick={() => open(d)}><Eye className="w-4 h-4" /></button>
                    <button className="icon-btn text-gray-400 hover:text-green-600" title={d.verified ? "Mark as not checked" : "Mark as checked"} onClick={() => verify(d)}><CheckCircle2 className="w-4 h-4" /></button>
                    <button className="icon-btn text-gray-400 hover:text-red-500" title="Remove" onClick={() => remove(d)}><Trash2 className="w-4 h-4" /></button>
                  </div></td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── The employee's login ── */
function LoginPanel({ employee }: { employee: Employee }) {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user);
  const canManage = !!me && [...ADMIN_ROLES, "hr_admin"].includes(me.role);
  const key = ["logins", "employee", employee.id];
  const { data, isLoading } = useQuery({ queryKey: key, queryFn: () => usersApi.list({ employee_id: employee.id }) });
  const login = data?.items?.[0];
  const [email, setEmail] = useState((employee as any).work_email || employee.email || "");
  const [role, setRole] = useState("employee");
  const [link, setLink] = useState<{ title: string; res: LinkResult } | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: key }); qc.invalidateQueries({ queryKey: ["logins"] }); };
  const fail = (m: string) => (e: any) => toast.error(errText(e, m));
  const shown = (title: string) => (res: LinkResult) => { refresh(); if (res.email_sent) toast.success(`E-mail sent to ${res.email}`); else setLink({ title, res }); };

  const create = useMutation({ mutationFn: () => usersApi.create({ employee_id: employee.id, email: email.trim(), role, send_invite: true }),
    onSuccess: (r) => { refresh(); if (r.email_sent) toast.success(`Login created. Invite sent to ${r.email}`);
      else if (r.link || r.invited) setLink({ title: "Invite not sent", res: { email: r.email, email_sent: false, link: r.link } }); },
    onError: fail("Could not create the login") });
  const resend = useMutation({ mutationFn: () => usersApi.resend(login!.id), onSuccess: shown("Invite link"), onError: fail("Could not send the invite") });
  const reset = useMutation({ mutationFn: () => usersApi.reset(login!.id), onSuccess: shown("Password reset link"), onError: fail("Could not send the reset link") });
  const disable = useMutation({ mutationFn: (reason: string) => usersApi.disable(login!.id, reason), onSuccess: () => { toast.success("Login disabled"); refresh(); }, onError: fail("Could not disable the login") });
  const enable = useMutation({ mutationFn: () => usersApi.enable(login!.id), onSuccess: () => { toast.success("Login enabled"); refresh(); }, onError: fail("Could not enable the login") });

  if (isLoading) return <div className="grid place-items-center py-10"><Loader2 className="w-5 h-5 animate-spin text-primary-500" /></div>;
  const STATUS: Record<string, [string, string]> = { active: ["badge-green", "Active"], invited: ["badge-amber", "Invite sent — password not set yet"], disabled: ["badge-red", "Disabled"] };

  return (
    <div className="max-w-3xl">
      {!login ? (
        <div className="space-y-3">
          <p className="text-sm text-gray-700">This employee cannot sign in yet.</p>
          {canManage ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
              <div className="sm:col-span-2"><label className={LABEL}>E-mail to sign in with</label>
                <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></div>
              <div><label className={LABEL}>Role</label>
                <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
                  {(data?.roles || [{ key: "employee", label: "Employee" }]).map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select></div>
              <button className="btn-primary text-xs sm:col-span-3 sm:w-max flex items-center gap-1.5 disabled:opacity-60" disabled={!email.trim() || create.isPending}
                onClick={() => create.mutate()}><KeyRound className="w-3.5 h-3.5" /> Create login and send invite</button>
            </div>
          ) : <p className="text-xs text-gray-500">Ask HR or an admin to create the login.</p>}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
            <Row k="Signs in with" v={login.email} />
            <Row k="Role" v={login.role_label} />
            <Row k="Status" v={<span className={STATUS[login.status]?.[0]}>{STATUS[login.status]?.[1] || login.status}</span>} />
            <Row k="Last sign-in" v={login.last_login ? new Date(login.last_login).toLocaleString() : "Never"} />
            {login.disabled_reason && <Row k="Reason" v={login.disabled_reason} />}
          </div>
          {canManage && (
            <div className="flex flex-wrap gap-2 pt-1">
              {login.status === "invited" && <button className="btn-secondary text-xs flex items-center gap-1.5" onClick={() => resend.mutate()}><Send className="w-3.5 h-3.5" /> Resend invite</button>}
              {login.status === "active" && <button className="btn-secondary text-xs flex items-center gap-1.5" onClick={() => reset.mutate()}><RotateCcw className="w-3.5 h-3.5" /> Reset password</button>}
              {login.status !== "disabled" && login.id !== me?.id && <button className="btn-secondary text-xs flex items-center gap-1.5 !text-red-600" onClick={() => {
                const reason = window.prompt("Why is this login being disabled? (kept in the audit log)", "");
                if (reason !== null) disable.mutate(reason);
              }}><Ban className="w-3.5 h-3.5" /> Disable login</button>}
              {login.status === "disabled" && <button className="btn-secondary text-xs flex items-center gap-1.5" onClick={() => enable.mutate()}><CheckCircle2 className="w-3.5 h-3.5" /> Enable login</button>}
            </div>
          )}
          <p className="text-xs text-gray-500">To change the role, use Setup → Users &amp; Logins.</p>
        </div>
      )}
      {link && <LinkBox title={link.title} res={link.res} onClose={() => setLink(null)} />}
    </div>
  );
}

function Row({ k, v }: { k: string; v: ReactNode }) {
  return <div className="flex justify-between gap-4 border-b border-surface-border/60 py-1.5"><span className="text-gray-400">{k}</span><span className="text-gray-800 font-medium text-right">{v}</span></div>;
}
