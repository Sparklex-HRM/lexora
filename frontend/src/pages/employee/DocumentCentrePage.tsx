import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import {
  FolderOpen, FileText, FileSignature, Receipt, ShieldCheck, Upload, Download, BookOpen, CheckCircle2,
  Send, Plus, Archive, RotateCcw, Layers, ExternalLink,
} from "lucide-react";
import { Printer } from "lucide-react";
import { printPdfBlob } from "../../lib/print";
import { PageHeader, Spinner, Empty, StatTile } from "../../components/ui";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Modal, Field, inputCls, err } from "../canada/_shared";
import { useAuthStore } from "../../store/authStore";
import { lettersApi, openPdf } from "../../api/lifecycle";
import { LetterList } from "../../components/lifecycle/EmployeeLifecyclePanel";
import { essDocsApi, DOC_TYPES, MyDoc, Policy, AckRow } from "../../api/essDocs";
import { useUrlTab } from "../../search/useUrlTab";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/* Document centre (employee portal, batch 3): my documents · letters · payslips & tax (links only —
   no amounts on this page) · company policies with "I have read and accept" · HR publish & track. */

const HR = ["superadmin", "org_admin", "hr_admin", "hr_manager"];
type Tab = "docs" | "letters" | "pay" | "policies" | "manage";

const TYPE_LABEL: Record<string, string> = Object.fromEntries(DOC_TYPES.map((t) => [t.value, t.label]));
Object.assign(TYPE_LABEL, { leave_attachment: "Leave attachment", request_attachment: "Request attachment" });
// System captures that are not "documents" to a person (attendance selfies, asset photos).
const HIDDEN_TYPES = new Set(["punch_selfie", "asset_photo"]);
function groupOf(t: string) {
  if (["sin_letter", "photo_id", "work_permit", "td1", "photo", "address_proof", "bank_proof"].includes(t)) return "Identity & bank";
  if (["education", "experience"].includes(t)) return "Education & experience";
  if (t.endsWith("_attachment")) return "Attachments to requests";
  return "Other";
}
const typeLabel = (t: string) => TYPE_LABEL[t] || t.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
const fmtDate = (s?: string | null) => (s ? new Date(s).toLocaleDateString("en-CA", { day: "2-digit", month: "short", year: "numeric" }) : "—");
const fmtWhen = (s?: string | null) => (s ? new Date(s).toLocaleString("en-CA", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");
const kb = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

const STATUS_BADGE: Record<string, [string, string]> = {
  acknowledged: ["badge-green", "Accepted"], pending: ["badge-amber", "To accept"],
  not_required: ["badge-teal", "For information"], archived: ["badge-purple", "Archived"],
};
function StatusBadge({ s }: { s: string }) {
  const [cls, label] = STATUS_BADGE[s] || ["badge-purple", s];
  return <span className={cls}>{label}</span>;
}

export default function DocumentCentrePage() {
  const user = useAuthStore((s) => s.user);
  const isHr = !!user && HR.includes(user.role);
  const [tab, setTab] = useUrlTab<Tab>("docs");   // ?tab= (global search deep link)
  const summary = useQuery({ queryKey: ["doc-centre-summary"], queryFn: essDocsApi.summary, retry: false });
  const s = summary.data;
  const tabs: { key: Tab; label: string; icon: any; count?: number }[] = [
    { key: "docs", label: "My documents", icon: FileText, count: s?.my_documents },
    { key: "letters", label: "Letters", icon: FileSignature, count: s?.letters },
    { key: "pay", label: "Payslips & tax", icon: Receipt },
    { key: "policies", label: "Policies", icon: ShieldCheck, count: s?.policies_to_acknowledge || undefined },
    ...(isHr ? [{ key: "manage" as Tab, label: "Publish & track", icon: Layers, count: s?.hr?.pending_acknowledgements || undefined }] : []),
  ];
  return (
    <div className="min-w-0">
      <PageHeader title="Document centre" icon={FolderOpen}
        subtitle="Your documents, letters, payslips and company policies in one place." />
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <StatTile label="My documents" value={s?.my_documents ?? "—"} icon={FileText} />
        <StatTile label="Letters" value={s?.letters ?? "—"} icon={FileSignature} />
        <StatTile label="Policies" value={s?.policies ?? "—"} icon={BookOpen} />
        <StatTile label="To accept" value={s?.policies_to_acknowledge ?? "—"} icon={ShieldCheck} accent={!!s?.policies_to_acknowledge} />
      </div>
      <div className="flex gap-1 overflow-x-auto border-b border-surface-border mb-4 -mx-1 px-1">
        {tabs.map((t) => (
          <button key={t.key} type="button" onClick={() => setTab(t.key)}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px ${tab === t.key ? "border-primary-500 text-primary-700 font-semibold" : "border-transparent text-gray-500 hover:text-gray-800"}`}>
            <t.icon className="w-4 h-4" />{t.label}
            {!!t.count && <span className="ml-1 text-[10px] rounded-full bg-primary-50 text-primary-700 px-1.5">{t.count}</span>}
          </button>
        ))}
      </div>
      {tab === "docs" && <MyDocuments employeeId={user?.employee_id || null} email={user?.email || ""} />}
      {tab === "letters" && <Letters />}
      {tab === "pay" && <PayLinks />}
      {tab === "policies" && <Policies />}
      {tab === "manage" && isHr && <PublishTrack />}
    </div>
  );
}

/* ─────────────────────────── My documents ─────────────────────────── */
function MyDocuments({ employeeId, email }: { employeeId: string | null; email: string }) {
  const qc = useQueryClient();
  const [docType, setDocType] = useState("other");
  const [file, setFile] = useState<File | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const docs = useQuery({ queryKey: ["my-docs", employeeId], queryFn: () => essDocsApi.myDocs(employeeId!), enabled: !!employeeId, retry: false });
  const up = useMutation({
    mutationFn: () => essDocsApi.uploadMyDoc(employeeId!, file!, docType),
    onSuccess: () => {
      toast.success("Document uploaded");
      setFile(null); setInputKey((k) => k + 1);
      qc.invalidateQueries({ queryKey: ["my-docs"] }); qc.invalidateQueries({ queryKey: ["doc-centre-summary"] });
    },
    onError: (e) => toast.error(err(e, "Could not upload the document")),
  });
  const rows = useMemo(() => (docs.data || []).filter((d) => !HIDDEN_TYPES.has(d.doc_type))
    .map((d) => ({ ...d, type_label: typeLabel(d.doc_type), group: groupOf(d.doc_type),
      source: d.uploaded_by_name && email && d.uploaded_by_name.toLowerCase() === email.toLowerCase() ? "You" : "HR" })), [docs.data, email]);
  if (!employeeId) return <div className="card"><Empty>Your login is not linked to an employee record, so there are no personal documents to show.</Empty></div>;
  const open = async (d: MyDoc) => {
    try {
      const b = await essDocsApi.downloadMyDoc(employeeId, d.id);
      d.content_type === "application/pdf" ? openPdf(b, d.file_name) : saveBlob(b, d.file_name);
    } catch (e) { toast.error(err(e, "Could not open the document")); }
  };
  const print = async (d: MyDoc) => {
    try { printPdfBlob(await essDocsApi.downloadMyDoc(employeeId, d.id), d.file_name); } catch (e) { toast.error(err(e, "Could not open the document")); }
  };
  const cols: GridColumn<any>[] = [
    { key: "type_label", label: "Type" },
    { key: "file_name", label: "File", render: (r) => <button type="button" className="text-primary-700 hover:underline text-left" onClick={() => open(r)}>{r.file_name}</button>, text: (r) => r.file_name },
    { key: "source", label: "Added by", render: (r) => <span className={r.source === "You" ? "badge-teal" : "badge-purple"}>{r.source}</span>, text: (r) => r.source },
    { key: "uploaded_at", label: "Added on", type: "date" },
    { key: "size_bytes", label: "Size", render: (r) => kb(r.size_bytes), text: (r) => String(r.size_bytes) },
    { key: "verified", label: "Checked by HR", render: (r) => r.verified ? <span className="badge-green">Checked</span> : <span className="badge-amber">Not yet</span>, text: (r) => (r.verified ? "Checked" : "Not yet") },
    { key: "dl", label: "", sortable: false, render: (r) => <span className="inline-flex"><button type="button" className="text-gray-500 hover:text-gray-800" title="Download" onClick={() => open(r)}><Download className="w-4 h-4" /></button>{r.content_type === "application/pdf" && <button type="button" className="ml-2 text-gray-500 hover:text-gray-800" title="Print (A4)" onClick={() => print(r)}><Printer className="w-4 h-4" /></button>}</span>, text: () => "" },
  ];
  return (
    <div className="space-y-3">
      <div className="card">
        <div className="text-sm font-semibold text-gray-800 mb-2 flex items-center gap-1.5"><Upload className="w-4 h-4" />Add a document</div>
        <div className="grid grid-cols-1 sm:grid-cols-[220px_1fr_auto] gap-2 items-end">
          <Field label="Document type">
            <select className={inputCls} value={docType} onChange={(e) => setDocType(e.target.value)}>
              {DOC_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
            </select>
          </Field>
          <Field label="File (JPG, PNG or PDF, up to 5 MB)">
            <input key={inputKey} type="file" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
              className="block w-full text-xs text-gray-600"
              onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </Field>
          <button type="button" className="btn-primary h-8 text-xs" disabled={!file || up.isPending} onClick={() => up.mutate()}>
            {up.isPending ? "Uploading…" : "Upload"}
          </button>
        </div>
      </div>
      {docs.isLoading ? <div className="card flex justify-center py-10"><Spinner /></div>
        : docs.isError ? <div className="card"><Empty>{err(docs.error, "Your documents are not available.")}</Empty></div>
        : <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName="my-documents" minWidth={760}
            groupBy={{ key: (r) => r.group, label: "Group" }} initialSort={{ key: "uploaded_at", dir: "desc" }}
            empty="No documents yet. HR's uploads and yours will appear here." />}
    </div>
  );
}

/* ─────────────────────────── Letters ─────────────────────────── */
function Letters() {
  const q = useQuery({ queryKey: ["my-letters"], queryFn: () => lettersApi.mine(), retry: false });
  if (q.isLoading) return <div className="card flex justify-center py-10"><Spinner /></div>;
  if (q.isError) return <div className="card"><Empty>{err(q.error, "Your letters are not available.")}</Empty></div>;
  return <LetterList letters={q.data || []} />;
}

/* ─────────────────────────── Payslips & tax (links only) ─────────────────────────── */
function PayLinks() {
  const items = [
    { to: "/my-pay", icon: Receipt, title: "Pay stubs", text: "Every pay stub the company has released to you, with PDF download." },
    { to: "/my-pay?tab=tax", icon: FileText, title: "Tax slips", text: "Your T4 (and RL-1 in Québec) once the year-end slips are issued." },
  ];
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {items.map((i) => (
        <Link key={i.to} to={i.to} className="card hover:border-primary-300 transition-colors flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl bg-primary-50 flex items-center justify-center flex-shrink-0"><i.icon className="w-5 h-5 text-primary-600" /></div>
          <div className="min-w-0">
            <div className="font-semibold text-gray-800 flex items-center gap-1">{i.title}<ExternalLink className="w-3.5 h-3.5 text-gray-400" /></div>
            <p className="text-sm text-gray-500">{i.text}</p>
          </div>
        </Link>
      ))}
      <p className="md:col-span-2 text-[11px] text-gray-500">Amounts are shown only on those pages, so nothing about your pay is visible here.</p>
    </div>
  );
}

/* ─────────────────────────── Policies (employee) ─────────────────────────── */
function Policies() {
  const qc = useQueryClient();
  const [reading, setReading] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "pending">("all");
  const q = useQuery({ queryKey: ["policies"], queryFn: essDocsApi.policies, retry: false });
  const list = (q.data || []).filter((p) => filter === "all" || p.my_status === "pending");
  const pendingCount = (q.data || []).filter((p) => p.my_status === "pending").length;
  if (q.isLoading) return <div className="card flex justify-center py-10"><Spinner /></div>;
  if (q.isError) return <div className="card"><Empty>{err(q.error, "Policies are not available.")}</Empty></div>;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        {(["all", "pending"] as const).map((f) => (
          <button key={f} type="button" onClick={() => setFilter(f)}
            className={`px-3 h-7 rounded-full text-xs border ${filter === f ? "bg-primary-50 border-primary-300 text-primary-700" : "border-surface-border text-gray-600"}`}>
            {f === "all" ? `All (${q.data?.length || 0})` : `To accept (${pendingCount})`}
          </button>
        ))}
      </div>
      {!list.length ? <div className="card"><Empty>{filter === "pending" ? "Nothing to accept — you are up to date." : "No company policies have been published yet."}</Empty></div> : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {list.map((p) => (
            <div key={p.id} className="card flex flex-col gap-2">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-semibold text-gray-800 truncate" title={p.title}>{p.title}</div>
                  <div className="text-[11px] text-gray-500">{p.category || "General"} · {p.version_label} · effective {fmtDate(p.effective_date)}</div>
                </div>
                <StatusBadge s={p.my_status} />
              </div>
              {p.my_status === "acknowledged" && <div className="text-[11px] text-green-700 flex items-center gap-1"><CheckCircle2 className="w-3.5 h-3.5" />Accepted {fmtWhen(p.my_acknowledged_at)}</div>}
              {p.previous_versions.length > 0 && <div className="text-[11px] text-gray-400">Earlier: {p.previous_versions.map((v) => v.version_label).join(", ")}</div>}
              <div className="mt-auto flex gap-2 pt-1">
                <button type="button" className="btn-secondary h-8 text-xs flex items-center gap-1" onClick={() => setReading(p.id)}><BookOpen className="w-3.5 h-3.5" />Read</button>
                {p.my_status === "pending" && <button type="button" className="btn-primary h-8 text-xs flex items-center gap-1" onClick={() => setReading(p.id)}><ShieldCheck className="w-3.5 h-3.5" />Acknowledge</button>}
              </div>
            </div>
          ))}
        </div>
      )}
      {reading && <PolicyReader id={reading} onClose={() => setReading(null)} onDone={() => {
        qc.invalidateQueries({ queryKey: ["policies"] }); qc.invalidateQueries({ queryKey: ["doc-centre-summary"] });
      }} />}
    </div>
  );
}

function PolicyReader({ id, onClose, onDone }: { id: string; onClose: () => void; onDone: () => void }) {
  const q = useQuery({ queryKey: ["policy", id], queryFn: () => essDocsApi.policy(id), retry: false });
  const p = q.data;
  const ack = useMutation({
    mutationFn: () => essDocsApi.acknowledge(id, p!.version_no),
    onSuccess: () => { toast.success("Thank you — your acceptance is recorded"); onDone(); onClose(); },
    onError: (e) => { toast.error(err(e, "Could not record your acceptance")); q.refetch(); },
  });
  const openFile = async () => {
    try { openPdf(await essDocsApi.file(id), p?.file_name || "policy.pdf"); } catch (e) { toast.error(err(e, "Could not open the PDF")); }
  };
  return (
    <Modal title={p?.title || "Policy"} width="max-w-4xl" onClose={onClose}
      sub={p ? `${p.category || "General"} · ${p.version_label} · effective ${fmtDate(p.effective_date)}` : undefined}
      footer={p && <>
        {p.has_file && <button type="button" className="btn-secondary h-8 text-xs flex items-center gap-1" onClick={openFile}><Download className="w-3.5 h-3.5" />Open PDF</button>}
        {p.my_status === "pending" ? <button type="button" className="btn-primary h-8 text-xs flex items-center gap-1" disabled={ack.isPending} onClick={() => ack.mutate()}>
          <ShieldCheck className="w-3.5 h-3.5" />{ack.isPending ? "Saving…" : "I have read and accept"}</button>
          : <button type="button" className="btn-secondary h-8 text-xs" onClick={onClose}>Close</button>}
      </>}>
      {q.isLoading ? <div className="flex justify-center py-10"><Spinner /></div> : q.isError ? <Empty>{err(q.error, "This policy is not available.")}</Empty> : p && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap"><StatusBadge s={p.my_status} />
            {p.my_status === "acknowledged" && <span className="text-xs text-gray-500">You accepted this version on {fmtWhen(p.my_acknowledged_at)}.</span>}</div>
          {p.body ? <div className="whitespace-pre-wrap text-sm text-gray-700 leading-relaxed border border-surface-border rounded-lg p-4 bg-surface">{p.body}</div>
            : <p className="text-sm text-gray-500">This policy is in the attached PDF — open it before accepting.</p>}
          {p.has_file && p.body && <p className="text-xs text-gray-500">The full document is also attached as a PDF ({p.file_name}).</p>}
        </div>
      )}
    </Modal>
  );
}

/* ─────────────────────────── Publish & track (HR) ─────────────────────────── */
function Progress({ pct }: { pct: number }) {
  return <div className="h-2 rounded-full bg-gray-100 overflow-hidden w-full"><div className="h-full bg-primary-500" style={{ width: `${pct}%` }} /></div>;
}

function PublishTrack() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<{ mode: "new" } | { mode: "version"; policy: Policy } | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["policies-manage"], queryFn: essDocsApi.manage, retry: false });
  const refresh = () => ["policies-manage", "policies", "doc-centre-summary", "policy-register"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const act = useMutation({
    mutationFn: ({ id, a }: { id: string; a: "archive" | "restore" }) => (a === "archive" ? essDocsApi.archive(id) : essDocsApi.restore(id)),
    onSuccess: (_d, v) => { toast.success(v.a === "archive" ? "Policy archived" : "Policy published again"); refresh(); },
    onError: (e) => toast.error(err(e, "Could not change the policy")),
  });
  const rows = q.data || [];
  const selected = rows.find((p) => p.id === sel) || null;
  const cols: GridColumn<Policy>[] = [
    { key: "title", label: "Policy", render: (r) => <button type="button" className="text-primary-700 hover:underline text-left font-medium" onClick={() => setSel(r.id)}>{r.title}</button>, text: (r) => r.title },
    { key: "category", label: "Category", text: (r) => r.category || "" },
    { key: "version_label", label: "Version" },
    { key: "effective_date", label: "Effective", type: "date" },
    { key: "status", label: "Status", render: (r) => <span className={r.status === "published" ? "badge-green" : "badge-purple"}>{r.status === "published" ? "Published" : "Archived"}</span>, text: (r) => r.status },
    { key: "progress", label: "Accepted", sortable: true, width: "200px",
      render: (r) => r.acks ? <div className="flex items-center gap-2 min-w-[160px]"><Progress pct={r.acks.percent} /><span className="text-[11px] tabular-nums whitespace-nowrap">{r.acks.acknowledged}/{r.acks.total}</span></div> : <span className="text-[11px] text-gray-400">No acceptance needed</span>,
      text: (r) => (r.acks ? String(r.acks.percent).padStart(3, "0") : "") },
    { key: "pending", label: "Pending", type: "number", render: (r) => r.acks ? r.acks.pending : "—", text: (r) => String(r.acks?.pending ?? "") },
    { key: "actions", label: "", sortable: false, text: () => "", render: (r) => (
      <div className="flex gap-1 justify-end">
        <button type="button" className="btn-secondary h-7 px-2 text-[11px]" onClick={() => setEditing({ mode: "version", policy: r })}>New version</button>
        {r.status === "published"
          ? <button type="button" className="btn-secondary h-7 px-2 text-[11px] flex items-center gap-1" onClick={() => act.mutate({ id: r.id, a: "archive" })}><Archive className="w-3 h-3" />Archive</button>
          : <button type="button" className="btn-secondary h-7 px-2 text-[11px] flex items-center gap-1" onClick={() => act.mutate({ id: r.id, a: "restore" })}><RotateCcw className="w-3 h-3" />Restore</button>}
      </div>) },
  ];
  return (
    <div className="space-y-4">
      {q.isLoading ? <div className="card flex justify-center py-10"><Spinner /></div>
        : q.isError ? <div className="card"><Empty>{err(q.error, "Policies are not available.")}</Empty></div>
        : <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName="company-policies" minWidth={960}
            onRowClick={(r) => setSel(r.id)} empty="No policies yet — publish the first one."
            toolbar={<button type="button" className="btn-primary h-8 text-xs flex items-center gap-1" onClick={() => setEditing({ mode: "new" })}><Plus className="w-3.5 h-3.5" />Publish policy</button>} />}
      {selected && <Register policy={selected} />}
      {editing && <PolicyEditor state={editing} onClose={() => setEditing(null)} onSaved={(p) => { refresh(); setSel(p.id); setEditing(null); }} />}
    </div>
  );
}

function Register({ policy }: { policy: Policy }) {
  const qc = useQueryClient();
  const [show, setShow] = useState<"pending" | "acknowledged" | "all">("pending");
  const q = useQuery({ queryKey: ["policy-register", policy.id, policy.version_no], queryFn: () => essDocsApi.register(policy.id), retry: false });
  const remind = useMutation({
    mutationFn: () => essDocsApi.remind(policy.id),
    onSuccess: (r) => { toast.success(r.reminded ? `Reminder sent to ${r.reminded} people` : "Nobody is pending"); qc.invalidateQueries({ queryKey: ["policies-manage"] }); },
    onError: (e) => toast.error(err(e, "Could not send the reminder")),
  });
  const exportCsv = async () => {
    try { saveBlob(await essDocsApi.registerCsv(policy.id, show === "all" ? undefined : show), `${policy.title}-${policy.version_label}-${show}.csv`); }
    catch (e) { toast.error(err(e, "Could not export")); }
  };
  const r = q.data;
  const rows = (r?.rows || []).filter((x) => show === "all" || x.status === show);
  const cols: GridColumn<AckRow>[] = [
    { key: "employee_number", label: "Emp no.", text: (x) => x.employee_number || "" },
    { key: "name", label: "Name" },
    { key: "department", label: "Department", text: (x) => x.department || "" },
    { key: "job_title", label: "Job title", text: (x) => x.job_title || "" },
    { key: "status", label: "Status", render: (x) => <StatusBadge s={x.status} />, text: (x) => x.status },
    { key: "acknowledged_at", label: "Accepted at", render: (x) => fmtWhen(x.acknowledged_at), text: (x) => x.acknowledged_at || "" },
    { key: "user_agent", label: "Browser", render: (x) => <span className="block max-w-[260px] truncate" title={x.user_agent || ""}>{x.user_agent || "—"}</span>, text: (x) => x.user_agent || "" },
  ];
  if (!policy.ack_required) return <div className="card text-sm text-gray-500">{policy.title} is for information only — nobody needs to accept it.</div>;
  return (
    <div className="card space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-semibold text-gray-800">{policy.title} · {policy.version_label}</div>
          <div className="text-[11px] text-gray-500">Published {fmtWhen(policy.published_at)}{policy.last_reminded_at ? ` · last reminder ${fmtWhen(policy.last_reminded_at)}` : ""}</div>
        </div>
        <div className="flex gap-2 flex-wrap">
          <button type="button" className="btn-secondary h-8 text-xs flex items-center gap-1" onClick={exportCsv}><Download className="w-3.5 h-3.5" />Export CSV</button>
          <button type="button" className="btn-primary h-8 text-xs flex items-center gap-1" disabled={remind.isPending || !r?.pending || policy.status !== "published"} onClick={() => remind.mutate()}>
            <Send className="w-3.5 h-3.5" />{remind.isPending ? "Sending…" : `Remind pending (${r?.pending ?? 0})`}</button>
        </div>
      </div>
      {r && <div className="flex items-center gap-3"><Progress pct={r.percent} /><span className="text-xs tabular-nums whitespace-nowrap text-gray-600">{r.acknowledged} of {r.total} accepted ({r.percent}%)</span></div>}
      {q.isLoading ? <div className="flex justify-center py-6"><Spinner /></div> : q.isError ? <Empty>{err(q.error, "The register is not available.")}</Empty> :
        <DataGrid columns={cols} rows={rows} rowKey={(x) => x.employee_id} exportName={`${policy.title}-${show}`} minWidth={900}
          filters={<div className="flex gap-1">{(["pending", "acknowledged", "all"] as const).map((f) => (
            <button key={f} type="button" onClick={() => setShow(f)}
              className={`px-2.5 h-7 rounded-full text-[11px] border ${show === f ? "bg-primary-50 border-primary-300 text-primary-700" : "border-surface-border text-gray-600"}`}>
              {f === "pending" ? `Pending (${r?.pending ?? 0})` : f === "acknowledged" ? `Accepted (${r?.acknowledged ?? 0})` : "Everyone"}</button>))}</div>}
          empty={show === "pending" ? "Everyone has accepted this version." : "Nobody here yet."} />}
    </div>
  );
}

function PolicyEditor({ state, onClose, onSaved }: {
  state: { mode: "new" } | { mode: "version"; policy: Policy }; onClose: () => void; onSaved: (p: Policy) => void;
}) {
  const cur = state.mode === "version" ? state.policy : null;
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("");
  const [label, setLabel] = useState(cur ? `v${cur.version_no + 1}` : "v1");
  const [effective, setEffective] = useState(todayISO());
  const [body, setBody] = useState("");
  const [ackRequired, setAckRequired] = useState(cur ? cur.ack_required : true);
  const [file, setFile] = useState<File | null>(null);
  const [keepFile, setKeepFile] = useState(!!cur?.has_file);
  const [busy, setBusy] = useState(false);
  const prefill = useQuery({ queryKey: ["policy", cur?.id], queryFn: () => essDocsApi.policy(cur!.id), enabled: !!cur, retry: false });
  const [prefilled, setPrefilled] = useState(false);
  if (cur && prefill.data && !prefilled) { setBody(prefill.data.body || ""); setPrefilled(true); }

  const save = async () => {
    setBusy(true);
    try {
      const document_id = file ? (await essDocsApi.uploadFile(file)).id : null;
      const p = cur
        ? await essDocsApi.newVersion(cur.id, { version_label: label, effective_date: effective || null, body: body || null,
            document_id, ack_required: ackRequired, keep_file: !file && keepFile })
        : await essDocsApi.publish({ title, category: category || null, version_label: label, effective_date: effective || null,
            body: body || null, document_id, ack_required: ackRequired });
      toast.success(cur ? `${p.title} ${p.version_label} published — everyone accepts again` : `${p.title} published`);
      onSaved(p);
    } catch (e) { toast.error(err(e, "Could not publish the policy")); }
    finally { setBusy(false); }
  };
  return (
    <Modal width="max-w-4xl" onClose={onClose}
      title={cur ? `New version — ${cur.title}` : "Publish a company policy"}
      sub={cur ? `Current: ${cur.version_label}. A new version asks everyone to read and accept again.` : "Employees see it on their Document centre as soon as you publish."}
      footer={<><button type="button" className="btn-secondary h-8 text-xs" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary h-8 text-xs" disabled={busy || (!cur && !title.trim()) || !label.trim() || (!body.trim() && !file && !(cur && keepFile))} onClick={save}>
          {busy ? "Publishing…" : cur ? "Publish new version" : "Publish"}</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
        {!cur && <Field label="Title" className="md:col-span-2"><input className={inputCls} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="Code of Conduct" /></Field>}
        {!cur && <Field label="Category" className="md:col-span-2"><input className={inputCls} value={category} maxLength={60} onChange={(e) => setCategory(e.target.value)} placeholder="Conduct, HR, Finance…" list="policy-cats" />
          <datalist id="policy-cats">{["Conduct", "HR", "Finance", "IT & security", "Health & safety", "Workplace"].map((c) => <option key={c} value={c} />)}</datalist></Field>}
        <Field label="Version label"><input className={inputCls} value={label} maxLength={40} onChange={(e) => setLabel(e.target.value)} /></Field>
        <Field label="Effective from"><input type="date" className={inputCls} value={effective} onChange={(e) => setEffective(e.target.value)} /></Field>
        <Field label="Acceptance" className="md:col-span-2">
          <label className="flex items-center gap-2 h-8 text-xs text-gray-700"><input type="checkbox" checked={ackRequired} onChange={(e) => setAckRequired(e.target.checked)} />Everyone must read and accept this policy</label>
        </Field>
        <Field label="Policy text" className="md:col-span-4">
          <textarea className="input-base w-full text-sm min-h-[260px] leading-relaxed" value={body} onChange={(e) => setBody(e.target.value)}
            placeholder="Paste the policy text here, attach a PDF below, or both." />
        </Field>
        <Field label="PDF (optional, up to 5 MB)" className="md:col-span-2">
          <input type="file" accept="application/pdf,.pdf" className="block w-full text-xs text-gray-600" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </Field>
        {cur?.has_file && !file && <Field label="Current PDF" className="md:col-span-2">
          <label className="flex items-center gap-2 h-8 text-xs text-gray-700"><input type="checkbox" checked={keepFile} onChange={(e) => setKeepFile(e.target.checked)} />Keep {cur.file_name} for this version</label>
        </Field>}
      </div>
    </Modal>
  );
}
