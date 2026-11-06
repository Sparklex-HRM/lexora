import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Users, UserPlus, CalendarClock, Wallet, MapPin, Landmark, Cake, Award,
  ClipboardCheck, Rocket, ArrowRight, ArrowUp, Download, CalendarDays, ChevronDown,
  Activity as ActIcon, FileText, BarChart3, Gift, GraduationCap, DollarSign, LucideIcon,
} from "lucide-react";
import {
  PieChart, Pie, Cell, AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, LabelList,
} from "recharts";
import toast from "react-hot-toast";
import { Spinner } from "../components/ui";
import { employeeApi, onboardingApi, money, moneySym, moneyLocaleName, PAY_FREQUENCIES, Employee } from "../api/endpoints";
import { useAuthStore, HR_WRITE, ADMIN_ROLES, HR_ROLES } from "../store/authStore";

// Pay totals are the pay office's (org admin, HR, payroll). A manager or the accountant also lands on
// this dashboard but sees no pay KPI / panel (decision 2026-10-21: no compensation outside the pay office).
const PAY_OFFICE = [...ADMIN_ROLES, ...HR_ROLES, "payroll_admin"];
import { AttentionPanel, MyWeekPanel, SetupBanner } from "./settings/AttentionPanel";   // U4: what needs me today
import { SetupChecklistCard } from "../components/SubscriptionBanner";   // S4: new-org checklist
import EmployeeHome from "./employee/EmployeeHome";   // 2026-10-16 employee portal
import { can } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const C = {
  purple: "#4f46e5", purpleL: "#818cf8",   // Lexora indigo (primary)
  teal: "#0ea5e9", tealL: "#38bdf8",       // sky (accent)
  green: "#22c55e", amber: "#f59e0b", red: "#ef4444", blue: "#3b82f6", pink: "#ec4899", slate: "#cbd5e1",
};
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/* compact currency so KPI numbers never truncate: $635K · $1.02M (CAD) */
const kmoney = (n: number) => {
  const v = Math.round(Number(n) || 0);
  const sym = moneySym();
  if (Math.abs(v) >= 1_000_000) return sym + (v / 1_000_000).toFixed(2).replace(/\.?0+$/, "") + "M";
  if (Math.abs(v) >= 10_000) return sym + Math.round(v / 1000) + "K";
  return sym + v.toLocaleString(moneyLocaleName());
};

/* ── building blocks ── */
function Kpi({ label, value, delta, up, icon: Icon, tone }: {
  label: string; value: React.ReactNode; delta?: string; up?: boolean; icon: LucideIcon; tone: string;
}) {
  return (
    <div className="card !p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider truncate">{label}</p>
          <p className="text-lg sm:text-xl leading-tight font-bold text-gray-800 mt-1 tabular-nums truncate">{value}</p>
          {delta && (
            <p className={`text-[10px] mt-1 flex items-center gap-0.5 font-medium truncate ${up ? "text-green-600" : "text-gray-400"}`}>
              {up && <ArrowUp className="h-2.5 w-2.5 shrink-0" />}{delta}
            </p>
          )}
        </div>
        <div className={`h-8 w-8 shrink-0 rounded-xl grid place-items-center ${tone}`}><Icon className="h-4 w-4" /></div>
      </div>
    </div>
  );
}
function Chip({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-500 bg-surface border border-surface-border rounded-lg px-2.5 py-1 cursor-default">
      {children}<ChevronDown className="h-3 w-3 text-gray-400" />
    </span>
  );
}
function Panel({ title, icon: Icon, filter, footer, children, className = "" }: {
  title: string; icon?: LucideIcon; filter?: React.ReactNode;
  footer?: { to: string; label: string }; children: React.ReactNode; className?: string;
}) {
  return (
    <div className={`card !p-0 flex flex-col ${className}`}>
      <div className="flex items-center justify-between px-4 pt-4 pb-3">
        <h3 className="font-display font-semibold text-gray-800 text-[13px] flex items-center gap-1.5">
          {Icon && <Icon className="h-3.5 w-3.5 text-primary-500" />}{title}
        </h3>
        {filter}
      </div>
      <div className="px-4 flex-1">{children}</div>
      {footer && (
        <Link to={footer.to} className="mt-3 px-4 py-3 border-t border-surface-border/70 text-xs font-medium text-primary-600 hover:text-primary-700 flex items-center gap-1">
          {footer.label} <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      )}
    </div>
  );
}
const dot = (c: string) => <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: c }} />;
const INITIAL_TONES = ["bg-primary-100 text-primary-700", "bg-teal-100 text-teal-600", "bg-blue-100 text-blue-600", "bg-amber-100 text-amber-600", "bg-pink-100 text-pink-600"];
function Avatar({ name, i = 0 }: { name: string; i?: number }) {
  const initials = name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();
  return <span className={`h-9 w-9 shrink-0 rounded-full grid place-items-center text-xs font-semibold ${INITIAL_TONES[i % INITIAL_TONES.length]}`}>{initials}</span>;
}
/* semicircular gauge — reference "satisfaction 67%" style */
function Gauge({ value, label, color }: { value: number; label?: string; color: string }) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  const data = [{ value: v }, { value: 100 - v }];
  return (
    <div className="relative h-[74px] my-1">
      <ResponsiveContainer>
        <PieChart>
          <Pie data={data} dataKey="value" startAngle={180} endAngle={0} cx="50%" cy="94%" innerRadius={38} outerRadius={56} cornerRadius={4} stroke="none">
            <Cell fill={color} /><Cell fill="#eef0f6" />
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="absolute inset-x-0 bottom-0 text-center pointer-events-none">
        <div className="text-lg font-bold text-gray-800 tabular-nums leading-none">{v}%</div>
        {label && <div className="text-[10px] text-gray-400 mt-0.5">{label}</div>}
      </div>
    </div>
  );
}

function daysUntilAnnual(iso?: string | null): number | null {
  if (!iso) return null;
  const now = new Date(); const p = iso.split("-").map(Number);
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let next = new Date(now.getFullYear(), p[1] - 1, p[2]);
  if (next < today) next = new Date(now.getFullYear() + 1, p[1] - 1, p[2]);
  return Math.round((next.getTime() - today.getTime()) / 86400000);
}

/** The employee role gets its own home (no org KPIs); every other role keeps the dashboard below. */
export default function DashboardHome() {
  const role = useAuthStore((s) => s.user?.role);
  return role === "employee" ? <EmployeeHome /> : <AdminDashboardHome />;
}

function AdminDashboardHome() {
  const user = useAuthStore((s) => s.user);
  const { data: emps, isLoading } = useQuery({ queryKey: ["employees"], queryFn: employeeApi.list });
  // onboarding sessions are HR data: only HR / admin ask for them (others would get 403)
  const { data: sessions } = useQuery({ queryKey: ["onboarding"], queryFn: () => onboardingApi.list(),
    enabled: can(user, "onboarding.view", HR_WRITE) });
  if (isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  const list: Employee[] = emps || [];
  const payOffice = PAY_OFFICE.includes(user?.role || "");
  const now = new Date();
  const ymNow = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const active = list.filter((e) => e.status === "active");
  const onboardingCount = (sessions || []).filter((s) => s.status !== "activated" && s.status !== "cancelled").length;
  const newJoiners = list.filter((e) => (e.hire_date || "").startsWith(ymNow)).length;
  const annual = list.reduce((s, e) => s + (e.annual_salary || 0), 0);
  const provinces = new Set(list.map((e) => e.province_of_employment).filter(Boolean));

  const statusMix = [
    { name: "Active", value: active.length, color: C.green },
    { name: "Onboarding", value: list.filter((e) => e.status === "onboarding").length + onboardingCount, color: C.purple },
    { name: "On leave", value: list.filter((e) => e.status === "on_leave").length, color: C.amber },
    { name: "Terminated", value: list.filter((e) => e.status === "terminated").length, color: C.red },
  ].filter((s) => s.value > 0);
  const statusTotal = statusMix.reduce((s, x) => s + x.value, 0) || 1;

  const trend = Array.from({ length: 6 }).map((_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - (5 - i), 1);
    const e = new Date(d.getFullYear(), d.getMonth() + 1, 0);          // local month end (not UTC — IST is +5:30)
    const end = `${e.getFullYear()}-${String(e.getMonth() + 1).padStart(2, "0")}-${String(e.getDate()).padStart(2, "0")}`;
    return { name: MONTHS[d.getMonth()], count: list.filter((e) => (e.hire_date || "9999") <= end).length };
  });
  const byDept = Object.entries(list.reduce<Record<string, number>>((m, e) => {
    const k = e.department || "Unassigned"; m[k] = (m[k] || 0) + 1; return m;
  }, {})).map(([name, count]) => ({ name, count })).sort((a, b) => b.count - a.count).slice(0, 8);
  const freqMix = PAY_FREQUENCIES.map((f) => ({ name: f.label, count: list.filter((e) => e.pay_frequency === f.value).length })).filter((f) => f.count > 0);
  const payrollReady = list.length ? (100 * list.filter((e) => e.bank_transit && e.td1_federal_claim != null).length) / list.length : 0;

  const events = [
    ...list.filter((e) => e.date_of_birth).map((e) => ({ kind: "birthday" as const, name: `${e.first_name} ${e.last_name}`, days: daysUntilAnnual(e.date_of_birth) })),
    ...list.filter((e) => e.hire_date).map((e) => ({ kind: "anniversary" as const, name: `${e.first_name} ${e.last_name}`, days: daysUntilAnnual(e.hire_date) })),
  ].filter((e) => e.days !== null && e.days! <= 45).sort((a, b) => a.days! - b.days!).slice(0, 4);
  const eol = new Date(now.getFullYear(), now.getMonth() + 1, 0);

  const pipe = {
    total: (sessions || []).length,
    progress: (sessions || []).filter((s) => s.status === "invited").length,
    review: (sessions || []).filter((s) => s.status === "submitted").length,
    activated: (sessions || []).filter((s) => s.status === "activated").length,
  };
  const activity = (sessions || []).slice(0, 5).map((s) => {
    const when = s.activated_at || s.submitted_at || s.created_at;
    const [verb, tone] = s.status === "activated" ? ["was activated as an employee", C.green]
      : s.status === "submitted" ? ["submitted their intake for review", C.amber]
      : s.status === "approved" ? ["was approved by HR", C.teal] : ["started onboarding", C.purple];
    return { name: `${s.first_name} ${s.last_name}`, verb, when, tone: tone as string, status: s.status };
  });

  const exportCsv = () => {
    const cols = ["employee_number", "first_name", "last_name", "department", "job_title", "province_of_employment", "status",
      ...(payOffice ? ["pay_frequency", "annual_salary"] : [])];
    const rows = [cols.join(",")].concat(list.map((e) => cols.map((c) => `"${(e as any)[c] ?? ""}"`).join(",")));
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "lexora-employees.csv"; a.click();
    toast.success("Employee report exported");
  };
  const fmtDate = (d: Date) => d.toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" });

  return (
    <div className="space-y-4">
      {/* header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-display font-bold text-gray-800">Dashboard</h1>
          <p className="text-sm text-gray-400">Welcome back, {user?.first_name || "there"} 👋 · {user?.org_name || "your organization"}</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs text-gray-600 bg-white border border-surface-border rounded-lg px-3 py-2">
            <CalendarDays className="h-3.5 w-3.5 text-gray-400" />{fmtDate(now)}<ChevronDown className="h-3 w-3 text-gray-300" />
          </span>
          <button className="btn-primary flex items-center gap-1.5 text-sm" onClick={exportCsv}><Download className="h-4 w-4" /> Download Report</button>
        </div>
      </div>

      {/* U4: setup banner (org admin, until the checklist is complete) + what needs me today */}
      <SetupChecklistCard />
      {can(user, "settings.edit", ADMIN_ROLES) && <SetupBanner />}
      {user?.role === "employee" ? <MyWeekPanel /> : <AttentionPanel />}

      {/* KPI row */}
      <div className={`grid grid-cols-2 md:grid-cols-3 ${payOffice ? "lg:grid-cols-5" : "lg:grid-cols-4"} gap-3`}>
        <Kpi label="Total Employees" value={list.length.toLocaleString()} delta={`${active.length} active`} up icon={Users} tone="bg-primary-50 text-primary-600" />
        <Kpi label="Onboarding" value={onboardingCount} delta={`${pipe.review} awaiting review`} up={pipe.review > 0} icon={UserPlus} tone="bg-teal-50 text-teal-500" />
        <Kpi label={`New Joiners (${MONTHS[now.getMonth()]})`} value={newJoiners} delta="hired this month" up={newJoiners > 0} icon={CalendarClock} tone="bg-blue-50 text-blue-500" />
        {payOffice && <Kpi label="Annual Payroll" value={kmoney(annual)} delta={`${moneySym()}${Math.round(annual / 12).toLocaleString(moneyLocaleName())}/mo`} icon={Wallet} tone="bg-amber-50 text-amber-500" />}
        <Kpi label="Provinces" value={provinces.size} delta={[...provinces].join(" · ") || "—"} icon={MapPin} tone="bg-green-50 text-green-600" />
      </div>

      {/* charts row */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <Panel title="Workforce Status" icon={Users} filter={<Chip>This month</Chip>} footer={{ to: "/employees", label: "View Workforce Report" }}>
          <div className="flex items-center gap-4 py-1">
            <div className="relative h-28 w-28 shrink-0">
              <ResponsiveContainer>
                <PieChart>
                  <Pie data={statusMix.length ? statusMix : [{ name: "None", value: 1, color: C.slate }]} dataKey="value" innerRadius={36} outerRadius={52} paddingAngle={3} cornerRadius={5} stroke="none">
                    {(statusMix.length ? statusMix : [{ color: C.slate }]).map((s, i) => <Cell key={i} fill={s.color} />)}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
              <div className="absolute inset-0 grid place-items-center pointer-events-none">
                <div className="text-center"><div className="text-lg font-bold text-gray-800 tabular-nums">{list.length}</div><div className="text-[9px] text-gray-400">Total</div></div>
              </div>
            </div>
            <div className="flex-1 space-y-2">
              {statusMix.map((s) => (
                <div key={s.name} className="flex items-center justify-between text-sm">
                  <span className="flex items-center gap-2 text-gray-600">{dot(s.color)}{s.name}</span>
                  <span className="text-gray-800 font-medium tabular-nums">{s.value} <span className="text-gray-300 text-xs">({Math.round((s.value / statusTotal) * 100)}%)</span></span>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel title="Employee Trend" icon={BarChart3} filter={<Chip>Last 6 months</Chip>} footer={{ to: "/reports", label: "View Full Report" }}>
          <div className="h-32 pt-1">
            <ResponsiveContainer>
              <AreaChart data={trend} margin={{ top: 8, right: 6, left: -22, bottom: 0 }}>
                <defs><linearGradient id="hc" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={C.purple} stopOpacity={0.38} /><stop offset="55%" stopColor={C.purple} stopOpacity={0.12} /><stop offset="100%" stopColor={C.purple} stopOpacity={0} />
                </linearGradient></defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#eef0f6" vertical={false} />
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#9ca3af" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "#9ca3af" }} axisLine={false} tickLine={false} allowDecimals={false} width={34} />
                <Tooltip />
                <Area type="monotone" dataKey="count" stroke={C.purple} strokeWidth={2.5} fill="url(#hc)"
                  dot={{ r: 3.5, fill: "#fff", stroke: C.purple, strokeWidth: 2 }} activeDot={{ r: 5 }} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Upcoming Events" icon={CalendarDays} filter={<Link to="/employees" className="text-[11px] text-primary-600 hover:underline">View All</Link>}>
          <div className="space-y-2.5 py-1">
            {events.map((e, i) => (
              <div key={i} className="flex items-center gap-3">
                <Avatar name={e.name} i={i} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-700 font-medium truncate">{e.kind === "birthday" ? "Employee Birthday" : "Work Anniversary"}</p>
                  <p className="text-[11px] text-gray-400 truncate">{e.name}</p>
                </div>
                <span className={`h-7 w-7 shrink-0 rounded-lg grid place-items-center ${e.kind === "birthday" ? "bg-pink-50 text-pink-500" : "bg-amber-50 text-amber-500"}`}>
                  {e.kind === "birthday" ? <Gift className="h-3.5 w-3.5" /> : <Award className="h-3.5 w-3.5" />}
                </span>
              </div>
            ))}
            <div className="flex items-center gap-3">
              <span className="h-9 w-9 shrink-0 rounded-full grid place-items-center bg-green-50 text-green-600"><DollarSign className="h-4 w-4" /></span>
              <div className="flex-1 min-w-0"><p className="text-sm text-gray-700 font-medium">Payroll Processing</p><p className="text-[11px] text-gray-400">Next pay run</p></div>
              <span className="text-[11px] text-gray-400 whitespace-nowrap">{fmtDate(eol)}</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="h-9 w-9 shrink-0 rounded-full grid place-items-center bg-blue-50 text-blue-600"><GraduationCap className="h-4 w-4" /></span>
              <div className="flex-1 min-w-0"><p className="text-sm text-gray-700 font-medium">TD1 review reminder</p><p className="text-[11px] text-gray-400">Annual tax forms</p></div>
            </div>
          </div>
        </Panel>
      </div>

      {/* summary + quick access row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <Panel title="Onboarding Summary" icon={UserPlus} filter={<Chip>This month</Chip>} footer={{ to: "/onboarding", label: "View Onboarding" }}>
          <div className="space-y-2.5 text-sm py-0.5">
            <SumRow k="Total sessions" v={pipe.total} />
            <SumRow k="In progress" v={pipe.progress} tone="text-amber-600" />
            <SumRow k="Awaiting review" v={pipe.review} tone="text-primary-600" />
            <SumRow k="Activated" v={pipe.activated} tone="text-green-600" />
          </div>
        </Panel>

        {payOffice && <Panel title="Payroll Summary" icon={Wallet} filter={<Chip>{MONTHS[now.getMonth()]}</Chip>} footer={{ to: "/payroll", label: "View Payroll Dashboard" }}>
          <div className="rounded-lg bg-green-50 border border-green-100 px-3 py-2 flex items-center gap-2">
            <span className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
            <span className="text-xs text-green-700 font-medium">Payroll status · Ready</span>
          </div>
          <Gauge value={payrollReady} label="payroll-ready" color={C.teal} />
          <div className="flex justify-between text-sm">
            <div><div className="text-[11px] text-gray-400">Employees</div><div className="font-bold text-gray-800 tabular-nums">{list.length}</div></div>
            <div className="text-right"><div className="text-[11px] text-gray-400">Annual cost</div><div className="font-bold text-gray-800 tabular-nums">{kmoney(annual)}</div></div>
          </div>
        </Panel>}

        {payOffice && <Panel title="Pay-frequency Mix" icon={CalendarClock} filter={<Chip>All</Chip>}>
          <div className="space-y-2.5 py-1">
            {freqMix.map((f, i) => {
              const pct = Math.round((f.count / list.length) * 100);
              return (
                <div key={f.name}>
                  <div className="flex justify-between text-xs text-gray-500 mb-1"><span>{f.name}</span><span className="tabular-nums">{f.count} ({pct}%)</span></div>
                  <div className="h-1.5 rounded-full bg-primary-50 overflow-hidden"><div className="h-full rounded-full" style={{ width: `${pct}%`, background: [C.purple, C.teal, C.blue, C.amber][i % 4] }} /></div>
                </div>
              );
            })}
          </div>
        </Panel>}

        <Panel title="Quick Access">
          <div className="grid grid-cols-4 gap-2 py-1">
            {[
              { to: "/onboarding", label: "Onboard", icon: UserPlus, tone: "bg-primary-50 text-primary-600" },
              { to: "/employees", label: "People", icon: Users, tone: "bg-teal-50 text-teal-500" },
              ...(payOffice ? [{ to: "/payroll", label: "Payroll", icon: Wallet, tone: "bg-amber-50 text-amber-500" }] : []),
              { to: "/attendance", label: "Time", icon: CalendarClock, tone: "bg-blue-50 text-blue-500" },
              { to: "/leave", label: "Leave", icon: CalendarDays, tone: "bg-green-50 text-green-600" },
              { to: "/approvals", label: "Approve", icon: ClipboardCheck, tone: "bg-pink-50 text-pink-500" },
              { to: "/reports", label: "Reports", icon: FileText, tone: "bg-primary-50 text-primary-600" },
              { to: "/masters", label: "Masters", icon: Landmark, tone: "bg-teal-50 text-teal-500" },
            ].map((q) => (
              <Link key={q.to} to={q.to} className="flex flex-col items-center gap-1.5 py-2 rounded-lg hover:bg-surface transition-colors">
                <span className={`h-10 w-10 rounded-2xl grid place-items-center ${q.tone}`}><q.icon className="h-4 w-4" /></span>
                <span className="text-[10px] text-gray-500">{q.label}</span>
              </Link>
            ))}
          </div>
        </Panel>
      </div>

      {/* department + activity row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Panel title="Department-wise Headcount" icon={BarChart3} className="lg:col-span-2" filter={<Chip>Active people</Chip>}>
          <div className="h-44 pt-1">
            {byDept.length ? (
              <ResponsiveContainer>
                <BarChart data={byDept} margin={{ top: 16, right: 8, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#eef0f6" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: "#9ca3af" }} axisLine={false} tickLine={false} interval={0} />
                  <YAxis tick={{ fontSize: 11, fill: "#9ca3af" }} axisLine={false} tickLine={false} allowDecimals={false} width={30} />
                  <Tooltip cursor={{ fill: "#f0edf9" }} />
                  <Bar dataKey="count" radius={[7, 7, 0, 0]} maxBarSize={44} fill={C.purple}>
                    <LabelList dataKey="count" position="top" style={{ fill: "#6b7280", fontSize: 11, fontWeight: 600 }} />
                    {byDept.map((_, i) => <Cell key={i} fill={i % 2 ? C.purpleL : C.purple} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : <div className="h-full grid place-items-center text-xs text-gray-400">Add employees with departments to populate this.</div>}
          </div>
        </Panel>

        <Panel title="Recent Activities" icon={ActIcon} filter={<Link to="/onboarding" className="text-[11px] text-primary-600 hover:underline">View All</Link>}>
          <div className="space-y-3.5 py-1">
            {activity.length ? activity.map((a, i) => (
              <div key={i} className="flex gap-3">
                <span className="mt-0.5 h-8 w-8 shrink-0 rounded-full grid place-items-center" style={{ background: `${a.tone}1a`, color: a.tone }}>
                  {a.status === "activated" ? <Rocket className="h-3.5 w-3.5" /> : a.status === "submitted" ? <ClipboardCheck className="h-3.5 w-3.5" /> : <UserPlus className="h-3.5 w-3.5" />}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-gray-700 leading-snug"><b className="font-medium">{a.name}</b> {a.verb}</p>
                  <p className="text-[11px] text-gray-400">{a.when ? new Date(a.when).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : ""}</p>
                </div>
              </div>
            )) : <div className="flex flex-col items-center gap-2 py-8 text-gray-400"><ActIcon className="h-6 w-6" /><p className="text-xs">Activity appears as you onboard people.</p></div>}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function SumRow({ k, v, tone }: { k: string; v: number; tone?: string }) {
  return <div className="flex items-center justify-between"><span className="text-gray-500">{k}</span><span className={`font-semibold tabular-nums ${tone || "text-gray-800"}`}>{v}</span></div>;
}
