import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Coffee, LogIn, LogOut, Timer } from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import DataGrid, { type GridColumn } from "../components/DataGrid";
import { timeApi } from "../api/endpoints";
import { errText } from "../api/platform";
import { useAuthStore } from "../store/authStore";
import { addDaysISO, dateInZone, todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

type Dir = "in" | "out" | "break_start" | "break_end";
const DIR_LABEL: Record<string, string> = { in: "Clock in", out: "Clock out", break_start: "Break started", break_end: "Break ended" };
const SOURCE: Record<string, string> = { web: "This screen", kiosk: "Kiosk", mobile: "Mobile", admin: "Entered by HR", biometric: "Biometric device" };
const DAY_STATE: Record<string, string> = { present: "Present", late: "Late", on_shift: "On shift", absent: "Absent", leave: "On leave", holiday: "Holiday", rest_day: "Rest day", scheduled: "Scheduled" };

const clock = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "—");

/** Punch in / out for the signed-in person, through the existing time service. */
export default function TimeClockPage() {
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const me = user?.employee_id || "";
  const [now, setNow] = useState(new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  const today = todayISO(undefined, now);                      // the org's calendar day
  const start = useMemo(() => addDaysISO(today, -13), [today]);

  // The server files a punch under its LOCAL (org-zone) work date; rows written before that fix sit under
  // the UTC date, which can be the day before or after near midnight in the org's zone — so read both and keep today's punches.
  const punches = useQuery({
    queryKey: ["ts", "clock", me, today], enabled: !!me,
    queryFn: async () => {
      const days = [today, addDaysISO(today, -1)];
      const seen = new Set<string>();
      const all = (await Promise.all(days.map((d) => timeApi.punches(me, d)))).flat().filter((p: any) => !seen.has(p.id) && !!seen.add(p.id));
      return all.filter((p: any) => dateInZone(p.at) === today).sort((a: any, b: any) => a.at.localeCompare(b.at));
    },
  });
  const history = useQuery({ queryKey: ["ts", "clock-days", me, today], enabled: !!me, queryFn: () => timeApi.employeeAttendance(me, start, today) });
  const list: any[] = punches.data || [];
  const last = list[list.length - 1];
  const state: "out" | "in" | "break" = !last || last.direction === "out" ? "out" : last.direction === "break_start" ? "break" : "in";

  const punch = useMutation({
    mutationFn: async (direction: Dir) => {
      let pos: { lat?: number; lng?: number } = {};
      if (navigator.geolocation) {
        pos = await new Promise((res) => navigator.geolocation.getCurrentPosition(
          (p) => res({ lat: p.coords.latitude, lng: p.coords.longitude }), () => res({}), { timeout: 4000, maximumAge: 60000 }));
      }
      return timeApi.punch({ employee_id: me, direction, source: "web", ...pos } as any);
    },
    onSuccess: (r: any, direction) => {
      toast.success(`${DIR_LABEL[direction]} recorded at ${clock(r.at)}`);
      if (r.geofence_ok === false) toast("You are outside the work site area. The punch was still recorded.", { duration: 6000 });
      qc.invalidateQueries({ queryKey: ["ts"] });
    },
    onError: (e) => toast.error(errText(e, "The punch was not recorded. Try again.")),
  });

  const worked = useMemo(() => {
    let ms = 0, since: number | null = null;
    for (const p of list) {
      const t = new Date(p.at).getTime();
      if (p.direction === "in" || p.direction === "break_end") since = since ?? t;
      else if (since !== null) { ms += t - since; since = null; }
    }
    if (since !== null) ms += now.getTime() - since;
    const m = Math.max(0, Math.floor(ms / 60000));
    return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
  }, [list, now]);

  if (!me) {
    return (
      <div>
        <PageHeader title="Time Clock" icon={Timer} subtitle="Clock in and out" />
        <div className="card text-sm text-gray-600 max-w-xl">Your login is not linked to an employee record, so there is nobody to clock in. Ask HR to link your login to your employee record.</div>
      </div>
    );
  }
  const big = "w-full py-5 rounded-xl text-lg font-semibold inline-flex items-center justify-center gap-2 disabled:opacity-50";
  const punchCols: GridColumn[] = [
    { key: "at", label: "Time", render: (p) => clock(p.at), width: "110px" },
    { key: "direction", label: "Punch", text: (p) => DIR_LABEL[p.direction] || p.direction, render: (p) => DIR_LABEL[p.direction] || p.direction },
    { key: "source", label: "Recorded from", text: (p) => SOURCE[p.source] || p.source, render: (p) => SOURCE[p.source] || p.source },
    { key: "note", label: "Note", className: "text-gray-500" },
  ];
  const dayCols: GridColumn[] = [
    { key: "work_date", label: "Date", type: "date" },
    { key: "status", label: "Day", text: (d) => DAY_STATE[d.status] || d.status, render: (d) => DAY_STATE[d.status] || d.status },
    { key: "first_in", label: "First in", render: (d) => clock(d.first_in), text: (d) => clock(d.first_in) },
    { key: "last_out", label: "Last out", render: (d) => clock(d.last_out), text: (d) => clock(d.last_out) },
    { key: "planned_hours", label: "Planned", type: "hours" }, { key: "actual_hours", label: "Worked", type: "hours" },
    { key: "late_minutes", label: "Late (min)", type: "number" },
  ];

  return (
    <div>
      <PageHeader title="Time Clock" icon={Timer} subtitle={`${user?.first_name || ""} ${user?.last_name || ""}`.trim() || "Clock in and out"} />
      <div className="grid lg:grid-cols-3 gap-4">
        <div className="card lg:col-span-1 text-center">
          <div className="text-xs text-gray-500">{now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</div>
          <div className="text-5xl font-bold text-gray-800 tabular-nums my-3" aria-live="off">{now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
          <div className={`inline-block px-3 py-1 rounded-full text-sm font-medium mb-4 ${state === "in" ? "bg-green-100 text-green-700" : state === "break" ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-600"}`} role="status">
            {punches.isLoading ? "Checking…" : state === "in" ? `Clocked in since ${clock(last?.at)}` : state === "break" ? `On a break since ${clock(last?.at)}` : list.length ? `Clocked out at ${clock(last?.at)}` : "Not clocked in today"}
          </div>
          <div className="space-y-2">
            {state === "out" && <button className={`${big} bg-primary-600 text-white hover:bg-primary-700`} disabled={punch.isPending || punches.isLoading} onClick={() => punch.mutate("in")}><LogIn className="w-5 h-5" /> Clock in</button>}
            {state === "in" && <>
              <button className={`${big} bg-red-600 text-white hover:bg-red-700`} disabled={punch.isPending} onClick={() => punch.mutate("out")}><LogOut className="w-5 h-5" /> Clock out</button>
              <button className={`${big} !py-3 !text-base btn-secondary`} disabled={punch.isPending} onClick={() => punch.mutate("break_start")}><Coffee className="w-4 h-4" /> Start break</button>
            </>}
            {state === "break" && <button className={`${big} bg-primary-600 text-white hover:bg-primary-700`} disabled={punch.isPending} onClick={() => punch.mutate("break_end")}><LogIn className="w-5 h-5" /> End break</button>}
          </div>
          <div className="mt-4 text-sm text-gray-600">Worked today: <b className="tabular-nums">{worked}</b></div>
          <p className="text-[11px] text-gray-400 mt-3">Your location is added to the punch only if your browser allows it. Missed a punch? Ask for a fix on <Link to="/attendance" className="text-primary-600 hover:underline">Attendance</Link>.</p>
        </div>
        <div className="lg:col-span-2 space-y-4">
          <div>
            <h3 className="font-display font-semibold text-gray-800 mb-2">Today's punches</h3>
            {punches.isError ? <div className="card text-sm text-red-600">Could not load today's punches. {errText(punches.error)}</div>
              : <DataGrid columns={punchCols} rows={list} rowKey={(p) => p.id} empty="No punches today." minWidth={480} exportName={`my-punches-${today}`} searchPlaceholder="Search punches…" />}
          </div>
          <div>
            <h3 className="font-display font-semibold text-gray-800 mb-2">Last 14 days</h3>
            {history.isLoading ? <div className="grid place-items-center py-8"><Spinner /></div>
              : history.isError ? <div className="card text-sm text-red-600">Could not load your attendance. {errText(history.error)}</div>
              : <DataGrid columns={dayCols} rows={[...(history.data?.days || [])].reverse()} rowKey={(d) => d.work_date} empty="No attendance recorded in the last 14 days." minWidth={640} exportName="my-attendance-14-days" searchPlaceholder="Search days…" />}
          </div>
        </div>
      </div>
    </div>
  );
}
