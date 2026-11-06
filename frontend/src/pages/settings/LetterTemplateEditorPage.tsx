// D1 — Settings → Letter templates: rich-text editor + merge-field picker (employee, org, salary,
// dates, custom fields) + live preview with a sample employee + draft / publish / version history /
// rollback. The letter generation API is untouched: letters use the PUBLISHED body only.
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Navigate } from "react-router-dom";
import toast from "react-hot-toast";
import { FileSignature, History, Save, Send, RotateCcw, Eye, Plus, X } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import LetterRichEditor, { type MergeField } from "../../components/letters/LetterRichEditor";
import { lettersApi, openPdf, blobErrText, fmtDate } from "../../api/lifecycle";
import { letterTemplatesApi, errDetail, type PreviewText, type TemplateVersion } from "../../api/config";
import { useEmployees, empLabel } from "../common/_shared";
import { useAuthStore } from "../../store/authStore";
import AppModal, { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const SETUP = ["org_admin", "hr_admin", "superadmin"];
const VIEW = [...SETUP, "hr_manager", "payroll_admin"];
const LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1";

export default function LetterTemplateEditorPage() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const qc = useQueryClient();
  const [open, setOpen] = useState<any | null>(null);
  const { data: templates = [], isLoading } = useQuery({ queryKey: ["letter-templates"], queryFn: () => lettersApi.templates() });
  if (!role) return <Navigate to="/login" replace />;
  if (!VIEW.includes(role)) return <Navigate to="/settings" replace />;
  const canSetup = SETUP.includes(role);
  const cols: GridColumn<any>[] = [
    { key: "name", label: "Template", render: (r) => <span className="font-medium text-gray-800">{r.name}</span> },
    { key: "key", label: "Key", render: (r) => <span className="font-mono text-[11px]">{r.key}</span> },
    { key: "country", label: "Country", render: (r) => r.country || "Any" },
    { key: "version", label: "Published version", type: "number", width: "120px" },
    { key: "has_draft", label: "Draft", text: (r) => (r.has_draft ? "yes" : "no"), render: (r) => (r.has_draft ? <span className="badge-amber">Unpublished draft</span> : <span className="text-gray-300">—</span>) },
    { key: "is_default", label: "Text", text: (r) => (r.is_default ? "default" : "edited"), render: (r) => (r.is_default ? <span className="badge-purple">Default</span> : <span className="badge-teal">Edited</span>) },
    { key: "fields", label: "Fields", text: (r) => (r.fields || []).join(" "), render: (r) => <span className="text-[11px] text-gray-500">{(r.fields || []).length} fields{(r.fields || []).some((f: string) => f.startsWith("custom.")) ? " · uses custom fields" : ""}</span> },
    { key: "active", label: "In use", text: (r) => (r.active ? "yes" : "no"), render: (r) => (r.active ? "Yes" : "No") },
    { key: "updated_by", label: "Changed by", render: (r) => <span className="text-xs text-gray-500">{r.updated_by || ""} · {fmtDate(r.updated_at)}</span> },
  ];
  return (
    <div>
      <PageHeader title="Letter templates" icon={FileSignature}
        subtitle="Write each letter once with merge fields; preview it on a real employee; publish when ready. Issued letters keep the version they were made from."
        actions={<div className="flex gap-2">
          <Link to="/letters" className="btn-secondary text-xs">Issued letters & letterhead</Link>
          {canSetup && <button className="btn-primary flex items-center gap-1 text-xs" onClick={() => setOpen({ key: "", name: "", body: "# Title\n\nDear {{employee.full_name}},\n\n", country: "", language: "en", active: true, isNew: true })}><Plus className="w-4 h-4" /> New template</button>}
        </div>} />
      {isLoading ? <div className="grid place-items-center py-16"><Spinner /></div> : (
        <DataGrid columns={cols} rows={templates} rowKey={(r: any) => r.id} exportName="letter-templates" minWidth={1000} density="compact" onRowClick={(r) => setOpen(r)} empty="No templates yet." />
      )}
      {open && <Editor tpl={open} canSetup={canSetup} onClose={() => setOpen(null)} onChanged={() => qc.invalidateQueries({ queryKey: ["letter-templates"] })} />}
    </div>
  );
}

function Editor({ tpl, canSetup, onClose, onChanged }: { tpl: any; canSetup: boolean; onClose: () => void; onChanged: () => void }) {
  const employees = useEmployees();
  const [tab, setTab] = useState<"edit" | "versions">("edit");
  const [name, setName] = useState(tpl.name || "");
  const [body, setBody] = useState(tpl.body || "");
  const [country, setCountry] = useState(tpl.country || "");
  const [active, setActive] = useState(tpl.active !== false);
  const [sample, setSample] = useState("");
  const [preview, setPreview] = useState<PreviewText | null>(null);
  const [dirty, setDirty] = useState(false);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const { data: cat } = useQuery({ queryKey: ["letter-fields"], queryFn: () => lettersApi.fields() });
  const versions = useQuery({ queryKey: ["letter-versions", tpl.id], queryFn: () => letterTemplatesApi.versions(tpl.id), enabled: !tpl.isNew });
  const fields: MergeField[] = useMemo(() => cat?.fields || [], [cat]);
  const draft = versions.data?.versions.find((x) => x.state === "draft");
  useEffect(() => { if (draft && !dirty && body === tpl.body) { setBody(draft.body); setName(draft.name); setNote(draft.note || ""); } }, [draft]);   // eslint-disable-line react-hooks/exhaustive-deps
  const setB = (b: string) => { setBody(b); setDirty(true); };
  const errs = useMemo(() => collect({
    name: v.required(name, "Template name") || (name.trim().length < 2 ? "Template name needs at least 2 characters" : null) || v.maxLen(name.trim(), 160, "Template name"),
    body: v.required(body, "Letter text"),
    note: v.maxLen(note, 300, "Change note"),
  }), [name, body, note]);
  const shown = (k: string) => (tried || k === "note" ? errs[k] : null);
  // Block a save when the form is incomplete; block publish / create while the preview reports merge-field errors.
  const guard = (strict: boolean) => {
    setTried(true);
    if (Object.keys(errs).length) { toast.error(firstError(errs)); return false; }
    if (strict && preview && !preview.ok) { toast.error(`Fix the letter text first: ${preview.errors[0] || "the preview shows a problem"}`); return false; }
    return true;
  };

  // live preview, debounced
  useEffect(() => {
    if (tpl.isNew && !body.trim()) return;
    const h = setTimeout(async () => {
      try { setPreview(await letterTemplatesApi.previewText({ body, employee_id: sample || undefined })); }
      catch (e) { setPreview({ ok: false, errors: [errDetail(e)], blocks: [], text: "", missing: [], employee: "" }); }
    }, 500);
    return () => clearTimeout(h);
  }, [body, sample, tpl.isNew]);

  const done = (msg: string) => () => { toast.success(msg); setDirty(false); onChanged(); versions.refetch(); };
  const fail = (e: any) => toast.error(errDetail(e), { duration: 8000 });
  const saveDraft = useMutation({ mutationFn: () => letterTemplatesApi.saveDraft(tpl.id, { body, name: name.trim(), note: note.trim() }), onSuccess: done("Draft saved — letters still use the published text"), onError: fail });
  const publish = useMutation({ mutationFn: async () => { await letterTemplatesApi.saveDraft(tpl.id, { body, name: name.trim(), note: note.trim() }); return letterTemplatesApi.publish(tpl.id); }, onSuccess: (r: any) => { setNote(""); done(`Published as version ${r.version}`)(); }, onError: fail });
  const create = useMutation({ mutationFn: () => lettersApi.createTemplate({ name: name.trim(), body, country, language: "en", active }), onSuccess: () => { toast.success("Template created"); onChanged(); onClose(); }, onError: fail });
  const saveMeta = useMutation({ mutationFn: () => lettersApi.saveTemplate(tpl.id, { name: name.trim(), body: tpl.body, country, language: tpl.language || "en", active }), onSuccess: done("Settings saved"), onError: fail });
  const reset = useMutation({ mutationFn: () => lettersApi.resetTemplate(tpl.id), onSuccess: (r: any) => { setBody(r.body); setName(r.name); done("Default text restored")(); }, onError: fail });
  const rollback = useMutation({ mutationFn: (v: number) => letterTemplatesApi.rollback(tpl.id, v), onSuccess: (r: any) => { setBody(r.body); setName(r.name); done(`Version ${r.version} published (rolled back)`)(); setTab("edit"); }, onError: fail });
  const pdf = useMutation({ mutationFn: () => lettersApi.preview({ employee_id: sample || preview?.employee_id || "", body }), onSuccess: (b) => openPdf(b, "preview.pdf"), onError: async (e) => toast.error(await blobErrText(e), { duration: 8000 }) });

  return (
    <AppModal size="screen" onClose={onClose} closeOnBackdrop={!dirty}
      title={tpl.isNew ? "New letter template" : `Template · ${tpl.name}`}
      subtitle={tpl.isNew ? "Saved as version 1 when created." : `Published version ${versions.data?.published_version ?? tpl.version}${draft ? " · a draft is open" : ""} · the preview on the right updates as you type`}
      actions={!tpl.isNew && <div className="flex gap-1 mr-2">{([["edit", "Editor"], ["versions", `Versions (${(versions.data?.versions || []).filter((x) => x.state === "published").length})`]] as const).map(([k, l]) => (
        <button key={k} onClick={() => setTab(k)} className={`px-3 py-1 rounded-lg text-xs font-medium ${tab === k ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>{l}</button>))}</div>}
      bodyClassName="!p-0 flex flex-col lg:!overflow-hidden"
      footer={<>
        {!tpl.isNew && canSetup && tpl.key && <button className="btn-secondary text-xs inline-flex items-center gap-1 mr-auto" onClick={() => { if (window.confirm("Replace the published text with the default text?")) reset.mutate(); }}><RotateCcw className="w-3.5 h-3.5" /> Restore default text</button>}
        <button className="btn-secondary text-xs" onClick={onClose}>Close</button>
        {tpl.isNew ? <button className="btn-primary text-xs" disabled={create.isPending} onClick={() => { if (guard(true)) create.mutate(); }}>{create.isPending ? "Creating…" : "Create template"}</button> : canSetup && <>
          <button className="btn-secondary text-xs" disabled={saveMeta.isPending} onClick={() => { setTried(true); if (errs.name) { toast.error(errs.name); return; } saveMeta.mutate(); }} title="Name, in-use flag — without touching the text">Save settings</button>
          <button className="btn-secondary text-xs inline-flex items-center gap-1" disabled={saveDraft.isPending} onClick={() => { if (guard(false)) saveDraft.mutate(); }}><Save className="w-3.5 h-3.5" /> Save draft</button>
          <button className="btn-primary text-xs inline-flex items-center gap-1" disabled={publish.isPending} onClick={() => { if (guard(true) && window.confirm("Publish this text? New letters will use it from now on.")) publish.mutate(); }}><Send className="w-3.5 h-3.5" /> Publish</button>
        </>}
      </>}>
        {tab === "edit" ? (
          <div className="lg:flex-1 lg:min-h-0 grid grid-cols-1 lg:grid-cols-[1.15fr_1fr] gap-0">
            <div className="flex flex-col lg:min-h-0 px-4 py-3 lg:border-r border-surface-border lg:overflow-y-auto">
              <FormSection title="Template settings" className="!mb-2">
                <FormGrid cols={4}>
                  <div className="sm:col-span-2"><label className={LABEL}>Name<Req /></label><input className="input !py-1.5 text-sm" maxLength={160} value={name} disabled={!canSetup} onChange={(e) => { setName(e.target.value); setDirty(true); }} placeholder="e.g. Appointment letter — staff" />
                    <FieldError msg={shown("name")} />
                    {!tpl.isNew && tpl.key && <p className="text-[11px] text-gray-500 mt-0.5">Key <code className="font-mono">{tpl.key}</code> — used by the screens that issue this letter.</p>}</div>
                  <div><label className={LABEL}>Country</label><select className="input !py-1.5 text-sm" disabled={!tpl.isNew} value={country} onChange={(e) => setCountry(e.target.value)}><option value="">Any</option><option value="CA">Canada</option></select>
                    <p className="text-[11px] text-gray-500 mt-0.5">Fixed once created.</p></div>
                  <div><label className={LABEL}>In use</label><select className="input !py-1.5 text-sm" disabled={!canSetup} value={active ? "1" : "0"} onChange={(e) => setActive(e.target.value === "1")}><option value="1">Yes</option><option value="0">No</option></select>
                    <p className="text-[11px] text-gray-500 mt-0.5">No hides it from issuing; text and history are kept.</p></div>
                  {!tpl.isNew && canSetup && <div className="sm:col-span-2 lg:col-span-4"><label className={LABEL}>Change note</label><input className="input !py-1.5 text-sm" maxLength={300} value={note} onChange={(e) => { setNote(e.target.value); setDirty(true); }} placeholder="e.g. Updated the probation clause" />
                    <FieldError msg={shown("note")} />
                    <p className="text-[11px] text-gray-500 mt-0.5">Saved with the draft and shown in the Versions list when you publish, so others know what changed.</p></div>}
                </FormGrid>
              </FormSection>
              <div className="flex-1 min-h-0"><LetterRichEditor value={body} onChange={setB} fields={fields} disabled={!canSetup} minHeight="48vh" /></div>
              <FieldError msg={shown("body")} />
            </div>
            <div className="flex flex-col min-h-0 px-4 py-3 bg-surface/40 overflow-y-auto">
              <div className="flex flex-wrap items-center gap-2 mb-2">
                <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Live preview</span>
                <select className="input !py-1 !w-60 text-xs" value={sample} onChange={(e) => setSample(e.target.value)}><option value="">Sample: {preview?.employee || "first employee"}</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</select>
                <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 ml-auto" disabled={pdf.isPending || !preview?.ok} onClick={() => pdf.mutate()}><Eye className="w-3.5 h-3.5" /> PDF</button>
              </div>
              {!preview ? <div className="text-sm text-gray-400">Type to see the letter.</div> : !preview.ok ? (
                <div className="rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm px-3 py-2">{preview.errors.map((e, i) => <div key={i}>{e}</div>)}</div>
              ) : (
                <div className="bg-white border border-surface-border rounded-lg shadow-sm p-6 text-[13px] text-gray-800 leading-relaxed">
                  <div className="flex justify-between items-start border-b border-surface-border pb-2 mb-3">
                    <div className="text-right ml-auto"><div className="font-bold text-sm">{preview.letterhead?.company_name}</div><div className="text-[11px] text-gray-500 whitespace-pre-line">{preview.letterhead?.address}</div></div>
                  </div>
                  <div className="flex justify-between text-[11px] text-gray-500 mb-3"><span>Ref: LTR/…/…</span><span>{preview.date}</span></div>
                  <PreviewBlocks blocks={preview.blocks} />
                  <div className="mt-8"><div className="text-[11px] text-gray-400 mb-6">(signature)</div><div className="font-semibold">{preview.letterhead?.signatory_name}</div><div className="text-xs">{preview.letterhead?.signatory_designation}</div><div className="text-xs">{preview.letterhead?.company_name}</div></div>
                </div>)}
              {preview?.ok && preview.missing.length > 0 && <p className="text-[11px] text-amber-700 mt-2">Empty for {preview.employee}: {preview.missing.map((m) => m.label).join(", ")} — shown as [field]. The letter can still be issued with “allow blank”.</p>}
              <p className="text-[11px] text-gray-400 mt-2">Amounts print in Canadian dollars ($1,200,000.00); dates as “16 October 2026”.</p>
            </div>
          </div>
        ) : (
          <div className="lg:flex-1 lg:min-h-0 lg:overflow-y-auto p-4">
            <VersionsTable versions={versions.data?.versions || []} canSetup={canSetup} busy={rollback.isPending} onRollback={(ver) => { if (window.confirm(`Publish the text of version ${ver} again as a new version?`)) rollback.mutate(ver); }} onLoad={(ver) => { setBody(ver.body); setName(ver.name); setDirty(true); setTab("edit"); toast.success(`Version ${ver.version} loaded into the editor (not published)`); }} />
          </div>
        )}
    </AppModal>
  );
}

function PreviewBlocks({ blocks }: { blocks: PreviewText["blocks"] }) {
  const runs = (rs?: { text: string; bold: boolean }[]) => (rs || []).map((r, i) => (r.bold ? <b key={i}>{r.text}</b> : <span key={i}>{r.text}</span>));
  return (
    <div>
      {blocks.map((b, i) => {
        if (b.t === "h1") return <h1 key={i} className="text-base font-bold mb-2">{runs(b.runs)}</h1>;
        if (b.t === "h2") return <h2 key={i} className="text-sm font-bold mb-1.5 mt-2">{runs(b.runs)}</h2>;
        if (b.t === "hr") return <hr key={i} className="my-3 border-surface-border" />;
        if (b.t === "li") return <div key={i} className="pl-4 flex gap-2"><span className="text-gray-500">{b.mark}</span><span>{runs(b.runs)}</span></div>;
        if (b.t === "table") return (
          <table key={i} className="w-full text-xs border-collapse my-2">
            <tbody>{(b.rows || []).map((row, ri) => <tr key={ri} className={ri === 0 ? "bg-surface font-semibold" : ""}>{row.map((cell, ci) => <td key={ci} className={`border border-surface-border px-2 py-1 ${b.money && ci > 0 ? "text-right tabular-nums" : ""}`}>{runs(cell)}</td>)}</tr>)}</tbody>
          </table>);
        return <p key={i} className="mb-2">{runs(b.runs)}</p>;
      })}
    </div>
  );
}

function VersionsTable({ versions, canSetup, busy, onRollback, onLoad }: { versions: TemplateVersion[]; canSetup: boolean; busy: boolean; onRollback: (v: number) => void; onLoad: (v: TemplateVersion) => void }) {
  const [show, setShow] = useState<TemplateVersion | null>(null);
  const rows = versions.filter((v) => v.state === "published");
  const draft = versions.find((v) => v.state === "draft");
  return (
    <div className="space-y-3">
      {draft && <div className="rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-sm px-3 py-2 flex items-center gap-2"><History className="w-4 h-4" /> An unpublished draft by {draft.saved_by} ({fmtDate(draft.saved_at)}) is open in the editor. Letters do not use it until you publish.</div>}
      <DataGrid rows={rows} rowKey={(v: TemplateVersion) => String(v.version)} minWidth={800} density="compact" exportName="template-versions" empty="No published versions yet."
        initialSort={{ key: "version", dir: "desc" }}
        columns={[
          { key: "version", label: "Version", type: "number", width: "90px", render: (v: TemplateVersion) => <span className="font-semibold">v{v.version}{v.current && <span className="badge-green ml-1.5">in use</span>}</span> },
          { key: "name", label: "Name" }, { key: "note", label: "Note" },
          { key: "saved_by", label: "Published by" },
          { key: "saved_at", label: "On", render: (v: TemplateVersion) => fmtDate(v.saved_at), text: (v: TemplateVersion) => v.saved_at || "" },
          { key: "body", label: "Text", text: (v: TemplateVersion) => v.body, render: (v: TemplateVersion) => <button className="text-primary-600 text-xs underline" onClick={(e) => { e.stopPropagation(); setShow(v); }}>{v.body.length} chars · view</button> },
          { key: "actions", label: "", sortable: false, text: () => "", render: (v: TemplateVersion) => (
            <div className="flex gap-1 justify-end">
              <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={(e) => { e.stopPropagation(); onLoad(v); }}>Load into editor</button>
              {canSetup && !v.current && <button className="btn-primary !py-0.5 !px-2 text-[11px]" disabled={busy} onClick={(e) => { e.stopPropagation(); onRollback(v.version); }}>Roll back to this</button>}
            </div>) },
        ]} />
      {show && (
        <div className="card">
          <div className="flex items-center justify-between mb-2"><b className="text-sm">Version {show.version} — {show.name}</b><button className="icon-btn" onClick={() => setShow(null)}><X className="w-4 h-4" /></button></div>
          <pre className="text-xs whitespace-pre-wrap font-mono bg-surface rounded-lg p-3 max-h-80 overflow-auto">{show.body}</pre>
        </div>)}
    </div>
  );
}
