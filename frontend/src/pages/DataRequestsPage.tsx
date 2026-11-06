import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Inbox, Download, FileJson, AlertTriangle, Clock } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { privacyApi, saveBlob, type DataRequestRow } from "../api/privacy";
import { employeeApi } from "../api/endpoints";
import { useAuthStore } from "../store/authStore";

const ADMIN = ["superadmin", "org_admin", "hr_admin", "hr_manager"];
const TYPES = [
  { key: "access", label: "Access my information", help: "Get a copy of the personal information the company holds about you (JSON + PDF)." },
  { key: "correction", label: "Correct my information", help: "Tell us which fields are wrong and the right values." },
  { key: "erasure", label: "Delete information no longer needed", help: "Ask for personal information to be removed once it is no longer needed. Payroll and tax records stay for the period the law requires (CRA: 6 years)." },
  { key: "nomination", label: "Authorise a representative", help: "Name a person who may make privacy requests on your behalf." },
];
const BAND: Record<string, string> = { ok: "badge-green", soon: "badge-amber", urgent: "badge-red", overdue: "badge-red", closed: "badge-teal", none: "badge-purple" };
const inputCls = "w-full border border-surface-border rounded-lg px-3 py-1.5 text-sm bg-white";
const th = "px-3 py-2 text-left text-[11px] uppercase tracking-wide text-gray-400 font-semibold";
const td = "px-3 py-1.5 text-sm text-gray-700 border-t border-surface-border/70";
const fmt = (s?: string | null) => (s ? s.slice(0, 10) : "—");

export default function DataRequestsPage() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const isAdmin = ADMIN.includes(role);
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [q, setQ] = useState("");
  const { data, isLoading } = useQuery({ queryKey: ["privacy", "requests", status, type, q], queryFn: () => privacyApi.requests({ status: status || undefined, request_type: type || undefined, q: q || undefined }) });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["privacy"] });
  const items = data?.items || [];
  const csv = () => {
    const head = ["ref", "employee_name", "type_label", "status", "submitted_at", "due_at", "days_left", "assigned_to"];
    saveBlob(new Blob([[head.join(","), ...items.map((r: any) => head.map((h) => JSON.stringify(r[h] ?? "")).join(","))].join("\n")], { type: "text/csv" }), "data-requests.csv");
  };
  return (
    <div>
      <PageHeader title="Privacy requests" icon={Inbox} subtitle="Your rights under PIPEDA — access, correction, deletion, a representative · answered within 30 days" />
      {isAdmin && data && <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="Open" value={data.open} icon={Inbox} accent />
        <StatTile label="Past the 30-day limit" value={data.overdue} icon={AlertTriangle} />
        <StatTile label="Due in 14 days" value={items.filter((r) => r.band === "urgent").length} icon={Clock} />
        <StatTile label="Total ever" value={data.total} />
      </div>}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        <div className="lg:col-span-3 card !p-0">
          <div className="px-4 py-2.5 border-b border-surface-border flex flex-wrap items-center gap-2">
            <span className="font-display font-semibold text-gray-800">{isAdmin ? "Request queue" : "My requests"}</span>
            <select className="border border-surface-border rounded-lg px-2 py-1 text-xs ml-auto" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">all statuses</option>{["open", "in_progress", "on_hold", "completed", "rejected"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</select>
            <select className="border border-surface-border rounded-lg px-2 py-1 text-xs" value={type} onChange={(e) => setType(e.target.value)}>
              <option value="">all types</option>{TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}</select>
            {isAdmin && <input className="border border-surface-border rounded-lg px-2 py-1 text-xs" placeholder="Search ref / name" value={q} onChange={(e) => setQ(e.target.value)} />}
            <button onClick={csv} className="text-xs inline-flex items-center gap-1 border border-surface-border rounded-lg px-2 py-1 bg-white hover:bg-gray-50"><Download className="w-3 h-3" /> CSV</button>
          </div>
          {isLoading ? <div className="grid place-items-center py-10"><Spinner /></div> : (
            <div className="overflow-x-auto"><table className="w-full">
              <thead><tr><th className={th}>Ref</th>{isAdmin && <th className={th}>Employee</th>}<th className={th}>Type</th><th className={th}>Raised</th><th className={th}>Due</th><th className={th}>SLA</th><th className={th}>Status</th><th className={th}>Assigned</th><th className={th}></th></tr></thead>
              <tbody>{items.map((r) => <Row key={r.id} r={r} isAdmin={isAdmin} onChange={invalidate} />)}
                {!items.length && <tr><td colSpan={9}><Empty>No requests.</Empty></td></tr>}</tbody>
            </table></div>)}
        </div>
        <NewRequest isAdmin={isAdmin} onCreated={invalidate} />
      </div>
    </div>
  );
}

function Row({ r, isAdmin, onChange }: { r: DataRequestRow; isAdmin: boolean; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(r.resolution_note || "");
  const [hold, setHold] = useState(r.hold_reason || "");
  const [assignee, setAssignee] = useState(r.assigned_to || "");
  const patch = useMutation({ mutationFn: (body: any) => privacyApi.patchRequest(r.id, body), onSuccess: () => { toast.success("Updated"); onChange(); }, onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not update") });
  const erase = useMutation({ mutationFn: () => privacyApi.applyErasure(r.id),
    onSuccess: (res) => { toast.success(res.status === "on_hold" ? "Put on hold — statutory retention" : "Erasure applied"); onChange(); }, onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not apply") });
  const exportFor = async (pdf: boolean) => {
    try {
      if (pdf) saveBlob(await privacyApi.exportMyDataPdf(isAdmin ? r.employee_id : undefined), `${r.ref}-data.pdf`);
      else saveBlob(new Blob([JSON.stringify(await privacyApi.exportMyData(isAdmin ? r.employee_id : undefined), null, 1)], { type: "application/json" }), `${r.ref}-data.json`);
    } catch { toast.error("Export failed"); }
  };
  const colSpan = isAdmin ? 9 : 8;
  return (<>
    <tr className={r.overdue ? "bg-rose-50/50" : ""}>
      <td className={`${td} font-mono text-xs`}>{r.ref}</td>
      {isAdmin && <td className={`${td} font-medium text-gray-800`}>{r.employee_name || "—"}</td>}
      <td className={td}>{r.type_label}</td>
      <td className={`${td} text-xs`}>{fmt(r.submitted_at)}</td>
      <td className={`${td} text-xs`}>{fmt(r.due_at)}</td>
      <td className={td}><span className={`badge ${BAND[r.band] || "badge-purple"}`}>{r.band === "closed" ? "closed" : r.overdue ? `${-r.days_left!}d overdue` : `${r.days_left}d left`}</span></td>
      <td className={td}><span className="badge badge-teal">{r.status.replace("_", " ")}</span></td>
      <td className={`${td} text-xs`}>{r.assigned_to || "—"}</td>
      <td className={`${td} text-right`}><button className="text-xs text-primary-700 hover:underline" onClick={() => setOpen(!open)}>{open ? "Close" : "Open"}</button></td>
    </tr>
    {open && <tr><td colSpan={colSpan} className="bg-gray-50/70 px-4 py-3">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 text-sm">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-gray-400 font-semibold">Details</div>
          <div className="text-gray-700 whitespace-pre-wrap">{r.details || "—"}</div>
          {r.payload && <pre className="text-[11px] text-gray-500 mt-1">{JSON.stringify(r.payload, null, 1)}</pre>}
          {r.hold_reason && <div className="mt-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2"><b>On hold:</b> {r.hold_reason}</div>}
          {r.resolution_note && <div className="mt-2 text-xs text-gray-600"><b>Resolution:</b> {r.resolution_note}</div>}
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-gray-400 font-semibold">Export (access right)</div>
          <div className="flex gap-2 mt-1">
            <button onClick={() => exportFor(false)} className="text-xs inline-flex items-center gap-1 border border-surface-border rounded-lg px-2 py-1 bg-white hover:bg-gray-50"><FileJson className="w-3 h-3" /> JSON</button>
            <button onClick={() => exportFor(true)} className="text-xs inline-flex items-center gap-1 border border-surface-border rounded-lg px-2 py-1 bg-white hover:bg-gray-50"><Download className="w-3 h-3" /> PDF</button>
          </div>
          {isAdmin && r.request_type === "erasure" && r.status !== "completed" && <button onClick={() => { if (confirm("Apply erasure now? Contact, emergency, representative and bank details will be blanked. The SIN and payroll records stay encrypted for the retention period.")) erase.mutate(); }} className="mt-3 text-xs rounded-lg bg-rose-600 text-white font-semibold px-3 py-1.5">Apply erasure</button>}
        </div>
        {isAdmin && <div className="space-y-1.5">
          <div className="text-[11px] uppercase tracking-wide text-gray-400 font-semibold">Work it</div>
          <input className={inputCls} placeholder="Assigned to (email)" value={assignee} onChange={(e) => setAssignee(e.target.value)} />
          <textarea className={inputCls} rows={2} placeholder="Resolution note (what you did / sent)" value={note} onChange={(e) => setNote(e.target.value)} />
          <input className={inputCls} placeholder="Hold reason (needed for 'on hold')" value={hold} onChange={(e) => setHold(e.target.value)} />
          <div className="flex flex-wrap gap-1.5">
            <button className="text-xs border border-surface-border rounded px-2 py-1 bg-white" onClick={() => patch.mutate({ assigned_to: assignee, resolution_note: note, hold_reason: hold || null })}>Save</button>
            {["in_progress", "on_hold", "completed", "rejected"].map((s) => <button key={s} className={`text-xs rounded px-2 py-1 text-white ${s === "completed" ? "bg-emerald-600" : s === "rejected" ? "bg-rose-600" : "bg-gray-500"}`} onClick={() => patch.mutate({ status: s, assigned_to: assignee || undefined, resolution_note: note || undefined, hold_reason: hold || undefined })}>{s.replace("_", " ")}</button>)}
          </div>
        </div>}
      </div>
    </td></tr>}
  </>);
}

function NewRequest({ isAdmin, onCreated }: { isAdmin: boolean; onCreated: () => void }) {
  const me = useAuthStore((s) => s.user);
  const [type, setType] = useState("access");
  const [details, setDetails] = useState("");
  const [empId, setEmpId] = useState("");
  const [nominee, setNominee] = useState({ nominee_name: "", nominee_relationship: "", nominee_phone: "" });
  const { data: emps } = useQuery({ queryKey: ["employees"], queryFn: employeeApi.list, enabled: isAdmin });
  const create = useMutation({
    mutationFn: () => privacyApi.createRequest({ employee_id: isAdmin ? (empId || undefined) : undefined, request_type: type, details, payload: type === "nomination" ? nominee : undefined }),
    onSuccess: (r) => { toast.success(`Request ${r.ref} raised — due ${fmt(r.due_at)}`); setDetails(""); onCreated(); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not raise the request"),
  });
  const t = TYPES.find((x) => x.key === type)!;
  const needsEmp = isAdmin && !empId;
  return (
    <div className="card">
      <div className="font-display font-semibold text-gray-800 mb-1">Raise a request</div>
      {isAdmin && <>
        <label className="text-[11px] text-gray-500">On behalf of</label>
        <select className={`${inputCls} mb-2`} value={empId} onChange={(e) => setEmpId(e.target.value)}>
          <option value="">— choose employee —</option>{((emps || []) as any[]).map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name} · {e.employee_number}</option>)}</select>
      </>}
      {!isAdmin && !me?.employee_id && <div className="text-xs text-rose-600 mb-2">Your login is not linked to an employee record — ask HR.</div>}
      <div className="grid grid-cols-2 gap-1.5 mb-2">
        {TYPES.map((x) => <button key={x.key} onClick={() => setType(x.key)} className={`rounded-lg border px-2 py-1.5 text-xs font-medium ${type === x.key ? "border-primary-500 bg-primary-50 text-primary-700" : "border-surface-border text-gray-600"}`}>{x.label}</button>)}
      </div>
      <p className="text-[11px] text-gray-400 mb-2">{t.help}</p>
      {type === "nomination" && <div className="space-y-1.5 mb-2">
        <input className={inputCls} placeholder="Representative name" value={nominee.nominee_name} onChange={(e) => setNominee({ ...nominee, nominee_name: e.target.value })} />
        <input className={inputCls} placeholder="Relationship" value={nominee.nominee_relationship} onChange={(e) => setNominee({ ...nominee, nominee_relationship: e.target.value })} />
        <input className={inputCls} placeholder="Phone" value={nominee.nominee_phone} onChange={(e) => setNominee({ ...nominee, nominee_phone: e.target.value })} />
      </div>}
      <textarea className={inputCls} rows={3} placeholder={type === "correction" ? "Which field is wrong and what it should be" : "Anything we should know"} value={details} onChange={(e) => setDetails(e.target.value)} />
      <button onClick={() => create.mutate()} disabled={create.isPending || needsEmp || (!isAdmin && !me?.employee_id)} className="w-full mt-2 rounded-lg bg-primary-600 hover:bg-primary-700 text-white text-sm font-semibold py-2 disabled:opacity-50">Submit — answered within 30 days</button>
    </div>
  );
}
