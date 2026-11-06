import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { leaveApi } from "../../api/endpoints";
import { errText } from "../../api/platform";
import { useLang, useT } from "../../i18n";
import { fmtDate } from "../../i18n/format";
import { BIG, FIELD, LABEL, MobileShell, useMe } from "./_mobile";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const today = () => todayISO();

/** /mobile/leave — apply for leave from the phone; balances and my requests. */
export default function MobileLeavePage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const { me } = useMe();
  const types = useQuery({ queryKey: ["leave-types"], queryFn: () => leaveApi.types() });
  const bal = useQuery({ queryKey: ["m-leave-bal", me?.id], queryFn: () => leaveApi.balances(me!.id), enabled: !!me });
  const reqs = useQuery({ queryKey: ["m-leave-reqs", me?.id], queryFn: () => leaveApi.requests({ employee_id: me!.id }), enabled: !!me });
  const [f, setF] = useState({ leave_type_code: "", start_date: today(), end_date: today(), half_day: false, reason: "" });
  const apply = useMutation({
    mutationFn: () => leaveApi.createRequest({ employee_id: me!.id, ...f, leave_type_code: code }),
    onSuccess: () => { toast.success(t("leave.sent")); setF({ ...f, reason: "" }); qc.invalidateQueries({ queryKey: ["m-leave-reqs"] }); qc.invalidateQueries({ queryKey: ["m-leave-bal"] }); },
    onError: (e) => toast.error(errText(e, t("common.error"))),
  });
  const cancel = useMutation({ mutationFn: (id: string) => leaveApi.cancelRequest(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["m-leave-reqs"] }), onError: (e) => toast.error(errText(e)) });
  const typeList = (types.data || []) as any[];
  const code = f.leave_type_code || typeList[0]?.code || "";
  const STATUS: Record<string, string> = { pending: t("leave.pending"), approved: t("leave.approved"), rejected: t("leave.rejected"), cancelled: t("leave.cancelled") };
  const TONE: Record<string, string> = { pending: "bg-amber-100 text-amber-800", approved: "bg-green-100 text-green-700", rejected: "bg-red-100 text-red-700", cancelled: "bg-gray-100 text-gray-600" };

  if (!me) return <MobileShell title={t("leave.title")}><div className="card text-sm text-gray-600">{t("common.not_linked")}</div></MobileShell>;
  return (
    <MobileShell title={t("leave.title")}>
      <div className="grid grid-cols-2 gap-2 mb-3">
        {(bal.data?.balances || []).map((b) => (
          <div key={b.id} className="card !py-2.5 !px-3">
            <div className="text-[11px] text-gray-500 truncate">{b.leave_type_name || b.leave_type_code}</div>
            <div className="text-xl font-bold text-gray-800 tabular-nums">{b.remaining}<span className="text-xs text-gray-400 font-normal"> / {b.entitled}</span></div>
          </div>
        ))}
      </div>
      <form className="card space-y-3" onSubmit={(e) => { e.preventDefault(); if (!code) return toast.error(t("leave.no_types")); apply.mutate(); }}>
        <div><label className={LABEL}>{t("leave.type")}</label>
          <select className={FIELD} value={code} onChange={(e) => setF({ ...f, leave_type_code: e.target.value })}>
            {typeList.map((x) => <option key={x.code} value={x.code}>{x.name}</option>)}
          </select>
          {!typeList.length && !types.isLoading && <p className="text-xs text-red-600 mt-1">{t("leave.no_types")}</p>}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div><label className={LABEL}>{t("leave.from")}</label><input type="date" className={FIELD} value={f.start_date} onChange={(e) => setF({ ...f, start_date: e.target.value, end_date: f.end_date < e.target.value ? e.target.value : f.end_date })} /></div>
          <div><label className={LABEL}>{t("leave.to")}</label><input type="date" className={FIELD} min={f.start_date} value={f.end_date} onChange={(e) => setF({ ...f, end_date: e.target.value })} /></div>
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.half_day} onChange={(e) => setF({ ...f, half_day: e.target.checked })} /> {t("leave.half_day")}</label>
        <div><label className={LABEL}>{t("leave.reason")}</label><textarea className={FIELD} rows={2} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></div>
        <button className={`${BIG} bg-primary-600 text-white !py-3 !text-base`} disabled={apply.isPending || !code}>{t("leave.apply")}</button>
      </form>
      <h3 className="font-display font-semibold text-gray-800 mt-4 mb-2">{t("leave.requests")}</h3>
      <div className="card !p-0 divide-y divide-surface-border">
        {(reqs.data || []).length === 0 && <div className="py-6 text-center text-sm text-gray-400">—</div>}
        {(reqs.data || []).map((r) => (
          <div key={r.id} className="px-3 py-2.5 text-sm flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <div className="font-medium text-gray-800 truncate">{r.leave_type_name || r.leave_type_code} · {t("leave.days", { n: r.days })}</div>
              <div className="text-xs text-gray-500">{fmtDate(r.start_date, lang)} → {fmtDate(r.end_date, lang)}</div>
            </div>
            <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${TONE[r.status] || "bg-gray-100"}`}>{STATUS[r.status] || r.status}</span>
            {r.status === "pending" && <button className="text-xs text-red-600" onClick={() => cancel.mutate(r.id)}>{t("common.cancel")}</button>}
          </div>
        ))}
      </div>
    </MobileShell>
  );
}
