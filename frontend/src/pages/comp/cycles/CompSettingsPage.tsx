import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Plus, Settings, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader } from "../../../components/ui";
import { err } from "../../hiring/api";
import { Field, Input, Note, Select, Textarea } from "../../hiring/ui";
import { ccApi, Row } from "./api";

/* Defaults a new cycle starts from: compa bands, grade midpoints, merit matrix, budget rules. */
export default function CompSettingsPage() {
  const qc = useQueryClient();
  const { data, isError, error } = useQuery({ queryKey: ["comp", "settings"], queryFn: ccApi.settings });
  const [f, setF] = useState<Row | null>(null);
  useEffect(() => { if (data && !f) setF(JSON.parse(JSON.stringify(data))); }, [data, f]);
  const save = useMutation({
    mutationFn: () => ccApi.saveSettings({ compa_bands: f!.compa_bands, default_matrix: f!.default_matrix, budget_mode: f!.budget_mode,
      default_budget_pct: f!.default_budget_pct, allow_lump_sum: f!.allow_lump_sum, guidelines: f!.guidelines,
      grade_midpoints: Object.fromEntries(Object.entries(f!.grade_midpoints || {}).filter(([k, v]) => k && v !== "")) }),
    onSuccess: () => { toast.success("Compensation settings saved. Cycles already created keep the rules they were created with."); qc.invalidateQueries({ queryKey: ["comp", "settings"] }); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  if (isError) return <Note tone="warn">{err(error, "Settings could not be loaded.")}</Note>;
  if (!f) return <div className="text-sm text-gray-400">Loading…</div>;
  const ro = !f.can_edit;
  const upd = (key: string, i: number, k: string, v: any) => setF({ ...f, [key]: f[key].map((r: Row, j: number) => (j === i ? { ...r, [k]: v } : r)) });
  const add = (key: string, row: any) => setF({ ...f, [key]: [...(f[key] || []), row] });
  const del = (key: string, i: number) => setF({ ...f, [key]: f[key].filter((_: any, j: number) => j !== i) });
  const rm = (key: string, i: number) => !ro && <button className="icon-btn" title="Remove" onClick={() => del(key, i)}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>;
  const more = (key: string, row: any, label: string) => !ro && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-2" onClick={() => add(key, row)}><Plus className="w-3 h-3" />{label}</button>;
  const bandKeys: string[] = (f.compa_bands || []).map((b: Row) => b.key).filter(Boolean);
  const mids = Object.entries(f.grade_midpoints || {});
  return (
    <div>
      <PageHeader title="Compensation settings" icon={Settings} subtitle="Default merit matrix, compa-ratio bands, grade midpoints and budget rules. A new cycle starts from these and freezes its own copy."
        actions={<Link to="/comp/cycles" className="btn-secondary !py-1.5 text-xs inline-flex items-center gap-1"><ArrowLeft className="w-3.5 h-3.5" />Cycles</Link>} />
      {ro && <Note>Only an HR head or an org admin can change these settings.</Note>}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Compa-ratio bands</h3>
          <p className="text-xs text-gray-500 mb-2">Compa-ratio = current annual pay ÷ grade midpoint. Lower bound inclusive, upper bound exclusive; leave a bound empty for open-ended.</p>
          {(f.compa_bands as Row[]).map((b, i) => (
            <div key={i} className="grid grid-cols-[6rem_1fr_6rem_6rem_2rem] gap-2 mb-1.5 items-center">
              <Input disabled={ro} value={b.key} onChange={(e) => upd("compa_bands", i, "key", e.target.value)} placeholder="key" />
              <Input disabled={ro} value={b.label || ""} onChange={(e) => upd("compa_bands", i, "label", e.target.value)} placeholder="Label" />
              <Input type="number" step="0.01" disabled={ro} value={b.min_cr ?? ""} onChange={(e) => upd("compa_bands", i, "min_cr", e.target.value === "" ? null : e.target.value)} placeholder="from" />
              <Input type="number" step="0.01" disabled={ro} value={b.max_cr ?? ""} onChange={(e) => upd("compa_bands", i, "max_cr", e.target.value === "" ? null : e.target.value)} placeholder="to" />
              {rm("compa_bands", i)}
            </div>))}
          {more("compa_bands", { key: "", label: "", min_cr: null, max_cr: null }, "Add a band")}
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Grade midpoints ($ per year)</h3>
          <p className="text-xs text-gray-500 mb-2">One midpoint per grade. A grade without a midpoint has no compa-ratio: the matrix then uses the rating's any-band row.</p>
          {mids.map(([g, v], i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_2rem] gap-2 mb-1.5 items-center">
              <Input disabled={ro} value={g} onChange={(e) => { const o = Object.entries(f.grade_midpoints); o[i] = [e.target.value, v]; setF({ ...f, grade_midpoints: Object.fromEntries(o) }); }} placeholder="Grade" />
              <Input type="number" disabled={ro} value={v as any} onChange={(e) => setF({ ...f, grade_midpoints: { ...f.grade_midpoints, [g]: e.target.value } })} placeholder="Midpoint" />
              {!ro && <button className="icon-btn" title="Remove" onClick={() => { const o = { ...f.grade_midpoints }; delete o[g]; setF({ ...f, grade_midpoints: o }); }}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>}
            </div>))}
          {!ro && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-2" onClick={() => setF({ ...f, grade_midpoints: { ...f.grade_midpoints, "": "" } })}><Plus className="w-3 h-3" />Add a grade</button>}
        </div>
        <div className="card xl:col-span-2">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Default merit matrix</h3>
          <p className="text-xs text-gray-500 mb-2">Rating (exactly as published by the appraisal) × compa-ratio band → recommended % with a minimum and a maximum. Empty band = applies to every band.</p>
          {!(f.default_matrix || []).length && <div className="text-sm text-gray-400 mb-2">No rows yet. Add one row per rating you give an increase for.</div>}
          {(f.default_matrix as Row[]).map((m, i) => (
            <div key={i} className="grid grid-cols-2 sm:grid-cols-[1fr_8rem_6rem_6rem_6rem_2rem] gap-2 mb-1.5 items-end">
              <Field label={i ? "" : "Rating"}><Input disabled={ro} value={m.rating || ""} onChange={(e) => upd("default_matrix", i, "rating", e.target.value)} /></Field>
              <Field label={i ? "" : "Compa band"}><Select disabled={ro} value={m.cr_band || ""} onChange={(e) => upd("default_matrix", i, "cr_band", e.target.value)}><option value="">Any band</option>{bandKeys.map((k) => <option key={k} value={k}>{k}</option>)}</Select></Field>
              <Field label={i ? "" : "Min %"}><Input type="number" step="0.5" disabled={ro} value={m.min_pct ?? ""} onChange={(e) => upd("default_matrix", i, "min_pct", e.target.value)} /></Field>
              <Field label={i ? "" : "Recommended %"}><Input type="number" step="0.5" disabled={ro} value={m.rec_pct ?? ""} onChange={(e) => upd("default_matrix", i, "rec_pct", e.target.value)} /></Field>
              <Field label={i ? "" : "Max %"}><Input type="number" step="0.5" disabled={ro} value={m.max_pct ?? ""} onChange={(e) => upd("default_matrix", i, "max_pct", e.target.value)} /></Field>
              <div className="pb-1">{rm("default_matrix", i)}</div>
            </div>))}
          {more("default_matrix", { rating: "", cr_band: "", min_pct: "", rec_pct: "", max_pct: "" }, "Add a row")}
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Budget rules</h3>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Budget limit"><Select disabled={ro} value={f.budget_mode} onChange={(e) => setF({ ...f, budget_mode: e.target.value })}><option value="soft">Soft (flag only)</option><option value="hard">Hard (refuse)</option></Select></Field>
            <Field label="Default budget % of base"><Input type="number" step="0.5" disabled={ro} value={f.default_budget_pct ?? ""} onChange={(e) => setF({ ...f, default_budget_pct: e.target.value })} /></Field>
          </div>
          <label className="flex items-center gap-2 text-sm mt-3"><input type="checkbox" disabled={ro} checked={!!f.allow_lump_sum} onChange={(e) => setF({ ...f, allow_lump_sum: e.target.checked })} />Managers may propose one-time lump sums</label>
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Default guidelines for managers</h3>
          <Textarea rows={5} disabled={ro} value={f.guidelines || ""} onChange={(e) => setF({ ...f, guidelines: e.target.value })} placeholder="Shown on every manager worksheet. For example: stay inside the matrix; use the comment box for anything outside." />
        </div>
      </div>
      {!ro && <div className="mt-4 flex justify-end"><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={() => save.mutate()}>Save settings</button></div>}
    </div>
  );
}
