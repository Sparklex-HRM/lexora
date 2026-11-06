import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { MapPinned } from "lucide-react";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { StatTile } from "../../components/ui";
import { timeApi } from "../../api/endpoints";
import { errText } from "../../api/platform";
import { mobileApi, type PunchMeta } from "../../api/mobile";
import { useLang, useT } from "../../i18n";
import { fmtDate, fmtTime } from "../../i18n/format";
import { AuthImg, FIELD, MobileShell } from "./_mobile";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const today = () => todayISO();
const DIR: Record<string, string> = { in: "In", out: "Out", break_start: "Break start", break_end: "Break end" };

function Selfie({ m }: { m: PunchMeta }) {
  if (!m.has_selfie) return <span className="text-gray-300">—</span>;
  return <AuthImg load={() => mobileApi.selfieBlob(m.id)} className="w-10 h-10 rounded-lg object-cover border border-surface-border" alt="selfie" />;
}

/** /mobile/team-punches — manager's map-free list: distance from site, flags, selfies; plus pending attendance fixes with that day's phone punches. */
export default function TeamPunchesPage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const [day, setDay] = useState(today());
  const data = useQuery({ queryKey: ["team-punches", day], queryFn: () => mobileApi.today(day) });
  const regs = useQuery({ queryKey: ["ts", "regs", "pending"], queryFn: () => timeApi.regularizations("pending") });
  const [open, setOpen] = useState<string | null>(null);
  const decide = useMutation({
    mutationFn: ({ id, ok }: { id: string; ok: boolean }) => (ok ? timeApi.approveReg(id) : timeApi.rejectReg(id)),
    onSuccess: () => { toast.success(t("common.done")); qc.invalidateQueries({ queryKey: ["ts"] }); },
    onError: (e) => toast.error(errText(e)),
  });
  const rows = data.data?.rows || [];
  const s = data.data?.summary;
  const cols: GridColumn<PunchMeta>[] = [
    { key: "employee_name", label: t("team.person"), render: (r) => <div><div className="font-medium">{r.employee_name}</div><div className="text-[11px] text-gray-400">{r.department || ""}</div></div> },
    { key: "device_time", label: t("team.device_time"), render: (r) => fmtTime(r.device_time, lang), text: (r) => r.device_time || "" },
    { key: "direction", label: t("team.punches"), text: (r) => DIR[r.direction] || r.direction, render: (r) => DIR[r.direction] || r.direction, width: "90px" },
    { key: "fence_name", label: t("team.site") },
    { key: "distance_m", label: t("team.distance"), type: "number", render: (r) => r.distance_m == null ? "—" : <span className={r.inside_fence === false ? "text-amber-700 font-semibold" : "text-green-700"}>{r.distance_m} m</span> },
    { key: "accuracy_m", label: "GPS ±m", type: "number" },
    { key: "selfie", label: t("team.selfie"), render: (r) => <Selfie m={r} />, text: (r) => (r.has_selfie ? "yes" : "no"), width: "60px" },
    { key: "flags", label: t("punch.flags"), text: (r) => r.flag_labels.join(", "), render: (r) => <div className="flex flex-wrap gap-1">{r.flag_labels.map((f) => <span key={f} className={`text-[10px] font-semibold px-1.5 py-0.5 rounded-full ${f === "Outside site" || f === "Mock location" ? "bg-amber-100 text-amber-800" : f === "Synced late" ? "bg-purple-100 text-purple-800" : "bg-gray-100 text-gray-600"}`}>{f}</span>)}</div> },
    { key: "server_time", label: t("team.server_time"), render: (r) => fmtTime(r.server_time, lang), text: (r) => r.server_time || "" },
    { key: "refused_reason", label: t("team.refused_reason"), className: "text-red-600 text-xs" },
  ];
  return (
    <MobileShell title={t("team.title")} wide actions={<input type="date" className={`${FIELD} !w-auto !py-1.5 !text-sm`} value={day} onChange={(e) => setDay(e.target.value)} />}>
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
        <StatTile label={t("team.punches")} value={s?.punches ?? "—"} icon={MapPinned} accent />
        <StatTile label={t("team.people")} value={s?.people ?? "—"} />
        <StatTile label={t("team.outside")} value={s?.outside ?? "—"} />
        <StatTile label={t("team.late")} value={s?.late_sync ?? "—"} />
        <StatTile label={t("team.refused")} value={s?.refused ?? "—"} />
      </div>
      <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} empty={t("team.no_rows")} minWidth={960} exportName={`team-punches-${day}`} initialSort={{ key: "device_time", dir: "desc" }} />

      <h3 className="font-display font-semibold text-gray-800 mt-5 mb-2">{t("team.regs")} ({(regs.data || []).length})</h3>
      <div className="card !p-0 divide-y divide-surface-border">
        {(regs.data || []).length === 0 && <div className="py-5 text-center text-sm text-gray-400">—</div>}
        {(regs.data || []).map((r) => (
          <div key={r.id} className="px-3 py-2.5 text-sm">
            <div className="flex items-center gap-2 flex-wrap">
              <div className="flex-1 min-w-[12rem]"><b>{r.employee_name}</b> · {fmtDate(r.work_date, lang)} · {r.reason}{r.requested_in ? ` · in ${r.requested_in}` : ""}{r.requested_out ? ` · out ${r.requested_out}` : ""}{r.note ? ` · ${r.note}` : ""}</div>
              <button className="text-xs text-primary-700 font-semibold" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? t("common.close") : t("team.selfie")}</button>
              <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => decide.mutate({ id: r.id, ok: false })}>{t("team.reject")}</button>
              <button className="btn-primary !py-1 !px-2 text-xs" onClick={() => decide.mutate({ id: r.id, ok: true })}>{t("team.approve")}</button>
            </div>
            {open === r.id && <RegPunches employee_id={r.employee_id} day={r.work_date} />}
          </div>
        ))}
      </div>
    </MobileShell>
  );
}

function RegPunches({ employee_id, day }: { employee_id: string; day: string }) {
  const t = useT();
  const lang = useLang();
  const q = useQuery({ queryKey: ["reg-punches", employee_id, day], queryFn: () => mobileApi.punches(employee_id, day) });
  const rows = q.data || [];
  if (q.isLoading) return <div className="text-xs text-gray-400 mt-2">{t("common.loading")}</div>;
  if (!rows.length) return <div className="text-xs text-gray-400 mt-2">{t("team.no_rows")}</div>;
  return (
    <div className="mt-2 flex flex-wrap gap-2">
      {rows.map((m) => (
        <div key={m.id} className="flex items-center gap-2 rounded-lg border border-surface-border px-2 py-1.5 text-xs">
          <Selfie m={m} />
          <div><div className="font-semibold">{DIR[m.direction] || m.direction} · {fmtTime(m.device_time, lang)}</div>
            <div className="text-gray-500">{m.fence_name ? `${m.fence_name} · ${m.distance_m ?? "—"} m` : t("punch.no_site")}{m.refused ? ` · ${t("team.refused")}` : ""}</div></div>
        </div>
      ))}
    </div>
  );
}
