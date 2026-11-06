// Stream D4 — move an employee to another legal entity from an effective date.
// Shows the person's entity history (spells), lets HR pick the new entity and date, and
// the transfer type. 2026-10-24 forms wrap-up: transfer type + service continuity / leave / final-pay terms are
// recorded on the career-change record; inline checks; grouped sections.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ArrowRightLeft } from "lucide-react";
import { WideModal } from "./DataGrid";
import { FormSection } from "./Modal";
import { Spinner, Badge } from "./ui";
import { entitiesApi } from "../api/entities";
import { v, collect, firstError, FieldError, Req } from "../lib/validators";
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const errText = (e: any, fb: string) => { const d = e?.response?.data?.detail; return typeof d === "string" ? d : d?.message || fb; };
const today = () => todayISO();
const LBL = "block text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-0.5";
type TransferType = "permanent" | "secondment" | "business_transfer";
const TYPES: { value: TransferType; label: string; hint: string }[] = [
  { value: "permanent", label: "Permanent transfer", hint: "Employment moves fully to the new entity." },
  { value: "business_transfer", label: "Business transfer", hint: "The business (or a unit) moves to the new entity and the people go with it." },
  { value: "secondment", label: "Secondment / deputation", hint: "Short-term posting. The person usually stays on the old entity's payroll account." },
];

export default function EntityTransferModal({ employeeId, employeeName, onClose }: { employeeId: string; employeeName: string; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: ents } = useQuery({ queryKey: ["entities", "headcount"], queryFn: entitiesApi.headcount });
  const { data: hist, isLoading } = useQuery({ queryKey: ["entity-history", employeeId], queryFn: () => entitiesApi.history(employeeId) });
  const [form, setForm] = useState({
    entity_id: "", effective_date: today(), reason: "", note: "",
    transfer_type: "permanent" as TransferType, service_continuity: true, carry_leave: true, final_pay_under_old_entity: false,
  });
  const [errs, setErrs] = useState<Record<string, string>>({});
  const set = (patch: Partial<typeof form>) => setForm((p) => ({ ...p, ...patch }));
  const options = (ents?.entities || []).filter((x) => x.is_active && x.id !== hist?.entity_id);
  const currentFrom = (hist?.history || []).find((h) => h.status === "current")?.from_date || "";
  const run = useMutation({
    mutationFn: () => entitiesApi.transfer(employeeId, { ...form, reason: form.reason.trim() || undefined, note: form.note.trim() || undefined }),
    onSuccess: (r) => {
      toast.success(r.applied ? "Transferred to the new legal entity" : `Transfer scheduled for ${form.effective_date}`);
      qc.invalidateQueries({ queryKey: ["entity-history", employeeId] });
      qc.invalidateQueries({ queryKey: ["employees"] });
      qc.invalidateQueries({ queryKey: ["entities"] });
      onClose();
    },
    onError: (e: any) => toast.error(errText(e, "Transfer failed")),
  });
  const submit = () => {
    const found = collect({
      entity_id: v.required(form.entity_id, "New legal entity"),
      effective_date: v.required(form.effective_date, "Effective date")
        || (currentFrom && form.effective_date <= currentFrom ? `Effective date must be after the current spell started (${currentFrom})` : null),
      reason: v.maxLen(form.reason, 200, "Reason"),
      note: v.maxLen(form.note, 500, "Note"),
    });
    setErrs(found);
    if (Object.keys(found).length) return toast.error(firstError(found));
    run.mutate();
  };
  const target = options.find((x) => x.id === form.entity_id);
  const type = TYPES.find((t) => t.value === form.transfer_type)!;
  const check = (k: "service_continuity" | "carry_leave" | "final_pay_under_old_entity", label: string, hint: string) => (
    <label className="flex items-start gap-2 text-[12px] text-gray-700">
      <input type="checkbox" className="mt-0.5" checked={form[k]} onChange={(e) => set({ [k]: e.target.checked } as any)} />
      <span>{label}<span className="block text-[11px] text-gray-400">{hint}</span></span>
    </label>
  );
  return (
    <WideModal title={`Transfer ${employeeName} to another legal entity`} onClose={onClose}
      footer={<>
        <button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-sm inline-flex items-center gap-1.5" disabled={run.isPending} onClick={submit}>
          <ArrowRightLeft className="w-3.5 h-3.5" /> {run.isPending ? "Transferring…" : form.effective_date > today() ? "Schedule transfer" : "Transfer now"}
        </button>
      </>}>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <div className="text-[12px] text-gray-600 mb-3">Currently employed by <b>{hist?.entity_name || "—"}</b>. The old spell closes the day before the effective date; the new entity's CRA payroll account (RP), Revenu Québec number and WCB account apply from the next pay period.</div>
          <FormSection title="Destination and date">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="block sm:col-span-2"><span className={LBL}>New legal entity<Req /></span>
                <select className="input" value={form.entity_id} onChange={(e) => set({ entity_id: e.target.value })}>
                  <option value="">Choose…</option>{options.map((x) => <option key={x.id} value={x.id}>{x.short_code} · {x.name}</option>)}
                </select><FieldError msg={errs.entity_id} /></label>
              <label className="block"><span className={LBL}>Transfer type<Req /></span>
                <select className="input" value={form.transfer_type} onChange={(e) => {
                  const t = e.target.value as TransferType;
                  set({ transfer_type: t });
                }}>
                  {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
                <span className="block text-[11px] text-gray-400 mt-0.5">{type.hint}</span></label>
              <label className="block"><span className={LBL}>Effective date<Req /></span>
                <input type="date" className="input" value={form.effective_date} min={currentFrom || undefined} onChange={(e) => set({ effective_date: e.target.value })} />
                <FieldError msg={errs.effective_date} />
                {!errs.effective_date && <span className="block text-[11px] text-gray-400 mt-0.5">A future date schedules the transfer; it applies on that day.</span>}</label>
              <label className="block sm:col-span-2"><span className={LBL}>Reason</span>
                <input className="input" maxLength={200} value={form.reason} onChange={(e) => set({ reason: e.target.value })} placeholder="Group restructuring / business transfer / secondment" />
                <FieldError msg={errs.reason} /></label>
              <label className="block sm:col-span-2"><span className={LBL}>Note</span>
                <textarea className="input" rows={2} maxLength={500} value={form.note} onChange={(e) => set({ note: e.target.value })}
                  placeholder="Board resolution / transfer letter reference" />
                <FieldError msg={errs.note} /></label>
            </div>
          </FormSection>
          <FormSection title="Service and settlement" hint="Recorded on the transfer for audit and for the final-pay decision — nothing is computed from these by itself.">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {check("service_continuity", "Earlier service counts", "Tick for a group restructuring or sale of business where past service counts for vacation and notice (provincial employment standards).")}
              {check("carry_leave", "Leave balances move with the person", "Untick if vacation pay is paid out or reset at the old entity.")}
              {check("final_pay_under_old_entity", "Final pay and ROE under the old entity", "The old entity issues final pay (wages, vacation pay) and a Record of Employment before the move.")}
            </div>
          </FormSection>
          {target && <div className="rounded-lg border border-primary-200 bg-primary-50 px-3 py-2 text-[12px] text-primary-800">From {form.effective_date}: pay stubs, the T4 / RL-1 and the remittances for this person carry <b>{target.name}</b>.</div>}
        </div>
        <div>
          <div className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">Entity history</div>
          {isLoading ? <Spinner /> : (
            <div className="overflow-x-auto"><table className="w-full text-[12px]">
              <thead className="text-[10px] uppercase text-gray-400"><tr><th className="text-left py-1">Entity</th><th className="text-left">From</th><th className="text-left">To</th><th className="text-left">Status</th></tr></thead>
              <tbody>
                {(hist?.history || []).map((h) => (
                  <tr key={h.id} className="border-t border-surface-border/70">
                    <td className="py-1">{h.entity_name}</td><td>{h.from_date || "—"}</td><td>{h.to_date || "—"}</td>
                    <td><Badge tone={h.status === "current" ? "active" : h.status === "scheduled" ? "pending" : "terminated"}>{h.status}</Badge></td>
                  </tr>))}
                {!hist?.history?.length && <tr><td colSpan={4} className="py-2 text-gray-400">No history yet.</td></tr>}
              </tbody>
            </table></div>
          )}
        </div>
      </div>
    </WideModal>
  );
}
