import { useState, useMemo, useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarDays, Plus, CheckCircle2, XCircle, X, Wand2, Users, Clock, PieChart, CalendarCheck, Printer } from "lucide-react";
import toast from "react-hot-toast";
import { useSearchParams } from "react-router-dom";
import { PageHeader, StatTile, Spinner, Empty, Badge } from "../components/ui";
import { Pager } from "../components/Pager";
import { leaveApi, type LeaveBalance, type LeaveRequest, type MasterRow } from "../api/endpoints";
import { useAuthStore, APPROVER_ROLES, HR_WRITE, TEAM_APPROVER_ROLES } from "../store/authStore";
import { usableLeave, eventLeave } from "../api/ess";
import api from "../api/client";
import { LeaveApplyModal, CancelLeaveModal, CompOffPanel } from "./employee/LeaveForms";
import { printRegion } from "../lib/print";   // print wrap-up 2026-10-24
import PrintRegionButton, { PrintHeading } from "../components/PrintRegionButton";
import { useUrlTab } from "../search/useUrlTab";
import { screenRolesFor } from "../config/permissions";
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today
const fmt = (iso?: string | null) => iso ? new Date(iso + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }) : "—";
const REQ_TONE: Record<string, string> = { pending: "on_leave", approved: "active", rejected: "terminated", cancelled: "onboarding", cancel_requested: "on_leave" };
async function downloadLeaveDoc(id: string) {
  const r = await api.get(`/leave/requests/${id}/attachment`, { responseType: "blob" });
  const name = /filename="([^"]+)"/.exec(String(r.headers["content-disposition"] || ""))?.[1] || "leave-document";
  const url = URL.createObjectURL(r.data as Blob); const a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const TABS = [
  { key: "overview", label: "Overview", icon: PieChart, roles: APPROVER_ROLES },
  { key: "mine", label: "My leave", icon: CalendarCheck, roles: null },
  { key: "approvals", label: "Approvals", icon: CheckCircle2, roles: APPROVER_ROLES },
];

export default function LeavePage() {
  // 2026-10-24: a custom role carrying leave.view / leave.approve opens the HR / approver views (server checks calls)
  const user = useAuthStore((s) => s.user);
  const viewRoles = screenRolesFor(user, "leave.view");
  const apprRoles = screenRolesFor(user, "leave.approve");
  const canApprove = apprRoles.some((r) => TEAM_APPROVER_ROLES.includes(r));
  const isHR = viewRoles.some((r) => HR_WRITE.includes(r));
  const visibleTabs = TABS.filter((t) => !t.roles || (t.key === "approvals" ? apprRoles : viewRoles).some((r) => t.roles!.includes(r)));
  const [tab, setTab] = useUrlTab<string>(canApprove ? "approvals" : "mine");   // ?tab= (global search deep link)
  const [params] = useSearchParams();
  const [showRequest, setShowRequest] = useState(params.get("apply") === "1");

  const approvals = useQuery({ queryKey: ["lv", "approvals"], queryFn: () => leaveApi.approvals(), enabled: canApprove });
  const summary = useQuery({ queryKey: ["lv", "summary"], queryFn: () => leaveApi.summary(), enabled: canApprove || isHR });

  const s = summary.data;
  const pendingCount = approvals.data?.pending_total ?? 0;

  return (
    <div>
      <PageHeader title="Leave" icon={CalendarDays} subtitle="Fiscal-year balances → request → manager approval"
        actions={<button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setShowRequest(true)}><Plus className="w-4 h-4" />Request leave</button>} />

      {canApprove && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
          <StatTile label="Awaiting my approval" value={pendingCount} sub={approvals.data ? `${approvals.data.days_total} days` : ""} icon={Clock} accent />
          <StatTile label="Requests this year" value={s ? s.requests : "—"} sub={s ? `${s.by_status?.approved || 0} approved` : ""} icon={CalendarDays} />
          <StatTile label="Days allocated" value={s ? `${s.total_allocated_days}` : "—"} sub={s ? `${s.employees_with_balances} employees` : ""} icon={Users} />
          <StatTile label="Days taken" value={s ? `${s.total_used_days}` : "—"} sub="approved & used" icon={PieChart} />
        </div>
      )}

      <div className="flex gap-1 mb-4 overflow-x-auto">
        {visibleTabs.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${tab === t.key ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>
            <t.icon className="w-4 h-4" />{t.label}
            {t.key === "approvals" && pendingCount > 0 && <span className="ml-1 text-[10px] bg-amber-100 text-amber-700 rounded-full px-1.5 py-0.5">{pendingCount}</span>}
          </button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab isHR={isHR} />}
      {tab === "mine" && <MyLeaveTab />}
      {tab === "approvals" && <ApprovalsTab />}

      {showRequest && <LeaveApplyModal onClose={() => setShowRequest(false)} />}
    </div>
  );
}

// ── My leave — balances + own requests ───────────────────────────────────────
function MyLeaveTab() {
  const qc = useQueryClient();
  const bal = useQuery({ queryKey: ["lv", "balances", "me"], queryFn: () => leaveApi.balances(), retry: false });
  const mine = useQuery({ queryKey: ["lv", "requests", "mine"], queryFn: () => leaveApi.requests({ scope: "mine" }), retry: false });
  const [askCancel, setAskCancel] = useState<LeaveRequest | null>(null);
  const cancel = useMutation({
    mutationFn: (id: string) => leaveApi.cancelRequest(id),
    onSuccess: () => { toast.success("Request cancelled"); qc.invalidateQueries({ queryKey: ["lv"] }); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Cancel failed"),
  });

  if (bal.isError) return <div className="card"><Empty>Your login isn't linked to an employee record, so personal balances aren't available. Use the Approvals / Overview tabs, or ask HR to link your account.</Empty></div>;

  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-display font-semibold text-gray-800 mb-2">My balances · FY {bal.data?.fiscal_year || ""}</h3>
        {bal.isLoading ? <div className="grid place-items-center py-10"><Spinner className="h-6 w-6" /></div>
          : !bal.data?.balances.length ? <div className="card"><Empty>No balances allocated yet — ask HR to run the fiscal-year allocation.</Empty></div>
          : (
            <div className="space-y-3">
              {/* 2026-10-16: one total = usable types only; event leave is listed apart and never summed */}
              <div className="card !py-3 flex flex-wrap items-center gap-x-6 gap-y-1" data-testid="leave-usable-total">
                <div><span className="font-display text-2xl font-bold text-primary-600 tabular-nums">{usableLeave(bal.data)}</span>
                  <span className="text-sm text-gray-500 ml-1.5">days you can take this year</span></div>
                {eventLeave(bal.data).length > 0 && <div className="text-xs text-gray-500">Event leave (only for that event, not counted above): {eventLeave(bal.data).map((e) => `${e.name} ${e.remaining} days`).join(" · ")}</div>}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {bal.data.balances.filter((b: any) => (b.category || "usable") === "usable").map((b) => <BalanceCard key={b.id} b={b} />)}
              </div>
              {bal.data.balances.some((b: any) => b.category && b.category !== "usable") && <>
                <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400 pt-1">Event and unpaid leave</h4>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {bal.data.balances.filter((b: any) => b.category && b.category !== "usable").map((b) => <BalanceCard key={b.id} b={b} />)}
                </div></>}
            </div>
          )}
      </div>
      <div>
        <h3 className="font-display font-semibold text-gray-800 mb-2">My requests</h3>
        <RequestTable q={mine} onCancel={(id) => {
          const r = (mine.data || []).find((x: LeaveRequest) => x.id === id);
          if (r && r.status === "approved") setAskCancel(r); else cancel.mutate(id);
        }} showEmployee={false} />
      </div>
      <CompOffPanel />
      {askCancel && <CancelLeaveModal id={askCancel.id} label={`${askCancel.leave_type_name} · ${fmt(askCancel.start_date)}${askCancel.end_date !== askCancel.start_date ? ` – ${fmt(askCancel.end_date)}` : ""}`} onClose={() => setAskCancel(null)} />}
    </div>
  );
}

function BalanceCard({ b }: { b: LeaveBalance }) {
  const pct = b.entitled > 0 ? Math.min(100, Math.round(((b.used + b.pending) / b.entitled) * 100)) : 0;
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <span className="font-medium text-gray-800">{b.leave_type_name}</span>
        {!b.paid && <span className="text-[10px] text-gray-400 uppercase">unpaid</span>}
      </div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="font-display text-3xl font-bold text-primary-600 tabular-nums">{b.remaining}</span>
        <span className="text-sm text-gray-400">/ {b.entitled} days left</span>
      </div>
      <div className="h-2 rounded-full bg-gray-100 overflow-hidden mt-2">
        <div className="h-full rounded-full bg-primary-500" style={{ width: `${pct}%` }} />
      </div>
      <div className="flex justify-between text-[11px] text-gray-500 mt-1.5">
        <span>{b.used} used{b.pending ? ` · ${b.pending} pending` : ""}</span>
        {b.carried_over > 0 && <span>+{b.carried_over} carried</span>}
      </div>
    </div>
  );
}

// ── Approvals queue (manager / HR) ───────────────────────────────────────────
function ApprovalsTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["lv", "approvals"], queryFn: () => leaveApi.approvals() });
  const act = useMutation({
    mutationFn: ({ id, op, note }: { id: string; op: "approve" | "reject"; note?: string }) =>
      op === "approve" ? leaveApi.approveRequest(id, note) : leaveApi.rejectRequest(id, note),
    onSuccess: (_r, v) => { toast.success(`Leave ${v.op}d`); qc.invalidateQueries({ queryKey: ["lv"] }); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Action failed"),
  });

  const rows = q.data?.pending || [];
  return (
    <div className="card !p-0">
      <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between">
        <h3 className="font-display font-semibold text-gray-800">Pending my approval</h3>
        <PrintHeading>Leave pending approval · {rows.length} request{rows.length === 1 ? "" : "s"}</PrintHeading>
        <span className="flex items-center gap-2">{!!rows.length && <span className="text-xs text-gray-400">{q.data?.days_total} days across {rows.length}</span>}
        {!!rows.length && <PrintRegionButton title="Print the pending list (A4)" />}</span>
      </div>
      {q.isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : !rows.length ? <Empty>Nothing awaiting your approval. 🎉</Empty>
        : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[720px]">
              <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                <tr><th className="px-5 py-2.5 font-semibold">Employee</th><th className="px-3 py-2.5 font-semibold">Type</th><th className="px-3 py-2.5 font-semibold">Dates</th><th className="px-3 py-2.5 font-semibold text-right">Days</th><th className="px-3 py-2.5 font-semibold">Reason</th><th className="px-4 py-2.5 font-semibold text-right">Decision</th></tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-surface-border hover:bg-surface/50">
                    <td className="px-5 py-2.5"><div className="font-medium text-gray-800">{r.employee_name}</div><div className="text-[11px] text-gray-400">{r.employee_number}</div></td>
                    <td className="px-3 py-2.5"><span className="badge-teal">{r.leave_type_name}</span></td>
                    <td className="px-3 py-2.5 text-gray-600">{fmt(r.start_date)}{r.end_date !== r.start_date ? ` – ${fmt(r.end_date)}` : ""}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums font-semibold text-gray-800">{r.days}{r.half_day ? " ½" : ""}</td>
                    <td className="px-3 py-2.5 text-gray-500 max-w-[220px] truncate">{r.reason || "—"}</td>
                    <td className="px-4 py-2.5 text-right whitespace-nowrap">
                      <span className="inline-flex gap-1.5">
                        <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 text-green-700" onClick={() => act.mutate({ id: r.id, op: "approve" })} disabled={act.isPending}><CheckCircle2 className="w-3.5 h-3.5" />Approve</button>
                        <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 text-red-600" onClick={() => { const note = window.prompt("Reason for rejection (optional):") || undefined; act.mutate({ id: r.id, op: "reject", note }); }} disabled={act.isPending}><XCircle className="w-3.5 h-3.5" />Reject</button>
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}

// ── Overview (HR/admin): all requests + allocate ─────────────────────────────
function OverviewTab({ isHR }: { isHR: boolean }) {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("");
  const q = useQuery({ queryKey: ["lv", "requests", "org", statusFilter], queryFn: () => leaveApi.requests({ scope: "org", ...(statusFilter ? { status: statusFilter } : {}) }), retry: false });
  const allocate = useMutation({
    mutationFn: () => leaveApi.allocate({}),
    onSuccess: (r) => { toast.success(`Allocated ${r.balances_created} balances to ${r.employees} employees (FY ${r.fiscal_year})`); qc.invalidateQueries({ queryKey: ["lv"] }); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Allocation failed"),
  });

  return (
    <div className="card !p-0">
      <div className="px-5 py-3 border-b border-surface-border flex items-center justify-between gap-3 flex-wrap">
        <h3 className="font-display font-semibold text-gray-800">All leave requests</h3>
        <div className="flex items-center gap-2">
          <select className="input !py-1.5 !w-auto text-sm" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All statuses</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option><option value="cancelled">Cancelled</option>
          </select>
          {isHR && <button className="btn-secondary text-sm inline-flex items-center gap-1.5" onClick={() => allocate.mutate()} disabled={allocate.isPending}><Wand2 className="w-4 h-4" />{allocate.isPending ? "Allocating…" : "Allocate FY balances"}</button>}
        </div>
      </div>
      <RequestTable q={q} showEmployee bare />
    </div>
  );
}

// ── Shared request table ─────────────────────────────────────────────────────
function RequestTable({ q, onCancel, showEmployee, bare }: { q: any; onCancel?: (id: string) => void; showEmployee: boolean; bare?: boolean }) {
  const rows: LeaveRequest[] = q.data || [];
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const total = rows.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const curPage = Math.min(page, pageCount);
  // Print: every row (not just this page) of this table only.
  const [printAll, setPrintAll] = useState(false);
  const tableRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!printAll) return;
    const t = setTimeout(() => { printRegion(tableRef.current); setPrintAll(false); }, 50);
    return () => clearTimeout(t);
  }, [printAll]);
  const paged = printAll ? rows : rows.slice((curPage - 1) * pageSize, curPage * pageSize);
  useEffect(() => { setPage(1); }, [total, pageSize]);
  const body = q.isLoading ? <div className="grid place-items-center py-12"><Spinner className="h-6 w-6" /></div>
    : q.isError ? <Empty>Couldn't load requests.</Empty>
    : !rows.length ? <Empty>No leave requests.</Empty>
    : (
      <>
      <div className="flex justify-end px-3 pt-2 lx-no-print">
        <button type="button" className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => setPrintAll(true)} title="Print every request listed (A4)" data-testid="leave-requests-print"><Printer className="w-3.5 h-3.5" /> Print</button>
      </div>
      <div className="overflow-x-auto" ref={tableRef}>
        <PrintHeading>Leave requests · {total} request{total === 1 ? "" : "s"}</PrintHeading>
        <table className="w-full text-sm min-w-[680px]">
          <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
            <tr>
              {showEmployee && <th className="px-5 py-2.5 font-semibold">Employee</th>}
              <th className="px-3 py-2.5 font-semibold">Type</th><th className="px-3 py-2.5 font-semibold">Dates</th>
              <th className="px-3 py-2.5 font-semibold text-right">Days</th><th className="px-3 py-2.5 font-semibold">Status</th>
              <th className="px-3 py-2.5 font-semibold">Reviewed by</th>
              {onCancel && <th className="px-4 py-2.5 font-semibold text-right"></th>}
            </tr>
          </thead>
          <tbody>
            {paged.map((r) => (
              <tr key={r.id} className="border-t border-surface-border hover:bg-surface/50">
                {showEmployee && <td className="px-5 py-2.5"><div className="font-medium text-gray-800">{r.employee_name}</div><div className="text-[11px] text-gray-400">{r.employee_number}</div></td>}
                <td className="px-3 py-2.5"><span className="badge-teal">{r.leave_type_name}</span></td>
                <td className="px-3 py-2.5 text-gray-600">{fmt(r.start_date)}{r.end_date !== r.start_date ? ` – ${fmt(r.end_date)}` : ""}</td>
                <td className="px-3 py-2.5 text-right tabular-nums">{r.days}{r.half_day ? ` ½ (${r.half_session === "second" ? "2nd" : "1st"} half)` : ""}</td>
                <td className="px-3 py-2.5"><Badge tone={REQ_TONE[r.status]}>{r.status === "cancel_requested" ? "cancel requested" : r.status}</Badge>
                  {r.attachment_id && <a className="block text-[11px] text-primary-600 hover:underline cursor-pointer" onClick={() => downloadLeaveDoc(r.id)}>document</a>}</td>
                <td className="px-3 py-2.5 text-gray-500 text-xs">{r.reviewed_by_name || "—"}{r.review_note ? <span className="block text-[11px] text-gray-400 truncate max-w-[180px]">{r.review_note}</span> : ""}</td>
                {onCancel && <td className="px-4 py-2.5 text-right">{r.status === "pending" || r.status === "approved" ? <button className="btn-secondary !py-1 !px-2.5 text-xs" onClick={() => onCancel(r.id)}>{r.status === "approved" ? "Ask to cancel" : "Withdraw"}</button> : r.status === "cancel_requested" ? <span className="text-[11px] text-amber-600">cancellation with approver</span> : <span className="text-[11px] text-gray-300">—</span>}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager total={total} page={curPage} pageSize={pageSize} onPage={setPage} onPageSize={setPageSize} />
      </>
    );
  return bare ? body : <div className="card !p-0">{body}</div>;
}
