import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { exitApi } from "../api/exit";
import { UserMinus, Wallet, FileText, CheckCircle2, ArrowRight, Palmtree, AlertTriangle } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader, StatTile, Spinner, Empty } from "../components/ui";
import { employeeApi, payrollApi, money } from "../api/endpoints";
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

// The gateway returns the compliance gate as {detail: {error, message, reasons, hint}} —
// never show "[object Object]" to the person; say what is blocking and how to proceed.
const errorText = (e: any, fallback: string): string => {
  const d = e?.response?.data?.detail;
  if (!d) return fallback;
  if (typeof d === "string") return d;
  return [d.message, ...(d.reasons || [])].filter(Boolean).join(" ") || fallback;
};
const isGateError = (e: any) => /compliance_gate_open/.test(String(e?.response?.data?.detail?.error || ""));

const today = () => todayISO();
const fmt = (d?: string | null) => d ? new Date(d + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }) : "—";
const REASONS = [
  { code: "A", label: "Shortage of work / end of contract" }, { code: "E", label: "Quit / resignation" },
  { code: "D", label: "Illness or injury" }, { code: "M", label: "Dismissal" },
  { code: "N", label: "Leave of absence" }, { code: "F", label: "Maternity / parental" },
  { code: "G", label: "Retirement" }, { code: "K", label: "Other" },
];

export default function OffboardingPage() {
  const qc = useQueryClient();
  const { data: emps } = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const [empId, setEmpId] = useState("");
  const [reason, setReason] = useState("A");
  const [lastDay, setLastDay] = useState(today());
  const [result, setResult] = useState<any>(null);
  // Opened from an exit case (?case=…): the employee, the REAL last working day, the ROE
  // reason code and the clearance recoveries come from the case.
  const [sp] = useSearchParams();
  const caseId = sp.get("case") || "";
  const fromCase = useQuery({ queryKey: ["exit-settlement", caseId], queryFn: () => exitApi.settlement(caseId), enabled: !!caseId });
  const pre = fromCase.data?.prefill?.body;
  useEffect(() => {
    if (!pre) return;
    setEmpId(pre.employee_id);
    if (pre.last_day) setLastDay(pre.last_day);
    if (pre.reason) setReason(pre.reason);
  }, [pre?.employee_id, pre?.last_day, pre?.reason]);   // eslint-disable-line react-hooks/exhaustive-deps

  const active = (emps || []).filter((e) => e.status !== "terminated");
  const emp = (emps || []).find((e) => e.id === empId);
  const vac = useQuery({ queryKey: ["vac-owed", empId], queryFn: () => payrollApi.vacationOwed(empId), enabled: !!empId });
  const comp = useQuery({ queryKey: ["payroll-compliance"], queryFn: () => payrollApi.compliance() });
  // Gated by the CRA verification gate (same rule as Run Payroll).
  const activeGate = comp.data?.gate;
  const gateOpen = !!activeGate?.gate_open;

  const confirmTest = (reasons: string[]) =>
    window.confirm(`⚠ Payroll is NOT verified for real pay.\n\n• ${reasons.join("\n• ")}\n\nThe final settlement will be recorded as a TEST run — NOT for remittance or filing. Verify against CRA (Year-end → Compliance) before a real cheque.\n\nProceed as a test run?`);

  const doRun = () => {
    let ack = false;
    if (gateOpen) {
      if (!confirmTest(activeGate?.reasons || [])) return;
      ack = true;
    }
    run.mutate(ack);
  };

  const run = useMutation({
    mutationFn: (ack: boolean) => payrollApi.finalPay({ employee_id: empId, last_day: lastDay, reason, acknowledge_unverified: ack,
      ...(caseId && pre ? { case_id: caseId, extra_deductions: pre.extra_deductions || [] } : {}) }),
    onSuccess: (r) => { setResult(r); toast.success("Final pay run & ROE generated"); qc.invalidateQueries({ queryKey: ["vac-owed"] }); qc.invalidateQueries({ queryKey: ["ytd"] }); qc.invalidateQueries({ queryKey: ["exit-case"] }); qc.invalidateQueries({ queryKey: ["exit-cases"] }); },
    onError: (e: any, ack) => {
      // The server gate can be open even when the compliance read was stale: offer the TEST-mode run instead of a dead end.
      if (!ack && isGateError(e)) {
        const d = e.response.data.detail;
        if (confirmTest(d.reasons || [d.message])) { run.mutate(true); return; }
        toast.error(d.message || "Final pay blocked until payroll is verified");
        return;
      }
      toast.error(errorText(e, "Final pay failed"));
    },
  });
  const terminate = useMutation({
    mutationFn: () => employeeApi.update(empId, { status: "terminated", termination_date: lastDay }),
    onSuccess: () => { toast.success("Employee marked terminated"); qc.invalidateQueries({ queryKey: ["employees"] }); },
    onError: () => toast.error("Could not update status"),
  });

  const reset = () => { setResult(null); setEmpId(""); };

  return (
    <div>
      <PageHeader title="Offboarding" icon={UserMinus} subtitle="Final pay + Record of Employment — the lifecycle end, in one step" />
      {caseId && fromCase.data && (
        <div className="card mb-3 text-sm text-gray-700">
          Settling exit case <Link className="text-primary-600 font-semibold" to={`/exits/${caseId}`}>{fromCase.data.case.case_number}</Link> · {fromCase.data.case.employee_name}.
          The last working day and the reason come from the case.{" "}
          {fromCase.data.pay_status?.note && <span className="text-gray-500">{fromCase.data.pay_status.note}</span>}
          {(fromCase.data.prefill?.clearance_recovery_total || 0) > 0 && <span className="block text-amber-700 text-xs mt-1">Clearance recoveries of {money(fromCase.data.prefill.clearance_recovery_total)} are deducted from the final cheque.</span>}
          {(fromCase.data.prefill?.manual || []).map((m: string, i: number) => <span key={i} className="block text-amber-700 text-xs mt-1">{m}</span>)}
          {!fromCase.data.ready && <span className="block text-red-600 text-xs mt-1">{fromCase.data.blocked_reason}</span>}
        </div>)}
      {gateOpen && (
        <div className="mb-3 rounded-xl border-2 border-red-300 bg-red-50 px-4 py-3 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-600 shrink-0 mt-0.5" />
          <div className="text-xs text-red-700 min-w-0">
            <div className="font-semibold text-red-800 text-sm">Payroll is NOT verified for real pay — a final settlement from here is recorded as <span className="uppercase">test</span></div>
            {(activeGate?.reasons || []).map((r: string, i: number) => <div key={i} className="mt-0.5">• {r}</div>)}
            <div className="mt-1.5 text-[11px] text-red-600">
              <>{activeGate?.constants_verified ?? 0}/{activeGate?.constants_total ?? 0} constants verified. <Link to="/payroll?tab=yearend&ye=compliance" className="underline font-medium">Open Compliance →</Link></>
            </div>
          </div>
        </div>
      )}
      {!caseId && <div className="card mb-3 text-xs text-gray-500">Tip: start from <Link className="text-primary-600 font-semibold" to="/exits">Exits</Link> so notice, clearance and the real last working day are tracked. This screen on its own only pays the final cheque.</div>}

      {!result ? (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
          {/* pick + form */}
          <div className="lg:col-span-3 card">
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">Employee to offboard</label>
            <select className="input" value={empId} onChange={(e) => { setEmpId(e.target.value); setResult(null); }}>
              <option value="">Select an employee…</option>
              {active.map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name} · {e.employee_number}{e.branch_name ? ` · ${e.branch_name}` : ""}</option>)}
            </select>

            {emp && (
              <div className="mt-4 space-y-4">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                  <Field label="Department" value={emp.department || "—"} />
                  <Field label="Province" value={emp.province_of_employment} />
                  <Field label="Hired" value={fmt(emp.hire_date)} />
                  <Field label="Pay" value={emp.pay_type === "hourly" ? `${money(emp.hourly_rate)}/h` : money(emp.annual_salary)} />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block text-xs font-semibold text-gray-500">Reason (ROE box 16)
                    <select className="input mt-1" value={reason} onChange={(e) => setReason(e.target.value)}>
                      {REASONS.map((r) => <option key={r.code} value={r.code}>{r.code} — {r.label}</option>)}
                    </select>
                  </label>
                  <label className="block text-xs font-semibold text-gray-500">Last day worked
                    <input type="date" className="input mt-1" value={lastDay} onChange={(e) => setLastDay(e.target.value)} />
                  </label>
                </div>
                <div className="flex items-center justify-between pt-1">
                  <div className="text-xs text-gray-500">This runs a final cheque (paying out vacation owed) and generates the ROE.</div>
                  <button className="btn-primary inline-flex items-center gap-1.5" onClick={doRun} disabled={run.isPending}>
                    {run.isPending ? "Processing…" : <>Run final pay &amp; issue ROE <ArrowRight className="w-4 h-4" /></>}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* vacation owed preview */}
          <div className="lg:col-span-2 card">
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Vacation owed</label>
            {!empId ? <Empty>Pick an employee to see their outstanding vacation.</Empty>
              : vac.isLoading ? <div className="grid place-items-center py-8"><Spinner className="h-6 w-6" /></div>
              : (
                <div>
                  <div className="flex items-baseline gap-2">
                    <Palmtree className="w-5 h-5 text-primary-500" />
                    <span className="font-display text-3xl font-bold text-primary-600 tabular-nums">{money(vac.data?.balance || 0)}</span>
                  </div>
                  <div className="text-xs text-gray-500 mt-1">accrued {money(vac.data?.accrued || 0)} · paid {money(vac.data?.paid || 0)}</div>
                  <p className="text-[11px] text-gray-400 mt-3">ESA vacation pay owed on separation. It's paid out in the final cheque and drawn to zero.</p>
                </div>
              )}
          </div>
        </div>
      ) : (
        <ResultView result={result} onTerminate={() => terminate.mutate()} terminated={emp?.status === "terminated"} onReset={reset} />
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: any }) {
  return <div><div className="text-[10px] uppercase text-gray-400">{label}</div><div className="font-medium text-gray-800">{value}</div></div>;
}

function ResultView({ result, onTerminate, terminated, onReset }: { result: any; onTerminate: () => void; terminated: boolean; onReset: () => void }) {
  const fp = result.final_pay, roe = result.roe || {};
  return (
    <div>
      <div className="card mb-4 flex items-center gap-3 !border-l-4 !border-l-green-500">
        <CheckCircle2 className="w-6 h-6 text-green-500 flex-shrink-0" />
        <div><div className="font-display font-bold text-gray-800">{result.employee} — offboarding processed</div>
          <div className="text-xs text-gray-500">Final pay run and ROE draft generated for last day {fmt(result.last_day)}.</div></div>
        <button className="btn-secondary text-xs ml-auto" onClick={onReset}>Offboard another</button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Vacation paid out" value={money(result.vacation_paid)} icon={Palmtree} accent />
        <StatTile label="Final cheque net" value={money(fp?.net_pay || 0)} icon={Wallet} />
        <StatTile label="ROE insurable hrs" value={roe.block15a_total_insurable_hours ?? "—"} sub={roe.block15a_hours_source} />
        <StatTile label="ROE insurable earnings" value={money(roe.block15b_total_insurable_earnings || 0)} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* final cheque */}
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-3 flex items-center gap-2"><Wallet className="w-4 h-4 text-primary-500" />Final cheque</h3>
          {!fp ? <Empty>No cheque — nothing owed.</Empty> : (
            <div className="space-y-1.5 text-sm">
              <Row label="Gross" value={money(fp.gross)} />
              <Row label="CPP" value={money(fp.deductions?.cpp || 0)} />
              <Row label="EI" value={money(fp.deductions?.ei || 0)} />
              <Row label="Income tax" value={money(fp.deductions?.income_tax || 0)} />
              <div className="border-t border-surface-border pt-1.5"><Row label="Net pay" value={money(fp.net_pay)} bold /></div>
            </div>
          )}
        </div>
        {/* ROE */}
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-3 flex items-center gap-2"><FileText className="w-4 h-4 text-primary-500" />Record of Employment (draft)</h3>
          <div className="space-y-1.5 text-sm">
            <Row label="Block 16 · reason" value={roe.block16_reason?.label} />
            <Row label="Block 10 · first day" value={fmt(roe.block10_first_day_worked)} />
            <Row label="Block 11 · last day paid" value={fmt(roe.block11_last_day_paid)} />
            <Row label="Block 15A · insurable hrs" value={`${roe.block15a_total_insurable_hours ?? "—"} (${roe.block15a_hours_source})`} />
            <Row label="Block 15B · insurable $" value={money(roe.block15b_total_insurable_earnings || 0)} />
          </div>
          <p className="text-[11px] text-gray-400 mt-3">Block data for review — a real ROE is filed via ROE Web / XML.</p>
        </div>
      </div>

      {!terminated && (
        <div className="card mt-4 flex items-center justify-between gap-4 flex-wrap !border-l-4 !border-l-amber-400">
          <div className="flex items-center gap-2 text-sm text-gray-600"><AlertTriangle className="w-4 h-4 text-amber-500" />Employee is still marked active. Mark them terminated to complete the separation.</div>
          <button className="btn-primary text-sm" onClick={onTerminate}>Mark terminated</button>
        </div>
      )}
    </div>
  );
}

function Row({ label, value, bold }: { label: string; value: any; bold?: boolean }) {
  return <div className="flex items-center justify-between"><span className="text-gray-500">{label}</span><span className={`tabular-nums ${bold ? "font-bold text-gray-900" : "text-gray-700"}`}>{value}</span></div>;
}
