import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import DataGrid, { Tabs, type GridColumn } from "../components/DataGrid";
import { leaveApi, mastersApi, type LeaveRequest } from "../api/endpoints";
import { errText } from "../api/platform";
import { useAuthStore } from "../store/authStore";
import { viewRoleFor } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const HR = ["superadmin", "org_admin", "hr_admin", "hr_manager"];        // the roles the leave service treats as "whole company"
const WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const nice = (s: string) => new Date(s + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

/** Team absence calendar: approved + pending leave and holidays, from the leave service.
 *  HR sees the whole company, a manager their team, everyone else their own leave. */
export default function AbsenceCalendarPage() {
  const user = useAuthStore((s) => s.user);
  const role = viewRoleFor(user, "leave.view", [...HR, "manager"]);   // a custom leave role gets its acting view
  const scope = HR.includes(role) ? "org" : role === "manager" ? "team" : "mine";
  const scopeLabel = scope === "org" ? "Whole company" : scope === "team" ? "Your team" : "Your own leave";
  const [view, setView] = useState<"calendar" | "list">("calendar");
  const [month, setMonth] = useState(() => { const d = new Date(); return new Date(d.getFullYear(), d.getMonth(), 1); });
  const [state, setState] = useState("");
  const [open, setOpen] = useState<string | null>(null);

  const leave = useQuery({ queryKey: ["lv", "calendar", scope], queryFn: () => leaveApi.requests({ scope, status: "pending,approved" }), enabled: scope !== "mine" || !!user?.employee_id });
  const mine = useQuery({ queryKey: ["lv", "calendar", "mine"], queryFn: () => leaveApi.requests({ scope: "mine", status: "pending,approved" }), enabled: scope === "team" && !!user?.employee_id });
  const holidays = useQuery({ queryKey: ["masters", "stat-holidays"], queryFn: () => mastersApi.list("stat-holidays") });

  const rows = useMemo(() => {
    const seen = new Set<string>();
    return [...(leave.data || []), ...(mine.data || [])].filter((r) => (seen.has(r.id) ? false : (seen.add(r.id), true)))
      .filter((r) => !state || r.status === state);
  }, [leave.data, mine.data, state]);
  const holidayByDay = useMemo(() => {
    const m: Record<string, string[]> = {};
    for (const h of holidays.data || []) { const d = String(h.holiday_date || "").slice(0, 10); if (d) (m[d] ||= []).push(h.name); }
    return m;
  }, [holidays.data]);

  const cells = useMemo(() => {
    const first = new Date(month), lead = (first.getDay() + 6) % 7;
    const days = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    const out: (string | null)[] = Array(lead).fill(null);
    for (let d = 1; d <= days; d++) out.push(iso(new Date(month.getFullYear(), month.getMonth(), d)));
    while (out.length % 7) out.push(null);
    return out;
  }, [month]);
  const onDay = (d: string) => rows.filter((r) => r.start_date <= d && r.end_date >= d);
  const today = iso(new Date());
  const monthStart = iso(month), monthEnd = iso(new Date(month.getFullYear(), month.getMonth() + 1, 0));
  const inMonth = rows.filter((r) => r.start_date <= monthEnd && r.end_date >= monthStart);

  const cols: GridColumn<LeaveRequest>[] = [
    { key: "employee_name", label: "Employee", render: (r) => <div><div className="text-gray-800">{r.employee_name || "—"}</div><div className="text-[11px] text-gray-400">{r.employee_number || ""}</div></div>, text: (r) => `${r.employee_name || ""} ${r.employee_number || ""}` },
    { key: "leave_type_name", label: "Leave type", text: (r) => r.leave_type_name || r.leave_type_code },
    { key: "start_date", label: "From", type: "date" }, { key: "end_date", label: "To", type: "date" },
    { key: "days", label: "Days", type: "number" },
    { key: "status", label: "State", text: (r) => (r.status === "approved" ? "Approved" : "Waiting for approval"), render: (r) => <span className={r.status === "approved" ? "badge-green" : "badge-amber"} style={{ textTransform: "none" }}>{r.status === "approved" ? "Approved" : "Waiting for approval"}</span> },
    { key: "manager_name", label: "Manager" },
  ];

  const loading = leave.isLoading || holidays.isLoading;
  return (
    <div>
      <PageHeader title="Absence Calendar" icon={CalendarDays} subtitle={`${scopeLabel} · approved and pending leave, with holidays`} />
      {scope === "mine" && !user?.employee_id && <div className="card text-sm text-gray-600 mb-3">Your login is not linked to an employee record, so only holidays are shown.</div>}
      {leave.isError && <div className="rounded-lg bg-red-50 text-red-700 text-sm px-3 py-2 mb-3">Could not load leave. {errText(leave.error)}</div>}
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <div className="flex-1 min-w-[200px]"><Tabs tabs={[{ key: "calendar", label: "Calendar" }, { key: "list", label: "List" }]} value={view} onChange={setView} /></div>
        <select className="input !py-1.5 text-sm !w-auto mb-4" value={state} onChange={(e) => setState(e.target.value)} aria-label="State">
          <option value="">Approved and pending</option><option value="approved">Approved only</option><option value="pending">Pending only</option></select>
      </div>

      {loading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div> : view === "list" ? (
        <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName="absence-list" minWidth={860} initialSort={{ key: "start_date", dir: "desc" }}
          empty="No approved or pending leave." searchPlaceholder="Search person or leave type…" />
      ) : (
        <div className="card !p-0">
          <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
            <div className="flex items-center gap-1">
              <button className="p-1.5 rounded-lg hover:bg-gray-50 text-gray-500" aria-label="Previous month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))}><ChevronLeft className="w-4 h-4" /></button>
              <span className="font-display font-semibold text-gray-800 w-40 text-center">{month.toLocaleDateString(undefined, { month: "long", year: "numeric" })}</span>
              <button className="p-1.5 rounded-lg hover:bg-gray-50 text-gray-500" aria-label="Next month" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))}><ChevronRight className="w-4 h-4" /></button>
              <button className="btn-secondary !py-1 !px-2 text-xs ml-2" onClick={() => { const d = new Date(); setMonth(new Date(d.getFullYear(), d.getMonth(), 1)); }}>Today</button>
            </div>
            <div className="hidden sm:flex items-center gap-3 text-[11px] text-gray-500">
              <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-green-500" /> Approved</span>
              <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm border-2 border-dashed border-amber-500" /> Pending</span>
              <span className="inline-flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-primary-200" /> Holiday</span>
            </div>
          </div>
          <div className="grid grid-cols-7 text-[11px] uppercase text-gray-400 border-b border-surface-border">{WEEK.map((w) => <div key={w} className="px-1.5 py-1 text-center font-semibold">{w}</div>)}</div>
          <div className="grid grid-cols-7">
            {cells.map((d, i) => {
              if (!d) return <div key={i} className="min-h-[64px] sm:min-h-[96px] border-b border-r border-surface-border bg-surface/40" />;
              const people = onDay(d), hol = holidayByDay[d];
              return (
                <button key={d} onClick={() => setOpen(open === d ? null : d)} aria-label={`${nice(d)}: ${people.length} on leave${hol ? `, holiday ${hol.join(", ")}` : ""}`}
                  className={`min-h-[64px] sm:min-h-[96px] border-b border-r border-surface-border p-1 text-left align-top hover:bg-surface/60 ${hol ? "bg-primary-50/60" : ""} ${open === d ? "ring-2 ring-inset ring-primary-400" : ""}`}>
                  <div className={`text-xs font-semibold ${d === today ? "inline-block px-1.5 rounded-full bg-primary-600 text-white" : "text-gray-600"}`}>{Number(d.slice(8))}</div>
                  {hol && <div className="text-[10px] text-primary-700 truncate">{hol.join(", ")}</div>}
                  <div className="hidden sm:block space-y-0.5 mt-0.5">
                    {people.slice(0, 3).map((r) => (
                      <div key={r.id} className={`text-[10px] px-1 rounded truncate ${r.status === "approved" ? "bg-green-100 text-green-800" : "border border-dashed border-amber-500 text-amber-800"}`}>{r.employee_name || "—"}</div>
                    ))}
                    {people.length > 3 && <div className="text-[10px] text-gray-500">+{people.length - 3} more</div>}
                  </div>
                  {people.length > 0 && <div className="sm:hidden text-[10px] font-semibold text-gray-600 mt-1">{people.length} off</div>}
                </button>
              );
            })}
          </div>
          <div className="px-3 py-2.5 text-sm">
            {open ? (
              <div>
                <div className="font-semibold text-gray-800 mb-1">{nice(open)}{holidayByDay[open] ? ` · Holiday: ${holidayByDay[open].join(", ")}` : ""}</div>
                {onDay(open).length === 0 ? <div className="text-gray-400">Nobody is on leave.</div>
                  : <ul className="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-6 gap-y-1">{onDay(open).map((r) => (
                    <li key={r.id} className="text-gray-700">{r.employee_name || "—"} <span className="text-gray-400">· {r.leave_type_name || r.leave_type_code} · {r.status === "approved" ? "approved" : "waiting for approval"}</span></li>))}</ul>}
              </div>
            ) : <span className="text-gray-400">{inMonth.length ? `${inMonth.length} leave request${inMonth.length === 1 ? "" : "s"} this month. Pick a day to see who is off.` : "No leave this month."}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
