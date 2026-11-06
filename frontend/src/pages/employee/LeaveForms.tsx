import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Paperclip, X } from "lucide-react";
import { Modal, Field, inputCls, err } from "../canada/_shared";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { leaveApi, type MasterRow } from "../../api/endpoints";
import { leaveEssApi } from "../../api/ess";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const localISO = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const daysBetween = (a: string, b: string) => Math.max(0, Math.round((new Date(b + "T00:00:00").getTime() - new Date(a + "T00:00:00").getTime()) / 86400000) + 1);

/** Apply for leave: type, dates, first / second half, CC colleagues, contact during leave,
 *  supporting document (required when the leave type's policy says so). */
export function LeaveApplyModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const types = useQuery({ queryKey: ["lv", "types"], queryFn: () => leaveApi.types() });
  const people = useQuery({ queryKey: ["directory"], queryFn: leaveEssApi.directory, staleTime: 300_000 });
  const bal = useQuery({ queryKey: ["lv", "balances", "me"], queryFn: () => leaveApi.balances(), retry: false });
  const [tried, setTried] = useState(false);
  const [f, setF] = useState({ leave_type_code: "", start_date: localISO(), end_date: localISO(), half_day: false, half_session: "first",
    reason: "", contact_phone: "", contact_note: "" });
  const [cc, setCc] = useState<string[]>([]);
  const [ccQ, setCcQ] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const set = (k: string, v: any) => setF((x) => ({ ...x, [k]: v }));
  const list = (types.data || []) as (MasterRow & { attachment_after_days?: number | null; category?: string | null })[];
  const lt = list.find((t) => t.code === f.leave_type_code);
  const span = f.half_day ? 0.5 : daysBetween(f.start_date, f.end_date);
  const needsDoc = lt?.attachment_after_days != null && span > Number(lt.attachment_after_days);
  const others = (people.data || []).filter((p) => !p.is_self);
  const matches = useMemo(() => ccQ.trim().length < 2 ? [] : others.filter((p) => !cc.includes(p.id) &&
    `${p.display_name} ${p.department || ""}`.toLowerCase().includes(ccQ.toLowerCase())).slice(0, 6), [ccQ, others, cc]);

  const create = useMutation({
    mutationFn: async () => {
      let attachment_id: string | undefined;
      if (file) attachment_id = (await leaveEssApi.upload(file)).id;
      return leaveApi.createRequest({ leave_type_code: f.leave_type_code, start_date: f.start_date, end_date: f.half_day ? f.start_date : f.end_date,
        half_day: f.half_day, half_session: f.half_day ? f.half_session : undefined, reason: f.reason || undefined,
        cc_employee_ids: cc.length ? cc : undefined, contact_phone: f.contact_phone || undefined, contact_note: f.contact_note || undefined, attachment_id });
    },
    onSuccess: () => { toast.success("Leave requested — sent to your approver"); qc.invalidateQueries({ queryKey: ["lv"] }); onClose(); },
    onError: (e) => toast.error(err(e, "Request failed")),
  });
  const b = (bal.data?.balances || []).find((x) => x.leave_type_code === f.leave_type_code);
  const MAX_DOC_MB = 5;
  const errs = collect({
    leave_type_code: v.required(f.leave_type_code, "Leave type"),
    start_date: v.required(f.start_date, "From date"),
    end_date: f.half_day ? null : (v.required(f.end_date, "To date") || v.dateOrder(f.start_date, f.end_date, "The To date cannot be before the From date")),
    reason: v.maxLen(f.reason, 1000, "Reason"),
    contact_phone: v.mobile(f.contact_phone),
    contact_note: v.maxLen(f.contact_note, 300, "Contact note"),
    file: needsDoc && !file ? `A supporting document is required for ${lt?.name} over ${lt?.attachment_after_days} day(s)`
      : file && !/\.(pdf|png|jpe?g)$/i.test(file.name) ? "Attach a PDF, JPG or PNG file"
      : file && file.size > MAX_DOC_MB * 1024 * 1024 ? `The file can be at most ${MAX_DOC_MB} MB` : null,
  });
  const show = (k: string) => (tried ? errs[k] : undefined);
  const submit = () => { setTried(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); create.mutate(); };
  const Lb = ({ t, req }: { t: string; req?: boolean }) => <span className="block text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">{t}{req && <Req />}</span>;
  const Hint = ({ t }: { t: string }) => <span className="block text-[11px] text-gray-500 mt-0.5">{t}</span>;

  return (
    <Modal title="Apply for leave" sub="Weekly offs and holidays are not counted" onClose={onClose} width="max-w-4xl"
      footer={<div className="flex justify-end gap-2"><button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" data-testid="leave-submit" disabled={create.isPending} onClick={submit}>{create.isPending ? "Submitting…" : "Submit request"}</button></div>}>
      {f.leave_type_code && (
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-surface-border bg-surface px-3 py-2 text-xs" data-testid="leave-balance-strip">
          <span className="font-semibold text-gray-700">{lt?.name || f.leave_type_code}</span>
          {b ? <>
            <span>Available: <b className={span > Number(b.remaining) && b.paid ? "text-red-600" : "text-gray-800"}>{Number(b.remaining)}</b> day(s)</span>
            <span className="text-gray-500">Entitled {Number(b.entitled)} · used {Number(b.used)} · pending {Number(b.pending)}</span>
          </> : <span className="text-gray-400">{bal.isLoading ? "Loading balance…" : "No balance allotted for this type"}</span>}
          <span className="ml-auto text-gray-500">Selected: {span} calendar day(s)</span>
        </div>)}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="block"><Lb t="Leave type" req /><select className={inputCls} value={f.leave_type_code} onChange={(e) => set("leave_type_code", e.target.value)} aria-label="Leave type">
          <option value="">Select…</option>
          {list.map((t) => <option key={t.id} value={t.code || ""}>{t.name}{t.paid === false ? " (unpaid)" : t.category === "event" ? " (event leave)" : ""}</option>)}
        </select><FieldError msg={show("leave_type_code")} /></label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block"><Lb t="From" req /><input type="date" className={inputCls} value={f.start_date} onChange={(e) => setF((x) => ({ ...x, start_date: e.target.value, end_date: e.target.value > x.end_date ? e.target.value : x.end_date }))} /><FieldError msg={show("start_date")} /></label>
          <label className="block"><Lb t="To" req /><input type="date" className={inputCls} value={f.half_day ? f.start_date : f.end_date} min={f.start_date} disabled={f.half_day} onChange={(e) => set("end_date", e.target.value)} /><FieldError msg={show("end_date")} /></label>
        </div>
        <div className="flex flex-wrap items-center gap-3 sm:col-span-2 text-sm text-gray-600">
          <label className="inline-flex items-center gap-2"><input type="checkbox" checked={f.half_day} onChange={(e) => set("half_day", e.target.checked)} />Half day</label>
          {f.half_day && <div className="flex rounded-lg border border-surface-border overflow-hidden text-xs">
            {[["first", "First half (morning)"], ["second", "Second half (afternoon)"]].map(([v, l]) => (
              <button key={v} type="button" onClick={() => set("half_session", v)} className={`px-2.5 py-1.5 ${f.half_session === v ? "bg-primary-50 text-primary-700" : ""}`}>{l}</button>))}
          </div>}
          <span className="text-[11px] text-gray-500">A half day counts as 0.5; weekly offs and holidays in your range are not counted.</span>
        </div>
        <label className="block sm:col-span-2"><Lb t="Reason" /><textarea className="input-base text-xs w-full min-h-[60px] p-2" maxLength={1000} value={f.reason} placeholder="Family function, medical appointment…" onChange={(e) => set("reason", e.target.value)} /><FieldError msg={show("reason")} /></label>
        <label className="block"><Lb t="Contact number during leave" /><input className={inputCls} inputMode="tel" value={f.contact_phone} placeholder="98xxxxxx10" onChange={(e) => set("contact_phone", e.target.value)} />
          <Hint t="Number where your manager or HR can reach you while you are away." /><FieldError msg={show("contact_phone")} /></label>
        <label className="block"><Lb t="Contact note" /><input className={inputCls} maxLength={300} value={f.contact_note} placeholder="Reachable on WhatsApp after 6 pm; address while away" onChange={(e) => set("contact_note", e.target.value)} />
          <Hint t="When / how to reach you, or where you will be (up to 300 characters)." /><FieldError msg={show("contact_note")} /></label>
        <Field label="Copy colleagues (CC)" className="sm:col-span-2">
          <div className="flex flex-wrap gap-1 mb-1">{cc.map((id) => { const p = others.find((x) => x.id === id); return (
            <span key={id} className="inline-flex items-center gap-1 text-[11px] bg-primary-50 text-primary-700 rounded-full px-2 py-0.5">{p?.display_name || id}
              <button type="button" aria-label="Remove" onClick={() => setCc(cc.filter((x) => x !== id))}><X className="w-3 h-3" /></button></span>); })}</div>
          <input className={inputCls} value={ccQ} placeholder="Type 2+ letters of a colleague's name" onChange={(e) => setCcQ(e.target.value)} aria-label="Search colleagues" />
          {matches.length > 0 && <div className="border border-surface-border rounded-lg mt-1 divide-y divide-surface-border bg-white">{matches.map((p) => (
            <button type="button" key={p.id} className="w-full text-left px-2.5 py-1.5 text-xs hover:bg-gray-50" onClick={() => { setCc([...cc, p.id]); setCcQ(""); }}>
              {p.display_name} <span className="text-gray-400">· {p.department || "—"}</span></button>))}</div>}
          <Hint t="These colleagues get a copy for information only; they do not approve it." />
        </Field>
        <Field label={needsDoc ? `Supporting document (required for ${lt?.name} over ${lt?.attachment_after_days} day(s))` : "Supporting document (optional)"} className="sm:col-span-2">
          <label className={`flex items-center gap-2 text-xs border border-dashed rounded-lg px-3 py-2 cursor-pointer ${needsDoc && !file ? "border-amber-400 bg-amber-50" : "border-surface-border"}`}>
            <Paperclip className="w-3.5 h-3.5" />{file ? file.name : "PDF, JPG or PNG — e.g. a medical certificate"}
            <input type="file" accept=".pdf,.png,.jpg,.jpeg" className="hidden" data-testid="leave-file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          </label>
          <Hint t={`PDF, JPG or PNG, up to ${MAX_DOC_MB} MB.`} /><FieldError msg={show("file")} />
        </Field>
      </div>
    </Modal>
  );
}

/** Ask the approver to cancel an APPROVED leave (days come back only when they agree). */
export function CancelLeaveModal({ id, label, onClose }: { id: string; label: string; onClose: () => void }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const m = useMutation({
    mutationFn: () => leaveEssApi.requestCancel(id, reason),
    onSuccess: () => { toast.success("Cancellation sent to your approver"); qc.invalidateQueries({ queryKey: ["lv"] }); onClose(); },
    onError: (e) => toast.error(err(e, "Could not send")),
  });
  return (
    <Modal title="Ask to cancel approved leave" sub={label} onClose={onClose} width="max-w-3xl"
      footer={<div className="flex justify-end gap-2"><button className="btn-secondary" onClick={onClose}>Keep leave</button>
        <button className="btn-primary" disabled={m.isPending || reason.trim().length < 3} onClick={() => m.mutate()}>Send to approver</button></div>}>
      <Field label="Why cancel? (your approver sees this)"><textarea className="input-base text-xs w-full min-h-[70px] p-2" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Trip postponed" /></Field>
      <p className="text-[11px] text-gray-400 mt-2">The leave stays approved until your approver agrees; then the days return to your balance.</p>
    </Modal>
  );
}

/** Comp-off: claim a day off for work on a holiday / weekly off (checked against your punches). */
export function CompOffPanel() {
  const qc = useQueryClient();
  const rows = useQuery({ queryKey: ["lv", "comp-off"], queryFn: () => leaveEssApi.compOffs() });
  const pol = useQuery({ queryKey: ["lv", "ess-policy"], queryFn: leaveEssApi.policy });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ worked_date: "", reason: "" });
  const claim = useMutation({
    mutationFn: () => leaveEssApi.claimCompOff(f),
    onSuccess: () => { toast.success("Comp-off claim sent for approval"); qc.invalidateQueries({ queryKey: ["lv"] }); setOpen(false); setF({ worked_date: "", reason: "" }); },
    onError: (e) => toast.error(err(e, "Could not claim")),
  });
  const withdraw = useMutation({ mutationFn: (id: string) => leaveEssApi.decideCompOff(id, "cancel"), onSuccess: () => qc.invalidateQueries({ queryKey: ["lv"] }), onError: (e) => toast.error(err(e)) });
  const cols: GridColumn<any>[] = [
    { key: "worked_date", label: "Worked on", type: "date" },
    { key: "day_label", label: "Day" },
    { key: "hours_worked", label: "Hours (punches)", type: "hours" },
    { key: "days", label: "Comp-off days", type: "number" },
    { key: "expires_on", label: "Use by", type: "date" },
    { key: "status", label: "Status", render: (r) => <span className={r.status === "approved" ? "badge-green" : r.status === "pending" ? "badge-amber" : r.status === "rejected" ? "badge-red" : "badge-purple"}>{r.status}</span> },
    { key: "act", label: "", text: () => "", render: (r) => r.status === "pending" ? <button className="text-xs text-red-600 hover:underline" onClick={(e) => { e.stopPropagation(); withdraw.mutate(r.id); }}>Withdraw</button> : null },
  ];
  const p = pol.data;
  return (
    <div className="card !p-0" data-testid="comp-off-panel">
      <div className="px-4 py-3 border-b border-surface-border flex flex-wrap items-center gap-2">
        <span className="font-display font-semibold text-gray-800 text-sm">Compensatory off</span>
        {p && <span className="text-[11px] text-gray-400">Company policy: {p.comp_off_full_day_hours} h = 1 day, {p.comp_off_half_day_hours} h = half a day · use within {p.comp_off_expiry_days} days</span>}
        <button className="btn-primary !py-1 text-xs ml-auto" onClick={() => setOpen(true)}>Claim comp-off</button>
      </div>
      <DataGrid columns={cols} rows={rows.data || []} rowKey={(r) => r.id} exportName="my-comp-off" minWidth={760} pageSizeDefault={10}
        empty="No comp-off claims. Worked on a holiday or weekly off? Claim it here." />
      {open && <Modal title="Claim a compensatory off" sub="For work on a holiday or a weekly off — your punches for that day are checked" onClose={() => setOpen(false)} width="max-w-3xl"
        footer={<div className="flex justify-end gap-2"><button className="btn-secondary" onClick={() => setOpen(false)}>Cancel</button>
          <button className="btn-primary" disabled={!f.worked_date || f.reason.trim().length < 3 || claim.isPending} onClick={() => claim.mutate()}>Send for approval</button></div>}>
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Day you worked"><input type="date" className={inputCls} max={localISO()} value={f.worked_date} onChange={(e) => setF({ ...f, worked_date: e.target.value })} /></Field>
          <Field label="What you worked on"><input className={inputCls} value={f.reason} placeholder="Release cut-over on Sunday" onChange={(e) => setF({ ...f, reason: e.target.value })} /></Field>
        </div>
      </Modal>}
    </div>
  );
}
