import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { BookOpenCheck, Plus, Upload, CheckCircle2, Archive, Pencil, Info, BarChart3 } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../../components/DataGrid";
import { PageHeader, Spinner, StatTile, Badge, Empty } from "../../../components/ui";
import { errText, when } from "../../../api/platform";
import { deskApi, type DeskClause, type DeskPolicy, type DeskScope } from "./api";

/** HR Desk → Policy clauses (HR admin). Import a policy document, review the AI-readiness notes, approve clause by clause (SME sign-off).
 *  Only APPROVED versions are ever used for answers; a clause is versioned + effective-dated + scoped (location / grade / employment type / role).
 *  Route to wire (stream A / phase 2): <Route path="ai/desk/policies" element={<RequireRole allowed={HR_WRITE}><PolicyClausesPage /></RequireRole>} />
 *  Nav: { to: "/ai/desk/policies", label: "HR Desk policies", icon: BookOpenCheck, roles: HR_WRITE, ai: true } */
export default function PolicyClausesPage() {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const policies = useQuery({ queryKey: ["desk", "policies"], queryFn: deskApi.policies });
  const [pid, setPid] = useState<string>(sp.get("policy") || "");
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [metricsOpen, setMetricsOpen] = useState(false);
  const detail = useQuery({ queryKey: ["desk", "policy", pid], queryFn: () => deskApi.policy(pid), enabled: !!pid });
  const refresh = () => qc.invalidateQueries({ queryKey: ["desk"] });
  useEffect(() => { if (!pid && policies.data?.items?.length) setPid(policies.data.items[0].id); }, [policies.data, pid]);
  useEffect(() => { const n: Record<string, string> = {}; if (pid) n.policy = pid; if (sp.get("clause")) n.clause = sp.get("clause")!; setSp(n, { replace: true }); }, [pid]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (policies.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (policies.isError) return <div className="card text-sm text-red-600">Could not load HR Desk policies. {errText(policies.error)}</div>;
  const items = policies.data?.items || [];
  const current = items.find((p) => p.id === pid) || null;
  const askedQ = sp.get("q");   // from the HR Desk dashboard: an employee question the desk could not answer from policy

  return (
    <div>
      {askedQ && (
        <div className="card mb-3 border-amber-200 bg-amber-50/60 text-sm text-amber-900">
          <b>Write a clause for this question:</b> “{askedQ}” — pick the policy it belongs to, add the clause (one rule, a heading, an effective date), then approve it. The desk answers from approved clauses only.
        </div>)}
      <PageHeader title="HR Desk — policy clauses" icon={BookOpenCheck}
        subtitle="What the Ask-HR assistant may quote. Import a policy, fix the readiness notes, approve each clause (SME sign-off). Only approved versions are used; every answer cites policy, clause, version and effective date."
        actions={<div className="flex gap-2">
          <button className="btn-secondary text-xs flex items-center gap-1" onClick={() => setMetricsOpen(true)}><BarChart3 className="w-3.5 h-3.5" /> Weekly report</button>
          <button className="btn-primary text-xs flex items-center gap-1" onClick={() => setCreating(true)}><Plus className="w-3.5 h-3.5" /> New policy</button></div>} />
      {!items.length ? <Empty>No HR Desk policies yet. Create one, then import the document (txt / md / docx / pdf) or paste its text.</Empty> : (
        <div className="grid grid-cols-1 xl:grid-cols-[320px_1fr] gap-4">
          <div className="card !p-0 overflow-hidden self-start">
            <div className="px-3 py-2 text-[11px] font-semibold text-gray-400 uppercase tracking-wide border-b border-surface-border">Policies</div>
            <ul className="divide-y divide-surface-border max-h-[70vh] overflow-auto">
              {items.map((p) => (
                <li key={p.id}>
                  <button onClick={() => setPid(p.id)} className={`w-full text-left px-3 py-2.5 hover:bg-primary-50/60 ${p.id === pid ? "bg-primary-50" : ""}`}>
                    <div className="flex items-center justify-between gap-2"><span className="font-medium text-sm text-gray-800 truncate">{p.title}</span><Badge tone={p.status === "approved" ? "active" : p.status === "retired" ? "terminated" : "on_leave"}>{p.status}</Badge></div>
                    <div className="text-[11px] text-gray-500 mt-0.5">{p.key} · {p.approved} approved / {p.clauses} clauses{p.avg_ready !== null ? ` · readiness ${p.avg_ready}` : ""}{p.owner ? ` · ${p.owner}` : ""}</div>
                  </button>
                </li>))}
            </ul>
          </div>
          <div className="min-w-0">
            {!current ? null : detail.isLoading || !detail.data ? <div className="grid place-items-center py-16"><Spinner /></div> : (
              <PolicyDetail policy={detail.data.policy} clauses={detail.data.clauses} focusClause={sp.get("clause")} onImport={() => setImporting(true)} onChanged={refresh} />
            )}
          </div>
        </div>
      )}
      {creating && <CreatePolicy onClose={() => setCreating(false)} onCreated={(p) => { setCreating(false); setPid(p.id); refresh(); }} />}
      {importing && current && <ImportDialog policy={current} onClose={() => setImporting(false)} onDone={() => { setImporting(false); refresh(); }} />}
      {metricsOpen && <MetricsDialog onClose={() => setMetricsOpen(false)} />}
    </div>
  );
}

const READY = (s: number) => (s >= 80 ? "bg-green-100 text-green-700" : s >= 40 ? "bg-amber-100 text-amber-700" : "bg-red-100 text-red-700");
const scopeText = (s: DeskScope | null | undefined) => {
  const parts = [s?.locations?.length ? `locations: ${s.locations.join(", ")}` : "", s?.grades?.length ? `grades: ${s.grades.join(", ")}` : "",
    s?.employment_types?.length ? `types: ${s.employment_types.join(", ")}` : "", s?.roles?.length ? `roles: ${s.roles.join(", ")}` : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : "everyone";
};

function PolicyDetail({ policy, clauses, focusClause, onImport, onChanged }: { policy: DeskPolicy; clauses: DeskClause[]; focusClause: string | null; onImport: () => void; onChanged: () => void }) {
  type Tab = "draft" | "approved" | "retired" | "all";
  const [tab, setTab] = useState<Tab>(clauses.some((c) => c.status === "draft") ? "draft" : "approved");
  const [editing, setEditing] = useState<DeskClause | null>(null);
  const [adding, setAdding] = useState(false);
  const approve = useMutation({ mutationFn: (c: DeskClause) => deskApi.approve(c.id, {}), onSuccess: (c) => { toast.success(`Approved cl. ${c.clause_id} v${c.version} (effective ${c.effective_from})`); onChanged(); }, onError: (e) => toast.error(errText(e)) });
  const retire = useMutation({ mutationFn: (c: DeskClause) => deskApi.retire(c.id), onSuccess: () => { toast.success("Clause retired"); onChanged(); }, onError: (e) => toast.error(errText(e)) });
  const rows = useMemo(() => clauses.filter((c) => tab === "all" || c.status === tab), [clauses, tab]);
  const counts = { draft: clauses.filter((c) => c.status === "draft").length, approved: clauses.filter((c) => c.status === "approved").length, retired: clauses.filter((c) => c.status === "retired").length };
  const avg = clauses.length ? Math.round(clauses.reduce((a, c) => a + (c.ai_ready_score || 0), 0) / clauses.length) : null;
  useEffect(() => { if (focusClause) { const c = clauses.find((x) => x.id === focusClause); if (c) { setTab(c.status as Tab); setTimeout(() => document.getElementById(`clause-${c.id}`)?.scrollIntoView({ block: "center" }), 50); } } }, [focusClause, clauses]);

  const cols: GridColumn<DeskClause>[] = [
    { key: "clause_id", label: "Clause", sortable: true, width: "90px", render: (c) => <span id={`clause-${c.id}`} className={`font-mono text-xs ${c.id === focusClause ? "bg-primary-100 px-1 rounded" : ""}`}>{c.clause_id} <span className="text-gray-400">v{c.version}</span></span>, text: (c) => `${c.clause_id} v${c.version}` },
    { key: "title", label: "Title", sortable: true, render: (c) => <div><div className="font-medium text-gray-800">{c.title || "—"}</div><div className="text-xs text-gray-500 line-clamp-2 max-w-xl">{c.text}</div></div>, text: (c) => `${c.title} ${c.text}` },
    { key: "effective_from", label: "Effective", type: "date", sortable: true, render: (c) => <span className="text-xs tabular-nums">{c.effective_from || "—"}{c.effective_to ? ` → ${c.effective_to}` : ""}</span>, text: (c) => c.effective_from || "" },
    { key: "scope", label: "Scope", render: (c) => <span className="text-xs text-gray-600">{scopeText(c.scope)}</span>, text: (c) => scopeText(c.scope) },
    { key: "ai_ready_score", label: "AI-ready", type: "number", sortable: true, render: (c) => <span title={(c.ai_ready_notes || []).join("\n")} className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full ${READY(c.ai_ready_score)}`}>{c.ai_ready_score}{c.ai_ready_notes?.length ? ` · ${c.ai_ready_notes.length} note${c.ai_ready_notes.length > 1 ? "s" : ""}` : ""}</span>, text: (c) => String(c.ai_ready_score) },
    { key: "status", label: "Status", sortable: true, render: (c) => <div><Badge tone={c.status === "approved" ? "active" : c.status === "retired" ? "terminated" : "on_leave"}>{c.status}</Badge>{c.approved_by && <div className="text-[10px] text-gray-400 mt-0.5">{c.approved_by} · {c.approved_at ? when(c.approved_at) : ""}</div>}</div>, text: (c) => c.status },
    { key: "_", label: "", render: (c) => <span className="flex gap-1">
      {c.status !== "retired" && <button className="icon-btn text-gray-500" title={c.status === "approved" ? "Edit (creates the next draft version)" : "Edit draft"} onClick={() => setEditing(c)}><Pencil className="w-4 h-4" /></button>}
      {c.status === "draft" && <button className="icon-btn text-green-600" title="Approve (SME sign-off)" onClick={() => { if (confirm(`Approve clause ${c.clause_id} v${c.version}? It becomes quotable from ${c.effective_from || "today"} and any earlier approved version is closed the day before.`)) approve.mutate(c); }}><CheckCircle2 className="w-4 h-4" /></button>}
      {c.status === "approved" && <button className="icon-btn text-red-600" title="Retire" onClick={() => { if (confirm(`Retire clause ${c.clause_id} v${c.version}? The assistant stops quoting it today.`)) retire.mutate(c); }}><Archive className="w-4 h-4" /></button>}
    </span> },
  ];

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <StatTile label="Approved clauses" value={counts.approved} accent sub={`${counts.draft} draft · ${counts.retired} retired`} />
        <StatTile label="AI-readiness (avg)" value={avg ?? "—"} sub="length · heading · effective date · no dead ends" />
        <StatTile label="Owner" value={<span className="text-base">{policy.owner || "—"}</span>} sub={policy.source_doc ? `source: ${policy.source_doc}` : "no source document recorded"} />
        <StatTile label="Policy" value={<Badge tone={policy.status === "approved" ? "active" : "on_leave"}>{policy.status}</Badge>} sub={policy.key} />
      </div>
      <div className="flex items-center justify-between gap-2 mb-2">
        <Tabs<Tab> tabs={[{ key: "draft", label: "Drafts", count: counts.draft }, { key: "approved", label: "Approved", count: counts.approved }, { key: "retired", label: "Retired", count: counts.retired }, { key: "all", label: "All" }]} value={tab} onChange={setTab} />
        <div className="flex gap-2 shrink-0"><button className="btn-secondary text-xs flex items-center gap-1" onClick={() => setAdding(true)}><Plus className="w-3.5 h-3.5" /> Clause</button>
          <button className="btn-primary text-xs flex items-center gap-1" onClick={onImport}><Upload className="w-3.5 h-3.5" /> Import document</button></div>
      </div>
      <DataGrid<DeskClause> columns={cols} rows={rows} rowKey={(c) => c.id} exportName={`desk-policy-${policy.key}`} empty={tab === "draft" ? "No drafts — import a document or add a clause." : "Nothing here yet."} minWidth={1000} />
      <p className="text-[11px] text-gray-500 mt-3 inline-flex items-start gap-1"><Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />Approving a new version closes the previous approved version the day before the new effective date, so a question about a past date still gets the clause that applied then. An approved clause is never edited in place.</p>
      {editing && <ClauseDialog policyId={policy.id} clause={editing} onClose={() => setEditing(null)} onDone={() => { setEditing(null); onChanged(); }} />}
      {adding && <ClauseDialog policyId={policy.id} clause={null} onClose={() => setAdding(false)} onDone={() => { setAdding(false); onChanged(); }} />}
    </div>
  );
}

function CreatePolicy({ onClose, onCreated }: { onClose: () => void; onCreated: (p: DeskPolicy) => void }) {
  const [title, setTitle] = useState("");
  const [key, setKey] = useState("");
  const [owner, setOwner] = useState("");
  const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60);
  const create = useMutation({ mutationFn: () => deskApi.createPolicy({ key: key || slug(title), title: title.trim(), owner }), onSuccess: onCreated, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title="New HR Desk policy" subtitle="A policy is a container of clauses; the assistant quotes clauses, not the document." onClose={onClose} size="wide"
      footer={<><button className="btn-secondary text-xs" onClick={onClose}>Cancel</button><button className="btn-primary text-xs" disabled={title.trim().length < 2 || create.isPending} onClick={() => create.mutate()}>Create</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <label className="text-xs text-gray-600">Title<input className="input mt-1 w-full" value={title} onChange={(e) => { setTitle(e.target.value); if (!key) setKey(""); }} placeholder="Leave Policy" /></label>
        <label className="text-xs text-gray-600">Key<input className="input mt-1 w-full font-mono" value={key || slug(title)} onChange={(e) => setKey(slug(e.target.value))} placeholder="leave" /></label>
        <label className="text-xs text-gray-600">Owner (SME)<input className="input mt-1 w-full" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="HR head / policy owner" /></label>
      </div>
    </WideModal>
  );
}

function ImportDialog({ policy, onClose, onDone }: { policy: DeskPolicy; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [eff, setEff] = useState("");
  const [source, setSource] = useState(policy.source_doc || "");
  const [scope, setScope] = useState<DeskScope>({});
  const [result, setResult] = useState<{ imported: number; avg_ready: number | null; next_step: string } | null>(null);
  const imp = useMutation({
    mutationFn: () => file ? deskApi.importFile(policy.id, file, eff || undefined) : deskApi.importText(policy.id, { text, source_doc: source, effective_from: eff || null, scope }),
    onSuccess: (r) => { setResult(r); toast.success(`${r.imported} draft clause(s) imported`); }, onError: (e) => toast.error(errText(e)) });
  const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  return (
    <WideModal title={`Import into ${policy.title}`} subtitle="Headings and numbered sections become DRAFT clauses with an AI-readiness score. Existing drafts of this policy are replaced; approved clauses are untouched." onClose={onClose} size="wide"
      footer={<><button className="btn-secondary text-xs" onClick={result ? onDone : onClose}>{result ? "Done" : "Cancel"}</button>
        {!result && <button className="btn-primary text-xs" disabled={imp.isPending || (!file && text.trim().length < 20)} onClick={() => imp.mutate()}>{imp.isPending ? "Importing…" : "Import"}</button>}</>}>
      {result ? (
        <div className="text-sm text-gray-700 space-y-2"><div className="flex items-center gap-2"><CheckCircle2 className="w-5 h-5 text-green-600" /><b>{result.imported}</b> draft clause(s) · average readiness <b>{result.avg_ready ?? "—"}</b>/100</div><p>{result.next_step}</p></div>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <label className="text-xs text-gray-600">File (txt / md / docx / pdf)<input type="file" accept=".txt,.md,.docx,.pdf" className="mt-1 block w-full text-xs" onChange={(e) => setFile(e.target.files?.[0] || null)} /></label>
            <label className="text-xs text-gray-600">Effective from (if the document does not say)<input type="date" className="input mt-1 w-full" value={eff} onChange={(e) => setEff(e.target.value)} /></label>
            <label className="text-xs text-gray-600">Source document reference<input className="input mt-1 w-full" value={source} onChange={(e) => setSource(e.target.value)} placeholder="Leave Policy v3.pdf / SharePoint link" /></label>
          </div>
          {!file && <label className="text-xs text-gray-600 block">Or paste the policy text<textarea className="input mt-1 w-full h-56 font-mono text-xs" value={text} onChange={(e) => setText(e.target.value)} placeholder={"# Leave Policy\nEffective from 1 April 2026\n\n## 3.1 Casual leave\nEmployees are entitled to 12 days…"} /></label>}
          <details className="text-xs text-gray-600"><summary className="cursor-pointer font-medium">Scope (who these clauses apply to — blank = everyone)</summary>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mt-2">
              <label>Locations<input className="input mt-1 w-full" placeholder="Bengaluru, Mumbai" onChange={(e) => setScope({ ...scope, locations: list(e.target.value) })} /></label>
              <label>Grades<input className="input mt-1 w-full" placeholder="G1, G2" onChange={(e) => setScope({ ...scope, grades: list(e.target.value) })} /></label>
              <label>Employment types<input className="input mt-1 w-full" placeholder="full_time, contract" onChange={(e) => setScope({ ...scope, employment_types: list(e.target.value) })} /></label>
              <label>Roles<input className="input mt-1 w-full" placeholder="employee, manager" onChange={(e) => setScope({ ...scope, roles: list(e.target.value) })} /></label>
            </div></details>
        </div>
      )}
    </WideModal>
  );
}

function ClauseDialog({ policyId, clause, onClose, onDone }: { policyId: string; clause: DeskClause | null; onClose: () => void; onDone: () => void }) {
  const [cid, setCid] = useState(clause?.clause_id || "");
  const [title, setTitle] = useState(clause?.title || "");
  const [text, setText] = useState(clause?.text || "");
  const [from, setFrom] = useState(clause?.effective_from || "");
  const [to, setTo] = useState(clause?.effective_to || "");
  const [scope, setScope] = useState<DeskScope>(clause?.scope || {});
  const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
  const save = useMutation({
    mutationFn: () => clause ? deskApi.patchClause(clause.id, { clause_id: cid, title, text, effective_from: from || null, effective_to: to || null, scope })
      : deskApi.addClause(policyId, { clause_id: cid, title, text, effective_from: from || null, effective_to: to || null, scope }),
    onSuccess: (c) => { toast.success(c.note || `Saved cl. ${c.clause_id} v${c.version} (draft, readiness ${c.ai_ready_score})`); onDone(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={clause ? `Clause ${clause.clause_id} v${clause.version}${clause.status === "approved" ? " — approved: your edit becomes the next draft version" : ""}` : "New clause (draft)"} onClose={onClose} size="wide"
      footer={<><button className="btn-secondary text-xs" onClick={onClose}>Cancel</button><button className="btn-primary text-xs" disabled={!cid || text.trim().length < 5 || save.isPending} onClick={() => save.mutate()}>Save draft</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-3">
        <label className="text-xs text-gray-600">Clause no.<input className="input mt-1 w-full font-mono" value={cid} onChange={(e) => setCid(e.target.value)} placeholder="3.1" /></label>
        <label className="text-xs text-gray-600 md:col-span-3">Title<input className="input mt-1 w-full" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Casual leave" /></label>
      </div>
      <label className="text-xs text-gray-600 block mb-3">Text (one rule, complete on its own, no "see HR")<textarea className="input mt-1 w-full h-40" value={text} onChange={(e) => setText(e.target.value)} /></label>
      <div className="grid grid-cols-1 md:grid-cols-6 gap-3">
        <label className="text-xs text-gray-600">Effective from<input type="date" className="input mt-1 w-full" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="text-xs text-gray-600">Effective to<input type="date" className="input mt-1 w-full" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <label className="text-xs text-gray-600">Locations<input className="input mt-1 w-full" defaultValue={(scope.locations || []).join(", ")} onChange={(e) => setScope({ ...scope, locations: list(e.target.value) })} /></label>
        <label className="text-xs text-gray-600">Grades<input className="input mt-1 w-full" defaultValue={(scope.grades || []).join(", ")} onChange={(e) => setScope({ ...scope, grades: list(e.target.value) })} /></label>
        <label className="text-xs text-gray-600">Employment types<input className="input mt-1 w-full" defaultValue={(scope.employment_types || []).join(", ")} onChange={(e) => setScope({ ...scope, employment_types: list(e.target.value) })} /></label>
        <label className="text-xs text-gray-600">Roles<input className="input mt-1 w-full" defaultValue={(scope.roles || []).join(", ")} onChange={(e) => setScope({ ...scope, roles: list(e.target.value) })} /></label>
      </div>
      {clause?.ai_ready_notes?.length ? <div className="mt-3 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-2">Readiness notes: {clause.ai_ready_notes.join(" · ")}</div> : null}
    </WideModal>
  );
}

function MetricsDialog({ onClose }: { onClose: () => void }) {
  const [days, setDays] = useState(7);
  const m = useQuery({ queryKey: ["desk", "metrics", days], queryFn: () => deskApi.metrics(days) });
  const pct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
  return (
    <WideModal title="HR Desk — weekly report" subtitle="Self-solved deflection, abstain and hand-off rates, CSAT and the top questions. Every number is defined below." onClose={onClose} size="wide"
      actions={<select className="input text-xs" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option></select>}>
      {m.isLoading || !m.data ? <div className="grid place-items-center py-10"><Spinner /></div> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatTile label="Self-solved (deflection)" value={pct(m.data.deflection_rate)} accent sub={`${m.data.self_solved} of ${m.data.candidate_sessions} sessions`} />
            <StatTile label="Abstain rate" value={pct(m.data.abstain_rate)} sub={`${m.data.abstained_turns} of ${m.data.answered_turns + m.data.abstained_turns} turns`} />
            <StatTile label="Hand-off rate" value={pct(m.data.handoff_rate)} sub={`${m.data.handoffs} tickets · ${m.data.hard_routed_sessions} hard-routed`} />
            <StatTile label="CSAT" value={pct(m.data.csat)} sub={`${m.data.csat_rated} rated · median ${m.data.median_ms !== null ? `${(m.data.median_ms / 1000).toFixed(1)} s` : "—"}`} />
          </div>
          <div><h3 className="font-display font-semibold text-gray-800 mb-1 text-sm">Top questions (by intent)</h3>
            {m.data.top_intents.length ? <ul className="text-sm text-gray-700 grid grid-cols-1 md:grid-cols-2 gap-x-6">{m.data.top_intents.map((t) => <li key={t.intent} className="flex justify-between border-b border-surface-border py-1"><span className="font-mono text-xs">{t.intent}</span><span className="tabular-nums">{t.count}</span></li>)}</ul> : <Empty>No questions in this window.</Empty>}</div>
          <details className="text-xs text-gray-600"><summary className="cursor-pointer font-medium">Definitions</summary><ul className="list-disc ml-5 mt-1 space-y-0.5">{Object.entries(m.data.definitions).map(([k, v]) => <li key={k}><b>{k}</b>: {v}</li>)}</ul></details>
        </div>
      )}
    </WideModal>
  );
}
