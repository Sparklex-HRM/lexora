import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { ShieldCheck, FileText, Users, AlertTriangle, Archive, Lock, Contact, ScrollText, Download, Play, CheckCircle2, XCircle, RefreshCw } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "../components/ui";
import { privacyApi, sensitiveApi, securityApi, saveBlob, type BreachRow, type RetentionRow } from "../api/privacy";
import { useAuthStore } from "../store/authStore";
import { useUrlTab } from "../search/useUrlTab";
import { formatDateTime } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const ADMIN = ["superadmin", "org_admin", "hr_admin", "hr_manager"];
const TABS = [
  { key: "dashboard", label: "Compliance dashboard", icon: ShieldCheck, admin: true },
  { key: "consents", label: "My consents", icon: CheckCircle2, admin: false },
  { key: "coverage", label: "Consent coverage", icon: Users, admin: true },
  { key: "notice", label: "Privacy notice", icon: FileText, admin: false },
  { key: "contacts", label: "Privacy Officer", icon: Contact, admin: false },
  { key: "breaches", label: "Breach register", icon: AlertTriangle, admin: true },
  { key: "retention", label: "Retention & purge", icon: Archive, admin: true },
  { key: "contract", label: "Service-provider terms", icon: ScrollText, admin: true },
  { key: "security", label: "Security & export", icon: Lock, admin: true },
];
const inputCls = "w-full border border-surface-border rounded-lg px-3 py-1.5 text-sm bg-white";
const th = "px-3 py-2 text-left text-[11px] uppercase tracking-wide text-gray-400 font-semibold";
const td = "px-3 py-1.5 text-sm text-gray-700 border-t border-surface-border/70";
const fmt = (s?: string | null) => formatDateTime(s);   // instant → viewer's zone (was the UTC clock)

export default function PrivacyCenterPage() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const isAdmin = ADMIN.includes(role);
  const [tab, setTab] = useUrlTab<string>(isAdmin ? "dashboard" : "consents");   // ?tab= (global search deep link)
  const tabs = TABS.filter((t) => isAdmin || !t.admin);
  return (
    <div>
      <PageHeader title="Privacy Centre" icon={ShieldCheck}
        subtitle="Digital Personal Data Protection Act 2023 · Rules 2025 — consent, notice, rights, breaches, retention, encryption" />
      <div className="flex flex-wrap gap-1.5 mb-4">
        {tabs.map((t) => {
          const I = t.icon;
          return <button key={t.key} onClick={() => setTab(t.key)}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition ${tab === t.key ? "border-primary-500 bg-primary-50 text-primary-700" : "border-surface-border bg-white text-gray-600 hover:bg-gray-50"}`}>
            <I className="w-3.5 h-3.5" /> {t.label}</button>;
        })}
      </div>
      {tab === "dashboard" && <DashboardTab />}
      {tab === "consents" && <MyConsentsTab />}
      {tab === "coverage" && <CoverageTab />}
      {tab === "notice" && <NoticeTab isAdmin={isAdmin} />}
      {tab === "contacts" && <ContactsTab isAdmin={isAdmin} />}
      {tab === "breaches" && <BreachTab />}
      {tab === "retention" && <RetentionTab />}
      {tab === "contract" && <ContractTab role={role} />}
      {tab === "security" && <SecurityTab role={role} />}
    </div>
  );
}

// ── Dashboard ──────────────────────────────────────────────────────────────
function DashboardTab() {
  const { data, isLoading } = useQuery({ queryKey: ["privacy", "dashboard"], queryFn: privacyApi.dashboard });
  if (isLoading || !data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const enc = data.encryption || {};
  return (
    <>
      <div className="grid grid-cols-2 lg:grid-cols-6 gap-3 mb-4">
        <StatTile label="PIPEDA readiness" value={`${data.readiness_pct}%`} icon={ShieldCheck} accent sub={`${data.checklist.filter((c: any) => c.done).length} of ${data.checklist.length} items done`} />
        <StatTile label="Consent coverage" value={`${data.consent.coverage_pct}%`} sub={`${data.consent.fully_covered} of ${data.consent.employees} employees`} />
        <StatTile label="Open requests" value={data.requests.open} sub={data.requests.overdue ? `${data.requests.overdue} past the 30-day limit` : "none overdue"} />
        <StatTile label="Breach register" value={data.breaches.total} sub={`${data.breaches.open} open`} />
        <StatTile label="Encryption" value={enc.configured ? "ON" : enc.dev_fallback ? "DEV KEY" : "—"} sub={enc.cutover_done ? "cutover done" : `${enc.org_rows_pending_backfill ?? "?"} rows pending`} />
        <StatTile label="Notice version" value={`v${data.notice_version}`} sub={data.last_retention_run ? `retention run ${fmt(data.last_retention_run)}` : "no retention run yet"} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card !p-0">
          <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800">PIPEDA readiness checklist</div>
          <table className="w-full"><tbody>
            {data.checklist.map((c: any) => (
              <tr key={c.key}><td className={`${td} w-8`}>{c.done ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <XCircle className="w-4 h-4 text-rose-500" />}</td><td className={td}>{c.label}</td></tr>
            ))}
          </tbody></table>
        </div>
        <div className="card">
          <div className="font-display font-semibold text-gray-800 mb-2">Encryption status</div>
          <table className="w-full text-sm"><tbody>
            {[["Algorithm", enc.algorithm], ["Key id", enc.key_id], ["Production key set", enc.configured ? "yes" : "NO — LEXORA_DATA_KEY missing (dev fallback)"],
              ["Employees in org", enc.org_rows], ["Rows with encrypted identifiers", enc.org_rows_encrypted], ["Rows still holding plaintext", enc.org_rows_with_plaintext],
              ["Backfilled at", fmt(enc.backfilled_at)], ["Cutover done", enc.cutover_done ? fmt(enc.cutover_done_at) : "not yet (plaintext kept for rollback)"],
              ["Protected fields", (enc.fields || []).join(", ")]].map(([k, v]) => (
              <tr key={String(k)}><td className="py-1 pr-3 text-gray-500 w-56">{k}</td><td className="py-1 text-gray-800">{String(v ?? "—")}</td></tr>))}
          </tbody></table>
          <p className="text-[11px] text-gray-400 mt-2">Every read of a full SIN or bank value writes a <code>employee.sensitive_read</code> row in the <Link className="text-primary-600" to="/audit-log">audit log</Link>.</p>
        </div>
      </div>
    </>
  );
}

// ── My consents ────────────────────────────────────────────────────────────
function MyConsentsTab() {
  const qc = useQueryClient();
  const empId = useAuthStore((s) => s.user?.employee_id) || undefined;
  const { data, isLoading, error } = useQuery({ queryKey: ["privacy", "consents", empId], queryFn: () => privacyApi.consents(undefined) });
  const [name, setName] = useState("");
  const give = useMutation({ mutationFn: (p: string[]) => privacyApi.giveConsent({ purposes: p, signed_name: name }),
    onSuccess: () => { toast.success("Consent recorded"); qc.invalidateQueries({ queryKey: ["privacy"] }); }, onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not record consent") });
  const withdraw = useMutation({ mutationFn: (p: string[]) => privacyApi.withdrawConsent({ purposes: p, reason: "withdrawn by employee" }),
    onSuccess: (r: any) => { toast.success("Consent withdrawn"); if (r.warning) toast(r.warning, { icon: "⚠️", duration: 8000 }); qc.invalidateQueries({ queryKey: ["privacy"] }); }, onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not withdraw") });
  if (isLoading) return <div className="grid place-items-center py-16"><Spinner /></div>;
  if (error || !data) return <div className="card"><Empty>Your login is not linked to an employee record, so there are no consents to show.</Empty></div>;
  const pending = data.purposes.filter((p) => p.status !== "given" || p.stale).map((p) => p.purpose);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 card !p-0">
        <div className="px-4 py-2.5 border-b border-surface-border flex items-center justify-between">
          <span className="font-display font-semibold text-gray-800">What you have agreed to</span>
          <span className="text-[11px] text-gray-400">privacy notice v{data.current_notice_version}</span>
        </div>
        <table className="w-full">
          <thead><tr><th className={th}>Purpose</th><th className={th}>What it means</th><th className={th}>Status</th><th className={th}>Since</th><th className={th}></th></tr></thead>
          <tbody>{data.purposes.map((p) => (
            <tr key={p.purpose}>
              <td className={`${td} font-medium text-gray-800 whitespace-nowrap`}>{p.label}</td>
              <td className={`${td} text-xs text-gray-500`}>{p.text}</td>
              <td className={td}><span className={`badge ${p.status === "given" && !p.stale ? "badge-green" : p.status === "withdrawn" ? "badge-red" : "badge-amber"}`}>{p.stale ? "re-consent needed" : p.status.replace("_", " ")}</span></td>
              <td className={`${td} text-xs whitespace-nowrap`}>{fmt(p.given_at)}</td>
              <td className={`${td} text-right whitespace-nowrap`}>
                {p.status === "given" && !p.stale
                  ? <button className="text-xs text-rose-600 hover:underline" onClick={() => withdraw.mutate([p.purpose])}>Withdraw</button>
                  : <button className="text-xs text-primary-700 hover:underline" onClick={() => give.mutate([p.purpose])}>Agree</button>}
              </td>
            </tr>))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <div className="font-display font-semibold text-gray-800 mb-1">Agree to all pending</div>
        <p className="text-[11px] text-gray-400 mb-2">Type your name as a signature. Read the <b>Privacy notice</b> tab first.</p>
        <input className={inputCls} placeholder="Your full name" value={name} onChange={(e) => setName(e.target.value)} />
        <button disabled={!pending.length || !name.trim() || give.isPending} onClick={() => give.mutate(pending)}
          className="w-full mt-2 rounded-lg bg-primary-600 hover:bg-primary-700 text-white text-sm font-semibold py-2 disabled:opacity-50">
          {pending.length ? `Agree to ${pending.length} purpose(s)` : "Nothing pending"}
        </button>
        <div className="mt-4 text-[12px] text-gray-500 leading-relaxed">
          Need a copy of your information, a correction, a deletion or to authorise a representative? Go to <Link to="/data-requests" className="text-primary-600 font-medium">Privacy requests</Link>. We answer within 30 days.
        </div>
        {!!data.history.length && <div className="mt-4">
          <div className="text-[11px] uppercase tracking-wide text-gray-400 font-semibold mb-1">History</div>
          <div className="max-h-48 overflow-auto text-[11px] text-gray-500 space-y-0.5">
            {data.history.map((h: any) => <div key={h.id}>{fmt(h.given_at)} · {h.purpose_label} · {h.status}{h.withdrawn_at ? ` (${fmt(h.withdrawn_at)})` : ""} · v{h.notice_version}</div>)}
          </div>
        </div>}
      </div>
    </div>
  );
}

// ── Coverage grid ──────────────────────────────────────────────────────────
function CoverageTab() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["privacy", "coverage"], queryFn: privacyApi.consentSummary });
  const [q, setQ] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const record = useMutation({ mutationFn: (eid: string) => privacyApi.giveConsent({ employee_id: eid, purposes: data.purposes, channel: "hr_recorded", signed_name: "recorded by HR (paper form)" }),
    onSuccess: () => { toast.success("Recorded"); qc.invalidateQueries({ queryKey: ["privacy"] }); } });
  if (isLoading || !data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const rows = (data.items as any[]).filter((r) => (!onlyMissing || !r.complete) && (!q || `${r.name} ${r.employee_number} ${r.department}`.toLowerCase().includes(q.toLowerCase())));
  const csv = () => {
    const head = ["employee_number", "name", "department", ...data.purposes, "complete"];
    const body = rows.map((r) => head.map((h) => JSON.stringify(r[h] ?? "")).join(","));
    saveBlob(new Blob([[head.join(","), ...body].join("\n")], { type: "text/csv" }), "consent-coverage.csv");
  };
  return (
    <div className="card !p-0">
      <div className="px-4 py-2.5 border-b border-surface-border flex flex-wrap items-center gap-2">
        <span className="font-display font-semibold text-gray-800">Consent coverage</span>
        <span className="badge badge-teal">{data.coverage.coverage_pct}% complete · {data.coverage.fully_covered}/{data.coverage.employees}</span>
        <input className="border border-surface-border rounded-lg px-2 py-1 text-xs ml-auto" placeholder="Search name / number / dept" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="text-xs text-gray-500 flex items-center gap-1"><input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} /> only missing</label>
        <button onClick={csv} className="text-xs inline-flex items-center gap-1 border border-surface-border rounded-lg px-2 py-1 bg-white hover:bg-gray-50"><Download className="w-3 h-3" /> CSV</button>
      </div>
      <div className="overflow-x-auto"><table className="w-full">
        <thead><tr><th className={th}>#</th><th className={th}>Employee</th><th className={th}>Dept</th>{data.purposes.map((p: string) => <th key={p} className={th}>{p.replace("_", " ")}</th>)}<th className={th}>Action</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.employee_id} className={r.complete ? "" : "bg-amber-50/40"}>
            <td className={`${td} text-xs text-gray-400`}>{r.employee_number}</td>
            <td className={`${td} font-medium text-gray-800`}>{r.name}</td>
            <td className={`${td} text-xs`}>{r.department || "—"}</td>
            {data.purposes.map((p: string) => <td key={p} className={td}><span className={`badge ${r[p] === "given" ? "badge-green" : r[p] === "withdrawn" ? "badge-red" : "badge-amber"}`}>{r[p].replace("_", " ")}</span></td>)}
            <td className={td}>{!r.complete && <button className="text-xs text-primary-700 hover:underline" onClick={() => record.mutate(r.employee_id)}>Record paper consent</button>}</td>
          </tr>))}
          {!rows.length && <tr><td colSpan={9}><Empty>No employees match.</Empty></td></tr>}
        </tbody>
      </table></div>
    </div>
  );
}

// ── Notice ─────────────────────────────────────────────────────────────────
function NoticeTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["privacy", "notice"], queryFn: privacyApi.notice });
  const { data: versions } = useQuery({ queryKey: ["privacy", "notice-versions"], queryFn: privacyApi.noticeVersions, enabled: isAdmin });
  const [edit, setEdit] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const publish = useMutation({ mutationFn: () => privacyApi.publishNotice({ title, body }),
    onSuccess: (n: any) => { toast.success(`Published v${n.version} — employees will be asked to re-consent`); setEdit(false); qc.invalidateQueries({ queryKey: ["privacy"] }); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not publish") });
  if (isLoading || !data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
      <div className="lg:col-span-3 card">
        <div className="flex items-center gap-2 mb-2">
          <h3 className="font-display font-semibold text-gray-800">{data.title}</h3>
          <span className={`badge ${data.is_default ? "badge-amber" : "badge-green"}`}>{data.is_default ? "default template — not yet published" : `v${data.version} · ${fmt(data.published_at)}`}</span>
          {isAdmin && !edit && <button className="ml-auto text-xs text-primary-700 hover:underline" onClick={() => { setTitle(data.title); setBody(data.body); setEdit(true); }}>Edit & publish new version</button>}
        </div>
        {edit ? <>
          <input className={`${inputCls} mb-2`} value={title} onChange={(e) => setTitle(e.target.value)} />
          <textarea className={`${inputCls} font-mono text-xs`} rows={26} value={body} onChange={(e) => setBody(e.target.value)} />
          <div className="flex gap-2 mt-2">
            <button onClick={() => publish.mutate()} disabled={publish.isPending} className="rounded-lg bg-primary-600 text-white text-sm font-semibold px-4 py-1.5 disabled:opacity-50">Publish</button>
            <button onClick={() => setEdit(false)} className="rounded-lg border border-surface-border text-sm px-4 py-1.5">Cancel</button>
          </div>
        </> : <pre className="whitespace-pre-wrap text-[13px] leading-relaxed text-gray-700 font-sans">{data.body}</pre>}
      </div>
      <div className="card">
        <div className="font-display font-semibold text-gray-800 mb-1">Versions</div>
        <p className="text-[11px] text-gray-400 mb-2">Publishing a new version marks every consent given against an older version as "re-consent needed".</p>
        {(versions?.items || []).map((v: any) => <div key={v.id} className="text-xs text-gray-600 py-1 border-t border-surface-border/60">v{v.version} · {fmt(v.published_at)} · {v.published_by}</div>)}
        {!versions?.items?.length && <div className="text-xs text-gray-400">No published versions yet.</div>}
      </div>
    </div>
  );
}

// ── Contacts ───────────────────────────────────────────────────────────────
function ContactsTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["privacy", "settings"], queryFn: privacyApi.settings });
  const [form, setForm] = useState<any>(null);
  const f = form || data || {};
  const save = useMutation({ mutationFn: () => privacyApi.saveSettings(f), onSuccess: () => { toast.success("Contacts published"); setForm(null); qc.invalidateQueries({ queryKey: ["privacy"] }); } });
  if (isLoading) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const F = ({ k, label }: { k: string; label: string }) => (
    <div><label className="text-[11px] text-gray-500">{label}</label>
      <input className={inputCls} value={f[k] || ""} readOnly={!isAdmin} onChange={(e) => setForm({ ...f, [k]: e.target.value })} /></div>);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div className="card">
        <h3 className="font-display font-semibold text-gray-800 mb-1">Privacy Officer (the person accountable for personal information)</h3>
        <p className="text-[11px] text-gray-400 mb-3">PIPEDA Principle 4.1: name the person accountable for privacy and publish how to reach them. Shown here and in the login-page footer.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <F k="dpo_name" label="Privacy Officer name" /><F k="dpo_email" label="Privacy Officer email" /><F k="dpo_phone" label="Privacy Officer phone" />
          <F k="grievance_officer_name" label="Complaints contact name" /><F k="grievance_officer_email" label="Complaints contact email" />
        </div>
        {isAdmin && <button onClick={() => save.mutate()} disabled={!form || save.isPending} className="mt-3 rounded-lg bg-primary-600 text-white text-sm font-semibold px-4 py-1.5 disabled:opacity-50">Save & publish</button>}
      </div>
      <div className="card text-sm text-gray-600 leading-relaxed">
        <h3 className="font-display font-semibold text-gray-800 mb-1">Escalation path (plain words)</h3>
        <ol className="list-decimal ml-5 space-y-1">
          <li>Raise a request in <Link to="/data-requests" className="text-primary-600">Privacy requests</Link> — answered within <b>30 days</b> (PIPEDA s.8(3)).</li>
          <li>Not happy? Write to the Privacy Officer above.</li>
          <li>Still not resolved? Complain to the <b>Office of the Privacy Commissioner of Canada</b> (Québec employees: the <b>Commission d'accès à l'information</b>).</li>
        </ol>
        <div className="mt-3 text-[11px] text-gray-400">Lexora HRM (the software provider) processes personal information on your organization's behalf; your organization stays accountable for it (Principle 4.1.3).</div>
      </div>
    </div>
  );
}

// ── Breaches ───────────────────────────────────────────────────────────────
function BreachTab() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["privacy", "breaches"], queryFn: privacyApi.breaches });
  const [open, setOpen] = useState<string | null>(null);
  const [form, setForm] = useState({ description: "", individuals_affected: 0, severity: "medium", data_categories: "" });
  const create = useMutation({ mutationFn: () => privacyApi.createBreach({ ...form, data_categories: form.data_categories.split(",").map((s) => s.trim()).filter(Boolean) }),
    onSuccess: () => { toast.success("Breach recorded — work the report / notify checklist"); setForm({ description: "", individuals_affected: 0, severity: "medium", data_categories: "" }); qc.invalidateQueries({ queryKey: ["privacy"] }); } });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: string; body: any }) => privacyApi.patchBreach(id, body), onSuccess: () => qc.invalidateQueries({ queryKey: ["privacy"] }) });
  if (isLoading || !data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
      <div className="lg:col-span-3 card !p-0">
        <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800">Breach register <span className="badge badge-amber ml-2">{data.open} open</span></div>
        <div className="overflow-x-auto"><table className="w-full">
          <thead><tr><th className={th}>Ref</th><th className={th}>Detected</th><th className={th}>Severity</th><th className={th}>People</th><th className={th}>Data</th><th className={th}>Commissioner</th><th className={th}>Keep record until</th><th className={th}>Status</th><th className={th}></th></tr></thead>
          <tbody>{data.items.map((b: BreachRow) => (<>
            <tr key={b.id}>
              <td className={`${td} font-mono text-xs`}>{b.ref}</td><td className={`${td} text-xs`}>{fmt(b.detected_at)}</td>
              <td className={td}><span className={`badge ${b.severity === "critical" || b.severity === "high" ? "badge-red" : "badge-amber"}`}>{b.severity}</span></td>
              <td className={`${td} tabular-nums`}>{b.individuals_affected}</td><td className={`${td} text-xs`}>{b.data_categories.join(", ") || "—"}</td>
              <td className={`${td} text-xs`}>{b.commissioner_notified_at ? fmt(b.commissioner_notified_at) : "not reported"}</td>
              <td className={`${td} text-xs`}>{b.keep_record_until}</td>
              <td className={td}><span className="badge badge-teal">{b.status}</span></td>
              <td className={`${td} text-right`}><button className="text-xs text-primary-700 hover:underline" onClick={() => setOpen(open === b.id ? null : b.id)}>{open === b.id ? "Close" : "Checklist"}</button></td>
            </tr>
            {open === b.id && <tr key={b.id + "x"}><td colSpan={9} className="bg-gray-50/70 px-4 py-3">
              <div className="text-xs text-gray-600 mb-2">{b.description}</div>
              <div className="grid sm:grid-cols-2 gap-1">
                {b.checklist.map((c) => <label key={c.key} className="flex items-start gap-2 text-xs text-gray-700">
                  <input type="checkbox" checked={c.done} onChange={(e) => patch.mutate({ id: b.id, body: e.target.checked ? { checklist_done: [c.key], ...(c.key === "report_commissioner" ? { commissioner_notified: true } : {}), ...(c.key === "notify_individuals" ? { individuals_notified: true } : {}) } : { checklist_undone: [c.key] } })} />
                  <span>{c.label}{c.at && <span className="text-gray-400"> · {fmt(c.at)}</span>}</span></label>)}
              </div>
              <div className="flex gap-2 mt-2">
                {["contained", "notified", "closed"].map((s) => <button key={s} className="text-xs border border-surface-border rounded px-2 py-1 bg-white hover:bg-gray-50" onClick={() => patch.mutate({ id: b.id, body: { status: s } })}>Mark {s}</button>)}
              </div>
            </td></tr>}
          </>))}
            {!data.items.length && <tr><td colSpan={9}><Empty>No breaches logged. Good — keep it that way.</Empty></td></tr>}
          </tbody>
        </table></div>
      </div>
      <div className="card">
        <div className="font-display font-semibold text-gray-800 mb-1">Log a breach</div>
        <p className="text-[11px] text-gray-400 mb-2">PIPEDA s.10.1: record every breach (kept 24 months). If there is a real risk of significant harm, report it to the Privacy Commissioner and notify each affected person as soon as feasible.</p>
        <textarea className={inputCls} rows={4} placeholder="What happened" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        <div className="grid grid-cols-2 gap-2 mt-2">
          <div><label className="text-[11px] text-gray-500">People affected</label><input type="number" className={inputCls} value={form.individuals_affected} onChange={(e) => setForm({ ...form, individuals_affected: Number(e.target.value) })} /></div>
          <div><label className="text-[11px] text-gray-500">Severity</label><select className={inputCls} value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>{["low", "medium", "high", "critical"].map((s) => <option key={s}>{s}</option>)}</select></div>
        </div>
        <label className="text-[11px] text-gray-500">Data involved (comma separated)</label>
        <input className={inputCls} placeholder="sin, bank, salary" value={form.data_categories} onChange={(e) => setForm({ ...form, data_categories: e.target.value })} />
        <button disabled={form.description.length < 10 || create.isPending} onClick={() => create.mutate()} className="w-full mt-2 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-sm font-semibold py-2 disabled:opacity-50">Log breach</button>
      </div>
    </div>
  );
}

// ── Retention ──────────────────────────────────────────────────────────────
function RetentionTab() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["privacy", "retention"], queryFn: privacyApi.retention });
  const { data: runs } = useQuery({ queryKey: ["privacy", "retention-runs"], queryFn: privacyApi.retentionRuns });
  const [rows, setRows] = useState<RetentionRow[] | null>(null);
  const items = rows || data?.items || [];
  const save = useMutation({ mutationFn: () => privacyApi.saveRetention(items), onSuccess: () => { toast.success("Retention policy saved"); setRows(null); qc.invalidateQueries({ queryKey: ["privacy"] }); } });
  const run = useMutation({ mutationFn: (dry: boolean) => privacyApi.runRetention(dry),
    onSuccess: (r: any) => { toast.success(`${r.dry_run ? "Dry-run" : "Purge"} done — ${r.total_candidates} candidate(s)`); qc.invalidateQueries({ queryKey: ["privacy"] }); },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Run failed") });
  if (isLoading) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const set = (i: number, k: string, v: any) => { const n = items.map((r, j) => (j === i ? { ...r, [k]: v } : r)); setRows(n); };
  const last = runs?.items?.[0];
  // S4: default periods are a starting point — each stays "needs confirmation" until confirmed.
  const confirmRow = (record_type: string) => privacyApi.saveRetention([{ record_type, confirm: true } as any])
    .then(() => { toast.success("Retention period confirmed"); qc.invalidateQueries({ queryKey: ["privacy"] }); })
    .catch((e: any) => toast.error(e?.response?.data?.detail || "Could not confirm"));
  const unconfirmed = items.filter((r: any) => r.needs_confirmation).length;
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="lg:col-span-2 card !p-0">
        <div className="px-4 py-2.5 border-b border-surface-border flex items-center gap-2">
          <span className="font-display font-semibold text-gray-800">Retention per record type</span>
          <span className="text-[11px] text-gray-400">years after the year of exit / last entry</span>
          <button onClick={() => save.mutate()} disabled={!rows || save.isPending} className="ml-auto rounded-lg bg-primary-600 text-white text-xs font-semibold px-3 py-1.5 disabled:opacity-50">Save</button>
        </div>
        <table className="w-full">
          <thead><tr><th className={th}>Record type</th><th className={th}>Years</th><th className={th}>Action</th><th className={th}>On</th><th className={th}>Legal basis</th><th className={th}>Confirmed</th></tr></thead>
          <tbody>{items.map((r, i) => (
            <tr key={r.record_type}>
              <td className={`${td} font-medium whitespace-nowrap`}>{r.record_type.replace(/_/g, " ")}</td>
              <td className={td}><input type="number" min={0} max={30} className="w-16 border border-surface-border rounded px-2 py-0.5 text-sm" value={r.retain_years} onChange={(e) => set(i, "retain_years", Number(e.target.value))} /></td>
              <td className={td}><select className="border border-surface-border rounded px-2 py-0.5 text-sm" value={r.action} onChange={(e) => set(i, "action", e.target.value)}>{["anonymise", "delete", "review"].map((a) => <option key={a}>{a}</option>)}</select></td>
              <td className={td}><input type="checkbox" checked={r.enabled} onChange={(e) => set(i, "enabled", e.target.checked)} /></td>
              <td className={`${td} text-[11px] text-gray-500`}>{r.legal_basis}</td>
              <td className={`${td} whitespace-nowrap`}>{(r as any).needs_confirmation
                ? <><span className="badge badge-amber">needs confirmation</span> <button className="text-[11px] text-primary-700 underline" onClick={() => confirmRow(r.record_type)}>Confirm</button></>
                : <span className="text-[11px] text-gray-500">{(r as any).confirmed_by || "yes"}</span>}</td>
            </tr>))}</tbody>
        </table>
        {unconfirmed > 0 && <div className="px-4 py-2 text-[11px] text-amber-800 bg-amber-50 border-t border-amber-200">
          {unconfirmed} period(s) are Lexora HRM defaults, not legal advice. Have your counsel / accountant confirm each period, then press Confirm. Editing a period clears its confirmation.</div>}
      </div>
      <div className="card">
        <div className="font-display font-semibold text-gray-800 mb-1">Purge job</div>
        <p className="text-[11px] text-gray-400 mb-2">A dry-run runs every day by itself and only counts. A real purge anonymises exited employees past the window (statutory identifiers stay encrypted), deletes stale onboarding files and old login rows. Payroll records are never auto-deleted.</p>
        <div className="flex gap-2">
          <button onClick={() => run.mutate(true)} disabled={run.isPending} className="flex-1 inline-flex items-center justify-center gap-1 rounded-lg border border-primary-600 text-primary-700 text-sm font-semibold py-2"><RefreshCw className="w-4 h-4" /> Dry-run now</button>
          <button onClick={() => { if (confirm("Really purge? Exited employees past the retention window will be anonymised. This cannot be undone.")) run.mutate(false); }} disabled={run.isPending} className="flex-1 inline-flex items-center justify-center gap-1 rounded-lg bg-rose-600 text-white text-sm font-semibold py-2"><Play className="w-4 h-4" /> Run purge</button>
        </div>
        {last && <div className="mt-3 text-xs text-gray-600">
          <div className="font-semibold text-gray-700">Last run · {fmt(last.run_at)} · {last.dry_run ? "dry-run" : "PURGE"} · {last.triggered_by}</div>
          <table className="w-full mt-1"><tbody>
            {Object.entries(last.summary?.record_types || {}).map(([k, v]: any) => <tr key={k}><td className="py-0.5 text-gray-500">{k.replace(/_/g, " ")}</td><td className="py-0.5 text-right tabular-nums">{v.count ?? v.error}</td><td className="py-0.5 text-right text-gray-400 pl-2">{v.action}</td></tr>)}
          </tbody></table>
          {last.summary?.acted && !last.dry_run && <div className="mt-1 text-gray-500">acted: {JSON.stringify(last.summary.acted)}</div>}
        </div>}
      </div>
    </div>
  );
}

// ── Processor contract ─────────────────────────────────────────────────────
function ContractTab({ role }: { role: string }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["privacy", "contract"], queryFn: privacyApi.processorContract });
  const accept = useMutation({ mutationFn: privacyApi.acceptContract, onSuccess: () => { toast.success("Terms accepted"); qc.invalidateQueries({ queryKey: ["privacy"] }); }, onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not accept") });
  if (isLoading || !data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  return (
    <div className="card">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h3 className="font-display font-semibold text-gray-800">Service-provider privacy terms — Lexora HRM (v{data.version})</h3>
        {data.accepted_at ? <span className="badge badge-green">accepted v{data.accepted_version} · {fmt(data.accepted_at)} · {data.accepted_by}</span> : <span className="badge badge-amber">not yet accepted</span>}
        <button onClick={async () => saveBlob(await privacyApi.processorContractPdf(), "lexora-service-provider-privacy-terms.pdf")} className="ml-auto text-xs inline-flex items-center gap-1 border border-surface-border rounded-lg px-2 py-1 bg-white hover:bg-gray-50"><Download className="w-3 h-3" /> PDF</button>
        {["org_admin", "superadmin"].includes(role) && !data.accepted_at && <button onClick={() => accept.mutate()} className="text-xs rounded-lg bg-primary-600 text-white font-semibold px-3 py-1.5">Accept on behalf of the company</button>}
      </div>
      <pre className="whitespace-pre-wrap text-[13px] leading-relaxed text-gray-700 font-sans">{data.text}</pre>
    </div>
  );
}

// ── Security ───────────────────────────────────────────────────────────────
function SecurityTab({ role }: { role: string }) {
  const { data: enc } = useQuery({ queryKey: ["enc-status"], queryFn: sensitiveApi.encryptionStatus });
  const [busy, setBusy] = useState(false);
  const exportOrg = async () => { try { setBusy(true); saveBlob(await securityApi.orgExport(), "lexora-org-export.zip"); toast.success("Export ready"); } catch { toast.error("Export failed"); } finally { setBusy(false); } };
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div className="card">
        <h3 className="font-display font-semibold text-gray-800 mb-1">Full organisation export</h3>
        <p className="text-[11px] text-gray-400 mb-2">One zip with a JSON file per table for your organisation (employees, payroll, leave, attendance, consents, requests…). Encrypted identifiers stay encrypted inside the export.</p>
        {["org_admin", "superadmin"].includes(role) ? <button onClick={exportOrg} disabled={busy} className="rounded-lg bg-primary-600 text-white text-sm font-semibold px-4 py-2 disabled:opacity-50 inline-flex items-center gap-1"><Download className="w-4 h-4" /> {busy ? "Preparing…" : "Download backup zip"}</button> : <div className="text-xs text-gray-400">Org admin only.</div>}
      </div>
      <div className="card">
        <h3 className="font-display font-semibold text-gray-800 mb-1">Sign-in audit</h3>
        <p className="text-[11px] text-gray-400 mb-2">Every sign-in attempt (success / wrong password / locked) with IP and browser.</p>
        <Link to="/security/sessions" className="text-sm text-primary-700 font-medium hover:underline">Open session audit →</Link>
        <div className="text-[11px] text-gray-400 mt-3">Login rate limit: 5 failures per email + IP in 15 minutes → locked 15 minutes. Password policy: 10+ characters with capital, small, number, symbol.</div>
      </div>
      <div className="card">
        <h3 className="font-display font-semibold text-gray-800 mb-1">Encryption key</h3>
        {enc ? <table className="w-full text-xs"><tbody>
          {[["Key id", enc.key_id], ["Production key", enc.configured ? "set" : "NOT SET (dev fallback)"], ["Keys in ring", (enc.keys_in_ring || []).join(", ")], ["Cutover", enc.cutover_done ? "done" : "pending"], ["Launch-ready", enc.launch_ready ? "yes" : "no"]].map(([k, v]) => <tr key={String(k)}><td className="py-0.5 text-gray-500 w-32">{k}</td><td className="py-0.5 text-gray-800">{String(v)}</td></tr>)}
        </tbody></table> : <Spinner className="h-5 w-5" />}
        <p className="text-[11px] text-gray-400 mt-2">Set <code>LEXORA_DATA_KEY</code> (base64 of 32 random bytes) on every service before launch; rotate with <code>LEXORA_DATA_KEYS</code> + <code>LEXORA_ENCRYPTION_ROTATE=1</code>.</p>
      </div>
    </div>
  );
}
