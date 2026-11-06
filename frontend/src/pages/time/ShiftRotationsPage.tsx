import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { AlertTriangle, CalendarRange, Check, Moon, Plus, Repeat, Settings2, Shuffle, Trash2, Users, Wand2, X } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Field, Input, Modal, Select, Stat, Textarea, useEmployees } from "../common/_shared";
import { FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { RotaDay, RotaGenerate, RotaPattern, RotaShiftDef, rotaApi, rotaErr, SWAP_LABEL, Swap } from "../../api/rotations";
import { useUrlTab } from "../../search/useUrlTab";
import { useUserRole } from "../../store/authStore";
import { addDaysISO, mondayOfISO, todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const HR = ["org_admin", "hr_admin", "hr_manager", "hr_staff", "superadmin"];
const isoToday = () => todayISO();
const nextMonday = () => addDaysISO(mondayOfISO(todayISO()), 7);
const PALETTE = ["#0ea5e9", "#f59e0b", "#6366f1", "#10b981", "#4f46e5", "#ef4444"];

type Tab = "patterns" | "assign" | "generate" | "swaps" | "settings";

/** Rotation calendar: weeks as rows, Mon–Sun columns, coloured shift chips. */
export function RotaCalendar({ days, compact = false }: { days: RotaDay[]; compact?: boolean }) {
  if (!days?.length) return null;
  const first = new Date(days[0].date + "T00:00:00");
  const lead = (first.getDay() + 6) % 7;
  const cells: (RotaDay | null)[] = [...Array(lead).fill(null), ...days];
  const weeks: (RotaDay | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return (
    <div className="overflow-x-auto" data-testid="rota-calendar">
      <table className="w-full min-w-[560px] text-[11px] border-collapse">
        <thead><tr className="text-gray-400 uppercase text-[10px]">{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <th key={d} className="py-1 font-semibold">{d}</th>)}</tr></thead>
        <tbody>{weeks.map((w, i) => (
          <tr key={i}>{Array.from({ length: 7 }, (_, j) => w[j] ?? null).map((c, j) => (
            <td key={j} className="p-0.5 align-top">
              {c ? (
                <div className={`rounded-md px-1.5 ${compact ? "py-0.5" : "py-1"} border ${c.code === "OFF" ? "bg-gray-50 border-gray-200 text-gray-400" : "border-transparent text-white"}`}
                  style={c.code !== "OFF" ? { background: c.color || "#4f46e5" } : undefined} title={`${c.date} ${c.name} ${c.start ? `${c.start}–${c.end}` : ""}`}>
                  <div className="flex items-center justify-between gap-1"><span className="opacity-80">{c.date.slice(8)}</span><b>{c.code}</b>{c.night && <Moon className="w-3 h-3" />}</div>
                  {!compact && <div className="truncate">{c.code === "OFF" ? "Off" : `${c.start}–${c.end}`}</div>}
                </div>) : <div />}
            </td>))}
          </tr>))}</tbody>
      </table>
    </div>
  );
}

export default function ShiftRotationsPage() {
  const role = useUserRole() || "";
  const hr = HR.includes(role);
  const [tab, setTab] = useUrlTab<Tab>("patterns");
  const TABS: { k: Tab; label: string; icon: any }[] = [
    { k: "patterns", label: "Patterns", icon: Repeat }, { k: "assign", label: "Assign people", icon: Users },
    { k: "generate", label: "Generate roster", icon: Wand2 }, { k: "swaps", label: "Swap requests", icon: Shuffle },
    { k: "settings", label: "Rules", icon: Settings2 }];
  return (
    <div>
      <PageHeader title="Shift rotations" icon={Repeat} subtitle="Rotation patterns (3-shift A/B/C, 4-on-2-off, weekly offs, any N-day cycle) → assign people → the roster is generated weeks ahead, respecting leave, holidays, night-shift safeguards and rest."
        actions={<Link to="/scheduling" className="btn-secondary inline-flex items-center gap-1.5"><CalendarRange className="w-4 h-4" />Shifts &amp; roster</Link>} />
      <div className="flex gap-1 mb-4 overflow-x-auto">
        {TABS.map((t) => <button key={t.k} onClick={() => setTab(t.k)} data-testid={`rota-tab-${t.k}`}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ${tab === t.k ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}><t.icon className="w-3.5 h-3.5" />{t.label}</button>)}
      </div>
      {tab === "patterns" && <PatternsTab hr={hr} />}
      {tab === "assign" && <AssignTab hr={hr} />}
      {tab === "generate" && <GenerateTab hr={hr} />}
      {tab === "swaps" && <TeamSwapsTab />}
      {tab === "settings" && <SettingsTab hr={hr} />}
    </div>
  );
}

// ══════════════════════════ patterns ══════════════════════════

function PatternsTab({ hr }: { hr: boolean }) {
  const pats = useQuery({ queryKey: ["rota-patterns"], queryFn: rotaApi.patterns });
  const tpl = useQuery({ queryKey: ["rota-templates"], queryFn: rotaApi.templates });
  const [edit, setEdit] = useState<Partial<RotaPattern> | null>(null);
  const [view, setView] = useState<RotaPattern | null>(null);
  const cols: GridColumn<RotaPattern>[] = [
    { key: "name", label: "Pattern", render: (p) => <span className="font-medium text-gray-800">{p.name}</span>, text: (p) => p.name },
    { key: "cycle_days", label: "Cycle", type: "number", render: (p) => `${p.cycle_days} days` },
    { key: "work_days", label: "Work days", type: "number" },
    { key: "shifts", label: "Shifts", text: (p) => Object.entries(p.shifts).map(([k, v]) => `${k} ${v.start}-${v.end}`).join(", "),
      render: (p) => <span className="flex flex-wrap gap-1">{Object.entries(p.shifts).map(([k, v]) => <span key={k} className="rounded px-1.5 py-0.5 text-[10px] text-white" style={{ background: v.color || "#4f46e5" }}>{k} {v.start}–{v.end}</span>)}</span> },
    { key: "night", label: "Night", render: (p) => (p.night ? <Moon className="w-3.5 h-3.5 text-indigo-500" /> : "—"), text: (p) => (p.night ? "yes" : "no") },
  ];
  return (
    <div className="grid xl:grid-cols-3 gap-4">
      <div className="xl:col-span-2 card !p-0">
        <DataGrid columns={cols} rows={pats.data || []} rowKey={(p) => p.id} exportName="rotation-patterns" minWidth={720} onRowClick={(p) => setView(p)}
          empty={hr ? "No patterns yet — start one from a template on the right." : "HR has not defined any rotation patterns yet."}
          toolbar={hr ? <button className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => setEdit({ name: "", cycle: ["G", "G", "G", "G", "G", "G", "OFF"], shifts: { G: { name: "General", start: "09:00", end: "18:00", break_minutes: 60, color: PALETTE[4] } } })}><Plus className="w-3.5 h-3.5" />Custom pattern</button> : undefined} />
      </div>
      <div className="card">
        <h3 className="font-display font-semibold text-gray-800 mb-2 text-sm">Start from a template</h3>
        {tpl.isLoading ? <Spinner /> : (tpl.data || []).map((t) => (
          <div key={t.key} className="py-2 border-t border-surface-border first:border-t-0">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0"><div className="text-sm font-medium text-gray-800">{t.name}</div><div className="text-[11px] text-gray-500">{t.cycle_days}-day cycle · {t.description}</div></div>
              {hr && <button className="btn-secondary !py-1 text-xs shrink-0" data-testid={`tpl-${t.key}`} onClick={() => setEdit({ name: t.name, template: t.key, cycle: t.cycle, shifts: t.shifts, description: t.description })}>Use</button>}
            </div>
          </div>))}
      </div>
      {edit && <PatternEditor init={edit} onClose={() => setEdit(null)} />}
      {view && <PatternView p={view} hr={hr} onClose={() => setView(null)} onEdit={() => { setEdit(view); setView(null); }} />}
    </div>
  );
}

function PatternView({ p, hr, onClose, onEdit }: { p: RotaPattern; hr: boolean; onClose: () => void; onEdit: () => void }) {
  const qc = useQueryClient();
  const [start, setStart] = useState(nextMonday());
  const [crews, setCrews] = useState(1);
  const [crewOff, setCrewOff] = useState(7);
  const pv = useQuery({ queryKey: ["rota-preview", p.id, start, crews, crewOff], queryFn: () => rotaApi.previewSaved(p.id, { start, days: 56, crews, crew_offset: crewOff }) });
  const del = useMutation({ mutationFn: () => rotaApi.deletePattern(p.id), onSuccess: () => { toast.success("Pattern removed"); qc.invalidateQueries({ queryKey: ["rota-patterns"] }); onClose(); }, onError: (e) => toast.error(rotaErr(e)) });
  return (
    <Modal title={p.name} subtitle={`${p.cycle_days}-day cycle · preview calendar`} onClose={onClose} size="xl"
      footer={hr ? <><button className="btn-secondary text-red-600 inline-flex items-center gap-1" onClick={() => del.mutate()} disabled={del.isPending}><Trash2 className="w-4 h-4" />Remove</button><button className="btn-primary" onClick={onEdit}>Edit pattern</button></> : undefined}>
      <div className="grid sm:grid-cols-3 gap-3 mb-3">
        <Field label="Preview from"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
        <Field label="Crews (coverage check)"><Input type="number" min={1} max={6} value={crews} onChange={(e) => setCrews(Math.max(1, +e.target.value || 1))} /></Field>
        <Field label="Days between crews"><Input type="number" min={0} value={crewOff} onChange={(e) => setCrewOff(Math.max(0, +e.target.value || 0))} /></Field>
      </div>
      {pv.isLoading ? <Spinner /> : <RotaCalendar days={pv.data?.days || []} />}
      {pv.data?.coverage && <CoverageStrip cov={pv.data.coverage} />}
    </Modal>
  );
}

function CoverageStrip({ cov }: { cov: { days: number; per_day: Record<string, number>[] } }) {
  return (
    <div className="mt-3">
      <div className="text-xs font-medium text-gray-600 mb-1">Crews on each shift, per cycle day</div>
      <div className="flex flex-wrap gap-1">{cov.per_day.map((d, i) => (
        <div key={i} className="rounded border border-surface-border px-1.5 py-0.5 text-[10px] text-gray-600" title={`Cycle day ${i + 1}`}>
          <b className="text-gray-400 mr-1">{i + 1}</b>{Object.entries(d).map(([k, v]) => `${k}×${v}`).join(" ")}
        </div>))}</div>
    </div>
  );
}

function PatternEditor({ init, onClose }: { init: Partial<RotaPattern>; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(init.name || "");
  const [code, setCode] = useState(init.code || "");
  const [description, setDescription] = useState(init.description || "");
  const [tried, setTried] = useState(false);
  const [shifts, setShifts] = useState<[string, RotaShiftDef][]>(Object.entries(init.shifts || {}));
  const [cycle, setCycle] = useState<string[]>(init.cycle || ["OFF"]);
  const [start, setStart] = useState(nextMonday());
  const codes = shifts.map(([k]) => k.toUpperCase()).filter(Boolean);
  const shiftObj = useMemo(() => Object.fromEntries(shifts.filter(([k]) => k).map(([k, v]) => [k.toUpperCase(), v])), [shifts]);
  const pv = useQuery({ queryKey: ["rota-preview-draft", JSON.stringify([cycle, shiftObj, start])], queryFn: () => rotaApi.preview({ cycle, shifts: shiftObj, start, days: 42 }), retry: false });
  const toMin = (t: string) => { const m = /^(\d{1,2}):(\d{2})$/.exec(t || ""); return m ? Number(m[1]) * 60 + Number(m[2]) : NaN; };
  /** Paid hours of a shift — same rule as the server (rotation_logic.shift_hours): span (overnight rolls over) minus break. */
  const hoursOf = (s: RotaShiftDef) => { let span = toMin(s.end) - toMin(s.start); if (!Number.isFinite(span)) return NaN; if (span <= 0) span += 1440; return (span - (Number(s.break_minutes) || 0)) / 60; };
  const shiftErr = (): string | null => {
    if (!shifts.length) return "Add at least one shift (code, start and end time)";
    const seen = new Set<string>();
    for (const [i, [k, s]] of shifts.entries()) {
      const c = k.trim().toUpperCase(), n = `Shift ${i + 1}${c ? ` (${c})` : ""}`;
      if (!c) return `${n}: give it a short code (A, B, N …)`;
      if (c === "OFF") return `${n}: OFF is reserved for a day off — use another code`;
      if (seen.has(c)) return `${n}: two shifts share the code ${c}`;
      seen.add(c);
      if (!s.name?.trim()) return `${n}: give it a name`;
      if (s.name.length > 40) return `${n}: name can be at most 40 characters`;
      if (!s.start || !s.end) return `${n}: enter both start and end times`;
      if (s.start === s.end) return `${n}: start and end cannot be the same time`;
      const b = Number(s.break_minutes);
      if (!Number.isInteger(b) || b < 0 || b > 180) return `${n}: break must be a whole number of minutes, 0–180`;
      if (!(hoursOf(s) > 0)) return `${n}: the break is longer than the shift`;
    }
    return null;
  };
  const errs = collect({
    name: v.required(name, "Pattern name") || v.maxLen(name.trim(), 120, "Pattern name"),
    code: v.maxLen(code.trim(), 20, "Short code"),
    shifts: shiftErr(),
    cycle: cycle.every((c) => c === "OFF") ? "The cycle needs at least one working day"
      : (() => { const i = cycle.findIndex((c) => c !== "OFF" && !shiftObj[c]); return i >= 0 ? `Day ${i + 1} uses shift ${cycle[i]}, which is no longer defined` : null; })(),
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const submit = () => { setTried(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  const save = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), code: code.trim() || null, cycle, shifts: shiftObj, template: init.template, description: description.trim() || null };
      return init.id ? rotaApi.updatePattern(init.id, body) : rotaApi.createPattern(body);
    },
    onSuccess: () => { toast.success("Pattern saved"); qc.invalidateQueries({ queryKey: ["rota-patterns"] }); onClose(); },
    onError: (e) => toast.error(rotaErr(e)),
  });
  const setLen = (n: number) => { n = Math.max(1, Math.min(84, n || 1)); setCycle((c) => (n <= c.length ? c.slice(0, n) : [...c, ...Array(n - c.length).fill("OFF")])); };
  const updShift = (i: number, k: keyof RotaShiftDef | "code", v: any) => setShifts((s) => s.map((x, j) => (j !== i ? x : k === "code" ? [String(v).toUpperCase().slice(0, 6), x[1]] : [x[0], { ...x[1], [k]: v }])));
  return (
    <Modal title={init.id ? `Edit ${init.name}` : "New rotation pattern"} subtitle="Define the shifts, then the cycle — day 1 is the assignment's start date (start crews on a Monday for weekly patterns)." onClose={onClose} size="screen"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={save.isPending} onClick={submit} data-testid="save-pattern">Save pattern</button></>}>
      <div className="grid xl:grid-cols-2 gap-5">
        <div>
          <FormSection title="Pattern">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="sm:col-span-2"><Field label={<>Pattern name<Req /></>}><Input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} placeholder="e.g. Plant 3-shift A/B/C" data-testid="pattern-name" /></Field><FieldError msg={show("name")} /></div>
              <div><Field label="Short code" hint="Optional tag for reports, e.g. PLT3"><Input value={code} maxLength={20} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="PLT3" /></Field><FieldError msg={show("code")} /></div>
              <div className="sm:col-span-3"><Field label="Description" hint="Who it is for and how crews rotate — shown to HR when assigning."><Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="e.g. Security crews at Plant 2 — 6 days on, 1 off, weekly change A → B → C" /></Field></div>
            </div>
          </FormSection>
          <FormSection title="Shifts" hint="Code is the short tag on the roster (unique, up to 6 characters). An end earlier than the start rolls over to the next day.">
          <div className="overflow-x-auto">
          <table className="w-full text-xs mt-1 min-w-[640px]"><thead className="text-[10px] uppercase text-gray-400"><tr>{["Code *", "Name *", "Start *", "End *", "Break (min)", "Paid hours", "Colour", ""].map((h) => <th key={h} className="text-left px-1 py-1">{h}</th>)}</tr></thead>
            <tbody>{shifts.map(([k, v], i) => (
              <tr key={i} className="border-t border-surface-border">
                <td className="px-1 py-1"><Input value={k} maxLength={6} onChange={(e) => updShift(i, "code", e.target.value)} className="!w-16" /></td>
                <td className="px-1 py-1"><Input value={v.name} maxLength={40} onChange={(e) => updShift(i, "name", e.target.value)} className="min-w-[7rem]" /></td>
                <td className="px-1 py-1"><Input type="time" value={v.start} onChange={(e) => updShift(i, "start", e.target.value)} className="!w-28" /></td>
                <td className="px-1 py-1"><Input type="time" value={v.end} onChange={(e) => updShift(i, "end", e.target.value)} className="!w-28" /></td>
                <td className="px-1 py-1"><Input type="number" min={0} max={180} step={5} value={v.break_minutes} onChange={(e) => updShift(i, "break_minutes", +e.target.value)} className="!w-20" /></td>
                <td className="px-1 py-1 tabular-nums text-gray-600 whitespace-nowrap">{Number.isFinite(hoursOf(v)) ? `${hoursOf(v).toFixed(2)} h` : "—"}{toMin(v.end) < toMin(v.start) && <Moon className="inline w-3 h-3 ml-1 text-indigo-500" aria-label="ends next day" />}</td>
                <td className="px-1 py-1"><input type="color" value={v.color || PALETTE[i % PALETTE.length]} onChange={(e) => updShift(i, "color", e.target.value)} /></td>
                <td className="px-1 py-1"><button className="icon-btn" onClick={() => setShifts((s) => s.filter((_, j) => j !== i))} title="Remove"><X className="w-3.5 h-3.5" /></button></td>
              </tr>))}</tbody></table>
          </div>
          <FieldError msg={show("shifts")} />
          <button className="btn-secondary !py-1 text-xs mt-1" onClick={() => setShifts((s) => [...s, [String.fromCharCode(65 + s.length), { name: `Shift ${s.length + 1}`, start: "09:00", end: "17:00", break_minutes: 30, color: PALETTE[s.length % PALETTE.length] }]])}>+ Shift</button>
          </FormSection>
          <FormSection title="Cycle" hint="Total days in one repeat. Day 1 falls on each crew's start date.">
          <div className="flex items-end gap-3">
            <Field label="Cycle length (days)"><Input type="number" min={1} max={84} value={cycle.length} onChange={(e) => setLen(+e.target.value)} className="!w-28" data-testid="cycle-length" /></Field>
            <span className="text-[11px] text-gray-500 pb-2">{cycle.filter((c) => c !== "OFF").length} work days · {cycle.filter((c) => c === "OFF").length} off · 1–84 days</span>
          </div>
          <FieldError msg={show("cycle")} />
          <div className="mt-2 grid grid-cols-7 gap-1" data-testid="cycle-grid">
            {cycle.map((c, i) => (
              <label key={i} className="text-[10px] text-gray-400">Day {i + 1}
                <select className="input !py-1 !px-1 text-xs w-full" value={c} onChange={(e) => setCycle((cy) => cy.map((x, j) => (j === i ? e.target.value : x)))}
                  style={c !== "OFF" && shiftObj[c] ? { background: shiftObj[c].color || "#4f46e5", color: "white" } : undefined}>
                  <option value="OFF">OFF</option>{codes.map((k) => <option key={k} value={k}>{k}</option>)}
                  {c !== "OFF" && !codes.includes(c) && <option value={c}>{c} (removed)</option>}
                </select></label>))}
          </div>
          </FormSection>
        </div>
        <div>
          <div className="flex items-end gap-3 mb-2">
            <Field label="Preview from" hint="Only for the preview — not saved with the pattern."><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
            <span className="text-[11px] text-gray-500 pb-2">6 weeks · night shifts marked with a moon</span>
          </div>
          {pv.isError ? <div className="text-xs text-rose-700 bg-rose-50 rounded-lg px-3 py-2">{rotaErr(pv.error)}</div> : pv.isLoading ? <Spinner /> : <RotaCalendar days={pv.data?.days || []} />}
        </div>
      </div>
    </Modal>
  );
}

// ══════════════════════════ assign ══════════════════════════

function AssignTab({ hr }: { hr: boolean }) {
  const qc = useQueryClient();
  const pats = useQuery({ queryKey: ["rota-patterns"], queryFn: rotaApi.patterns });
  const list = useQuery({ queryKey: ["rota-assignments"], queryFn: rotaApi.assignments });
  const employees = useEmployees();
  const [pid, setPid] = useState("");
  const [mode, setMode] = useState<"people" | "team">("people");
  const [picked, setPicked] = useState<string[]>([]);
  const [dept, setDept] = useState("");
  const [q, setQ] = useState("");
  const [f, setF] = useState({ start_date: nextMonday(), end_date: "", offset_days: 0, stagger_days: 0, crew: "" });
  const depts = useMemo(() => [...new Set(employees.map((e: any) => e.department).filter(Boolean))].sort() as string[], [employees]);
  const shown = employees.filter((e: any) => !q || `${e.first_name} ${e.last_name} ${e.employee_number} ${e.department}`.toLowerCase().includes(q.toLowerCase()));
  const save = useMutation({
    mutationFn: () => rotaApi.assign({ pattern_id: pid, ...(mode === "team" ? { department: dept } : { employee_ids: picked }), start_date: f.start_date,
      end_date: f.end_date || undefined, offset_days: f.offset_days, stagger_days: mode === "people" ? f.stagger_days : 0, crew: f.crew || undefined }),
    onSuccess: (r) => { toast.success(`${r.created} assignment(s) made — now generate the roster`); setPicked([]); qc.invalidateQueries({ queryKey: ["rota-assignments"] }); },
    onError: (e) => toast.error(rotaErr(e)),
  });
  const end = useMutation({ mutationFn: (id: string) => rotaApi.endAssignment(id), onSuccess: (r) => { toast.success(`Ended · ${r.removed_planned_shifts} planned shift(s) removed`); qc.invalidateQueries({ queryKey: ["rota-assignments"] }); }, onError: (e) => toast.error(rotaErr(e)) });
  const cols: GridColumn[] = [
    { key: "who", label: "Who", text: (a) => a.employee || `Team: ${a.department}`, render: (a) => a.employee ? <span className="font-medium text-gray-800">{a.employee}<span className="text-gray-400 ml-1">{a.employee_number}</span></span> : <span className="badge-teal">Team · {a.department}</span> },
    { key: "pattern", label: "Pattern" }, { key: "crew", label: "Crew" },
    { key: "start_date", label: "From", type: "date" }, { key: "end_date", label: "To", type: "date" },
    { key: "offset_days", label: "Cycle offset", type: "number" },
    { key: "act", label: "", text: () => "", render: (a) => <button className="btn-secondary !py-0.5 text-[11px]" onClick={(e) => { e.stopPropagation(); end.mutate(a.id); }}>End</button> },
  ];
  return (
    <div className="grid xl:grid-cols-5 gap-4">
      <div className="xl:col-span-2 card space-y-3">
        <h3 className="font-display font-semibold text-gray-800 text-sm">Put people on a pattern</h3>
        <Field label="Pattern"><Select value={pid} onChange={(e) => setPid(e.target.value)} data-testid="assign-pattern"><option value="">Pick a pattern…</option>{(pats.data || []).map((p) => <option key={p.id} value={p.id}>{p.name} ({p.cycle_days} d)</option>)}</Select></Field>
        {hr && <div className="flex gap-3 text-sm"><label className="flex items-center gap-1.5"><input type="radio" checked={mode === "people"} onChange={() => setMode("people")} />Named people</label><label className="flex items-center gap-1.5"><input type="radio" checked={mode === "team"} onChange={() => setMode("team")} />A whole department (crew)</label></div>}
        {mode === "team" ? (
          <Field label="Department" hint="New joiners in the department follow the pattern automatically; a personal assignment overrides it."><Select value={dept} onChange={(e) => setDept(e.target.value)}><option value="">Pick…</option>{depts.map((d) => <option key={d} value={d}>{d}</option>)}</Select></Field>
        ) : (
          <div>
            <Input placeholder="Search your people…" value={q} onChange={(e) => setQ(e.target.value)} />
            <div className="mt-1 max-h-56 overflow-y-auto border border-surface-border rounded-lg divide-y divide-surface-border" data-testid="assign-people">
              {shown.map((e: any) => (
                <label key={e.id} className="flex items-center gap-2 px-2 py-1 text-xs hover:bg-gray-50">
                  <input type="checkbox" checked={picked.includes(e.id)} onChange={(ev) => setPicked((p) => (ev.target.checked ? [...p, e.id] : p.filter((x) => x !== e.id)))} />
                  <span className="font-medium text-gray-800">{e.first_name} {e.last_name}</span><span className="text-gray-400">{e.employee_number} · {e.department || "—"}</span>
                </label>))}
            </div>
            <div className="text-[11px] text-gray-500 mt-1">{picked.length} picked</div>
          </div>)}
        <div className="grid grid-cols-2 gap-3">
          <Field label="Start date"><Input type="date" value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value })} /></Field>
          <Field label="End date (optional)"><Input type="date" value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></Field>
          <Field label="Start on cycle day" hint="0 = day 1 of the cycle"><Input type="number" min={0} value={f.offset_days} onChange={(e) => setF({ ...f, offset_days: Math.max(0, +e.target.value || 0) })} /></Field>
          {mode === "people" && <Field label="Stagger per person (days)" hint="3-shift crews: 7 → A, B, C"><Input type="number" min={0} value={f.stagger_days} onChange={(e) => setF({ ...f, stagger_days: Math.max(0, +e.target.value || 0) })} data-testid="assign-stagger" /></Field>}
          <Field label="Crew label (optional)"><Input value={f.crew} onChange={(e) => setF({ ...f, crew: e.target.value })} placeholder="Crew B" /></Field>
        </div>
        <button className="btn-primary w-full" disabled={save.isPending || !pid || (mode === "team" ? !dept : !picked.length)} onClick={() => save.mutate()} data-testid="assign-save">Assign</button>
      </div>
      <div className="xl:col-span-3 card !p-0">
        <DataGrid columns={cols} rows={list.data || []} rowKey={(a) => a.id} exportName="rotation-assignments" minWidth={760} empty="Nobody is on a rotation pattern yet." />
      </div>
    </div>
  );
}

// ══════════════════════════ generate ══════════════════════════

function GenerateTab({ hr }: { hr: boolean }) {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["rota-settings"], queryFn: rotaApi.settings });
  const [start, setStart] = useState(isoToday());
  const [weeks, setWeeks] = useState<number | "">("");
  const [ovm, setOvm] = useState(false);
  const [ovp, setOvp] = useState(false);
  const [res, setRes] = useState<RotaGenerate | null>(null);
  const run = useMutation({
    mutationFn: (dry: boolean) => rotaApi.generate({ start, weeks: weeks ? +weeks : undefined, dry_run: dry, overwrite_manual: ovm, overwrite_published: ovp }),
    onSuccess: (r) => { setRes(r); if (!r.dry_run) { toast.success(`Roster generated: ${r.created} new, ${r.updated} refreshed, ${r.deleted} removed`); qc.invalidateQueries({ queryKey: ["ta", "roster"] }); } },
    onError: (e) => toast.error(rotaErr(e)),
  });
  const issueCols: GridColumn[] = [{ key: "date", label: "Date", type: "date", width: "110px" }, { key: "name", label: "Employee" }, { key: "reason", label: "Why" }];
  const rowCols: GridColumn[] = [{ key: "work_date", label: "Date", type: "date", width: "110px" }, { key: "name", label: "Employee" },
    { key: "shift_code", label: "Shift" }, { key: "time", label: "Time", text: (r) => (r.kind === "off" ? "—" : `${r.start_time}–${r.end_time}`) },
    { key: "planned_hours", label: "Hours", type: "hours" }, { key: "note", label: "Note" }];
  return (
    <div className="space-y-4">
      <div className="card">
        <div className="grid sm:grid-cols-2 lg:grid-cols-5 gap-3 items-end">
          <Field label="From"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label="Weeks ahead" hint={`Company default ${st.data?.weeks_ahead ?? 4}`}><Input type="number" min={1} max={12} value={weeks} placeholder={String(st.data?.weeks_ahead ?? 4)} onChange={(e) => setWeeks(e.target.value ? +e.target.value : "")} /></Field>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={ovm} onChange={(e) => setOvm(e.target.checked)} />Also replace shifts set by hand / swapped</label>
          {hr ? <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={ovp} onChange={(e) => setOvp(e.target.checked)} />Also replace published shifts (HR)</label> : <span />}
          <div className="flex gap-2">
            <button className="btn-secondary flex-1" disabled={run.isPending} onClick={() => run.mutate(true)} data-testid="rota-preview">Preview</button>
            <button className="btn-primary flex-1" disabled={run.isPending || !res?.dry_run} onClick={() => run.mutate(false)} data-testid="rota-generate">Generate</button>
          </div>
        </div>
        <p className="text-[11px] text-gray-500 mt-2">Preview first: nothing is saved until you press Generate. Generated shifts are drafts — publish them on <Link className="underline" to="/scheduling">Shifts &amp; roster</Link> (the women's night-shift check runs again there). Approved leave and holidays are skipped; a shift that would break the {st.data?.min_rest_hours ?? 11} h company rest rule, or a woman's night shift without a complete safeguards checklist, is not planned and listed below.</p>
      </div>
      {res && (<>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <Stat label={res.dry_run ? "Preview" : "Saved"} value={`${res.start.slice(5)} → ${res.end.slice(5)}`} />
          <Stat label="People" value={res.employees} />
          <Stat label="Work shifts" value={res.work_shifts} accent />
          <Stat label="Days off" value={res.off_days} />
          <Stat label="Not planned" value={res.counts.conflicts} sub="rest / night safeguards" />
          <Stat label="Kept / skipped" value={`${res.counts.kept} / ${res.counts.skipped}`} sub="hand-made · leave" />
        </div>
        {!!res.conflicts.length && <div className="card !p-0" data-testid="rota-conflicts"><div className="px-4 pt-3 text-sm font-medium text-amber-800 flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" />Not planned — arrange these by hand</div>
          <DataGrid columns={issueCols} rows={res.conflicts} rowKey={(r: any) => r.employee_id + r.date} exportName="rota-not-planned" minWidth={640} pageSizeDefault={10} /></div>}
        <div className="grid xl:grid-cols-2 gap-4">
          <div className="card !p-0"><div className="px-4 pt-3 text-sm font-medium text-gray-700">Leave days skipped</div><DataGrid columns={issueCols} rows={res.skipped} rowKey={(r: any) => r.employee_id + r.date} exportName="rota-skipped" minWidth={520} pageSizeDefault={8} empty="No leave in the window." /></div>
          <div className="card !p-0"><div className="px-4 pt-3 text-sm font-medium text-gray-700">Kept as they are</div><DataGrid columns={issueCols} rows={res.kept} rowKey={(r: any) => r.employee_id + r.date} exportName="rota-kept" minWidth={520} pageSizeDefault={8} empty="Nothing hand-made in the window." /></div>
        </div>
        <div className="card !p-0"><div className="px-4 pt-3 text-sm font-medium text-gray-700">{res.dry_run ? "Shifts that will be created" : "Shifts generated"}</div>
          <DataGrid columns={rowCols} rows={res.rows} rowKey={(r: any) => r.employee_id + r.work_date} exportName="rota-shifts" minWidth={760} pageSizeDefault={25} /></div>
      </>)}
    </div>
  );
}

// ══════════════════════════ swaps (manager / HR) ══════════════════════════

export function SwapGrid({ rows, actions }: { rows: Swap[]; actions?: (s: Swap) => React.ReactNode }) {
  const fmt = (s: any) => (s ? `${s.work_date} ${s.start_time}–${s.end_time}` : "—");
  const cols: GridColumn<Swap>[] = [
    { key: "created_at", label: "Asked", type: "date", width: "100px" },
    { key: "requester", label: "Asked by" }, { key: "gives", label: "Gives", text: (s) => fmt(s.requester_shift) },
    { key: "partner", label: "Colleague" }, { key: "takes", label: "Takes", text: (s) => fmt(s.partner_shift) },
    { key: "reason", label: "Reason" },
    { key: "status", label: "Status", text: (s) => SWAP_LABEL[s.status] || s.status, render: (s) => <span className={s.status === "approved" ? "badge-green" : s.status.startsWith("pending") ? "badge-amber" : "badge-red"}>{SWAP_LABEL[s.status] || s.status}</span> },
    { key: "decided_by", label: "Decided by" },
    ...(actions ? [{ key: "act", label: "", text: () => "", render: (s: Swap) => actions(s) } as GridColumn<Swap>] : []),
  ];
  return <DataGrid columns={cols} rows={rows} rowKey={(s) => s.id} exportName="shift-swaps" minWidth={980} empty="No swap requests." />;
}

function TeamSwapsTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["rota-swaps", "team"], queryFn: () => rotaApi.swaps("team") });
  const act = useMutation({ mutationFn: ({ id, a, note }: { id: string; a: "approve" | "reject"; note?: string }) => rotaApi.swapAct(id, a, note),
    onSuccess: (s) => { toast.success(s.status === "approved" ? "Swap approved — the roster now shows it" : "Swap rejected"); qc.invalidateQueries({ queryKey: ["rota-swaps"] }); qc.invalidateQueries({ queryKey: ["ta", "roster"] }); },
    onError: (e) => toast.error(rotaErr(e)) });
  return (
    <div className="card !p-0">
      <p className="text-[11px] text-gray-500 px-4 pt-3">A swap needs the colleague to accept first; then it waits for the manager here and in <Link to="/approvals" className="underline">Approvals</Link> (out-of-office delegates can decide). Rest and night-shift rules are checked again before the roster changes.</p>
      <SwapGrid rows={q.data || []} actions={(s) => s.status === "pending_manager" ? (
        <span className="flex gap-1">
          <button className="btn-primary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1" onClick={() => act.mutate({ id: s.id, a: "approve" })} data-testid="swap-approve"><Check className="w-3 h-3" />Approve</button>
          <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => { const note = window.prompt("Why are you rejecting this swap?") || ""; if (note.trim()) act.mutate({ id: s.id, a: "reject", note }); }}>Reject</button>
        </span>) : null} />
    </div>
  );
}

// ══════════════════════════ settings ══════════════════════════

function SettingsTab({ hr }: { hr: boolean }) {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["rota-settings"], queryFn: rotaApi.settings });
  const [f, setF] = useState<{ min_rest_hours: number; weeks_ahead: number; auto_generate: boolean; swaps_enabled: boolean } | null>(null);
  const s = f || (st.data ? { min_rest_hours: st.data.min_rest_hours, weeks_ahead: st.data.weeks_ahead, auto_generate: st.data.auto_generate, swaps_enabled: st.data.swaps_enabled } : null);
  const save = useMutation({ mutationFn: () => rotaApi.saveSettings(s!), onSuccess: () => { toast.success("Rotation rules saved"); setF(null); qc.invalidateQueries({ queryKey: ["rota-settings"] }); }, onError: (e) => toast.error(rotaErr(e)) });
  if (!s) return <Spinner />;
  return (
    <div className="card max-w-3xl space-y-3">
      <div className="grid sm:grid-cols-2 gap-3">
        <Field label="Minimum rest between two shifts (hours)" hint="0 = no check"><Input type="number" min={0} max={24} step={0.5} value={s.min_rest_hours} disabled={!hr} onChange={(e) => setF({ ...s, min_rest_hours: +e.target.value })} data-testid="rest-hours" /></Field>
        <Field label="Weeks generated ahead"><Input type="number" min={1} max={12} value={s.weeks_ahead} disabled={!hr} onChange={(e) => setF({ ...s, weeks_ahead: +e.target.value })} /></Field>
      </div>
      <p className="text-[11px] text-amber-800 bg-amber-50 rounded-lg px-3 py-2">{st.data?.rest_note}</p>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={s.auto_generate} disabled={!hr} onChange={(e) => setF({ ...s, auto_generate: e.target.checked })} />
        <span>Top up the roster automatically every night<span className="block text-[11px] text-gray-500">Keeps {s.weeks_ahead} weeks of draft shifts ahead from the assignments. Last run: {st.data?.last_auto_run ? new Date(st.data.last_auto_run).toLocaleString("en-CA") : "never"}.</span></span></label>
      <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={s.swaps_enabled} disabled={!hr} onChange={(e) => setF({ ...s, swaps_enabled: e.target.checked })} />
        <span>Employees may ask colleagues to swap published shifts<span className="block text-[11px] text-gray-500">Colleague accepts → manager approves (Approvals → chain "Shift swap").</span></span></label>
      {hr && <div className="flex justify-end"><button className="btn-primary" disabled={!f || save.isPending} onClick={() => save.mutate()}>Save rules</button></div>}
      {!hr && <p className="text-[11px] text-gray-500">Read-only — HR sets these rules.</p>}
    </div>
  );
}
