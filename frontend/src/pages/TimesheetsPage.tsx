import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck, Wand2, CheckCircle2, XCircle, Lock, Send, X, ArrowRight, Scale, ChevronDown } from "lucide-react";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell } from "recharts";
import toast from "react-hot-toast";
import { PageHeader, StatTile, Spinner, Empty, Badge } from "../components/ui";
import { timeApi, money, PAY_FREQUENCIES, type Timesheet } from "../api/endpoints";
import { useAuthStore, HR_WRITE } from "../store/authStore";
import { addDaysISO, todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today
import { can } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const RULE_PROVINCES = ["ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "NU", "YT"];
const shift = (iso: string, days: number) => addDaysISO(iso, days);
const fmt = (iso?: string | null) => iso ? new Date(iso + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric" }) : "—";
const TS_TONE: Record<string, string> = { draft: "onboarding", submitted: "on_leave", approved: "active", locked: "active", rejected: "terminated" };

/** Mirror of the backend feed: a timesheet's hours → REG / OT 1.5× / OT2 2× / STAT. */
function feedEarnings(t: Timesheet) {
  const rate = t.hourly_rate || 0;
  const out: Array<{ code: string; hours: number; amount: number; mult?: string }> = [];
  if (t.pay_type === "hourly" && t.reg_hours > 0) out.push({ code: "REG", hours: t.reg_hours, amount: t.reg_hours * rate });
  if (t.ot_hours > 0) out.push({ code: "OT", hours: t.ot_hours, amount: t.ot_hours * rate * 1.5, mult: "× 1.5" });
  if ((t.ot2_hours || 0) > 0) out.push({ code: "OT2", hours: t.ot2_hours || 0, amount: (t.ot2_hours || 0) * rate * 2, mult: "× 2" });
  const statAmt = t.stat_pay || t.stat_hours * rate;
  if (statAmt > 0) out.push({ code: "STAT", hours: t.stat_hours, amount: statAmt, mult: "avg wage" });
  return out;
}

export default function TimesheetsPage() {
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role) || "";
  const me = useAuthStore((s) => s.user?.employee_id) || "";
  // The buttons follow the server's rules (WASA 2026-10-21, kept by decision 6): HR may act on any
  // timesheet; a manager only on direct reports'; the owner submits their own; the pay office reads
  // readiness and the sheets but does not edit or approve them.
  const user = useAuthStore((s) => s.user);
  const isHr = can(user, ["attendance.edit", "attendance.approve"], HR_WRITE);
  const payOffice = isHr || can(user, "attendance.view", ["payroll_admin"]);
  const mgr = role === "manager" || (!isHr && can(user, "attendance.approve", ["manager"]));
  const canGenerate = isHr || mgr;
  const canSubmit = (t: Timesheet) => isHr || (!!me && t.employee_id === me) || (mgr && !!me && t.manager_id === me);
  const canReview = (t: Timesheet) => isHr || (mgr && !!me && t.manager_id === me);

  const [freq, setFreq] = useState("biweekly");
  const [end, setEnd] = useState(todayISO());
  const [start, setStart] = useState(shift(todayISO(), -13));
  const [statusFilter, setStatusFilter] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const list = useQuery({
    queryKey: ["ta", "timesheets", statusFilter, end],
    queryFn: () => timeApi.timesheets({ ...(statusFilter ? { status: statusFilter } : {}), period_end: end }),
  });
  const readiness = useQuery({
    queryKey: ["ta", "readiness", freq, start, end],
    queryFn: () => timeApi.readiness(freq, start, end),
    enabled: payOffice,                         // the payroll gate is the pay office's view
  });

  const invalidate = () => { qc.invalidateQueries({ queryKey: ["ta", "timesheets"] }); qc.invalidateQueries({ queryKey: ["ta", "readiness"] }); };
  const gen = useMutation({
    mutationFn: () => timeApi.generate({ period_start: start, period_end: end, pay_frequency: freq }),
    onSuccess: (r) => { toast.success(`${r.created} drafted${r.skipped ? `, ${r.skipped} already existed` : ""}`); invalidate(); },
    onError: () => toast.error("Generate failed"),
  });
  const act = useMutation({
    mutationFn: ({ id, op, note }: { id: string; op: string; note?: string }) =>
      op === "submit" ? timeApi.submitTimesheet(id)
        : op === "approve" ? timeApi.approveTimesheet(id, note)
        : op === "reject" ? timeApi.rejectTimesheet(id, note)
        : timeApi.lockTimesheet(id),
    onSuccess: (_r, v) => { toast.success(`Timesheet ${v.op === "submit" ? "submitted" : v.op + "d"}`); invalidate(); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Action failed"),
  });

  const rows = list.data || [];
  const totals = useMemo(() => {
    const t = rows.reduce((a, ts) => {
      a.reg += ts.reg_hours; a.ot += ts.ot_hours; a.ot2 += (ts.ot2_hours || 0); a.stat += ts.stat_hours;
      a.gross += feedEarnings(ts).reduce((s, e) => s + e.amount, 0);
      a.by[ts.status] = (a.by[ts.status] || 0) + 1;
      return a;
    }, { reg: 0, ot: 0, ot2: 0, stat: 0, gross: 0, by: {} as Record<string, number> });
    return t;
  }, [rows]);
  const STATUS_ORDER = [["draft", "Draft", "#94a3b8"], ["submitted", "Submitted", "#d97706"], ["approved", "Approved", "#059669"], ["locked", "Locked", "#4f46e5"], ["rejected", "Rejected", "#e11d48"]] as const;
  const statusData = STATUS_ORDER.map(([k, label, color]) => ({ label, color, count: totals.by[k] || 0 }));
  const hoursMix = [{ name: "Hours", REG: +totals.reg.toFixed(1), OT: +totals.ot.toFixed(1), OT2: +totals.ot2.toFixed(1), STAT: +totals.stat.toFixed(1) }];
  const r = readiness.data;

  return (
    <div>
      <PageHeader title="Timesheets" icon={ClipboardCheck} subtitle="Generate, approve & lock payable hours — the feed into payroll" />

      {/* Toolbar */}
      <div className="card mb-4">
        <div className="flex items-end gap-3 flex-wrap">
          <label className="text-xs text-gray-500">Pay frequency
            <select className="input !py-1.5 mt-1 !w-40" value={freq} onChange={(e) => setFreq(e.target.value)}>
              {PAY_FREQUENCIES.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </select>
          </label>
          <label className="text-xs text-gray-500">Period start
            <input type="date" className="input !py-1.5 mt-1 !w-40" value={start} max={end} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="text-xs text-gray-500">Period end
            <input type="date" className="input !py-1.5 mt-1 !w-40" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          {canGenerate && <button className="btn-primary inline-flex items-center gap-1.5" onClick={() => gen.mutate()} disabled={gen.isPending}>
            <Wand2 className="w-4 h-4" />{gen.isPending ? "Generating…" : "Generate from attendance"}
          </button>}
        </div>
      </div>

      <RulesCard />

      {/* Payroll readiness — the gate (pay office only) */}
      {payOffice && r && (
        <div className="card mb-4">
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div>
              <div className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">Payroll readiness · {freq}</div>
              <div className="mt-1 text-sm text-gray-700">
                <span className="font-display text-2xl font-bold text-gray-800 tabular-nums">{r.ready}</span>
                <span className="text-gray-400"> / {r.total_employees} approved</span>
                {r.submitted > 0 && <span className="ml-3 badge-amber">{r.submitted} awaiting approval</span>}
                {r.all_ready ? <span className="ml-2 badge-green">Ready to run</span> : <span className="ml-2 badge-red">{r.missing_count} not ready</span>}
              </div>
            </div>
            <div className="w-full sm:w-64">
              <div className="h-2.5 rounded-full bg-gray-100 overflow-hidden">
                <div className="h-full rounded-full bg-primary-500 transition-all" style={{ width: `${r.coverage_pct}%` }} />
              </div>
              <div className="text-[11px] text-gray-400 mt-1 text-right">{r.coverage_pct}% coverage for {fmt(start)} – {fmt(end)}</div>
            </div>
          </div>
          {!r.all_ready && r.missing.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {r.missing.slice(0, 12).map((m) => <span key={m.employee_id} className="text-[11px] px-2 py-0.5 rounded bg-gray-100 text-gray-600">{m.name} · {m.status}</span>)}
              {r.missing.length > 12 && <span className="text-[11px] text-gray-400">+{r.missing.length - 12} more</span>}
            </div>
          )}
        </div>
      )}

      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-4">
        <StatTile label="Timesheets" value={rows.length} sub={`ending ${fmt(end)}`} accent />
        <StatTile label="Regular hours" value={`${totals.reg.toFixed(1)}h`} />
        <StatTile label="Overtime" value={`${(totals.ot + totals.ot2).toFixed(1)}h`} sub={`1.5× / 2×${totals.ot2 ? ` · ${totals.ot2.toFixed(1)}h @2×` : ""}`} />
        <StatTile label="Stat holiday" value={`${totals.stat.toFixed(1)}h`} />
        <StatTile label="Payable (est.)" value={money(totals.gross)} sub="reg + OT + stat" />
      </div>

      {/* Charts — status distribution + hours mix */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-3 text-sm">By status</h3>
          <ResponsiveContainer width="100%" height={190}>
            <BarChart data={statusData} margin={{ top: 4, right: 6, left: -24, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#8890ad" }} />
              <YAxis tick={{ fontSize: 11, fill: "#8890ad" }} allowDecimals={false} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e6f0" }} />
              <Bar dataKey="count" radius={[4, 4, 0, 0]} barSize={34}>
                {statusData.map((d) => <Cell key={d.label} fill={d.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-3 text-sm">Hours mix</h3>
          <ResponsiveContainer width="100%" height={150}>
            <BarChart data={hoursMix} layout="vertical" margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" horizontal={false} />
              <XAxis type="number" tick={{ fontSize: 11, fill: "#8890ad" }} />
              <YAxis type="category" dataKey="name" hide />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e6f0" }} />
              <Bar dataKey="REG" stackId="h" fill="#0ea5e9" barSize={44} radius={[4, 0, 0, 4]} />
              <Bar dataKey="OT" stackId="h" fill="#d97706" barSize={44} />
              <Bar dataKey="OT2" stackId="h" fill="#e11d48" barSize={44} />
              <Bar dataKey="STAT" stackId="h" fill="#3b82f6" barSize={44} radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="flex flex-wrap gap-3 mt-1 text-[11px] text-gray-500">
            <span><span className="inline-block w-2.5 h-2.5 rounded-sm bg-sky-500 mr-1 align-middle"></span>REG</span>
            <span><span className="inline-block w-2.5 h-2.5 rounded-sm bg-amber-600 mr-1 align-middle"></span>OT 1.5×</span>
            <span><span className="inline-block w-2.5 h-2.5 rounded-sm bg-rose-600 mr-1 align-middle"></span>OT2 2×</span>
            <span><span className="inline-block w-2.5 h-2.5 rounded-sm bg-violet-600 mr-1 align-middle"></span>STAT</span>
          </div>
        </div>
      </div>

      {/* List */}
      <div className="card !p-0">
        <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between gap-3 flex-wrap">
          <h3 className="font-display font-semibold text-gray-800">Timesheets ending {fmt(end)}</h3>
          <select className="input !py-1.5 !w-auto text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All statuses</option>
            <option value="draft">Draft</option>
            <option value="submitted">Submitted</option>
            <option value="approved">Approved</option>
            <option value="locked">Locked</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>
        {list.isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
          : !rows.length ? <Empty>No timesheets for this period — generate them from attendance above.</Empty>
          : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm min-w-[760px]">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr>
                    <th className="px-5 py-2.5 font-semibold">Employee</th>
                    <th className="px-3 py-2.5 font-semibold text-right">REG</th>
                    <th className="px-3 py-2.5 font-semibold text-right">OT</th>
                    <th className="px-3 py-2.5 font-semibold text-right">STAT</th>
                    <th className="px-3 py-2.5 font-semibold text-right">Total</th>
                    <th className="px-3 py-2.5 font-semibold">Status</th>
                    <th className="px-4 py-2.5 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((t) => (
                    <tr key={t.id} className="border-t border-surface-border hover:bg-surface/50 cursor-pointer" onClick={() => setOpenId(t.id)}>
                      <td className="px-5 py-2.5">
                        <div className="font-medium text-gray-800">{t.employee_name}</div>
                        <div className="text-[11px] text-gray-400">{t.employee_number || "—"} · {t.pay_type} · {t.province}</div>
                      </td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-gray-700">{t.reg_hours.toFixed(1)}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-amber-600">{t.ot_hours ? t.ot_hours.toFixed(1) : "—"}{(t.ot2_hours || 0) > 0 && <span className="text-red-600 text-[10px] ml-1">+{(t.ot2_hours || 0).toFixed(1)}@2×</span>}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-primary-600">{t.stat_pay ? money(t.stat_pay) : t.stat_hours ? t.stat_hours.toFixed(1) : "—"}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums font-semibold text-gray-800">{t.total_hours.toFixed(1)}</td>
                      <td className="px-3 py-2.5"><Badge tone={TS_TONE[t.status]}>{t.status}</Badge></td>
                      <td className="px-4 py-2.5 text-right whitespace-nowrap" onClick={(e) => e.stopPropagation()}>
                        {t.status === "draft" && canSubmit(t) && <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1" onClick={() => act.mutate({ id: t.id, op: "submit" })}><Send className="w-3.5 h-3.5" />Submit</button>}
                        {t.status === "submitted" && canReview(t) && (
                          <span className="inline-flex gap-1.5">
                            <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 text-green-700" onClick={() => act.mutate({ id: t.id, op: "approve" })}><CheckCircle2 className="w-3.5 h-3.5" />Approve</button>
                            <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 text-red-600" onClick={() => act.mutate({ id: t.id, op: "reject" })}><XCircle className="w-3.5 h-3.5" />Reject</button>
                          </span>
                        )}
                        {t.status === "approved" && canReview(t) && <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1" onClick={() => act.mutate({ id: t.id, op: "lock" })}><Lock className="w-3.5 h-3.5" />Lock</button>}
                        {t.status === "locked" && <span className="text-[11px] text-gray-400 inline-flex items-center gap-1"><Lock className="w-3 h-3" />Fed to payroll</span>}
                        {t.status === "rejected" && canSubmit(t) && <button className="btn-secondary !py-1 !px-2.5 text-xs" onClick={() => act.mutate({ id: t.id, op: "submit" })}>Resubmit</button>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </div>

      <p className="text-[11px] text-gray-400 mt-3">Approved &amp; locked timesheets feed the pay run as REG / OT (1.5×) / OT2 (2×) / STAT earnings. Overtime &amp; stat pay use the provincial employment-standards rules — figures are design references pending legal verification.</p>

      {openId && <TimesheetDrawer id={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}

function TimesheetDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: t, isLoading } = useQuery({ queryKey: ["ta", "timesheet", id], queryFn: () => timeApi.timesheet(id) });
  const earnings = t ? feedEarnings(t) : [];
  const gross = earnings.reduce((a, e) => a + e.amount, 0);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={onClose}>
      <div className="w-full sm:max-w-3xl h-full bg-surface-card shadow-2xl overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b border-surface-border flex items-center justify-between sticky top-0 bg-surface-card">
          <div>
            <h3 className="font-display font-bold text-gray-800">{t?.employee_name || "Timesheet"}</h3>
            {t && <div className="text-xs text-gray-400">{fmt(t.period_start)} – {fmt(t.period_end)} · {t.pay_frequency}</div>}
          </div>
          <button className="btn-secondary !p-2" onClick={onClose} aria-label="Close"><X className="w-4 h-4" /></button>
        </div>
        {isLoading || !t ? <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div> : (
          <div className="p-5 space-y-5">
            <div className="grid grid-cols-3 gap-2 text-center">
              <div className="rounded-lg bg-surface p-3"><div className="text-[10px] uppercase text-gray-400">Regular</div><div className="font-display text-lg font-bold text-gray-800 tabular-nums">{t.reg_hours.toFixed(1)}h</div></div>
              <div className="rounded-lg bg-surface p-3"><div className="text-[10px] uppercase text-gray-400">Overtime</div><div className="font-display text-lg font-bold text-amber-600 tabular-nums">{t.ot_hours.toFixed(1)}h</div>{(t.ot2_hours || 0) > 0 && <div className="text-[10px] text-red-600">+{(t.ot2_hours || 0).toFixed(1)}h @ 2×</div>}</div>
              <div className="rounded-lg bg-surface p-3"><div className="text-[10px] uppercase text-gray-400">Stat pay</div><div className="font-display text-lg font-bold text-primary-600 tabular-nums">{t.stat_pay ? money(t.stat_pay) : `${t.stat_hours.toFixed(1)}h`}</div></div>
            </div>

            <div>
              <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Daily lines</h4>
              <div className="rounded-lg border border-surface-border overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className="px-3 py-2 text-left font-semibold">Date</th><th className="px-2 py-2 text-right font-semibold">REG</th><th className="px-2 py-2 text-right font-semibold">OT</th><th className="px-2 py-2 text-right font-semibold">2×</th><th className="px-3 py-2 text-right font-semibold">STAT</th></tr></thead>
                  <tbody>
                    {(t.lines || []).map((l) => (
                      <tr key={l.id} className="border-t border-surface-border">
                        <td className="px-3 py-1.5 text-gray-600">{fmt(l.work_date)}{l.note ? <span className="text-[10px] text-gray-400 ml-1">{l.note}</span> : ""}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{l.reg_hours ? l.reg_hours.toFixed(1) : "—"}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-amber-600">{l.ot_hours ? l.ot_hours.toFixed(1) : "—"}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums text-red-600">{(l.ot2_hours || 0) ? (l.ot2_hours || 0).toFixed(1) : "—"}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-primary-600">{l.stat_hours ? l.stat_hours.toFixed(1) : "—"}</td>
                      </tr>
                    ))}
                    {!(t.lines || []).length && <tr><td colSpan={5} className="px-3 py-4 text-center text-gray-400 text-xs">No daily lines.</td></tr>}
                  </tbody>
                </table>
              </div>
            </div>

            {/* The feed preview — the whole point */}
            <div>
              <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">Becomes pay-run entries <ArrowRight className="w-3.5 h-3.5" /></h4>
              <div className="rounded-lg border border-primary-200 bg-primary-50/50 p-3">
                {earnings.length ? (
                  <>
                    {earnings.map((e) => (
                      <div key={e.code} className="flex items-center justify-between py-1 text-sm">
                        <span className="font-mono text-xs px-1.5 py-0.5 rounded bg-white text-primary-700 border border-primary-200">{e.code}</span>
                        <span className="text-gray-500 tabular-nums">{e.code === "STAT" ? `avg daily wage · ${e.hours.toFixed(1)}h equiv` : `${e.hours.toFixed(1)}h × ${money(t.hourly_rate)}${e.mult ? " " + e.mult : ""}`}</span>
                        <span className="font-semibold text-gray-800 tabular-nums">{money(e.amount)}</span>
                      </div>
                    ))}
                    <div className="flex items-center justify-between pt-2 mt-1 border-t border-primary-200 text-sm font-semibold">
                      <span className="text-gray-600">Earnings this period</span><span className="text-gray-900 tabular-nums">{money(gross)}</span>
                    </div>
                  </>
                ) : <div className="text-xs text-gray-500">{t.pay_type === "salary" ? "Salaried — base salary covers regular hours; OT/STAT would add here." : "No payable hours yet."}</div>}
              </div>
              <p className="text-[10px] text-gray-400 mt-1.5">{t.pay_type === "hourly" ? "Hourly REG replaces payroll's synthetic base — actual worked hours become pay." : "Salary base stands; only OT/STAT feed as extra entries."}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const STAT_METHOD_LABEL: Record<string, string> = {
  four_week_div20: "4-week regular wages ÷ 20",
  thirty_day_avg: "30-day wages ÷ days worked",
  avg_daily_worked: "lookback wages ÷ days worked",
};
const ELIG_LABEL: Record<string, string> = {
  last_and_first: "worked scheduled shift before & after",
  "30day_15worked": "worked 15 of last 30 days",
  none: "no qualifying test",
};

/** Collapsible reference of the effective provincial ESA rules (design refs). */
function RulesCard() {
  const [open, setOpen] = useState(false);
  const [prov, setProv] = useState("ON");
  const { data: r } = useQuery({ queryKey: ["ta", "rules", prov], queryFn: () => timeApi.rules(prov), enabled: open });

  return (
    <div className="card mb-4 !p-0 overflow-hidden">
      <button className="w-full flex items-center justify-between px-5 py-3 text-left" onClick={() => setOpen((o) => !o)}>
        <span className="inline-flex items-center gap-2 font-display font-semibold text-gray-800"><Scale className="w-4 h-4 text-primary-500" />Provincial ESA rules{r ? ` · ${r.province_label}` : ""}</span>
        <ChevronDown className={`w-4 h-4 text-gray-400 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="px-5 pb-5 border-t border-surface-border pt-4">
          <div className="flex flex-wrap items-center gap-2 mb-3">
            {RULE_PROVINCES.map((p) => (
              <button key={p} onClick={() => setProv(p)}
                className={`px-2.5 py-1 rounded-md text-xs font-semibold ${prov === p ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>{p}</button>
            ))}
          </div>
          {!r ? <div className="grid place-items-center py-8"><Spinner className="h-6 w-6" /></div> : (
            <div className="grid sm:grid-cols-3 gap-3">
              <div className="rounded-lg bg-surface p-3">
                <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">Overtime</div>
                <div className="text-sm text-gray-800 font-medium">{r.overtime.summary}</div>
                <div className="text-[11px] text-gray-400 mt-1">{r.overtime.banked_allowed ? "Banked time allowed" : "No banked time"}</div>
              </div>
              <div className="rounded-lg bg-surface p-3">
                <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">Stat holiday pay</div>
                <div className="text-sm text-gray-800 font-medium">{STAT_METHOD_LABEL[r.stat_holiday.avg_wage_method] || r.stat_holiday.avg_wage_method}</div>
                <div className="text-[11px] text-gray-400 mt-1">Eligible: {ELIG_LABEL[r.stat_holiday.eligibility] || r.stat_holiday.eligibility} · worked {r.stat_holiday.worked_premium}×</div>
              </div>
              <div className="rounded-lg bg-surface p-3">
                <div className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">Rest & breaks</div>
                <div className="text-sm text-gray-800 font-medium">{r.breaks.meal_minutes}-min meal after {r.breaks.meal_after_hours}h</div>
                <div className="text-[11px] text-gray-400 mt-1">{r.breaks.daily_rest_hours}h daily · {r.breaks.weekly_rest_hours}h weekly rest</div>
              </div>
            </div>
          )}
          {r && <p className="text-[11px] text-amber-600 mt-3">⚠︎ {r.disclaimer}</p>}
        </div>
      )}
    </div>
  );
}
