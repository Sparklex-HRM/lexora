// D1 — Settings → Approval workflows: a no-code chain per request type.
// Steps (drag or arrow to reorder) · approvers by role / manager level / named person / any-of group ·
// conditions (amount thresholds, department…) · sequential or parallel ("everyone") · escalation · SLA ·
// auto-approve rules · a test-run simulator ("who would approve a $2,500 expense from Sales?").
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import toast from "react-hot-toast";
import { GitBranch, Plus, X, ArrowUp, ArrowDown, GripVertical, Play, RotateCcw, History, Save } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { workflowsApi, errDetail, type ApproverSpec, type Chain, type ChainDefinition, type ChainStep, type Condition, type SimResult, type WorkflowMeta } from "../../api/config";
import { useAuthStore } from "../../store/authStore";
import { when } from "../../api/platform";
import { entitiesApi } from "../../api/entities";
import AppModal, { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const VIEW = ["org_admin", "hr_admin", "hr_manager", "hr_staff", "payroll_admin", "superadmin"];
const LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1";
const newStep = (n: number): ChainStep => ({ id: `s${Date.now().toString(36)}`, name: `Step ${n}`, mode: "any", approvers: [{ type: "manager", level: 1 }], when: [], escalate_after_hours: 0, escalate_to: null, sla_hours: 0 });

export default function ApprovalWorkflowsPage() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const qc = useQueryClient();
  const meta = useQuery({ queryKey: ["wf", "meta"], queryFn: workflowsApi.meta });
  const list = useQuery({ queryKey: ["wf", "list"], queryFn: workflowsApi.list });
  const [key, setKey] = useState<string>(() => { try { return new URLSearchParams(window.location.search).get("type") || ""; } catch { return ""; } });  // G5 deep link
  const [newKey, setNewKey] = useState("");
  const chain = useMemo(() => list.data?.items.find((c) => c.request_type === key) || null, [list.data, key]);
  if (!role) return <Navigate to="/login" replace />;
  if (!VIEW.includes(role)) return <Navigate to="/settings" replace />;
  const canEdit = !!list.data?.can_edit;
  const cols: GridColumn<Chain>[] = [
    { key: "label", label: "Request type", render: (c) => <span className="font-medium text-gray-800">{c.label}<span className="text-gray-400 text-xs ml-1.5 font-mono">{c.request_type}</span></span> },
    { key: "name", label: "Workflow" },
    { key: "steps", label: "Steps", type: "number", width: "70px" },
    { key: "auto_rules", label: "Auto rules", type: "number", width: "90px" },
    { key: "is_default", label: "Text", text: (c) => (c.is_default ? "default" : "customised"), render: (c) => (c.is_default ? <span className="badge-purple">Default</span> : <span className="badge-teal">Customised</span>) },
    { key: "active", label: "Active", text: (c) => (c.active ? "yes" : "no"), render: (c) => (c.active ? "Yes" : <span className="badge-red">Off — default applies</span>) },
    { key: "version", label: "Version", type: "number", width: "80px" },
    { key: "updated_by", label: "Changed by", render: (c) => <span className="text-xs text-gray-500">{c.updated_by || ""}{c.updated_at ? ` · ${when(c.updated_at)}` : ""}</span> },
  ];
  return (
    <div>
      <PageHeader title="Approval workflows" icon={GitBranch}
        subtitle="Who approves what, in which order. Each request type has one chain. The seeded default is one step — whoever the owning screen names — so nothing changes until you edit it." />
      {list.isLoading || meta.isLoading ? <div className="grid place-items-center py-16"><Spinner /></div> : (
        <DataGrid<Chain> columns={cols} rows={list.data?.items || []} rowKey={(c) => c.request_type} exportName="approval-workflows" minWidth={900} density="compact"
          onRowClick={(c) => setKey(c.request_type)} empty="No request types yet."
          toolbar={canEdit && <div className="flex items-center gap-1"><input className="input !py-1 text-xs !w-44" placeholder="new type key, e.g. travel" value={newKey} onChange={(e) => setNewKey(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"))} />
            <button className="btn-secondary !py-1 !px-2 text-xs" disabled={!newKey} onClick={() => { setKey(newKey); setNewKey(""); }}>Add</button></div>} />
      )}
      <p className="text-[11px] text-gray-400 mt-2">Other screens register a request type by calling the approvals registry with a kind and a context (amount, department, employee…). A type that is not listed here still works with the default chain. Every save is audited and keeps a version.</p>
      {key && meta.data && <Editor requestType={key} chain={chain} meta={meta.data} canEdit={canEdit} onClose={() => setKey("")} onSaved={() => { qc.invalidateQueries({ queryKey: ["wf"] }); }} />}
    </div>
  );
}

function specLabel(a: ApproverSpec, meta: WorkflowMeta): string {
  if (a.type === "caller") return "Approver named by the screen";
  if (a.type === "role") return meta.roles.find((r) => r.key === a.role)?.label || a.role;
  if (a.type === "manager") return a.level === 1 ? "Reporting manager" : `Manager level ${a.level}`;
  if (a.type === "user") return meta.people.find((p) => p.id === a.user_id)?.name || "Named person";
  if (a.type === "any_of") return "Any of: " + a.approvers.map((x) => specLabel(x, meta)).join(" / ");
  return "?";
}

// Context fields compared as numbers (the backend falls back to text comparison otherwise, which is never what an
// amount rule means). Values that can never be negative are checked >= 0.
const NUMERIC_FIELDS = new Set(["amount", "change_pct", "leave_days", "total_hours", "ot_hours", "notice_days", "openings", "budget_max", "budget_pct", "units", "hours", "policy_flags", "days_apart"]);
const SIGNED_FIELDS = new Set(["change_pct", "budget_pct"]);
const NUM_OPS = new Set(["gt", "gte", "lt", "lte"]);
const MAX_HOURS = 8760;   // a year — anything longer is a typo

function conditionError(c: Condition, fieldLabel: (k: string) => string): string | null {
  if (!c.field) return "Pick a field";
  if (c.op === "is_empty" || c.op === "not_empty") return null;
  const val = c.value === undefined || c.value === null ? "" : String(c.value).trim();
  if (!val) return `Enter a value for ${fieldLabel(c.field)}`;
  if (NUMERIC_FIELDS.has(c.field) && (NUM_OPS.has(c.op) || c.op === "eq" || c.op === "ne"))
    return v.range(val, SIGNED_FIELDS.has(c.field) ? -1e12 : 0, undefined, fieldLabel(c.field));
  return null;
}

function chainErrors(name: string, desc: string, d: ChainDefinition, fieldLabel: (k: string) => string): Record<string, string> {
  const out = collect({
    name: v.required(name, "Workflow name") || v.maxLen(name.trim(), 160, "Workflow name"),
    desc: v.maxLen(desc, 600, "Description"),
    sla: v.range(d.sla_hours, 0, MAX_HOURS, "Default SLA (hours)"),
  });
  if (!d.steps.length) out.steps = "A chain needs at least one step";
  d.steps.forEach((s, i) => {
    const n = `Step ${i + 1}`;
    const m = collect({
      [`s${i}_name`]: v.required(s.name, `${n} name`) || v.maxLen(s.name, 120, `${n} name`),
      [`s${i}_appr`]: !s.approvers.length ? `${n} needs at least one approver`
        : s.approvers.some((a) => a.type === "user" && !a.user_id) ? `${n}: pick the named person` : null,
      [`s${i}_esch`]: v.range(s.escalate_after_hours, 0, MAX_HOURS, `${n} escalate after (h)`)
        || (s.escalate_to && !Number(s.escalate_after_hours) ? `${n}: set after how many hours to escalate — 0 never escalates` : null),
      [`s${i}_esct`]: !s.escalate_to && Number(s.escalate_after_hours) > 0 ? `${n}: pick who the step escalates to` : null,
      [`s${i}_sla`]: v.range(s.sla_hours, 0, MAX_HOURS, `${n} SLA (h)`),
    });
    Object.assign(out, m);
    s.when.forEach((c, j) => { const e = conditionError(c, fieldLabel); if (e) out[`s${i}_c${j}`] = `${n}: ${e}`; });
  });
  const seen = new Set<string>();
  d.auto_approve.forEach((r, i) => {
    const n = `Auto-approve rule ${i + 1}`;
    const nm = (r.name || "").trim().toLowerCase();
    if (!nm) out[`a${i}_name`] = `${n} needs a name`;
    else if (seen.has(nm)) out[`a${i}_name`] = `${n}: another rule already has this name`;
    else { const m = v.maxLen(r.name, 120, `${n} name`); if (m) out[`a${i}_name`] = m; }
    seen.add(nm);
    if (!r.when.length) out[`a${i}_none`] = `${n} needs at least one condition — a rule with none would approve everything`;
    r.when.forEach((c, j) => { const e = conditionError(c, fieldLabel); if (e) out[`a${i}_c${j}`] = `${n}: ${e}`; });
  });
  return out;
}

function Editor({ requestType, chain, meta, canEdit, onClose, onSaved }: { requestType: string; chain: Chain | null; meta: WorkflowMeta; canEdit: boolean; onClose: () => void; onSaved: () => void }) {
  const fresh = (): ChainDefinition => chain ? JSON.parse(JSON.stringify(chain.definition)) : { steps: [{ ...newStep(1), approvers: [{ type: "caller" }], name: "Approver named by the screen" }], auto_approve: [], sla_hours: 0 };
  const [d, setD] = useState<ChainDefinition>(fresh);
  const [name, setName] = useState(chain?.name || `${meta.request_types.find((r) => r.key === requestType)?.label || requestType}`);
  const [desc, setDesc] = useState(chain?.description || "");
  const [active, setActive] = useState(chain?.active ?? true);
  const [tab, setTab] = useState<"steps" | "auto" | "simulate" | "runs">("steps");
  const [drag, setDrag] = useState<number | null>(null);
  const [tried, setTried] = useState(false);
  useEffect(() => { setD(fresh()); setName(chain?.name || name); setDesc(chain?.description || ""); setActive(chain?.active ?? true); }, [chain?.request_type, chain?.version]);   // eslint-disable-line react-hooks/exhaustive-deps
  const fields = meta.request_types.find((r) => r.key === requestType)?.fields || meta.context_fields.map((f) => f.key);
  const fieldLabel = (k: string) => meta.context_fields.find((f) => f.key === k)?.label || k;
  const errs = useMemo(() => chainErrors(name, desc, d, fieldLabel), [name, desc, d]);   // eslint-disable-line react-hooks/exhaustive-deps
  const shown = (k: string) => (tried || k !== "name" ? errs[k] : null);
  const save = useMutation({ mutationFn: () => workflowsApi.save(requestType, { name: name.trim(), description: desc.trim(), definition: d, active }),
    onSuccess: (c) => { toast.success(`Saved as version ${c.version}`); onSaved(); }, onError: (e) => toast.error(errDetail(e), { duration: 8000 }) });
  const trySave = () => {
    setTried(true);
    const keys = Object.keys(errs);
    if (keys.length) {
      if (keys.every((k) => k.startsWith("a"))) setTab("auto"); else if (tab !== "steps" && keys.some((k) => !k.startsWith("a"))) setTab("steps");
      toast.error(firstError(errs));
      return;
    }
    save.mutate();
  };
  const reset = useMutation({ mutationFn: () => workflowsApi.reset(requestType), onSuccess: () => { toast.success("Back to the default chain"); onSaved(); }, onError: (e) => toast.error(errDetail(e)) });
  const setStep = (i: number, patch: Partial<ChainStep>) => setD((p) => ({ ...p, steps: p.steps.map((s, j) => (j === i ? { ...s, ...patch } : s)) }));
  const move = (i: number, to: number) => setD((p) => { if (to < 0 || to >= p.steps.length) return p; const s = [...p.steps]; const [x] = s.splice(i, 1); s.splice(to, 0, x); return { ...p, steps: s }; });
  const title = `${meta.request_types.find((r) => r.key === requestType)?.label || requestType} — approval chain`;
  const HINT = "text-[11px] text-gray-500 mt-0.5";
  const autoErrCount = Object.keys(errs).filter((k) => k.startsWith("a")).length;
  return (
    <AppModal size="xwide" title={title} onClose={onClose} closeOnBackdrop={!canEdit}
      subtitle={chain ? `Version ${chain.version} · ${chain.is_default ? "default (one step)" : "customised"} · changed by ${chain.updated_by || "—"}` : "New request type — saving creates it"}
      footer={<>
        {canEdit && chain && !chain.is_default && <button className="btn-secondary text-xs inline-flex items-center gap-1 mr-auto" onClick={() => { if (window.confirm("Replace this chain with the one-step default?")) reset.mutate(); }}><RotateCcw className="w-3.5 h-3.5" /> Back to default</button>}
        <button className="btn-secondary" onClick={onClose}>Close</button>
        {canEdit && <button className="btn-primary inline-flex items-center gap-1" disabled={save.isPending} onClick={trySave}><Save className="w-4 h-4" /> {save.isPending ? "Saving…" : "Save workflow"}</button>}
      </>}>
      <FormSection title="Chain settings">
        <FormGrid cols={4}>
          <div className="sm:col-span-2"><label className={LABEL}>Workflow name<Req /></label><input className="input" maxLength={160} value={name} disabled={!canEdit} onChange={(e) => setName(e.target.value)} />
            <FieldError msg={shown("name")} /></div>
          <div><label className={LABEL}>Default SLA (hours)</label><input className="input" type="number" min={0} max={MAX_HOURS} step={1} value={d.sla_hours} disabled={!canEdit} onChange={(e) => setD({ ...d, sla_hours: Math.trunc(Number(e.target.value)) || 0 })} />
            <FieldError msg={errs.sla} />
            <p className={HINT}>Deadline for the whole chain; 0 = none. Steps can set their own.</p></div>
          <div className="flex flex-col justify-end sm:pb-1"><label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={active} disabled={!canEdit} onChange={(e) => setActive(e.target.checked)} /> Active</label>
            <p className={HINT}>Off = the built-in one-step approval applies.</p></div>
          <div className="sm:col-span-2 lg:col-span-4"><label className={LABEL}>Description</label><input className="input" maxLength={600} value={desc} disabled={!canEdit} onChange={(e) => setDesc(e.target.value)} placeholder="What this chain is for, e.g. expense claims above $1,000 need Finance" />
            <FieldError msg={errs.desc} /></div>
        </FormGrid>
      </FormSection>
      <div className="flex gap-1 overflow-x-auto border-b border-surface-border mb-3" role="tablist">
        {([["steps", "Steps"], ["auto", `Auto-approve (${d.auto_approve.length})`], ["simulate", "Test run"], ["runs", "Recent runs"]] as const).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)} className={`px-3 py-1.5 text-sm font-medium whitespace-nowrap border-b-2 -mb-px ${tab === k ? "border-primary-500 text-primary-600" : "border-transparent text-gray-500"}`}>{l}{k === "auto" && tried && autoErrCount > 0 && <span className="ml-1 text-red-600">•</span>}</button>))}
      </div>
      {tab === "steps" && (
        <div className="space-y-3">
          {errs.steps && <FieldError msg={errs.steps} />}
          {d.steps.map((s, i) => (
            <div key={s.id || i} draggable={canEdit} onDragStart={() => setDrag(i)} onDragOver={(e) => e.preventDefault()} onDrop={() => { if (drag !== null && drag !== i) move(drag, i); setDrag(null); }}
              className={`border rounded-xl p-3 bg-white ${drag === i ? "opacity-50" : ""} border-surface-border`}>
              <div className="flex flex-wrap items-center gap-2 mb-2">
                <GripVertical className="w-4 h-4 text-gray-300 cursor-grab" />
                <span className="w-6 h-6 rounded-full bg-primary-600 text-white grid place-items-center text-xs font-bold">{i + 1}</span>
                <input className="input !py-1 w-full sm:!w-64 text-sm font-medium" maxLength={120} aria-label={`Step ${i + 1} name`} value={s.name} disabled={!canEdit} onChange={(e) => setStep(i, { name: e.target.value })} />
                <select className="input !py-1 w-full sm:!w-auto text-xs" value={s.mode} disabled={!canEdit} onChange={(e) => setStep(i, { mode: e.target.value as any })}>
                  <option value="any">Any one approver is enough</option><option value="all">Everyone listed must approve (parallel)</option></select>
                <span className="ml-auto flex gap-1">
                  <button className="icon-btn text-gray-400" title="Move up" disabled={!canEdit || i === 0} onClick={() => move(i, i - 1)}><ArrowUp className="w-4 h-4" /></button>
                  <button className="icon-btn text-gray-400" title="Move down" disabled={!canEdit || i === d.steps.length - 1} onClick={() => move(i, i + 1)}><ArrowDown className="w-4 h-4" /></button>
                  <button className="icon-btn text-gray-400 hover:text-red-600" title="Remove step" disabled={!canEdit || d.steps.length === 1} onClick={() => setD({ ...d, steps: d.steps.filter((_, j) => j !== i) })}><X className="w-4 h-4" /></button>
                </span>
              </div>
              <FieldError msg={errs[`s${i}_name`]} />
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
                <div>
                  <label className={LABEL}>Approvers<Req /></label>
                  <ApproverList specs={s.approvers} meta={meta} canEdit={canEdit} onChange={(a) => setStep(i, { approvers: a })} allowGroup />
                  <FieldError msg={errs[`s${i}_appr`]} />
                  <p className={HINT}>{s.mode === "all" ? "Everyone listed must approve before the request moves on." : "The first approver to decide settles this step."}</p>
                </div>
                <div>
                  <label className={LABEL}>Only when</label>
                  <ConditionList conds={s.when} fields={fields} fieldLabel={fieldLabel} ops={meta.operators} canEdit={canEdit} onChange={(w) => setStep(i, { when: w })} errors={s.when.map((_, j) => errs[`s${i}_c${j}`])} />
                  <p className={HINT}>The step runs only if all these hold for the request; none = always. Use Legal entity or Location to vary the chain by company or branch.</p>
                </div>
                <div>
                  <label className={LABEL}>Escalation &amp; SLA</label>
                  <div className="grid grid-cols-2 gap-2">
                    <div><label className={LABEL}>Escalate after (h)</label><input className="input !py-1 text-xs" type="number" min={0} max={MAX_HOURS} step={1} value={s.escalate_after_hours} disabled={!canEdit} onChange={(e) => setStep(i, { escalate_after_hours: Math.trunc(Number(e.target.value)) || 0 })} /></div>
                    <div><label className={LABEL}>SLA (h)</label><input className="input !py-1 text-xs" type="number" min={0} max={MAX_HOURS} step={1} value={s.sla_hours} disabled={!canEdit} onChange={(e) => setStep(i, { sla_hours: Math.trunc(Number(e.target.value)) || 0 })} /></div>
                    <div className="col-span-2"><label className={LABEL}>Escalate to</label>
                      <select className="input !py-1 text-xs" value={s.escalate_to ? (s.escalate_to.type === "role" ? `role:${s.escalate_to.role}` : s.escalate_to.type === "user" ? `user:${s.escalate_to.user_id}` : `manager:${(s.escalate_to as any).level}`) : ""} disabled={!canEdit}
                        onChange={(e) => { const [t, val] = e.target.value.split(":"); setStep(i, { escalate_to: !t ? null : t === "role" ? { type: "role", role: val } : t === "user" ? { type: "user", user_id: val } : { type: "manager", level: Number(val) } }); }}>
                        <option value="">— nobody —</option>
                        {meta.roles.map((r) => <option key={r.key} value={`role:${r.key}`}>{r.label}</option>)}
                        <option value="manager:2">Manager level 2</option><option value="manager:3">Manager level 3</option>
                        {meta.people.map((p) => <option key={p.id} value={`user:${p.id}`}>{p.name}</option>)}
                      </select></div>
                  </div>
                  <FieldError msg={errs[`s${i}_esch`] || errs[`s${i}_esct`] || errs[`s${i}_sla`]} />
                  <p className={HINT}>If nobody acts within the hours above, the person chosen is added to the step and told. SLA only marks the step overdue; 0 = none.</p>
                </div>
              </div>
            </div>))}
          {canEdit && d.steps.length < 12 && <button className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => setD({ ...d, steps: [...d.steps, newStep(d.steps.length + 1)] })}><Plus className="w-3.5 h-3.5" /> Add step</button>}
          {canEdit && d.steps.length >= 12 && <p className={HINT}>A chain can have at most 12 steps.</p>}
        </div>
      )}
      {tab === "auto" && (
        <div className="space-y-3">
          <p className="text-sm text-gray-600">When every condition of a rule holds, the request is approved at once (actor "Auto-approval rule") and the owning screen is told. A rule needs at least one condition.</p>
          {d.auto_approve.map((r, i) => (
            <div key={i} className="border border-surface-border rounded-xl p-3">
              <div className="flex items-center gap-2 mb-1"><label className="sr-only" htmlFor={`auto-name-${i}`}>Rule name</label>
                <input id={`auto-name-${i}`} className="input !py-1 w-full sm:!w-72 text-sm" maxLength={120} placeholder="Rule name, e.g. Small amounts" value={r.name} disabled={!canEdit} onChange={(e) => setD({ ...d, auto_approve: d.auto_approve.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
                <button className="icon-btn text-gray-400 hover:text-red-600 ml-auto" title="Remove rule" disabled={!canEdit} onClick={() => setD({ ...d, auto_approve: d.auto_approve.filter((_, j) => j !== i) })}><X className="w-4 h-4" /></button></div>
              <FieldError msg={errs[`a${i}_name`]} />
              <div className="mt-1"><ConditionList conds={r.when} fields={fields} fieldLabel={fieldLabel} ops={meta.operators} canEdit={canEdit} onChange={(w) => setD({ ...d, auto_approve: d.auto_approve.map((x, j) => (j === i ? { ...x, when: w } : x)) })} errors={r.when.map((_, j) => errs[`a${i}_c${j}`])} /></div>
              <FieldError msg={errs[`a${i}_none`]} />
            </div>))}
          {canEdit && <button className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => setD({ ...d, auto_approve: [...d.auto_approve, { name: d.auto_approve.length ? `Rule ${d.auto_approve.length + 1}` : "Small amounts", when: [{ field: fields[0] || "amount", op: "lt", value: "" }] }] })}><Plus className="w-3.5 h-3.5" /> Add rule</button>}
        </div>
      )}
      {tab === "simulate" && <Simulator requestType={requestType} definition={d} fields={fields} fieldLabel={fieldLabel} meta={meta} />}
      {tab === "runs" && <Runs requestType={requestType} />}
    </AppModal>
  );
}

function ApproverList({ specs, meta, canEdit, onChange, allowGroup }: { specs: ApproverSpec[]; meta: WorkflowMeta; canEdit: boolean; onChange: (a: ApproverSpec[]) => void; allowGroup?: boolean }) {
  const setAt = (i: number, a: ApproverSpec) => onChange(specs.map((x, j) => (j === i ? a : x)));
  const kindOf = (a: ApproverSpec) => a.type;
  return (
    <div className="space-y-1.5">
      {specs.map((a, i) => (
        <div key={i} className="flex flex-wrap items-center gap-1.5">
          <select className="input !py-1 !w-auto text-xs" value={kindOf(a)} disabled={!canEdit} onChange={(e) => {
            const t = e.target.value;
            setAt(i, t === "role" ? { type: "role", role: "hr" } : t === "manager" ? { type: "manager", level: 1 } : t === "user" ? { type: "user", user_id: meta.people[0]?.id || "" } : t === "any_of" ? { type: "any_of", approvers: [{ type: "role", role: "finance" }] } : { type: "caller" });
          }}>
            <option value="manager">Reporting manager (level N)</option><option value="role">Role</option><option value="user">Named person</option>
            {allowGroup && <option value="any_of">Any one of a group</option>}<option value="caller">Approver named by the screen</option>
          </select>
          {a.type === "role" && <select className="input !py-1 !w-auto text-xs" value={a.role} disabled={!canEdit} onChange={(e) => setAt(i, { type: "role", role: e.target.value })}>{meta.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select>}
          {a.type === "manager" && <select className="input !py-1 !w-auto text-xs" value={a.level} disabled={!canEdit} onChange={(e) => setAt(i, { type: "manager", level: Number(e.target.value) })}>{[1, 2, 3, 4, 5].map((l) => <option key={l} value={l}>{l === 1 ? "Level 1 (direct manager)" : `Level ${l} (manager's manager…)`}</option>)}</select>}
          {a.type === "user" && <select className="input !py-1 !w-56 text-xs" value={a.user_id} disabled={!canEdit} onChange={(e) => setAt(i, { type: "user", user_id: e.target.value })}>{meta.people.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.role}</option>)}</select>}
          {a.type === "any_of" && <div className="pl-3 border-l-2 border-primary-200 w-full"><ApproverList specs={a.approvers} meta={meta} canEdit={canEdit} onChange={(inner) => setAt(i, { type: "any_of", approvers: inner })} /></div>}
          <button className="icon-btn text-gray-400 hover:text-red-600" disabled={!canEdit || specs.length === 1} onClick={() => onChange(specs.filter((_, j) => j !== i))}><X className="w-3.5 h-3.5" /></button>
        </div>))}
      {canEdit && <button className="text-[11px] text-primary-600 inline-flex items-center gap-0.5" onClick={() => onChange([...specs, { type: "role", role: "hr" }])}><Plus className="w-3 h-3" /> add approver</button>}
    </div>
  );
}

function ConditionList({ conds, fields, fieldLabel, ops, canEdit, onChange, errors }: { conds: Condition[]; fields: string[]; fieldLabel: (k: string) => string; ops: { op: string; label: string }[]; canEdit: boolean; onChange: (c: Condition[]) => void; errors?: (string | undefined)[] }) {
  const setAt = (i: number, patch: Partial<Condition>) => onChange(conds.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  // Legal entity conditions compare the entity id — offer the entities by name instead of a raw id box.
  const usesEntity = conds.some((c) => c.field === "entity_id");
  const ents = useQuery({ queryKey: ["entities", "list", false], queryFn: () => entitiesApi.list(false), enabled: usesEntity, staleTime: 5 * 60_000 });
  return (
    <div className="space-y-1.5">
      {conds.map((c, i) => {
        const noValue = c.op === "is_empty" || c.op === "not_empty";
        const multi = c.op === "in" || c.op === "not_in";
        const entityPick = c.field === "entity_id" && !multi && !noValue && (ents.data?.entities || []).length > 0;
        return (
        <div key={i}>
          <div className="grid grid-cols-[1fr_1fr_auto] sm:grid-cols-[1fr_1fr_1fr_auto] gap-1">
            <select className="input !py-1 text-xs" aria-label="Field" value={c.field} disabled={!canEdit} onChange={(e) => setAt(i, { field: e.target.value })}>{fields.map((f) => <option key={f} value={f}>{fieldLabel(f)}</option>)}</select>
            <select className="input !py-1 text-xs" aria-label="Operator" value={c.op} disabled={!canEdit} onChange={(e) => setAt(i, { op: e.target.value })}>{ops.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}</select>
            {entityPick
              ? <select className="input !py-1 text-xs col-span-2 sm:col-span-1 row-start-2 sm:row-start-auto" aria-label="Value" value={c.value ?? ""} disabled={!canEdit} onChange={(e) => setAt(i, { value: e.target.value })}>
                  <option value="">— pick an entity —</option>{(ents.data?.entities || []).map((en) => <option key={en.id} value={en.id}>{en.short_code} · {en.name}</option>)}</select>
              : <input className="input !py-1 text-xs col-span-2 sm:col-span-1 row-start-2 sm:row-start-auto" aria-label="Value" value={c.value ?? ""} disabled={!canEdit || noValue}
                  inputMode={NUMERIC_FIELDS.has(c.field) && !multi ? "decimal" : undefined}
                  placeholder={noValue ? "no value needed" : multi ? "a, b, c" : c.field === "amount" ? "$ e.g. 1000" : NUMERIC_FIELDS.has(c.field) ? "number" : "value"} onChange={(e) => setAt(i, { value: e.target.value })} />}
            <button className="icon-btn text-gray-400 row-start-1 col-start-3 sm:row-start-auto sm:col-start-auto" title="Remove condition" disabled={!canEdit} onClick={() => onChange(conds.filter((_, j) => j !== i))}><X className="w-3.5 h-3.5" /></button>
          </div>
          <FieldError msg={errors?.[i]} />
        </div>); })}
      {canEdit && <button className="text-[11px] text-primary-600 inline-flex items-center gap-0.5" onClick={() => onChange([...conds, { field: fields[0] || "amount", op: "gte", value: "" }])}><Plus className="w-3 h-3" /> add condition</button>}
    </div>
  );
}

function Simulator({ requestType, definition, fields, fieldLabel, meta }: { requestType: string; definition: ChainDefinition; fields: string[]; fieldLabel: (k: string) => string; meta: WorkflowMeta }) {
  const [ctx, setCtx] = useState<Record<string, string>>({});
  const [who, setWho] = useState("");
  const [res, setRes] = useState<SimResult | null>(null);
  const run = useMutation({ mutationFn: () => workflowsApi.simulate(requestType, { context: Object.fromEntries(Object.entries(ctx).filter(([, v]) => v !== "")), definition, requester_employee_id: who || undefined }),
    onSuccess: setRes, onError: (e) => toast.error(errDetail(e), { duration: 8000 }) });
  return (
    <div className="grid grid-cols-1 lg:grid-cols-[22rem_1fr] gap-4">
      <div className="card space-y-2">
        <p className="text-xs text-gray-500">Try the chain as it is on this screen (unsaved changes included): who would approve a request with these values?</p>
        <div><label className={LABEL}>Requester (for manager steps)</label>
          <select className="input !py-1.5 text-sm" value={who} onChange={(e) => setWho(e.target.value)}><option value="">— pick a person —</option>{meta.people.filter((p) => p.role !== "org_admin").map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          <p className="text-[11px] text-gray-400">The person's employee record decides the reporting line.</p></div>
        {fields.map((f) => <div key={f}><label className={LABEL}>{fieldLabel(f)}</label><input className="input !py-1.5 text-sm" value={ctx[f] || ""} onChange={(e) => setCtx({ ...ctx, [f]: e.target.value })} placeholder={f === "amount" ? "25000" : ""} /></div>)}
        <button className="btn-primary text-xs inline-flex items-center gap-1" disabled={run.isPending} onClick={() => run.mutate()}><Play className="w-3.5 h-3.5" /> Run test</button>
      </div>
      <div>
        {!res ? <div className="card text-sm text-gray-500">Fill in a few values and press Run test.</div> : (
          <div className="card">
            {res.auto_approved ? <div className="rounded-lg bg-emerald-50 text-emerald-800 px-3 py-2 text-sm">Approved automatically by the rule <b>{res.auto_rule}</b> — nobody has to decide.</div> : (
              <ol className="space-y-2">
                {res.steps.map((s, i) => (
                  <li key={i} className="flex gap-3 items-start">
                    <span className="w-6 h-6 rounded-full bg-primary-600 text-white grid place-items-center text-xs font-bold shrink-0">{i + 1}</span>
                    <div><div className="font-medium text-gray-800">{s.name} <span className="text-xs text-gray-400">{s.mode === "all" ? "— everyone must approve" : "— any one"}</span></div>
                      <div className="text-sm text-gray-700">{s.approvers.join(s.mode === "all" ? " AND " : " or ")}</div>
                      {(s.escalate_after_hours > 0 || s.sla_hours > 0) && <div className="text-[11px] text-gray-400">{s.escalate_after_hours > 0 ? `escalates after ${s.escalate_after_hours} h` : ""}{s.escalate_after_hours > 0 && s.sla_hours > 0 ? " · " : ""}{s.sla_hours > 0 ? `SLA ${s.sla_hours} h` : ""}</div>}
                    </div>
                  </li>))}
              </ol>)}
            {res.skipped.length > 0 && <p className="text-xs text-gray-500 mt-3">Skipped (conditions do not apply): {res.skipped.join(", ")}</p>}
            {res.note && <p className="text-[11px] text-amber-700 mt-2">{res.note}</p>}
          </div>)}
      </div>
    </div>
  );
}

function Runs({ requestType }: { requestType: string }) {
  const q = useQuery({ queryKey: ["wf", "runs", requestType], queryFn: () => workflowsApi.runs(requestType) });
  const rows = q.data?.items || [];
  return (
    <DataGrid rows={rows} rowKey={(r: any) => r.id} minWidth={900} density="compact" exportName={`runs-${requestType}`} empty="No requests of this type have gone through a chain yet."
      columns={[
        { key: "started_at", label: "Started", render: (r: any) => when(r.started_at), text: (r: any) => r.started_at || "" },
        { key: "chain_name", label: "Chain", render: (r: any) => `${r.chain_name} v${r.chain_version}` },
        { key: "status", label: "Status" },
        { key: "current", label: "Step", render: (r: any) => `${Math.min(r.current + 1, r.steps.length)} of ${r.steps.length}` },
        { key: "steps", label: "Steps", text: (r: any) => r.steps.map((s: any) => s.name).join(" → "), render: (r: any) => <span className="text-xs">{r.steps.map((s: any, i: number) => <span key={i} className={`mr-1 ${s.done ? "text-emerald-700" : s.current ? "text-primary-700 font-semibold" : "text-gray-400"}`}>{s.name}{i < r.steps.length - 1 ? " →" : ""}</span>)}</span> },
        { key: "history", label: "History", text: (r: any) => r.history.map((h: any) => h.event).join(", "), render: (r: any) => <span className="text-[11px] text-gray-500 inline-flex items-center gap-1"><History className="w-3 h-3" />{r.history.map((h: any) => `${h.event}${h.by ? ` by ${h.by}` : ""}`).join(" · ")}</span> },
      ]} />
  );
}
