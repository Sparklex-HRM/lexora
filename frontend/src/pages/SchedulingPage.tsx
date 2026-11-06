import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronLeft, ChevronRight, Send, Trash2, Plus, Users, Layers, Clock, Gauge, Repeat } from "lucide-react";
import { Link } from "react-router-dom";
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from "recharts";
import toast from "react-hot-toast";
import { PageHeader, Spinner, Empty } from "../components/ui";
import { timeApi, type RosterShift } from "../api/endpoints";
import AppModal, { FormSection } from "../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../lib/validators";

const PRIMARY = "#4f46e5", AMBER = "#d97706";
// Local calendar date (YYYY-MM-DD). toISOString() is UTC: in IST (+5:30) local midnight is the
// PREVIOUS day in UTC, which shifted every column of the week by one (Mon showed Sunday's date).
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const mondayOf = (d: Date) => { const x = new Date(d); const dow = (x.getDay() + 6) % 7; x.setDate(x.getDate() - dow); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const key = (eid: string, date: string) => `${eid}|${date}`;
const isPub = (s: RosterShift) => s.status === "published";
const isDraft = (s: RosterShift) => !isPub(s);

// A compact KPI tile.
function Kpi({ label, value, sub, icon: Icon, tone = "default", ring }:
  { label: string; value: React.ReactNode; sub?: string; icon?: any; tone?: "default" | "accent" | "amber"; ring?: number }) {
  const v = { default: "text-gray-800", accent: "text-primary-600", amber: "text-amber-600" }[tone];
  const i = { default: "text-gray-300", accent: "text-primary-400", amber: "text-amber-400" }[tone];
  return (
    <div className="card !p-4">
      <div className="flex items-center justify-between"><span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">{label}</span>{Icon && <Icon className={`w-4 h-4 ${i}`} />}</div>
      <div className="flex items-end gap-2 mt-1.5">
        <span className={`text-2xl font-bold tabular-nums ${v}`}>{value}</span>
        {ring !== undefined && <div className="mb-1 flex-1 h-1.5 rounded-full bg-gray-100 overflow-hidden max-w-[70px]"><div className="h-full rounded-full bg-primary-500" style={{ width: `${Math.min(100, Math.max(0, ring))}%` }} /></div>}
      </div>
      {sub && <div className="text-xs text-gray-500 mt-1 truncate">{sub}</div>}
    </div>
  );
}

export default function SchedulingPage() {
  const qc = useQueryClient();
  const [anchor, setAnchor] = useState(() => mondayOf(new Date()));
  const weekDates = useMemo(() => Array.from({ length: 7 }, (_, i) => iso(addDays(anchor, i))), [anchor]);
  const start = weekDates[0], end = weekDates[6];
  const [edit, setEdit] = useState<{ eid: string; name: string; date: string; shift?: RosterShift } | null>(null);

  const { data, isLoading } = useQuery({ queryKey: ["ta", "roster", start], queryFn: () => timeApi.roster(start, end) });
  const shiftMap = useMemo(() => {
    const m = new Map<string, RosterShift>();
    (data?.shifts || []).forEach((s) => m.set(key(s.employee_id, s.work_date), s));
    return m;
  }, [data]);

  const publish = useMutation({
    mutationFn: () => timeApi.rosterPublish(start, end),
    onSuccess: (r) => { toast.success(`Published ${r.published} shift${r.published !== 1 ? "s" : ""}`); qc.invalidateQueries({ queryKey: ["ta", "roster"] }); },
    onError: () => toast.error("Publish failed"),
  });

  const allRows = data?.shifts || [];
  const shifts = allRows.filter((s: any) => (s.kind || "work") !== "off");   // G2: a rotation's day off is a row, not a shift
  const emps = data?.employees || [];
  const draft = allRows.filter(isDraft).length;
  const published = shifts.filter(isPub).length;
  const hours = Math.round(shifts.reduce((a, s) => a + s.planned_hours, 0) * 10) / 10;
  const scheduledEmps = new Set(shifts.map((s) => s.employee_id)).size;
  const workdayCells = emps.length * 5;                          // Mon–Fri capacity
  const weekdayShifts = shifts.filter((s) => (new Date(s.work_date + "T00:00:00").getDay() + 6) % 7 < 5).length;
  const coverage = workdayCells ? Math.round((weekdayShifts / workdayCells) * 100) : 0;
  const avgShift = shifts.length ? (hours / shifts.length).toFixed(1) : "0";

  // Coverage-by-day chart (published vs draft per weekday)
  const perDay = weekDates.map((d, i) => {
    const day = shifts.filter((s) => s.work_date === d);
    return { label: DOW[i], published: day.filter(isPub).length, draft: day.filter(isDraft).length };
  });
  // Per-employee weekly hours
  const empHours = useMemo(() => {
    const m = new Map<string, number>();
    shifts.forEach((s) => m.set(s.employee_id, (m.get(s.employee_id) || 0) + s.planned_hours));
    return m;
  }, [shifts]);

  const weekLabel = `${new Date(start + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric" })} – ${new Date(end + "T00:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric" })}`;

  return (
    <div>
      <PageHeader title="Scheduling" icon={CalendarClock} subtitle="Weekly shift roster — coverage, hours & publishing"
        actions={
          <div className="flex items-center gap-1.5 flex-wrap">
            <button className="btn-secondary !p-2" onClick={() => setAnchor(addDays(anchor, -7))} aria-label="Previous week"><ChevronLeft className="w-4 h-4" /></button>
            <span className="text-sm font-medium text-gray-700 w-32 text-center tabular-nums">{weekLabel}</span>
            <button className="btn-secondary !p-2" onClick={() => setAnchor(addDays(anchor, 7))} aria-label="Next week"><ChevronRight className="w-4 h-4" /></button>
            <Link to="/scheduling/rotations" className="btn-secondary inline-flex items-center gap-1.5 ml-1" data-testid="open-rotations"><Repeat className="w-4 h-4" />Rotation patterns</Link>
            <button className="btn-primary inline-flex items-center gap-1.5 ml-1" onClick={() => publish.mutate()} disabled={publish.isPending || !draft}><Send className="w-4 h-4" />Publish{draft ? ` (${draft})` : ""}</button>
          </div>
        } />

      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-4">
        <Kpi label="Shifts this week" value={shifts.length} sub={`${scheduledEmps}/${emps.length} employees`} icon={Layers} tone="accent" />
        <Kpi label="Coverage" value={`${coverage}%`} sub="of weekday capacity" icon={Gauge} ring={coverage} />
        <Kpi label="Published" value={published} sub="visible to staff" />
        <Kpi label="Draft" value={draft} sub={draft ? "ready to publish" : "all published"} icon={draft ? Send : undefined} tone={draft ? "amber" : "default"} />
        <Kpi label="Scheduled hours" value={`${hours}h`} sub={`avg ${avgShift}h / shift`} icon={Clock} />
        <Kpi label="On the roster" value={scheduledEmps} sub={`of ${emps.length} staff`} icon={Users} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-4 mb-4">
        <div className="card xl:col-span-1">
          <h3 className="font-display font-semibold text-gray-800 mb-3 text-sm">Coverage by day</h3>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={perDay} margin={{ top: 4, right: 6, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" />
              <XAxis dataKey="label" tick={{ fontSize: 11, fill: "#8890ad" }} />
              <YAxis tick={{ fontSize: 11, fill: "#8890ad" }} allowDecimals={false} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e3e6f0" }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="published" name="Published" stackId="a" fill={PRIMARY} radius={[0, 0, 0, 0]} barSize={18} />
              <Bar dataKey="draft" name="Draft" stackId="a" fill={AMBER} radius={[3, 3, 0, 0]} barSize={18} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="card !p-0 overflow-hidden xl:col-span-3">
          {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
            : !emps.length ? <Empty>No employees to schedule.</Empty>
            : (
              <div className="overflow-x-auto max-h-[560px] overflow-y-auto">
                <table className="w-full text-sm border-collapse min-w-[820px]">
                  <thead className="sticky top-0 z-20">
                    <tr className="bg-surface text-gray-400 text-[11px] uppercase tracking-wide">
                      <th className="px-4 py-2.5 text-left font-semibold sticky left-0 bg-surface z-10 min-w-[170px]">Employee</th>
                      {weekDates.map((d, i) => {
                        const dd = new Date(d + "T00:00:00");
                        const cnt = perDay[i].published + perDay[i].draft;
                        return <th key={d} className={`px-2 py-2 text-center font-semibold ${i >= 5 ? "text-gray-300" : ""}`}>{DOW[i]}<div className="text-[10px] font-normal">{dd.getDate()}</div><div className="text-[9px] font-normal text-primary-400">{cnt || ""}</div></th>;
                      })}
                      <th className="px-3 py-2.5 text-right font-semibold">Hrs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {emps.map((e) => (
                      <tr key={e.employee_id} className="border-t border-surface-border">
                        <td className="px-4 py-2 sticky left-0 bg-surface-card z-10">
                          <div className="font-medium text-gray-800 truncate">{e.name}</div>
                          <div className="text-[11px] text-gray-400 truncate">{e.department || e.employee_number || "—"}</div>
                        </td>
                        {weekDates.map((d, i) => {
                          const sh = shiftMap.get(key(e.employee_id, d));
                          return (
                            <td key={d} className={`px-1.5 py-1.5 text-center ${i >= 5 ? "bg-surface/40" : ""}`}>
                              {sh && (sh as any).kind === "off" ? (
                                <button onClick={() => setEdit({ eid: e.employee_id, name: e.name, date: d, shift: sh })} title={sh.note || "Day off"}
                                  className={`w-full rounded-md px-1.5 py-1 text-[11px] font-semibold leading-tight ${isPub(sh) ? "bg-gray-100 text-gray-500" : "bg-gray-50 text-gray-400 border border-dashed border-gray-300"}`}>
                                  {(sh as any).shift_code === "HOL" ? "Holiday" : "OFF"}<div className="text-[10px] font-normal opacity-70">rotation</div>
                                </button>
                              ) : sh ? (
                                <button onClick={() => setEdit({ eid: e.employee_id, name: e.name, date: d, shift: sh })}
                                  className={`w-full rounded-md px-1.5 py-1 text-[11px] font-medium leading-tight ${isPub(sh) ? "bg-primary-100 text-primary-700" : "bg-amber-50 text-amber-700 border border-dashed border-amber-300"}`}>
                                  {sh.start_time?.slice(0, 5)}–{sh.end_time?.slice(0, 5)}<div className="text-[10px] opacity-70">{sh.planned_hours}h{(sh as any).source === "pattern" ? ` · ${(sh as any).shift_code || "R"}` : (sh as any).source === "swap" ? " · swapped" : ""}</div>
                                </button>
                              ) : (
                                <button onClick={() => setEdit({ eid: e.employee_id, name: e.name, date: d })}
                                  className="w-full rounded-md py-1.5 text-gray-200 hover:text-primary-500 hover:bg-primary-50"><Plus className="w-3.5 h-3.5 mx-auto" /></button>
                              )}
                            </td>
                          );
                        })}
                        <td className="px-3 py-2 text-right tabular-nums font-semibold text-gray-700">{(empHours.get(e.employee_id) || 0) || "—"}{empHours.get(e.employee_id) ? "h" : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </div>
      </div>
      <p className="text-[11px] text-gray-400 mt-1">Indigo = published · dashed amber = draft · grey OFF = a rotation's day off (Rotation patterns generate weeks ahead). Click a cell to add or edit a shift; publish makes the week visible to employees. The header count shows shifts scheduled that day; the right column totals each person's week.</p>

      {edit && <ShiftEditor edit={edit} onClose={() => setEdit(null)} />}
    </div>
  );
}

function ShiftEditor({ edit, onClose }: { edit: { eid: string; name: string; date: string; shift?: RosterShift }; onClose: () => void }) {
  const qc = useQueryClient();
  const wasOff = (edit.shift as any)?.kind === "off";
  const [f, setF] = useState({
    kind: (wasOff ? "off" : "work") as "work" | "off",
    start_time: (!wasOff && edit.shift?.start_time?.slice(0, 5)) || "09:00", end_time: (!wasOff && edit.shift?.end_time?.slice(0, 5)) || "17:00",
    planned_hours: wasOff ? 8 : (edit.shift?.planned_hours ?? 8), location: edit.shift?.location || "", note: edit.shift?.note || "",
  });
  const [tried, setTried] = useState(false);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["ta", "roster"] });
  const toMin = (t: string) => { const m = /^(\d{1,2}):(\d{2})$/.exec(t || ""); return m ? Number(m[1]) * 60 + Number(m[2]) : NaN; };
  const off = f.kind === "off";
  const rawSpan = toMin(f.end_time) - toMin(f.start_time);
  const overnight = Number.isFinite(rawSpan) && rawSpan < 0;
  const spanMin = Number.isFinite(rawSpan) ? (rawSpan < 0 ? rawSpan + 1440 : rawSpan) : NaN;
  const errs = collect(off ? { note: v.maxLen(f.note, 200, "Note") } : {
    start_time: v.required(f.start_time, "Start time"),
    end_time: v.required(f.end_time, "End time") || (spanMin === 0 ? "End time must differ from the start time" : null),
    planned_hours: v.required(f.planned_hours, "Planned hours") || v.range(f.planned_hours, 0, 24, "Planned hours")
      || (Number.isFinite(spanMin) && Number(f.planned_hours) * 60 > spanMin ? "Planned hours cannot be more than the shift span" : null),
    location: v.maxLen(f.location, 120, "Location"),
    note: v.maxLen(f.note, 200, "Note"),
  });
  const show = (k: string) => (tried ? errs[k] : undefined);

  const save = useMutation({
    mutationFn: () => {
      // kind is accepted by /time/roster/assign (2026-10-24); built as a variable so the shared api type stays as it is
      const row = { employee_id: edit.eid, work_date: edit.date, kind: f.kind, start_time: f.start_time, end_time: f.end_time,
        planned_hours: off ? 0 : Number(f.planned_hours), location: f.location.trim() || undefined, note: f.note.trim() || undefined };
      return timeApi.rosterAssign([row]);
    },
    onSuccess: () => { toast.success(off ? "Day off saved" : "Shift saved"); invalidate(); onClose(); },
    onError: (e: any) => toast.error(typeof e?.response?.data?.detail === "string" ? e.response.data.detail : "Save failed"),
  });
  const submit = () => { setTried(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  const Lb = ({ t, req }: { t: string; req?: boolean }) => <span className="block text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">{t}{req && <Req />}</span>;
  const Hint = ({ t }: { t: string }) => <span className="block text-[11px] text-gray-500 mt-0.5">{t}</span>;
  const remove = useMutation({
    mutationFn: () => timeApi.deleteShift(edit.shift!.id),
    onSuccess: () => { toast.success("Shift removed"); invalidate(); onClose(); },
    onError: () => toast.error("Remove failed"),
  });

  const dl = new Date(edit.date + "T00:00:00").toLocaleDateString("en-CA", { weekday: "long", month: "short", day: "numeric" });
  return (
    <AppModal title={edit.name} subtitle={dl} onClose={onClose} size="form"
      footer={<>
        {edit.shift ? <button className="btn-secondary text-xs inline-flex items-center gap-1 text-red-600 mr-auto" onClick={() => remove.mutate()} disabled={remove.isPending}><Trash2 className="w-3.5 h-3.5" />Remove</button> : null}
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={submit} disabled={save.isPending}>{save.isPending ? "Saving…" : off ? "Save day off" : "Save shift"}</button>
      </>}>
      <FormSection title="Day">
        <div className="flex rounded-lg border border-surface-border overflow-hidden text-xs w-fit" role="radiogroup" aria-label="Day type">
          {([["work", "Working day"], ["off", "Day off (weekly off / holiday)"]] as const).map(([k, l]) => (
            <button key={k} type="button" role="radio" aria-checked={f.kind === k} onClick={() => setF({ ...f, kind: k })}
              className={`px-3 py-1.5 ${f.kind === k ? "bg-primary-50 text-primary-700 font-semibold" : "text-gray-600"}`}>{l}</button>))}
        </div>
        <Hint t={off ? "No hours are planned and attendance does not expect a punch that day." : "A hand-set shift replaces whatever a rotation pattern planned for this day."} />
      </FormSection>
      {!off && <FormSection title="Timing">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <label className="block"><Lb t="Start" req /><input type="time" className="input" value={f.start_time} onChange={(e) => setF({ ...f, start_time: e.target.value })} /><FieldError msg={show("start_time")} /></label>
          <label className="block"><Lb t="End" req /><input type="time" className="input" value={f.end_time} onChange={(e) => setF({ ...f, end_time: e.target.value })} />
            <Hint t={overnight ? "Ends the next morning (night shift)." : "An end earlier than the start rolls over to the next day."} /><FieldError msg={show("end_time")} /></label>
          <label className="block"><Lb t="Planned hours" req /><input type="number" step="0.5" min="0" max="24" className="input" value={f.planned_hours} onChange={(e) => setF({ ...f, planned_hours: e.target.value === "" ? ("" as any) : +e.target.value })} />
            <Hint t={Number.isFinite(spanMin) ? `Shift span ${Math.floor(spanMin / 60)} h ${spanMin % 60} min — enter paid hours after the break.` : "Paid working hours after the break."} />
            {Number.isFinite(spanMin) && spanMin > 0 && <button type="button" className="text-[11px] text-primary-600 hover:underline" onClick={() => setF({ ...f, planned_hours: Math.round((spanMin / 60) * 100) / 100 })}>Use full span</button>}
            <FieldError msg={show("planned_hours")} /></label>
        </div>
      </FormSection>}
      <FormSection title="Details">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {!off && <label className="block"><Lb t="Location" /><input className="input" value={f.location} maxLength={120} placeholder="optional" onChange={(e) => setF({ ...f, location: e.target.value })} />
            <Hint t="Site or branch where the employee reports — blank for the usual workplace." /><FieldError msg={show("location")} /></label>}
          <label className={`block ${off ? "sm:col-span-2" : ""}`}><Lb t="Note" /><input className="input" value={f.note} maxLength={200} placeholder={off ? "e.g. Weekly off moved to Tuesday" : "e.g. Covering for Ravi; client site"} onChange={(e) => setF({ ...f, note: e.target.value })} />
            <Hint t="Shown on the roster cell; up to 200 characters." /><FieldError msg={show("note")} /></label>
        </div>
      </FormSection>
    </AppModal>
  );
}
