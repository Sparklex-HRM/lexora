import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { MessageCircle, Save } from "lucide-react";
import { errText } from "../../../../api/platform";
import { ADMIN_ROLES } from "../../../../store/authStore";
import { useCan } from "../../../../config/permissions";
import { channelsApi, type WhatsApp } from "./api";

/** Agent reminders on WhatsApp (2026-10-26): off by default; in-app → e-mail → WhatsApp; approved template only;
 *  per-employee opt-in to "agent reminders" required; the token lives in a server environment variable (only its NAME
 *  is saved here). Without the full configuration nothing is sent — the log says "would send". */
export default function WhatsAppChannelCard() {
  const qc = useQueryClient();
  const isAdmin = useCan("ai.admin", ADMIN_ROLES);
  const q = useQuery({ queryKey: ["agentic", "channels", "whatsapp"], queryFn: channelsApi.whatsapp });
  const [v, setV] = useState<WhatsApp["value"] | null>(null);
  const [emp, setEmp] = useState("");
  const [preview, setPreview] = useState<any>(null);
  useEffect(() => { if (q.data) setV(JSON.parse(JSON.stringify(q.data.value))); }, [q.data]);
  const save = useMutation({ mutationFn: () => channelsApi.saveWhatsapp(v || {}), onSuccess: () => { toast.success("WhatsApp channel saved"); qc.invalidateQueries({ queryKey: ["agentic", "channels"] }); },
    onError: (e) => toast.error(errText(e)) });
  const pv = useMutation({ mutationFn: () => channelsApi.preview(emp.trim(), Number(v?.after_reminders || 2)), onSuccess: setPreview, onError: (e) => toast.error(errText(e)) });
  if (!q.data || !v) return null;
  const rd = q.data.readiness;
  return (
    <div className="card text-sm" data-testid="whatsapp-channel">
      <div className="flex items-center gap-2 mb-1"><MessageCircle className="w-4 h-4 text-green-600" /><h3 className="font-semibold text-gray-800">Agent reminders on WhatsApp</h3>
        <span className={v.enabled ? "badge-green" : "badge-amber"}>{v.enabled ? "On" : "Off"}</span>
        <span className={`ml-auto text-xs ${rd.can_send ? "text-green-700" : "text-gray-500"}`}>{rd.can_send ? "Ready to send" : `Nothing is sent — missing: ${rd.missing.join(", ")}`}</span></div>
      <p className="text-gray-600 mb-3">{q.data.note}</p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <label className="flex items-center gap-2 text-gray-700"><input type="checkbox" disabled={!isAdmin} checked={v.enabled} onChange={(e) => setV({ ...v, enabled: e.target.checked })} />Send reminders on WhatsApp</label>
        <label className="text-xs text-gray-600">Meta phone number id<input className="input mt-1" disabled={!isAdmin} value={v.phone_number_id} onChange={(e) => setV({ ...v, phone_number_id: e.target.value })} /></label>
        <label className="text-xs text-gray-600">Token environment variable (name only)<input className="input mt-1" disabled={!isAdmin} placeholder="LEXORA_WA_TOKEN" value={v.token_env} onChange={(e) => setV({ ...v, token_env: e.target.value.toUpperCase() })} /></label>
        <label className="text-xs text-gray-600">Approved template for agent reminders<input className="input mt-1" disabled={!isAdmin} placeholder="lexora_task_reminder" value={v.templates.agent_chase} onChange={(e) => setV({ ...v, templates: { agent_chase: e.target.value } })} /></label>
        <label className="text-xs text-gray-600">WhatsApp from reminder number<input className="input mt-1" type="number" min={1} max={10} disabled={!isAdmin} value={v.after_reminders} onChange={(e) => setV({ ...v, after_reminders: Number(e.target.value) || 2 })} /></label>
        <label className="text-xs text-gray-600">Template language<input className="input mt-1" disabled={!isAdmin} value={v.language} onChange={(e) => setV({ ...v, language: e.target.value })} /></label>
      </div>
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        {isAdmin && <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}><Save className="w-4 h-4" />Save</button>}
        <input className="input w-72" placeholder="Employee id — dry run (never sends)" value={emp} onChange={(e) => setEmp(e.target.value)} />
        <button className="btn-secondary" disabled={!emp.trim() || pv.isPending} onClick={() => pv.mutate()}>Preview</button>
      </div>
      {preview && <div className="mt-2 rounded-lg border border-surface-border p-3 text-xs text-gray-700">Channels: <b>{preview.plan.join(" → ")}</b> · consent {preview.consent.ok ? "given" : `not usable (${preview.consent.reason})`} · {preview.would_send ? <>would send template <b>{preview.would_send.template?.name || preview.would_send.error}</b> to {preview.consent.to}</> : "no WhatsApp message"} · sent: no</div>}
      <p className="mt-2 text-xs text-gray-500">Consent is the employee's WhatsApp opt-in with the event "agent_chase" (Reminders from Lexora HRM's agents) on the current number — a general opt-in does not cover agent reminders, and a changed number needs a new opt-in.</p>
    </div>
  );
}
