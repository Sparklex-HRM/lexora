import { useState, useMemo, useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Wallet, Play, History, Sigma, FileText, Calculator, X, Pencil, ChevronRight, Plus, Trash2, SlidersHorizontal, AlertCircle, AlertTriangle, Info, Table2, Landmark, Award, Printer, ShieldCheck, Download, Search, ChevronsLeft, ChevronLeft, ChevronsRight, Lock, Zap, Users, CheckCircle2, CircleDollarSign, Receipt, CalendarClock } from "lucide-react";
import { useSearchParams, Link } from "react-router-dom";
import { useAuthStore } from "../store/authStore";
import { PageHeader, StatTile, Spinner, Badge, Empty } from "../components/ui";
import PayslipModal from "../components/PayslipModal";
import { printRegion } from "../lib/print";   // print wrap-up 2026-10-24
import {
  payrollApi, timeApi, employeeApi, mastersApi, MasterRow, RunEntries, RunEntry, RunFlag, RunLine, AnnualPreview, YtdRow, RunRefusal,
  money, freqLabel, PAY_FREQUENCIES, PROVINCES,
} from "../api/endpoints";
import { addDaysISO, todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

// POST /payroll/runs refuses with a coded 422 (PAYROLL_RUN_HAS_ERRORS / _WARNINGS): one readable toast.
const runRefusalMessage = (e: any): string => {
  const d = e?.response?.data?.detail as RunRefusal | string | undefined;
  if (!d) return "Run failed";
  if (typeof d === "string") return d;
  const who = (d.code === "PAYROLL_RUN_HAS_ERRORS" ? d.errors : d.warnings) || [];
  const names = who.slice(0, 3).map((x) => `${x.name}: ${x.message}`).join(" · ");
  return `${d.code ? `[${d.code}] ` : ""}${d.message || "Run refused"}${names ? ` — ${names}${who.length > 3 ? ` (+${who.length - 3} more)` : ""}` : ""}`;
};

// Canadian payroll wording: CPP / CPP2 / EI / tax, CRA remittance (PD7A).
const L = {
  subtitle: "Run payroll by pay frequency · CRA-accurate CPP / CPP2 / EI / tax",
  deductions_sub: "CPP · CPP2 · EI · tax",
  remittance: "CRA remittance", remittance_sub: "incl. employer share",
  cra: "CRA", col_a: "CPP", col_b: "EI",
};

function YearPicker({ year, setYear }: { year: number; setYear: (y: number) => void }) {
  return (
    <select className="input !py-1 !w-auto text-sm" value={year} onChange={(e) => setYear(+e.target.value)}>
      {[2026, 2025].map((y) => <option key={y} value={y}>{y}</option>)}
    </select>
  );
}

// Anomaly flag indicator on a preview row (hover for the messages).
function FlagBadge({ flags }: { flags?: RunFlag[] }) {
  if (!flags || !flags.length) return null;
  const hasErr = flags.some((f) => f.level === "error");
  const hasWarn = flags.some((f) => f.level === "warn");
  const Icon = hasErr ? AlertCircle : hasWarn ? AlertTriangle : Info;
  const color = hasErr ? "text-red-500" : hasWarn ? "text-amber-500" : "text-gray-300";
  return (
    <span title={flags.map((f) => `${f.level.toUpperCase()}: ${f.message}`).join("\n")}
      className={`inline-flex items-center align-middle ml-1 ${color}`}>
      <Icon className="w-3.5 h-3.5" />
    </span>
  );
}

const CAPS = { cpp: 4230.45, cpp2: 416.0, ei: 1123.07 };
const today = () => todayISO();
const addDays = (iso: string, n: number) => addDaysISO(iso, n);
// Local date: new Date(y, m + 1, 0) is LOCAL midnight; toISOString() (UTC) could shift it a day east of UTC.
const monthEnd = (iso: string) => { const d = new Date(iso); const e = new Date(d.getFullYear(), d.getMonth() + 1, 0); return `${e.getFullYear()}-${String(e.getMonth() + 1).padStart(2, "0")}-${String(e.getDate()).padStart(2, "0")}`; };
// Period start + pay date derived from the period-END and the pay FREQUENCY.
// Previously hardcoded to biweekly's 14-day window, which mislabeled the period on
// monthly / weekly / semi-monthly runs (the amounts were right, the dates weren't).
const derivePeriod = (pe: string, freq: string): { ps: string; pd: string } => {
  const [y, m, d] = pe.split("-").map(Number);
  const p = (n: number) => String(n).padStart(2, "0");
  if (freq === "weekly") return { ps: addDays(pe, -6), pd: addDays(pe, 2) };
  if (freq === "semi_monthly") return { ps: d <= 15 ? `${y}-${p(m)}-01` : `${y}-${p(m)}-16`, pd: addDays(pe, 2) };
  if (freq === "monthly") return { ps: `${y}-${p(m)}-01`, pd: pe };
  return { ps: addDays(pe, -13), pd: addDays(pe, 2) };   // biweekly (2-week window)
};

// Off-cycle run intents. The backend /payroll/off-cycle endpoint is per-employee and does
// not take a type, so this drives UI intent + labelling only (see TODO(off-cycle) in RunTab).
const OFF_CYCLE_TYPES = [
  { value: "bonus", label: "Bonus" },
  { value: "correction", label: "Correction" },
  { value: "retro", label: "Retro pay" },
  { value: "termination", label: "Termination" },
];
const offTypeLabel = (v: string) => OFF_CYCLE_TYPES.find((t) => t.value === v)?.label ?? v;
const hasError = (l: RunLine) => !!l.flags?.some((f) => f.level === "error");

const TABS = [
  { key: "run", label: "Run Payroll", icon: Play },
  { key: "runs", label: "Pay Runs", icon: History },
  { key: "ytd", label: "YTD", icon: Sigma },
  { key: "payslips", label: "Payslips", icon: FileText },
  { key: "yearend", label: "Year-end", icon: Award },
  { key: "calc", label: "Calculator", icon: Calculator },
];

// ── Run cockpit state (shared by the command band + the Run Payroll tab) ─────
// Lifted to PayrollPage so the persistent KPI band always reflects the current
// selected subset, regardless of which tab is active.
function useRunCockpit() {
  const qc = useQueryClient();
  const [sp] = useSearchParams();   // deep-link: /payroll?mode=offcycle jumps into off-cycle
  const [mode, setMode] = useState<"regular" | "offcycle">(sp.get("mode") === "offcycle" ? "offcycle" : "regular");
  const [freq, setFreq] = useState("biweekly");
  const [offType, setOffType] = useState("bonus");
  const [pe, setPe] = useState(today());
  const [entries, setEntries] = useState<RunEntries>({});
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [extraPay, setExtraPay] = useState(false);   // 27-pay biweekly / 53-pay weekly year
  const { ps, pd } = derivePeriod(pe, freq);
  // Snap the period-end to the calendar month-end when switching to monthly, so the
  // run naturally covers a whole month (the user can still pick any date after).
  useEffect(() => { if (freq === "monthly") setPe((cur) => monthEnd(cur)); }, [freq]);
  // Only weekly/biweekly calendars can have an extra pay; CRA annualizes by the actual count.
  const periodsInYear = extraPay && (freq === "weekly" || freq === "biweekly")
    ? (freq === "weekly" ? 53 : 27) : undefined;

  // Canada reckons YTD on the calendar year.
  const fyYear = 2026;

  const entriesKey = JSON.stringify(entries);
  // Re-run of a VOIDED run: "as originally paid" (the voided run's salary rates; queued arrears
  // stay) or at today's salary history. Only meaningful when a voided run exists for the period.
  const [asVoided, setAsVoided] = useState(true);
  const { data: runsForBasis } = useQuery({ queryKey: ["runs"], queryFn: () => payrollApi.runs() });
  const hasVoided = mode === "regular" && !!(runsForBasis || []).find((r: any) => r.pay_frequency === freq && r.period_end === pe && r.status === "void")
    && !(runsForBasis || []).find((r: any) => r.pay_frequency === freq && r.period_end === pe && r.status !== "void");
  const salaryBasis: "current" | "as_voided" = hasVoided && asVoided ? "as_voided" : "current";
  const { data: prev, isLoading } = useQuery({ queryKey: ["run-preview", freq, entriesKey, periodsInYear, fyYear, ps, pe, salaryBasis], queryFn: () => payrollApi.runPreview(freq, entries, periodsInYear, fyYear, ps, pe, salaryBasis) });
  const { data: ytdLedger } = useQuery({ queryKey: ["ytd", fyYear], queryFn: () => payrollApi.ytd(fyYear) });

  const lines = useMemo<RunLine[]>(() => prev?.lines ?? [], [prev]);
  const readyLines = useMemo(() => lines.filter((l) => !hasError(l)), [lines]);
  const attentionLines = useMemo(() => lines.filter((l) => hasError(l)), [lines]);
  const readyIds = useMemo(() => readyLines.map((l) => l.employee_id), [readyLines]);
  const readySig = readyIds.slice().sort().join(",");

  // Re-seed selection only when the *set* of ready employees changes (frequency/mode
  // switch, or an error flag toggles membership) — adjusting amounts keeps the selection.
  const readyIdsRef = useRef<string[]>([]);
  readyIdsRef.current = readyIds;
  useEffect(() => {
    setSelected(mode === "offcycle" ? new Set() : new Set(readyIdsRef.current));
  }, [mode, readySig]);

  const selectedLines = useMemo(() => lines.filter((l) => selected.has(l.employee_id)), [lines, selected]);
  const sel = useMemo(() => {
    const s = (f: (l: RunLine) => number) => selectedLines.reduce((a, l) => a + (Number(f(l)) || 0), 0);
    const gross = s((l) => l.gross), cpp = s((l) => l.cpp), cpp2 = s((l) => l.cpp2), ei = s((l) => l.ei), tax = s((l) => l.income_tax), net = s((l) => l.net);
    // CRA remittance (PD7A): employer matches CPP/CPP2 1:1 and EI at 1.4×.
    const deductions = cpp + cpp2 + ei + tax;
    const cra = tax + cpp * 2 + cpp2 * 2 + ei * 2.4;
    const employer = cpp + cpp2 + ei * 1.4;
    return { count: selectedLines.length, gross, cpp, cpp2, ei, tax, net, deductions, cra, employer };
  }, [selectedLines]);

  const ytdGross = useMemo(() => (ytdLedger?.employees ?? []).reduce((a, e) => a + (Number(e.gross) || 0), 0), [ytdLedger]);

  return {
    qc, mode, setMode, freq, setFreq, offType, setOffType, pe, setPe, ps, pd,
    entries, setEntries, selected, setSelected, extraPay, setExtraPay, periodsInYear,
    prev, isLoading, lines, readyLines, attentionLines, readyIds,
    sel, ytdGross, fyYear, hasVoided, asVoided, setAsVoided, salaryBasis,
  };
}
type Cockpit = ReturnType<typeof useRunCockpit>;

// Persistent KPI band above the tab bar — always reflects the selected subset.
function CommandBand({ c }: { c: Cockpit }) {
  const { sel, ytdGross, fyYear } = c;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-4">
      <StatTile label="Net · selected" value={money(sel.net)} sub={`${sel.count} employee${sel.count !== 1 ? "s" : ""}`} icon={Wallet} accent />
      <StatTile label="Gross · selected" value={money(sel.gross)} icon={CircleDollarSign} />
      <StatTile label="Deductions" value={money(sel.deductions)} sub={L.deductions_sub} icon={Receipt} />
      <StatTile label={L.remittance} value={money(sel.cra)} sub={L.remittance_sub} icon={Landmark} />
      <StatTile label="YTD gross" value={money(ytdGross)} sub={`all employees · ${fyYear}`} icon={Sigma} />
    </div>
  );
}

export default function PayrollPage() {
  // Tab is URL-driven so the sidebar can deep-link (e.g. /payroll?tab=yearend&ye=roe).
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "run";
  const setTab = (t: string) => {
    const n = new URLSearchParams(params);
    n.set("tab", t);
    if (t !== "yearend") n.delete("ye");
    setParams(n, { replace: true });
  };
  const [stubId, setStubId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [ytdEdit, setYtdEdit] = useState<YtdRow | null>(null);
  const cockpit = useRunCockpit();

  return (
    <div>
      <PageHeader title="Payroll" icon={Wallet} subtitle={L.subtitle} />
      <CommandBand c={cockpit} />
      <div className="flex gap-1 mb-4 overflow-x-auto">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${
              tab === t.key ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>
            <t.icon className="w-4 h-4" />{t.label}
          </button>
        ))}
      </div>

      {tab === "run" && <RunTab c={cockpit} />}
      {tab === "runs" && <RunsTab onOpen={setRunId} />}
      {tab === "ytd" && <YtdTab onEdit={setYtdEdit} />}
      {tab === "payslips" && <PayslipsTab onOpen={setStubId} />}
      {tab === "yearend" && <YearEndTab />}
      {tab === "calc" && <CalcTab />}

      {runId && <RunDetailModal runId={runId} onClose={() => setRunId(null)} onStub={(s) => { setRunId(null); setStubId(s); }} />}
      {stubId && <PayslipModal stubId={stubId} onClose={() => setStubId(null)} />}
      {ytdEdit && <YtdEditModal row={ytdEdit} onClose={() => setYtdEdit(null)} />}
    </div>
  );
}

// ── Run payroll (cockpit) ────────────────────────────────────────────────────
function RunTab({ c }: { c: Cockpit }) {
  const {
    qc, mode, setMode, freq, setFreq, offType, setOffType, pe, setPe, ps, pd,
    entries, setEntries, selected, setSelected, extraPay, setExtraPay, periodsInYear,
    prev, isLoading, readyLines, attentionLines, readyIds, sel, fyYear, hasVoided, asVoided, setAsVoided, salaryBasis,
  } = c;
  const [editEmp, setEditEmp] = useState<{ id: string; name: string } | null>(null);
  const { data: components } = useQuery({ queryKey: ["m", "pay-components"], queryFn: () => mastersApi.list("pay-components") });

  const invalidate = (keys: string[]) => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));

  // Compliance gate — is payroll verified for REAL pay? (constants + CRA-PDOC cases)
  const comp = useQuery({ queryKey: ["payroll-compliance"], queryFn: () => payrollApi.compliance() });
  // Real pay is gated by the CRA verification gate.
  const activeGate = comp.data?.gate;
  const gateOpen = !!activeGate?.gate_open;

  // Is THIS period already committed? Match a persisted run on frequency + period-end.
  // When found, the Run tab reads as "done" instead of re-offering a commit for a
  // period that the backend would 409 as a duplicate.
  const { data: runsList } = useQuery({ queryKey: ["runs"], queryFn: () => payrollApi.runs() });
  // A VOIDED test run no longer blocks the period — it can be run again.
  const committedRun = mode === "regular"
    ? (runsList || []).find((r: any) => r.pay_frequency === freq && r.period_end === pe && r.status !== "void")
    : null;
  const voidedRun = mode === "regular"
    ? (runsList || []).find((r: any) => r.pay_frequency === freq && r.period_end === pe && r.status === "void")
    : null;

  // Regular run — commits only the SELECTED subset via the new employee_ids param.
  const exec = useMutation({
    mutationFn: ({ ids, ack, ackWarn }: { ids: string[]; ack: boolean; ackWarn: boolean }) => payrollApi.runExecute({ pay_frequency: freq, period_start: ps, period_end: pe, pay_date: pd, entries, employee_ids: ids, acknowledge_unverified: ack, acknowledge_warnings: ackWarn, salary_basis: salaryBasis, periods_in_year: periodsInYear, year: fyYear }),
    onSuccess: (r: any) => {
      invalidate(["runs", "ytd", "run-preview", "payslips"]);
      setEntries({});
      toast.success(`${r.compliance?.mode === "test" ? "TEST " : ""}Pay run committed — ${r.employee_count} paid, net ${money(r.total_net)}`);
    },
    onError: (e: any) => toast.error(runRefusalMessage(e), { duration: 9000 }),
  });

  // TODO(off-cycle): the backend /payroll/off-cycle endpoint is per-employee and takes NO
  // "type" (bonus/correction/retro/termination) — the type select is UI intent only. There is
  // no bulk off-cycle statutory preview, so the roster/figures reuse the regular runPreview;
  // the amounts actually posted come from each employee's Adjust entries.
  const offExec = useMutation({
    mutationFn: ({ ids, ack }: { ids: string[]; ack: boolean }) => Promise.all(ids.map((id) =>
      payrollApi.offCycle({ employee_id: id, pay_date: pd, earnings: entries[id]?.earnings ?? [], deductions: entries[id]?.deductions ?? [], acknowledge_unverified: ack }))),
    onSuccess: (res) => {
      invalidate(["off-cycle", "ytd", "run-preview", "payslips", "runs"]);
      setEntries({});
      toast.success(`Off-cycle ${offTypeLabel(offType).toLowerCase()} created for ${res.length} employee${res.length !== 1 ? "s" : ""}`);
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Off-cycle run failed"),
  });

  // Pull approved worked hours from Time & Attendance → merge REG/OT/STAT into the
  // run's entries. Hourly REG replaces payroll's synthetic base; salary base stands.
  const readiness = useQuery({
    queryKey: ["ta-readiness", freq, ps, pe],
    queryFn: () => timeApi.readiness(freq, ps, pe), enabled: mode === "regular",
  });
  const FED = ["REG", "OT", "OT2", "STAT"];
  const pullFeed = useMutation({
    mutationFn: () => timeApi.payrollFeed(freq, ps, pe),
    onSuccess: (f) => {
      if (!f.employee_count) { toast("No approved timesheets for this period yet", { icon: "🕓" }); return; }
      setEntries((prevE) => {
        const n: RunEntries = { ...prevE };
        for (const [id, e] of Object.entries(f.entries)) {
          const ex = n[id] || { earnings: [], deductions: [] };
          const kept = ex.earnings.filter((x) => !FED.includes(x.code));
          // keep `hours` on each earning so the pay run can record real insurable hours (ROE).
          n[id] = { earnings: [...kept, ...e.earnings.map((x) => ({ code: x.code, amount: x.amount, hours: x.hours }))], deductions: ex.deductions };
        }
        return n;
      });
      toast.success(`Pulled ${f.employee_count} timesheet${f.employee_count !== 1 ? "s" : ""} from Time & Attendance`);
    },
    onError: () => toast.error("Couldn't reach Time & Attendance"),
  });

  const pending = exec.isPending || offExec.isPending;
  const entryCount = (id: string) => (entries[id]?.earnings.length || 0) + (entries[id]?.deductions.length || 0);
  const setEmpEntry = (id: string, v: RunEntry | null) =>
    setEntries((prevE) => { const n = { ...prevE }; if (v) n[id] = v; else delete n[id]; return n; });

  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const allSelected = readyLines.length > 0 && readyLines.every((l) => selected.has(l.employee_id));
  const someSelected = readyLines.some((l) => selected.has(l.employee_id));
  const toggleAll = () => setSelected(() => (allSelected ? new Set() : new Set(readyIds)));

  const selectAllRef = useRef<HTMLInputElement>(null);
  useEffect(() => { if (selectAllRef.current) selectAllRef.current.indeterminate = someSelected && !allSelected; }, [someSelected, allSelected]);

  const commit = () => {
    const ids = [...selected];
    if (!ids.length) return;
    // Selected rows are drawn from the "ready" pool (no error flag), but keep the anomaly
    // confirm as a safety net in case a warning-only row also carries an error.
    const selErr = readyLines.filter((l) => selected.has(l.employee_id)).reduce((n, l) => n + (l.flags?.filter((f) => f.level === "error").length || 0), 0);
    if (selErr > 0 && !window.confirm(`This run has ${selErr} error(s) among selected employees. Commit anyway?`)) return;
    // Compliance gate: a run while unverified must be explicitly acknowledged as TEST.
    let ack = false;
    if (gateOpen) {
      const reasons = (activeGate?.reasons || []).join("\n• ");
      if (!window.confirm(`⚠ PAYROLL IS NOT VERIFIED FOR REAL PAY.\n\n• ${reasons}\n\nThis will be recorded as a TEST run — NOT for remittance or filing. Verify against CRA (Year-end → Compliance) before real cheques.\n\nProceed as a test run?`)) return;
      ack = true;
    }
    // Warnings (pay swings, no tax on a high salary …) are acknowledged here; the server refuses
    // an un-acknowledged run with PAYROLL_RUN_HAS_WARNINGS and NEVER commits an error-level one.
    let ackWarn = false;
    const selWarn = readyLines.filter((l) => selected.has(l.employee_id)).reduce((n, l) => n + (l.flags?.filter((f) => f.level === "warn").length || 0), 0);
    if (mode === "regular" && selWarn > 0) {
      if (!window.confirm(`${selWarn} warning${selWarn > 1 ? "s" : ""} on the selected employees (see the ⚠ flags). Acknowledge and commit?`)) return;
      ackWarn = true;
    }
    if (mode === "offcycle") offExec.mutate({ ids, ack });
    else exec.mutate({ ids, ack, ackWarn });
  };

  const step = isLoading ? 0 : sel.count === 0 ? 1 : 2;   // Prepare · Select · Commit
  const STEPS = ["Prepare", "Select", "Commit"];

  return (
    <>
      {/* Compliance gate banner — impossible to miss when payroll isn't verified */}
      {gateOpen && (
        <div className="mb-4 rounded-xl border-2 border-red-300 bg-red-50 px-4 py-3">
          <div className="flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-red-600 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-red-800 text-sm">Payroll is NOT verified for real pay — runs are recorded as <span className="uppercase">test</span></div>
              <div className="text-xs text-red-700 mt-1 space-y-0.5">
                {(activeGate?.reasons || []).map((r: string, i: number) => <div key={i}>• {r}</div>)}
              </div>
              <div className="text-[11px] text-red-600 mt-1.5">
                <>{activeGate?.constants_verified}/{activeGate?.constants_total} constants verified · {activeGate?.pdoc_confirmed}/{activeGate?.pdoc_total} CRA-PDOC cases confirmed.
                  <Link to="/payroll?tab=yearend&ye=compliance" className="underline font-medium ml-1">Open Compliance →</Link></>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Voided run for this period — it can be run again */}
      {voidedRun && !committedRun && (
        <div className="mb-4 rounded-xl border border-gray-300 bg-gray-50 px-4 py-2.5 text-xs text-gray-700" data-testid="voided-run-note">
          <b>The {freqLabel(freq).toLowerCase()} run for {pe} was voided</b>{voidedRun.void?.reason ? ` — “${voidedRun.void.reason}”` : ""}{voidedRun.void?.by ? ` by ${voidedRun.void.by}` : ""}. The period can be run again; the year-to-date ledger was reversed.
          {hasVoided && (
            <label className="mt-1.5 flex items-center gap-2 cursor-pointer" title="On: the re-run pays the salary rates the voided run paid (a hike applied after it stays in the queued arrears). Off: today's salary history is used — cancel the queued arrears for this month first, or the increment is paid twice.">
              <input type="checkbox" checked={asVoided} onChange={(e) => setAsVoided(e.target.checked)} data-testid="as-voided-toggle" />
              <span>Re-run <b>as originally paid</b> (salary rates of the voided run{prev?.salary_basis?.employees_at_voided_rates ? ` · ${prev.salary_basis.employees_at_voided_rates} employees` : ""}); queued arrears for this month stay as they are</span>
            </label>
          )}
        </div>
      )}

      {/* Already-committed banner — this exact period is done, not runnable */}
      {committedRun && (
        <div className="mb-4 rounded-xl border-2 border-green-300 bg-green-50 px-4 py-3">
          <div className="flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 text-green-600 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <div className="font-semibold text-green-800 text-sm">
                This {freqLabel(freq)} period (ending {pe}) is already committed
              </div>
              <div className="text-xs text-green-700 mt-1">
                {committedRun.employee_count} employee{committedRun.employee_count !== 1 ? "s" : ""} · gross {money(committedRun.total_gross)} · net {money(committedRun.total_net)}. Re-running the same period is blocked — advance the <b>pay date</b> to prepare the next run.
              </div>
              <Link to="/payroll?tab=runs" className="text-[12px] text-green-700 underline font-medium mt-1 inline-block">View in Runs →</Link>
            </div>
          </div>
        </div>
      )}

      {/* Run toolbar */}
      <div className="card !p-0 mb-4 overflow-hidden">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3">
          {/* Mode toggle */}
          <div className="inline-flex rounded-lg border border-surface-border p-0.5 bg-surface">
            {([["regular", "Regular", Play], ["offcycle", "Off-cycle", Zap]] as const).map(([m, label, Icon]) => (
              <button key={m} onClick={() => setMode(m)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                  mode === m ? "bg-white text-primary-600 shadow-sm" : "text-gray-500 hover:text-gray-700"}`}>
                <Icon className="w-3.5 h-3.5" />{label}
              </button>
            ))}
          </div>

          {/* Frequency (regular) or off-cycle type */}
          <div className="flex items-center gap-1.5">
            <label className="text-xs font-semibold text-gray-500">{mode === "regular" ? "Frequency" : "Type"}</label>
            {mode === "regular" ? (
              <select className="input !py-1.5 !w-auto text-sm" value={freq} onChange={(e) => setFreq(e.target.value)}>
                {PAY_FREQUENCIES
                  .map((f) => <option key={f.value} value={f.value}>{f.label} · {f.periods}/yr</option>)}
              </select>
            ) : (
              <select className="input !py-1.5 !w-auto text-sm" value={offType} onChange={(e) => setOffType(e.target.value)}>
                {OFF_CYCLE_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            )}
          </div>

          {/* 27-pay / 53-pay year — rare calendar years with an extra pay; CRA annualizes by the actual count */}
          {mode === "regular" && (freq === "biweekly" || freq === "weekly") && (
            <label className="flex items-center gap-1.5 text-xs text-gray-600 cursor-pointer" title={`This calendar year has ${freq === "weekly" ? "53 weekly" : "27 biweekly"} pays instead of the usual ${freq === "weekly" ? "52" : "26"}. CRA prorates the CPP exemption and annualizes over the actual number of pays.`}>
              <input type="checkbox" checked={extraPay} onChange={(e) => setExtraPay(e.target.checked)} />
              {freq === "weekly" ? "53-pay year" : "27-pay year"}
              {periodsInYear && <span className="badge-amber text-[10px]">{periodsInYear} pays</span>}
            </label>
          )}

          {/* Period end */}
          <div className="flex items-center gap-1.5">
            <label className="text-xs font-semibold text-gray-500">{mode === "regular" ? "Period end" : "Pay date basis"}</label>
            <input className="input !py-1.5 !w-auto text-sm" type="date" value={pe} onChange={(e) => setPe(e.target.value)} />
          </div>

          {/* Derived chips */}
          <div className="flex items-center gap-1.5 text-[11px]">
            <span className="inline-flex items-center gap-1 rounded-full bg-surface border border-surface-border px-2 py-1 text-gray-500">Start <b className="text-gray-700 tabular-nums">{ps}</b></span>
            <span className="inline-flex items-center gap-1 rounded-full bg-surface border border-surface-border px-2 py-1 text-gray-500">Pay <b className="text-gray-700 tabular-nums">{pd}</b></span>
          </div>

          {/* Pull worked hours from Time & Attendance */}
          {mode === "regular" && (
            <button className="btn-secondary !py-1.5 inline-flex items-center gap-1.5 text-xs" onClick={() => pullFeed.mutate()} disabled={pullFeed.isPending}
              title="Merge approved REG/OT/STAT hours from Time & Attendance into this run">
              <CalendarClock className="w-3.5 h-3.5" />{pullFeed.isPending ? "Pulling…" : "Pull hours from T&A"}
              {readiness.data && readiness.data.total_employees > 0 && (
                <span className={readiness.data.all_ready ? "text-green-600 font-semibold" : "text-amber-600 font-semibold"}>{readiness.data.ready}/{readiness.data.total_employees}</span>
              )}
            </button>
          )}

          {/* Stepper + status */}
          <div className="ml-auto flex items-center gap-3">
            <div className="hidden md:flex items-center gap-1 text-[11px] font-medium">
              {STEPS.map((s, i) => (
                <span key={s} className="inline-flex items-center gap-1">
                  <span className={`inline-flex items-center justify-center w-4 h-4 rounded-full text-[9px] ${i <= step ? "bg-primary-600 text-white" : "bg-surface border border-surface-border text-gray-400"}`}>{i + 1}</span>
                  <span className={i <= step ? "text-primary-600" : "text-gray-400"}>{s}</span>
                  {i < STEPS.length - 1 && <ChevronRight className="w-3 h-3 text-gray-300" />}
                </span>
              ))}
            </div>
            <span className="badge-purple">Draft</span>
          </div>
        </div>

        {/* Anomaly banner */}
        {prev?.anomalies && (prev.anomalies.errors > 0 || prev.anomalies.warnings > 0) && (
          <div className={`px-4 py-2 text-xs flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-surface-border ${prev.anomalies.errors ? "bg-red-50 text-red-700" : "bg-amber-50 text-amber-700"}`}>
            <AlertTriangle className="w-4 h-4 shrink-0" />
            {prev.anomalies.errors > 0 && <span><b>{prev.anomalies.errors}</b> error{prev.anomalies.errors > 1 ? "s" : ""} moved to Needs attention</span>}
            {prev.anomalies.warnings > 0 && <span><b>{prev.anomalies.warnings}</b> warning{prev.anomalies.warnings > 1 ? "s" : ""} — review before committing</span>}
          </div>
        )}
      </div>

      {/* Ready vs Needs-attention split */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        {/* Ready to run */}
        <div className="card !p-0 xl:col-span-2 flex flex-col">
          <div className="flex items-center justify-between px-5 py-3 border-b border-surface-border">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-primary-500" />
              <h3 className="font-display font-semibold text-gray-800">Ready to run <span className="text-gray-400 font-normal">({readyLines.length})</span></h3>
            </div>
            <span className="text-xs text-gray-400 tabular-nums">{sel.count} selected</span>
          </div>
          {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
            : readyLines.length === 0 ? <Empty>{mode === "offcycle" ? "No eligible employees for an off-cycle payment." : `No employees ready on the ${freqLabel(freq).toLowerCase()} frequency.`}</Empty>
            : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                    <tr>
                      <th className="px-3 py-2 w-10"><input ref={selectAllRef} type="checkbox" className="accent-primary-600 w-4 h-4 cursor-pointer align-middle" checked={allSelected} onChange={toggleAll} title="Select all" /></th>
                      <th className="px-2 py-2 font-semibold">File #</th>
                      <th className="px-4 py-2 font-semibold">Employee</th>
                      <th className="px-4 py-2 font-semibold text-right">Gross</th>
                      <th className="px-3 py-2 font-semibold text-right">{L.col_a}</th>
                      <th className="px-3 py-2 font-semibold text-right">{L.col_b}</th>
                      <th className="px-3 py-2 font-semibold text-right">Tax</th>
                      <th className="px-4 py-2 font-semibold text-right">Net</th>
                      <th className="px-4 py-2 font-semibold text-center">Adjust</th>
                    </tr>
                  </thead>
                  <tbody>
                    {readyLines.map((l) => {
                      const on = selected.has(l.employee_id);
                      return (
                        <tr key={l.employee_id} className={`border-t border-surface-border transition-colors ${on ? "bg-primary-50/40" : "hover:bg-gray-50"}`}>
                          <td className="px-3 py-2"><input type="checkbox" className="accent-primary-600 w-4 h-4 cursor-pointer align-middle" checked={on} onChange={() => toggle(l.employee_id)} /></td>
                          <td className="px-2 py-2 text-xs text-gray-400 tabular-nums whitespace-nowrap">{l.employee_number || "—"}</td>
                          <td className="px-4 py-2 text-gray-800 whitespace-nowrap">{l.name} <span className="text-xs text-gray-400">· {l.province}</span><FlagBadge flags={l.flags} />
                            {l.payable && l.payable.reason_code !== "full" && <span className="block text-[11px] text-amber-700" title={(l.payable.review || []).join(" ")}>{l.payable.reason}</span>}</td>
                          <td className="px-4 py-2 text-right tabular-nums text-gray-700">{money(l.gross)}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-gray-500">{money((l.cpp || 0) + (l.cpp2 || 0))}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-gray-500">{money(l.ei)}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-gray-500">{money(l.income_tax)}</td>
                          <td className="px-4 py-2 text-right tabular-nums font-semibold text-gray-800">{money(l.net)}</td>
                          <td className="px-4 py-2 text-center">
                            <button onClick={() => setEditEmp({ id: l.employee_id, name: l.name })}
                              className={`inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium border transition-colors ${
                                entryCount(l.employee_id) ? "bg-primary-50 text-primary-600 border-primary-200" : "text-gray-400 border-surface-border hover:border-primary-300 hover:text-primary-500"}`}>
                              <SlidersHorizontal className="w-3 h-3" />
                              {entryCount(l.employee_id) ? `${entryCount(l.employee_id)}` : "Add"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                  <tfoot>
                    <tr className="border-t-2 border-surface-border bg-surface font-semibold">
                      <td /><td />
                      <td className="px-4 py-2 text-gray-800">Selected · {sel.count}</td>
                      <td className="px-4 py-2 text-right tabular-nums">{money(sel.gross)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{money(sel.cpp + sel.cpp2)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{money(sel.ei)}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{money(sel.tax)}</td>
                      <td className="px-4 py-2 text-right tabular-nums text-primary-600">{money(sel.net)}</td>
                      <td />
                    </tr>
                  </tfoot>
                </table>
              </div>
            )}
        </div>

        {/* Needs attention */}
        <div className="card !p-0 flex flex-col">
          <div className="flex items-center justify-between px-5 py-3 border-b border-surface-border">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-500" />
              <h3 className="font-display font-semibold text-gray-800">Needs attention <span className="text-gray-400 font-normal">({attentionLines.length})</span></h3>
            </div>
          </div>
          {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
            : attentionLines.length === 0 ? (
              <div className="px-5 py-12 text-center">
                <CheckCircle2 className="w-8 h-8 text-green-400 mx-auto mb-2" />
                <p className="text-sm text-gray-400">All clear — no blocking issues.</p>
              </div>
            ) : (
              <div className="flex-1 overflow-y-auto">
                <div className="divide-y divide-surface-border">
                  {attentionLines.map((l) => (
                    <div key={l.employee_id} className="px-5 py-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-sm font-medium text-gray-800 truncate">{l.name}</div>
                        <span className="text-[10px] text-gray-400 tabular-nums shrink-0">{l.employee_number || "—"}</span>
                      </div>
                      <div className="text-[11px] text-gray-400 mb-1.5">{l.province}</div>
                      <div className="flex flex-wrap gap-1">
                        {(l.flags || []).filter((f) => f.level === "error").map((f, i) => (
                          <span key={i} className="inline-flex items-center gap-1 rounded-full bg-red-50 text-red-600 px-2 py-0.5 text-[10px] font-medium">
                            <AlertCircle className="w-3 h-3 shrink-0" />{f.message}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="px-5 py-2.5 text-[11px] text-gray-400 bg-surface border-t border-surface-border">Excluded from this run until resolved.</div>
              </div>
            )}
        </div>
      </div>

      {!!prev?.excluded?.length && (
        <div className="card !p-0 mt-3">
          <div className="px-4 py-2 border-b border-surface-border text-sm font-semibold text-gray-800">Not paid in this period ({prev.excluded.length})</div>
          <table className="w-full text-xs"><tbody>{prev.excluded.map((x) => (
            <tr key={x.employee_id} className="border-t border-surface-border"><td className="px-4 py-1.5 text-gray-400 tabular-nums whitespace-nowrap">{x.employee_number || "—"}</td><td className="px-2 py-1.5 font-medium text-gray-800 whitespace-nowrap">{x.name}</td><td className="px-2 py-1.5 text-gray-600 w-full">{x.reason}</td></tr>))}</tbody></table>
        </div>)}
      {prev?.date_rules && <p className="text-[11px] text-gray-400 mt-2">Part periods are paid by {prev.date_rules.basis_label}. Joiners are paid from the joining date and leavers up to the last working day.{prev.date_rules.period_known ? "" : " Pick the pay period to see part-period pay."}</p>}

      <p className="text-[11px] text-gray-400 mt-3 flex items-start gap-1">
        <ShieldCheck className="w-3.5 h-3.5 mt-px shrink-0 text-amber-500" />
        <span>Each employee is computed from their own carried-forward YTD; re-running the same period is blocked. Use <b className="text-gray-500">Adjust</b> for one-off bonuses / OT / RRSP. 2026 statutory constants are estimates pending CRA verification — see <b>Year-end → Compliance</b> before real payroll.</span>
      </p>

      {/* Sticky commit bar */}
      <div className="sticky bottom-0 z-30 mt-4">
        <div className="card !py-3 !px-5 border border-primary-100 shadow-card-hover flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
            <div className="flex items-center gap-1.5"><Users className="w-4 h-4 text-primary-500" /><span className="text-gray-500">Selected</span> <b className="text-gray-800 tabular-nums">{sel.count}</b></div>
            <div><span className="text-gray-400 text-[11px] uppercase tracking-wide mr-1">Gross</span><b className="text-gray-800 tabular-nums">{money(sel.gross)}</b></div>
            <div><span className="text-gray-400 text-[11px] uppercase tracking-wide mr-1">Net</span><b className="text-gray-800 tabular-nums">{money(sel.net)}</b></div>
            <div><span className="text-gray-400 text-[11px] uppercase tracking-wide mr-1">{L.cra}</span><b className="text-gray-800 tabular-nums">{money(sel.cra)}</b></div>
          </div>
          {committedRun ? (
            <Link to="/payroll?tab=runs"
              className="flex items-center gap-2 rounded-lg bg-green-600 hover:bg-green-700 text-white font-semibold px-4 py-2 text-sm transition">
              <CheckCircle2 className="w-4 h-4" /> Committed — view run
            </Link>
          ) : (
            <button onClick={commit} disabled={pending || sel.count === 0}
              className="btn-primary flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed">
              <Lock className="w-4 h-4" />
              {pending ? "Committing…" : mode === "offcycle"
                ? `Create ${offTypeLabel(offType).toLowerCase()} · ${sel.count}`
                : `Commit & lock ${sel.count} employee${sel.count !== 1 ? "s" : ""}`}
            </button>
          )}
        </div>
      </div>

      {editEmp && (
        <EntryModal emp={editEmp} components={components} value={entries[editEmp.id]}
          onSave={(v) => setEmpEntry(editEmp.id, v)} onClose={() => setEditEmp(null)} />
      )}
    </>
  );
}

// ── Per-employee pay-component entry (bonus / OT / RRSP / dues for one run) ───
function EntryModal({ emp, components, value, onSave, onClose }: {
  emp: { id: string; name: string }; components?: MasterRow[]; value?: RunEntry;
  onSave: (v: RunEntry | null) => void; onClose: () => void;
}) {
  const active = (components || []).filter((c) => c.is_active);
  const earnComps = active.filter((c) => c.kind === "earning");
  const dedComps = active.filter((c) => c.kind === "deduction");
  const [earnings, setEarnings] = useState<{ code: string; amount: number }[]>(value?.earnings ? [...value.earnings] : []);
  const [deductions, setDeductions] = useState<{ code: string; amount: number }[]>(value?.deductions ? [...value.deductions] : []);

  const flags = (code: string) => {
    const c = active.find((x) => x.code === code);
    if (!c) return [];
    if (c.kind === "deduction") return [c.taxable ? "after-tax" : "pre-tax (cuts income tax)"];
    return [c.pensionable && "CPP", c.insurable && "EI", c.taxable && "taxable",
      c.vacationable && "vacationable", c.non_periodic && "bonus method"].filter(Boolean) as string[];
  };

  const save = () => {
    const e = earnings.filter((r) => r.code && r.amount > 0);
    const d = deductions.filter((r) => r.code && r.amount > 0);
    onSave(e.length || d.length ? { earnings: e, deductions: d } : null);
    onClose();
  };

  const renderList = (
    rows: { code: string; amount: number }[], setRows: (r: { code: string; amount: number }[]) => void,
    opts: MasterRow[], label: string, placeholder: string,
  ) => (
    <div className="rounded-lg border border-surface-border p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{label}</div>
        <button onClick={() => setRows([...rows, { code: opts[0]?.code || "", amount: 0 }])}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-primary-600 hover:text-primary-700">
          <Plus className="w-3 h-3" /> Add
        </button>
      </div>
      {rows.length === 0 ? <p className="text-xs text-gray-400 py-1">{placeholder}</p> : (
        <div className="space-y-2">
          {rows.map((r, i) => (
            <div key={i} className="flex items-center gap-2">
              <select className="input !py-1.5 text-sm flex-1" value={r.code}
                onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, code: e.target.value } : x))}>
                <option value="">Select…</option>
                {opts.map((o) => <option key={o.id} value={o.code || ""}>{o.name}{o.code ? ` (${o.code})` : ""}</option>)}
              </select>
              <div className="relative w-32">
                <span className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400 text-sm">$</span>
                <input className="input !py-1.5 text-sm !pl-5 text-right" type="number" step="0.01" min="0" value={r.amount || ""}
                  onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, amount: parseFloat(e.target.value) || 0 } : x))} />
              </div>
              <button onClick={() => setRows(rows.filter((_, j) => j !== i))} className="icon-btn text-gray-300 hover:text-red-500"><Trash2 className="w-4 h-4" /></button>
            </div>
          ))}
          {rows.map((r, i) => r.code && (
            <div key={`f${i}`} className="flex flex-wrap gap-1 pl-1 -mt-1">
              {flags(r.code).map((f) => <span key={f} className="px-1.5 py-0.5 rounded bg-surface text-[10px] text-gray-500">{f}</span>)}
            </div>
          ))}
        </div>
      )}
    </div>
  );

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[min(80rem,96vw)] max-h-[92vh] overflow-y-auto">
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-5 py-3 flex items-center justify-between">
          <div>
            <h2 className="font-display font-semibold text-gray-800">Pay adjustments · {emp.name}</h2>
            <p className="text-[11px] text-gray-500">One-off earnings & deductions for this run. Statutory impact follows each component's master flags.</p>
          </div>
          <button onClick={onClose} className="icon-btn text-gray-400"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-5 space-y-3">
          {renderList(earnings, setEarnings, earnComps, "Additional earnings", "No extra earnings — base pay only.")}
          {renderList(deductions, setDeductions, dedComps, "Deductions", "No extra deductions.")}
          {active.length === 0 && <p className="text-[11px] text-amber-600">No pay components found. Add them under Master Data → Pay Components (or Load starter set).</p>}
          <div className="flex justify-end gap-2 pt-1">
            <button onClick={onClose} className="btn-secondary text-xs">Cancel</button>
            <button onClick={save} className="btn-primary text-xs">Apply to run</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Pay runs history ────────────────────────────────────────────────────────
function RunsTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["runs"], queryFn: payrollApi.runs });
  if (isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (!data || !data.length) return <div className="card"><Empty>No pay runs yet. Commit one from the <b>Run Payroll</b> tab.</Empty></div>;
  return (
    <div className="card !p-0">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
            <tr>
              <th className="px-5 py-2.5 font-semibold">Pay date</th>
              <th className="px-5 py-2.5 font-semibold">Frequency</th>
              <th className="px-5 py-2.5 font-semibold">Period</th>
              {data.some((r) => r.entity_name) && <th className="px-5 py-2.5 font-semibold">Entity</th>}
              <th className="px-5 py-2.5 font-semibold text-right">Employees</th>
              <th className="px-5 py-2.5 font-semibold text-right">Gross</th>
              <th className="px-5 py-2.5 font-semibold text-right">Net</th>
              <th className="px-5 py-2.5 font-semibold">Status</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.id} onClick={() => onOpen(r.id)} className="border-t border-surface-border hover:bg-gray-50 cursor-pointer">
                <td className="px-5 py-3 font-medium text-gray-800">{r.pay_date}</td>
                <td className="px-5 py-3"><span className="badge-teal">{freqLabel(r.pay_frequency)}</span></td>
                <td className="px-5 py-3 text-gray-500 text-xs">{r.period_start} → {r.period_end}</td>
                {data.some((x) => x.entity_name) && <td className="px-5 py-3 text-xs" title={(r.entities || []).map((x) => `${x.name}: ${x.employees}`).join(" · ")}>
                  {r.entity_id ? <span className="badge-teal">{r.entity_name}</span> : <span className="text-gray-500">All entities{r.entities?.length ? ` (${r.entities.length})` : ""}</span>}</td>}
                <td className="px-5 py-3 text-right tabular-nums text-gray-700">{r.employee_count}</td>
                <td className={`px-5 py-3 text-right tabular-nums ${r.status === "void" ? "text-gray-400 line-through" : "text-gray-700"}`}>{money(r.total_gross)}</td>
                <td className={`px-5 py-3 text-right tabular-nums font-semibold ${r.status === "void" ? "text-gray-400 line-through" : "text-gray-800"}`}>{money(r.total_net)}</td>
                <td className="px-5 py-3">
                  <span className="inline-flex items-center gap-1.5" title={r.status === "void" ? `Voided${r.void?.by ? ` by ${r.void.by}` : ""}${r.void?.reason ? ` — ${r.void.reason}` : ""}` : undefined}>
                    {r.status === "void"
                      ? <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-gray-200 text-gray-700 uppercase">void</span>
                      : <Badge tone="active">{r.status}</Badge>}
                    {r.compliance_mode === "test" && r.status !== "void" && <span className="badge-amber text-[10px] uppercase">test</span>}
                  </span>
                </td>
                <td className="px-3"><ChevronRight className="w-4 h-4 text-gray-300" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RunDetailModal({ runId, onClose, onStub }: { runId: string; onClose: () => void; onStub: (s: string) => void }) {
  const [view, setView] = useState<"stubs" | "register" | "remittance">("stubs");
  const [regDept, setRegDept] = useState<string | null>(null);   // register department segment
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["run", runId], queryFn: () => payrollApi.runDetail(runId) });
  // Void a TEST run (reason required): the YTD ledger is reversed, the period can be re-run.
  const voidRun = useMutation({
    mutationFn: (reason: string) => payrollApi.voidRun(runId, reason),
    onSuccess: (r) => {
      ["runs", "run", "ytd", "run-preview", "payslips"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast.success(`TEST run ${r.period_start} → ${r.period_end} voided — ${r.void.employees} payslips withdrawn, ${r.void.ytd_rows_reversed} YTD rows reversed`);
      (r.warnings || []).forEach((w) => toast(w, { icon: "⚠️", duration: 9000 }));
      onClose();
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail?.message || e?.response?.data?.detail || "Could not void this run", { duration: 8000 }),
  });
  const askVoid = () => {
    const reason = window.prompt(`Void this TEST run (${data?.period_start} → ${data?.period_end})?\n\nThe ${data?.employee_count} payslips are withdrawn, the year-to-date ledger (gross, CPP, EI, tax) is reversed and the period can be run again. Only a TEST run can be voided — never a live one.\n\nReason (required):`, "");
    if (reason === null) return;
    if (reason.trim().length < 3) { toast.error("Give a reason (at least 3 characters)"); return; }
    voidRun.mutate(reason.trim());
  };
  const { data: reg } = useQuery({ queryKey: ["run-register", runId], queryFn: () => payrollApi.register(runId), enabled: view === "register" });
  const { data: rem } = useQuery({ queryKey: ["run-remittance", runId], queryFn: () => payrollApi.remittance(runId), enabled: view === "remittance" });
  const regRows = useMemo(() => {
    const rows = reg?.rows || [];
    return regDept ? rows.filter((r: any) => r.department === regDept) : rows;
  }, [reg, regDept]);
  const VIEWS = [{ k: "stubs", label: "Pay stubs", icon: FileText }, { k: "register", label: "Register", icon: Table2 }, { k: "remittance", label: "Remittance", icon: Landmark }] as const;

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[min(110rem,96vw)] max-h-[92vh] overflow-hidden flex flex-col">
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-5 py-3 flex items-center justify-between">
          <div>
            <h2 className="font-display font-semibold text-gray-800">Pay run · {data ? freqLabel(data.pay_frequency) : "…"}
              {data?.status === "void" && <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-200 text-gray-700 uppercase align-middle">void</span>}
              {data && data.status !== "void" && data.compliance_mode === "test" && <span className="ml-2 badge-amber text-[10px] uppercase align-middle">test run</span>}
            </h2>
            <p className="text-[11px] text-gray-500">{data ? `${data.period_start} → ${data.period_end} · pay date ${data.pay_date}` : ""}</p>
          </div>
          <div className="flex items-center gap-2">
            {data?.can_void && (
              <button className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1.5 text-red-700 border-red-200 hover:bg-red-50" onClick={askVoid} disabled={voidRun.isPending} data-testid="void-run-btn"
                title="Void this TEST run: payslips withdrawn, YTD reversed, the month can be run again">
                <Trash2 className="w-3.5 h-3.5" />{voidRun.isPending ? "Voiding…" : "Void this TEST run"}
              </button>
            )}
            <button onClick={onClose} className="icon-btn text-gray-400"><X className="w-4 h-4" /></button>
          </div>
        </div>
        {data?.status === "void" && (
          <div className="px-5 py-2.5 bg-gray-50 border-b border-surface-border text-xs text-gray-700" data-testid="void-banner">
            <b>Voided</b>{data.void?.at ? ` on ${String(data.void.at).slice(0, 10)}` : ""}{data.void?.by ? ` by ${data.void.by}` : ""}{data.void?.reason ? ` — “${data.void.reason}”` : ""}.
            {" "}{data.void?.employees ?? 0} payslips withdrawn · {data.void?.ytd_rows_reversed ?? 0} YTD rows reversed. The period can be run again.
          </div>
        )}
        <div className="flex gap-1 px-4 pt-3 border-b border-surface-border">
          {VIEWS.map((v) => (
            <button key={v.k} onClick={() => setView(v.k)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-t-lg text-xs font-medium transition-colors ${
                view === v.k ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>
              <v.icon className="w-3.5 h-3.5" />{v.label}
            </button>
          ))}
        </div>
        {isLoading || !data ? <div className="grid place-items-center py-20"><Spinner className="h-8 w-8" /></div> : (
          <div className="overflow-y-auto">
            {view === "stubs" && <>
              <div className="grid grid-cols-3 gap-3 p-4">
                <Kpi label="Employees" value={data.employee_count} />
                <Kpi label="Total gross" value={money(data.total_gross)} />
                <Kpi label="Total net" value={money(data.total_net)} accent />
              </div>
              <table className="w-full text-sm">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr>
                    <th className="px-5 py-2 font-semibold">Employee</th>
                    <th className="px-5 py-2 font-semibold text-right">Gross</th>
                    <th className="px-5 py-2 font-semibold text-right">Deductions</th>
                    <th className="px-5 py-2 font-semibold text-right">Net</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.stubs.map((s) => (
                    <tr key={s.stub_id} onClick={() => onStub(s.stub_id)} className="border-t border-surface-border hover:bg-gray-50 cursor-pointer">
                      <td className="px-5 py-2.5 text-gray-800">{s.name}<span className="text-xs text-gray-400"> · {s.employee_number}</span></td>
                      <td className="px-5 py-2.5 text-right tabular-nums text-gray-700">{money(s.gross)}</td>
                      <td className="px-5 py-2.5 text-right tabular-nums text-gray-500">{money(s.cpp + s.cpp2 + s.ei + s.income_tax)}</td>
                      <td className="px-5 py-2.5 text-right tabular-nums font-semibold text-gray-800">{money(s.net_pay)}</td>
                      <td className="px-3 text-primary-500 text-xs">payslip →</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>}

            {view === "register" && (!reg ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div> : <>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4">
                <Kpi label="Total gross" value={money(reg.totals.gross)} />
                <Kpi label="Deductions" value={money(reg.totals.cpp + reg.totals.cpp2 + reg.totals.ei + reg.totals.income_tax + reg.totals.pre_tax + reg.totals.after_tax)} />
                <Kpi label="Employer cost" value={money(reg.totals.employer_cost)} />
                <Kpi label="Total cost" value={money(reg.totals.total_cost)} accent />
              </div>
              <div className="flex items-center gap-2 px-4 pb-2 text-xs text-gray-400 flex-wrap">
                <span>Click a <b className="text-gray-500">department</b> cell to segment the register</span>
                <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 ml-auto" data-testid="run-register-print" title="Print the payroll register (landscape)"
                  onClick={() => printRegion(document.querySelector<HTMLElement>('[data-print="run-register"]'), { landscape: true })}><Printer className="w-3.5 h-3.5" /> Print register</button>
                {regDept && <button onClick={() => setRegDept(null)} className="inline-flex items-center gap-1 rounded-full bg-primary-50 text-primary-600 px-2 py-0.5 font-medium">Dept: {regDept} · {regRows.length} <X className="w-3 h-3" /></button>}
              </div>
              <div className="overflow-x-auto" data-print="run-register">
                <div className="lx-print-only pb-2 text-sm font-semibold text-gray-800">Payroll register · {data.period_start} → {data.period_end} · pay date {data.pay_date}{regDept ? ` · department ${regDept}` : ""} · {regRows.length} employees · gross {money(reg.totals.gross)} · printed {new Date().toLocaleString("en-CA")}</div>
                <table className="w-full text-sm">
                  <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                    <tr>
                      <th className="px-5 py-2 font-semibold">Employee</th>
                      <th className="px-3 py-2 font-semibold">Dept</th>
                      {["Gross", "CPP", "EI", "Tax", "Net", "Employer"].map((h) => <th key={h} className="px-3 py-2 font-semibold text-right">{h}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {regRows.map((r: any) => (
                      <tr key={r.employee_id} className="border-t border-surface-border">
                        <td className="px-5 py-2 text-gray-800">{r.name}<span className="text-xs text-gray-400"> · {r.employee_number}</span></td>
                        <td onClick={() => setRegDept(r.department)} title="Segment by department"
                          className={`px-3 py-2 text-xs cursor-pointer hover:text-primary-600 hover:underline ${regDept === r.department ? "text-primary-600 font-medium" : "text-gray-500"}`}>{r.department}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-gray-700">{money(r.gross)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-gray-500">{money(r.cpp + r.cpp2)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-gray-500">{money(r.ei)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-gray-500">{money(r.income_tax)}</td>
                        <td className="px-3 py-2 text-right tabular-nums font-semibold text-gray-800">{money(r.net)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-gray-500">{money(r.er_cpp + r.er_cpp2 + r.er_ei)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {reg.by_department?.length > 1 && (
                <div className="p-4">
                  <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">By department (for the GL journal)</div>
                  <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2">
                    {reg.by_department.map((d: any) => (
                      <div key={d.department} className="rounded-lg border border-surface-border px-3 py-2 text-sm">
                        <div className="font-medium text-gray-800">{d.department} <span className="text-xs text-gray-400">· {d.employees}</span></div>
                        <div className="text-xs text-gray-500 tabular-nums">gross {money(d.gross)} · net {money(d.net)} · er {money(d.employer_cost)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>)}

            {view === "remittance" && (!rem ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div> : (
              <div className="p-5 space-y-4 max-w-2xl mx-auto">
                <p className="text-xs text-gray-500">CRA source-deduction remittance (PD7A) for pay date <b>{rem.pay_date}</b> — this is what's owed to the Canada Revenue Agency for this run.</p>
                <div className="rounded-xl border border-surface-border overflow-hidden">
                  <RemRow label="Federal & provincial income tax" value={rem.income_tax} />
                  <RemRow label="CPP — employee" value={rem.cpp_employee} sub />
                  <RemRow label="CPP — employer" value={rem.cpp_employer} sub />
                  <RemRow label="CPP total" value={rem.cpp_total} />
                  <RemRow label="EI — employee" value={rem.ei_employee} sub />
                  <RemRow label="EI — employer (1.4×)" value={rem.ei_employer} sub />
                  <RemRow label="EI total" value={rem.ei_total} />
                </div>
                <div className="rounded-xl bg-gradient-to-r from-primary-600 to-teal-500 text-white px-5 py-4 flex items-center justify-between">
                  <span className="font-semibold">Total remittance to CRA</span>
                  <span className="text-2xl font-bold tabular-nums">{money(rem.total_remittance)}</span>
                </div>
                <p className="text-[10px] text-gray-400">Remittance due date depends on your CRA remitter type (regular / quarterly / accelerated). Statutory constants pending CRA verification.</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RemRow({ label, value, sub }: { label: string; value: number; sub?: boolean }) {
  return (
    <div className={`flex items-center justify-between px-4 py-2.5 border-b border-surface-border last:border-0 ${sub ? "pl-8 bg-surface/40" : ""}`}>
      <span className={`text-sm ${sub ? "text-gray-500" : "text-gray-700 font-medium"}`}>{label}</span>
      <span className={`tabular-nums ${sub ? "text-gray-500 text-sm" : "text-gray-800 font-semibold"}`}>{money(value)}</span>
    </div>
  );
}

// ── YTD ─────────────────────────────────────────────────────────────────────
function YtdTab({ onEdit }: { onEdit: (r: YtdRow) => void }) {
  const [year, setYear] = useState(2026);
  const { data, isLoading } = useQuery({ queryKey: ["ytd", year], queryFn: () => payrollApi.ytd(year), placeholderData: (p: any) => p });
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-display font-semibold text-gray-800">Year-to-date ledger · {year}
          <span className="text-xs font-normal text-gray-400"> — the T4 in progress</span></h3>
        <YearPicker year={year} setYear={setYear} />
      </div>
      {isLoading && !data ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : !data || !data.employees.length ? <div className="card"><Empty>No YTD for {year} — run payroll, or set opening balances after a run.</Empty></div>
        : <YtdBody data={data} onEdit={onEdit} year={year} />}
    </div>
  );
}

const YTD_COLS = [
  { k: "gross", label: "Gross · Box 14" },
  { k: "pensionable", label: "Pensionable · 26" },
  { k: "cpp", label: "CPP · 16", cap: "cpp" },
  { k: "cpp2", label: "CPP2 · 16A", cap: "cpp2" },
  { k: "ei", label: "EI · 18", cap: "ei" },
  { k: "insurable", label: "Insurable · 24" },
  { k: "income_tax", label: "Income tax · 22" },
  { k: "deductions", label: "Total deductions" },
  { k: "vac_balance", label: "Vacation owed" },
];

function YtdBody({ data, onEdit, year }: { data: any; onEdit: (r: YtdRow) => void; year: number }) {
  const { data: emps } = useQuery({ queryKey: ["employees"], queryFn: employeeApi.list });
  const meta = useMemo<Record<string, any>>(() => Object.fromEntries((emps || []).map((e: any) => [e.id, e])), [emps]);
  const [sort, setSort] = useState<{ k: string; dir: 1 | -1 }>({ k: "gross", dir: -1 });
  const [seg, setSeg] = useState<{ k: string; label: string; v: string; disp: string } | null>(null);
  const [drill, setDrill] = useState<any | null>(null);
  const caps: any = data.caps || {};

  const rows = useMemo(() => {
    let enriched = data.employees.map((e: any) => ({
      ...e,
      department: meta[e.employee_id]?.department || "—",
      province: meta[e.employee_id]?.province_of_employment || "—",
      freq: meta[e.employee_id]?.pay_frequency || "biweekly",
      deductions: +(e.cpp + e.cpp2 + e.ei + e.income_tax).toFixed(2),
    }));
    if (seg) enriched = enriched.filter((r: any) => String(r[seg.k]) === seg.v);
    return enriched.sort((a: any, b: any) => {
      const va = a[sort.k], vb = b[sort.k];
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * sort.dir;
      return String(va ?? "").localeCompare(String(vb ?? "")) * sort.dir;
    });
  }, [data, meta, sort, seg]);

  const totals = useMemo(() => YTD_COLS.reduce((acc, c) => {
    acc[c.k] = rows.reduce((s: number, r: any) => s + (r[c.k] || 0), 0); return acc;
  }, {} as Record<string, number>), [rows]);

  const toggleSort = (k: string) => setSort((s) => s.k === k ? { k, dir: (s.dir === 1 ? -1 : 1) as 1 | -1 } : { k, dir: -1 });
  const arrow = (k: string) => sort.k === k ? (sort.dir === 1 ? " ▲" : " ▼") : "";
  const atCap = (r: any, key?: string) => !!key && caps[key] && r[key] >= caps[key] - 0.01;

  const exportCsv = () => {
    const esc = (v: any) => `"${String(v ?? "").replace(/"/g, '""')}"`;
    const head = ["Employee", "Emp #", "Department", "Province", "Frequency", ...YTD_COLS.map((c) => c.label.replace(/ · .*/, ""))];
    const lines = [head.join(",")];
    rows.forEach((r: any) => lines.push([r.name, r.employee_number, r.department, r.province, freqLabel(r.freq), ...YTD_COLS.map((c) => (r[c.k] ?? 0).toFixed(2))].map(esc).join(",")));
    lines.push(["TOTALS", "", "", "", "", ...YTD_COLS.map((c) => totals[c.k].toFixed(2))].map(esc).join(","));
    const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `lexora-ytd-${year}.csv`; a.click(); URL.revokeObjectURL(url);
  };

  const stHead = "sticky top-0 z-20 bg-primary-700 text-white px-3 py-2 border-l border-primary-800/60 cursor-pointer whitespace-nowrap select-none";
  const cellBorder = "border-l border-b border-surface-border/70";

  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Kpi label="Employees" value={rows.length} />
        <Kpi label="YTD gross" value={money(totals.gross)} />
        <Kpi label="YTD income tax" value={money(totals.income_tax)} />
        <Kpi label="YTD deductions" value={money(totals.deductions)} accent />
      </div>
      <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
        <div className="flex items-center gap-2 text-xs text-gray-400 flex-wrap">
          <span>{rows.length} employees · header sorts · click a <b className="text-gray-500">dimension</b> cell to segment, a <b className="text-gray-500">value</b> to drill in</span>
          {seg && <button onClick={() => setSeg(null)} className="inline-flex items-center gap-1 rounded-full bg-primary-50 text-primary-600 px-2 py-0.5 font-medium">{seg.label}: {seg.disp} <X className="w-3 h-3" /></button>}
        </div>
        <span className="flex items-center gap-2">
          <button onClick={exportCsv} className="btn-secondary text-xs flex items-center gap-1.5"><Download className="w-3.5 h-3.5" /> Export CSV</button>
          <button onClick={() => printRegion(document.querySelector<HTMLElement>('[data-print="ytd-register"]'), { landscape: true })} className="btn-secondary text-xs flex items-center gap-1.5" title="Print the year-to-date register (A4 landscape)" data-testid="ytd-print"><Printer className="w-3.5 h-3.5" /> Print</button>
        </span>
      </div>
      <div className="rounded-xl border border-surface-border overflow-auto max-h-[68vh] bg-white shadow-card" data-print="ytd-register">
        <div className="lx-print-only pb-2 text-sm font-semibold text-gray-800">Year-to-date payroll register · {year} · {rows.length} employees{seg ? ` · ${seg.label}: ${seg.disp}` : ""} · printed {new Date().toLocaleString("en-CA")}</div>
        <table className="text-xs border-separate w-full" style={{ borderSpacing: 0, minWidth: 1180 }}>
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-30 bg-primary-700 text-white text-left px-3 py-2 border-r border-primary-800" style={{ minWidth: 190 }}>Employee</th>
              {[["Department", "department"], ["Prov", "province"], ["Frequency", "freq"]].map(([h, k]) => (
                <th key={k} onClick={() => toggleSort(k)} className={stHead + " text-left"}>{h}{arrow(k)}</th>
              ))}
              {YTD_COLS.map((c) => <th key={c.k} onClick={() => toggleSort(c.k)} className={stHead + " text-right"}>{c.label}{arrow(c.k)}</th>)}
              <th className="sticky top-0 z-20 bg-primary-700 px-2 py-2 border-l border-primary-800/60" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r: any, i: number) => (
              <tr key={r.employee_id} className="group">
                <th scope="row" onClick={() => setDrill(r)} title="Drill into this employee"
                  className={`sticky left-0 z-10 ${i % 2 ? "bg-gray-50" : "bg-white"} group-hover:bg-primary-50 text-left font-normal px-3 py-1.5 border-r border-b border-surface-border cursor-pointer`} style={{ minWidth: 190 }}>
                  <div className="font-medium text-gray-800 whitespace-nowrap group-hover:text-primary-600">{r.name}</div>
                  <div className="text-[10px] text-gray-400">{r.employee_number}</div>
                </th>
                <td onClick={() => setSeg({ k: "department", label: "Department", v: r.department, disp: r.department })} title="Segment by department"
                  className={`px-3 py-1.5 ${cellBorder} text-gray-600 whitespace-nowrap cursor-pointer hover:!bg-primary-50 hover:text-primary-600 ${i % 2 ? "bg-gray-50/40" : ""}`}>{r.department}</td>
                <td onClick={() => setSeg({ k: "province", label: "Province", v: r.province, disp: r.province })} title="Segment by province"
                  className={`px-3 py-1.5 ${cellBorder} text-gray-600 cursor-pointer hover:!bg-primary-50 hover:text-primary-600 ${i % 2 ? "bg-gray-50/40" : ""}`}>{r.province}</td>
                <td onClick={() => setSeg({ k: "freq", label: "Frequency", v: r.freq, disp: freqLabel(r.freq) })} title="Segment by frequency"
                  className={`px-3 py-1.5 ${cellBorder} text-gray-500 whitespace-nowrap cursor-pointer hover:!bg-primary-50 hover:text-primary-600 ${i % 2 ? "bg-gray-50/40" : ""}`}>{freqLabel(r.freq)}</td>
                {YTD_COLS.map((c) => (
                  <td key={c.k} onClick={() => setDrill(r)} title={atCap(r, (c as any).cap) ? "At annual maximum · click to drill" : "Click to drill into this employee's records"}
                    className={`px-3 py-1.5 text-right tabular-nums ${cellBorder} whitespace-nowrap cursor-pointer ${atCap(r, (c as any).cap) ? "bg-green-50 text-green-700 font-medium" : `text-gray-700 ${i % 2 ? "bg-gray-50/40" : ""}`} hover:!bg-primary-50`}>{money(r[c.k])}</td>
                ))}
                <td className={`px-2 py-1.5 text-right ${cellBorder} ${i % 2 ? "bg-gray-50/40" : ""}`}>
                  <button onClick={() => onEdit(r)} className="icon-btn text-gray-300 hover:text-primary-600" title="Adjust YTD"><Pencil className="w-3.5 h-3.5" /></button>
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="font-semibold">
              <th className="sticky left-0 bottom-0 z-20 bg-surface text-left px-3 py-2 border-r border-t-2 border-surface-border" style={{ minWidth: 190 }}>Totals · {rows.length}</th>
              <td className="sticky bottom-0 bg-surface border-l border-t-2 border-surface-border" colSpan={3} />
              {YTD_COLS.map((c) => <td key={c.k} className="sticky bottom-0 bg-surface px-3 py-2 text-right tabular-nums border-l border-t-2 border-surface-border text-gray-800 whitespace-nowrap">{money(totals[c.k])}</td>)}
              <td className="sticky bottom-0 bg-surface border-l border-t-2 border-surface-border" />
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="text-[10px] text-gray-400 mt-2">Box numbers reference the CRA T4. Green cells are at the annual CPP / CPP2 / EI maximum. Pensionable / insurable are shown uncapped here; the T4 caps box 24/26 at MIE / YMPE.</p>
      {drill && <YtdDrillModal row={drill} year={year} onClose={() => setDrill(null)} />}
    </>
  );
}

// Data-mining drill-through: the source payslips behind an employee's YTD totals.
function YtdDrillModal({ row, year, onClose }: { row: any; year: number; onClose: () => void }) {
  const { data: slips, isLoading } = useQuery({ queryKey: ["employee-ledger", row.employee_id, year], queryFn: () => payrollApi.employeeLedger(row.employee_id, year) });
  const yslips = slips || [];   // already filtered to the year + ordered by pay date, server-side
  const net = +(row.gross - row.deductions).toFixed(2);
  const METRICS: [string, number, boolean?][] = [
    ["Gross · Box 14", row.gross], ["Pensionable · 26", row.pensionable],
    ["CPP · 16", row.cpp], ["CPP2 · 16A", row.cpp2], ["EI · 18", row.ei],
    ["Insurable · 24", row.insurable], ["Income tax · 22", row.income_tax],
    ["Total deductions", row.deductions], ["Net (est.)", net, true],
  ];
  // Running totals for the source-record audit trail — each Σ column sums to the YTD figure above.
  let rGross = 0, rCpp = 0, rEi = 0, rTax = 0, rNet = 0;
  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[min(84rem,97vw)] max-h-[94vh] overflow-hidden flex flex-col">
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-5 py-3 flex items-center justify-between">
          <div>
            <h2 className="font-display font-semibold text-gray-800">Data mining · {row.name}</h2>
            <p className="text-[11px] text-gray-500">{row.employee_number} · {row.department} · {row.province} · {freqLabel(row.freq)} · {year}</p>
          </div>
          <div className="flex items-center gap-2">
            <Link to={`/employees?q=${encodeURIComponent(row.employee_number || row.name)}`} className="btn-secondary text-xs">Open profile</Link>
            <button onClick={onClose} className="icon-btn text-gray-400"><X className="w-4 h-4" /></button>
          </div>
        </div>
        <div className="overflow-y-auto p-5 space-y-4">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">YTD figures (the aggregated totals)</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {METRICS.map(([label, val, accent]) => (
                <div key={label} className={`rounded-lg border px-3 py-2 ${accent ? "border-primary-200 bg-primary-50/40" : "border-surface-border"}`}>
                  <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{label}</div>
                  <div className={`text-sm font-bold tabular-nums ${accent ? "text-primary-600" : "text-gray-800"}`}>{money(val)}</div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">
              Source payslips ({yslips.length}) — every CPP / EI / tax dollar traced to its pay stub
            </div>
            {isLoading ? <div className="grid place-items-center py-10"><Spinner className="h-6 w-6" /></div>
              : !yslips.length ? <Empty>No payslips found for {year}.</Empty>
              : (
                <div className="rounded-xl border border-surface-border overflow-auto max-h-[46vh]">
                  <table className="text-xs border-separate w-full" style={{ borderSpacing: 0, minWidth: 820 }}>
                    <thead>
                      <tr>
                        <th className="sticky top-0 bg-surface text-gray-500 px-3 py-2 font-semibold border-b border-surface-border text-left">#</th>
                        <th className="sticky top-0 bg-surface text-gray-500 px-3 py-2 font-semibold border-b border-surface-border text-left">Pay date</th>
                        {["Gross", "CPP", "EI", "Tax", "Net"].map((h) => (
                          <th key={h} className="sticky top-0 bg-surface text-gray-500 px-3 py-2 font-semibold border-b border-surface-border text-right">{h}</th>
                        ))}
                        {["Σ Gross", "Σ CPP", "Σ EI", "Σ Tax"].map((h, k) => (
                          <th key={h} className={`sticky top-0 bg-surface text-gray-400 px-3 py-2 font-semibold border-b border-surface-border text-right whitespace-nowrap ${k === 0 ? "border-l" : ""}`}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {yslips.map((s: any, j: number) => {
                        const cpp = (s.cpp || 0) + (s.cpp2 || 0);
                        rGross += s.gross || 0; rCpp += cpp; rEi += s.ei || 0; rTax += s.income_tax || 0; rNet += s.net_pay || 0;
                        return (
                          <tr key={s.stub_id} className="hover:bg-gray-50">
                            <td className="px-3 py-1.5 text-gray-400 border-b border-surface-border/60">{j + 1}</td>
                            <td className="px-3 py-1.5 text-gray-700 border-b border-surface-border/60 whitespace-nowrap">{s.pay_date}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-gray-700 border-b border-surface-border/60">{money(s.gross)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-gray-600 border-b border-surface-border/60">{money(cpp)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-gray-600 border-b border-surface-border/60">{money(s.ei)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-gray-600 border-b border-surface-border/60">{money(s.income_tax)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums font-medium text-gray-800 border-b border-surface-border/60">{money(s.net_pay)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-gray-400 border-b border-l border-surface-border/60">{money(rGross)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-gray-400 border-b border-surface-border/60">{money(rCpp)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-gray-400 border-b border-surface-border/60">{money(rEi)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums text-gray-400 border-b border-surface-border/60">{money(rTax)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                    <tfoot>
                      <tr className="font-semibold bg-surface">
                        <td className="px-3 py-2 border-t border-surface-border" colSpan={2}>{yslips.length} payslips</td>
                        <td className="px-3 py-2 text-right tabular-nums border-t border-surface-border">{money(rGross)}</td>
                        <td className="px-3 py-2 text-right tabular-nums border-t border-surface-border">{money(rCpp)}</td>
                        <td className="px-3 py-2 text-right tabular-nums border-t border-surface-border">{money(rEi)}</td>
                        <td className="px-3 py-2 text-right tabular-nums border-t border-surface-border">{money(rTax)}</td>
                        <td className="px-3 py-2 text-right tabular-nums border-t border-surface-border">{money(rNet)}</td>
                        <td className="px-3 py-2 border-t border-l border-surface-border" colSpan={4} />
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}
            <p className="text-[10px] text-gray-400 mt-2">Each running total (Σ) accumulates its column down to the YTD figure above — CPP reconciles to Box 16 + 16A combined. This is the audit trail behind every deduction on the ledger.</p>
          </div>
        </div>
      </div>
    </div>
  );
}

function YtdEditModal({ row, onClose }: { row: YtdRow; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState({ gross: row.gross, income_tax: row.income_tax, cpp: row.cpp, cpp2: row.cpp2, ei: row.ei, pensionable: row.pensionable, insurable: row.insurable });
  const save = useMutation({
    mutationFn: () => payrollApi.ytdPatch(row.employee_id, { year: row.year, ...f }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["ytd"] }); toast.success("YTD updated"); onClose(); },
    onError: () => toast.error("Update failed"),
  });
  const FIELDS: [keyof typeof f, string][] = [["gross", "Gross"], ["income_tax", "Income tax"], ["cpp", "CPP"], ["cpp2", "CPP2"], ["ei", "EI"], ["pensionable", "Pensionable"], ["insurable", "Insurable"]];
  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[min(72rem,96vw)] max-h-[92vh] overflow-y-auto">
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-5 py-3 flex items-center justify-between">
          <div><h2 className="font-display font-semibold text-gray-800">Adjust YTD · {row.name}</h2><p className="text-[11px] text-gray-500">Set opening / corrected balances for {row.year}</p></div>
          <button onClick={onClose} className="icon-btn text-gray-400"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-5">
          <div className="grid grid-cols-2 gap-3">
            {FIELDS.map(([k, label]) => (
              <div key={k}><label className="block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1">{label}</label>
                <input className="input" type="number" step="0.01" value={f[k]} onChange={(e) => setF((s) => ({ ...s, [k]: parseFloat(e.target.value) || 0 }))} /></div>
            ))}
          </div>
          <p className="text-[11px] text-amber-600 mt-3">⚠ Adjusting YTD changes cap headroom for the rest of the year — use for mid-year onboarding from a prior provider.</p>
          <div className="flex justify-end gap-2 mt-4">
            <button onClick={onClose} className="btn-secondary text-xs">Cancel</button>
            <button onClick={() => save.mutate()} disabled={save.isPending} className="btn-primary text-xs disabled:opacity-60">{save.isPending ? "Saving…" : "Save YTD"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Payslips ────────────────────────────────────────────────────────────────
function PayslipsTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["payslips"], queryFn: () => payrollApi.payslips() });
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  const filtered = useMemo(() => (data || []).filter((p) => {
    const s = q.toLowerCase().trim();
    return !s || `${p.name || ""} ${p.pay_date || ""}`.toLowerCase().includes(s);
  }), [data, q]);

  const total = filtered.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const curPage = Math.min(page, pageCount);
  const paged = filtered.slice((curPage - 1) * pageSize, curPage * pageSize);
  // reset to page 1 whenever the search / page-size changes
  useEffect(() => { setPage(1); }, [q, pageSize]);

  if (isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (!data || !data.length) return <div className="card"><Empty>No payslips yet — run payroll to generate them.</Empty></div>;
  return (
    <div className="card !p-0">
      <div className="px-4 py-3 border-b border-surface-border flex items-center gap-2">
        <Search className="w-4 h-4 text-gray-400 shrink-0" />
        <input className="bg-transparent outline-none text-sm flex-1 text-gray-800"
          placeholder="Search employee name or pay date…" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="text-xs text-gray-400">{total} payslip{total !== 1 ? "s" : ""}</span>
      </div>
      {total === 0 ? <Empty>No payslips match your search.</Empty> : (
        <>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Employee</th>
                <th className="px-5 py-2.5 font-semibold">Pay date</th>
                <th className="px-5 py-2.5 font-semibold">Frequency</th>
                <th className="px-5 py-2.5 font-semibold text-right">Gross</th>
                <th className="px-5 py-2.5 font-semibold text-right">Net</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {paged.map((p) => (
                <tr key={p.stub_id} onClick={() => onOpen(p.stub_id)} className="border-t border-surface-border hover:bg-gray-50 cursor-pointer">
                  <td className="px-5 py-3 font-medium text-gray-800">{p.name}</td>
                  <td className="px-5 py-3 text-gray-500">{p.pay_date}</td>
                  <td className="px-5 py-3"><span className="badge-teal">{freqLabel(p.pay_frequency)}</span></td>
                  <td className="px-5 py-3 text-right tabular-nums text-gray-700">{money(p.gross)}</td>
                  <td className="px-5 py-3 text-right tabular-nums font-semibold text-gray-800">{money(p.net_pay)}</td>
                  <td className="px-3 text-primary-500 text-xs">view →</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="flex flex-col sm:flex-row items-center justify-between gap-2 px-4 py-3 border-t border-surface-border text-xs text-gray-500">
          <div className="flex items-center gap-2">
            <span>Rows per page</span>
            <select className="input !py-1 !w-auto text-xs" value={pageSize} onChange={(e) => setPageSize(+e.target.value)}>
              {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <div className="tabular-nums">Showing {(curPage - 1) * pageSize + 1}–{Math.min(curPage * pageSize, total)} of {total}</div>
          <div className="flex items-center gap-1">
            <button disabled={curPage <= 1} onClick={() => setPage(1)} className="icon-btn disabled:opacity-30" title="First page"><ChevronsLeft className="w-4 h-4" /></button>
            <button disabled={curPage <= 1} onClick={() => setPage(curPage - 1)} className="icon-btn disabled:opacity-30" title="Previous"><ChevronLeft className="w-4 h-4" /></button>
            <span className="px-2 tabular-nums">Page {curPage} / {pageCount}</span>
            <button disabled={curPage >= pageCount} onClick={() => setPage(curPage + 1)} className="icon-btn disabled:opacity-30" title="Next"><ChevronRight className="w-4 h-4" /></button>
            <button disabled={curPage >= pageCount} onClick={() => setPage(pageCount)} className="icon-btn disabled:opacity-30" title="Last page"><ChevronsRight className="w-4 h-4" /></button>
          </div>
        </div>
        </>
      )}
    </div>
  );
}

// ── Calculator (frequency preview) ──────────────────────────────────────────
function CalcTab() {
  const [salary, setSalary] = useState(78000);
  const [freq, setFreq] = useState("biweekly");
  const [prov, setProv] = useState("ON");
  const run = useMutation({ mutationFn: () => payrollApi.previewAnnual({ annual_salary: salary, pay_frequency: freq, province: prov }) });
  const r: AnnualPreview | undefined = run.data;
  const p1 = r?.periods?.[0];
  return (
    <div className="grid lg:grid-cols-3 gap-4">
      <div className="card">
        <label className="block text-xs font-semibold text-gray-500 mb-1">Annual salary ($)</label>
        <input className="input" type="number" step="1000" value={salary} onChange={(e) => setSalary(parseFloat(e.target.value) || 0)} />
        <label className="block text-xs font-semibold text-gray-500 mb-1 mt-4">Pay frequency</label>
        <div className="grid grid-cols-2 gap-2">
          {PAY_FREQUENCIES.map((f) => (
            <button key={f.value} onClick={() => setFreq(f.value)}
              className={`rounded-lg px-2 py-2 text-xs font-semibold border transition-colors ${freq === f.value ? "bg-primary-600 text-white border-primary-600" : "bg-white text-gray-700 border-surface-border hover:border-primary-300"}`}>
              {f.label}<div className="text-[10px] font-normal opacity-80">{f.periods}/yr</div>
            </button>
          ))}
        </div>
        <label className="block text-xs font-semibold text-gray-500 mb-1 mt-4">Province</label>
        <select className="input" value={prov} onChange={(e) => setProv(e.target.value)}>{PROVINCES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</select>
        <button className="btn-primary w-full mt-5 disabled:opacity-60" onClick={() => run.mutate()} disabled={run.isPending}>{run.isPending ? "Calculating…" : "Calculate net pay"}</button>
      </div>
      <div className="card lg:col-span-2">
        <h3 className="font-display font-semibold text-gray-800 mb-1">Per-period paystub</h3>
        {!r || !p1 ? <div className="grid place-items-center text-center py-20 text-sm text-gray-400">Enter details and press <span className="mx-1 font-semibold text-primary-600">Calculate</span>.</div> : (
          <>
            <p className="text-xs text-gray-400 mb-4">{freqLabel(r.pay_frequency)} · {r.periods_per_year} periods/yr · gross {money(r.gross_per_period)}/period</p>
            <div className="space-y-1.5">
              {[["Gross", money(p1.gross), false], ["CPP", `− ${money(p1.cpp)}`, false], ["CPP2", `− ${money(p1.cpp2)}`, p1.cpp2 === 0], ["EI", `− ${money(p1.ei)}`, false], ["Income tax", `− ${money(p1.income_tax)}`, false]].map(([l, v, m]: any) => (
                <div key={l} className="flex justify-between text-sm"><span className={m ? "text-gray-400" : "text-gray-700"}>{l}</span><span className={`tabular-nums ${m ? "text-gray-400" : "text-gray-800"}`}>{v}</span></div>
              ))}
              <div className="border-t border-surface-border my-2" />
              <div className="flex justify-between"><span className="font-bold text-gray-800">Net pay</span><span className="tabular-nums font-bold text-primary-600">{money(p1.net)}</span></div>
            </div>
            <div className="mt-4 rounded-lg p-3 bg-gradient-to-r from-primary-50 to-teal-50 grid grid-cols-3 gap-3">
              {[["CPP", r.year_end_ytd.cpp, CAPS.cpp], ["CPP2", r.year_end_ytd.cpp2, CAPS.cpp2], ["EI", r.year_end_ytd.ei, CAPS.ei]].map(([l, v, c]: any) => (
                <div key={l}><div className="text-[11px] font-semibold text-primary-700">{l} YTD</div><div className="text-sm tabular-nums text-gray-800">{money(v)}</div><div className="text-[10px] text-gray-400">cap {money(c)}</div></div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── Year-end: T4 · Remittance schedule · ROE ────────────────────────────────
function YearEndTab() {
  const [params, setParams] = useSearchParams();
  const sub = (params.get("ye") as "t4" | "rl1" | "remit" | "roe" | "eht" | "compliance") || "t4";
  const setSub = (s: string) => { const n = new URLSearchParams(params); n.set("ye", s); setParams(n, { replace: true }); };
  const YE = [{ k: "t4", label: "T4 Slips", icon: FileText }, { k: "rl1", label: "RL-1 (Québec)", icon: FileText }, { k: "remit", label: "Remittance Schedule", icon: Landmark }, { k: "roe", label: "Records of Employment", icon: Award }, { k: "eht", label: "Employer Health Tax", icon: CircleDollarSign }, { k: "compliance", label: "Compliance", icon: ShieldCheck }] as const;
  return (
    <div>
      <div className="flex gap-1 mb-4 overflow-x-auto">
        {YE.map((v) => (
          <button key={v.k} onClick={() => setSub(v.k)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors ${
              sub === v.k ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>
            <v.icon className="w-3.5 h-3.5" />{v.label}
          </button>
        ))}
      </div>
      {sub === "t4" && <T4View />}
      {sub === "rl1" && <Rl1View />}
      {sub === "remit" && <RemitScheduleView />}
      {sub === "roe" && <RoeView />}
      {sub === "eht" && <EhtView />}
      {sub === "compliance" && <ComplianceView />}
    </div>
  );
}

// ── Employer Health Tax — employer-side payroll tax per province on total payroll ──
function EhtView() {
  const [year] = useState(2026);
  const { data, isLoading } = useQuery({ queryKey: ["eht", year], queryFn: () => payrollApi.eht(year) });
  if (isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (!data) return <div className="card"><Empty>Employer Health Tax unavailable.</Empty></div>;
  const applic = (data.provinces || []).filter((p: any) => p.applicable);
  return (
    <>
      <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-[12px] text-amber-800 flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
        <span><b>Sourced (2026), confirm before remitting.</b> Employer Health Tax is an employer-cost remittance (not employee withholding), on total annual payroll per province. Rates/exemptions reconciled against provincial finance ministries — ON &amp; MB primary-source, BC/NL/QC cross-checked. Re-confirm the figure before you remit, especially Quebec's mid-band rate.</span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <StatTile label="Total payroll (YTD)" value={cad2(data.total_payroll)} icon={Users} />
        <StatTile label="Total EHT (est.)" value={cad2(data.total_eht)} icon={CircleDollarSign} accent />
        <StatTile label="Taxing provinces" value={data.applicable_count} sub="of provinces with payroll" />
        <StatTile label="Year" value={data.year} />
      </div>
      <div className="card !p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Province</th>
                <th className="px-4 py-2.5 font-semibold">Employer health tax</th>
                <th className="px-4 py-2.5 font-semibold text-right">Total payroll</th>
                <th className="px-4 py-2.5 font-semibold text-right">Exemption</th>
                <th className="px-4 py-2.5 font-semibold text-right">Eff. rate</th>
                <th className="px-4 py-2.5 font-semibold text-right">EHT (est.)</th>
              </tr>
            </thead>
            <tbody>
              {(data.provinces || []).map((p: any) => (
                <tr key={p.province} className="border-t border-surface-border">
                  <td className="px-5 py-2.5 font-medium text-gray-800">{p.province}</td>
                  <td className="px-4 py-2.5 text-gray-600 text-xs">{p.applicable ? p.name : <span className="text-gray-400">No employer health tax</span>}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-600">{cad2(p.payroll)}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-500">{p.applicable ? cad2(p.exemption || 0) : "—"}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums text-gray-500">{p.applicable ? `${(p.effective_rate * 100).toFixed(3)}%` : "—"}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums font-semibold text-gray-800">{p.applicable ? cad2(p.tax) : "—"}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-surface-border bg-surface/50 font-semibold">
                <td className="px-5 py-2.5" colSpan={2}>Total</td>
                <td className="px-4 py-2.5 text-right tabular-nums">{cad2(data.total_payroll)}</td>
                <td /><td />
                <td className="px-4 py-2.5 text-right tabular-nums text-primary-600">{cad2(data.total_eht)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
      {!applic.length && <p className="text-[12px] text-gray-400 mt-3">No employer-health-tax province has payroll yet (AB/SK/NB/NS/PE and the territories don't levy one; ON/BC/MB/NL/QC do, often above a large exemption).</p>}
    </>
  );
}

// ── Verification Center — run each gated case in PDOC/WebRAS, paste the result,
//    confirm the engine matches to the cent (the gate-closing check, no Python). ──
const cad2 = (n: number) => "$" + (Number(n) || 0).toLocaleString("en-CA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const QC_FIELDS: [string, string][] = [["qpp", "QPP"], ["qpp2", "QPP2"], ["qpip", "QPIP"], ["qc_tax", "QC tax"], ["fed", "Federal"], ["ei", "EI"], ["net", "Net"]];
const PROV_FIELDS: [string, string][] = [["cpp", "CPP+CPP2"], ["ei", "EI"], ["tax", "Tax"], ["net", "Net"]];

function VerificationCenter() {
  const { data, isLoading } = useQuery({ queryKey: ["verification"], queryFn: () => payrollApi.verification() });
  if (isLoading) return <div className="card mb-4 grid place-items-center py-8"><Spinner className="h-6 w-6" /></div>;
  if (!data) return null;
  return (
    <div className="card mb-4">
      <div className="flex items-center gap-2 mb-1"><ShieldCheck className="w-4 h-4 text-primary-500" />
        <h3 className="font-display font-semibold text-gray-800 text-sm">Verification Center</h3></div>
      <p className="text-[11px] text-gray-400 mb-3">Run each case in the linked authoritative calculator, paste its per-period figures, and the engine is checked to the cent (✓ = matches). This is the gate-closing reconciliation — no Python editing.</p>
      <VSection label="Quebec" sub={data.quebec.source} url={data.quebec.url} cases={data.quebec.cases} fields={QC_FIELDS} />
      <VSection label="Provinces & territories" sub={data.provinces.source} url={data.provinces.url} cases={data.provinces.cases} fields={PROV_FIELDS} />
    </div>
  );
}

function VSection({ label, sub, url, cases, fields }: { label: string; sub: string; url: string; cases: any[]; fields: [string, string][] }) {
  const confirmed = cases.filter((c) => c.confirmed).length;
  return (
    <div className="mb-4">
      <div className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
        <div><span className="font-semibold text-gray-700 text-sm">{label}</span> <span className="text-[11px] text-gray-400">· {sub}</span></div>
        <a href={url} target="_blank" rel="noopener noreferrer" className="text-[11px] text-primary-600 underline">Open calculator ↗</a>
      </div>
      <div className="text-[11px] text-gray-500 mb-2 tabular-nums">{confirmed}/{cases.length} cases confirmed</div>
      <div className="space-y-2">{cases.map((c, i) => <VCase key={i} c={c} fields={fields} />)}</div>
    </div>
  );
}

function VCase({ c, fields }: { c: any; fields: [string, string][] }) {
  const [paste, setPaste] = useState<Record<string, string>>({});
  const eng = c.engine || {};
  if (eng.error) return <div className="rounded-lg border border-surface-border px-3 py-2 text-xs text-rose-600">{c.name}: engine error — {eng.error}</div>;
  const rows = fields.filter(([k]) => eng[k] !== undefined);
  const match = (k: string) => { const p = parseFloat(paste[k]); return isNaN(p) ? null : Math.abs(p - (eng[k] ?? 0)) <= 0.02; };
  const anyPasted = rows.some(([k]) => (paste[k] ?? "") !== "");
  const allMatch = anyPasted && rows.every(([k]) => match(k) === true);
  return (
    <div className={`rounded-lg border px-3 py-2 ${c.confirmed ? "border-green-200 bg-green-50/40" : allMatch ? "border-green-300 bg-green-50" : "border-surface-border"}`}>
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span className="text-xs font-medium text-gray-700">{c.name}</span>
        {c.confirmed ? <span className="badge-green text-[10px]">confirmed</span> : allMatch ? <span className="badge-green text-[10px]">✓ matches</span> : <span className="text-[10px] text-gray-400">pending</span>}
      </div>
      <div className="overflow-x-auto">
        <table className="text-[11px] w-full min-w-[400px]">
          <thead><tr className="text-gray-400"><th className="text-left font-medium py-0.5">Field</th><th className="text-right font-medium">Engine</th><th className="text-right font-medium pr-1">Paste authoritative</th><th className="w-5"></th></tr></thead>
          <tbody>
            {rows.map(([k, lbl]) => {
              const m = c.confirmed ? true : match(k);
              const rec = c.recorded?.[k];
              return (
                <tr key={k} className="border-t border-surface-border/60">
                  <td className="py-1 text-gray-600">{lbl}</td>
                  <td className="text-right tabular-nums text-gray-700">{cad2(eng[k])}</td>
                  <td className="text-right">
                    {c.confirmed
                      ? <span className="tabular-nums text-gray-500">{rec != null ? cad2(rec) : "—"}</span>
                      : <input value={paste[k] ?? ""} onChange={(e) => setPaste({ ...paste, [k]: e.target.value })} placeholder="0.00"
                          className="w-24 text-right border border-surface-border rounded px-1.5 py-0.5 tabular-nums" />}
                  </td>
                  <td className="text-center">{m === true ? <span className="text-green-600 font-bold">✓</span> : m === false ? <span className="text-rose-500 font-bold">✗</span> : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ComplianceView() {
  const { data, isLoading } = useQuery({ queryKey: ["compliance"], queryFn: () => payrollApi.compliance() });
  if (isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (!data) return <div className="card"><Empty>Compliance status unavailable.</Empty></div>;
  const cpct = data.total ? Math.round((data.verified / data.total) * 100) : 0;
  const gate = data.gate || { gate_open: !data.ready_for_real_pay, reasons: [] };
  const pdoc = data.pdoc || { confirmed: 0, total: 0, pending: 0 };
  const ppct = pdoc.total ? Math.round((pdoc.confirmed / pdoc.total) * 100) : 0;
  const ready = !gate.gate_open;
  return (
    <>
      <div className={`rounded-xl px-5 py-4 mb-4 ${ready ? "bg-green-50 border border-green-200" : "bg-red-50 border-2 border-red-300"}`}>
        <div className="flex items-start gap-4">
          {ready ? <ShieldCheck className="w-8 h-8 shrink-0 text-green-600" /> : <AlertTriangle className="w-8 h-8 shrink-0 text-red-600" />}
          <div className="flex-1">
            <div className={`font-display font-bold ${ready ? "text-green-800" : "text-red-800"}`}>
              {ready ? "✓ Verified — ready for real pay" : "⚠ NOT verified for real pay — the compliance gate is OPEN"}
            </div>
            <div className="text-xs text-gray-600 mt-1 space-y-0.5">
              {ready ? "All statutory constants confirmed against CRA and every PDOC golden case matches. Real pay runs are permitted."
                : (gate.reasons || []).map((r: string, i: number) => <div key={i}>• {r}</div>)}
            </div>
            {!ready && <div className="text-[11px] text-red-600 mt-1.5">Real pay runs are <b>blocked</b>; any run is recorded as a <b>TEST</b> (not for remittance/filing) until this gate closes.</div>}
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-3">
          {[["Statutory constants", data.verified, data.total, cpct], ["CRA-PDOC golden cases", pdoc.confirmed, pdoc.total, ppct]].map(([label, done, total, pc]: any) => (
            <div key={label}>
              <div className="flex items-center justify-between text-[11px] text-gray-600 mb-1"><span className="font-medium">{label}</span><span className="tabular-nums">{done}/{total}</span></div>
              <div className="h-1.5 rounded-full bg-white/70 overflow-hidden"><div className={`h-full rounded-full ${pc === 100 ? "bg-green-500" : "bg-red-400"}`} style={{ width: `${pc}%` }} /></div>
            </div>
          ))}
        </div>
      </div>
      {data.jurisdictions && (
        <div className="card mb-4">
          <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
            <div className="font-display font-semibold text-gray-800 text-sm">Canada coverage</div>
            <div className="text-[11px] text-gray-500 tabular-nums">
              <span className="text-green-600 font-semibold">{data.jurisdictions.live}</span> live ·{" "}
              <span className="text-amber-600 font-semibold">{data.jurisdictions.gated}</span> gated ·{" "}
              <span className="text-gray-400 font-semibold">{data.jurisdictions.not_built}</span> not built{" "}
              <span className="text-gray-400">of {data.jurisdictions.total}</span>
            </div>
          </div>
          <div className="grid grid-cols-4 sm:grid-cols-7 gap-2">
            {data.jurisdictions.jurisdictions.map((j: any) => {
              const c = j.state === "live" ? "bg-green-50 border-green-300 text-green-700"
                : j.state === "gated" ? "bg-amber-50 border-amber-300 text-amber-700"
                : "bg-gray-50 border-gray-200 text-gray-400";
              const title = j.state === "live" ? "Verified — real pay allowed"
                : j.state === "gated" ? "Built — gated (test only) until CRA-verified"
                : "Not built into the engine yet";
              return (
                <div key={j.code} title={title} className={`rounded-lg border px-2 py-2 text-center ${c}`}>
                  <div className="font-bold text-sm tabular-nums">{j.code}</div>
                  <div className="text-[9px] uppercase tracking-wide mt-0.5">{j.state === "not_built" ? "—" : j.state}</div>
                </div>
              );
            })}
          </div>
          <div className="text-[11px] text-gray-400 mt-3">
            Each province/territory opens for <b>real pay</b> only after its own statutory constants are verified against CRA. ON/BC/AB are live; Quebec and the rest are <b>gated</b> (test-mode runs only) until verified.
          </div>
        </div>
      )}
      <VerificationCenter />
      <div className="card !p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Statutory figure</th>
                <th className="px-3 py-2.5 font-semibold text-right">Value</th>
                <th className="px-3 py-2.5 font-semibold">CRA source</th>
                <th className="px-3 py-2.5 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.constants.map((c: any) => (
                <tr key={c.key} className="border-t border-surface-border">
                  <td className="px-5 py-2 text-gray-800">{c.label}<div className="text-[10px] text-gray-400 font-mono">{c.key}</div></td>
                  <td className="px-3 py-2 text-right tabular-nums text-gray-600 max-w-[220px] truncate" title={c.value}>{c.value}</td>
                  <td className="px-3 py-2 text-gray-500 text-xs">{c.source}</td>
                  <td className="px-3 py-2">{c.status === "verified"
                    ? <span className="badge-green">verified</span>
                    : <span className="badge-amber">estimated</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <p className="text-[11px] text-gray-400 mt-3">Verify each figure against the CRA source, then mark it verified in <span className="font-mono">compliance.py</span>. Validate end-to-end scenarios with <span className="font-mono">verify_pdoc.py</span> against the CRA Payroll Deductions Online Calculator.</p>
    </>
  );
}

function T4View() {
  const [year, setYear] = useState(2026);
  const [slip, setSlip] = useState<any>(null);
  const { data, isLoading } = useQuery({ queryKey: ["t4", year], queryFn: () => payrollApi.t4(year), placeholderData: (p: any) => p });
  const t4csv = () => {
    if (!data?.slips?.length) return;
    const cols: [string, string][] = [["box14_employment_income", "Box14_Income"], ["box16_cpp", "Box16_CPP"], ["box16a_cpp2", "Box16A_CPP2"], ["box17_qpp", "Box17_QPP"], ["box17a_qpp2", "Box17A_QPP2"], ["box18_ei", "Box18_EI"], ["box22_income_tax", "Box22_Tax"], ["box24_ei_insurable", "Box24_EI_Insurable"], ["box26_cpp_pensionable", "Box26_CPP_Pensionable"], ["box55_ppip", "Box55_PPIP"], ["box56_ppip_insurable", "Box56_PPIP_Insurable"]];
    const rows = [["Employee", "EmployeeNumber", "Province", ...cols.map((c) => c[1])],
      ...data.slips.map((sl: any) => [sl.name, sl.employee_number || "", sl.province || "", ...cols.map((c) => (sl.boxes[c[0]] ?? 0).toFixed(2))]),
      ["TOTAL", "", "", ...cols.map((c) => (data.summary[c[0]] ?? 0).toFixed(2))]];
    const blob = new Blob([rows.map((r: any[]) => r.map((c: any) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `lexora-t4-${year}.csv`; a.click(); URL.revokeObjectURL(a.href);
  };
  if (isLoading && !data) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  const s = data?.summary || {};
  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Kpi label="Box 14 · income" value={money(s.box14_employment_income || 0)} />
        <Kpi label="Box 16 · CPP" value={money(s.box16_cpp || 0)} />
        <Kpi label="Box 18 · EI" value={money(s.box18_ei || 0)} />
        <Kpi label="Box 22 · tax" value={money(s.box22_income_tax || 0)} accent />
      </div>
      <div className="card !p-0">
        <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between">
          <h3 className="font-display font-semibold text-gray-800">T4 slips · {year}</h3>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">{data?.slip_count || 0} employees</span>
            <button className="btn-secondary text-xs inline-flex items-center gap-1.5 !py-1.5" onClick={t4csv} disabled={!data?.slip_count}><Download className="w-3.5 h-3.5" />CSV</button>
            <YearPicker year={year} setYear={setYear} />
          </div>
        </div>
        {!data || !data.slip_count ? <Empty>No T4s for {year} — run payroll to build the YTD ledger.</Empty> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
              <tr>
                <th className="px-5 py-2.5 font-semibold">Employee</th>
                {["14 income", "16 CPP", "17 QPP", "18 EI", "22 tax", "26 pens."].map((h) => <th key={h} className="px-3 py-2.5 font-semibold text-right">{h}</th>)}
                <th />
              </tr>
            </thead>
            <tbody>
              {data.slips.map((sl: any) => (
                <tr key={sl.employee_id} onClick={() => setSlip(sl)} className="border-t border-surface-border hover:bg-gray-50 cursor-pointer">
                  <td className="px-5 py-2.5 text-gray-800">{sl.name}<span className="text-xs text-gray-400"> · {sl.employee_number}</span></td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-gray-700">{money(sl.boxes.box14_employment_income)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money(sl.boxes.box16_cpp)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money(sl.boxes.box17_qpp || 0)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money(sl.boxes.box18_ei)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money(sl.boxes.box22_income_tax)}</td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money(sl.boxes.box26_cpp_pensionable)}</td>
                  <td className="px-3 text-primary-500 text-xs whitespace-nowrap">view slip →</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
      </div>
      {slip && <T4SlipModal slip={slip} year={data?.year || year} onClose={() => setSlip(null)} />}
    </>
  );
}

// RL-1 (Relevé 1) — Québec employees' year-end slip (GET /payroll/rl1). Box E = Québec income tax only.
function Rl1View() {
  const [year, setYear] = useState(2026);
  const { data, isLoading } = useQuery({ queryKey: ["rl1", year], queryFn: () => payrollApi.rl1(year), placeholderData: (p: any) => p });
  if (isLoading && !data) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  const s = data?.summary || {};
  const COLS: [string, string][] = [["box_a_employment_income", "A income"], ["box_b_qpp", "B QPP"], ["box_c_ei", "C EI"],
    ["box_e_quebec_income_tax", "E QC tax"], ["box_g_qpp_pensionable", "G QPP pens."], ["box_h_qpip", "H QPIP"], ["box_i_qpip_insurable", "I QPIP ins."]];
  return (
    <>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Kpi label="Box A · income" value={money(s.box_a_employment_income || 0)} />
        <Kpi label="Box B · QPP" value={money(s.box_b_qpp || 0)} />
        <Kpi label="Box H · QPIP" value={money(s.box_h_qpip || 0)} />
        <Kpi label="Box E · Québec tax" value={money(s.box_e_quebec_income_tax || 0)} accent />
      </div>
      <div className="card !p-0">
        <div className="px-5 py-3 border-b border-surface-border flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-display font-semibold text-gray-800">RL-1 slips · {year}</h3>
          <div className="flex items-center gap-2">
            <span className="text-xs text-gray-400">{data?.slip_count || 0} Québec employees</span>
            <YearPicker year={year} setYear={setYear} />
          </div>
        </div>
        {!data || !data.slip_count ? <Empty>No RL-1 slips for {year} — no Québec employee was paid this year.</Empty> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
              <tr><th className="px-5 py-2.5 font-semibold">Employee</th>{COLS.map(([, h]) => <th key={h} className="px-3 py-2.5 font-semibold text-right">{h}</th>)}</tr>
            </thead>
            <tbody>
              {data.slips.map((sl: any) => (
                <tr key={sl.employee_id} className="border-t border-surface-border">
                  <td className="px-5 py-2.5 text-gray-800">{sl.name}<span className="text-xs text-gray-400"> · {sl.employee_number}</span></td>
                  {COLS.map(([k]) => <td key={k} className="px-3 py-2.5 text-right tabular-nums text-gray-600">{money(sl.boxes[k] || 0)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        )}
        {(data?.notes || []).length > 0 && <ul className="px-5 py-3 border-t border-surface-border text-xs text-gray-500 list-disc pl-9 space-y-0.5">{data.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>}
      </div>
    </>
  );
}

function T4SlipModal({ slip, year, onClose }: { slip: any; year: number; onClose: () => void }) {
  const org = useAuthStore((s) => s.user?.org_name);
  const b = slip.boxes;
  const BOXES: [string, string, number][] = [
    ["14", "Employment income", b.box14_employment_income],
    ["16", "Employee's CPP contributions", b.box16_cpp],
    ["16A", "Employee's CPP2 contributions", b.box16a_cpp2],
    ...((b.box17_qpp || b.box55_ppip) ? [["17", "Employee's QPP contributions", b.box17_qpp] as [string, string, number],
      ["17A", "Employee's QPP2 contributions", b.box17a_qpp2] as [string, string, number]] : []),
    ["18", "Employee's EI premiums", b.box18_ei],
    ["22", "Income tax deducted", b.box22_income_tax],
    ["24", "EI insurable earnings", b.box24_ei_insurable],
    ["26", "CPP/QPP pensionable earnings", b.box26_cpp_pensionable],
    ...(b.box55_ppip ? [["55", "Employee's PPIP premiums", b.box55_ppip] as [string, string, number],
      ["56", "PPIP insurable earnings", b.box56_ppip_insurable] as [string, string, number]] : []),
  ];
  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-[min(72rem,96vw)] max-h-[92vh] overflow-hidden flex flex-col">
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-5 py-3 flex items-center justify-between">
          <div>
            <h2 className="font-display font-semibold text-gray-800">T4 · Statement of Remuneration Paid</h2>
            <p className="text-[11px] text-gray-500">{slip.name} · tax year {year}</p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => window.print()} className="icon-btn text-gray-400" title="Print"><Printer className="w-4 h-4" /></button>
            <button onClick={onClose} className="icon-btn text-gray-400"><X className="w-4 h-4" /></button>
          </div>
        </div>
        <div className="overflow-y-auto p-5 space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Employer</div><div className="font-display font-semibold text-gray-800">{org || "Lexora HRM"}</div></div>
            <div className="text-right"><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Employee</div><div className="font-semibold text-gray-800">{slip.name}</div><div className="text-xs text-gray-500">{slip.employee_number} · {slip.province}</div></div>
          </div>
          <div className="grid sm:grid-cols-2 gap-2">
            {BOXES.map(([num, label, val]) => (
              <div key={num} className="rounded-lg border border-surface-border px-3 py-2 flex items-center justify-between">
                <div><span className="inline-block min-w-[34px] text-[11px] font-bold text-primary-600">Box {num}</span><span className="text-xs text-gray-500">{label}</span></div>
                <span className="tabular-nums font-semibold text-gray-800">{money(val)}</span>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-gray-400">Draft T4 generated from the YTD ledger. Boxes 24/26 capped at the annual EI/CPP maximums. Add the employer legal name, Business Number and employee SIN before filing; extended boxes (20 RPP, 44 dues) require per-component YTD. Statutory maximums pending CRA verification.</p>
        </div>
      </div>
    </div>
  );
}

function RemitScheduleView() {
  const [year, setYear] = useState(2026);
  const { data, isLoading } = useQuery({ queryKey: ["remit-sched", year], queryFn: () => payrollApi.remittanceSchedule(year), placeholderData: (p: any) => p });
  if (isLoading && !data) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (!data || !data.months.length) return (
    <div>
      <div className="flex items-center justify-end mb-3"><YearPicker year={year} setYear={setYear} /></div>
      <div className="card"><Empty>No remittances for {year} — commit a pay run first.</Empty></div>
    </div>
  );
  return (
    <div className="card !p-0">
      <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between">
        <h3 className="font-display font-semibold text-gray-800">CRA remittance schedule · {year}</h3>
        <div className="flex items-center gap-2"><span className="text-xs text-gray-400">{data.remitter_type}</span><YearPicker year={year} setYear={setYear} /></div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
            <tr>
              <th className="px-5 py-2.5 font-semibold">Month</th>
              <th className="px-3 py-2.5 font-semibold text-right">Income tax</th>
              <th className="px-3 py-2.5 font-semibold text-right">CPP</th>
              <th className="px-3 py-2.5 font-semibold text-right">EI</th>
              <th className="px-3 py-2.5 font-semibold text-right">Remittance</th>
              <th className="px-5 py-2.5 font-semibold">Due date</th>
            </tr>
          </thead>
          <tbody>
            {data.months.map((m: any) => (
              <tr key={m.month} className="border-t border-surface-border">
                <td className="px-5 py-2.5 font-medium text-gray-800">{m.month}</td>
                <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money(m.income_tax)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money(m.cpp)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{money(m.ei)}</td>
                <td className="px-3 py-2.5 text-right tabular-nums font-semibold text-gray-800">{money(m.remittance)}</td>
                <td className="px-5 py-2.5"><span className="badge-teal">{m.due_date}</span></td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-surface-border bg-surface font-semibold">
              <td className="px-5 py-2.5 text-gray-800" colSpan={4}>Year total</td>
              <td className="px-3 py-2.5 text-right tabular-nums text-primary-600">{money(data.total_remittance)}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function RoeView() {
  const [emp, setEmp] = useState("");
  const [reason, setReason] = useState("A");
  const [roe, setRoe] = useState<any>(null);
  const { data: ytd } = useQuery({ queryKey: ["ytd", 2026], queryFn: () => payrollApi.ytd(2026) });
  const gen = useMutation({ mutationFn: () => payrollApi.roe(emp, reason), onSuccess: setRoe, onError: () => toast.error("No pay history for this employee") });
  const REASONS: [string, string][] = [["A", "Shortage of work / end of contract"], ["E", "Quit"], ["M", "Dismissal"], ["D", "Illness or injury"], ["N", "Leave of absence"], ["F", "Maternity"], ["G", "Retirement"], ["K", "Other"]];
  const emps = ytd?.employees || [];
  return (
    <div className="grid lg:grid-cols-3 gap-4">
      <div className="card h-fit">
        <h3 className="font-display font-semibold text-gray-800 mb-3">Generate ROE</h3>
        <label className="block text-xs font-semibold text-gray-500 mb-1">Employee</label>
        <select className="input" value={emp} onChange={(e) => { setEmp(e.target.value); setRoe(null); }}>
          <option value="">Select…</option>
          {emps.map((e) => <option key={e.employee_id} value={e.employee_id}>{e.name}</option>)}
        </select>
        <label className="block text-xs font-semibold text-gray-500 mb-1 mt-4">Reason for issuing (Block 16)</label>
        <select className="input" value={reason} onChange={(e) => setReason(e.target.value)}>
          {REASONS.map(([c, l]) => <option key={c} value={c}>{c} — {l}</option>)}
        </select>
        <button className="btn-primary w-full mt-5 disabled:opacity-60" onClick={() => gen.mutate()} disabled={!emp || gen.isPending}>
          {gen.isPending ? "Generating…" : "Generate draft ROE"}
        </button>
        <p className="text-[11px] text-gray-400 mt-2">A real ROE is filed via CRA/Service Canada ROE Web. This produces the block data for review.</p>
      </div>
      <div className="card lg:col-span-2">
        {!roe ? <div className="grid place-items-center text-center py-20 text-sm text-gray-400">Select an employee and press <span className="mx-1 font-semibold text-primary-600">Generate</span>.</div> : (
          <>
            <div className="flex items-center justify-between mb-3">
              <div><h3 className="font-display font-semibold text-gray-800">Record of Employment — draft</h3><p className="text-[11px] text-gray-500">{roe.employee.name} · {roe.employee.employee_number}</p></div>
              <div className="flex items-center gap-1.5">
                <button onClick={async () => {
                  try {
                    const blob = await payrollApi.roeXml(emp, reason);
                    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
                    a.download = `roe-${roe.employee.employee_number || emp}-draft.xml`; a.click(); URL.revokeObjectURL(a.href);
                  } catch { toast.error("Could not export ROE XML"); }
                }} className="btn-secondary text-xs inline-flex items-center gap-1.5 !py-1.5" title="Download draft ROE XML data package"><Download className="w-3.5 h-3.5" />XML</button>
                <button onClick={() => window.print()} className="icon-btn text-gray-400" title="Print"><Printer className="w-4 h-4" /></button>
              </div>
            </div>
            <div className="grid sm:grid-cols-2 gap-2 text-sm">
              <Block n="6" label="Pay period type" value={freqLabel(roe.block6_pay_period_type)} />
              <Block n="16" label="Reason" value={`${roe.block16_reason.code} — ${roe.block16_reason.label}`} />
              <Block n="10" label="First day worked" value={roe.block10_first_day_worked || "—"} />
              <Block n="11" label="Last day paid" value={roe.block11_last_day_paid} />
              <Block n="15A" label="Total insurable hours" value={String(roe.block15a_total_insurable_hours)} />
              <Block n="15B" label="Total insurable earnings" value={money(roe.block15b_total_insurable_earnings)} />
            </div>
            <div className="mt-4">
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Block 15C · insurable earnings by pay period</div>
              <div className="overflow-x-auto max-h-64">
                <table className="w-full text-sm">
                  <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide sticky top-0">
                    <tr><th className="px-3 py-2 font-semibold">Period end</th><th className="px-3 py-2 font-semibold text-right">Insurable earnings</th><th className="px-3 py-2 font-semibold text-right">Hours</th></tr>
                  </thead>
                  <tbody>
                    {roe.block15c_by_period.map((p: any, i: number) => (
                      <tr key={i} className="border-t border-surface-border">
                        <td className="px-3 py-1.5 text-gray-700">{p.period_end}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-gray-600">{money(p.insurable_earnings)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums text-gray-500">{p.insurable_hours}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Block({ n, label, value }: { n: string; label: string; value: string }) {
  return (
    <div className="rounded-lg border border-surface-border px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold"><span className="text-primary-600">Block {n}</span> · {label}</div>
      <div className="text-sm font-medium text-gray-800">{value}</div>
    </div>
  );
}

function Kpi({ label, value, accent }: { label: string; value: any; accent?: boolean }) {
  return (
    <div className="rounded-lg border border-surface-border bg-surface px-4 py-3">
      <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{label}</div>
      <div className={`text-lg font-bold tabular-nums ${accent ? "text-primary-600" : "text-gray-800"}`}>{value}</div>
    </div>
  );
}
