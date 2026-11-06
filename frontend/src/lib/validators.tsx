import { todayISO } from "./datetime";   // 2026-10-25: UTC store, org-zone today
/**
 * Canadian HR / payroll field checks (SIN, postal code, phone, CRA / Revenu Québec numbers, bank transit).
 *
 * Every check returns null when the value is fine (or blank — "required" is
 * a separate check) and a short plain-English message otherwise, so forms can
 * show it under the field and block Save:
 *
 *   const errs = collect({ sin: v.sin(f.sin), postal: v.postal(f.postal_code), ... });
 *   if (Object.keys(errs).length) return toast.error(firstError(errs));
 *
 * These are FORMAT checks only (SIN also passes the Luhn check digit). They never
 * prove a number is issued — that is CRA / Service Canada's job.
 */
const blank = (s: unknown) => s === undefined || s === null || String(s).trim() === "";
const up = (s: unknown) => String(s ?? "").trim().toUpperCase();
const digits = (s: unknown) => String(s ?? "").replace(/[\s-]/g, "");

export const RE = {
  sin: /^[0-9]{9}$/,
  postal: /^[ABCEGHJ-NPRSTVXY][0-9][ABCEGHJ-NPRSTV-Z] ?[0-9][ABCEGHJ-NPRSTV-Z][0-9]$/,
  phone: /^[2-9][0-9]{2}[2-9][0-9]{6}$/,
  email: /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/,
  bn: /^[0-9]{9}$/,
  rp: /^[0-9]{9}RP[0-9]{4}$/,
  rq: /^[0-9]{10}(TQ[0-9]{4})?$/,
  institution: /^[0-9]{3}$/,
  transit: /^[0-9]{5}$/,
  bankAccount: /^[0-9]{7,12}$/,
};

/** Luhn check digit (SIN). */
export function luhnOk(num: string): boolean {
  let sum = 0;
  for (let i = 0; i < num.length; i++) {
    let d = Number(num[num.length - 1 - i]);
    if (i % 2 === 1) { d *= 2; if (d > 9) d -= 9; }
    sum += d;
  }
  return sum % 10 === 0;
}

/** Strip spaces / dashes / brackets / a leading +1 or 1 from a North American phone number. */
export const normMobile = (s: unknown) => String(s ?? "").replace(/[\s\-().]/g, "").replace(/^(\+1|1)(?=[2-9][0-9]{9}$)/, "");

export const v = {
  required: (s: unknown, label = "This field") => (blank(s) ? `${label} is required` : null),
  sin: (s: unknown) => (blank(s) || (RE.sin.test(digits(s)) && !digits(s).startsWith("0") && luhnOk(digits(s))) ? null : "SIN is 9 digits and must pass the check digit (e.g. 046 454 286)"),
  postal: (s: unknown) => (blank(s) || RE.postal.test(up(s)) ? null : "Postal code looks like A1A 1A1"),
  mobile: (s: unknown) => (blank(s) || RE.phone.test(normMobile(s)) ? null : "Phone is 10 digits, e.g. 416 555 0123 (+1 optional)"),
  email: (s: unknown) => (blank(s) || RE.email.test(String(s).trim()) ? null : "Enter a valid e-mail address"),
  bn: (s: unknown) => (blank(s) || RE.bn.test(digits(s)) ? null : "Business Number is 9 digits"),
  rp: (s: unknown) => (blank(s) || RE.rp.test(digits(s).toUpperCase()) ? null : "Payroll account looks like 123456789RP0001"),
  rq: (s: unknown) => (blank(s) || RE.rq.test(digits(s).toUpperCase()) ? null : "Revenu Québec number is 10 digits (optionally TQ + 4 digits)"),
  institution: (s: unknown) => (blank(s) || RE.institution.test(digits(s)) ? null : "Institution number is 3 digits"),
  transit: (s: unknown) => (blank(s) || RE.transit.test(digits(s)) ? null : "Transit number is 5 digits"),
  bankAccount: (s: unknown) => (blank(s) || RE.bankAccount.test(digits(s)) ? null : "Bank account number is 7 to 12 digits"),
  /** amount / count must be >= min (default 0) and <= max when given */
  range: (s: unknown, min = 0, max?: number, label = "Value") => {
    if (blank(s)) return null;
    const n = Number(s);
    if (!Number.isFinite(n)) return `${label} must be a number`;
    if (n < min) return `${label} cannot be less than ${min}`;
    if (max !== undefined && n > max) return `${label} cannot be more than ${max}`;
    return null;
  },
  percent: (s: unknown, label = "Percentage") => v.range(s, 0, 100, label),
  /** `to` must not be before `from` (ISO yyyy-mm-dd strings) */
  dateOrder: (from: unknown, to: unknown, msg = "End date cannot be before the start date") =>
    (blank(from) || blank(to) || String(to) >= String(from) ? null : msg),
  notFuture: (s: unknown, label = "Date") => (blank(s) || String(s).slice(0, 10) <= todayISO() ? null : `${label} cannot be in the future`),
  maxLen: (s: unknown, n: number, label = "Text") => (blank(s) || String(s).length <= n ? null : `${label} can be at most ${n} characters`),
};

/** Drop the nulls: { field: message } for the failing checks only. */
export function collect(checks: Record<string, string | null | undefined>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, m] of Object.entries(checks)) if (m) out[k] = m;
  return out;
}
export const firstError = (errs: Record<string, string>) => Object.values(errs)[0] || "";

/** Small red message under a field. */
export function FieldError({ msg }: { msg?: string | null }) {
  return msg ? <span className="block text-[11px] text-red-600 mt-0.5" role="alert">{msg}</span> : null;
}

/** Red asterisk for required labels. */
export const Req = () => <span className="text-red-500" aria-hidden="true"> *</span>;
