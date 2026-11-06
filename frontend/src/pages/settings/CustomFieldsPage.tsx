// D1 — Settings → Custom fields: the per-org field registry (employee / candidate / asset / expense).
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import toast from "react-hot-toast";
import { ListPlus, Plus, Pencil, Ban, RotateCcw } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { customFieldsApi, errDetail, type CfDef, type CfEntity, type CfType } from "../../api/config";
import { useAuthStore } from "../../store/authStore";
import { useUrlTab } from "../../search/useUrlTab";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const SETUP = ["org_admin", "hr_admin", "superadmin"];
const LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1";
const BLANK = (entity: CfEntity): Partial<CfDef> => ({ entity, key: "", label: "", type: "text", required: false, options: [], validation: {}, visible_to: [], editable_by: [], section: "", sort_order: 100, show_in_grid: true, active: true });

export default function CustomFieldsPage() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const qc = useQueryClient();
  const [entity, setEntity] = useUrlTab<CfEntity>("employee", "entity");   // ?entity= (global search deep link)
  const [edit, setEdit] = useState<Partial<CfDef> | null>(null);
  const meta = useQuery({ queryKey: ["cf", "meta"], queryFn: customFieldsApi.meta });
  const defs = useQuery({ queryKey: ["cf", "defs", entity, "setup"], queryFn: () => customFieldsApi.definitions(entity) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["cf"] });
  const toggle = useMutation({
    mutationFn: async (d: CfDef) => { if (d.active) await customFieldsApi.deactivate(d.id); else await customFieldsApi.update(d.id, { ...d, active: true }); },
    onSuccess: (_r, d) => { toast.success(d.active ? `${d.label} deactivated — stored values are kept` : `${d.label} is active again`); refresh(); },
    onError: (e) => toast.error(errDetail(e)),
  });
  if (!role) return <Navigate to="/login" replace />;
  if (!SETUP.includes(role)) return <Navigate to="/settings" replace />;
  const roleLabel = (k: string) => meta.data?.roles.find((r) => r.key === k)?.label || k;
  const rows = defs.data?.items || [];
  const cols: GridColumn<CfDef>[] = [
    { key: "sort_order", label: "#", type: "number", width: "56px" },
    { key: "label", label: "Label", render: (d) => <span className={`font-medium ${d.active ? "text-gray-800" : "text-gray-400 line-through"}`}>{d.label}</span> },
    { key: "key", label: "Key", render: (d) => <code className="text-[11px] bg-gray-50 px-1 rounded">{d.key}</code> },
    { key: "type_label", label: "Type", text: (d) => d.type_label || d.type, render: (d) => <span>{d.type_label || d.type}{(d.type === "select" || d.type === "multiselect") && d.options?.length ? <span className="text-gray-400 text-xs"> · {d.options.length} options</span> : ""}{d.type === "lookup" && d.options?.[0] ? <span className="text-gray-400 text-xs"> · {d.options[0]}</span> : ""}</span> },
    { key: "required", label: "Required", text: (d) => (d.required ? "yes" : "no"), render: (d) => (d.required ? <span className="badge-amber">Required</span> : <span className="text-gray-300">—</span>) },
    { key: "section", label: "Section" },
    { key: "visible_to", label: "Visible to", text: (d) => (d.visible_to.length ? d.visible_to.map(roleLabel).join(", ") : "Everyone who can see the record"), render: (d) => <span className="text-xs text-gray-600">{d.visible_to.length ? d.visible_to.map(roleLabel).join(", ") : "Everyone who sees the record"}</span> },
    { key: "editable_by", label: "Editable by", text: (d) => (d.editable_by.length ? d.editable_by.map(roleLabel).join(", ") : "Org admin + HR"), render: (d) => <span className="text-xs text-gray-600">{d.editable_by.length ? d.editable_by.map(roleLabel).join(", ") : "Org admin + HR"}</span> },
    { key: "validation", label: "Rules", text: (d) => rules(d), render: (d) => <span className="text-xs text-gray-500">{rules(d) || "—"}</span> },
    { key: "show_in_grid", label: "In grids", text: (d) => (d.show_in_grid ? "yes" : "no"), render: (d) => (d.show_in_grid ? "Yes" : "No") },
    { key: "active", label: "Status", text: (d) => (d.active ? "active" : "inactive"), render: (d) => (d.active ? <span className="badge-green">Active</span> : <span className="badge-red">Inactive</span>) },
    { key: "updated_by", label: "Changed by", render: (d) => <span className="text-xs text-gray-500">{d.updated_by || ""}{d.updated_at ? ` · ${new Date(d.updated_at).toLocaleDateString()}` : ""}</span> },
    { key: "actions", label: "", sortable: false, text: () => "", render: (d) => (
      <div className="flex gap-1 justify-end">
        <button className="icon-btn text-gray-400 hover:text-primary-600" title="Edit" onClick={(e) => { e.stopPropagation(); setEdit(d); }}><Pencil className="w-4 h-4" /></button>
        <button className={`icon-btn text-gray-400 ${d.active ? "hover:text-red-600" : "hover:text-green-600"}`} title={d.active ? "Deactivate (values are kept)" : "Activate"} onClick={(e) => { e.stopPropagation(); toggle.mutate(d); }}>{d.active ? <Ban className="w-4 h-4" /> : <RotateCcw className="w-4 h-4" />}</button>
      </div>) },
  ];
  return (
    <div>
      <PageHeader title="Custom fields" icon={ListPlus}
        subtitle="Add your own fields to employees, candidates, assets and expense claims. They appear on the forms, the profile, the grids (column chooser), exports, letters ({{custom.key}}) and the report builder."
        actions={<button className="btn-primary flex items-center gap-1.5" onClick={() => setEdit(BLANK(entity))}><Plus className="w-4 h-4" /> New field</button>} />
      <Tabs value={entity} onChange={setEntity} tabs={(meta.data?.entities || [{ key: "employee", label: "Employee" }]).map((e) => ({ key: e.key, label: e.label }))} />
      {defs.isLoading ? <div className="grid place-items-center py-16"><Spinner /></div> : (
        <DataGrid<CfDef> columns={cols} rows={rows} rowKey={(d) => d.id} exportName={`custom-fields-${entity}`} minWidth={1200}
          initialSort={{ key: "sort_order", dir: "asc" }} onRowClick={(d) => setEdit(d)} density="compact"
          empty={`No custom fields on ${entity}s yet. Click New field.`} />
      )}
      <p className="text-[11px] text-gray-400 mt-2">Every definition change is written to the audit log. Deactivating a field hides it everywhere but keeps the stored values; the type cannot change once a field exists (deactivate it and add a new one).</p>
      {edit && meta.data && <Editor def={edit} meta={meta.data} onClose={() => setEdit(null)} onDone={() => { setEdit(null); refresh(); }} />}
    </div>
  );
}

function rules(d: CfDef): string {
  const v = d.validation || {}; const out: string[] = [];
  if (v.regex) out.push(`pattern ${v.regex}`);
  if (v.min !== undefined && v.min !== null && v.min !== "") out.push(`min ${v.min}`);
  if (v.max !== undefined && v.max !== null && v.max !== "") out.push(`max ${v.max}`);
  return out.join(" · ");
}

type CfMeta = Awaited<ReturnType<typeof customFieldsApi.meta>>;

function Editor({ def, meta, onClose, onDone }: { def: Partial<CfDef>; meta: CfMeta; onClose: () => void; onDone: () => void }) {
  const isNew = !def.id;
  const [f, setF] = useState<Partial<CfDef>>({ ...def, validation: { ...(def.validation || {}) } });
  const [opts, setOpts] = useState((def.type === "lookup" ? "" : (def.options || []).join(", ")));
  const [lookup, setLookup] = useState(def.type === "lookup" ? (def.options?.[0] || "employees") : "employees");
  const [tried, setTried] = useState(false);
  const set = (k: keyof CfDef, v: any) => setF((p) => ({ ...p, [k]: v }));
  const setV = (k: string, v: any) => setF((p) => ({ ...p, validation: { ...(p.validation || {}), [k]: v } }));
  const keyPreview = useMemo(() => f.key || (f.label || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40), [f.key, f.label]);
  const t = (f.type || "text") as CfType;
  const isChoice = t === "select" || t === "multiselect";
  const optList = useMemo(() => opts.split(",").map((s) => s.trim()).filter(Boolean), [opts]);
  const errs = useMemo(() => {
    const val = f.validation || {};
    const minS = val.min ?? "", maxS = val.max ?? "";
    const isLen = t === "text" || t === "file";
    const out = collect({
      label: v.required(f.label, "Label") || v.maxLen(f.label?.trim(), 120, "Label"),
      key: isNew && f.key && !(f.key[0] >= "a" && f.key[0] <= "z") ? "The key must start with a lowercase letter" : null,
      options: isChoice ? (optList.length === 0 ? "Add at least one option"
        : new Set(optList.map((o) => o.toLowerCase())).size !== optList.length ? "Options must be different from each other" : null) : null,
      min: t === "number" ? v.range(minS, -1e15, undefined, "Minimum") : isLen ? v.range(minS, 0, 4000, "Min length") : null,
      max: t === "number" ? v.range(maxS, -1e15, undefined, "Maximum") : isLen ? v.range(maxS, 0, 4000, "Max length") : null,
      order: v.range(f.sort_order, 0, 9999, "Order"),
      help: v.maxLen(val.help, 300, "Help text"),
      section: v.maxLen(f.section, 60, "Section"),
    });
    if (!out.min && !out.max && minS !== "" && maxS !== "") {
      if (t === "date") { const m = v.dateOrder(minS, maxS, "The latest date cannot be before the earliest date"); if (m) out.max = m; }
      else if ((t === "number" || isLen) && Number(maxS) < Number(minS)) out.max = t === "number" ? "Maximum cannot be less than the minimum" : "Max length cannot be less than the min length";
    }
    if (isLen && !out.min && minS !== "" && !Number.isInteger(Number(minS))) out.min = "Min length must be a whole number";
    if (isLen && !out.max && maxS !== "" && !Number.isInteger(Number(maxS))) out.max = "Max length must be a whole number";
    if (val.regex && isLen) {
      let m: string | null = null;
      try { new RegExp(String(val.regex)); } catch { m = "This pattern is not a valid regular expression"; }
      m = m || v.maxLen(val.regex, 200, "Pattern");
      if (m) out.regex = m;
    }
    if (!Number.isInteger(Number(f.sort_order ?? 100)) && !out.order) out.order = "Order must be a whole number";
    return out;
  }, [f, t, isNew, isChoice, optList]);
  const shown = (k: string) => (tried || !["label", "options"].includes(k) ? errs[k] : null);
  const save = useMutation({
    mutationFn: () => {
      const body: Partial<CfDef> = { ...f, label: (f.label || "").trim(), options: t === "lookup" ? [lookup] : isChoice ? optList : null };
      return isNew ? customFieldsApi.create(body) : customFieldsApi.update(def.id!, body);
    },
    onSuccess: () => { toast.success(isNew ? "Field added" : "Field saved"); onDone(); },
    onError: (e) => toast.error(errDetail(e), { duration: 7000 }),
  });
  const trySave = () => {
    setTried(true);
    if (Object.keys(errs).length) { toast.error(firstError(errs)); return; }
    save.mutate();
  };
  const toggleRole = (k: "visible_to" | "editable_by", r: string) => set(k, (f[k] || []).includes(r) ? (f[k] || []).filter((x) => x !== r) : [...(f[k] || []), r]);
  const HINT = "text-[11px] text-gray-500 mt-0.5";
  return (
    <WideModal title={isNew ? `New custom field — ${meta.entities.find((e) => e.key === f.entity)?.label || f.entity}` : `Custom field · ${def.label}`} onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={save.isPending} onClick={trySave}>{save.isPending ? "Saving…" : isNew ? "Add field" : "Save"}</button></>}>
      <FormSection title="Basics">
        <FormGrid cols={4}>
          <div><label className={LABEL}>Entity<Req /></label>
            <select className="input" value={f.entity} disabled={!isNew} onChange={(e) => set("entity", e.target.value)}>{meta.entities.map((e) => <option key={e.key} value={e.key}>{e.label}</option>)}</select>
            <p className={HINT}>{isNew ? "The record this field is added to." : "Fixed once saved."}</p></div>
          <div><label className={LABEL}>Label<Req /></label><input className="input" maxLength={120} value={f.label || ""} onChange={(e) => set("label", e.target.value)} placeholder="e.g. Blood group" autoFocus />
            <FieldError msg={shown("label")} /></div>
          <div><label className={LABEL}>Key</label><input className="input font-mono text-xs" maxLength={40} value={f.key || ""} disabled={!isNew} placeholder={keyPreview || "from the label"} onChange={(e) => set("key", e.target.value.toLowerCase().replace(/[^a-z0-9_]+/g, "_"))} />
            <FieldError msg={errs.key} />
            <p className={HINT}>{isNew ? `Lowercase letters, digits and underscores. Letters use {{custom.${keyPreview || "key"}}}. Fixed once saved.` : `{{custom.${f.key}}}`}</p></div>
          <div><label className={LABEL}>Type<Req /></label>
            <select className="input" value={t} disabled={!isNew} onChange={(e) => set("type", e.target.value)}>{meta.types.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</select>
            <p className={HINT}>{isNew ? "Cannot be changed once values exist." : "Fixed — deactivate and add a new field to change it."}</p></div>
          {isChoice && <div className="sm:col-span-2 lg:col-span-4"><label className={LABEL}>Options (comma separated)<Req /></label><input className="input" value={opts} onChange={(e) => setOpts(e.target.value)} placeholder="O+, A+, B+, AB+" />
            <FieldError msg={shown("options")} />
            <p className={HINT}>{optList.length ? `${optList.length} option${optList.length === 1 ? "" : "s"}: ${optList.join(" · ")}` : "Type the choices separated by commas."}</p></div>}
          {t === "lookup" && <div className="sm:col-span-2"><label className={LABEL}>Master list<Req /></label><select className="input" value={lookup} onChange={(e) => setLookup(e.target.value)}>{meta.lookups.map((l) => <option key={l} value={l}>{l.replace("_", " ")}</option>)}</select>
            <p className={HINT}>People pick a value from this list.</p></div>}
        </FormGrid>
      </FormSection>

      <FormSection title="Validation" hint="Checked every time a value is saved on a record.">
        <FormGrid cols={4}>
          {(t === "text" || t === "file") && <div className="sm:col-span-2"><label className={LABEL}>Pattern (regular expression)</label><input className="input font-mono text-xs" maxLength={200} value={f.validation?.regex || ""} onChange={(e) => setV("regex", e.target.value)} placeholder="e.g. [A-Z]{2}[0-9]{4}" />
            <FieldError msg={errs.regex} />
            <p className={HINT}>Optional. The whole value must match, e.g. [A-Z]{"{2}"}[0-9]{"{4}"} for a vehicle code.</p></div>}
          {(t === "text" || t === "file" || t === "number" || t === "date") && <>
            <div><label className={LABEL}>{t === "date" ? "Earliest date" : t === "number" ? "Minimum" : "Min length"}</label><input className="input" type={t === "date" ? "date" : "number"} min={t === "number" || t === "date" ? undefined : 0} step={t === "number" ? "any" : 1} value={(f.validation?.min as any) ?? ""} onChange={(e) => setV("min", e.target.value)} />
              <FieldError msg={errs.min} /></div>
            <div><label className={LABEL}>{t === "date" ? "Latest date" : t === "number" ? "Maximum" : "Max length"}</label><input className="input" type={t === "date" ? "date" : "number"} min={t === "number" || t === "date" ? undefined : 0} step={t === "number" ? "any" : 1} value={(f.validation?.max as any) ?? ""} onChange={(e) => setV("max", e.target.value)} />
              <FieldError msg={errs.max} /></div></>}
          {!(t === "text" || t === "file" || t === "number" || t === "date") && <p className="sm:col-span-2 text-[11px] text-gray-500 self-center">{isChoice || t === "lookup" ? "Only the listed choices are accepted." : "Yes / No — no further rules."}</p>}
          <div className="sm:col-span-2 lg:col-span-4"><label className={LABEL}>Help text (shown under the input)</label><input className="input" maxLength={300} value={f.validation?.help || ""} onChange={(e) => setV("help", e.target.value)} placeholder="e.g. As printed on the blood donor card" />
            <FieldError msg={errs.help} /></div>
        </FormGrid>
      </FormSection>

      <FormSection title="Placement">
        <FormGrid cols={4}>
          <div><label className={LABEL}>Section on the form</label><input className="input" maxLength={60} value={f.section || ""} onChange={(e) => set("section", e.target.value)} placeholder="e.g. Medical" />
            <FieldError msg={errs.section} />
            <p className={HINT}>Fields with the same section are grouped together.</p></div>
          <div><label className={LABEL}>Order</label><input className="input" type="number" min={0} step={1} value={f.sort_order ?? 100} onChange={(e) => set("sort_order", e.target.value === "" ? 100 : Number(e.target.value))} />
            <FieldError msg={errs.order} />
            <p className={HINT}>Lower numbers come first.</p></div>
          <div className="sm:col-span-2 flex flex-col gap-1.5 sm:pt-4">
            <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={!!f.required} onChange={(e) => set("required", e.target.checked)} /> Required — a record cannot be saved without it</label>
            <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={f.show_in_grid !== false} onChange={(e) => set("show_in_grid", e.target.checked)} /> Offer as a grid column (list views and exports)</label>
            <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active !== false} onChange={(e) => set("active", e.target.checked)} /> Active</label>
          </div>
        </FormGrid>
      </FormSection>

      <FormSection title="Access">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-x-4 gap-y-3">
          <div><label className={LABEL}>Visible to (none ticked = everyone who can open the record)</label>
            <div className="flex flex-wrap gap-1.5">{meta.roles.map((r) => <label key={r.key} className={`text-xs px-2 py-1 rounded-full border cursor-pointer ${(f.visible_to || []).includes(r.key) ? "bg-primary-50 border-primary-300 text-primary-700" : "border-surface-border text-gray-600"}`}><input type="checkbox" className="hidden" checked={(f.visible_to || []).includes(r.key)} onChange={() => toggleRole("visible_to", r.key)} />{r.label}</label>)}</div></div>
          <div><label className={LABEL}>Editable by (none ticked = org admin + HR)</label>
            <div className="flex flex-wrap gap-1.5">{meta.roles.map((r) => <label key={r.key} className={`text-xs px-2 py-1 rounded-full border cursor-pointer ${(f.editable_by || []).includes(r.key) ? "bg-primary-50 border-primary-300 text-primary-700" : "border-surface-border text-gray-600"}`}><input type="checkbox" className="hidden" checked={(f.editable_by || []).includes(r.key)} onChange={() => toggleRole("editable_by", r.key)} />{r.label}</label>)}</div>
            <p className={HINT}>"Employee (self)" lets a person change the field on their own record only; a manager can read direct reports.</p></div>
        </div>
      </FormSection>
    </WideModal>
  );
}
