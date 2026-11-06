import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import api from "../../api/client";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Dialog } from "../UsersPage";
import { bgvApi, errText, when } from "../../api/integrations";
import { LABEL, Section } from "./shared";

// Integrations service (S5): WhatsApp consent (Meta Cloud API), the masked provider-call log and the inbound-webhook log.
export interface OptinRow { id: string; employee_id: string; phone: string; status: string; source: string; events: string[];
  consent_text: string; recorded_by: string | null; at: string | null; revoked_at: string | null; }
const waOptinApi = {
  events: () => api.get<{ events: { key: string; label: string; kind: string; template: string | null; own_template: boolean }[];
    default_consent: string; provider_enabled: boolean; mode: string }>("/integrations/whatsapp/events").then((r) => r.data),
  list: () => api.get<{ items: OptinRow[] }>("/integrations/whatsapp/optins").then((r) => r.data),
  optIn: (body: { employee_id?: string; events?: string[]; consent_text?: string }) => api.post<OptinRow>("/integrations/whatsapp/optin", body).then((r) => r.data),
  optOut: (employee_id?: string) => api.post<OptinRow>("/integrations/whatsapp/optout", { employee_id }).then((r) => r.data),
  send: (employee_id: string, event: string) => api.post<{ status: string; error: string | null }>("/integrations/whatsapp/send", { employee_id, event }).then((r) => r.data),
};
export interface CallRow { id: string; provider: string; operation: string; method: string; host: string; path: string;
  http_status: number | null; ok: boolean; outcome: string; attempts: number; duration_ms: number; idempotency_key: string | null;
  request: any; response: any; error: string | null; at: string | null; }
interface InboundHook { id: string; provider: string; event: string; event_id: string; verified: boolean; outcome: string; received_at: string | null; }
const providerCallsApi = {
  list: (provider?: string) => api.get<{ items: CallRow[] }>("/integrations/calls", { params: { provider } }).then((r) => r.data),
  webhooks: () => api.get<{ items: InboundHook[] }>("/integrations/webhooks/log").then((r) => r.data),
};

const TONE: Record<string, string> = {
  paid: "badge-green", processed: "badge-green", signed: "badge-green", verified: "badge-green", complete: "badge-green", opted_in: "badge-green",
  overdue: "badge-red", failed: "badge-red", reversed: "badge-red", rejected: "badge-red", mismatch: "badge-red", cancelled: "badge-red", opted_out: "badge-red",
  due_soon: "badge-amber", unknown: "badge-amber", needs_attention: "badge-amber", not_configured: "badge-amber", unauthenticated: "badge-amber",
  upcoming: "badge-purple", processing: "badge-teal", queued: "badge-teal", sent: "badge-teal", sign_initiated: "badge-teal", authenticated: "badge-teal",
};
export const Pill = ({ s }: { s: string | null | undefined }) => <span className={TONE[s || ""] || "badge-purple"}>{(s || "—").replace(/_/g, " ")}</span>;

function useEmployees() {
  return useQuery({ queryKey: ["integrations", "bgv-meta"], queryFn: bgvApi.meta }).data?.employees || [];
}

// ═══════════════════════════════ WhatsApp consent ════════════════════════════════

export function WhatsAppConsentSection() {
  const qc = useQueryClient();
  const emps = useEmployees();
  const name = (id: string) => emps.find((e) => e.id === id)?.name || id.slice(0, 8);
  const { data: ev } = useQuery({ queryKey: ["integrations", "wa-events"], queryFn: waOptinApi.events });
  const { data } = useQuery({ queryKey: ["integrations", "wa-optins"], queryFn: waOptinApi.list });
  const [emp, setEmp] = useState("");
  const [how, setHow] = useState("");
  const record = useMutation({ mutationFn: () => waOptinApi.optIn({ employee_id: emp, consent_text: how }),
    onSuccess: () => { toast.success("Consent recorded"); setHow(""); qc.invalidateQueries({ queryKey: ["integrations", "wa-optins"] }); },
    onError: (e: any) => toast.error(errText(e, "Could not record")) });
  const out = useMutation({ mutationFn: (id: string) => waOptinApi.optOut(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["integrations", "wa-optins"] }) });
  const send = useMutation({ mutationFn: (v: { e: string; k: string }) => waOptinApi.send(v.e, v.k),
    onSuccess: (r) => (r.status === "sent" ? toast.success : toast.error)(`${r.status}${r.error ? ` — ${r.error}` : ""}`),
    onError: (e: any) => toast.error(errText(e, "Not sent")) });
  const cols: GridColumn<OptinRow>[] = [
    { key: "employee_id", label: "Employee", text: (r) => name(r.employee_id), render: (r) => name(r.employee_id) },
    { key: "phone", label: "Number" }, { key: "status", label: "Status", render: (r) => <Pill s={r.status} /> },
    { key: "source", label: "Given", render: (r) => r.source === "self" ? "By the employee" : "Recorded by HR" },
    { key: "events", label: "Messages", text: (r) => r.events.join(", ") || "all", render: (r) => <span className="text-[11px]">{r.events.join(", ") || "all"}</span> },
    { key: "consent_text", label: "Consent", render: (r) => <span className="text-[11px] line-clamp-2">{r.consent_text}</span> },
    { key: "at", label: "When", text: (r) => r.at || "", render: (r) => when(r.at) },
    { key: "actions", label: "", render: (r) => r.status === "opted_in" ? <div className="flex gap-1 justify-end">
      <select className="input !py-0.5 text-[11px] !w-36" defaultValue="" onChange={(e) => { if (e.target.value) send.mutate({ e: r.employee_id, k: e.target.value }); e.target.value = ""; }}>
        <option value="">Send now…</option>{(ev?.events || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</select>
      <button className="btn-secondary !py-0.5 !px-1.5 text-[11px]" onClick={() => out.mutate(r.employee_id)}>Opt out</button></div> : null },
  ];
  return (
    <Section title="WhatsApp consent and message templates" sub="Messages go only to employees who opted in on their current number. Each event uses its own approved template ({{1}} title, {{2}} link) or the generic one.">
      <div className="card !p-0 overflow-x-auto mb-3"><table className="w-full text-[12px]"><thead><tr className="text-left text-gray-500">
        <th className="px-3 py-1.5">Event</th><th className="px-3 py-1.5">Triggered by</th><th className="px-3 py-1.5">Template</th></tr></thead>
        <tbody>{(ev?.events || []).map((x) => <tr key={x.key} className="border-t border-surface-border"><td className="px-3 py-1.5">{x.label}</td>
          <td className="px-3 py-1.5">{x.kind} notifications</td><td className="px-3 py-1.5 font-mono">{x.template || <span className="text-amber-700">not set</span>}{!x.own_template && x.template ? " (generic)" : ""}</td></tr>)}</tbody></table></div>
      <div className="card !p-3 mb-3 grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
        <div><label className={LABEL}>Employee</label><select className="input" value={emp} onChange={(e) => setEmp(e.target.value)}><option value="">— pick —</option>
          {emps.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></div>
        <div className="md:col-span-2"><label className={LABEL}>How consent was given (required)</label><input className="input" value={how} onChange={(e) => setHow(e.target.value)} placeholder="Signed onboarding form, Oct 12, 2026" /></div>
        <button className="btn-primary text-xs disabled:opacity-60" disabled={!emp || !how.trim() || record.isPending} onClick={() => record.mutate()}>Record opt-in</button>
      </div>
      <DataGrid<OptinRow> columns={cols} rows={data?.items || []} rowKey={(r) => r.id} exportName="whatsapp-consents" empty="No WhatsApp consent on record — nothing is sent on WhatsApp." minWidth={1000} />
    </Section>
  );
}

// ═══════════════════════════════ Provider calls ══════════════════════════════════

export function ProviderCallsTab() {
  const [prov, setProv] = useState("");
  const { data } = useQuery({ queryKey: ["integrations", "calls", prov], queryFn: () => providerCallsApi.list(prov || undefined) });
  const { data: hooks } = useQuery({ queryKey: ["integrations", "inbound-hooks"], queryFn: providerCallsApi.webhooks });
  const [open, setOpen] = useState<CallRow | null>(null);
  const cols: GridColumn<CallRow>[] = [
    { key: "at", label: "When", text: (r) => r.at || "", render: (r) => when(r.at) }, { key: "provider", label: "Provider" }, { key: "operation", label: "Operation" },
    { key: "method", label: "Method" }, { key: "path", label: "Path", render: (r) => <span className="font-mono text-[11px]">{r.host}{r.path}</span> },
    { key: "http_status", label: "HTTP", text: (r) => String(r.http_status ?? "") }, { key: "outcome", label: "Outcome", render: (r) => <Pill s={r.ok ? "processed" : r.outcome === "unknown" ? "unknown" : "failed"} /> },
    { key: "attempts", label: "Tries", text: (r) => String(r.attempts) }, { key: "duration_ms", label: "ms", text: (r) => String(r.duration_ms) },
    { key: "error", label: "Error", render: (r) => <span className="text-[11px] text-red-700">{r.error}</span> },
  ];
  return (
    <>
      <Section title="Provider calls" sub="Every request Lexora made to an outside provider (WhatsApp, Slack, Teams, DocuSign, background checks) with personal data masked. Keys and headers are never stored."
        right={<select className="input !w-44" value={prov} onChange={(e) => setProv(e.target.value)}><option value="">All providers</option>
          {["whatsapp", "slack", "teams", "esign_docusign", "bgv"].map((p) => <option key={p} value={p}>{p}</option>)}</select>}>
        <DataGrid<CallRow> columns={cols} rows={data?.items || []} rowKey={(r) => r.id} exportName="provider-calls" onRowClick={setOpen} minWidth={1100} initialSort={{ key: "at", dir: "desc" }} empty="No provider calls yet." />
      </Section>
      <Section title="Webhooks received" sub="Provider → Lexora events. Duplicates are ignored by event id.">
        <DataGrid<InboundHook> columns={[{ key: "received_at", label: "When", render: (r) => when(r.received_at) }, { key: "provider", label: "Provider" }, { key: "event", label: "Event" },
          { key: "verified", label: "Signature", render: (r) => r.verified ? "verified" : "re-checked by pull" }, { key: "outcome", label: "Outcome" }]}
          rows={hooks?.items || []} rowKey={(r) => r.id} exportName="provider-webhooks" minWidth={800} empty="Nothing received yet." />
      </Section>
      {open && <Dialog title={`${open.provider} · ${open.operation}`} sub={`${open.method} ${open.host}${open.path} · HTTP ${open.http_status ?? "—"}`} onClose={() => setOpen(null)}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-[11px]">
          <div><div className={LABEL}>Request (masked)</div><pre className="bg-surface p-2 rounded overflow-auto max-h-[50vh]">{JSON.stringify(open.request, null, 2)}</pre></div>
          <div><div className={LABEL}>Response (masked)</div><pre className="bg-surface p-2 rounded overflow-auto max-h-[50vh]">{JSON.stringify(open.response, null, 2)}</pre></div>
        </div></Dialog>}
    </>
  );
}
