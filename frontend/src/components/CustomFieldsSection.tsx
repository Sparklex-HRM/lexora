// D1 — custom fields on any record: definitions come from Settings → Custom fields; values live on the
// record's custom_data. Drop <CustomFieldsSection entity="employee" id={...} /> into a form or a profile.
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { customFieldsApi, errDetail, type CfDef, type CfEntity, type CfValues } from "../api/config";
import { mastersApi, employeeApi, opsApi } from "../api/endpoints";

export const CF_LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1";

/** Definitions this role may see for one entity (cached). */
export function useCustomFieldDefs(entity: CfEntity, enabled = true) {
  return useQuery({ queryKey: ["cf", "defs", entity], queryFn: () => customFieldsApi.definitions(entity), enabled, staleTime: 120_000 });
}

/** Values of one record (+ the fields the role may see / edit). */
export function useCustomValues(entity: CfEntity, id?: string | null) {
  return useQuery({ queryKey: ["cf", "values", entity, id], queryFn: () => customFieldsApi.values(entity, id!), enabled: !!id, retry: false });
}

/** Bulk values for a grid: {rowId: {key: value}} plus the grid-visible definitions. */
export function useCustomGrid(entity: CfEntity, enabled = true) {
  const q = useQuery({ queryKey: ["cf", "bulk", entity], queryFn: () => customFieldsApi.bulk(entity), enabled, retry: false, staleTime: 60_000 });
  const gridDefs = useMemo(() => (q.data?.fields || []).filter((f) => f.show_in_grid && f.active), [q.data]);
  return { ...q, gridDefs, valuesOf: (id: string): CfValues => q.data?.values?.[id] || {} };
}

export function displayValue(def: CfDef, v: any): string {
  if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return "";
  if (def.type === "boolean") return v ? "Yes" : "No";
  if (def.type === "multiselect" && Array.isArray(v)) return v.join(", ");
  if (def.type === "date") { const dt = new Date(String(v)); return isNaN(dt.getTime()) ? String(v) : dt.toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }); }
  if (def.type === "number") return Number(v).toLocaleString("en-CA");
  return String(v);
}

/** Validate one value the way the server will (so the form can say so before saving). */
export function checkValue(def: CfDef, v: any): string | null {
  const empty = v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length);
  if (empty) return def.required ? `${def.label} is required` : null;
  const val = def.validation || {};
  if (def.type === "text" || def.type === "file") {
    const s = String(v);
    if (val.regex) { try { if (!new RegExp(`^(?:${val.regex})$`).test(s)) return `${def.label} does not match the required pattern${val.help ? ` (${val.help})` : ""}`; } catch { /* server decides */ } }
    if (val.min !== undefined && val.min !== null && s.length < Number(val.min)) return `${def.label} needs at least ${val.min} characters`;
    if (val.max !== undefined && val.max !== null && s.length > Number(val.max)) return `${def.label} can have at most ${val.max} characters`;
  }
  if (def.type === "number") {
    const n = Number(v); if (isNaN(n)) return `${def.label} must be a number`;
    if (val.min !== undefined && val.min !== null && n < Number(val.min)) return `${def.label} must be at least ${val.min}`;
    if (val.max !== undefined && val.max !== null && n > Number(val.max)) return `${def.label} must be at most ${val.max}`;
  }
  if (def.type === "date") {
    if (val.min && String(v) < String(val.min)) return `${def.label} cannot be before ${val.min}`;
    if (val.max && String(v) > String(val.max)) return `${def.label} cannot be after ${val.max}`;
  }
  return null;
}

function LookupOptions({ source }: { source: string }) {
  const emps = useQuery({ queryKey: ["employees"], queryFn: employeeApi.list, enabled: source === "employees" });
  const branches = useQuery({ queryKey: ["ops", "branches"], queryFn: () => opsApi.list("branches"), enabled: source === "branches" });
  const masters = useQuery({ queryKey: ["m", source], queryFn: () => mastersApi.list(source as any), enabled: !["employees", "branches"].includes(source) });
  if (source === "employees") return <>{(emps.data || []).map((e) => <option key={e.id} value={`${e.first_name} ${e.last_name} · ${e.employee_number}`}>{e.first_name} {e.last_name} · {e.employee_number}</option>)}</>;
  if (source === "branches") return <>{((branches.data || []) as any[]).map((b) => <option key={b.id} value={b.name}>{b.name}</option>)}</>;
  return <>{((masters.data || []) as any[]).map((m) => <option key={m.id || m.name} value={m.name}>{m.name}</option>)}</>;
}

/** One input per definition type. */
export function CustomFieldInput({ def, value, onChange, disabled, className = "input" }: { def: CfDef; value: any; onChange: (v: any) => void; disabled?: boolean; className?: string }) {
  const v = value ?? "";
  switch (def.type) {
    case "number": return <input type="number" className={className} value={v} disabled={disabled} min={def.validation?.min as any} max={def.validation?.max as any} onChange={(e) => onChange(e.target.value)} />;
    case "date": return <input type="date" className={className} value={String(v).slice(0, 10)} disabled={disabled} min={def.validation?.min as any} max={def.validation?.max as any} onChange={(e) => onChange(e.target.value)} />;
    case "boolean": return (
      <select className={className} value={v === "" ? "" : v ? "yes" : "no"} disabled={disabled} onChange={(e) => onChange(e.target.value === "" ? "" : e.target.value === "yes")}>
        <option value="">—</option><option value="yes">Yes</option><option value="no">No</option></select>);
    case "select": return (
      <select className={className} value={v} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>{(def.options || []).map((o) => <option key={o} value={o}>{o}</option>)}</select>);
    case "multiselect": {
      const picked: string[] = Array.isArray(v) ? v : [];
      return (
        <div className="flex flex-wrap gap-1.5 py-1">
          {(def.options || []).map((o) => (
            <label key={o} className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full border cursor-pointer ${picked.includes(o) ? "bg-primary-50 border-primary-300 text-primary-700" : "border-surface-border text-gray-600"} ${disabled ? "opacity-60 cursor-default" : ""}`}>
              <input type="checkbox" className="hidden" disabled={disabled} checked={picked.includes(o)} onChange={(e) => onChange(e.target.checked ? [...picked, o] : picked.filter((x) => x !== o))} />{o}
            </label>))}
        </div>);
    }
    case "lookup": return (
      <>
        <input className={className} list={`cf-lookup-${def.key}`} value={v} disabled={disabled} onChange={(e) => onChange(e.target.value)} placeholder="Choose or type…" />
        <datalist id={`cf-lookup-${def.key}`}><LookupOptions source={(def.options || ["employees"])[0]} /></datalist>
      </>);
    case "file": return <input className={className} value={v} disabled={disabled} onChange={(e) => onChange(e.target.value)} placeholder="Document id, link or file name" />;
    default: return <input className={className} value={v} disabled={disabled} maxLength={2000} onChange={(e) => onChange(e.target.value)} />;
  }
}

/**
 * The section itself. Two ways to use it:
 *  - with `id`: loads + saves the record's values itself (profile, detail sheets, edit forms of existing rows)
 *  - with `values` + `onChange` and no id: a controlled block for a NEW record; call `saveCustomValues` after creating it.
 */
export function CustomFieldsSection({ entity, id, values, onChange, mode = "edit", title = "Custom fields", compact = false, onSaved }: {
  entity: CfEntity; id?: string | null; values?: CfValues; onChange?: (v: CfValues) => void; mode?: "edit" | "view";
  title?: string | null; compact?: boolean; onSaved?: (v: CfValues) => void;
}) {
  const qc = useQueryClient();
  const defsQ = useCustomFieldDefs(entity);
  const valQ = useCustomValues(entity, id);
  const [local, setLocal] = useState<CfValues>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (valQ.data) { setLocal(valQ.data.values || {}); setDirty(false); } }, [valQ.data]);
  const defs = useMemo(() => {
    const src = (id && valQ.data?.fields) ? valQ.data.fields : (defsQ.data?.items || []);
    return src.filter((f) => f.active);
  }, [defsQ.data, valQ.data, id]);
  const controlled = !id && !!onChange;
  const cur: CfValues = controlled ? (values || {}) : local;
  const set = (k: string, v: any) => { if (controlled) onChange!({ ...cur, [k]: v }); else { setLocal((p) => ({ ...p, [k]: v })); setDirty(true); } };
  const sections = useMemo(() => {
    const g = new Map<string, CfDef[]>();
    defs.forEach((d) => { const s = d.section || ""; g.set(s, [...(g.get(s) || []), d]); });
    return [...g.entries()];
  }, [defs]);
  if (!defs.length) return null;
  const save = async () => {
    const problems = defs.filter((d) => d.can_edit !== false).map((d) => checkValue(d, cur[d.key])).filter(Boolean);
    if (problems.length) return toast.error(problems[0] as string);
    setBusy(true);
    try {
      const patch: CfValues = {}; defs.forEach((d) => { if (d.can_edit !== false) patch[d.key] = cur[d.key] ?? ""; });
      const out = await customFieldsApi.save(entity, id!, patch);
      qc.invalidateQueries({ queryKey: ["cf"] }); setDirty(false); toast.success("Custom fields saved"); onSaved?.(out.values);
    } catch (e) { toast.error(errDetail(e, "Could not save the custom fields")); } finally { setBusy(false); }
  };
  const grid = compact ? "grid grid-cols-1 sm:grid-cols-2 gap-2" : "grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3";
  return (
    <div className="space-y-3" data-testid={`cf-section-${entity}`}>
      {title && <div className="flex items-center justify-between"><h3 className="text-xs font-semibold text-gray-600 uppercase tracking-wide">{title}</h3>
        {!!id && mode === "edit" && valQ.data?.can_edit && <button type="button" className="btn-primary !py-1 !px-2.5 text-xs disabled:opacity-50" disabled={!dirty || busy} onClick={save}>{busy ? "Saving…" : "Save custom fields"}</button>}</div>}
      {sections.map(([sec, list]) => (
        <div key={sec || "_"}>
          {sec && <div className="text-[11px] font-semibold text-gray-500 mb-1">{sec}</div>}
          <div className={grid}>
            {list.map((d) => {
              const ro = mode === "view" || d.can_edit === false;
              const err = !ro ? checkValue(d, cur[d.key]) : null;
              return (
                <div key={d.key}>
                  <label className={CF_LABEL}>{d.label}{d.required && <span className="text-red-500"> *</span>}</label>
                  {ro ? <div className="text-sm text-gray-800 min-h-[1.5rem]">{displayValue(d, cur[d.key]) || <span className="text-gray-300">—</span>}</div>
                    : <CustomFieldInput def={d} value={cur[d.key]} onChange={(v) => set(d.key, v)} />}
                  {d.validation?.help && !ro && <p className="text-[11px] text-gray-400 mt-0.5">{d.validation.help}</p>}
                  {err && dirty && <p className="text-[11px] text-red-600 mt-0.5">{err}</p>}
                </div>);
            })}
          </div>
        </div>))}
      {!!id && mode === "edit" && !title && valQ.data?.can_edit && <button type="button" className="btn-primary !py-1 !px-2.5 text-xs disabled:opacity-50" disabled={!dirty || busy} onClick={save}>{busy ? "Saving…" : "Save custom fields"}</button>}
    </div>
  );
}

/** After creating a NEW record: persist the values collected by a controlled section. Returns the problems, if any. */
export async function saveCustomValues(entity: CfEntity, id: string, values: CfValues, defs: CfDef[]): Promise<string | null> {
  const patch: CfValues = {};
  Object.entries(values || {}).forEach(([k, v]) => { if (v !== undefined && v !== "" && v !== null) patch[k] = v; });
  const problems = defs.filter((d) => d.active && d.can_edit !== false).map((d) => checkValue(d, values[d.key])).filter(Boolean);
  if (problems.length) return problems[0] as string;
  if (!Object.keys(patch).length) return null;
  try { await customFieldsApi.save(entity, id, patch); return null; } catch (e) { return errDetail(e, "Custom fields were not saved"); }
}

export type { CfDef, CfEntity, CfValues };
