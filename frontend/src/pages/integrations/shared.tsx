import { ReactNode, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Copy, Loader2, PlugZap } from "lucide-react";
import { Dialog } from "../UsersPage";
import { Spinner } from "../../components/ui";
import { ProviderInfo, errText, integrationsApi, when } from "../../api/integrations";

export const LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1";
export const KEEP = "••••••••";

export const STATUS_BADGE: Record<string, string> = {
  configured: "badge-green", not_configured: "badge-amber", error: "badge-red", not_built: "badge-red",
  active: "badge-green", revoked: "badge-red", expired: "badge-amber", delivered: "badge-green", failed: "badge-amber",
  dead: "badge-red", pending: "badge-purple", sent: "badge-teal", signed: "badge-green", declined: "badge-red",
  cancelled: "badge-red", draft: "badge-purple", read: "badge-green", verified: "badge-green", mismatch: "badge-red",
  clear: "badge-green", discrepancy: "badge-red", in_progress: "badge-teal", requested: "badge-purple",
};
export const STATUS_TEXT: Record<string, string> = {
  configured: "Configured", not_configured: "Not configured", error: "Error", not_built: "Not built",
  active: "Active", revoked: "Revoked", expired: "Expired", delivered: "Delivered", failed: "Failed (will retry)",
  dead: "Gave up", pending: "Pending", sent: "Sent", signed: "Signed", declined: "Declined", cancelled: "Cancelled",
  draft: "Draft", read: "Read", verified: "Verified", mismatch: "Mismatch", clear: "Clear", discrepancy: "Discrepancy",
  in_progress: "In progress", requested: "Requested",
};
export const Status = ({ s }: { s: string | null | undefined }) => (
  <span className={STATUS_BADGE[s || ""] || "badge-purple"}>{STATUS_TEXT[s || ""] || s || "—"}</span>
);

export function SecretBox({ title, value, note, onClose }: { title: string; value: string; note?: string; onClose: () => void }) {
  const copy = async () => {
    try { await navigator.clipboard.writeText(value); toast.success("Copied"); }
    catch { toast.error("Could not copy. Select the text and copy it."); }
  };
  return (
    <Dialog title={title} sub={note || "Copy it now. It is not shown again."} onClose={onClose}
      footer={<button className="btn-primary text-xs" onClick={onClose}>Done</button>}>
      <div className="flex flex-col sm:flex-row gap-2">
        <input className="input font-mono text-xs flex-1" readOnly value={value} onFocus={(e) => e.target.select()} />
        <button className="btn-secondary flex items-center justify-center gap-1.5 text-xs" onClick={copy}><Copy className="w-3.5 h-3.5" /> Copy</button>
      </div>
    </Dialog>
  );
}

/** Generic provider card + wide config dialog driven by the backend's field list. */
export function ProviderPanel({ p, onChanged }: { p: ProviderInfo; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const test = useMutation({ mutationFn: () => integrationsApi.test(p.key),
    onSuccess: (r) => { (r.ok ? toast.success : toast.error)(r.message); onChanged(); },
    onError: (e: any) => toast.error(errText(e, "The test could not run")) });
  return (
    <div className="card !p-3 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-semibold text-gray-800 text-sm truncate">{p.label}</div>
          <div className="text-[11px] text-gray-400 capitalize">{p.group}{p.mode ? ` · ${p.modes.find((m) => m.key === p.mode)?.label || p.mode}` : ""}</div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {p.status === "configured" && p.environment && <span className={p.environment === "live" ? "badge-red" : "badge-teal"}>{p.environment === "live" ? "LIVE" : "Sandbox"}</span>}
          <Status s={p.status} />
        </div>
      </div>
      <p className="text-xs text-gray-600">{p.status_message}</p>
      {p.last_error && <p className="text-xs text-red-600">Last error: {p.last_error}</p>}
      <p className="text-[11px] text-gray-400">{p.note}</p>
      {p.docs_url && <a className="text-[11px] text-primary-600 underline" href={p.docs_url} target="_blank" rel="noreferrer">Provider API docs</a>}
      <div className="flex items-center justify-between gap-2 mt-auto pt-1">
        <span className="text-[11px] text-gray-400">
          {p.last_test_at ? `Tested ${when(p.last_test_at)} · ${p.last_test_ok ? "passed" : "failed"}` : "Never tested"}
        </span>
        <div className="flex gap-1.5">
          <button className="btn-secondary !py-1 !px-2 text-xs flex items-center gap-1" disabled={test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <PlugZap className="w-3 h-3" />} Test</button>
          <button className="btn-primary !py-1 !px-2 text-xs" onClick={() => setOpen(true)}>Configure</button>
        </div>
      </div>
      {open && <ProviderDialog providerKey={p.key} onClose={() => setOpen(false)} onSaved={() => { setOpen(false); onChanged(); }} />}
    </div>
  );
}

export function ProviderDialog({ providerKey, onClose, onSaved }: { providerKey: string; onClose: () => void; onSaved: () => void }) {
  const qc = useQueryClient();
  const { data: p } = useQuery({ queryKey: ["integrations", "provider", providerKey], queryFn: () => integrationsApi.provider(providerKey) });
  const [mode, setMode] = useState<string | null>(null);
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [cfg, setCfg] = useState<Record<string, any>>({});
  if (!p) return <Dialog title="Loading…" onClose={onClose}><div className="grid place-items-center py-10"><Spinner /></div></Dialog>;
  const curMode = mode ?? p.mode;
  const curEnabled = enabled ?? p.enabled;
  const fields = (p.fields[curMode] || p.fields[""] || []).filter((f) => f.kind !== "hidden");
  const val = (k: string) => (k in cfg ? cfg[k] : (curMode === p.mode ? (p.config[k] ?? "") : ""));
  const save = useMutation({
    mutationFn: () => integrationsApi.save(p.key, { mode: curMode, enabled: curEnabled,
      // pasted keys often carry a stray space or newline at the ends — trim them (a kept secret stays the KEEP marker)
      config: Object.fromEntries(fields.map((f) => { const x = val(f.key); return [f.key, typeof x === "string" && x !== KEEP ? x.trim() : x]; })) }),
    onSuccess: () => { toast.success("Saved"); qc.invalidateQueries({ queryKey: ["integrations"] }); onSaved(); },
    onError: (e: any) => toast.error(errText(e, "Could not save")) });
  const missing = fields.find((f) => f.required && String(val(f.key) ?? "").trim() === "");
  const badNum = fields.find((f) => f.kind === "number" && String(val(f.key) ?? "") !== "" && val(f.key) !== KEEP && !(Number(val(f.key)) >= 0));
  const trySave = () => {
    if (curEnabled && missing) { toast.error(`${missing.label} is required before switching this on`); return; }
    if (badNum) { toast.error(`${badNum.label} must be a number, 0 or more`); return; }
    save.mutate();
  };
  return (
    <Dialog title={p.label} sub={p.note} onClose={onClose}
      footer={<>
        <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs disabled:opacity-60" disabled={save.isPending} onClick={trySave}>Save</button>
      </>}>
      {p.stub && <div className="mb-3 text-xs px-3 py-2 rounded-lg bg-amber-50 text-amber-800 border border-amber-200">
        Interface only: credentials are stored (encrypted) but no live call is built. The status stays "Not configured" and nothing is sent, verified or signed.</div>}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {p.modes.length > 1 && (
          <div>
            <label className={LABEL}>Mode</label>
            <select className="input" value={curMode} onChange={(e) => { setMode(e.target.value); setCfg({}); }}>
              {p.modes.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
            </select>
          </div>
        )}
        <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer md:self-end">
          <input type="checkbox" checked={curEnabled} onChange={(e) => setEnabled(e.target.checked)} /> Switched on
          <span className="text-[11px] text-gray-400">— only after the details below are saved; jobs start using it</span>
        </label>
        {fields.map((f) => (
          <div key={f.key} className={f.kind === "textarea" || f.key.includes("cert") ? "md:col-span-2" : ""}>
            <label className={LABEL}>{f.label}{f.required ? "" : " (optional)"}</label>
            {f.key.includes("cert") || f.kind === "textarea"
              ? <textarea className="input font-mono text-xs h-24" value={val(f.key) === KEEP ? "" : val(f.key)} placeholder={val(f.key) === KEEP ? "Saved — paste to replace" : ""}
                  onChange={(e) => setCfg({ ...cfg, [f.key]: e.target.value })} />
              : <input className="input" type={f.secret ? "password" : f.kind === "number" ? "number" : "text"}
                  value={val(f.key)} placeholder={f.secret && p.config[f.key] === KEEP ? "Saved — type to replace" : ""}
                  onFocus={(e) => { if (e.target.value === KEEP) setCfg({ ...cfg, [f.key]: "" }); }}
                  onBlur={(e) => { if (f.secret && e.target.value === "" && p.config[f.key] === KEEP) setCfg({ ...cfg, [f.key]: KEEP }); }}
                  onChange={(e) => setCfg({ ...cfg, [f.key]: e.target.value })} />}
            {f.help && <p className="text-[11px] text-gray-400 mt-1">{f.help}</p>}
          </div>
        ))}
      </div>
      <p className="text-[11px] text-gray-400 mt-4">Secrets are encrypted at rest and never shown again. Every change is written to the audit trail.</p>
    </Dialog>
  );
}

export function Section({ title, sub, right, children }: { title: string; sub?: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-5">
      <div className="flex items-end justify-between gap-3 mb-2">
        <div><h2 className="font-display font-semibold text-gray-800">{title}</h2>{sub && <p className="text-xs text-gray-500">{sub}</p>}</div>
        {right}
      </div>
      {children}
    </div>
  );
}
