import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Timer } from "lucide-react";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Modal, inputCls, err } from "../canada/_shared";
import { essTimeApi, permissionApi } from "../../api/ess";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const REASON: Record<string, string> = { missed_punch: "Missed punch", wrong_time: "Wrong time recorded", forgot: "Forgot to punch", other: "Other" };
const TONE: Record<string, string> = { pending: "badge-amber", approved: "badge-green", rejected: "badge-red", cancelled: "badge-purple" };
export const statusBadge = (s: string) => <span className={TONE[s] || "badge-teal"}>{s}</span>;
const toMin = (t: string) => { const m = /^(\d{1,2}):(\d{2})$/.exec(t || ""); return m ? Number(m[1]) * 60 + Number(m[2]) : NaN; };
const localISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

/** The employee's own attendance corrections and permissions — dense grids with export. */
export function MyTimeRequests() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["my-time-requests"], queryFn: essTimeApi.requests });
  const withdraw = useMutation({ mutationFn: (id: string) => permissionApi.cancel(id),
    onSuccess: () => { toast.success("Permission withdrawn"); qc.invalidateQueries({ queryKey: ["my-time-requests"] }); qc.invalidateQueries({ queryKey: ["my-attendance"] }); },
    onError: (e) => toast.error(err(e)) });
  const cols: GridColumn<any>[] = [
    { key: "work_date", label: "Day", type: "date" },
    { key: "reason", label: "Reason", text: (r) => REASON[r.reason] || r.reason },
    { key: "requested_in", label: "In" },
    { key: "requested_out", label: "Out" },
    { key: "note", label: "Note" },
    { key: "status", label: "Status", text: (r) => r.status, render: (r) => statusBadge(r.status) },
    { key: "created_at", label: "Raised", type: "date" },
  ];
  const pcols: GridColumn<any>[] = [
    { key: "work_date", label: "Day", type: "date" },
    { key: "from_time", label: "From" },
    { key: "to_time", label: "To" },
    { key: "hours", label: "Hours", type: "hours" },
    { key: "reason", label: "Reason" },
    { key: "status", label: "Status", text: (r) => r.status, render: (r) => statusBadge(r.status) },
    { key: "act", label: "", text: () => "", render: (r) => r.status === "pending" ? <button className="text-xs text-red-600 hover:underline" onClick={(e) => { e.stopPropagation(); withdraw.mutate(r.id); }}>Withdraw</button> : null },
  ];
  return (
    <div className="grid xl:grid-cols-2 gap-4">
      <div className="card !p-0">
        <div className="px-4 py-3 border-b border-surface-border font-display font-semibold text-gray-800 text-sm">My attendance corrections</div>
        <DataGrid columns={cols} rows={data?.corrections || []} rowKey={(r) => r.id} exportName="my-attendance-corrections"
          empty="No corrections raised yet. Pick a day on the calendar and choose Request correction." minWidth={720}
          initialSort={{ key: "created_at", dir: "desc" }} pageSizeDefault={10} />
      </div>
      <div className="card !p-0" data-testid="my-permissions">
        <div className="px-4 py-3 border-b border-surface-border font-display font-semibold text-gray-800 text-sm">My permissions (short hours)</div>
        <DataGrid columns={pcols} rows={data?.permissions || []} rowKey={(r) => r.id} exportName="my-permissions"
          empty="No permissions asked for. Use Ask for permission to step out for an hour or two." minWidth={680}
          initialSort={{ key: "work_date", dir: "desc" }} pageSizeDefault={10} />
      </div>
    </div>
  );
}

/** "Ask for permission": leave the office for a short time (company policy caps per month + hours). */
export function PermissionButton() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const pol = useQuery({ queryKey: ["perm-policy"], queryFn: permissionApi.policy, enabled: open });
  const [f, setF] = useState({ work_date: localISO(), from_time: "16:00", to_time: "17:30", reason: "" });
  const m = useMutation({ mutationFn: () => permissionApi.create(f),
    onSuccess: () => { toast.success("Permission sent to your approver"); qc.invalidateQueries({ queryKey: ["my-time-requests"] }); qc.invalidateQueries({ queryKey: ["my-attendance"] }); qc.invalidateQueries({ queryKey: ["perm-policy"] }); setOpen(false); },
    onError: (e) => toast.error(err(e, "Could not send")) });
  const p = pol.data;
  const mins = toMin(f.to_time) - toMin(f.from_time);
  const sameMonth = f.work_date.slice(0, 7) === localISO().slice(0, 7);
  const errs = collect({
    work_date: v.required(f.work_date, "Day"),
    from_time: v.required(f.from_time, "From time"),
    to_time: v.required(f.to_time, "To time") || (Number.isFinite(mins) && mins <= 0 ? "To time must be after the From time" : null)
      || (p && p.max_hours && mins > p.max_hours * 60 ? `A permission can be at most ${p.max_hours} h under company policy` : null),
    reason: f.reason.trim().length < 3 ? "Give a reason of at least 3 characters" : v.maxLen(f.reason, 500, "Reason"),
    quota: p && p.max_per_month && sameMonth && p.used_this_month >= p.max_per_month ? `You have used all ${p.max_per_month} permission(s) allowed this month` : null,
  });
  const [tried, setTried] = useState(false);
  const send = () => { setTried(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); m.mutate(); };
  const show = (k: string) => (tried ? errs[k] : undefined);
  const L = ({ t, req }: { t: string; req?: boolean }) => <span className="block text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">{t}{req && <Req />}</span>;
  return (
    <>
      <button className="btn-secondary inline-flex items-center gap-1.5 text-sm" onClick={() => setOpen(true)} data-testid="ask-permission"><Timer className="w-4 h-4" />Ask for permission</button>
      {open && <Modal title="Ask for permission" sub="Step out for a short time — your approver decides" onClose={() => setOpen(false)} width="max-w-3xl"
        footer={<div className="flex justify-end gap-2"><button className="btn-secondary" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn-primary" disabled={m.isPending} onClick={send}>Send for approval</button></div>}>
        {p && <p className="text-xs text-gray-500 mb-3">Company policy: up to <b>{p.max_per_month}</b> a month, at most <b>{p.max_hours} h</b> each. Used this month: <b>{p.used_this_month}</b>
          {p.max_per_month ? <> · left: <b>{Math.max(0, p.max_per_month - p.used_this_month)}</b></> : null}.</p>}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <label className="block"><L t="Day" req /><input type="date" className={inputCls} value={f.work_date} onChange={(e) => setF({ ...f, work_date: e.target.value })} />
            <span className="block text-[11px] text-gray-500 mt-0.5">The day you need to step out.</span><FieldError msg={show("work_date")} /></label>
          <label className="block"><L t="From" req /><input type="time" className={inputCls} value={f.from_time} onChange={(e) => setF({ ...f, from_time: e.target.value })} />
            <span className="block text-[11px] text-gray-500 mt-0.5">When you plan to leave your desk.</span><FieldError msg={show("from_time")} /></label>
          <label className="block"><L t="To" req /><input type="time" className={inputCls} value={f.to_time} onChange={(e) => setF({ ...f, to_time: e.target.value })} />
            <span className="block text-[11px] text-gray-500 mt-0.5" data-testid="perm-duration">
              {Number.isFinite(mins) && mins > 0 ? `Duration: ${Math.floor(mins / 60)} h ${mins % 60} min` : "When you expect to be back."}</span>
            <FieldError msg={show("to_time")} /></label>
          <label className="block sm:col-span-3"><L t="Reason" req /><input className={inputCls} value={f.reason} maxLength={500} placeholder="Bank visit" onChange={(e) => setF({ ...f, reason: e.target.value })} />
            <span className="block text-[11px] text-gray-500 mt-0.5">Short reason your approver will see (3–500 characters).</span><FieldError msg={show("reason")} /></label>
        </div>
        {errs.quota && <p className="mt-3 text-xs text-red-600" role="alert">{errs.quota}</p>}
      </Modal>}
    </>
  );
}
