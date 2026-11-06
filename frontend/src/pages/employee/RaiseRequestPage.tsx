import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Check, Download, FileText, Inbox, Paperclip, Send, Undo2, X } from "lucide-react";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { PageHeader, Spinner, Empty } from "../../components/ui";
import { Modal, Field, inputCls, err } from "../canada/_shared";
import { useAuthStore } from "../../store/authStore";
import {
  essRequestsApi, EmployeeRequest, HR_ROLES, REQUEST_TONE, RequestField, RequestType,
} from "../../api/essRequests";
import { useUrlTab } from "../../search/useUrlTab";
import { can } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const badge = (s: string) => <span className={REQUEST_TONE[s] || "badge-teal"}>{s}</span>;
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString("en-CA", { day: "numeric", month: "short", year: "numeric" }) : "—");
const LONG_TEXT = /address|reason|detail|purpose|description|note/i;

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** One input per field type — the same field types HR uses for custom fields. */
function FieldInput({ f, value, onChange, employeeId }:
  { f: RequestField; value: any; onChange: (v: any) => void; employeeId?: string | null }) {
  const [uploading, setUploading] = useState(false);
  const [fileName, setFileName] = useState<string>("");
  const v = f.validation || {};
  if (f.type === "select")
    return (
      <select className={inputCls} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">— choose —</option>
        {(f.options || []).map((o) => <option key={o} value={o}>{o}</option>)}
      </select>);
  if (f.type === "multiselect") {
    const cur: string[] = Array.isArray(value) ? value : [];
    return (
      <div className="flex flex-wrap gap-x-4 gap-y-1">{(f.options || []).map((o) => (
        <label key={o} className="text-xs flex items-center gap-1.5">
          <input type="checkbox" checked={cur.includes(o)}
            onChange={(e) => onChange(e.target.checked ? [...cur, o] : cur.filter((x) => x !== o))} />{o}
        </label>))}
      </div>);
  }
  if (f.type === "boolean")
    return (
      <label className="text-xs flex items-center gap-2 h-8">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} /> Yes
      </label>);
  if (f.type === "date")
    return <input type="date" className={inputCls} value={value ?? ""} min={v.min ? String(v.min) : undefined}
      max={v.max ? String(v.max) : undefined} onChange={(e) => onChange(e.target.value)} />;
  if (f.type === "number")
    return <input type="number" className={inputCls} value={value ?? ""} min={v.min as any} max={v.max as any}
      onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />;
  if (f.type === "file")
    return (
      <div className="flex flex-wrap items-center gap-2">
        <label className="btn-secondary !py-1 text-xs cursor-pointer inline-flex items-center gap-1">
          <Paperclip className="w-3.5 h-3.5" /> {value ? "Replace file" : "Attach file"}
          <input type="file" className="hidden" disabled={uploading || !employeeId} onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file || !employeeId) return;
            setUploading(true);
            try {
              const doc = await essRequestsApi.uploadAttachment(employeeId, file);
              onChange(doc.id); setFileName(doc.file_name || file.name);
            } catch (ex) { toast.error(err(ex, "Upload failed")); } finally { setUploading(false); e.target.value = ""; }
          }} />
        </label>
        {uploading && <Spinner className="h-4 w-4" />}
        {value && <span className="text-xs text-gray-600 truncate max-w-[14rem]">{fileName || "file attached"}</span>}
        {value && <button type="button" className="text-xs text-red-600 hover:underline" onClick={() => { onChange(""); setFileName(""); }}>Remove</button>}
      </div>);
  if (f.type === "text" && LONG_TEXT.test(f.label + " " + f.key))
    return <textarea className="input-base text-xs w-full min-h-[72px]" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
  return <input className={inputCls} value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
}

const emptyVal = (v: any) => v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length);

function RaiseModal({ type, onClose }: { type: RequestType; onClose: () => void }) {
  const qc = useQueryClient();
  const employeeId = useAuthStore((s) => s.user?.employee_id);
  const fields = (type.fields || []).filter((f) => f.active !== false);
  const [vals, setVals] = useState<Record<string, any>>({});
  const send = useMutation({
    mutationFn: () => {
      const miss = fields.find((f) => f.required && emptyVal(vals[f.key]));
      if (miss) return Promise.reject({ response: { data: { detail: `${miss.label} is required` } } });
      const clean = Object.fromEntries(Object.entries(vals).filter(([, v]) => !emptyVal(v)));
      return essRequestsApi.raise(type.id, clean);
    },
    onSuccess: () => { toast.success("Request sent for approval"); qc.invalidateQueries({ queryKey: ["ess-requests"] }); onClose(); },
    onError: (e) => toast.error(err(e, "Could not send the request")),
  });
  return (
    <Modal title={type.name} sub={type.description || undefined} onClose={onClose} width="max-w-4xl"
      footer={<>
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary inline-flex items-center gap-1" disabled={send.isPending || !employeeId} onClick={() => send.mutate()}>
          <Send className="w-4 h-4" /> Send request
        </button></>}>
      {!employeeId && <p className="text-xs text-red-600 mb-3">Your login is not linked to an employee record, so you cannot raise a request.</p>}
      {type.handler_note && <p className="text-xs text-gray-500 mb-3"><b>Who handles it:</b> {type.handler_note}</p>}
      {!fields.length ? <p className="text-sm text-gray-500">This request has no form — just send it.</p> : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {fields.map((f) => (
            <Field key={f.key} label={f.label + (f.required ? " *" : "")}
              className={f.type === "text" && LONG_TEXT.test(f.label + " " + f.key) || f.type === "multiselect" ? "md:col-span-2" : ""}>
              <FieldInput f={f} value={vals[f.key]} employeeId={employeeId}
                onChange={(v) => setVals((s) => ({ ...s, [f.key]: v }))} />
              {f.validation?.help && <span className="block text-[11px] text-gray-400 mt-1">{f.validation.help}</span>}
            </Field>))}
        </div>)}
    </Modal>
  );
}

function DetailModal({ r, onClose }: { r: EmployeeRequest; onClose: () => void }) {
  return (
    <Modal title={`${r.type_name} · ${r.ref}`} sub={`${r.employee_name || ""} · raised ${day(r.created_at)}`} onClose={onClose} width="max-w-3xl"
      footer={<button className="btn-secondary" onClick={onClose}>Close</button>}>
      <div className="flex items-center gap-2 mb-3">{badge(r.status)}
        {r.decided_by_name && <span className="text-xs text-gray-500">by {r.decided_by_name} on {day(r.decided_at)}</span>}</div>
      {r.decision_note && <p className="text-xs text-gray-700 mb-3"><b>Note:</b> {r.decision_note}</p>}
      <dl className="grid grid-cols-1 sm:grid-cols-[12rem_1fr] gap-x-4 gap-y-2 text-sm">
        {r.rows.map((x) => (
          <div key={x.key} className="contents">
            <dt className="text-[11px] font-semibold uppercase tracking-wide text-gray-500 pt-0.5">{x.label}</dt>
            <dd className="text-gray-800 break-words">{x.type === "file"
              ? <button className="text-primary-600 hover:underline inline-flex items-center gap-1 text-xs"
                  onClick={() => essRequestsApi.file(r.id, x.key).then((b) => saveBlob(b, `${r.ref}-${x.key}`)).catch((e) => toast.error(err(e)))}>
                  <Download className="w-3.5 h-3.5" /> Download</button>
              : x.display || "—"}</dd>
          </div>))}
      </dl>
    </Modal>
  );
}

function DecideModal({ r, verdict, onClose }: { r: EmployeeRequest; verdict: "approve" | "reject"; onClose: () => void }) {
  const qc = useQueryClient();
  const [comment, setComment] = useState("");
  const go = useMutation({ mutationFn: () => essRequestsApi.decide(r.id, verdict, comment),
    onSuccess: () => { toast.success(verdict === "approve" ? "Approved" : "Rejected"); qc.invalidateQueries({ queryKey: ["ess-requests"] }); onClose(); },
    onError: (e) => toast.error(err(e)) });
  return (
    <Modal title={`${verdict === "approve" ? "Approve" : "Reject"} — ${r.type_name}`} sub={`${r.employee_name} · ${r.ref}`} onClose={onClose} width="max-w-3xl"
      footer={<>
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={go.isPending || (verdict === "reject" && !comment.trim())} onClick={() => go.mutate()}>
          {verdict === "approve" ? "Approve" : "Reject"}</button></>}>
      <p className="text-xs text-gray-600 mb-3">{r.summary}</p>
      <Field label={verdict === "reject" ? "Reason (the employee sees this) *" : "Note to the employee (optional)"}>
        <textarea className="input-base text-xs w-full min-h-[80px]" value={comment} onChange={(e) => setComment(e.target.value)} />
      </Field>
    </Modal>
  );
}

export default function RaiseRequestPage() {
  const user = useAuthStore((s) => s.user);
  const qc = useQueryClient();
  const isHr = can(user, ["people.approve", "people.edit"], HR_ROLES);
  const isMgr = !isHr && can(user, "people.approve", ["manager"]);
  const [tab, setTab] = useUrlTab<"raise" | "mine" | "all">("raise");   // ?tab= (global search deep link)
  const [open, setOpen] = useState<RequestType | null>(null);
  const [view, setView] = useState<EmployeeRequest | null>(null);
  const [decide, setDecide] = useState<{ r: EmployeeRequest; v: "approve" | "reject" } | null>(null);
  const [fStatus, setFStatus] = useState("pending");
  const [fType, setFType] = useState("");

  const types = useQuery({ queryKey: ["ess-request-types"], queryFn: () => essRequestsApi.types() });
  const list = useQuery({ queryKey: ["ess-requests", "list"], queryFn: () => essRequestsApi.list() });
  const withdraw = useMutation({ mutationFn: (id: string) => essRequestsApi.withdraw(id),
    onSuccess: () => { toast.success("Request withdrawn"); qc.invalidateQueries({ queryKey: ["ess-requests"] }); },
    onError: (e) => toast.error(err(e)) });

  const rows = list.data || [];
  const mine = rows.filter((r) => r.employee_id === user?.employee_id);
  const others = rows.filter((r) => r.employee_id !== user?.employee_id)
    .filter((r) => (!fStatus || r.status === fStatus) && (!fType || r.type_id === fType));
  const byCat = useMemo(() => {
    const m = new Map<string, RequestType[]>();
    for (const t of types.data || []) { const c = t.category || "General"; m.set(c, [...(m.get(c) || []), t]); }
    return [...m.entries()];
  }, [types.data]);

  const mineCols: GridColumn<EmployeeRequest>[] = [
    { key: "ref", label: "Ref", width: "7rem" },
    { key: "type_name", label: "Request" },
    { key: "summary", label: "Details", render: (r) => <span className="truncate block max-w-[22rem]" title={r.summary}>{r.summary}</span>, text: (r) => r.summary },
    { key: "status", label: "Status", text: (r) => r.status, render: (r) => badge(r.status) },
    { key: "created_at", label: "Raised", type: "date" },
    { key: "decided_by_name", label: "Decided by" },
    { key: "decision_note", label: "Note" },
    { key: "_a", label: "", sortable: false, render: (r) => (
      <span className="flex gap-2 justify-end">
        <button className="text-xs text-primary-600 hover:underline" onClick={(e) => { e.stopPropagation(); setView(r); }}>View</button>
        {r.can_withdraw && <button className="text-xs text-red-600 hover:underline inline-flex items-center gap-0.5"
          onClick={(e) => { e.stopPropagation(); withdraw.mutate(r.id); }}><Undo2 className="w-3 h-3" />Withdraw</button>}
      </span>) },
  ];
  const allCols: GridColumn<EmployeeRequest>[] = [
    { key: "ref", label: "Ref", width: "7rem" },
    { key: "employee_name", label: "Employee" },
    { key: "department", label: "Department" },
    { key: "type_name", label: "Request" },
    { key: "summary", label: "Details", render: (r) => <span className="truncate block max-w-[20rem]" title={r.summary}>{r.summary}</span>, text: (r) => r.summary },
    { key: "status", label: "Status", text: (r) => r.status, render: (r) => badge(r.status) },
    { key: "created_at", label: "Raised", type: "date" },
    { key: "decided_by_name", label: "Decided by" },
    { key: "_a", label: "", sortable: false, render: (r) => (
      <span className="flex gap-2 justify-end">
        <button className="text-xs text-primary-600 hover:underline" onClick={(e) => { e.stopPropagation(); setView(r); }}>View</button>
        {r.can_decide && <>
          <button className="text-xs text-green-700 hover:underline inline-flex items-center gap-0.5" onClick={(e) => { e.stopPropagation(); setDecide({ r, v: "approve" }); }}><Check className="w-3 h-3" />Approve</button>
          <button className="text-xs text-red-600 hover:underline inline-flex items-center gap-0.5" onClick={(e) => { e.stopPropagation(); setDecide({ r, v: "reject" }); }}><X className="w-3 h-3" />Reject</button>
        </>}
      </span>) },
  ];

  const tabs: [typeof tab, string, number | null][] = [["raise", "Raise a request", null], ["mine", "My requests", mine.length]];
  if (isHr || isMgr) tabs.push(["all", isHr ? "All requests" : "Team requests", rows.filter((r) => r.employee_id !== user?.employee_id && r.status === "pending").length]);

  return (
    <div className="max-w-[1400px] mx-auto">
      <PageHeader title="Raise a request" icon={Inbox}
        subtitle="Ask HR for a letter, an address change, an ID card and more — every request goes through your company's approval chain." />
      <div className="flex gap-1 border-b border-surface-border mb-4 overflow-x-auto">
        {tabs.map(([k, label, n]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px ${tab === k ? "border-primary-500 text-primary-700 font-semibold" : "border-transparent text-gray-500 hover:text-gray-700"}`}>
            {label}{n ? <span className="ml-1.5 badge-teal">{n}</span> : null}
          </button>))}
      </div>

      {tab === "raise" && (types.isLoading ? <div className="flex justify-center py-10"><Spinner /></div>
        : !(types.data || []).length ? <Empty>HR has not set up any request types yet.</Empty> : (
          // one grid in HR's order (category as a label on the card) — a category with one type no longer
          // leaves three empty columns next to it
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">{byCat.flatMap(([cat, ts]) => ts.map((t) => (
            <button key={t.id} onClick={() => setOpen(t)} data-testid={`req-type-${t.key}`}
              className="card text-left hover:border-primary-300 hover:shadow-md transition !p-4 flex flex-col gap-1.5">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">{cat}</span>
              <span className="flex items-center gap-2 font-display font-semibold text-gray-800 text-sm"><FileText className="w-4 h-4 text-primary-500 shrink-0" />{t.name}</span>
              {t.description && <span className="text-xs text-gray-500 line-clamp-2">{t.description}</span>}
              <span className="text-[11px] text-gray-400 mt-auto">{t.fields.length} field(s){t.handler_note ? ` · ${t.handler_note}` : ""}</span>
            </button>)))}
          </div>))}

      {tab === "mine" && (
        <DataGrid columns={mineCols} rows={mine} rowKey={(r) => r.id} exportName="my-requests" minWidth={900}
          onRowClick={setView} empty="You have not raised any request yet." />)}

      {tab === "all" && (isHr || isMgr) && (
        <DataGrid columns={allCols} rows={others} rowKey={(r) => r.id} exportName="employee-requests" minWidth={1100}
          onRowClick={setView} empty="No requests match."
          filters={<div className="flex flex-wrap gap-2">
            <select className="input-base h-8 text-xs" value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
              <option value="">All statuses</option>
              {["pending", "approved", "rejected", "withdrawn"].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select className="input-base h-8 text-xs" value={fType} onChange={(e) => setFType(e.target.value)}>
              <option value="">All types</option>
              {(types.data || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>} />)}

      {open && <RaiseModal type={open} onClose={() => { setOpen(null); setTab("mine"); }} />}
      {view && <DetailModal r={view} onClose={() => setView(null)} />}
      {decide && <DecideModal r={decide.r} verdict={decide.v} onClose={() => setDecide(null)} />}
    </div>
  );
}
