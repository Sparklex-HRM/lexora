import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Activity, KeyRound, RefreshCw, Copy, Wifi, WifiOff } from "lucide-react";
import { devicePushApi, apiErr, type DeviceHealth, type PushSettings } from "../../api/devicePush";
import { usersApi } from "../../api/identity";
import { Grid, Modal, Field, Input, Select, Textarea, Stat } from "../common/_shared";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

/** G3 (2026-10-23) — live device health + push settings (ZKTeco / eSSL ADMS, Matrix COSEC, generic HTTP). */
const ago = (iso: string | null) => {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 172800) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleString("en-CA", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
};
const STATUS_TONE: Record<string, string> = { online: "badge-green", offline: "badge-red", never: "badge-amber", disabled: "badge-purple" };
const drift = (r: DeviceHealth) => {
  if (r.clock_drift_sec == null) return "—";
  const v = r.clock_drift_sec, a = Math.abs(v);
  const txt = a < 60 ? `${v > 0 ? "+" : "−"}${a}s` : `${v > 0 ? "+" : "−"}${Math.round(a / 60)} min`;
  const tone = r.drift_level === "bad" ? "text-red-600 font-semibold" : r.drift_level === "warn" ? "text-amber-600 font-semibold" : "text-gray-700";
  return <span className={tone} title={r.drift_source === "exact" ? "Device time sent with the push" : "Estimated from real-time punches"}>{txt}{r.drift_source === "estimate" ? " ≈" : ""}</span>;
};

export default function DeviceHealthPanel() {
  const qc = useQueryClient();
  const [edit, setEdit] = useState<DeviceHealth | null>(null);
  const { data, isLoading } = useQuery({ queryKey: ["bio-health"], queryFn: devicePushApi.health, refetchInterval: 30000 });
  const check = useMutation({ mutationFn: devicePushApi.checkNow, onSuccess: (r) => { toast.success(r.alerts_sent ? `${r.alerts_sent} offline alert(s) sent` : "No new offline devices"); qc.invalidateQueries({ queryKey: ["bio-health"] }); }, onError: (e) => toast.error(apiErr(e)) });
  const s = data?.summary;
  return (
    <div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-3">
        <Stat label="Online" value={s?.online ?? "—"} accent />
        <Stat label="Offline" value={s?.offline ?? "—"} sub="alert sent to the admins" />
        <Stat label="Never connected" value={s?.never ?? "—"} />
        <Stat label="Push off" value={s?.disabled ?? "—"} />
        <Stat label="Punches today (push)" value={s?.punches_today ?? "—"} />
        <Stat label="Clock warnings" value={s?.drift_warnings ?? "—"} sub="device time off by > 2 min" />
      </div>
      <div className="flex flex-wrap items-center gap-2 mb-2 text-xs text-gray-500">
        <Activity className="w-4 h-4" /> Refreshes every 30 s. Click a device to set up push, the IP allow-list, alerts and its token.
        <button className="btn-secondary !py-1 text-xs ml-auto flex items-center gap-1" disabled={check.isPending} onClick={() => check.mutate()}><RefreshCw className="w-3 h-3" /> Check offline now</button>
      </div>
      <Grid rows={data?.devices || []} keyOf={(r: DeviceHealth) => r.device_id} loading={isLoading} exportName="device_health.csv" onRow={(r: DeviceHealth) => setEdit(r)}
        empty="No devices yet. Add one with + Device, then click it here to switch on push." cols={[
          { k: "status", label: "Status", render: (r: DeviceHealth) => <span className={`${STATUS_TONE[r.status]} capitalize inline-flex items-center gap-1`}>{r.status === "online" ? <Wifi className="w-3 h-3" /> : r.status === "offline" ? <WifiOff className="w-3 h-3" /> : null}{r.status}</span> },
          { k: "name", label: "Device", render: (r: DeviceHealth) => <span><b>{r.name}</b><span className="text-gray-400"> · {r.serial}</span></span> },
          { k: "location", label: "Location", render: (r: DeviceHealth) => r.location || "—" },
          { k: "protocol", label: "Push", render: (r: DeviceHealth) => r.enabled ? ({ adms: "ZKTeco / eSSL", cosec: "Matrix COSEC", http: "HTTP" } as any)[r.protocol || ""] : "off" },
          { k: "last_seen_at", label: "Last contact", render: (r: DeviceHealth) => ago(r.last_seen_at) },
          { k: "last_punch_at", label: "Last punch", render: (r: DeviceHealth) => ago(r.last_punch_at) },
          { k: "punches_today", label: "Punches today", align: "right" },
          { k: "clock_drift_sec", label: "Clock drift", align: "right", render: drift, csv: (r: DeviceHealth) => (r.clock_drift_sec ?? "") + "" },
          { k: "last_batch", label: "Last batch", csv: (r: DeviceHealth) => JSON.stringify(r.last_batch || {}), render: (r: DeviceHealth) => r.last_batch?.received != null
            ? <span className="text-[11px]" title={`duplicates ${r.last_batch.duplicates || 0} · double taps ${r.last_batch.bounced || 0} · out of order ${r.last_batch.out_of_order || 0} · unmatched ${r.last_batch.unmatched || 0} · rejected ${(r.last_batch.future || 0) + (r.last_batch.too_old || 0)}`}>
                {r.last_batch.written}/{r.last_batch.received} added{(r.last_batch.duplicates || r.last_batch.bounced) ? ` · ${(r.last_batch.duplicates || 0) + (r.last_batch.bounced || 0)} dup` : ""}{r.last_batch.out_of_order ? ` · ${r.last_batch.out_of_order} late` : ""}{r.last_batch.unmatched ? ` · ${r.last_batch.unmatched} unmatched` : ""}</span> : "—" },
          { k: "last_ip", label: "Address", render: (r: DeviceHealth) => r.last_ip || "—" },
          { k: "firmware", label: "Firmware", render: (r: DeviceHealth) => <span className="text-[11px]">{r.firmware || "—"}</span> },
          { k: "rejects_today", label: "Refused today", align: "right", render: (r: DeviceHealth) => r.rejects_today ? <span className="text-red-600 font-semibold" title={r.last_reject || ""}>{r.rejects_today}</span> : 0 },
        ]} />
      {edit && <PushModal d={edit} onClose={() => setEdit(null)} onDone={() => { qc.invalidateQueries({ queryKey: ["bio-health"] }); }} />}
    </div>
  );
}

const ADMIN_ROLES = ["org_admin", "hr_admin", "hr_manager"];

/** Light check of one allow-list entry: IPv4 (optionally /1–32). IPv6 entries are left to the server's check. */
function ipEntryOk(s: string): boolean {
  if (s.includes(":")) return true;
  const [addr, bits, extra] = s.split("/");
  if (extra !== undefined) return false;
  if (bits !== undefined && !(bits !== "" && Number.isInteger(+bits) && +bits >= 1 && +bits <= 32)) return false;
  const parts = addr.split(".");
  return parts.length === 4 && parts.every((p) => p !== "" && p.length <= 3 && Number.isInteger(+p) && +p >= 0 && +p <= 255 && String(+p) === p);   // no leading zeros (the server refuses "010")
}
const whole = (n: unknown, min: number, max: number, label: string) =>
  Number.isInteger(Number(n)) ? v.range(n, min, max, label) : `${label} must be a whole number`;

function PushModal({ d, onClose, onDone }: { d: DeviceHealth; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<PushSettings & { ips: string }>({
    enabled: d.enabled, protocol: d.protocol || (d.vendor === "matrix" ? "cosec" : "adms"), allowed_ips: d.allowed_ips, ips: (d.allowed_ips || []).join("\n"),
    direction_mode: d.direction_mode || "device", bounce_sec: d.bounce_sec, max_backlog_days: d.max_backlog_days,
    offline_after_min: d.offline_after_min, alert_user_ids: d.alert_user_ids || [] });
  const [shown, setShown] = useState<DeviceHealth | null>(null);   // the answer that carried the token (held in memory only)
  const [hasToken, setHasToken] = useState(d.has_token);
  const set = (k: string, v: any) => setF((x) => ({ ...x, [k]: v }));
  const { data: users } = useQuery({ queryKey: ["logins-for-alerts"], queryFn: () => usersApi.list() });
  const admins = (users?.items || []).filter((u) => ADMIN_ROLES.includes(u.role) && u.status === "active");
  const ipList = f.ips.split(/[\s,]+/).filter(Boolean);
  const badIps = ipList.filter((x) => !ipEntryOk(x));
  const errs = collect({
    protocol: v.required(f.protocol, "Protocol"),
    ips: badIps.length ? `Not a valid IP or CIDR: ${badIps.slice(0, 3).join(", ")}${badIps.length > 3 ? " …" : ""}`
      : f.enabled && f.protocol === "adms" && !ipList.length && !hasToken ? "ZKTeco / eSSL on the plain /iclock path needs your office's fixed public IP here — or create a token below and use the token path" : null,
    token: f.enabled && (f.protocol === "cosec" || f.protocol === "http") && !hasToken ? "Create the device token first (below) — it goes into the device's server URL" : null,
    offline_after_min: whole(f.offline_after_min, 5, 1440, "Offline alert after"),
    bounce_sec: whole(f.bounce_sec, 0, 600, "Double-tap guard"),
    max_backlog_days: whole(f.max_backlog_days, 1, 120, "Accept backlog"),
  });
  const save = useMutation({
    mutationFn: () => devicePushApi.save(d.device_id, { ...f, allowed_ips: ipList }),
    onSuccess: () => { toast.success("Push settings saved"); onDone(); onClose(); }, onError: (e) => toast.error(apiErr(e)) });
  const submit = () => { if (Object.keys(errs).length) return toast.error(firstError(errs)); save.mutate(); };
  const token = useMutation({ mutationFn: () => devicePushApi.newToken(d.device_id), onSuccess: (r) => { setShown(r); setHasToken(true); onDone(); toast.success("New token created — copy it now"); }, onError: (e) => toast.error(apiErr(e)) });
  const copy = (t: string) => { navigator.clipboard?.writeText(t).then(() => toast.success("Copied"), () => toast.error("Copy failed — select and copy by hand")); };
  const p = shown?.paths_with_token || d.paths;
  return (
    <Modal title={`Push & health · ${d.name}`} subtitle={`Serial ${d.serial} · the organisation always comes from this registration, never from the device`} onClose={onClose} size="xl"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={save.isPending} onClick={submit}>Save</button></>}>
      <FormSection title="Connection & security">
      <FormGrid cols={2}>
        <Field label="Push" hint="Turn off to stop accepting punches from this terminal without losing its settings"><label className="flex items-center gap-2 text-sm mt-2"><input type="checkbox" checked={f.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Accept punches from this device</label><FieldError msg={errs.token} /></Field>
        <Field label={<>Protocol<Req /></>}><Select value={f.protocol} onChange={(e) => set("protocol", e.target.value)}>
          <option value="adms">ZKTeco / eSSL — ADMS (iclock)</option><option value="cosec">Matrix COSEC — PUSH API</option><option value="http">Generic HTTP (JSON)</option></Select><FieldError msg={errs.protocol} /></Field>
        <Field label={<>Allowed addresses (IP or CIDR, one per line){f.enabled && f.protocol === "adms" && !hasToken && <Req />}</>} span={2} hint={f.protocol === "adms" ? "Required for the plain /iclock path — your office's fixed public IP (not the device's LAN IP). Leave empty only if you use the token path." : "Optional extra check on top of the token. Use your office's fixed public IP."}>
          <Textarea rows={3} value={f.ips} placeholder={"203.0.113.10\n198.51.100.0/28"} onChange={(e) => set("ips", e.target.value)} /><FieldError msg={errs.ips} /></Field>
      </FormGrid>
      </FormSection>
      <FormSection title="Attendance rules" hint={`Device clock zone: ${d.timezone || "America/Toronto"} — change it on the device record (Devices & webhook tab) so punches land on the right day.`}>
      <FormGrid cols={3}>
        <Field label="In / out from" hint="device = the terminal's in/out key; blank presses alternate"><Select value={f.direction_mode} onChange={(e) => set("direction_mode", e.target.value)}>
          <option value="device">Device key (blank → alternate)</option><option value="alternate">Alternate in / out per day</option><option value="in">Always IN (entry door)</option><option value="out">Always OUT (exit door)</option></Select></Field>
        <Field label="Double-tap guard (seconds)" hint="0–600 · a second punch by the same person within this is ignored"><Input type="number" min={0} max={600} value={f.bounce_sec} onChange={(e) => set("bounce_sec", +e.target.value)} /><FieldError msg={errs.bounce_sec} /></Field>
        <Field label="Accept backlog up to (days)" hint="1–120 · a device that was offline cannot re-push older punches into closed payroll months"><Input type="number" min={1} max={120} value={f.max_backlog_days} onChange={(e) => set("max_backlog_days", +e.target.value)} /><FieldError msg={errs.max_backlog_days} /></Field>
      </FormGrid>
      </FormSection>
      <FormSection title="Offline alerts">
      <FormGrid cols={3}>
        <Field label={<>Offline alert after (minutes)<Req /></>} hint="5–1440 · no contact for this long = alert"><Input type="number" min={5} max={1440} value={f.offline_after_min} onChange={(e) => set("offline_after_min", +e.target.value)} /><FieldError msg={errs.offline_after_min} /></Field>
        <Field label="Who gets the offline alert (in-app + e-mail)" span={2} hint="Nobody picked = every org admin and HR admin">
          <div className="flex flex-wrap gap-2">{admins.map((u) => (
            <label key={u.id} className={`text-xs px-2 py-1 rounded-full border cursor-pointer ${f.alert_user_ids.includes(u.id) ? "bg-primary-50 border-primary-400 text-primary-700" : "border-gray-200 text-gray-600"}`}>
              <input type="checkbox" className="mr-1" checked={f.alert_user_ids.includes(u.id)} onChange={(e) => set("alert_user_ids", e.target.checked ? [...f.alert_user_ids, u.id] : f.alert_user_ids.filter((x) => x !== u.id))} />
              {u.name || u.email} <span className="text-gray-400">({u.role_label})</span></label>))}
            {!admins.length && <span className="text-xs text-gray-400">No admin logins found.</span>}</div></Field>
      </FormGrid>
      </FormSection>

      <div className="mt-4 rounded-lg border border-surface-border p-3">
        <div className="flex flex-wrap items-center gap-2 mb-2"><KeyRound className="w-4 h-4 text-gray-400" /><b className="text-sm">Device token</b>
          <span className="text-xs text-gray-500">{hasToken ? `set (…${shown?.token_hint || d.token_hint || "····"})` : "none yet"} — shown once when created; we keep only a fingerprint.</span>
          <button className="btn-secondary !py-1 text-xs ml-auto" disabled={token.isPending} onClick={() => { if (!hasToken || window.confirm("Make a new token? The old one stops working at once — update the device afterwards.")) token.mutate(); }}>{hasToken ? "Replace token" : "Create token"}</button></div>
        {shown?.token && <div className="rounded bg-amber-50 text-amber-900 text-xs p-2 mb-2 flex items-center gap-2"><code className="break-all">{shown.token}</code><button className="icon-btn" title="Copy" onClick={() => copy(shown.token!)}><Copy className="w-3 h-3" /></button><span>Copy it now — it will not be shown again.</span></div>}
        <div className="grid md:grid-cols-3 gap-3 text-xs text-gray-600">
          <div><b className="block text-gray-800 mb-1">ZKTeco / eSSL (ADMS)</b>Device menu → Comm → Cloud server / ADMS: server = <code>{p.base}</code>, port 443 (or your gateway port), HTTPS on. Path is fixed by the firmware: <code>/iclock/cdata</code> — accepted only from the allowed addresses. If your device or site proxy can add a path: <code className="break-all">{p.adms_token}</code>.</div>
          <div><b className="block text-gray-800 mb-1">Matrix COSEC</b>Device web page → Settings → Basic Profile → Server Connection → <i>Third Party Server</i>. Server URL = <code className="break-all">{p.base}{p.cosec_server_url}</code>. Only "User Allowed" events become punches.</div>
          <div><b className="block text-gray-800 mb-1">Generic HTTP</b><code className="break-all">POST {p.base}{p.http}</code> with <code>{'{"serial":"' + d.serial + '","device_time":"2026-10-23 09:00:00","punches":[{"emp_code":"12","at":"…","direction":"in"}]}'}</code>. No punches = heartbeat.</div>
        </div>
        <p className="text-[11px] text-gray-400 mt-2">We never send commands to the device and never store fingerprint / face templates or photos — only the code, time and in/out of each punch. Each punch updates that person's attendance day at once.</p>
      </div>
      {(d.rejects_today > 0 || d.last_reject) && <div className="mt-3 text-xs rounded-lg bg-red-50 text-red-700 px-3 py-2">Refused today: {d.rejects_today}. Last reason: {d.last_reject || "—"} {d.last_reject_at ? `(${ago(d.last_reject_at)})` : ""}</div>}
    </Modal>
  );
}
