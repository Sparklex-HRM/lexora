import { useState, type ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ShieldPlus, Plus, AlertTriangle, CalendarDays, Clock } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader, StatTile, Spinner, Empty, Card } from "../components/ui";
import { complianceApi, employeeApi } from "../api/endpoints";
import AppModal, { FormGrid, FormSection } from "../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../lib/validators";
import { todayISO } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const TYPES = ["injury", "near_miss", "illness", "property", "environmental"];
const SEVERITIES = ["minor", "moderate", "serious", "critical"];
const STATUSES = ["open", "investigating", "corrective_action", "closed"];
const SEV_CLS: Record<string, string> = { minor: "badge-teal", moderate: "badge-amber", serious: "badge-red", critical: "badge-red" };
const ST_CLS: Record<string, string> = { open: "badge-red", investigating: "badge-amber", corrective_action: "badge-purple", closed: "badge-green" };
const cap = (s: string) => s.replace(/_/g, " ");
const today = () => todayISO();

function IncidentModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState<any>({ occurred_on: today(), reported_on: today(), incident_type: "injury", severity: "minor", status: "open", description: "", location: "", employee_id: "", employee_name: "", branch_name: "", lost_time: false, lost_days: 0, wcb_reportable: false, wcb_claim_no: "", corrective_action: "" });
  const set = (k: string, val: any) => setF((p: any) => ({ ...p, [k]: val }));
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const people = ((emps.data || []) as any[]).filter((e) => e.status !== "terminated");
  const errs = collect({
    occurred_on: v.required(f.occurred_on, "Occurred on") || v.notFuture(f.occurred_on, "Occurred on"),
    reported_on: v.notFuture(f.reported_on, "Reported on") || v.dateOrder(f.occurred_on, f.reported_on, "Reported on cannot be before the incident date"),
    description: String(f.description || "").trim().length < 10 ? "Describe what happened (at least 10 characters)" : v.maxLen(f.description, 4000, "Description"),
    lost_days: v.range(f.lost_days, 0, 366, "Lost days") || (f.lost_time && !(Number(f.lost_days) > 0) ? "A lost-time injury needs the working days lost" : null),
    person: f.incident_type === "injury" && !f.employee_id && !String(f.employee_name || "").trim() ? "Name the injured person (pick the employee, or type a name for a visitor / contractor)" : null,
    wcb_claim_no: v.maxLen(f.wcb_claim_no, 40, "Report / claim no"),
    location: v.maxLen(f.location, 200, "Location"),
  });
  const save = useMutation({
    mutationFn: () => complianceApi.createIncident({ ...f, description: f.description.trim(), lost_days: Number(f.lost_days) || 0, employee_id: f.employee_id || undefined, reported_on: f.reported_on || undefined,
      wcb_claim_no: f.wcb_claim_no.trim() || undefined, corrective_action: f.corrective_action.trim() || undefined }),
    onSuccess: () => { toast.success("Incident logged"); onSaved(); onClose(); },
    onError: () => toast.error("Could not log the incident — check the fields and try again"),
  });
  const submit = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } save.mutate(); };
  const lab = (t: ReactNode, req?: boolean) => <span className="block text-xs font-semibold text-gray-500 mb-1">{t}{req && <Req />}</span>;
  return (
    <AppModal title="Log safety incident" subtitle="Accident register entry — serious injuries and fatalities must also be reported to the provincial workers' compensation board (WCB / WSIB / CNESST) and the provincial OHS regulator." onClose={onClose} size="wide"
      footer={<><button className="btn-secondary text-sm" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-sm disabled:opacity-60" disabled={!f.description || save.isPending} onClick={submit}>{save.isPending ? "Saving…" : "Log incident"}</button></>}>
      <FormSection title="When & where">
        <FormGrid cols={4}>
          <label className="text-sm">{lab("Occurred on", true)}<input type="date" max={today()} className="input" value={f.occurred_on} onChange={(e) => set("occurred_on", e.target.value)} /><FieldError msg={errs.occurred_on} /></label>
          <label className="text-sm">{lab("Reported on")}<input type="date" min={f.occurred_on || undefined} max={today()} className="input" value={f.reported_on} onChange={(e) => set("reported_on", e.target.value)} /><span className="block text-[11px] text-gray-400 mt-0.5">The statutory notice clock runs from here</span><FieldError msg={errs.reported_on} /></label>
          <label className="text-sm">{lab("Location")}<input className="input" maxLength={200} placeholder="e.g. Plant 2 – loading dock" value={f.location} onChange={(e) => set("location", e.target.value)} /><FieldError msg={errs.location} /></label>
          <label className="text-sm">{lab("Branch")}<input className="input" placeholder="branch name" value={f.branch_name} onChange={(e) => set("branch_name", e.target.value)} /></label>
        </FormGrid>
      </FormSection>
      <FormSection title="Person involved" hint="Pick the employee so the incident links to their record; type a name only for a visitor or contractor.">
        <FormGrid cols={2}>
          <label className="text-sm">{lab("Employee", f.incident_type === "injury" && !f.employee_name)}<select className="input" value={f.employee_id} onChange={(e) => { const p = people.find((x) => x.id === e.target.value); setF((x: any) => ({ ...x, employee_id: e.target.value, employee_name: p ? `${p.first_name} ${p.last_name}`.trim() : x.employee_name })); }}><option value="">— not an employee / none —</option>{people.map((p) => <option key={p.id} value={p.id}>{p.first_name} {p.last_name}{p.employee_number ? ` · ${p.employee_number}` : ""}</option>)}</select><FieldError msg={errs.person} /></label>
          <label className="text-sm">{lab("Name (if not an employee)")}<input className="input" maxLength={200} placeholder="visitor / contractor name" value={f.employee_name} disabled={!!f.employee_id} onChange={(e) => set("employee_name", e.target.value)} /></label>
        </FormGrid>
      </FormSection>
      <FormSection title="Classification">
        <FormGrid cols={4}>
          <label className="text-sm">{lab("Type", true)}<select className="input" value={f.incident_type} onChange={(e) => set("incident_type", e.target.value)}>{TYPES.map((t) => <option key={t} value={t}>{cap(t)}</option>)}</select></label>
          <label className="text-sm">{lab("Severity", true)}<select className="input" value={f.severity} onChange={(e) => set("severity", e.target.value)}>{SEVERITIES.map((t) => <option key={t} value={t}>{cap(t)}</option>)}</select></label>
          <label className="text-sm">{lab("Status", true)}<select className="input" value={f.status} onChange={(e) => set("status", e.target.value)}>{STATUSES.map((t) => <option key={t} value={t}>{cap(t)}</option>)}</select></label>
          <label className="text-sm">{lab("Lost days")}<input type="number" min="0" step="0.5" className="input" value={f.lost_days} onChange={(e) => set("lost_days", e.target.value)} /><span className="block text-[11px] text-gray-400 mt-0.5">Working days missed; half-days allowed</span><FieldError msg={errs.lost_days} /></label>
          <div className="sm:col-span-2 lg:col-span-4 flex flex-wrap items-center gap-x-6 gap-y-2">
            <label className="flex items-center gap-2 text-sm text-gray-600"><input type="checkbox" checked={f.lost_time} onChange={(e) => set("lost_time", e.target.checked)} />Lost-time injury (missed scheduled work)</label>
            <label className="flex items-center gap-2 text-sm text-gray-600"><input type="checkbox" checked={f.wcb_reportable} onChange={(e) => set("wcb_reportable", e.target.checked)} />Reportable — WCB / WSIB claim or report to the OHS regulator</label>
          </div>
          {f.wcb_reportable && <label className="text-sm sm:col-span-2">{lab("Report / claim reference")}<input className="input" maxLength={40} placeholder="WCB claim no or OHS report ref" value={f.wcb_claim_no} onChange={(e) => set("wcb_claim_no", e.target.value)} /><FieldError msg={errs.wcb_claim_no} /></label>}
        </FormGrid>
      </FormSection>
      <FormSection title="What happened & what was done">
        <FormGrid cols={2}>
          <label className="text-sm">{lab("Description", true)}<textarea className="input min-h-[96px]" maxLength={4000} placeholder="What happened, injury and body part, witnesses…" value={f.description} onChange={(e) => set("description", e.target.value)} /><FieldError msg={errs.description} /></label>
          <label className="text-sm">{lab("Immediate / corrective action")}<textarea className="input min-h-[96px]" maxLength={4000} placeholder="First aid given, machine guarded, retraining…" value={f.corrective_action} onChange={(e) => set("corrective_action", e.target.value)} /></label>
        </FormGrid>
      </FormSection>
    </AppModal>
  );
}

export default function SafetyPage() {
  const [modal, setModal] = useState(false);
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const qc = useQueryClient();
  const { data: summ } = useQuery({ queryKey: ["safety-summary"], queryFn: () => complianceApi.safetySummary(2026) });
  const { data, isLoading } = useQuery({ queryKey: ["safety", status, type], queryFn: () => complianceApi.safety({ status: status || undefined, incident_type: type || undefined, limit: 100 }) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["safety"] }); qc.invalidateQueries({ queryKey: ["safety-summary"] }); };
  const advance = useMutation({
    mutationFn: ({ id, s }: { id: string; s: string }) => complianceApi.updateIncident(id, { status: s }),
    onSuccess: () => { toast.success("Status updated"); refresh(); },
    onError: () => toast.error("Update failed"),
  });

  return (
    <div>
      <PageHeader title="Health & Safety" icon={ShieldPlus} subtitle="Accident register · lost-time and statutory (WCB / OHS) reporting"
        actions={<button className="btn-primary text-sm inline-flex items-center gap-1.5" onClick={() => setModal(true)}><Plus className="w-4 h-4" />Log incident</button>} />

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <StatTile label="Incidents (yr)" value={summ?.total ?? "—"} />
        <StatTile label="Open" value={summ?.open ?? "—"} accent icon={AlertTriangle} />
        <StatTile label="Lost-time cases" value={summ?.lost_time_cases ?? "—"} icon={Clock} />
        <StatTile label="Lost days" value={summ?.total_lost_days ?? "—"} />
        <StatTile label="Reportable" value={summ?.wcb_reportable ?? "—"} />
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select className="input !py-1.5 !w-auto text-sm" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option>{STATUSES.map((s) => <option key={s} value={s}>{cap(s)}</option>)}</select>
        <select className="input !py-1.5 !w-auto text-sm" value={type} onChange={(e) => setType(e.target.value)}><option value="">All types</option>{TYPES.map((t) => <option key={t} value={t}>{cap(t)}</option>)}</select>
        {(status || type) && <button className="text-xs text-gray-400 hover:text-gray-600" onClick={() => { setStatus(""); setType(""); }}>clear</button>}
      </div>

      <Card padded={false}>
        {isLoading || !data ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
          : !data.items.length ? <Empty>No incidents match — the register is clear.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface text-gray-400 text-left text-[11px] uppercase tracking-wide">
                <tr><th className="px-5 py-2.5 font-semibold">Ref</th><th className="px-3 py-2.5 font-semibold">Date</th><th className="px-3 py-2.5 font-semibold">Type</th><th className="px-3 py-2.5 font-semibold">Severity</th><th className="px-3 py-2.5 font-semibold">Description</th><th className="px-3 py-2.5 font-semibold">Lost</th><th className="px-3 py-2.5 font-semibold">Status</th><th className="px-4 py-2.5 font-semibold" /></tr>
              </thead>
              <tbody>
                {data.items.map((i: any) => (
                  <tr key={i.id} className="border-t border-surface-border align-top">
                    <td className="px-5 py-2.5 font-mono text-xs text-gray-500">{i.ref}</td>
                    <td className="px-3 py-2.5 text-gray-600 whitespace-nowrap"><span className="inline-flex items-center gap-1"><CalendarDays className="w-3 h-3 text-gray-300" />{i.occurred_on}</span></td>
                    <td className="px-3 py-2.5"><span className="badge-purple capitalize">{cap(i.incident_type)}</span></td>
                    <td className="px-3 py-2.5"><span className={`${SEV_CLS[i.severity]} capitalize`}>{i.severity}</span></td>
                    <td className="px-3 py-2.5 text-gray-700 max-w-[22rem]"><div className="line-clamp-2">{i.description}</div><div className="text-[11px] text-gray-400">{[i.location, i.employee_name].filter(Boolean).join(" · ")}{i.wcb_reportable && <span className="text-red-500"> · WCB</span>}</div></td>
                    <td className="px-3 py-2.5 tabular-nums text-gray-500">{i.lost_time ? `${i.lost_days}d` : "—"}</td>
                    <td className="px-3 py-2.5"><span className={`${ST_CLS[i.status]} capitalize`}>{cap(i.status)}</span></td>
                    <td className="px-4 py-2.5 text-right">
                      {i.status !== "closed" && (
                        <select className="input !py-1 !w-auto text-xs" value="" onChange={(e) => e.target.value && advance.mutate({ id: i.id, s: e.target.value })}>
                          <option value="">move to…</option>
                          {STATUSES.filter((s) => s !== i.status).map((s) => <option key={s} value={s}>{cap(s)}</option>)}
                        </select>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {modal && <IncidentModal onClose={() => setModal(false)} onSaved={refresh} />}
    </div>
  );
}
