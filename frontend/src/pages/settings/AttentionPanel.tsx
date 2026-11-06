// Stream U4 — "Needs your attention" (staff roles) and "My week" (employee)
import { usableLeave, MASK } from "../../api/ess";
import { useSalaryVisible } from "../../components/SalaryToggle";
// for the top of the dashboard, plus the compact setup banner for org admins.
// Every row is a real GET; a 403 hides the row; zero-count rows collapse into
// one grey line. No statutory date is typed here — dates come from the API.
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  CheckSquare, UserCheck, UserPlus, LogOut, FileWarning, TrendingUp, Wallet, Landmark, Cake,
  ArrowRight, Settings, CalendarDays, Receipt, Inbox, LucideIcon, Sparkles,
} from "lucide-react";
import { Spinner } from "../../components/ui";
import { useAuthStore, HR_WRITE, PAYROLL_ACCESS } from "../../store/authStore";
import { employeeApi, onboardingApi, payrollApi, leaveApi, mastersApi, money, type Employee, type OnboardingSession, type PayRun, type MasterRow, type PayslipRow } from "../../api/endpoints";
import { approvalsApi, type ApprovalList } from "../../api/platform";
import { probationApi, revisionsApi } from "../../api/lifecycle";
import { exitApi, type ExitCase } from "../../api/exit";
import { useSetupReadiness } from "./useSetupReadiness";
import { can } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/* ── date helpers (local calendar, Monday-first week) ───────────────────── */
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const today = () => { const n = new Date(); return new Date(n.getFullYear(), n.getMonth(), n.getDate()); };
function weekRange(): [string, string] {
  const t = today(); const dow = (t.getDay() + 6) % 7;
  const mon = new Date(t); mon.setDate(t.getDate() - dow);
  const sun = new Date(mon); sun.setDate(mon.getDate() + 6);
  return [iso(mon), iso(sun)];
}
const plusDays = (n: number) => { const d = today(); d.setDate(d.getDate() + n); return iso(d); };
const within = (date: string | null | undefined, from: string, to: string) => !!date && date.slice(0, 10) >= from && date.slice(0, 10) <= to;
/** Does the month-day of `isoDate` fall inside [from, to] (this week) in any year? */
function annualInWeek(isoDate: string | null | undefined, from: string, to: string): boolean {
  if (!isoDate || isoDate.length < 10) return false;
  const md = isoDate.slice(5, 10);
  const y1 = from.slice(0, 4), y2 = to.slice(0, 4);
  const cand = y1 === y2 ? [`${y1}-${md}`] : [`${y1}-${md}`, `${y2}-${md}`];
  return cand.some((c) => c >= from && c <= to);
}
const niceDate = (s?: string | null) => s ? new Date(s.length <= 10 ? s + "T00:00:00" : s).toLocaleDateString("en-CA", { day: "numeric", month: "short" }) : "";
const ageDays = (isoTs?: string | null) => isoTs ? Math.floor((Date.now() - new Date(isoTs).getTime()) / 86400000) : 0;

function useSafe<T>(key: string, fn: () => Promise<T>, enabled: boolean): UseQueryResult<T> {
  return useQuery<T>({ queryKey: ["attention", key], queryFn: fn, enabled, retry: false, staleTime: 60_000 });
}
const is403 = (q: UseQueryResult<any>) => (q.error as any)?.response?.status === 403;

interface Row { key: string; icon: LucideIcon; count: number | null; text: string; sub?: string; to: string; tone: "red" | "amber" | "blue" | "gray" | "green"; }

const TONE: Record<Row["tone"], string> = {
  red: "bg-red-50 text-red-600", amber: "bg-amber-50 text-amber-600", blue: "bg-blue-50 text-blue-600",
  gray: "bg-gray-100 text-gray-500", green: "bg-green-50 text-green-600",
};

function RowLine({ r }: { r: Row }) {
  return (
    <Link to={r.to} className="group flex items-center gap-3 px-4 py-2 border-b border-surface-border/60 last:border-b-0 hover:bg-surface/70 transition-colors">
      <span className={`h-7 min-w-[28px] px-1.5 rounded-lg grid place-items-center text-xs font-bold tabular-nums ${TONE[r.tone]}`}>
        {r.count === null ? <r.icon className="h-3.5 w-3.5" /> : r.count}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-gray-800 truncate">{r.text}</p>
        {r.sub && <p className="text-[11px] text-gray-400 truncate" title={r.sub}>{r.sub}</p>}
      </div>
      <ArrowRight className="h-3.5 w-3.5 text-gray-300 group-hover:text-primary-500 shrink-0" />
    </Link>
  );
}

function PanelShell({ title, icon: Icon, right, children }: { title: string; icon: LucideIcon; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="card !p-0">
      <div className="flex items-center justify-between px-4 pt-3.5 pb-2">
        <div>
          <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider">Today</p>
          <h3 className="font-display font-semibold text-gray-800 text-[15px] flex items-center gap-1.5"><Icon className="h-4 w-4 text-primary-500" />{title}</h3>
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

/* ── Staff roles: Needs your attention ──────────────────────────────────── */
export function AttentionPanel() {
  const user = useAuthStore((s) => s.user);
  const role = user?.role || "";
  const hr = can(user, "people.view", HR_WRITE);
  const payroll = can(user, "payroll.view", PAYROLL_ACCESS);
  const manager = role === "manager";
  const [wFrom, wTo] = weekRange();
  const ym = wFrom.slice(0, 7) === wTo.slice(0, 7) ? wFrom.slice(0, 7) : iso(today()).slice(0, 7);
  const monthLabel = new Date(today().getFullYear(), today().getMonth(), 1).toLocaleDateString("en-CA", { month: "long" });

  const approvals = useSafe<{ waiting_for_me: number }>("approvals", approvalsApi.summary, true);
  const probation = useSafe<any>("probation", () => probationApi.due({ within: 30 }), hr || manager);
  const emps = useQuery<Employee[]>({ queryKey: ["employees"], queryFn: employeeApi.list, enabled: hr || manager, retry: false });
  const sessions = useQuery<OnboardingSession[]>({ queryKey: ["onboarding"], queryFn: () => onboardingApi.list(), enabled: hr, retry: false });
  const exits = useSafe<{ cases: ExitCase[] }>("exits", () => exitApi.list(manager ? { scope: "team", status: "open" } : { status: "open" }), hr || payroll || manager);
  const revisions = useSafe<any[]>("revisions", () => revisionsApi.list({ status: "submitted" }), payroll || can(user, "compensation.view", ["hr_manager"]));
  const runs = useSafe<PayRun[]>("runs", payrollApi.runs, payroll);

  const qs = [approvals, probation, emps, sessions, exits, revisions, runs];
  const loading = qs.some((q) => q.isLoading && q.fetchStatus !== "idle");

  const rows: Row[] = [];
  const quiet: string[] = [];
  const add = (r: Row, quietLabel?: string) => { if (r.count === 0 && quietLabel) quiet.push(quietLabel); else rows.push(r); };

  if (approvals.data) {
    const n = approvals.data.waiting_for_me;
    add({ key: "appr", icon: CheckSquare, count: n, tone: n ? "red" : "gray", to: "/approvals",
      text: n === 1 ? "1 approval is waiting for you" : `${n} approvals are waiting for you`, sub: "Leave, timesheets, hikes and more — decide from the inbox" }, "approvals");
  }
  if (probation.data) {
    const list: any[] = probation.data.rows || [];
    const overdue = list.filter((r) => r.bucket === "overdue").length;
    add({ key: "prob", icon: UserCheck, count: list.length, tone: overdue ? "red" : "amber", to: "/probation",
      text: `${list.length} probation review${list.length === 1 ? "" : "s"} due in the next 30 days`,
      sub: overdue ? `${overdue} already past the end date` : list.slice(0, 3).map((r) => `${r.employee} · ${niceDate(r.probation_end_date)}`).join(" · ") }, "probation reviews");
  }
  if (emps.data) {
    const joining = emps.data.filter((e) => e.status !== "terminated" && within(e.hire_date, wFrom, wTo));
    const stuck = (sessions.data || []).filter((s) => (s.status === "invited" || s.status === "submitted") && ageDays(s.invited_at || s.created_at) > 7);
    const n = joining.length + stuck.length;
    add({ key: "join", icon: UserPlus, count: n, tone: stuck.length ? "amber" : "blue", to: hr ? "/onboarding" : "/employees",
      text: [joining.length && `${joining.length} joining this week`, stuck.length && `${stuck.length} onboarding${stuck.length === 1 ? "" : "s"} waiting more than 7 days`].filter(Boolean).join(" · ") || "Joiners",
      sub: joining.map((e) => `${e.first_name} ${e.last_name} · ${niceDate(e.hire_date)}`).concat(stuck.map((s) => `${s.first_name} ${s.last_name} · ${s.status}`)).slice(0, 4).join(" · ") }, "joiners");
    const permits = emps.data.filter((e) => e.status !== "terminated" && within(e.work_permit_expiry, iso(today()), plusDays(60)));
    if (permits.length) rows.push({ key: "permit", icon: FileWarning, count: permits.length, tone: "amber", to: "/employees",
      text: `${permits.length} work permit${permits.length === 1 ? "" : "s"} expire within 60 days`,
      sub: permits.map((e) => `${e.first_name} ${e.last_name} · ${niceDate(e.work_permit_expiry)}`).slice(0, 4).join(" · ") });
    else quiet.push("work permits");
    const cel = emps.data.filter((e) => e.status === "active").flatMap((e) => [
      ...(annualInWeek(e.date_of_birth, wFrom, wTo) ? [`${e.first_name} ${e.last_name} (birthday ${niceDate(e.date_of_birth)})`] : []),
      ...(annualInWeek(e.hire_date, wFrom, wTo) && (e.hire_date || "").slice(0, 4) < wFrom.slice(0, 4) ? [`${e.first_name} ${e.last_name} (${today().getFullYear() - Number((e.hire_date || "").slice(0, 4))} yr)`] : []),
    ]);
    add({ key: "cel", icon: Cake, count: cel.length, tone: "green", to: "/employees",
      text: `${cel.length} birthday${cel.length === 1 ? "" : "s"} or work anniversar${cel.length === 1 ? "y" : "ies"} this week`, sub: cel.slice(0, 4).join(" · ") }, "birthdays and anniversaries");
  }
  if (exits.data) {
    const open = exits.data.cases || [];
    const lwd = open.filter((c) => within(c.lwd || c.expected_lwd, wFrom, wTo));
    add({ key: "exit", icon: LogOut, count: open.length, tone: lwd.length ? "amber" : "blue", to: manager ? "/exits/team" : "/exits",
      text: `${open.length} exit${open.length === 1 ? "" : "s"} in progress${lwd.length ? ` · ${lwd.length} last working day this week` : ""}`,
      sub: open.slice(0, 3).map((c) => `${c.employee_name} · ${c.status_label}${c.lwd ? ` · last day ${niceDate(c.lwd)}` : ""}`).join(" · ") }, "exits");
  }
  if (revisions.data) {
    const n = revisions.data.length;
    add({ key: "rev", icon: TrendingUp, count: n, tone: "amber", to: "/salary-revisions",
      text: `${n} salary revision${n === 1 ? "" : "s"} awaiting approval` }, "salary revisions");
  }
  if (runs.data) {
    const run = runs.data.find((r) => (r.period_end || "").startsWith(ym) || (r.pay_date || "").startsWith(ym));
    const status = run ? run.status : "none";
    const word = status === "none" ? `No ${monthLabel} payroll run yet` : `${monthLabel} payroll run: ${status}${run ? ` · ${run.employee_count} people · ${money(run.total_net)} net` : ""}`;
    rows.push({ key: "pay", icon: Wallet, count: null, tone: status === "paid" || status === "approved" ? "green" : "amber", to: "/payroll",
      text: word });
  }
  if (payroll) {
    rows.push({ key: "stat", icon: Landmark, count: null, tone: "gray", to: "/payroll/canada/remittances", text: "CRA remittance schedule",
      sub: "Source-deduction due dates for your remitter type, plus provincial EHT and WCB — check before each pay date" });
  }

  const urgent = rows.filter((r) => r.tone === "red").length;
  return (
    <PanelShell title="Needs your attention" icon={Inbox}
      right={<span className={`text-[11px] font-medium rounded-lg px-2.5 py-1 ${urgent ? "bg-red-50 text-red-600" : "bg-surface text-gray-500 border border-surface-border"}`}>
        {loading && rows.length === 0 ? "checking…" : urgent ? `${urgent} urgent` : `${rows.length} item${rows.length === 1 ? "" : "s"}`}</span>}>
      {loading && rows.length === 0 ? <div className="grid place-items-center py-6"><Spinner /></div> : (
        <div className="grid grid-cols-1 lg:grid-cols-2 lg:[&>a:nth-child(odd)]:border-r lg:[&>a]:border-surface-border/60">
          {rows.map((r) => <RowLine key={r.key} r={r} />)}
          {rows.length === 0 && <p className="px-4 py-4 text-sm text-gray-400 lg:col-span-2">Nothing needs you right now.</p>}
        </div>
      )}
      {quiet.length > 0 && (
        <p className="px-4 py-2 text-[11px] text-gray-400 border-t border-surface-border/60">
          Nothing else needs you — {quiet.join(", ")} are all clear.
        </p>
      )}
    </PanelShell>
  );
}

/* ── Employee: My week ──────────────────────────────────────────────────── */
export function MyWeekPanel() {
  const mine = useSafe<ApprovalList>("mine", approvalsApi.mine, true);
  const balances = useSafe<{ balances: any[] }>("balances", () => leaveApi.balances(), true);
  const holidays = useSafe<MasterRow[]>("holidays", () => mastersApi.list("stat-holidays"), true);
  const slips = useSafe<PayslipRow[]>("payslips", () => payrollApi.payslips(), true);
  const [salaryOn] = useSalaryVisible();   // 2026-10-16: salary masked until "Show salary" (this session only)
  const loading = [mine, balances, holidays, slips].some((q) => q.isLoading);
  const rows: Row[] = [];
  if (mine.data) {
    const pending = mine.data.items.filter((i) => i.status === "pending");
    rows.push({ key: "req", icon: CheckSquare, count: pending.length, tone: pending.length ? "amber" : "gray", to: "/my-requests",
      text: pending.length ? `${pending.length} of my request${pending.length === 1 ? " is" : "s are"} waiting for a decision` : "No requests waiting on anyone",
      sub: pending.slice(0, 3).map((p) => `${p.kind_label}${p.waiting_on ? ` · with ${p.waiting_on.split(",")[0]}` : ""}`).join(" · ") });
  }
  if (balances.data) {
    const b = (balances.data.balances || []).filter((x) => x.paid && (x.category || "usable") === "usable");
    const total = usableLeave(balances.data);   // usable types only — event leave is not "days left"
    rows.push({ key: "bal", icon: CalendarDays, count: Math.round(total), tone: "green", to: "/leave",
      text: `${total} paid leave day${total === 1 ? "" : "s"} left this year`, sub: b.filter((x) => x.entitled > 0).map((x) => `${x.leave_type_code} ${x.remaining}/${x.entitled}`).join(" · ") || "No leave allocated yet — ask HR" });
  }
  if (holidays.data) {
    const t = iso(today());
    const next = [...holidays.data].filter((h: any) => (h.holiday_date || "") >= t).sort((a: any, b: any) => a.holiday_date.localeCompare(b.holiday_date))[0] as any;
    rows.push({ key: "hol", icon: Sparkles, count: null, tone: "blue", to: "/stat-holidays",
      text: next ? `Next holiday: ${next.name} on ${niceDate(next.holiday_date)}` : "No upcoming holiday in the list", sub: next?.province ? `${next.province} list` : undefined });
  }
  if (slips.data) {
    const last = [...slips.data].sort((a, b) => (b.pay_date || "").localeCompare(a.pay_date || ""))[0];
    rows.push({ key: "slip", icon: Receipt, count: null, tone: "green", to: "/my-pay",
      text: last ? `Latest payslip: ${niceDate(last.pay_date)} · ${salaryOn ? money(last.net_pay) : MASK} net` : "No payslip yet", sub: last ? `Period ending ${niceDate(last.period_end)}` : undefined });
  }
  return (
    <PanelShell title="My week" icon={CalendarDays}>
      {loading && rows.length === 0 ? <div className="grid place-items-center py-6"><Spinner /></div> : (
        <div className="grid grid-cols-1 lg:grid-cols-2 lg:[&>a:nth-child(odd)]:border-r lg:[&>a]:border-surface-border/60">
          {rows.map((r) => <RowLine key={r.key} r={r} />)}
          {rows.length === 0 && <p className="px-4 py-4 text-sm text-gray-400 lg:col-span-2">Nothing to show yet.</p>}
        </div>
      )}
    </PanelShell>
  );
}

/* ── Org admin: compact setup banner ────────────────────────────────────── */
export function SetupBanner() {
  const r = useSetupReadiness();
  if (r.loading || r.total === 0 || r.complete) return null;
  return (
    <Link to="/settings" className="flex items-center gap-3 rounded-xl border border-primary-100 bg-primary-50/60 px-4 py-2.5 hover:bg-primary-50 transition-colors">
      <span className="h-8 w-8 shrink-0 rounded-lg bg-white text-primary-600 grid place-items-center shadow-sm"><Settings className="h-4 w-4" /></span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-gray-800">Setup {r.done}/{r.total} complete — open Settings hub</p>
        <p className="text-[11px] text-gray-500 truncate">
          {r.items.filter((i) => i.status === "needs").map((i) => i.label).slice(0, 4).join(" · ")}{r.needs > 4 ? ` · +${r.needs - 4} more` : ""}
        </p>
      </div>
      <div className="hidden sm:block w-28 h-1.5 rounded-full bg-white overflow-hidden"><div className="h-full bg-primary-500" style={{ width: `${r.pct}%` }} /></div>
      <ArrowRight className="h-4 w-4 text-primary-500 shrink-0" />
    </Link>
  );
}
