import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronLeft, ChevronRight, Clock, AlertTriangle, CalendarDays, List, LayoutGrid, Send } from "lucide-react";
import toast from "react-hot-toast";
import clsx from "clsx";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Modal, inputCls, err } from "../canada/_shared";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { timeApi } from "../../api/endpoints";
import { essTimeApi, AttDay } from "../../api/ess";
import { useAuthStore } from "../../store/authStore";
import { MyTimeRequests, PermissionButton } from "./TimeRequests";

const CODE: Record<string, { label: string; cls: string }> = {
  P: { label: "Present", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  A: { label: "Absent", cls: "bg-rose-50 text-rose-700 border-rose-200" },
  L: { label: "Leave", cls: "bg-violet-50 text-violet-700 border-violet-200" },
  HL: { label: "Half-day leave", cls: "bg-violet-50 text-violet-700 border-violet-200" },
  H: { label: "Holiday", cls: "bg-sky-50 text-sky-700 border-sky-200" },
  WO: { label: "Weekly off", cls: "bg-gray-50 text-gray-500 border-gray-200" },
  "": { label: "Upcoming", cls: "bg-white text-gray-400 border-surface-border" },
};
const EXC: Record<string, string> = { late: "Late in", missing_out: "No out-punch", absent: "Absent" };
const REASONS = [
  { v: "missed_punch", l: "Missed punch" }, { v: "wrong_time", l: "Wrong time recorded" },
  { v: "forgot", l: "Forgot to punch" }, { v: "other", l: "Other (on duty, client visit …)" },
];
const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const addMonth = (m: string, n: number) => { const [y, mo] = m.split("-").map(Number); return ym(new Date(y, mo - 1 + n, 1)); };
const monthLabel = (m: string) => { const [y, mo] = m.split("-").map(Number); return new Date(y, mo - 1, 1).toLocaleDateString("en-CA", { month: "long", year: "numeric" }); };
const nice = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("en-CA", { weekday: "long", day: "numeric", month: "long" });

export default function MyAttendance() {
  const [month, setMonth] = useState(() => ym(new Date()));
  const [view, setView] = useState<"calendar" | "list">("calendar");
  const [sel, setSel] = useState<AttDay | null>(null);
  const [fix, setFix] = useState<AttDay | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["my-attendance", month], queryFn: () => essTimeApi.month(month) });

  const cells = useMemo(() => {
    if (!data) return [];
    const first = new Date(data.start + "T00:00:00");
    const lead = (first.getDay() + 6) % 7;                 // Monday-first
    return [...Array(lead).fill(null), ...data.days];
  }, [data]);
  const k = data?.kpis;

  const cols: GridColumn<AttDay>[] = [
    { key: "date", label: "Date", type: "date" },
    { key: "weekday", label: "Day" },
    { key: "code", label: "Status", text: (r) => CODE[r.code]?.label || r.code, render: (r) => <span className={clsx("px-1.5 py-0.5 rounded border text-[11px] font-semibold", CODE[r.code]?.cls)}>{r.code || "—"}</span> },
    { key: "shift", label: "Shift", text: (r) => r.shift ? `${r.shift.name} ${r.shift.start}-${r.shift.end}` : "" },
    { key: "first_in", label: "First in" },
    { key: "last_out", label: "Last out" },
    { key: "hours", label: "Hours", type: "hours" },
    { key: "late_minutes", label: "Late (min)", type: "number" },
    { key: "exceptions", label: "Exceptions", text: (r) => r.exceptions.map((e) => EXC[e] || e).join(", ") },
    { key: "correction", label: "Correction", text: (r) => r.correction ? r.correction.status : "" },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="My attendance" icon={CalendarClock} subtitle="Your month: present, leave, holidays and weekly offs, with first in and last out"
        actions={<PermissionButton />} />

      <div className="flex flex-wrap items-center gap-2">
        <button className="btn-secondary !px-2" aria-label="Previous month" onClick={() => setMonth(addMonth(month, -1))}><ChevronLeft className="w-4 h-4" /></button>
        <div className="font-display font-semibold text-gray-800 min-w-[9rem] text-center">{monthLabel(month)}</div>
        <button className="btn-secondary !px-2" aria-label="Next month" onClick={() => setMonth(addMonth(month, 1))}><ChevronRight className="w-4 h-4" /></button>
        <div className="ml-auto flex rounded-lg border border-surface-border overflow-hidden text-xs">
          <button className={clsx("px-2.5 py-1.5 flex items-center gap-1", view === "calendar" ? "bg-primary-50 text-primary-700" : "text-gray-500")} onClick={() => setView("calendar")}><LayoutGrid className="w-3.5 h-3.5" />Calendar</button>
          <button className={clsx("px-2.5 py-1.5 flex items-center gap-1", view === "list" ? "bg-primary-50 text-primary-700" : "text-gray-500")} onClick={() => setView("list")}><List className="w-3.5 h-3.5" />List</button>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="Average hours" value={k ? `${k.avg_hours} h` : "—"} sub={k ? `${k.days_present} day(s) worked` : ""} icon={Clock} />
        <Tile label="Late days" value={k?.late_days ?? "—"} sub="after the grace minutes" icon={AlertTriangle} tone="amber" />
        <Tile label="Exception days" value={k?.exception_days ?? "—"} sub="late, no out-punch or absent" icon={AlertTriangle} tone="red" />
        <Tile label="Leave · holidays" value={k ? `${k.leave_days} · ${k.holidays}` : "—"} sub={data?.shift ? `${data.shift.name}` : ""} icon={CalendarDays} />
      </div>

      {isLoading || !data ? <div className="grid place-items-center py-16"><Spinner /></div> : view === "list" ? (
        <DataGrid columns={cols} rows={data.days} rowKey={(r) => r.date} exportName={`my-attendance-${month}`} minWidth={900}
          onRowClick={(r) => setSel(r)} pageSizeDefault={31} initialSort={{ key: "date", dir: "asc" }} />
      ) : (
        <div className="grid lg:grid-cols-3 gap-4">
          <div className="card !p-2 sm:!p-3 lg:col-span-2">
            <div className="grid grid-cols-7 gap-1 text-[10px] sm:text-[11px] text-gray-400 font-semibold uppercase text-center mb-1">
              {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d}>{d}</div>)}
            </div>
            <div className="grid grid-cols-7 gap-1">
              {cells.map((d, i) => d === null ? <div key={`x${i}`} /> : (
                <button key={d.date} onClick={() => setSel(d)} data-testid={`att-${d.date}`}
                  className={clsx("rounded-lg border p-1 sm:p-1.5 text-left min-h-[52px] sm:min-h-[72px] flex flex-col transition-shadow hover:shadow",
                    CODE[d.code]?.cls, sel?.date === d.date && "ring-2 ring-primary-400", d.date === data.today && "outline outline-1 outline-primary-300")}>
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] sm:text-xs font-semibold">{Number(d.date.slice(8))}</span>
                    <span className="text-[9px] sm:text-[10px] font-bold">{d.code}</span>
                  </div>
                  {d.first_in && <span className="hidden sm:block text-[10px] tabular-nums leading-tight mt-auto">{d.first_in}–{d.last_out || "?"}</span>}
                  {d.hours > 0 && <span className="text-[9px] sm:text-[10px] tabular-nums leading-tight">{d.hours}h</span>}
                  {d.exceptions.length > 0 && <span className="h-1.5 w-1.5 rounded-full bg-rose-500 mt-auto" title={d.exceptions.map((e: string) => EXC[e]).join(", ")} />}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-3 text-[10px] text-gray-500">
              {Object.entries(CODE).filter(([c]) => c).map(([c, v]) => <span key={c} className="flex items-center gap-1"><b className={clsx("px-1 rounded border", v.cls)}>{c}</b>{v.label}</span>)}
              <span className="flex items-center gap-1"><span className="h-1.5 w-1.5 rounded-full bg-rose-500" />exception</span>
            </div>
          </div>
          <DayDetail d={sel} onFix={(d) => setFix(d)} />
        </div>
      )}

      <MyTimeRequests />
      {fix && <CorrectionModal day={fix} onClose={() => setFix(null)} />}
    </div>
  );
}

function Tile({ label, value, sub, icon: Icon, tone }: { label: string; value: React.ReactNode; sub?: string; icon: any; tone?: "amber" | "red" }) {
  return (
    <div className="card !p-3">
      <div className="flex items-center justify-between"><span className="text-[10px] uppercase tracking-wider font-semibold text-gray-400">{label}</span>
        <Icon className={clsx("w-4 h-4", tone === "amber" ? "text-amber-400" : tone === "red" ? "text-rose-400" : "text-gray-300")} /></div>
      <div className={clsx("text-xl font-bold tabular-nums mt-1", tone === "amber" ? "text-amber-600" : tone === "red" ? "text-rose-600" : "text-gray-800")}>{value}</div>
      {sub && <div className="text-[11px] text-gray-400 truncate">{sub}</div>}
    </div>
  );
}

function DayDetail({ d, onFix }: { d: AttDay | null; onFix: (d: AttDay) => void }) {
  if (!d) return <div className="card text-sm text-gray-400 grid place-items-center min-h-[10rem]">Pick a day to see its detail.</div>;
  const canFix = !d.future && d.code !== "WO" && d.code !== "H" && !(d.correction && d.correction.status === "pending");
  return (
    <div className="card space-y-3" data-testid="day-detail">
      <div>
        <div className="font-display font-semibold text-gray-800">{nice(d.date)}</div>
        <span className={clsx("inline-block mt-1 px-2 py-0.5 rounded border text-xs font-semibold", CODE[d.code]?.cls)}>{CODE[d.code]?.label || d.code}</span>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
        <dt className="text-gray-400 text-xs">Shift</dt><dd>{d.shift ? `${d.shift.name} · ${d.shift.start}–${d.shift.end}` : "—"}</dd>
        <dt className="text-gray-400 text-xs">First in</dt><dd className="tabular-nums">{d.first_in || "—"}</dd>
        <dt className="text-gray-400 text-xs">Last out</dt><dd className="tabular-nums">{d.last_out || "—"}</dd>
        <dt className="text-gray-400 text-xs">Hours</dt><dd className="tabular-nums">{d.hours} h of {d.planned_hours} h</dd>
        {d.late_minutes > 0 && <><dt className="text-gray-400 text-xs">Late by</dt><dd className="tabular-nums">{d.late_minutes} min</dd></>}
        {d.holiday && <><dt className="text-gray-400 text-xs">Holiday</dt><dd>{d.holiday}</dd></>}
        {d.leave && <><dt className="text-gray-400 text-xs">Leave</dt><dd>{d.leave.type}{d.leave.half ? " (half day)" : ""} · {d.leave.status}</dd></>}
        {d.permission && <><dt className="text-gray-400 text-xs">Permission</dt><dd>{d.permission.from_time}–{d.permission.to_time} · {d.permission.status}</dd></>}
        {d.correction && <><dt className="text-gray-400 text-xs">Correction</dt><dd>{d.correction.status}</dd></>}
      </dl>
      {d.exceptions.length > 0 && <div className="flex flex-wrap gap-1">{d.exceptions.map((e) => <span key={e} className="badge-red">{EXC[e] || e}</span>)}</div>}
      {canFix && <button className="btn-primary w-full flex items-center justify-center gap-1.5" onClick={() => onFix(d)}><Send className="w-3.5 h-3.5" />Request correction</button>}
    </div>
  );
}

export function CorrectionModal({ day, onClose }: { day: AttDay; onClose: () => void }) {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user?.employee_id) || "";
  const [f, setF] = useState({ reason: day.first_in && !day.last_out ? "missed_punch" : "wrong_time",
    requested_in: day.first_in || (day.shift?.start || "09:00"), requested_out: day.last_out || (day.shift?.end || "18:00"), note: "" });
  const [tried, setTried] = useState(false);
  const mins = (t: string) => { const x = /^(\d{1,2}):(\d{2})$/.exec(t || ""); return x ? Number(x[1]) * 60 + Number(x[2]) : NaN; };
  // A night shift (end earlier than start) may legitimately end the next morning.
  const overnight = !!day.shift && mins(day.shift.end) < mins(day.shift.start);
  const needIn = f.reason !== "other";
  const errs = collect({
    work_date: day.future ? "A correction is only for a day that has already started" : null,
    requested_in: needIn ? v.required(f.requested_in, "Correct in time") : null,
    requested_out: !overnight && f.requested_in && f.requested_out && mins(f.requested_out) <= mins(f.requested_in)
      ? "Out time must be after the in time" : null,
    note: v.required(f.note, "What happened") || (f.note.trim().length < 10 ? "Explain what happened in at least 10 characters" : v.maxLen(f.note, 1000, "Note")),
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const send = () => { setTried(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); m.mutate(); };
  const Lb = ({ t, req }: { t: string; req?: boolean }) => <span className="block text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">{t}{req && <Req />}</span>;
  const Hint = ({ t }: { t: string }) => <span className="block text-[11px] text-gray-500 mt-0.5">{t}</span>;
  const m = useMutation({
    mutationFn: () => timeApi.requestReg({ employee_id: me, work_date: day.date, ...f, requested_out: f.requested_out || null, note: f.note.trim() }),
    onSuccess: () => { toast.success("Correction sent to your approver"); qc.invalidateQueries({ queryKey: ["my-attendance"] }); qc.invalidateQueries({ queryKey: ["my-time-requests"] }); onClose(); },
    onError: (e) => toast.error(err(e, "Could not send the correction")),
  });
  return (
    <Modal title="Request an attendance correction" sub={nice(day.date)} onClose={onClose} width="max-w-3xl"
      footer={<div className="flex justify-end gap-2"><button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={m.isPending} onClick={send}>{m.isPending ? "Sending…" : "Send for approval"}</button></div>}>
      <div className="mb-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs" data-testid="correction-summary">
        <div className="rounded-lg border border-surface-border px-2.5 py-1.5"><div className="text-[10px] uppercase text-gray-400">Shift</div>{day.shift ? `${day.shift.name} · ${day.shift.start}–${day.shift.end}` : "No shift"}</div>
        <div className="rounded-lg border border-surface-border px-2.5 py-1.5"><div className="text-[10px] uppercase text-gray-400">Recorded</div>{day.first_in || "no in"} – {day.last_out || "no out"}</div>
        <div className="rounded-lg border border-surface-border px-2.5 py-1.5"><div className="text-[10px] uppercase text-gray-400">Worked / planned</div>{Number(day.hours || 0).toFixed(2)} h / {Number(day.planned_hours || 0).toFixed(2)} h</div>
        <div className="rounded-lg border border-surface-border px-2.5 py-1.5"><div className="text-[10px] uppercase text-gray-400">Day status</div>{day.status || day.code}</div>
      </div>
      <FieldError msg={show("work_date")} />
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block"><Lb t="Reason" req /><select className={inputCls} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })}>{REASONS.map((r) => <option key={r.v} value={r.v}>{r.l}</option>)}</select>
          <Hint t="Why the record is wrong — a missed punch, a wrong time, or a day not captured." /></label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block"><Lb t="Correct in time" req={needIn} /><input type="time" className={inputCls} value={f.requested_in} onChange={(e) => setF({ ...f, requested_in: e.target.value })} />
            <Hint t="The time you actually started (24-hour)." /><FieldError msg={show("requested_in")} /></label>
          <label className="block"><Lb t="Correct out time" /><input type="time" className={inputCls} value={f.requested_out} onChange={(e) => setF({ ...f, requested_out: e.target.value })} />
            <Hint t={overnight ? "Night shift: an out time earlier than in means the next morning." : "Leave blank only if you are still on shift."} /><FieldError msg={show("requested_out")} /></label>
        </div>
        <label className="block sm:col-span-2"><Lb t="What happened" req /><textarea className="input-base text-xs w-full min-h-[80px] p-2" maxLength={1000} value={f.note} placeholder="e.g. Card reader was down at the gate" onChange={(e) => setF({ ...f, note: e.target.value })} />
          <Hint t="Explain what happened so your approver can decide quickly (10–1000 characters)." /><FieldError msg={show("note")} /></label>
      </div>
      <p className="text-[11px] text-gray-400 mt-2">Recorded on screen: {day.first_in || "no in-punch"} – {day.last_out || "no out-punch"}. Your manager (or the approval workflow HR set up) decides; the day is recalculated when approved.</p>
    </Modal>
  );
}
