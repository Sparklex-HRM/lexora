import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { KeyRound, Plus, Ban, ScrollText } from "lucide-react";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { PageHeader, Badge } from "../../components/ui";
import { Dialog } from "../UsersPage";
import { SecretBox } from "../integrations/shared";
import { aiTokensApi, type AiToken, type AiToolCallRow } from "../../api/ai";
import { usersApi } from "../../api/identity";
import { errText, when } from "../../api/platform";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

/** AI access tokens (org admin): what Claude Desktop / ChatGPT / Copilot present to the MCP server. */
export default function AiTokensPage() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["ai-tokens"], queryFn: aiTokensApi.list });
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [calls, setCalls] = useState<AiToken | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["ai-tokens"] });
  const revoke = useMutation({ mutationFn: (id: string) => aiTokensApi.revoke(id), onSuccess: () => { toast.success("Token revoked"); refresh(); }, onError: (e: any) => toast.error(errText(e, "Could not revoke")) });

  const cols: GridColumn<AiToken>[] = [
    { key: "name", label: "Name", sortable: true },
    { key: "prefix", label: "Prefix", className: "font-mono text-xs" },
    { key: "user_email", label: "Acts as", sortable: true, render: (t) => <span>{t.user_email || t.user_id}<span className="text-gray-400"> · {t.role}</span></span>, text: (t) => `${t.user_email || t.user_id} ${t.role}` },
    { key: "scopes", label: "Scopes", render: (t) => <span className="flex gap-1">{t.scopes.map((s) => <Badge key={s} tone={s === "admin" ? "terminated" : s === "act" ? "on_leave" : "default"}>{s}</Badge>)}</span>, text: (t) => t.scopes.join(" ") },
    { key: "status", label: "Status", render: (t) => <Badge tone={t.status === "active" ? "active" : "terminated"}>{t.status}</Badge>, text: (t) => t.status },
    { key: "rate_per_min", label: "Rate / min", type: "number" },
    { key: "calls", label: "Calls", type: "number", sortable: true },
    { key: "last_used_at", label: "Last used", type: "date", render: (t) => <span className="text-xs">{t.last_used_at ? when(t.last_used_at) : "never"}</span>, text: (t) => t.last_used_at || "" },
    { key: "expires_at", label: "Expires", type: "date", render: (t) => <span className="text-xs">{t.expires_at ? when(t.expires_at) : "—"}</span>, text: (t) => t.expires_at || "" },
    { key: "created_by", label: "Created by", render: (t) => <span className="text-xs">{t.created_by} · {t.created_at ? when(t.created_at) : ""}</span>, text: (t) => t.created_by || "" },
    { key: "_", label: "", render: (t) => <span className="flex gap-1">
      <button className="icon-btn text-gray-500" title="Call log" onClick={() => setCalls(t)}><ScrollText className="w-4 h-4" /></button>
      {t.status === "active" && <button className="icon-btn text-red-600" title="Revoke" onClick={() => { if (confirm(`Revoke "${t.name}"? Clients using it stop working at once.`)) revoke.mutate(t.id); }}><Ban className="w-4 h-4" /></button>}</span> },
  ];

  return (
    <div>
      <PageHeader title="AI access tokens" icon={KeyRound} subtitle="Tokens that external AI clients (Claude Desktop, ChatGPT, Copilot…) use to reach the Lexora HRM MCP server. Each token acts as ONE user with read / act / admin scopes, expires, can be revoked, and every call is logged."
        actions={<div className="flex gap-2"><Link to="/ai/settings" className="btn-secondary text-xs">AI settings</Link>
          <button className="btn-primary text-xs flex items-center gap-1" onClick={() => setCreating(true)}><Plus className="w-3.5 h-3.5" /> New token</button></div>} />
      <div className="mb-3 text-xs text-gray-600 card">
        <b>How to connect:</b> MCP endpoint <code className="font-mono bg-gray-100 px-1 rounded">{window.location.origin}/api/ai/mcp</code> with header <code className="font-mono bg-gray-100 px-1 rounded">Authorization: Bearer &lt;token&gt;</code>, or run the stdio bridge on a laptop. Step-by-step for Claude Desktop, ChatGPT and Copilot is in <code className="font-mono">docs/ai/MCP.md</code>.
      </div>
      <DataGrid<AiToken> columns={cols} rows={data?.items || []} rowKey={(t) => t.id} exportName="ai-access-tokens" empty="No AI access tokens yet." minWidth={1100} />
      {creating && <CreateToken defaultDays={data?.default_days || 90} onClose={() => setCreating(false)} onCreated={(tok) => { setCreating(false); setSecret(tok); refresh(); }} />}
      {secret && <SecretBox title="Your new AI access token" value={secret} note="Copy it now — it is not shown again. Put it in the AI client's config (see docs/ai/MCP.md)." onClose={() => setSecret(null)} />}
      {calls && <CallLog token={calls} onClose={() => setCalls(null)} />}
    </div>
  );
}

function CreateToken({ defaultDays, onClose, onCreated }: { defaultDays: number; onClose: () => void; onCreated: (token: string) => void }) {
  const users = useQuery({ queryKey: ["logins"], queryFn: () => usersApi.list() });
  const [name, setName] = useState("Claude Desktop");
  const [userId, setUserId] = useState("");
  const [scopes, setScopes] = useState<string[]>(["read"]);
  const [days, setDays] = useState(defaultDays);
  const [rate, setRate] = useState(60);
  const create = useMutation({
    mutationFn: () => { const u = (users.data?.items || []).find((x) => x.id === userId); if (!u) throw new Error("Pick a user");
      return aiTokensApi.create({ name, user_id: u.id, user_email: u.email, role: u.role, employee_id: u.employee_id, scopes, days, rate_per_min: rate }); },
    onSuccess: (r) => onCreated(r.token), onError: (e: any) => toast.error(errText(e, "Could not create")) });
  const toggle = (s: string) => setScopes((x) => (x.includes(s) ? x.filter((y) => y !== s) : [...x, s]));
  const whole = (n: number, lo: number, hi: number) => Number.isInteger(n) && n >= lo && n <= hi;
  const errs = collect({
    name: v.required(name.trim(), "Name") || v.maxLen(name, 120, "Name"),
    user: v.required(userId, "Acts as user"),
    days: whole(days, 1, 365) ? null : "Valid for is a whole number of days, 1 to 365",
    rate: whole(rate, 1, 600) ? null : "Rate limit is a whole number, 1 to 600 calls a minute",
  });
  const expires = whole(days, 1, 365) ? new Date(Date.now() + days * 864e5).toLocaleDateString("en-CA", { day: "2-digit", month: "short", year: "numeric" }) : "—";
  const submit = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } create.mutate(); };
  return (
    <Dialog title="New AI access token" sub="The token can never see or do more than the user it acts as." onClose={onClose}
      footer={<><button className="btn-secondary text-xs" onClick={onClose}>Cancel</button><button className="btn-primary text-xs" disabled={!userId || !name || create.isPending} onClick={submit}>Create token</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
        <label className="text-xs text-gray-600">Name (which client)<Req /><input className="input mt-1" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder="Claude Desktop — Asha's laptop" /><FieldError msg={errs.name} /></label>
        <label className="text-xs text-gray-600">Acts as user<Req /><select className="input mt-1" value={userId} onChange={(e) => setUserId(e.target.value)}>
          <option value="">— pick —</option>{(users.data?.items || []).filter((u) => u.status === "active" && u.role !== "superadmin").map((u) => <option key={u.id} value={u.id}>{u.email} · {u.role_label || u.role}{u.employee_name ? ` · ${u.employee_name}` : ""}</option>)}</select><span className="block text-[11px] text-gray-400 mt-0.5">The token can never see or do more than this user</span></label>
        <div className="text-xs text-gray-600 md:col-span-2">Scopes
          <div className="flex gap-2 mt-1">{(["read", "act", "admin"] as const).map((s) => (
            <label key={s} className="flex items-center gap-1.5 border border-surface-border rounded-lg px-3 py-2"><input type="checkbox" checked={scopes.includes(s)} disabled={s === "read"} onChange={() => toggle(s)} />
              <b className="capitalize">{s}</b><span className="text-gray-500">{s === "read" ? "— balances, pay stubs, policy, directory…" : s === "act" ? "— propose + confirm actions (leave, tickets, exit…)" : "— company AI usage (admin / HR only)"}</span></label>))}</div></div>
        <label className="text-xs text-gray-600">Valid for (days, max 365)<Req /><input className="input mt-1" type="number" min={1} max={365} step={1} value={days} onChange={(e) => setDays(Number(e.target.value))} /><span className="block text-[11px] text-gray-400 mt-0.5">Stops working on {expires}; you can revoke it earlier</span><FieldError msg={errs.days} /></label>
        <label className="text-xs text-gray-600">Rate limit (calls per minute)<Req /><input className="input mt-1" type="number" min={1} max={600} step={1} value={rate} onChange={(e) => setRate(Number(e.target.value))} /><span className="block text-[11px] text-gray-400 mt-0.5">Extra calls in a minute are turned away</span><FieldError msg={errs.rate} /></label>
      </div>
    </Dialog>
  );
}

function CallLog({ token, onClose }: { token: AiToken; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["ai-token-calls", token.id], queryFn: () => aiTokensApi.calls(token.id) });
  const cols: GridColumn<AiToolCallRow>[] = [
    { key: "at", label: "When", type: "date", render: (r) => <span className="text-xs">{r.at ? when(r.at) : ""}</span>, text: (r) => r.at || "" },
    { key: "tool", label: "Tool", className: "font-mono text-xs", sortable: true },
    { key: "ok", label: "Result", render: (r) => <Badge tone={r.ok ? "active" : "terminated"}>{r.ok ? "ok" : "error"}</Badge>, text: (r) => (r.ok ? "ok" : "error") },
    { key: "error", label: "Error", className: "text-xs text-red-700" },
    { key: "args", label: "Arguments (shielded)", render: (r) => <code className="text-[10px]">{JSON.stringify(r.args)}</code>, text: (r) => JSON.stringify(r.args) },
    { key: "duration_ms", label: "ms", type: "number" },
  ];
  return (
    <Dialog title={`Call log — ${token.name}`} sub={data?.note || ""} onClose={onClose} footer={<button className="btn-primary text-xs" onClick={onClose}>Close</button>}>
      <DataGrid<AiToolCallRow> columns={cols} rows={data?.items || []} rowKey={(r) => r.id} exportName={`ai-token-${token.prefix}`} empty="No calls yet." minWidth={900} />
    </Dialog>
  );
}
