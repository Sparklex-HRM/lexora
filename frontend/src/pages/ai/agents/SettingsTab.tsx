import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { Power, Save, ShieldCheck, ToggleLeft, ToggleRight } from "lucide-react";
import { Spinner } from "../../../components/ui";
import { errText, when } from "../../../api/platform";
import { agenticApi, THRESHOLD_LABEL, type AgentSettings, type SessionGrant } from "../../../api/agentic";
import { ModeBadge } from "./shared";

/** Mode switch (shadow → live gated), approver roles per tier, thresholds, escalation roles, active session grants. */
export default function SettingsTab() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["agentic", "settings"], queryFn: agenticApi.settings });
  const grants = useQuery({ queryKey: ["agentic", "grants"], queryFn: agenticApi.grants });
  const [v, setV] = useState<AgentSettings["value"] | null>(null);
  const [reason, setReason] = useState("");
  useEffect(() => { if (q.data) setV(JSON.parse(JSON.stringify(q.data.value))); }, [q.data]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["agentic"] });
  const save = useMutation({ mutationFn: (val: Record<string, any>) => agenticApi.saveSettings(val), onSuccess: () => { toast.success("Agent settings saved"); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const mode = useMutation({ mutationFn: (p: { mode: "shadow" | "live"; reason: string }) => agenticApi.setMode(p.mode, p.reason),
    onSuccess: (r) => { toast.success(r.value.mode === "live" ? "Live mode on — proposals now go to the approval inbox and execute once approved" : "Back in shadow mode — nothing executes"); setReason(""); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const revoke = useMutation({ mutationFn: (id: string) => agenticApi.revokeGrant(id), onSuccess: () => { toast.success("Session grant revoked"); refresh(); }, onError: (e) => toast.error(errText(e)) });
  if (q.isLoading || !v || !q.data) return <Spinner />;
  const canEdit = q.data.can_edit;
  const roles = q.data.roles;
  const toggleRole = (tier: "tier1_roles" | "tier2_roles", r: string) => setV({ ...v, approvers: { ...v.approvers, [tier]: v.approvers[tier].includes(r) ? v.approvers[tier].filter((x) => x !== r) : [...v.approvers[tier], r] } });
  const toggleEsc = (r: string) => setV({ ...v, escalation_roles: v.escalation_roles.includes(r) ? v.escalation_roles.filter((x) => x !== r) : [...v.escalation_roles, r] });
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <section className="card space-y-3">
        <h2 className="font-display font-semibold text-gray-800 inline-flex items-center gap-2"><Power className="w-4 h-4" /> Add-on and mode</h2>
        <div className="text-sm text-gray-700 flex items-center gap-2 flex-wrap">
          <ModeBadge mode={v.mode} enabled={v.enabled !== false && (v.enabled === true || v.addon_purchased)} />
          <span>{v.addon_purchased ? "Agentic operations add-on is on the plan." : <>Add-on not purchased — <Link to="/plan-billing" className="text-primary-600 underline">Plan &amp; billing</Link>.</>}</span>
        </div>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={!canEdit} checked={v.enabled === true || (v.enabled === null && v.addon_purchased)} onChange={(e) => save.mutate({ enabled: e.target.checked ? (v.addon_purchased ? null : true) : false })} /> Agents switched on for this company <span className="text-xs text-gray-400">(unticking pauses them without changing the plan)</span></label>
        <div className="rounded-xl border border-surface-border p-3 space-y-2">
          <div className="flex items-center justify-between gap-2"><div><div className="font-semibold text-gray-800 text-sm">Mode: {v.mode === "live" ? "Live" : "Shadow"}</div><div className="text-xs text-gray-500">{v.live_gate_note}</div></div>
            {canEdit && (v.mode === "live"
              ? <button className="btn-secondary text-xs inline-flex items-center gap-1" disabled={mode.isPending} onClick={() => mode.mutate({ mode: "shadow", reason: "" })}><ToggleLeft className="w-4 h-4" /> Back to shadow</button>
              : <button className="btn-primary text-xs inline-flex items-center gap-1" disabled={mode.isPending || (!v.can_go_live && reason.trim().length < 10)} onClick={() => mode.mutate({ mode: "live", reason })}><ToggleRight className="w-4 h-4" /> Switch to live</button>)}</div>
          {v.mode !== "live" && !v.can_go_live && canEdit && <div><input className="input text-sm" placeholder="Admin override — why go live before two shadow cycles? (at least 10 characters, recorded in the log)" value={reason} onChange={(e) => setReason(e.target.value)} /></div>}
          <div className="text-xs text-gray-500">Shadow cycles done: <b>{v.shadow_cycles_done}</b> of {v.live_after_shadow_cycles}. {v.live_override && <span>Override on record by <b>{v.live_override.by}</b> ({when(v.live_override.at)}): “{v.live_override.reason}”.</span>}</div>
        </div>
      </section>
      <section className="card space-y-3">
        <h2 className="font-display font-semibold text-gray-800 inline-flex items-center gap-2"><ShieldCheck className="w-4 h-4" /> Who approves</h2>
        {(["tier1_roles", "tier2_roles"] as const).map((tier) => <div key={tier}>
          <div className="text-xs font-semibold text-gray-600 mb-1">{tier === "tier1_roles" ? "Tier 1 — one approver (master data, payroll inputs, ticket closure, outbound mail)" : "Tier 2 — two DIFFERENT approvers (bank details, net pay above threshold, filings, money, bulk)"}</div>
          <div className="flex flex-wrap gap-1.5">{roles.map((r) => <label key={r} className={`text-xs px-2 py-1 rounded-lg border cursor-pointer ${v.approvers[tier].includes(r) ? "border-primary-400 bg-primary-50 text-primary-700" : "border-surface-border text-gray-600"}`}><input type="checkbox" className="mr-1" disabled={!canEdit} checked={v.approvers[tier].includes(r)} onChange={() => toggleRole(tier, r)} />{r.replace(/_/g, " ")}</label>)}</div></div>)}
        <div><div className="text-xs font-semibold text-gray-600 mb-1">Escalation (after the reminder limit)</div>
          <div className="flex flex-wrap gap-1.5">{roles.map((r) => <label key={r} className={`text-xs px-2 py-1 rounded-lg border cursor-pointer ${v.escalation_roles.includes(r) ? "border-primary-400 bg-primary-50 text-primary-700" : "border-surface-border text-gray-600"}`}><input type="checkbox" className="mr-1" disabled={!canEdit} checked={v.escalation_roles.includes(r)} onChange={() => toggleEsc(r)} />{r.replace(/_/g, " ")}</label>)}</div></div>
        <p className="text-[11px] text-gray-400">The approver on every decision is the signed-in person (server-side), never a name typed in. An untrusted document in a run raises every action one tier regardless of these lists.</p>
      </section>
      <section className="card space-y-3">
        <h2 className="font-display font-semibold text-gray-800">Thresholds and cadence</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">{Object.entries(v.thresholds).map(([k, n]) => <label key={k} className="text-xs text-gray-600 flex items-center justify-between gap-2 border border-surface-border rounded-lg px-2 py-1.5"><span>{THRESHOLD_LABEL[k] || k.replace(/_/g, " ")}</span>
          <input type="number" min={0} className="input !py-1 w-24 text-right" disabled={!canEdit} value={n} onChange={(e) => setV({ ...v, thresholds: { ...v.thresholds, [k]: Number(e.target.value) } })} /></label>)}</div>
        {canEdit && <button className="btn-primary text-xs inline-flex items-center gap-1" disabled={save.isPending} onClick={() => save.mutate({ approvers: v.approvers, thresholds: v.thresholds, escalation_roles: v.escalation_roles })}><Save className="w-3.5 h-3.5" /> Save approvers, thresholds and escalation</button>}
      </section>
      <section className="card space-y-2">
        <h2 className="font-display font-semibold text-gray-800">Active "approve for session" grants</h2>
        {(grants.data?.items || []).length === 0 ? <p className="text-sm text-gray-500">None. A grant lets one tool from one agent run without a card until it expires; every use is logged with the grantor's name.</p> : (
          <table className="w-full text-[13px]"><thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-3 py-2">Agent</th><th className="px-3 py-2">Tool</th><th className="px-3 py-2">Granted by</th><th className="px-3 py-2">Expires</th><th className="px-3 py-2 text-right">Uses</th><th></th></tr></thead>
            <tbody>{(grants.data?.items || []).map((g: SessionGrant) => <tr key={g.id} className="border-t border-surface-border"><td className="px-3 py-1.5">{g.agent.replace(/_/g, " ")}</td><td className="px-3 py-1.5 font-mono text-xs">{g.tool_name}</td><td className="px-3 py-1.5">{g.approver_email || "—"} <span className="text-gray-400">({g.approver_role})</span></td><td className="px-3 py-1.5">{when(g.expires_at)}</td><td className="px-3 py-1.5 text-right tabular-nums">{g.uses}</td>
              <td className="px-3 py-1.5 text-right"><button className="btn-secondary !py-0.5 !px-2 text-xs" disabled={revoke.isPending} onClick={() => revoke.mutate(g.id)}>Revoke</button></td></tr>)}</tbody></table>)}
      </section>
    </div>
  );
}
