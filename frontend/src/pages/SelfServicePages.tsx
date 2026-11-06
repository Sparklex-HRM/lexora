import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import {
  UserCircle, Wallet, CalendarRange, HeartPulse, Inbox, FileText, Send, CalendarDays, Clock,
} from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { useAuthStore } from "../store/authStore";
import { employeeApi, payrollApi, mastersApi, money, freqLabel } from "../api/endpoints";
import PayslipModal from "../components/PayslipModal";
import { MASK } from "../api/ess";
import { SalaryToggle, useSalaryVisible } from "../components/SalaryToggle";
import { REGION_NAMES } from "../api/regions";
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

function useMe() {
  const user = useAuthStore((s) => s.user);
  const { data: emps, isLoading } = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const email = (user?.email || "").toLowerCase();
  const me: any = ((emps || []) as any[]).find((e) =>
    (e.email || "").toLowerCase() === email || (e.work_email || "").toLowerCase() === email);
  return { me, isLoading, user };
}

function NotLinked({ user }: { user: any }) {
  return (
    <div className="card max-w-xl">
      <Empty>We couldn't match <b>{user?.email}</b> to an employee record. Ask HR to link your account, or set your work email on your employee profile.</Empty>
    </div>
  );
}

// ── My Profile ──────────────────────────────────────────────────────────────
export default function MyProfilePage() {
  const { me, isLoading, user } = useMe();
  if (isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  return (
    <div>
      <PageHeader title="My Profile" icon={UserCircle} subtitle="Your details on record" />
      {!me ? <NotLinked user={user} /> : (
        <div className="grid lg:grid-cols-3 gap-4">
          <div className="card text-center">
            <div className="w-20 h-20 rounded-full bg-gradient-to-br from-primary-500 to-teal-500 grid place-items-center text-white text-2xl font-bold mx-auto mb-3">
              {((me.first_name?.[0] || "") + (me.last_name?.[0] || "")).toUpperCase()}
            </div>
            <div className="font-display font-semibold text-gray-800 text-lg">{me.first_name} {me.last_name}</div>
            <div className="text-sm text-gray-500">{me.job_title || "—"}</div>
            <div className="text-xs text-gray-400 mt-1">{me.employee_number}</div>
            <span className="badge-green mt-3 inline-block">{me.status || "active"}</span>
          </div>
          <div className="card lg:col-span-2">
            <div className="grid sm:grid-cols-2 gap-x-6 gap-y-3">
              {[["Department", me.department], ["Work location", me.work_location], ["Reports to", me.manager_name],
                ["Email", me.email], ["Work email", me.work_email], ["Phone", me.phone],
                ["Employment type", me.employment_type], ["Hire date", me.hire_date],
                ["Province of employment", REGION_NAMES.CA[me.province_of_employment] || me.province_of_employment], ["Pay frequency", me.pay_frequency && freqLabel(me.pay_frequency)],
              ].map(([label, val]) => (
                <div key={label as string}>
                  <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{label}</div>
                  <div className="text-sm text-gray-800">{(val as string) || "—"}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── My Pay ──────────────────────────────────────────────────────────────────
// 2026-10-16: amounts masked until "Show salary" (sessionStorage, this session only).
export function MyPayPage() {
  const { me, isLoading, user } = useMe();
  const [stubId, setStubId] = useState<string | null>(null);
  const [shown] = useSalaryVisible();
  const { data: slips } = useQuery({ queryKey: ["payslips", me?.id], queryFn: () => payrollApi.payslips(me!.id), enabled: !!me });
  // The T4 box-14 figure for the current calendar year.
  const year = Number(todayISO().slice(0, 4));
  const { data: t4 } = useQuery({ queryKey: ["t4", year], queryFn: () => payrollApi.t4(year), enabled: !!me });
  const myT4 = (t4?.slips || []).find((s: any) => s.employee_id === me?.id);
  const m = (v: number) => shown ? money(v) : MASK;

  if (isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  return (
    <div>
      <PageHeader title="My Pay" icon={Wallet} subtitle="Your pay stubs and tax slips"
        actions={<SalaryToggle />} />
      {!me ? <NotLinked user={user} /> : (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
            <StatTile label="Payslips" value={slips?.length || 0} icon={FileText} />
            <StatTile label="Last net pay" value={slips?.length ? m(slips[0].net_pay) : "—"} accent />
            <StatTile label={`${year} T4 income`} value={myT4 ? m(myT4.boxes.box14_employment_income) : "—"} sub="box 14 YTD" />
          </div>
          <div className="card !p-0">
            <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between gap-2"><h3 className="font-display font-semibold text-gray-800">Payslips</h3>
              {!shown && <span className="text-[11px] text-gray-400">Choose Show salary to open a payslip</span>}</div>
            {!slips || !slips.length ? <Empty>No payslips yet.</Empty> : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                    <tr><th className="px-5 py-2.5 font-semibold">Pay date</th><th className="px-4 py-2.5 font-semibold">Frequency</th><th className="px-4 py-2.5 font-semibold text-right">Gross</th><th className="px-4 py-2.5 font-semibold text-right">Net</th><th /></tr>
                  </thead>
                  <tbody>
                    {slips.map((p: any) => (
                      <tr key={p.stub_id} onClick={() => shown ? setStubId(p.stub_id) : toast("Choose Show salary first — it stays on for this session only")}
                        className="border-t border-surface-border hover:bg-gray-50 cursor-pointer">
                        <td className="px-5 py-3 font-medium text-gray-800">{p.pay_date}</td>
                        <td className="px-4 py-3"><span className="badge-teal">{freqLabel(p.pay_frequency)}</span></td>
                        <td className="px-4 py-3 text-right tabular-nums text-gray-700">{m(p.gross)}</td>
                        <td className="px-4 py-3 text-right tabular-nums font-semibold text-gray-800">{m(p.net_pay)}</td>
                        <td className="px-3 text-primary-500 text-xs">{shown ? "view →" : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
      {stubId && <PayslipModal stubId={stubId} onClose={() => setStubId(null)} />}
    </div>
  );
}

// ── My Benefits ─────────────────────────────────────────────────────────────
export function MyBenefitsPage() {
  const { data: plans, isLoading } = useQuery({ queryKey: ["m", "benefit-plans"], queryFn: () => mastersApi.list("benefit-plans") });
  const active = (plans || []).filter((p: any) => p.is_active !== false);
  return (
    <div>
      <PageHeader title="My Benefits" icon={HeartPulse} subtitle="Your coverage and enrollment options" />
      {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : !active.length ? <div className="card"><Empty>No benefit plans available yet.</Empty></div>
        : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {active.map((p: any) => (
              <div key={p.id} className="card">
                <div className="font-display font-semibold text-gray-800">{p.name}</div>
                <div className="text-xs text-gray-400">{p.carrier || "—"}</div>
                <div className="mt-3 text-sm text-gray-600">Your cost <b className="tabular-nums">{money(+p.ee_cost || 0)}</b> / pay</div>
                <div className="text-xs text-gray-400 tabular-nums">Employer contributes {money(+p.er_cost || 0)}</div>
              </div>
            ))}
          </div>
        )}
      <p className="text-[11px] text-gray-400 mt-3">To change coverage, use <Link to="/enrollment" className="text-primary-600">Benefits Enrollment</Link> during an open-enrollment window or after a qualifying life event.</p>
    </div>
  );
}

// ── My Time ─────────────────────────────────────────────────────────────────
export function MyTimePage() {
  return (
    <div>
      <PageHeader title="My Time" icon={CalendarRange} subtitle="Your time, schedule and time-off" />
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <Link to="/attendance" className="card hover:shadow-md transition-shadow">
          <div className="flex items-center gap-2.5 mb-1"><Clock className="w-5 h-5 text-primary-600" /><span className="font-display font-semibold text-gray-800">Attendance</span></div>
          <p className="text-xs text-gray-500">Your daily attendance and worked hours.</p>
        </Link>
        <Link to="/leave" className="card hover:shadow-md transition-shadow">
          <div className="flex items-center gap-2.5 mb-1"><CalendarDays className="w-5 h-5 text-primary-600" /><span className="font-display font-semibold text-gray-800">Leave & time-off</span></div>
          <p className="text-xs text-gray-500">Request vacation, sick and personal days.</p>
        </Link>
        <Link to="/my-requests" className="card hover:shadow-md transition-shadow">
          <div className="flex items-center gap-2.5 mb-1"><Inbox className="w-5 h-5 text-primary-600" /><span className="font-display font-semibold text-gray-800">My requests</span></div>
          <p className="text-xs text-gray-500">Track your submitted requests.</p>
        </Link>
      </div>
    </div>
  );
}

// ── My Requests ─────────────────────────────────────────────────────────────
interface Req { id: number; type: string; detail: string; date: string; status: string; }
export function MyRequestsPage() {
  const user = useAuthStore((s) => s.user);
  const key = `lx-requests-${user?.email || "me"}`;
  const [reqs, setReqs] = useState<Req[]>(() => { try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch { return []; } });
  const [type, setType] = useState("Time off");
  const [detail, setDetail] = useState("");
  const persist = (next: Req[]) => { setReqs(next); try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* ignore */ } };
  const submit = () => {
    if (!detail.trim()) return toast.error("Add a few details");
    persist([{ id: Date.now(), type, detail, date: todayISO(), status: "submitted" }, ...reqs]);
    setDetail(""); toast.success("Request submitted");
  };
  return (
    <div>
      <PageHeader title="My Requests" icon={Inbox} subtitle="Time-off and change requests you've submitted" />
      <div className="grid lg:grid-cols-3 gap-4">
        <div className="card h-fit">
          <h3 className="font-display font-semibold text-gray-800 mb-3">New request</h3>
          <label className="block text-xs font-semibold text-gray-500 mb-1">Type</label>
          <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
            {["Time off", "Schedule change", "Profile update", "Benefits change", "Other"].map((t) => <option key={t}>{t}</option>)}
          </select>
          <label className="block text-xs font-semibold text-gray-500 mb-1 mt-3">Details</label>
          <textarea className="input min-h-[90px]" value={detail} onChange={(e) => setDetail(e.target.value)} placeholder="What do you need?" />
          <button className="btn-primary w-full mt-3 flex items-center justify-center gap-1.5" onClick={submit}><Send className="w-3.5 h-3.5" /> Submit</button>
        </div>
        <div className="card lg:col-span-2 !p-0">
          <div className="px-5 py-3 border-b border-surface-border"><h3 className="font-display font-semibold text-gray-800">Submitted</h3></div>
          {!reqs.length ? <Empty>No requests yet — submit one on the left.</Empty> : (
            <div className="divide-y divide-surface-border">
              {reqs.map((r) => (
                <div key={r.id} className="px-5 py-3 flex items-start justify-between gap-3">
                  <div><div className="text-sm font-medium text-gray-800">{r.type}</div><div className="text-xs text-gray-500">{r.detail}</div><div className="text-[10px] text-gray-400 mt-0.5">{r.date}</div></div>
                  <span className="badge-amber shrink-0">{r.status}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
