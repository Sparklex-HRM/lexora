import { useState, useMemo } from "react";
import toast from "react-hot-toast";
import { Plus, Pencil, Trash2, X, ChevronLeft, ChevronRight, LucideIcon } from "lucide-react";
import { PageHeader, Spinner, Empty, StatTile } from "./ui";
import AppModal from "./Modal";

export interface Field {
  k: string; label: string;
  type?: "text" | "number" | "date" | "select" | "textarea" | "money";
  options?: string[]; required?: boolean; placeholder?: string;
}
export interface CollectionConfig {
  title: string; subtitle?: string; icon: LucideIcon; storageKey: string;
  fields: Field[];
  stages?: string[];          // present → kanban by `stage`
  seed?: any[];
  addLabel?: string;
  note?: string;
}

let _uid = 1;
const newId = () => `c${Date.now().toString(36)}${_uid++}`;
const fmtVal = (f: Field, v: any) => {
  if (v === undefined || v === null || v === "") return "—";
  if (f.type === "money") return `$${Number(v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (f.type === "date") { const d = new Date(v); return isNaN(d.getTime()) ? v : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); }
  return String(v);
};

function useCollection(cfg: CollectionConfig) {
  const [items, setItems] = useState<any[]>(() => {
    try {
      const raw = localStorage.getItem(cfg.storageKey);
      if (raw) return JSON.parse(raw);
    } catch { /* ignore */ }
    const seeded = (cfg.seed || []).map((s) => ({ id: newId(), ...s }));
    try { localStorage.setItem(cfg.storageKey, JSON.stringify(seeded)); } catch { /* ignore */ }
    return seeded;
  });
  const persist = (next: any[]) => { setItems(next); try { localStorage.setItem(cfg.storageKey, JSON.stringify(next)); } catch { /* ignore */ } };
  return { items, persist };
}

export default function CollectionPage({ cfg }: { cfg: CollectionConfig }) {
  const { items, persist } = useCollection(cfg);
  const [editing, setEditing] = useState<any | null>(null);
  const [creating, setCreating] = useState(false);

  const save = (row: any) => {
    if (!row.id) persist([{ id: newId(), stage: cfg.stages?.[0], ...row }, ...items]);
    else persist(items.map((i) => (i.id === row.id ? { ...i, ...row } : i)));
    setEditing(null); setCreating(false);
    toast.success(row.id ? "Saved" : "Added");
  };
  const remove = (id: string) => { if (window.confirm("Delete this item?")) persist(items.filter((i) => i.id !== id)); };
  const moveStage = (id: string, dir: number) => {
    if (!cfg.stages) return;
    persist(items.map((i) => {
      if (i.id !== id) return i;
      const idx = Math.max(0, Math.min(cfg.stages!.length - 1, cfg.stages!.indexOf(i.stage) + dir));
      return { ...i, stage: cfg.stages![idx] };
    }));
  };

  const stats = useMemo(() => {
    if (cfg.stages) return cfg.stages.map((s) => ({ label: s, value: items.filter((i) => (i.stage || cfg.stages![0]) === s).length }));
    const moneyField = cfg.fields.find((f) => f.type === "money");
    const out: { label: string; value: any }[] = [{ label: "Total", value: items.length }];
    if (moneyField) out.push({ label: `Total ${moneyField.label.toLowerCase()}`, value: fmtVal(moneyField, items.reduce((s, i) => s + (Number(i[moneyField.k]) || 0), 0)) });
    return out;
  }, [items, cfg]);

  const title = cfg.fields[0].k;

  return (
    <div>
      <PageHeader title={cfg.title} icon={cfg.icon} subtitle={cfg.subtitle}
        actions={<button className="btn-primary text-xs flex items-center gap-1.5" onClick={() => setCreating(true)}><Plus className="w-3.5 h-3.5" /> {cfg.addLabel || "Add"}</button>} />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        {stats.slice(0, 6).map((s, i) => <StatTile key={s.label} label={s.label} value={s.value} accent={i === stats.length - 1 && !cfg.stages} />)}
      </div>

      {items.length === 0 ? <div className="card"><Empty>Nothing here yet — add your first {cfg.title.toLowerCase()} item.</Empty></div>
        : cfg.stages ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-5">
            {cfg.stages.map((stage) => {
              const col = items.filter((i) => (i.stage || cfg.stages![0]) === stage);
              return (
                <div key={stage} className="bg-surface rounded-xl p-2 min-h-[120px]">
                  <div className="flex items-center justify-between px-1.5 py-1 mb-1">
                    <span className="text-xs font-semibold text-gray-600">{stage}</span>
                    <span className="text-[10px] text-gray-400">{col.length}</span>
                  </div>
                  <div className="space-y-2">
                    {col.map((i) => (
                      <div key={i.id} className="bg-white rounded-lg border border-surface-border p-2.5 group">
                        <div className="text-sm font-medium text-gray-800 leading-snug">{i[title] || "—"}</div>
                        {cfg.fields.slice(1, 3).map((f) => i[f.k] && <div key={f.k} className="text-[11px] text-gray-400 mt-0.5">{f.label}: {fmtVal(f, i[f.k])}</div>)}
                        <div className="flex items-center justify-between mt-2 pt-1.5 border-t border-surface-border">
                          <div className="flex gap-0.5">
                            <button onClick={() => moveStage(i.id, -1)} disabled={cfg.stages!.indexOf(i.stage) <= 0} className="icon-btn text-gray-300 hover:text-primary-600 disabled:opacity-30"><ChevronLeft className="w-3.5 h-3.5" /></button>
                            <button onClick={() => moveStage(i.id, 1)} disabled={cfg.stages!.indexOf(i.stage) >= cfg.stages!.length - 1} className="icon-btn text-gray-300 hover:text-primary-600 disabled:opacity-30"><ChevronRight className="w-3.5 h-3.5" /></button>
                          </div>
                          <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button onClick={() => setEditing(i)} className="icon-btn text-gray-300 hover:text-primary-600"><Pencil className="w-3.5 h-3.5" /></button>
                            <button onClick={() => remove(i.id)} className="icon-btn text-gray-300 hover:text-red-500"><Trash2 className="w-3.5 h-3.5" /></button>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="card !p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                  <tr>
                    {cfg.fields.map((f) => <th key={f.k} className={`px-4 py-2.5 font-semibold ${f.type === "money" || f.type === "number" ? "text-right" : ""}`}>{f.label}</th>)}
                    <th className="px-4 py-2.5 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr key={i.id} className="border-t border-surface-border hover:bg-gray-50">
                      {cfg.fields.map((f, fi) => (
                        <td key={f.k} className={`px-4 py-2.5 ${f.type === "money" || f.type === "number" ? "text-right tabular-nums text-gray-600" : "text-gray-700"} ${fi === 0 ? "font-medium text-gray-800" : ""}`}>
                          {f.type === "select" && i[f.k] ? <span className="badge-teal">{i[f.k]}</span> : fmtVal(f, i[f.k])}
                        </td>
                      ))}
                      <td className="px-4 py-2.5 text-right">
                        <div className="flex items-center justify-end gap-0.5">
                          <button onClick={() => setEditing(i)} className="icon-btn text-gray-300 hover:text-primary-600"><Pencil className="w-4 h-4" /></button>
                          <button onClick={() => remove(i.id)} className="icon-btn text-gray-300 hover:text-red-500"><Trash2 className="w-4 h-4" /></button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

      {cfg.note && <p className="text-[11px] text-gray-400 mt-3">{cfg.note}</p>}

      {(creating || editing) && (
        <EditModal cfg={cfg} row={editing} onSave={save} onClose={() => { setEditing(null); setCreating(false); }} />
      )}
    </div>
  );
}

function EditModal({ cfg, row, onSave, onClose }: { cfg: CollectionConfig; row: any | null; onSave: (r: any) => void; onClose: () => void }) {
  const [f, setF] = useState<any>(row ? { ...row } : {});
  const set = (k: string, v: any) => setF((p: any) => ({ ...p, [k]: v }));
  const submit = () => {
    for (const fld of cfg.fields) if (fld.required && !f[fld.k]) return toast.error(`${fld.label} is required`);
    onSave(f);
  };
  return (
    <AppModal title={`${row ? "Edit" : "New"} · ${cfg.title}`} onClose={onClose} size="form"
      footer={<><button onClick={onClose} className="btn-secondary text-xs">Cancel</button><button onClick={submit} className="btn-primary text-xs">{row ? "Save" : "Add"}</button></>}>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-4 gap-y-3">
        {cfg.fields.map((fld) => (
          <div key={fld.k} className={fld.type === "textarea" ? "sm:col-span-2 lg:col-span-3" : ""}>
            <label className="block text-xs font-semibold text-gray-500 mb-1">{fld.label}{fld.required && <span className="text-red-400"> *</span>}</label>
            {fld.type === "select" ? (
              <select className="input" value={f[fld.k] || ""} onChange={(e) => set(fld.k, e.target.value)}>
                <option value="">—</option>{fld.options!.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : fld.type === "textarea" ? (
              <textarea className="input min-h-[80px]" value={f[fld.k] || ""} placeholder={fld.placeholder} onChange={(e) => set(fld.k, e.target.value)} />
            ) : (
              <input className="input" type={fld.type === "money" ? "number" : fld.type || "text"} step={fld.type === "money" ? "0.01" : undefined}
                value={f[fld.k] ?? ""} placeholder={fld.placeholder} onChange={(e) => set(fld.k, fld.type === "number" || fld.type === "money" ? (e.target.value === "" ? "" : parseFloat(e.target.value)) : e.target.value)} />
            )}
          </div>
        ))}
        {cfg.stages && (
          <div>
            <label className="block text-xs font-semibold text-gray-500 mb-1">Stage</label>
            <select className="input" value={f.stage || cfg.stages[0]} onChange={(e) => set("stage", e.target.value)}>
              {cfg.stages.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        )}
      </div>
    </AppModal>
  );
}

// Tiny helper so page files stay one-liners.
export const spinnerBlock = <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
