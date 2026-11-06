import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { CheckCircle2, CheckSquare, ExternalLink, Inbox, RefreshCw, XCircle } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../components/ui";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../components/DataGrid";
import { approvalsApi, errText, when, type ApprovalRow, type DecidedRow, type Delegation, type KindCount } from "../api/platform";
import { useAuthStore } from "../store/authStore";
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today
import { useCan } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

type Tab = "inbox" | "mine" | "decided" | "delegation" | "all";
const OFFICE = ["org_admin", "superadmin", "hr", "hr_admin", "hr_manager", "hr_staff"];
const STATE: Record<string, { label: string; cls: string }> = {
  pending: { label: "Waiting", cls: "badge-amber" }, approved: { label: "Approved", cls: "badge-green" },
  rejected: { label: "Rejected", cls: "badge-red" }, cancelled: { label: "Cancelled", cls: "badge-purple" },
  withdrawn: { label: "Withdrawn", cls: "badge-purple" },
};
const WHY: Record<string, string> = { named: "Sent to you by name", role: "Your role", manager: "You are the manager", delegate: "Delegated to you" };
const age = (d: number) => (d === 0 ? "Today" : `${d} day${d === 1 ? "" : "s"}`);

function KindChips({ kinds, value, onChange }: { kinds: KindCount[]; value: string; onChange: (k: string) => void }) {
  if (!kinds.length) return null;
  const total = kinds.reduce((n, k) => n + k.count, 0);
  return (
    <div className="flex flex-wrap gap-1.5 mb-3">
      {[{ kind: "", label: "All", count: total }, ...kinds].map((k) => (
        <button key={k.kind || "all"} onClick={() => onChange(k.kind)}
          className={`px-2.5 py-1 rounded-full text-xs font-medium border transition-colors ${value === k.kind ? "bg-primary-600 border-primary-600 text-white" : "bg-white border-surface-border text-gray-600 hover:bg-gray-50"}`}>
          {k.label} <span className="tabular-nums opacity-80">{k.count}</span>
        </button>
      ))}
    </div>
  );
}

const what = (r: { title: string; summary: string[] }) => (
  <div className="min-w-0"><div className="font-medium text-gray-800">{r.title}</div>
    {r.summary.map((s, i) => <div key={i} className="text-xs text-gray-500">{s}</div>)}</div>
);
const openLink = (link: string) => link?.startsWith("/")
  ? <Link to={link} className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1"><ExternalLink className="w-3 h-3" /> Open</Link> : null;

/** `selfOnly` = the My Requests screen: just "things I raised", for every role. */
export default function ApprovalsPage({ selfOnly = false }: { selfOnly?: boolean }) {
  const office = useCan("approvals.view", OFFICE);
  const canDelegateOthers = useCan("approvals.admin", ["org_admin", "superadmin"]);
  const [sp, setSp] = useSearchParams();
  const tab: Tab = selfOnly ? "mine" : ((sp.get("tab") as Tab) || "inbox");
  const setTab = (t: Tab) => setSp(t === "inbox" ? {} : { tab: t }, { replace: true });
  const summary = useQuery({ queryKey: ["apr", "summary"], queryFn: approvalsApi.summary, enabled: !selfOnly, retry: false });

  if (selfOnly) {
    return (
      <div>
        <PageHeader title="My Requests" icon={Inbox} subtitle="Everything you raised that needs someone's approval, and where it stands" />
        <LegacyNotice />
        <Mine />
      </div>
    );
  }
  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "inbox", label: "Waiting for me", count: summary.data?.waiting_for_me },
    { key: "mine", label: "My requests" }, { key: "decided", label: "Decided by me" },
    { key: "delegation", label: "Out of office" },
    ...(office ? [{ key: "all" as Tab, label: "All pending" }] : []),
  ];
  return (
    <div>
      <PageHeader title="Approvals" icon={CheckSquare} subtitle="One inbox for every request that is waiting for a decision" />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === "inbox" && <Waiting mode="inbox" />}
      {tab === "all" && office && <Waiting mode="all" />}
      {tab === "mine" && <Mine />}
      {tab === "decided" && <Decided />}
      {tab === "delegation" && <Delegations canSetForOthers={canDelegateOthers} />}
    </div>
  );
}

export function MyRequestsPage() { return <ApprovalsPage selfOnly />; }

/** The old My Requests list lived only in this browser. It is not copied anywhere —
 *  say so instead of dropping it silently. */
function LegacyNotice() {
  const email = useAuthStore((s) => s.user?.email);
  const old = useMemo(() => {
    try { const v = JSON.parse(localStorage.getItem(`lx-requests-${email || "me"}`) || "[]"); return Array.isArray(v) ? v.length : 0; }
    catch { return 0; }
  }, [email]);
  if (!old) return null;
  return (
    <div className="rounded-lg bg-amber-50 text-amber-800 text-sm px-3 py-2 mb-3">
      This browser still holds {old} request{old === 1 ? "" : "s"} typed into the old version of this screen. They were never sent to anyone and are not shown here.
      Raise them again on the right screen (for example Leave) so they reach an approver.
    </div>
  );
}

function Waiting({ mode }: { mode: "inbox" | "all" }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["apr", mode], queryFn: mode === "inbox" ? approvalsApi.inbox : approvalsApi.all });
  const [kind, setKind] = useState("");
  const [act, setAct] = useState<{ row: ApprovalRow; op: "approve" | "reject" } | null>(null);
  const [comment, setComment] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["apr"] }); qc.invalidateQueries({ queryKey: ["lv"] }); qc.invalidateQueries({ queryKey: ["ts"] }); };
  const decide = useMutation({
    mutationFn: () => approvalsApi.decide(act!.row.id, act!.op, comment.trim()),
    onSuccess: (r: any) => { toast.success(r.status === "approved" ? "Approved" : r.status === "pending" ? (r.message || "Approved — on to the next step") : "Rejected"); setAct(null); setComment(""); refresh(); },
    onError: (e) => toast.error(errText(e), { duration: 7000 }),
  });
  const rows = useMemo(() => (q.data?.items || []).filter((r) => !kind || r.kind === kind), [q.data, kind]);
  const canDecide = (r: ApprovalRow) => r.can_decide_here && (mode === "inbox" || r.i_can_decide);
  const cols: GridColumn<ApprovalRow>[] = [
    { key: "kind_label", label: "Type", width: "140px", render: (r) => <span className="badge-teal" style={{ textTransform: "none" }}>{r.kind_label}</span> },
    { key: "title", label: "Request", text: (r) => `${r.title} ${r.summary.join(" ")}`, render: what },
    { key: "requester_name", label: "Raised by", width: "160px" },
    { key: "age_days", label: "Waiting", type: "number", width: "90px", render: (r) => <span className={r.age_days >= 3 ? "text-red-600 font-semibold" : ""} title={when(r.opened_at)}>{age(r.age_days)}</span> },
    { key: "step_label", label: "Step", width: "190px", text: (r) => r.step_label || "",
      render: (r) => (r.steps_total ? <span className="text-xs text-gray-600" title={(r.waiting_for || []).join(", ")}>{r.step_label}{r.mode === "all" ? " · everyone" : ""}
        {r.escalated && <span className="badge-amber ml-1">escalated</span>}{r.overdue && <span className="badge-red ml-1">SLA passed</span>}</span> : <span className="text-gray-300">—</span>) },
    mode === "inbox"
      ? { key: "why", label: "Why you", width: "170px", text: (r) => WHY[r.why || ""] || "", render: (r) => <span className="text-xs text-gray-500">{WHY[r.why || ""] || ""}{r.acting_for ? ` (for ${r.acting_for})` : ""}</span> }
      : { key: "waiting_on", label: "Waiting on", width: "220px", className: "text-xs text-gray-500" },
    { key: "act", label: "Decision", sortable: false, width: "250px", text: () => "",
      render: (r) => (
        <span className="inline-flex flex-wrap gap-1.5 justify-end w-full">
          {canDecide(r) && <>
            <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 text-green-700" onClick={() => { setComment(""); setAct({ row: r, op: "approve" }); }}><CheckCircle2 className="w-3.5 h-3.5" /> Approve</button>
            <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 text-red-600" onClick={() => { setComment(""); setAct({ row: r, op: "reject" }); }}><XCircle className="w-3.5 h-3.5" /> Reject</button>
          </>}
          {openLink(r.link)}
        </span>) },
  ];
  if (q.isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (q.isError) return <div className="card text-sm text-red-600">Could not load approvals. {errText(q.error)}</div>;
  const items = q.data?.items || [];
  const oldest = items.reduce((m, r) => Math.max(m, r.age_days), 0);
  return (
    <div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
        <StatTile label={mode === "inbox" ? "Waiting for me" : "Waiting, whole company"} value={items.length} icon={CheckSquare} accent />
        <StatTile label="Types" value={q.data?.by_kind.length ?? 0} />
        <StatTile label="Waiting 3 days or more" value={items.filter((r) => r.age_days >= 3).length} />
        <StatTile label="Oldest" value={items.length ? age(oldest) : "—"} />
      </div>
      <KindChips kinds={q.data?.by_kind || []} value={kind} onChange={setKind} />
      <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName={mode === "inbox" ? "approvals-waiting-for-me" : "approvals-all-pending"}
        searchPlaceholder="Search request or person…" minWidth={980} initialSort={{ key: "age_days", dir: "desc" }}
        empty={mode === "inbox" ? "Nothing is waiting for you." : "Nothing is waiting in the company."}
        toolbar={<button className="btn-secondary !py-1.5 !px-2.5 text-xs inline-flex items-center gap-1.5" onClick={refresh}><RefreshCw className="w-3.5 h-3.5" /> Refresh</button>} />
      {act && (
        <WideModal title={`${act.op === "approve" ? "Approve" : "Reject"} — ${act.row.title}`} onClose={() => setAct(null)}
          footer={<>
            <button className="btn-secondary text-sm" onClick={() => setAct(null)}>Cancel</button>
            <button className="btn-primary text-sm" disabled={decide.isPending || (act.op === "reject" && !comment.trim())} onClick={() => decide.mutate()}>
              {decide.isPending ? "Working…" : act.op === "approve" ? "Approve" : "Reject"}</button>
          </>}>
          <div className="grid lg:grid-cols-2 gap-4">
            <div className="text-sm">
              <div className="text-xs font-semibold text-gray-500 mb-1">{act.row.kind_label} · raised by {act.row.requester_name || "—"} · {when(act.row.opened_at)}</div>
              {act.row.summary.length ? <ul className="space-y-1 text-gray-700">{act.row.summary.map((s, i) => <li key={i}>{s}</li>)}</ul> : <p className="text-gray-500">No further details were sent with this request. Use Open to see it.</p>}
              {act.row.acting_for && <p className="mt-3 text-xs rounded-lg bg-primary-50 text-primary-700 px-3 py-2">You are deciding this for {act.row.acting_for}, who is out of office. The record will show both names.</p>}
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1">Comment {act.op === "reject" ? "(needed to reject)" : "(optional)"}</label>
              <textarea className="input min-h-[120px]" autoFocus value={comment} maxLength={1000} onChange={(e) => setComment(e.target.value)} placeholder={act.op === "reject" ? "Say why, so the person knows what to change" : "Add a note for the person who raised it"} />
              <p className="text-[11px] text-gray-400 mt-2">Your decision is recorded with your name, the time and this comment. It cannot be edited afterwards.</p>
            </div>
          </div>
        </WideModal>
      )}
    </div>
  );
}

function Mine() {
  const q = useQuery({ queryKey: ["apr", "mine"], queryFn: approvalsApi.mine });
  const [kind, setKind] = useState("");
  const [state, setState] = useState("");
  const [detail, setDetail] = useState<ApprovalRow | null>(null);
  const rows = useMemo(() => (q.data?.items || []).filter((r) => (!kind || r.kind === kind) && (!state || r.status === state)), [q.data, kind, state]);
  const cols: GridColumn<ApprovalRow>[] = [
    { key: "kind_label", label: "Type", width: "140px", render: (r) => <span className="badge-teal" style={{ textTransform: "none" }}>{r.kind_label}</span> },
    { key: "title", label: "Request", text: (r) => `${r.title} ${r.summary.join(" ")}`, render: what },
    { key: "opened_at", label: "Raised", width: "160px", render: (r) => when(r.opened_at) },
    { key: "status", label: "State", width: "110px", text: (r) => STATE[r.status]?.label || r.status, render: (r) => <span className={STATE[r.status]?.cls || "badge-purple"}>{STATE[r.status]?.label || r.status}</span> },
    { key: "waiting_on", label: "With", width: "230px", text: (r) => (r.status === "pending" ? r.waiting_on || "" : r.decided_by || ""),
      render: (r) => r.status === "pending"
        ? <span className="text-xs text-gray-500">{r.waiting_on} · {age(r.age_days)}</span>
        : <span className="text-xs text-gray-500">{r.decided_by ? `${r.decided_by} · ` : ""}{when(r.decided_at)}</span> },
    { key: "comment", label: "Comment", className: "text-xs text-gray-500 max-w-[220px]" },
    { key: "act", label: "", sortable: false, width: "150px", text: () => "",
      render: (r) => <span className="inline-flex gap-1.5 justify-end w-full">{(r.history?.length || 0) > 0 && <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => setDetail(r)}>History</button>}{openLink(r.link)}</span> },
  ];
  if (q.isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (q.isError) return <div className="card text-sm text-red-600">Could not load your requests. {errText(q.error)}</div>;
  return (
    <div>
      <KindChips kinds={q.data?.by_kind || []} value={kind} onChange={setKind} />
      <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName="my-requests" minWidth={1040} searchPlaceholder="Search my requests…"
        empty="You have not raised anything that needs approval. Requests start on their own screens, for example Leave or Timesheets."
        filters={<select className="input !py-1.5 text-sm !w-auto" value={state} onChange={(e) => setState(e.target.value)} aria-label="State">
          <option value="">All states</option>{Object.entries(STATE).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select>} />
      {detail && (
        <WideModal title={`History — ${detail.title}`} onClose={() => setDetail(null)}>
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase text-gray-500"><tr><th className="py-1.5 pr-3">When</th><th className="pr-3">Decision</th><th className="pr-3">By</th><th className="pr-3">Comment</th><th>Recorded from</th></tr></thead>
            <tbody>{(detail.history || []).map((h, i) => (
              <tr key={i} className="border-t border-surface-border align-top"><td className="py-1.5 pr-3 whitespace-nowrap">{when(h.at)}</td>
                <td className="pr-3 capitalize">{h.decision}</td><td className="pr-3">{h.by || "—"}{h.on_behalf_of ? ` (for ${h.on_behalf_of})` : ""}</td>
                <td className="pr-3 text-gray-600">{h.comment || "—"}</td><td className="text-gray-500">{h.recorded_from}</td></tr>))}</tbody>
          </table>
        </WideModal>
      )}
    </div>
  );
}

function Decided() {
  const q = useQuery({ queryKey: ["apr", "decided"], queryFn: approvalsApi.decided });
  const cols: GridColumn<DecidedRow>[] = [
    { key: "at", label: "When", width: "160px", render: (r) => when(r.at) },
    { key: "kind_label", label: "Type", width: "140px" },
    { key: "title", label: "Request", text: (r) => `${r.title} ${r.summary.join(" ")}`, render: what },
    { key: "requester_name", label: "Raised by", width: "160px" },
    { key: "decision", label: "Decision", width: "110px", text: (r) => r.decision, render: (r) => <span className={STATE[r.decision]?.cls || "badge-purple"}>{STATE[r.decision]?.label || r.decision}</span> },
    { key: "on_behalf_of", label: "For", width: "140px" },
    { key: "comment", label: "Comment", className: "text-xs text-gray-500 max-w-[240px]" },
  ];
  if (q.isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (q.isError) return <div className="card text-sm text-red-600">Could not load your decisions. {errText(q.error)}</div>;
  return <DataGrid columns={cols} rows={q.data?.items || []} rowKey={(r) => r.id} exportName="my-decisions" minWidth={1000}
    empty="You have not decided anything yet." searchPlaceholder="Search my decisions…" />;
}

function Delegations({ canSetForOthers }: { canSetForOthers: boolean }) {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user?.id);
  const list = useQuery({ queryKey: ["apr", "delegations"], queryFn: approvalsApi.delegations });
  const people = useQuery({ queryKey: ["apr", "people"], queryFn: approvalsApi.people });
  const today = todayISO();
  const [f, setF] = useState({ from: "", to: "", start: today, end: today, note: "" });
  const refresh = () => qc.invalidateQueries({ queryKey: ["apr"] });
  const create = useMutation({
    mutationFn: () => approvalsApi.createDelegation({ to_user_id: f.to, start_date: f.start, end_date: f.end, note: f.note || undefined, from_user_id: canSetForOthers && f.from ? f.from : undefined }),
    onSuccess: () => { toast.success("Delegation saved. The person has been told."); setF((p) => ({ ...p, to: "", note: "" })); refresh(); },
    onError: (e) => toast.error(errText(e), { duration: 7000 }),
  });
  const cancel = useMutation({ mutationFn: (d: Delegation) => approvalsApi.cancelDelegation(d.id), onSuccess: () => { toast.success("Delegation cancelled"); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const cols: GridColumn<Delegation>[] = [
    { key: "from_name", label: "Out of office" }, { key: "to_name", label: "Approvals go to" },
    { key: "start_date", label: "From", type: "date", width: "130px" }, { key: "end_date", label: "To", type: "date", width: "130px" },
    { key: "state", label: "State", width: "110px", render: (d) => <span className={d.state === "active" ? "badge-green" : d.state === "upcoming" ? "badge-amber" : "badge-purple"}>{d.state}</span> },
    { key: "note", label: "Note", className: "text-xs text-gray-500" },
    { key: "act", label: "", sortable: false, width: "90px", text: () => "",
      render: (d) => (d.state === "active" || d.state === "upcoming") && (d.mine || canSetForOthers)
        ? <button className="btn-secondary !py-1 !px-2 text-xs text-red-600" disabled={cancel.isPending} onClick={() => cancel.mutate(d)}>Cancel</button> : null },
  ];
  const others = (people.data || []).filter((p) => p.id !== (canSetForOthers && f.from ? f.from : me));
  return (
    <div className="space-y-4">
      <div className="card">
        <h3 className="font-display font-semibold text-gray-800 mb-1">Send my approvals to someone else</h3>
        <p className="text-sm text-gray-500 mb-3">Between the two dates, that person can decide anything that waits for you. You can still decide it yourself. The record shows who really decided.</p>
        <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end">
          {canSetForOthers && (
            <div><label className="block text-xs font-semibold text-gray-500 mb-1">Person out of office</label>
              <select className="input" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })}>
                <option value="">Me</option>{(people.data || []).filter((p) => p.id !== me).map((p) => <option key={p.id} value={p.id}>{p.name} — {p.role}</option>)}</select></div>
          )}
          <div><label className="block text-xs font-semibold text-gray-500 mb-1">Approvals go to</label>
            <select className="input" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })}>
              <option value="">Choose a person</option>{others.map((p) => <option key={p.id} value={p.id}>{p.name} — {p.role}</option>)}</select></div>
          <div><label className="block text-xs font-semibold text-gray-500 mb-1">From</label><input type="date" className="input" value={f.start} min={today} onChange={(e) => setF({ ...f, start: e.target.value })} /></div>
          <div><label className="block text-xs font-semibold text-gray-500 mb-1">To</label><input type="date" className="input" value={f.end} min={f.start} onChange={(e) => setF({ ...f, end: e.target.value })} /></div>
          <div className={canSetForOthers ? "" : "lg:col-span-1"}><label className="block text-xs font-semibold text-gray-500 mb-1">Note (optional)</label><input className="input" value={f.note} maxLength={300} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="On leave" /></div>
          <div><button className="btn-primary w-full" disabled={!f.to || !f.start || !f.end || create.isPending} onClick={() => create.mutate()}>Save delegation</button></div>
        </div>
      </div>
      {list.isLoading ? <div className="grid place-items-center py-10"><Spinner className="h-7 w-7" /></div>
        : <DataGrid columns={cols} rows={list.data || []} rowKey={(d) => d.id} exportName="approval-delegations" minWidth={860}
            empty="No delegations yet." searchPlaceholder="Search delegations…" />}
      <p className="text-xs text-gray-400">Reminders: approvers are reminded when a request has waited the number of days set in Company Settings → Approvals (0 switches reminders off). From the second reminder, HR is told as well.</p>
    </div>
  );
}

