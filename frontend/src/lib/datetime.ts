// Lexora HRM — dates and times in the browser (rule 2026-10-25: store UTC, show local, "today" in the org's zone).
//
// Two kinds of value:
//   • INSTANTS (created_at, punched_at, approved_at, sent_at …) travel as ISO-8601 UTC WITH an offset
//     ("2026-10-25T04:00:00+00:00" / "…Z"). Show them with formatDateTime / formatTime (viewer's zone);
//     send them with nowISO() / fromDateTimeLocal(). <input type="datetime-local"> ↔ toDateTimeLocal().
//   • CALENDAR DATES (work_date, period end, leave day, hire date, DOB, pay date) are plain "YYYY-MM-DD"
//     strings. Never turn one into a Date and back with toISOString(): west of UTC (Toronto, −04:00/−05:00)
//     a late-evening local time is already the NEXT day in UTC. Use the string helpers here (addDaysISO, monthEndISO …), which do
//     calendar arithmetic in UTC on purpose so no zone can shift them.
//   • "Today" / "this month" are the ORG's calendar (company setting locale.timezone, default
//     America/Toronto) — todayISO(), thisMonthISO() — not UTC's date and not a guess from the server.
//
// No imports: scripts/check_datetime.mjs runs this file directly under node.

export const DEFAULT_TZ = "America/Toronto";
let orgTz = DEFAULT_TZ;

export function validTimeZone(tz: string | null | undefined): boolean {
  if (!tz) return false;
  try { new Intl.DateTimeFormat("en-US", { timeZone: tz }); return true; } catch { return false; }
}
/** Called once the org's locale settings are known (DashboardLayout). Bad / blank names keep Toronto (Eastern) time. */
export function setOrgTimeZone(tz: string | null | undefined): void { orgTz = validTimeZone(tz) ? String(tz) : DEFAULT_TZ; }
export function orgTimeZone(): string { return orgTz; }
/** The viewer's own zone (browser); the org's zone when the browser will not say. */
export function viewerTimeZone(): string {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || orgTz; } catch { return orgTz; }
}

const p2 = (n: number) => String(n).padStart(2, "0");
const p4 = (n: number) => String(n).padStart(4, "0");
export const ymd = (y: number, m: number, d: number) => `${p4(y)}-${p2(m)}-${p2(d)}`;

/** "YYYY-MM-DD…" → parts (month 1–12), or null. Accepts a longer ISO string and reads its date part. */
export function parseISODate(iso: string | null | undefined): { y: number; m: number; d: number } | null {
  const mt = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  return mt ? { y: Number(mt[1]), m: Number(mt[2]), d: Number(mt[3]) } : null;
}
const fromUTC = (t: Date) => ymd(t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate());
const utcOf = (iso: string) => { const p = parseISODate(iso); return p ? new Date(Date.UTC(p.y, p.m - 1, p.d)) : null; };

const asDate = (v: Date | string | number) => (v instanceof Date ? v : new Date(v));

/** The calendar date an INSTANT falls on in ``tz`` (default: the org's zone). */
export function dateInZone(instant: Date | string | number, tz: string = orgTz): string {
  const t = asDate(instant);
  if (isNaN(t.getTime())) return "";
  try {
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(t);
    const g = (k: string) => Number(parts.find((x) => x.type === k)?.value);
    return ymd(g("year"), g("month"), g("day"));
  } catch {
    return toLocalDateISO(t);
  }
}

/** Today's date "YYYY-MM-DD" on the org's calendar. ``now`` is for tests. */
export function todayISO(tz: string = orgTz, now: Date | number = Date.now()): string { return dateInZone(now, tz); }
/** This month "YYYY-MM" on the org's calendar. */
export function thisMonthISO(tz: string = orgTz, now: Date | number = Date.now()): string { return todayISO(tz, now).slice(0, 7); }
/** The browser-local calendar date of a Date built from local parts (calendar grids) — no UTC shift. */
export function toLocalDateISO(d: Date): string { return ymd(d.getFullYear(), d.getMonth() + 1, d.getDate()); }

/** Calendar arithmetic on a plain date: addDaysISO("2026-10-31", 1) = "2026-11-01". */
export function addDaysISO(iso: string, n: number): string {
  const t = utcOf(iso); if (!t) return iso;
  t.setUTCDate(t.getUTCDate() + n); return fromUTC(t);
}
/** Days in a month (m = 1–12). */
export const daysInMonth = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate();
/** Last day of the month of "YYYY-MM" or "YYYY-MM-DD": monthEndISO("2026-10") = "2026-10-31", Feb 2028 = 29. */
export function monthEndISO(isoOrMonth: string): string {
  const mt = /^(\d{4})-(\d{2})/.exec(String(isoOrMonth || "")); if (!mt) return isoOrMonth;
  const y = Number(mt[1]), m = Number(mt[2]); return ymd(y, m, daysInMonth(y, m));
}
/** First day of the month of "YYYY-MM" or "YYYY-MM-DD". */
export function monthStartISO(isoOrMonth: string): string { return `${String(isoOrMonth).slice(0, 7)}-01`; }
/** Same day n months later, clamped to the month end (31 Jan + 1 month = 28/29 Feb, not 3 Mar). */
export function addMonthsISO(iso: string, n: number): string {
  const p = parseISODate(iso); if (!p) return iso;
  const idx = p.y * 12 + (p.m - 1) + n; const y = Math.floor(idx / 12), m = (idx % 12) + 1;
  return ymd(y, m, Math.min(p.d, daysInMonth(y, m)));
}
/** "YYYY-MM" n months later. */
export function addMonthsYM(ym: string, n: number): string { return addMonthsISO(`${String(ym).slice(0, 7)}-01`, n).slice(0, 7); }
/** Weekday of a plain date, Monday = 0 … Sunday = 6 (matches the backend's Python weekday()). */
export function weekdayISO(iso: string): number { const t = utcOf(iso); return t ? (t.getUTCDay() + 6) % 7 : 0; }
/** Monday of the week containing ``iso``. */
export function mondayOfISO(iso: string): string { return addDaysISO(iso, -weekdayISO(iso)); }
/** Whole days from ``a`` to ``b`` (b − a). */
export function daysBetweenISO(a: string, b: string): number {
  const x = utcOf(a), y = utcOf(b); return x && y ? Math.round((y.getTime() - x.getTime()) / 864e5) : 0;
}

/** The current instant as ISO UTC (for sending). */
export function nowISO(): string { return new Date().toISOString(); }

// ── display of INSTANTS (viewer's zone by default) ──────────────────────────────
export interface FmtOpts { tz?: string; locale?: string; zone?: boolean; seconds?: boolean }
const LOCALE = "en-CA";
/** "Oct 25, 2026, 10:30 a.m. EDT" — an instant shown in the viewer's zone (``zone: false`` hides the zone). */
export function formatDateTime(iso: string | number | Date | null | undefined, o: FmtOpts = {}): string {
  if (iso === null || iso === undefined || iso === "") return "—";
  const t = asDate(iso as any); if (isNaN(t.getTime())) return String(iso);
  try {
    return t.toLocaleString(o.locale || LOCALE, { timeZone: o.tz || viewerTimeZone(), day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", ...(o.seconds ? { second: "2-digit" } : {}), ...(o.zone === false ? {} : { timeZoneName: "short" }) });
  } catch { return t.toISOString(); }
}
/** "10:30 a.m. EDT" — the time of an instant in the viewer's zone. */
export function formatTime(iso: string | number | Date | null | undefined, o: FmtOpts = {}): string {
  if (iso === null || iso === undefined || iso === "") return "—";
  const t = asDate(iso as any); if (isNaN(t.getTime())) return String(iso);
  try {
    return t.toLocaleTimeString(o.locale || LOCALE, { timeZone: o.tz || viewerTimeZone(), hour: "2-digit", minute: "2-digit",
      ...(o.seconds ? { second: "2-digit" } : {}), ...(o.zone === false ? {} : { timeZoneName: "short" }) });
  } catch { return t.toISOString().slice(11, 16); }
}
/** The calendar date ("YYYY-MM-DD") an INSTANT falls on in the viewer's zone — for "Sent on" columns.
 *  A plain date passes through untouched; blank → "". (Never `created_at.slice(0, 10)`: that is UTC's date.) */
export function localDateOf(iso: string | null | undefined, tz?: string): string {
  if (!iso) return "";
  const s = String(iso);
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  return dateInZone(s, tz || viewerTimeZone()) || s.slice(0, 10);
}
/** Short zone label of ``tz`` at that moment ("EDT", "PST"). */
export function zoneLabel(tz: string = viewerTimeZone(), at: Date | number = Date.now()): string {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: tz, timeZoneName: "short" }).formatToParts(asDate(at)).find((x) => x.type === "timeZoneName")?.value || tz;
  } catch { return tz; }
}
/** A CALENDAR date "Oct 25, 2026" — never converted between zones. */
export function formatDate(iso: string | null | undefined, locale: string = LOCALE): string {
  const p = parseISODate(iso); if (!p) return iso ? String(iso) : "—";
  try { return new Date(Date.UTC(p.y, p.m - 1, p.d)).toLocaleDateString(locale, { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" }); }
  catch { return ymd(p.y, p.m, p.d); }
}

// ── <input type="datetime-local"> (the browser reads/writes it in the viewer's zone) ──
/** UTC instant → "YYYY-MM-DDTHH:mm" in the viewer's zone, for the input's value / min. */
export function toDateTimeLocal(iso: string | number | Date | null | undefined): string {
  if (iso === null || iso === undefined || iso === "") return "";
  const t = asDate(iso as any); if (isNaN(t.getTime())) return "";
  return `${toLocalDateISO(t)}T${p2(t.getHours())}:${p2(t.getMinutes())}`;
}
/** The input's local "YYYY-MM-DDTHH:mm" → ISO UTC instant for the API ("" → null). */
export function fromDateTimeLocal(v: string | null | undefined): string | null {
  if (!v) return null;
  const t = new Date(v); return isNaN(t.getTime()) ? null : t.toISOString();
}

// ── zone pickers (company setting, device clock) ─────────────────────────────────
export const TIME_ZONES: [string, string][] = [
  ["America/St_Johns", "Newfoundland (NT)"], ["America/Halifax", "Atlantic (AT) — NS, NB, PE"], ["America/Toronto", "Eastern (ET) — ON, QC"],
  ["America/Winnipeg", "Central (CT) — MB"], ["America/Regina", "Saskatchewan (CST, no DST)"], ["America/Edmonton", "Mountain (MT) — AB, NT"],
  ["America/Vancouver", "Pacific (PT) — BC"], ["America/Whitehorse", "Yukon (MST, no DST)"], ["America/Iqaluit", "Nunavut — Eastern"], ["UTC", "UTC"]];
/** The list, with the current value first when it is not one of ours. */
export const zoneOptions = (cur?: string): [string, string][] => (cur && !TIME_ZONES.some(([z]) => z === cur) ? [[cur, `${cur} (current)`], ...TIME_ZONES] : TIME_ZONES);
