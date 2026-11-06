import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Radar, Users, Wallet, UserPlus, CheckSquare, Wallet2, Activity, AlertTriangle,
  CheckCircle2, ArrowRight, Gauge,
} from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import { employeeApi, payrollApi, aiApi, money } from "../api/endpoints";
import { useAuthStore, ADMIN_ROLES, HR_ROLES } from "../store/authStore";
import { useCan } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

// Pay figures (monthly cost, pay runs, last run) are the pay office's: org admin, HR and payroll.
// A manager opens this page too, but sees no pay (decision 2026-10-21) and no 403 tile.
const PAY_OFFICE = [...ADMIN_ROLES, ...HR_ROLES, "payroll_admin"];

export default function CommandCenterPage() {
  const org = useAuthStore((s) => s.user?.org_name);
  const payOffice = useCan("data.pay", PAY_OFFICE);
  const admin = useCan("ai.admin", ADMIN_ROLES);
  const emps = useQuery({ queryKey: ["employees"], queryFn: employeeApi.list });
  const runs = useQuery({ queryKey: ["runs"], queryFn: payrollApi.runs, enabled: payOffice });
  const brief = useQuery({ queryKey: ["ai-command"], queryFn: aiApi.commandCenter });
  const acts = useQuery({ queryKey: ["ai-activity"], queryFn: () => aiApi.activity(8) });

  if (emps.isLoading || brief.isLoading || !emps.data || !brief.data)
    return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;

  const list = emps.data;
  const onboarding = list.filter((e) => e.status === "onboarding").length;
  const monthly = list.reduce((s, e) => s + (e.annual_salary || 0), 0) / 12;
  const score = brief.data.attention_score;
  const clear = score === 0;
  const attention = brief.data.layers.filter((l) => l.status === "attention");
  const lastRun = runs.data?.[0];

  return (
    <div>
      <PageHeader title="Command Center" icon={Radar}
        subtitle={`${org || "Organization"} · live operational control room`} />

      {/* Pulse hero */}
      <div className="rounded-2xl p-5 mb-4 text-white relative overflow-hidden"
        style={{ background: "linear-gradient(120deg,#241657 0%,#48328a 45%,#1e5e52 100%)" }}>
        <div className="flex items-center gap-5">
          <div className="w-20 h-20 rounded-2xl bg-white/10 backdrop-blur flex flex-col items-center justify-center shrink-0">
            <span className="text-2xl font-bold leading-none">{clear ? "OK" : score}</span>
            <span className="text-[9px] uppercase tracking-wide mt-1 text-white/70">pulse</span>
          </div>
          <div>
            <div className="flex items-center gap-2">
              {clear ? <CheckCircle2 className="w-5 h-5 text-emerald-300" /> : <AlertTriangle className="w-5 h-5 text-amber-300" />}
              <h2 className="font-display text-xl font-bold">{brief.data.headline}</h2>
            </div>
            <p className="text-sm text-white/70 mt-1">{brief.data.agents_online} agents online · {brief.data.runs_today} checks today · {list.length} employees</p>
          </div>
          {admin && <Link to="/ai/command-center" className="ml-auto hidden sm:flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/15 hover:bg-white/25 text-sm font-semibold transition-colors">
            <Gauge className="w-4 h-4" /> AI briefing
          </Link>}
        </div>
      </div>

      {/* Live tiles */}
      <div className={`grid grid-cols-2 ${payOffice ? "lg:grid-cols-5" : "lg:grid-cols-3"} gap-4 mb-4`}>
        <Tile icon={Users} label={payOffice ? "Active headcount" : "My team"} value={list.length} to="/employees" />
        {payOffice && <Tile icon={Wallet} label="Monthly cost" value={money(monthly)} to="/payroll" />}
        <Tile icon={UserPlus} label="Onboarding" value={onboarding} tone={onboarding ? "amber" : undefined} to="/onboarding" />
        <Tile icon={CheckSquare} label="Approvals" value={2} tone="amber" to="/approvals" />
        {payOffice && <Tile icon={Wallet2} label="Pay runs" value={runs.data?.length ?? 0} to="/payroll" />}
      </div>

      <div className="grid lg:grid-cols-3 gap-4">
        {/* Needs action */}
        <div className="card lg:col-span-2">
          <h3 className="font-display font-semibold text-gray-800 mb-3">Needs action</h3>
          {attention.length === 0 ? (
            <div className="flex items-center gap-2 text-sm text-emerald-600 py-6"><CheckCircle2 className="w-5 h-5" /> Nothing needs action right now.</div>
          ) : (
            <div className="space-y-2">
              {attention.map((l) => (
                <div key={l.domain} className="flex items-center justify-between gap-3 rounded-lg p-3 bg-amber-50">
                  <div className="flex items-start gap-2.5">
                    <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                    <div>
                      <div className="text-sm font-semibold text-gray-800">{l.domain} · {l.count} to review</div>
                      <div className="text-xs text-gray-500">{l.detail}</div>
                    </div>
                  </div>
                  <ArrowRight className="w-4 h-4 text-amber-500 shrink-0" />
                </div>
              ))}
            </div>
          )}

          {/* Payroll status strip (pay office only) */}
          {payOffice && <div className="mt-4 rounded-lg border border-surface-border p-3 flex items-center justify-between">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Payroll</div>
              <div className="text-sm text-gray-700">{lastRun ? `Last run ${lastRun.pay_date} · ${lastRun.employee_count} paid · net ${money(lastRun.total_net)}` : "No pay runs yet"}</div>
            </div>
            <Link to="/payroll" className="btn-primary text-xs flex items-center gap-1.5">Run payroll <ArrowRight className="w-3.5 h-3.5" /></Link>
          </div>}
        </div>

        {/* Live activity */}
        <div className="card">
          <div className="flex items-center gap-2 mb-3">
            <Activity className="w-4 h-4 text-primary-500" />
            <h3 className="font-display font-semibold text-gray-800">Live activity</h3>
          </div>
          <div className="space-y-2.5">
            {(acts.data?.items || []).slice(0, 7).map((a) => (
              <div key={a.id} className="flex items-start gap-2.5">
                <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${a.status === "attention" ? "bg-amber-500" : "bg-emerald-500"}`} />
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-gray-700 truncate">{a.agent}</div>
                  <div className="text-[11px] text-gray-400 truncate">{a.summary}</div>
                </div>
              </div>
            ))}
            {!acts.data?.items?.length && <div className="text-sm text-gray-400 py-4">No recent activity.</div>}
          </div>
          <Link to="/ai/activity" className="text-xs font-semibold text-primary-600 mt-3 inline-block">View all activity →</Link>
        </div>
      </div>
    </div>
  );
}

function Tile({ icon: Icon, label, value, tone, to }: { icon: any; label: string; value: any; tone?: string; to: string }) {
  return (
    <Link to={to} className="card hover:shadow-card-hover transition-shadow block">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">{label}</span>
        <Icon className={`w-4 h-4 ${tone === "amber" ? "text-amber-500" : "text-gray-300"}`} />
      </div>
      <div className={`text-2xl font-bold mt-1.5 tabular-nums ${tone === "amber" ? "text-amber-600" : "text-gray-800"}`}>{value}</div>
    </Link>
  );
}
