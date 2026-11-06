import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { MessageCircle, Send, ShieldCheck } from "lucide-react";
import api from "../../api/client";
import { PageHeader } from "../../components/ui";
import { apiErr } from "../../api/devicePush";
import { Grid, Stat } from "../common/_shared";
import { formatDateTime } from "../../lib/datetime";   // 2026-10-25: instants shown in the viewer's zone
import { useCan } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/** G7 (2026-10-23) — decide approvals from WhatsApp. Approvers opt in here (explicit "approvals" consent on their
 *  current number); admins see whether the org can send and the message log. Without Meta keys nothing is sent and
 *  the page says so. */
interface Me { consent_text: string; org_ready: boolean; org_reason: string | null; ttl_hours: number;
  items: { title: string; status: string; decision: string | null; phone: string; created_at: string | null; decided_at: string | null }[]; }
interface Status { ready: boolean; reason: string | null; mode: string; enabled: boolean; template_set: boolean; opted_in_approvers: number;
  ttl_hours: number; reason_minutes: number; counts: Record<string, number>; }
const waApi = {
  me: () => api.get<Me>("/approvals/whatsapp/me").then((r) => r.data),
  status: () => api.get<Status>("/approvals/whatsapp/status").then((r) => r.data),
  requests: () => api.get<{ items: any[] }>("/approvals/whatsapp/requests").then((r) => r.data),
  dispatch: () => api.post<{ sent: number; skipped: number; failed: number; not_configured: string | null }>("/approvals/whatsapp/dispatch").then((r) => r.data),
};
interface WaOptin { id: string; employee_id: string; phone: string; status: string; source: string; events: string[]; consent_text: string; recorded_by: string | null; at: string | null; revoked_at: string | null }
/* Backend: integrations service, /integrations/whatsapp/optin* (whatsapp_routes.py). */
const waOptinApi = {
  me: () => api.get<{ optin: WaOptin | null; default_consent: string }>("/integrations/whatsapp/optin/me").then((r) => r.data),
  optIn: (body: { events: string[]; consent_text: string; employee_id?: string }) => api.post<WaOptin>("/integrations/whatsapp/optin", body).then((r) => r.data),
  optOut: (employee_id?: string) => api.post("/integrations/whatsapp/optout", employee_id ? { employee_id } : {}).then((r) => r.data),
};
const WORD: Record<string, string> = { sent: "Sent — waiting", not_configured: "Not configured", failed: "Failed", processing: "Working",
  awaiting_reason: "Waiting for a reason", done: "Decided", refused: "Refused by Lexora HRM", expired: "Expired", superseded: "Decided elsewhere", cancelled: "Cancelled" };
const OFFICE = ["superadmin", "org_admin", "hr_admin", "hr_manager"];
const ALL_EVENTS = ["payslip_ready", "leave_approved", "attendance_missing", "invite"];

export default function WhatsAppApprovalsPage() {
  const qc = useQueryClient();
  const office = useCan("approvals.view", OFFICE);
  const canSend = useCan("approvals.admin", ["org_admin", "superadmin"]);
  const { data: me } = useQuery({ queryKey: ["wa-appr-me"], queryFn: waApi.me });
  const { data: mine } = useQuery({ queryKey: ["wa-optin-me"], queryFn: waOptinApi.me });
  const { data: st } = useQuery({ queryKey: ["wa-appr-status"], queryFn: waApi.status, enabled: office });
  const { data: log, isLoading } = useQuery({ queryKey: ["wa-appr-log"], queryFn: waApi.requests, enabled: office });
  const o = mine?.optin;
  const active = o?.status === "opted_in";
  const hasApprovals = active && (o?.events || []).includes("approvals");
  const refresh = () => ["wa-optin-me", "wa-appr-me", "wa-appr-status", "wa-appr-log"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const optIn = useMutation({
    mutationFn: () => {
      // keep what the person already agreed to; an old blanket consent ([] = every notification) is written out in full
      const prev = active ? (o!.events.length ? o!.events : ALL_EVENTS) : [];
      const text = [active ? o!.consent_text : "", me?.consent_text || ""].filter(Boolean).join("\n\n");
      return waOptinApi.optIn({ events: Array.from(new Set([...prev, "approvals"])), consent_text: text });
    },
    onSuccess: () => { toast.success("You will get approval requests on WhatsApp"); refresh(); }, onError: (e) => toast.error(apiErr(e)) });
  const optOut = useMutation({
    mutationFn: () => {
      const rest = (o?.events || []).filter((e) => e !== "approvals");
      return rest.length ? waOptinApi.optIn({ events: rest, consent_text: o!.consent_text }) : waOptinApi.optOut();
    },
    onSuccess: () => { toast.success("Approval requests on WhatsApp stopped"); refresh(); }, onError: (e) => toast.error(apiErr(e)) });
  const send = useMutation({ mutationFn: waApi.dispatch, onSuccess: (r) => { toast[r.not_configured ? "error" : "success"](r.not_configured || `${r.sent} sent · ${r.skipped} skipped · ${r.failed} failed`); refresh(); }, onError: (e) => toast.error(apiErr(e)) });
  return (
    <div>
      <PageHeader title="WhatsApp approvals" icon={MessageCircle} subtitle="Approve or reject from WhatsApp with one tap. Each button works once, only for you, only while the request is open — and runs the same checks as the Approvals inbox." />
      <div className="card mb-4">
        <div className="flex flex-wrap items-center gap-2 mb-2"><ShieldCheck className="w-4 h-4 text-primary-600" /><b className="text-sm">My setting</b>
          <span className={`ml-2 ${hasApprovals ? "badge-green" : "badge-purple"}`}>{hasApprovals ? `On · ${o?.phone}` : "Off"}</span></div>
        {me && !me.org_ready && <p className="text-xs text-amber-700 bg-amber-50 rounded px-2 py-1.5 mb-2">{me.org_reason} Until then nothing is sent; you can still opt in now.</p>}
        <p className="text-xs text-gray-600 mb-2 whitespace-pre-line">{me?.consent_text}</p>
        <p className="text-[11px] text-gray-400 mb-3">Requests expire after {me?.ttl_hours ?? 48} hours. Rejecting asks you for a reason by reply. Messages go to the mobile number on your employee record; a new number needs a fresh opt-in.</p>
        {hasApprovals
          ? <button className="btn-secondary" disabled={optOut.isPending} onClick={() => optOut.mutate()}>Stop approval requests on WhatsApp</button>
          : <button className="btn-primary" disabled={optIn.isPending || !me} onClick={() => optIn.mutate()}>I agree — send me approval requests on WhatsApp</button>}
        {(me?.items || []).length > 0 && <div className="mt-3"><Grid rows={me!.items} keyOf={(r: any) => r.created_at + r.title} title="My recent WhatsApp requests" cols={[
          { k: "created_at", label: "Sent", render: (r: any) => formatDateTime(r.created_at) }, { k: "title", label: "Request" },
          { k: "status", label: "State", render: (r: any) => WORD[r.status] || r.status }, { k: "decision", label: "Decision", render: (r: any) => r.decision || "—" }]} /></div>}
      </div>

      {office && <>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-3">
          <Stat label="Ready to send" value={st ? (st.ready ? "Yes" : "No") : "—"} sub={st?.reason || (st ? "Meta Cloud API + template set" : undefined)} accent={!!st?.ready} />
          <Stat label="Approvers opted in" value={st?.opted_in_approvers ?? "—"} />
          <Stat label="Waiting" value={(st?.counts?.sent || 0) + (st?.counts?.awaiting_reason || 0)} />
          <Stat label="Decided on WhatsApp" value={st?.counts?.done || 0} />
          <Stat label="Refused / expired" value={(st?.counts?.refused || 0) + (st?.counts?.expired || 0)} />
          <Stat label="Not configured" value={st?.counts?.not_configured || 0} sub="logged, not sent" />
        </div>
        <Grid rows={log?.items || []} keyOf={(r: any) => r.id} loading={isLoading} exportName="whatsapp_approvals.csv" title="WhatsApp approval messages"
          empty="Nothing sent yet. Approvers opt in above; the org needs WhatsApp (Meta) switched on with an approval template under Integrations."
          actions={canSend ? <button className="btn-secondary !py-1 text-xs flex items-center gap-1" disabled={send.isPending} onClick={() => send.mutate()}><Send className="w-3 h-3" /> Send pending now</button> : undefined}
          cols={[
            { k: "created_at", label: "Sent", render: (r: any) => formatDateTime(r.created_at) },
            { k: "title", label: "Request" }, { k: "approver", label: "Approver" }, { k: "phone", label: "To" },
            { k: "status", label: "State", render: (r: any) => WORD[r.status] || r.status },
            { k: "decision", label: "Decision", render: (r: any) => r.decision || "—" },
            { k: "result", label: "Note", render: (r: any) => <span className="text-[11px] text-gray-500">{r.result || "—"}</span> },
            { k: "expires_at", label: "Expires", render: (r: any) => formatDateTime(r.expires_at) },
          ]} />
      </>}
    </div>
  );
}
