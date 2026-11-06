import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck, Crosshair, LifeBuoy, MessagesSquare, Plus, Settings, Target, Trash2, TrendingUp, Users } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader } from "../../components/ui";
import { err } from "../hiring/api";
import { Field, Input, Note, Select, Tabs } from "../hiring/ui";
import { perfApi, Row } from "./api";
import Goals from "./Goals";
import Reviews from "./Reviews";
import Cycles from "./Cycles";
import Okrs from "./Okrs";
import Feedback360 from "./Feedback360";
import Pips from "./Pips";
import OneOnOnes from "./OneOnOnes";

const TABS = [
  { key: "goals", label: "Goals", icon: TrendingUp, hr: false },
  { key: "okrs", label: "Objectives & key results", icon: Crosshair, hr: false },
  { key: "reviews", label: "Reviews", icon: ClipboardCheck, hr: false },
  { key: "feedback", label: "360 feedback", icon: Users, hr: false },
  { key: "one-on-ones", label: "1:1s", icon: MessagesSquare, hr: false },
  { key: "improvement-plans", label: "Improvement plans", icon: LifeBuoy, hr: false },
  { key: "cycles", label: "Appraisal cycles", icon: Target, hr: true },
  { key: "settings", label: "Settings", icon: Settings, hr: true },
];

export default function PerformancePage() {
  const { tab = "goals" } = useParams();
  const info = useQuery({ queryKey: ["perf", "settings"], queryFn: perfApi.settings });
  if (info.isLoading) return <div className="text-sm text-gray-400">Loading…</div>;
  if (info.isError || !info.data) return <Note tone="warn">{err(info.error, "Performance could not be loaded.")}</Note>;
  const tabs = TABS.filter((t) => info.data.is_hr || !t.hr);
  if (!tabs.some((t) => t.key === tab)) return <Navigate to="/performance/goals" replace />;
  return (
    <div>
      <PageHeader title="Performance" icon={Target} subtitle="Goals, objectives and key results, reviews, 360 feedback, 1:1s and improvement plans" />
      <Tabs base="/performance" tabs={tabs} />
      {tab === "goals" && <Goals info={info.data} />}
      {tab === "okrs" && <Okrs />}
      {tab === "reviews" && <Reviews info={info.data} />}
      {tab === "feedback" && <Feedback360 />}
      {tab === "one-on-ones" && <OneOnOnes />}
      {tab === "improvement-plans" && <Pips />}
      {tab === "cycles" && <Cycles info={info.data} />}
      {tab === "settings" && <PerfSettings />}
    </div>
  );
}

function PerfSettings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["perf", "settings"], queryFn: perfApi.settings });
  const [f, setF] = useState<Row | null>(null);
  useEffect(() => { if (data && !f) setF(JSON.parse(JSON.stringify(data))); }, [data, f]);
  const save = useMutation({
    mutationFn: () => perfApi.saveSettings({ rating_scale: f!.rating_scale, competencies: f!.competencies, goal_weight: f!.goal_weight, competency_weight: f!.competency_weight,
      guideline: (f!.guideline || []).filter((g: Row) => g.label && g.pct !== "" && g.pct != null), increment_matrix: f!.increment_matrix }),
    onSuccess: () => { toast.success("Performance settings saved. Cycles already launched keep the scale they were launched with."); qc.invalidateQueries({ queryKey: ["perf"] }); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  if (!f) return <div className="text-sm text-gray-400">Loading…</div>;
  const ro = !f.can_edit;
  const upd = (key: string, i: number, k: string, v: any) => setF({ ...f, [key]: f[key].map((r: Row, j: number) => (j === i ? { ...r, [k]: v } : r)) });
  const add = (key: string, row: any) => setF({ ...f, [key]: [...(f[key] || []), row] });
  const del = (key: string, i: number) => setF({ ...f, [key]: f[key].filter((_: any, j: number) => j !== i) });
  const labels: string[] = (f.rating_scale || []).map((s: Row) => s.label).filter(Boolean);
  const gsum = (f.guideline || []).reduce((s: number, g: Row) => s + (Number(g.pct) || 0), 0);
  const rm = (key: string, i: number) => !ro && <button className="icon-btn" title="Remove" onClick={() => del(key, i)}><Trash2 className="w-3.5 h-3.5 text-gray-400" /></button>;
  const more = (key: string, row: any, label: string) => !ro && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1 mt-2" onClick={() => add(key, row)}><Plus className="w-3 h-3" />{label}</button>;
  return (
    <div>
      {ro && <Note>Only an HR head or an org admin can change these settings.</Note>}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Rating scale</h3>
          <p className="text-xs text-gray-500 mb-2">Your own labels and numbers. The highest number is the best rating.</p>
          {(f.rating_scale as Row[]).map((s, i) => (
            <div key={i} className="flex gap-2 mb-1.5 items-center"><Input className="!w-20" type="number" step="0.5" disabled={ro} value={s.value} onChange={(e) => upd("rating_scale", i, "value", e.target.value)} aria-label="Number" />
              <Input disabled={ro} value={s.label} onChange={(e) => upd("rating_scale", i, "label", e.target.value)} aria-label="Label" />{rm("rating_scale", i)}</div>))}
          {more("rating_scale", { label: "", value: "" }, "Add a level")}
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Competencies</h3>
          <p className="text-xs text-gray-500 mb-2">Each one is rated on the scale in the self review and the manager review.</p>
          {(f.competencies as string[]).map((c, i) => (
            <div key={i} className="flex gap-2 mb-1.5 items-center"><Input disabled={ro} value={c} onChange={(e) => setF({ ...f, competencies: f.competencies.map((x: string, j: number) => (j === i ? e.target.value : x)) })} />{rm("competencies", i)}</div>))}
          {more("competencies", "", "Add a competency")}
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Overall score formula</h3>
          <p className="text-xs text-gray-500 mb-3">Overall = (goal score x goal weight + competency score x competency weight) / (goal weight + competency weight). The review screen shows this sum line by line.</p>
          <div className="grid grid-cols-2 gap-3 max-w-md">
            <Field label="Goal weight %"><Input type="number" min={0} max={100} disabled={ro} value={f.goal_weight} onChange={(e) => setF({ ...f, goal_weight: e.target.value })} /></Field>
            <Field label="Competency weight %"><Input type="number" min={0} max={100} disabled={ro} value={f.competency_weight} onChange={(e) => setF({ ...f, competency_weight: e.target.value })} /></Field>
          </div>
        </div>
        <div className="card">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Guideline distribution (optional)</h3>
          <p className="text-xs text-gray-500 mb-2">The share of people you expect at each rating. Calibration compares the real spread with it. Leave empty for no guideline. {!!(f.guideline || []).length && <b className={gsum === 100 ? "text-green-600" : "text-amber-700"}>Adds up to {gsum} of 100.</b>}</p>
          {(f.guideline as Row[]).map((g, i) => (
            <div key={i} className="flex gap-2 mb-1.5 items-center"><Select disabled={ro} value={g.label} onChange={(e) => upd("guideline", i, "label", e.target.value)}><option value="">Pick a rating</option>{labels.map((l) => <option key={l}>{l}</option>)}</Select>
              <Input className="!w-24" type="number" disabled={ro} value={g.pct} onChange={(e) => upd("guideline", i, "pct", e.target.value)} aria-label="Percent" /><span className="text-xs text-gray-400">%</span>{rm("guideline", i)}</div>))}
          {more("guideline", { label: "", pct: "" }, "Add a row")}
        </div>
        <div className="card xl:col-span-2">
          <h3 className="font-display font-semibold text-gray-800 mb-1">Increment matrix</h3>
          <p className="text-xs text-gray-500 mb-2">Rating to increase % range. Leave the grade empty for a row that applies to every grade; a row with a grade wins for that grade. The increment proposal pre-fills the default % and lets HR edit inside the range. Outside the range needs a reason.</p>
          {!(f.increment_matrix || []).length && <div className="text-sm text-gray-400 mb-2">No rows yet. Add one row per rating you give an increase for.</div>}
          {(f.increment_matrix as Row[]).map((m, i) => (
            <div key={i} className="grid grid-cols-2 sm:grid-cols-[1fr_8rem_6rem_6rem_6rem_2rem] gap-2 mb-1.5 items-end">
              <Field label={i ? "" : "Rating"}><Select disabled={ro} value={m.band || ""} onChange={(e) => upd("increment_matrix", i, "band", e.target.value)}><option value="">Pick a rating</option>{labels.map((l) => <option key={l}>{l}</option>)}</Select></Field>
              <Field label={i ? "" : "Grade (optional)"}><Input disabled={ro} value={m.grade || ""} onChange={(e) => upd("increment_matrix", i, "grade", e.target.value)} /></Field>
              <Field label={i ? "" : "Minimum %"}><Input type="number" step="0.5" disabled={ro} value={m.min_pct ?? ""} onChange={(e) => upd("increment_matrix", i, "min_pct", e.target.value)} /></Field>
              <Field label={i ? "" : "Maximum %"}><Input type="number" step="0.5" disabled={ro} value={m.max_pct ?? ""} onChange={(e) => upd("increment_matrix", i, "max_pct", e.target.value)} /></Field>
              <Field label={i ? "" : "Default %"}><Input type="number" step="0.5" placeholder="middle" disabled={ro} value={m.default_pct ?? ""} onChange={(e) => upd("increment_matrix", i, "default_pct", e.target.value)} /></Field>
              <div className="pb-1">{rm("increment_matrix", i)}</div>
            </div>))}
          {more("increment_matrix", { band: "", grade: "", min_pct: "", max_pct: "", default_pct: "" }, "Add a row")}
        </div>
      </div>
      {!ro && <div className="mt-4 flex justify-end"><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={() => save.mutate()}>Save settings</button></div>}
    </div>
  );
}
