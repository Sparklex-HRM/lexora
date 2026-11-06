import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Ban, Plus, RefreshCw, Send, Trash2 } from "lucide-react";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Dialog } from "../UsersPage";
import { ApiKeyRow, DeliveryRow, WebhookRow, apiKeysApi, errText, webhooksApi, when } from "../../api/integrations";
import { LABEL, SecretBox, Section, Status } from "./shared";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

export function ApiKeysTab() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["integrations", "keys"], queryFn: apiKeysApi.list });
  const { data: meta } = useQuery({ queryKey: ["integrations", "keys", "scopes"], queryFn: apiKeysApi.scopes });
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["integrations"] });
  const revoke = useMutation({ mutationFn: (id: string) => apiKeysApi.revoke(id), onSuccess: () => { toast.success("Key revoked"); refresh(); },
    onError: (e: any) => toast.error(errText(e, "Could not revoke")) });
  const cols: GridColumn<ApiKeyRow>[] = [
    { key: "name", label: "Name", render: (k) => <span className="font-medium text-gray-800">{k.name}</span> },
    { key: "prefix", label: "Key", render: (k) => <span className="font-mono text-[11px]">{k.prefix}</span> },
    { key: "scopes", label: "Scopes", text: (k) => k.scopes.join(" "), render: (k) => <span className="font-mono text-[11px]">{k.scopes.join(", ")}</span> },
    { key: "role_label", label: "Acts as" }, { key: "status", label: "Status", render: (k) => <Status s={k.status} /> },
    { key: "expires_at", label: "Expires", text: (k) => k.expires_at || "", render: (k) => k.expires_at ? when(k.expires_at) : "Never" },
    { key: "last_used_at", label: "Last used", text: (k) => k.last_used_at || "", render: (k) => when(k.last_used_at) },
    { key: "created_by", label: "Created by" },
    { key: "a", label: "", sortable: false, text: () => "", render: (k) => k.status === "active"
      ? <button className="text-xs text-red-600 inline-flex items-center gap-1" onClick={() => revoke.mutate(k.id)}><Ban className="w-3 h-3" /> Revoke</button> : null },
  ];
  return (
    <div>
      <Section title="API keys" sub={meta?.how || ""}
        right={<button className="btn-primary text-xs flex items-center gap-1" onClick={() => setCreating(true)}><Plus className="w-3.5 h-3.5" /> New key</button>}>
        <DataGrid<ApiKeyRow> columns={cols} rows={data?.items || []} rowKey={(k) => k.id} exportName="api-keys" empty="No API keys yet." minWidth={1000} />
      </Section>
      {creating && meta && <CreateKey meta={meta} onClose={() => setCreating(false)} onDone={(key) => { setCreating(false); setSecret(key); refresh(); }} />}
      {secret && <SecretBox title="Your new API key" value={secret} onClose={() => setSecret(null)} />}
    </div>
  );
}

function CreateKey({ meta, onClose, onDone }: { meta: { services: string[]; roles: { key: string; label: string }[] }; onClose: () => void; onDone: (key: string) => void }) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("employee");
  const [days, setDays] = useState("365");
  const [scopes, setScopes] = useState<Set<string>>(new Set());
  const toggle = (s: string) => setScopes((p) => { const n = new Set(p); n.has(s) ? n.delete(s) : n.add(s); return n; });
  const m = useMutation({ mutationFn: () => apiKeysApi.create({ name: name.trim(), scopes: [...scopes], role, expires_days: days ? Number(days) : null }),
    onSuccess: (r) => onDone(r.key), onError: (e: any) => toast.error(errText(e, "Could not create the key")) });
  const errs = collect({
    name: v.required(name.trim(), "Name") || v.maxLen(name, 60, "Name"),
    days: days && !(Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 3650) ? "Expiry is a whole number of days, 1 to 3650 (or empty for never)" : null,
    scopes: scopes.size ? null : "Tick at least one scope",
  });
  const submit = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } m.mutate(); };
  return (
    <Dialog title="New API key" sub="The key acts for your organization with the role you pick, and only inside the scopes you tick." onClose={onClose}
      footer={<><button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs disabled:opacity-60" disabled={!name.trim() || !scopes.size || m.isPending} onClick={submit}>Create key</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div><label className={LABEL}>Name (what will use it)<Req /></label><input className="input" maxLength={60} value={name} autoFocus onChange={(e) => setName(e.target.value)} placeholder="e.g. Zapier, Tally sync" /></div>
        <div><label className={LABEL}>Acts as</label>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>{meta.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}</select><span className="block text-[11px] text-gray-400 mt-0.5">The key can do only what this role may, narrowed by the scopes</span></div>
        <div><label className={LABEL}>Expires in (days, empty = never)</label><input className="input" type="number" min={1} max={3650} step={1} value={days} onChange={(e) => setDays(e.target.value)} /><span className="block text-[11px] text-gray-400 mt-0.5">{days ? `Stops working on ${new Date(Date.now() + Number(days) * 864e5).toLocaleDateString("en-CA")}` : "Never expires — not advised for outside tools"}</span><FieldError msg={errs.days} /></div>
        <div className="md:col-span-3">
          <label className={LABEL}>Scopes<Req /></label>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1.5">
            {meta.services.map((s) => (
              <div key={s} className="flex items-center gap-2 text-xs border border-surface-border rounded-md px-2 py-1.5">
                <span className="font-mono flex-1">{s}</span>
                {["read", "write"].map((l) => (
                  <label key={l} className="flex items-center gap-1 cursor-pointer"><input type="checkbox" checked={scopes.has(`${s}:${l}`)} onChange={() => toggle(`${s}:${l}`)} />{l}</label>
                ))}
              </div>))}
          </div>
          <p className="text-[11px] text-gray-400 mt-1">read = GET only · write = everything. The gateway refuses anything outside these before it reaches a service.</p>
        </div>
      </div>
    </Dialog>
  );
}

export function WebhooksTab() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["integrations", "webhooks"], queryFn: webhooksApi.list });
  const { data: meta } = useQuery({ queryKey: ["integrations", "webhooks", "events"], queryFn: webhooksApi.events });
  const [fStatus, setFStatus] = useState("");
  const { data: del } = useQuery({ queryKey: ["integrations", "deliveries", fStatus], queryFn: () => webhooksApi.deliveries(fStatus ? { status: fStatus } : {}) });
  const [editing, setEditing] = useState<WebhookRow | "new" | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [payload, setPayload] = useState<DeliveryRow | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["integrations"] });
  const fail = (m: string) => (e: any) => toast.error(errText(e, m));
  const test = useMutation({ mutationFn: (id: string) => webhooksApi.test(id), onSuccess: (d) => { (d.status === "delivered" ? toast.success : toast.error)(`Test ${d.status}${d.http_status ? ` (HTTP ${d.http_status})` : ""}`); refresh(); }, onError: fail("Test failed") });
  const rotate = useMutation({ mutationFn: (id: string) => webhooksApi.rotate(id), onSuccess: (r) => setSecret(r.secret), onError: fail("Could not rotate") });
  const remove = useMutation({ mutationFn: (id: string) => webhooksApi.remove(id), onSuccess: () => { toast.success("Webhook removed"); refresh(); }, onError: fail("Could not remove") });
  const replay = useMutation({ mutationFn: (id: string) => webhooksApi.replay(id), onSuccess: (r) => { toast.success(`Replayed → ${r.status}`); refresh(); }, onError: fail("Could not replay") });
  const hcols: GridColumn<WebhookRow>[] = [
    { key: "url", label: "URL", render: (h) => <span className="font-mono text-[11px] break-all">{h.url}</span> },
    { key: "description", label: "Note" },
    { key: "events", label: "Events", text: (h) => h.events.join(" "), render: (h) => <span className="text-[11px]">{h.events.join(", ")}</span> },
    { key: "enabled", label: "On", text: (h) => (h.enabled ? "yes" : "no"), render: (h) => <Status s={h.enabled ? "active" : "revoked"} /> },
    { key: "last_status", label: "Last", render: (h) => h.last_status ? <Status s={h.last_status} /> : "—" },
    { key: "last_delivery_at", label: "Last delivery", text: (h) => h.last_delivery_at || "", render: (h) => when(h.last_delivery_at) },
    { key: "a", label: "", sortable: false, text: () => "", render: (h) => (
      <div className="flex gap-1 justify-end whitespace-nowrap">
        <button className="text-xs text-gray-600 inline-flex items-center gap-1 px-1.5 py-0.5 border border-surface-border rounded" onClick={() => test.mutate(h.id)}><Send className="w-3 h-3" /> Test</button>
        <button className="text-xs text-gray-600 inline-flex items-center gap-1 px-1.5 py-0.5 border border-surface-border rounded" onClick={() => setEditing(h)}>Edit</button>
        <button className="text-xs text-gray-600 inline-flex items-center gap-1 px-1.5 py-0.5 border border-surface-border rounded" onClick={() => rotate.mutate(h.id)}><RefreshCw className="w-3 h-3" /> Secret</button>
        <button className="text-xs text-red-600 inline-flex items-center gap-1 px-1.5 py-0.5 border border-surface-border rounded" onClick={() => { if (confirm("Remove this webhook?")) remove.mutate(h.id); }}><Trash2 className="w-3 h-3" /></button>
      </div>) },
  ];
  const dcols: GridColumn<DeliveryRow>[] = [
    { key: "created_at", label: "When", text: (d) => d.created_at || "", render: (d) => when(d.created_at) },
    { key: "event", label: "Event", render: (d) => <span className="font-mono text-[11px]">{d.event}</span> },
    { key: "url", label: "To", render: (d) => <span className="font-mono text-[11px] break-all">{d.url || d.webhook_id}</span> },
    { key: "status", label: "Status", render: (d) => <Status s={d.status} /> },
    { key: "attempt", label: "Tries", type: "number" }, { key: "http_status", label: "HTTP", type: "number" },
    { key: "next_attempt_at", label: "Next try", text: (d) => d.next_attempt_at || "", render: (d) => d.status === "failed" ? when(d.next_attempt_at) : "—" },
    { key: "response_excerpt", label: "Answer", render: (d) => <span className="text-[11px] text-gray-500">{(d.response_excerpt || "").slice(0, 80)}</span> },
    { key: "a", label: "", sortable: false, text: () => "", render: (d) => (
      <div className="flex gap-1 justify-end whitespace-nowrap">
        <button className="text-xs text-gray-600 px-1.5 py-0.5 border border-surface-border rounded" onClick={() => setPayload(d)}>Payload</button>
        <button className="text-xs text-primary-600 px-1.5 py-0.5 border border-surface-border rounded" onClick={() => replay.mutate(d.id)}>Replay</button>
      </div>) },
  ];
  return (
    <div>
      <Section title="Outbound webhooks" sub={meta ? `Signed with ${meta.signature}. Retries: ${meta.retries.join(", ")}, then marked "Gave up".` : ""}
        right={<button className="btn-primary text-xs flex items-center gap-1" onClick={() => setEditing("new")}><Plus className="w-3.5 h-3.5" /> Add webhook</button>}>
        <DataGrid<WebhookRow> columns={hcols} rows={data?.items || []} rowKey={(h) => h.id} empty="No webhooks yet." minWidth={1100} />
      </Section>
      <Section title="Delivery log">
        <DataGrid<DeliveryRow> columns={dcols} rows={del?.items || []} rowKey={(d) => d.id} exportName="webhook-deliveries" empty="Nothing delivered yet." minWidth={1100}
          initialSort={{ key: "created_at", dir: "desc" }}
          filters={<select className="input !py-1.5 !w-auto text-sm" value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
            <option value="">All statuses</option>{["pending", "delivered", "failed", "dead"].map((s) => <option key={s} value={s}>{s}</option>)}</select>} />
      </Section>
      {editing && meta && <HookDialog hook={editing === "new" ? null : editing} events={meta.events} onClose={() => setEditing(null)}
        onDone={(s) => { setEditing(null); refresh(); if (s) setSecret(s); }} />}
      {secret && <SecretBox title="Webhook signing secret" value={secret} onClose={() => setSecret(null)} />}
      {payload && <Dialog title={`Payload — ${payload.event}`} sub={`Delivery ${payload.id}`} onClose={() => setPayload(null)}>
        <pre className="text-[11px] bg-surface p-3 rounded overflow-auto max-h-[60vh]">{JSON.stringify(payload.payload, null, 2)}</pre></Dialog>}
    </div>
  );
}

function HookDialog({ hook, events, onClose, onDone }: { hook: WebhookRow | null; events: { key: string; label: string }[]; onClose: () => void; onDone: (secret?: string) => void }) {
  const [url, setUrl] = useState(hook?.url || "");
  const [desc, setDesc] = useState(hook?.description || "");
  const [enabled, setEnabled] = useState(hook?.enabled ?? true);
  const [sel, setSel] = useState<Set<string>>(new Set(hook?.events || []));
  const toggle = (k: string) => setSel((p) => { const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n; });
  // the server also takes "*" (every event) and "<service>.*" (every event of a service)
  const services = Array.from(new Set(events.map((e) => e.key.split(".")[0]).filter(Boolean)));
  const all = sel.has("*");
  const covered = (k: string) => all || sel.has(`${k.split(".")[0]}.*`);
  const errs = collect({
    url: v.required(url.trim(), "URL") || (!/^https:\/\/[^\s/]+\.[^\s]+$/i.test(url.trim()) ? "Use a full https:// address (plain http is refused)" : null),
    desc: v.maxLen(desc, 200, "Note"),
    events: sel.size ? null : "Tick at least one event",
  });
  const save = () => { if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } m.mutate(); };
  const m = useMutation({
    mutationFn: () => hook ? webhooksApi.update(hook.id, { url: url.trim(), description: desc.trim(), events: [...sel], enabled })
      : webhooksApi.create({ url: url.trim(), description: desc.trim(), events: [...sel], enabled }),
    onSuccess: (r: any) => { toast.success("Saved"); onDone(r.secret); }, onError: (e: any) => toast.error(errText(e, "Could not save")) });
  return (
    <Dialog title={hook ? "Edit webhook" : "Add webhook"} sub="Lexora HRM POSTs a JSON body with an HMAC-SHA256 signature header to this URL for each ticked event." onClose={onClose}
      footer={<><button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs disabled:opacity-60" disabled={!url || !sel.size || m.isPending} onClick={save}>Save</button></>}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div><label className={LABEL}>URL (https)<Req /></label><input className="input font-mono text-xs" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/lexora-webhook" /><span className="block text-[11px] text-gray-400 mt-0.5">Reachable from the internet; check the signature header before trusting the body</span>{url && <FieldError msg={errs.url} />}</div>
        <div><label className={LABEL}>Note</label><input className="input" maxLength={200} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="e.g. Accounting sync" /></div>
        <div className="md:col-span-2">
          <label className={LABEL}>Events<Req /></label>
          <div className="flex flex-wrap gap-1.5 mb-1.5">
            <label className="flex items-center gap-1.5 text-xs border border-primary-200 rounded-md px-2 py-1 cursor-pointer"><input type="checkbox" checked={all} onChange={() => setSel(all ? new Set() : new Set(["*"]))} /><b>All events</b> <span className="font-mono text-gray-400">*</span></label>
            {!all && services.map((sv) => <label key={sv} className="flex items-center gap-1.5 text-xs border border-surface-border rounded-md px-2 py-1 cursor-pointer"><input type="checkbox" checked={sel.has(`${sv}.*`)} onChange={() => setSel((p) => { const n = new Set([...p].filter((k) => k.split(".")[0] !== sv)); if (!p.has(`${sv}.*`)) n.add(`${sv}.*`); return n; })} />every <span className="font-mono">{sv}.*</span></label>)}
          </div>
          <span className="block text-[11px] text-gray-400 mb-1">A wildcard also covers events added later.</span>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
            {events.map((e) => (
              <label key={e.key} className={`flex items-center gap-2 text-xs border border-surface-border rounded-md px-2 py-1.5 cursor-pointer ${covered(e.key) ? "opacity-50" : ""}`}>
                <input type="checkbox" checked={covered(e.key) || sel.has(e.key)} disabled={covered(e.key)} onChange={() => toggle(e.key)} /><span className="font-mono">{e.key}</span><span className="text-gray-400">· {e.label}</span>
              </label>))}
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Switched on <span className="text-[11px] text-gray-400">(off pauses deliveries and keeps the secret)</span></label>
      </div>
    </Dialog>
  );
}
