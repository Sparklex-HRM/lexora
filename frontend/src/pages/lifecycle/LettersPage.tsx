import { useMemo, useRef, useState, type ChangeEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { FileSignature, Plus, Eye, X, Printer } from "lucide-react";
import { printPdfBlob } from "../../lib/print";
import { PageHeader } from "../../components/ui";
import { useAuthStore } from "../../store/authStore";
import { lettersApi, revisionsApi, openPdf, errText, blobErrText, fmtDate } from "../../api/lifecycle";
import { Grid, Modal, Field, Input, Select, Textarea, Status, Stat, useEmployees, empLabel } from "../common/_shared";
import { useUrlTab } from "../../search/useUrlTab";

/* The letter engine: issue letters from org templates, keep an unchangeable issued
   copy (numbered per year), re-issue with a new number, void with a reason. */

type Tab = "issued" | "templates" | "letterhead";
const AUTO = ["employee.", "org.", "letter.", "salary.annual_ctc", "salary.monthly_gross", "salary.currency", "salary.hourly_rate", "salary.breakup_table", "salary.increase_"];
const isAuto = (f: string) => AUTO.some((p) => f.startsWith(p));
const SETUP = ["org_admin", "hr_admin", "superadmin"];

export async function showLetter(id: string, name?: string) {
  try { openPdf(await lettersApi.pdf(id), name || "letter.pdf"); } catch (e) { toast.error(await blobErrText(e)); }
}

/** Print the issued letter PDF (same file as Open). */
export async function printLetter(id: string, name?: string) {
  try { printPdfBlob(await lettersApi.pdf(id), name || "letter.pdf"); } catch (e) { toast.error(await blobErrText(e)); }
}

export default function LettersPage() {
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role) || "";
  const canSetup = SETUP.includes(role);
  /* Deep links: /letters?employee=<id> (profile button) or /letters?employees=a,b,c (Employees list bulk bar).
     The grid is narrowed to those people until the chip is closed; the New letter form opens with the
     first person picked — a letter is issued to one person at a time. */
  const [params] = useSearchParams();
  const linkedIds = useMemo(() => [params.get("employee") || "", ...(params.get("employees") || "").split(",")].map((s) => s.trim()).filter(Boolean), [params]);
  const [onlyIds, setOnlyIds] = useState<string[]>(linkedIds);
  const employees = useEmployees();
  const onlyEmps = onlyIds.map((id) => employees.find((e: any) => e.id === id)).filter(Boolean);
  const [tab, setTab] = useUrlTab<Tab>("issued");   // ?tab= (global search deep link)
  const [status, setStatus] = useState("");
  const [issue, setIssue] = useState(linkedIds.length > 0);
  const [edit, setEdit] = useState<any | null>(null);
  const { data: rows = [], isLoading } = useQuery({ queryKey: ["letters"], queryFn: () => lettersApi.all() });
  const { data: templates = [] } = useQuery({ queryKey: ["letter-templates"], queryFn: () => lettersApi.templates() });
  const refresh = () => qc.invalidateQueries({ queryKey: ["letters"] });
  const act = useMutation({
    mutationFn: async ({ a, r }: { a: string; r: any }): Promise<any> => {
      if (a === "void") { const reason = window.prompt("Reason for voiding this letter"); if (!reason) return null; return lettersApi.void(r.id, reason); }
      if (a === "reissue") return lettersApi.reissue(r.id);
      if (a === "issue") return lettersApi.issueDraft(r.id);
      if (a === "discard") return lettersApi.discard(r.id);
      return null;
    },
    onSuccess: (res: any, v) => { if (!res) return; toast.success(v.a === "void" ? "Letter voided. The copy is kept." : v.a === "reissue" ? `Issued again as ${res.number}` : v.a === "issue" ? `Issued as ${res.number}` : "Draft deleted"); refresh(); },
    onError: (e) => toast.error(errText(e)) });
  const shown = rows.filter((r: any) => (!status || r.status === status) && (!onlyIds.length || onlyIds.includes(r.employee_id)));
  return (
    <div>
      <PageHeader title="Letters" icon={FileSignature} subtitle="Offer, appointment, confirmation, revision, promotion, transfer, relieving and other letters — on your letterhead."
        actions={<button className="btn-primary flex items-center gap-1" onClick={() => setIssue(true)}><Plus className="w-4 h-4" /> New letter</button>} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Stat label="Issued" value={rows.filter((r: any) => r.status === "issued").length} accent />
        <Stat label="Drafts" value={rows.filter((r: any) => r.status === "draft").length} />
        <Stat label="Void" value={rows.filter((r: any) => r.status === "void").length} />
        <Stat label="Templates" value={templates.length} />
      </div>
      <div className="flex gap-1 mb-3 border-b border-surface-border">
        {([["issued", "Letters"], ["templates", "Templates"], ["letterhead", "Letterhead and signatory"]] as [Tab, string][]).map(([k, l]) => (
          <button key={k} onClick={() => setTab(k)} className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px ${tab === k ? "border-primary-500 text-primary-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}>{l}</button>))}
      </div>
      {tab === "issued" && <Grid rows={shown} keyOf={(r: any) => r.id} loading={isLoading} exportName="letters.csv" empty={onlyIds.length ? "No letters for the selected people yet. Click New letter." : "No letters yet. Click New letter."}
        filters={<>
          <Select className="!w-36 !py-1 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All</option><option value="issued">Issued</option><option value="draft">Draft</option><option value="void">Void</option></Select>
          {onlyIds.length > 0 && <span className="badge-purple flex items-center gap-1" title="Opened from the employee profile or list. Click × to see everyone.">
            Only {onlyIds.length === 1 ? (onlyEmps[0] ? empLabel(onlyEmps[0]) : "this employee") : `${onlyIds.length} people selected from the list`}
            <button className="hover:text-red-600" aria-label="Show all employees" onClick={() => setOnlyIds([])}><X className="w-3 h-3" /></button></span>}
        </>}
        cols={[
          { k: "number", label: "Letter no", render: (r: any) => <span className="font-mono text-[11px]">{r.number || "draft"}</span> },
          { k: "letter_date", label: "Date", render: (r: any) => fmtDate(r.letter_date) },
          { k: "employee", label: "Employee", render: (r: any) => <span className="font-medium text-gray-800">{r.employee}<span className="text-gray-400 ml-1">{r.employee_number}</span></span> },
          { k: "name", label: "Letter" },
          { k: "effective_date", label: "Effective", render: (r: any) => fmtDate(r.effective_date) },
          { k: "status", label: "Status", render: (r: any) => <span title={r.void_reason || ""}><Status s={r.status} /></span> },
          { k: "issued_by", label: "Issued by" },
          { k: "template_version", label: "Template version", align: "center" },
          { k: "void_reason", label: "Void reason" },
          { k: "actions", label: "", csv: () => "", render: (r: any) => <span className="flex gap-1">
            <button className="btn-secondary !py-0.5 !px-2 text-xs flex items-center gap-1" onClick={() => showLetter(r.id, `${r.number || "draft"}.pdf`)}><Eye className="w-3 h-3" /> Open</button>
            <button className="btn-secondary !py-0.5 !px-2 text-xs flex items-center gap-1" title="Print this letter (A4)" onClick={() => printLetter(r.id, `${r.number || "draft"}.pdf`)}><Printer className="w-3 h-3" /> Print</button>
            {r.status === "draft" && <><button className="btn-primary !py-0.5 !px-2 text-xs" onClick={() => act.mutate({ a: "issue", r })}>Issue</button><button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => { if (window.confirm("Delete this draft?")) act.mutate({ a: "discard", r }); }}>Delete</button></>}
            {r.status === "issued" && <><button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => { if (window.confirm("Issue this letter again with a new number?")) act.mutate({ a: "reissue", r }); }}>Issue again</button><button className="btn-secondary !py-0.5 !px-2 text-xs text-red-600" onClick={() => act.mutate({ a: "void", r })}>Void</button></>}
          </span> },
        ]} />}
      {tab === "templates" && <Grid rows={templates} keyOf={(r: any) => r.id} exportName="letter_templates.csv" onRow={canSetup ? setEdit : undefined}
        actions={<span className="flex gap-1">
          <Link to="/settings/letter-templates" className="btn-primary !py-1 !px-2 text-xs">Rich-text editor, preview & versions</Link>
          {canSetup && <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => setEdit({ key: "", name: "", body: "# Title\n\nDear {{employee.full_name}},\n\n", country: "", language: "en", active: true, isNew: true })}>New template</button>}</span>}
        cols={[
          { k: "name", label: "Template", render: (r: any) => <span className="font-medium text-gray-800">{r.name}</span> },
          { k: "key", label: "Key", render: (r: any) => <span className="font-mono text-[11px]">{r.key}</span> },
          { k: "country", label: "Country", render: (r: any) => r.country || "Any" }, { k: "language", label: "Language" },
          { k: "version", label: "Version", align: "center" },
          { k: "is_default", label: "Text", csv: (r: any) => (r.is_default ? "default" : "edited"), render: (r: any) => r.is_default ? <span className="badge-purple">Default</span> : <span className="badge-teal">Edited</span> },
          { k: "active", label: "In use", align: "center", csv: (r: any) => (r.active ? "yes" : "no"), render: (r: any) => (r.active ? "Yes" : "No") },
          { k: "updated_by", label: "Changed by" }, { k: "updated_at", label: "Changed", render: (r: any) => fmtDate(r.updated_at) },
        ]} />}
      {tab === "letterhead" && <Letterhead canEdit={canSetup} />}
      {issue && <IssueModal templates={templates.filter((t: any) => t.active)} initialEmployeeId={linkedIds[0] || ""} pickedCount={linkedIds.length} onClose={() => setIssue(false)} onDone={() => { setIssue(false); refresh(); }} />}
      {edit && <TemplateEditor tpl={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); qc.invalidateQueries({ queryKey: ["letter-templates"] }); }} />}
    </div>
  );
}

function IssueModal({ templates, initialEmployeeId = "", pickedCount = 0, onClose, onDone }: { templates: any[]; initialEmployeeId?: string; pickedCount?: number; onClose: () => void; onDone: () => void }) {
  const employees = useEmployees();
  const [employeeId, setEmployeeId] = useState(initialEmployeeId);
  const [key, setKey] = useState(templates[0]?.key || "");
  const [eff, setEff] = useState("");
  const [vals, setVals] = useState<Record<string, string>>({});
  const tpl = templates.find((t: any) => t.key === key);
  const { data: cat } = useQuery({ queryKey: ["letter-fields"], queryFn: () => lettersApi.fields() });
  const label = (f: string) => (cat?.fields || []).find((x: any) => x.field === f)?.label || f;
  const manual: string[] = (tpl?.fields || []).filter((f: string) => !isAuto(f));
  const needsBreakup = (tpl?.fields || []).includes("salary.breakup_table");
  const buildData = async () => {
    const data: any = Object.fromEntries(Object.entries(vals).filter(([k, v]) => manual.includes(k) && v !== ""));
    if (needsBreakup && employeeId) {
      try { const b = await revisionsApi.breakup(employeeId); if (Object.keys(b.components || {}).length) { data.salary_components = b.components; data.component_names = b.component_names; } } catch { /* the letter says what is missing */ }
    }
    return data;
  };
  const preview = useMutation({ mutationFn: async () => lettersApi.preview({ employee_id: employeeId, template_key: key, data: await buildData(), effective_date: eff || undefined }),
    onSuccess: (b) => openPdf(b, "preview.pdf"), onError: async (e) => toast.error(await blobErrText(e)) });
  const save = useMutation({ mutationFn: async (issue: boolean) => lettersApi.create(employeeId, { template_key: key, data: await buildData(), effective_date: eff || undefined, issue }),
    onSuccess: async (r: any) => { toast.success(r.number ? `Issued as ${r.number}` : "Saved as draft"); if (r.number) await showLetter(r.letter_id, `${r.number}.pdf`); onDone(); },
    onError: (e) => toast.error(errText(e)) });
  const ok = !!employeeId && !!key;
  return (
    <Modal title="New letter" size="xl" onClose={onClose} subtitle="Employee and company details are filled in for you. Type only the details below. Preview before you issue."
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button>
        <button className="btn-secondary" disabled={!ok || preview.isPending} onClick={() => preview.mutate()}>Preview</button>
        <button className="btn-secondary" disabled={!ok || save.isPending} onClick={() => save.mutate(false)}>Save draft</button>
        <button className="btn-primary" disabled={!ok || save.isPending} onClick={() => save.mutate(true)}>Issue letter</button></>}>
      {pickedCount > 1 && <div className="text-xs rounded-lg px-3 py-2 bg-amber-50 text-amber-800 border border-amber-200 mb-3">{pickedCount} people selected from the list. A letter is issued to one person at a time — the first one is picked below; click New letter again for the next person.</div>}
      <div className="grid sm:grid-cols-4 gap-3 mb-4">
        <Field label="Employee" span={2}><Select value={employeeId} onChange={(e) => setEmployeeId(e.target.value)}><option value="">— pick an employee —</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select></Field>
        <Field label="Letter"><Select value={key} onChange={(e) => { setKey(e.target.value); setVals({}); }}>{templates.map((t: any) => <option key={t.id} value={t.key}>{t.name}</option>)}</Select></Field>
        <Field label="Effective date" hint="Optional"><Input type="date" value={eff} onChange={(e) => setEff(e.target.value)} /></Field>
      </div>
      {manual.length > 0 ? <>
        <div className="text-xs font-semibold text-gray-600 mb-2">Details for this letter</div>
        <div className="grid sm:grid-cols-3 gap-3">{manual.map((f) => (
          <Field key={f} label={label(f)}><Input type={/date|_from|_until/.test(f) ? "date" : /amount|annual/.test(f) ? "number" : "text"} value={vals[f] || ""} onChange={(e) => setVals({ ...vals, [f]: e.target.value })} /></Field>))}</div>
      </> : <p className="text-sm text-gray-500">This letter needs no extra details.</p>}
      <p className="text-xs text-gray-500 mt-4">An issued letter gets the next number and cannot be changed. To correct it, void it and issue a new one.</p>
    </Modal>
  );
}

function TemplateEditor({ tpl, onClose, onDone }: { tpl: any; onClose: () => void; onDone: () => void }) {
  const employees = useEmployees();
  const ref = useRef<HTMLTextAreaElement>(null);
  const [f, setF] = useState<any>({ key: tpl.key, name: tpl.name, body: tpl.body, country: tpl.country || "", language: tpl.language || "en", active: tpl.active !== false });
  const [sample, setSample] = useState("");
  const { data: cat } = useQuery({ queryKey: ["letter-fields"], queryFn: () => lettersApi.fields() });
  const groups = useMemo(() => { const g: Record<string, any[]> = {}; (cat?.fields || []).forEach((x: any) => (g[x.group] = g[x.group] || []).push(x)); return g; }, [cat]);
  const insert = (field: string) => {
    const el = ref.current; const tag = `{{${field}}}`;
    if (!el) { setF({ ...f, body: f.body + tag }); return; }
    const s = el.selectionStart, e = el.selectionEnd;
    setF({ ...f, body: f.body.slice(0, s) + tag + f.body.slice(e) });
    setTimeout(() => { el.focus(); el.setSelectionRange(s + tag.length, s + tag.length); }, 0);
  };
  const save = useMutation({ mutationFn: () => (tpl.isNew ? lettersApi.createTemplate(f) : lettersApi.saveTemplate(tpl.id, f)), onSuccess: () => { toast.success("Template saved"); onDone(); }, onError: (e) => toast.error(errText(e), { duration: 8000 }) });
  const reset = useMutation({ mutationFn: () => lettersApi.resetTemplate(tpl.id), onSuccess: () => { toast.success("Default text restored"); onDone(); }, onError: (e) => toast.error(errText(e)) });
  const preview = useMutation({ mutationFn: () => lettersApi.preview({ employee_id: sample, body: f.body }), onSuccess: (b) => openPdf(b, "preview.pdf"), onError: async (e) => toast.error(await blobErrText(e), { duration: 8000 }) });
  return (
    <Modal title={tpl.isNew ? "New letter template" : `Template · ${tpl.name}`} size="screen" onClose={onClose}
      subtitle="Write the letter in plain text. Click a field on the right to insert it. Letters already issued are not changed."
      footer={<>
        {!tpl.isNew && tpl.key && <button className="btn-secondary" onClick={() => { if (window.confirm("Replace this text with the default text?")) reset.mutate(); }}>Restore default text</button>}
        <Select className="!w-56" value={sample} onChange={(e) => setSample(e.target.value)}><option value="">Preview with employee…</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select>
        <button className="btn-secondary" disabled={!sample || preview.isPending} onClick={() => preview.mutate()}>Preview</button>
        <button className="btn-secondary" onClick={onClose}>Close</button>
        <button className="btn-primary" disabled={!f.name.trim() || save.isPending} onClick={() => save.mutate()}>Save template</button></>}>
      <div className="grid lg:grid-cols-4 gap-4 h-full">
        <div className="lg:col-span-3 flex flex-col">
          <div className="grid sm:grid-cols-4 gap-3 mb-3">
            <Field label="Name" span={2}><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label="Country"><Select disabled={!tpl.isNew} value={f.country} onChange={(e) => setF({ ...f, country: e.target.value })}><option value="">Any</option><option value="CA">Canada</option></Select></Field>
            <Field label="In use"><Select value={f.active ? "1" : "0"} onChange={(e) => setF({ ...f, active: e.target.value === "1" })}><option value="1">Yes</option><option value="0">No</option></Select></Field>
          </div>
          <textarea ref={ref} className="input font-mono text-xs flex-1 min-h-[52vh] w-full" value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} spellCheck />
          <p className="text-[11px] text-gray-500 mt-1"># Heading · ## Sub-heading · **bold** · - bullet · 1. numbered · | table | row | · --- line · blank line = new paragraph. No HTML, no code.</p>
        </div>
        <div className="overflow-y-auto max-h-[68vh] pr-1">
          <div className="text-xs font-semibold text-gray-600 mb-1">Fields you can use</div>
          {Object.entries(groups).map(([g, list]) => (
            <div key={g} className="mb-2"><div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{g}</div>
              {list.map((x: any) => <button key={x.field} onClick={() => insert(x.field)} title={`{{${x.field}}}`} className="block w-full text-left text-xs px-2 py-1 rounded hover:bg-primary-50 text-gray-700">{x.label}</button>)}</div>))}
        </div>
      </div>
    </Modal>
  );
}

function Letterhead({ canEdit }: { canEdit: boolean }) {
  const qc = useQueryClient();
  const { data: s } = useQuery({ queryKey: ["letter-settings"], queryFn: () => lettersApi.settings() });
  const [f, setF] = useState<any | null>(null);
  const v = f || s || {};
  const set = (k: string, val: any) => setF({ ...v, [k]: val });
  const pick = (k: string) => (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]; if (!file) return;
    if (file.size > 400 * 1024) { toast.error("The image is too large (max 400 KB)"); return; }
    const rd = new FileReader(); rd.onload = () => set(k, rd.result); rd.readAsDataURL(file);
  };
  const save = useMutation({
    mutationFn: () => lettersApi.saveSettings({ company_name: v.company_name || "", address: v.address || "", footer_text: v.footer_text || "", signatory_name: v.signatory_name || "",
      signatory_designation: v.signatory_designation || "", number_prefix: v.number_prefix || "LTR",
      ...(f && "logo" in f && f.logo !== s?.logo ? { logo: f.logo || "" } : {}), ...(f && "signature" in f && f.signature !== s?.signature ? { signature: f.signature || "" } : {}) }),
    onSuccess: () => { toast.success("Letterhead saved"); setF(null); qc.invalidateQueries({ queryKey: ["letter-settings"] }); }, onError: (e) => toast.error(errText(e)) });
  return (
    <div className="card max-w-4xl">
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Company name on letters"><Input disabled={!canEdit} value={v.company_name || ""} onChange={(e) => set("company_name", e.target.value)} /></Field>
        <Field label="Letter number prefix" hint={`Next letter looks like ${v.sample_number || "LTR/2026/0001"}`}><Input disabled={!canEdit} value={v.number_prefix || ""} onChange={(e) => set("number_prefix", e.target.value)} /></Field>
        <Field label="Company address" span={2}><Textarea disabled={!canEdit} rows={3} value={v.address || ""} onChange={(e) => set("address", e.target.value)} placeholder={"12 MG Road\nBengaluru 560001"} /></Field>
        <Field label="Signatory name"><Input disabled={!canEdit} value={v.signatory_name || ""} onChange={(e) => set("signatory_name", e.target.value)} /></Field>
        <Field label="Signatory designation"><Input disabled={!canEdit} value={v.signatory_designation || ""} onChange={(e) => set("signatory_designation", e.target.value)} /></Field>
        <Field label="Footer line" span={2}><Input disabled={!canEdit} value={v.footer_text || ""} onChange={(e) => set("footer_text", e.target.value)} placeholder="Registered office address · CIN · website" /></Field>
        {(["logo", "signature"] as const).map((k) => (
          <Field key={k} label={k === "logo" ? "Logo (PNG or JPG)" : "Signature image (optional)"}>
            <div className="flex items-center gap-3">
              {v[k] ? <img src={v[k]} alt={k} className="h-12 border border-surface-border rounded bg-white" /> : <span className="text-xs text-gray-400">None</span>}
              {canEdit && <><input type="file" accept="image/png,image/jpeg" onChange={pick(k)} className="text-xs" />{v[k] && <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => set(k, "")}>Remove</button>}</>}
            </div></Field>))}
      </div>
      <div className="mt-4">{canEdit ? <button className="btn-primary" disabled={!f || save.isPending} onClick={() => save.mutate()}>Save letterhead</button> : <p className="text-xs text-gray-500">Only the HR head or Org Admin can change the letterhead.</p>}</div>
      <p className="text-xs text-gray-500 mt-3">Letters are numbered one after another for each calendar year. The PDF uses Latin letters (French accents included); other scripts print as “?”.</p>
    </div>
  );
}
