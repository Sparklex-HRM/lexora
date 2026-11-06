import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Fingerprint, Upload, Plus, Link2, RefreshCw } from "lucide-react";
import { PageHeader } from "../../components/ui";
import { biometricApi } from "../../api/biometric";
import { Grid, Modal, Field, Input, Select, Status, Stat, useEmployees, empLabel, errMsg } from "../common/_shared";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { useUrlTab } from "../../search/useUrlTab";
import DeviceHealthPanel from "./DeviceHealthPanel";   // G3 2026-10-23: live push + device health
import { formatDateTime, zoneOptions } from "../../lib/datetime";   // 2026-10-25: instants shown in the viewer's zone

export default function BiometricImportPage() {
  const qc = useQueryClient();
  const employees = useEmployees();
  const [tab, setTab] = useUrlTab<"imports" | "devices" | "health" | "map">("imports");   // ?tab= (global search deep link)
  const [showUpload, setShowUpload] = useState(false);
  const [editDev, setEditDev] = useState<any | null | "new">(null);
  const { data: imports = [], isLoading } = useQuery({ queryKey: ["bio-imports"], queryFn: () => biometricApi.imports() });
  const { data: devices = [] } = useQuery({ queryKey: ["bio-devices"], queryFn: () => biometricApi.devices() });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["bio-imports"] }); qc.invalidateQueries({ queryKey: ["attendance"] }); };
  const last = imports[0];
  return (
    <div>
      <PageHeader title="Biometric attendance import" icon={Fingerprint} subtitle="Upload the daily/monthly export from your eSSL, ZKTeco or Matrix device — punches land in Attendance. Push devices can post straight to the webhook."
        actions={<div className="flex gap-2"><button className="btn-secondary flex items-center gap-1" onClick={() => setEditDev("new")}><Plus className="w-4 h-4" /> Device</button>
          <button className="btn-primary flex items-center gap-1" onClick={() => setShowUpload(true)}><Upload className="w-4 h-4" /> Upload file</button></div>} />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <Stat label="Imports" value={imports.length} />
        <Stat label="Last import" value={last ? `${last.punches_written} punches` : "—"} sub={last ? `${last.filename || last.device_id} · ${last.date_from} → ${last.date_to}` : undefined} accent />
        <Stat label="Unmatched codes (last)" value={last?.unmatched_codes?.length || 0} sub="map them under Code map" />
        <Stat label="Devices" value={devices.filter((d: any) => d.active).length} />
      </div>
      <div className="flex flex-wrap gap-2 mb-3">{([["imports", "Import log"], ["devices", "Devices & webhook"], ["health", "Device health & push"], ["map", "Code map"]] as const).map(([k, l]) => (
        <button key={k} className={`px-3 py-1.5 rounded-full text-xs ${tab === k ? "bg-primary-600 text-white" : "bg-gray-100 text-gray-600"}`} onClick={() => setTab(k)}>{l}</button>))}</div>
      {tab === "imports" && <Grid rows={imports} keyOf={(r: any) => r.id} loading={isLoading} exportName="biometric_imports.csv" empty="No imports yet. Click Upload file." cols={[
        { k: "created_at", label: "When", render: (r: any) => formatDateTime(r.created_at) }, { k: "source", label: "Source" },
        { k: "filename", label: "File / device", render: (r: any) => r.filename || r.device_id || "—" }, { k: "vendor", label: "Vendor", render: (r: any) => r.vendor || "—" },
        { k: "date_from", label: "From" }, { k: "date_to", label: "To" }, { k: "rows_total", label: "Rows", align: "right" },
        { k: "punches_written", label: "Punches added", align: "right", render: (r: any) => <b>{r.punches_written}</b> }, { k: "duplicates", label: "Duplicates", align: "right" },
        { k: "unmatched", label: "Unmatched", align: "right", render: (r: any) => r.unmatched ? <span className="text-amber-600 font-semibold" title={(r.unmatched_codes || []).join(", ")}>{r.unmatched}</span> : 0 },
        { k: "status", label: "Status", render: (r: any) => <Status s={r.status} /> }, { k: "created_by", label: "By" },
        { k: "errors", label: "Errors", csv: (r: any) => (r.errors || []).join(" | "), render: (r: any) => (r.errors || []).length ? <span className="text-[11px] text-red-600" title={r.errors.join("\n")}>{r.errors.length} rows</span> : "—" },
      ]} />}
      {tab === "devices" && <Grid rows={devices} keyOf={(r: any) => r.id} empty="No devices. Add one to get a webhook secret." exportName="devices.csv" onRow={(r: any) => setEditDev(r)} cols={[
        { k: "device_id", label: "Device id" }, { k: "name", label: "Name" }, { k: "vendor", label: "Vendor" }, { k: "location", label: "Location" }, { k: "timezone", label: "Time zone" },
        { k: "webhook_path", label: "Webhook", render: (r: any) => <code className="text-[11px]">POST {r.webhook_path}</code> },
        { k: "secret", label: "Secret header", csv: () => "(hidden)", render: (r: any) => <code className="text-[11px]" title="Shown once when created or rotated">X-Device-Secret: {r.secret_set ? "••••••••" : "not set"}</code> },
        { k: "last_seen_at", label: "Last push", render: (r: any) => r.last_seen_at ? formatDateTime(r.last_seen_at) : "never" },
        { k: "active", label: "Active", align: "center", csv: (r: any) => (r.active ? "yes" : "no"), render: (r: any) => r.active ? "✓" : "—" },
      ]} />}
      {tab === "health" && <DeviceHealthPanel />}
      {tab === "map" && <CodeMap employees={employees} devices={devices} lastUnmatched={last?.unmatched_codes || []} />}
      {showUpload && <UploadModal devices={devices} onClose={() => setShowUpload(false)} onDone={() => { setShowUpload(false); refresh(); }} />}
      {editDev && <DeviceModal d={editDev === "new" ? null : editDev} onClose={() => setEditDev(null)} onDone={() => { setEditDev(null); qc.invalidateQueries({ queryKey: ["bio-devices"] }); }} />}
    </div>
  );
}

// Device clock zone defaults to the org zone (America/Toronto); pick the site's own zone for other provinces.
const VENDORS: [string, string][] = [["essl", "eSSL"], ["zkteco", "ZKTeco"], ["matrix", "Matrix COSEC"], ["other", "Other"]];
const FILE_EXT = [".csv", ".txt", ".xlsx", ".xls", ".tsv"];
const MAX_MB = 20;

function UploadModal({ devices, onClose, onDone }: any) {
  const [file, setFile] = useState<File | null>(null);
  const [device, setDevice] = useState("");
  const [vendor, setVendor] = useState("");
  const [tz, setTz] = useState("America/Toronto");
  const [pv, setPv] = useState<any | null>(null);
  const pickDevice = (id: string) => {
    setDevice(id); setPv(null);
    const d = devices.find((x: any) => x.device_id === id);
    if (d) { setVendor(d.vendor || ""); if (d.timezone) setTz(d.timezone); }   // the device's own clock and make
  };
  const fileErr = !file ? null : !FILE_EXT.some((x) => file.name.toLowerCase().endsWith(x)) ? `Use a ${FILE_EXT.join(" / ")} file`
    : file.size > MAX_MB * 1024 * 1024 ? `File is ${(file.size / 1048576).toFixed(1)} MB — split it into files under ${MAX_MB} MB (e.g. one per fortnight)` : null;
  const errs = collect({ file: v.required(file ? "x" : "", "The attendance file") || fileErr, tz: v.required(tz, "Time zone") });
  const go = (fn: () => void) => { if (Object.keys(errs).length) return toast.error(firstError(errs)); fn(); };
  const preview = useMutation({ mutationFn: () => biometricApi.preview(file!, device || undefined, vendor || undefined), onSuccess: setPv, onError: (e) => toast.error(errMsg(e)) });
  const run = useMutation({ mutationFn: () => biometricApi.import(file!, device || undefined, tz, vendor || undefined), onSuccess: (r) => { toast.success(`${r.written} punches added · ${r.duplicates} duplicates skipped · ${r.unmatched} unmatched`); onDone(); }, onError: (e) => toast.error(errMsg(e)) });
  return (
    <Modal title="Upload attendance file" subtitle="CSV or Excel. We read EmpCode + Date + InTime/OutTime, or EmpCode + punch DateTime (+ In/Out). Same file twice = no duplicates." onClose={onClose} size="xl"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-secondary" disabled={!file || preview.isPending} onClick={() => go(() => preview.mutate())}>Check file</button>
        <button className="btn-primary" disabled={!file || !pv || run.isPending} onClick={() => go(() => run.mutate())}>Import {pv ? `${pv.matched} matched rows` : ""}</button></>}>
      <FormSection title="File" hint="The header row must have EmpCode and either Date + InTime / OutTime, or one punch DateTime column. Punches already in Attendance are skipped, never doubled.">
        <FormGrid cols={4}>
          <Field label={<>File (.csv / .xlsx)<Req /></>} span={2} hint={file && !fileErr ? `${file.name} · ${(file.size / 1024).toFixed(0)} KB` : `Up to ${MAX_MB} MB`}>
            <Input type="file" accept={FILE_EXT.join(",")} onChange={(e) => { setFile(e.target.files?.[0] || null); setPv(null); }} /><FieldError msg={fileErr} /></Field>
          <Field label="Device (optional, for code map)" hint="Pick one only if the same EmpCode means different people on different machines"><Select value={device} onChange={(e) => pickDevice(e.target.value)}><option value="">any</option>{devices.map((d: any) => <option key={d.device_id} value={d.device_id}>{d.name}</option>)}</Select></Field>
          <Field label="Device make (file format)" hint="Recorded on the import log"><Select value={vendor} onChange={(e) => setVendor(e.target.value)}><option value="">Not stated</option>{VENDORS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
          <Field label={<>Times in the file are in<Req /></>} hint="The clock the device was set to; times are stored converted from this zone"><Select value={tz} onChange={(e) => setTz(e.target.value)}>{zoneOptions(tz).map(([z, l]) => <option key={z} value={z}>{l}</option>)}</Select></Field>
        </FormGrid>
      </FormSection>
      {pv && <FormSection title="Check result">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
          <Stat label="Punch rows" value={pv.rows} /><Stat label="Matched" value={pv.matched} accent /><Stat label="Unmatched" value={pv.unmatched} sub="fix in Code map" />
          <Stat label="Dates" value={pv.date_from ? `${pv.date_from} → ${pv.date_to}` : "—"} /><Stat label="Columns found" value={Object.keys(pv.columns || {}).length} sub={Object.entries(pv.columns || {}).map(([k, v]) => `${k}=${v}`).join(", ")} />
        </div>
        {pv.unmatched > 0 && <div className="rounded-lg bg-amber-50 text-amber-800 px-3 py-2 text-xs mb-3">{pv.unmatched} punch rows have a device code we could not match. Import now and they are skipped — or map the codes under the <b>Code map</b> tab first, then import again (already-added punches are not doubled).</div>}
        {pv.errors?.length > 0 && <div className="rounded-lg bg-amber-50 text-amber-800 px-3 py-2 text-xs mb-3">{pv.errors.slice(0, 5).join(" · ")}{pv.errors.length > 5 ? ` · +${pv.errors.length - 5} more` : ""}</div>}
        <Grid rows={pv.codes || []} keyOf={(r: any) => r.code} empty="—" title="Employee codes in the file" cols={[
          { k: "code", label: "Device code" }, { k: "name", label: "Name in file", render: (r: any) => r.name || "—" }, { k: "punches", label: "Punches", align: "right" },
          { k: "employee", label: "Matched employee", render: (r: any) => r.employee ? <span className="text-green-700">{r.employee}</span> : <span className="badge-red">not matched</span> }]} />
      </FormSection>}
    </Modal>
  );
}

function DeviceModal({ d, onClose, onDone }: any) {
  const [f, setF] = useState<any>(d ? { device_id: d.device_id, name: d.name, vendor: d.vendor || "essl", location: d.location || "", timezone: d.timezone || "America/Toronto", active: d.active, rotate_secret: false }
    : { device_id: "", name: "", vendor: "essl", location: "", timezone: "America/Toronto", active: true });
  const [tried, setTried] = useState(false);
  const set = (k: string, v: any) => setF((x: any) => ({ ...x, [k]: v }));
  const errs = collect({
    device_id: v.required(f.device_id, "Device id / serial") || v.maxLen(f.device_id.trim(), 40, "Device id") || (/\s/.test(f.device_id.trim()) ? "Device id cannot contain spaces — type the serial exactly as printed on the device" : null),
    name: v.required(f.name, "Name") || v.maxLen(f.name.trim(), 120, "Name"),
    location: v.maxLen(f.location, 120, "Location"),
    timezone: v.required(f.timezone, "Time zone"),
  });
  const err = (k: string) => (tried || f[k] ? errs[k] : undefined);
  // Integration 2026-10-23: the server stores only a hash; the plain secret comes back ONCE (create / rotate).
  const [shown, setShown] = useState<string | null>(null);
  const body = { ...f, device_id: f.device_id.trim(), name: f.name.trim(), location: f.location.trim() || null };
  const save = useMutation({ mutationFn: () => (d ? biometricApi.updateDevice(d.id, body) : biometricApi.createDevice(body)),
    onSuccess: (r: any) => { toast.success("Device saved"); if (r?.secret) setShown(r.secret); else onDone(); }, onError: (e) => toast.error(errMsg(e)) });
  const submit = () => {
    setTried(true);
    if (Object.keys(errs).length) return toast.error(firstError(errs));
    if (f.rotate_secret && !window.confirm("Make a new secret? The old one stops working at once — the device must be set up again with the new one.")) return;
    save.mutate();
  };
  if (shown) return (
    <Modal title="Copy the device secret now" subtitle="It is shown only this once. Enter it on the device (X-Device-Secret header). Lost it? Edit the device and tick rotate." onClose={onDone} size="lg"
      footer={<button className="btn-primary" onClick={onDone}>I have copied it</button>}>
      <div className="flex items-center gap-2"><code className="text-sm bg-gray-100 px-2 py-1 rounded break-all" data-testid="device-secret-once">{shown}</code>
        <button className="btn-secondary !py-1 text-xs" onClick={() => { navigator.clipboard?.writeText(shown).then(() => toast.success("Copied"), () => toast.error("Copy failed — select and copy")); }}>Copy</button></div>
    </Modal>
  );
  return (
    <Modal title={d ? `Device · ${d.name}` : "Add a device"} subtitle="Push devices (ADMS / vendor cloud) call the webhook with the secret header." onClose={onClose} size="lg"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={save.isPending} onClick={submit}>Save</button></>}>
      <FormSection title="Device">
        <FormGrid cols={3}>
          <Field label={<>Device id / serial<Req /></>} hint="The serial printed on the device — it must match what the device sends"><Input value={f.device_id} maxLength={40} onChange={(e) => set("device_id", e.target.value)} placeholder="e.g. CKJG201960001" /><FieldError msg={err("device_id")} /></Field>
          <Field label={<>Name<Req /></>}><Input value={f.name} maxLength={120} onChange={(e) => set("name", e.target.value)} placeholder="e.g. Plant 2 — main gate" /><FieldError msg={err("name")} /></Field>
          <Field label={<>Make<Req /></>}><Select value={f.vendor} onChange={(e) => set("vendor", e.target.value)}>{VENDORS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
          <Field label="Location" hint="Site / door, shown in offline alerts"><Input value={f.location} maxLength={120} onChange={(e) => set("location", e.target.value)} placeholder="e.g. Pune plant · gate 1" /><FieldError msg={err("location")} /></Field>
          <Field label={<>Device clock time zone<Req /></>} hint="The zone the device's clock is set to; punches are converted from it"><Select value={f.timezone} onChange={(e) => set("timezone", e.target.value)}>{zoneOptions(f.timezone).map(([z, l]) => <option key={z} value={z}>{l}</option>)}</Select><FieldError msg={err("timezone")} /></Field>
          <Field label="Active"><label className="flex items-center gap-2 text-sm mt-2"><input type="checkbox" checked={!!f.active} onChange={(e) => set("active", e.target.checked)} /> Active — accept its punches</label></Field>
        </FormGrid>
      </FormSection>
      {d && <FormSection title="Webhook & secret" hint={`Last push: ${d.last_seen_at ? formatDateTime(d.last_seen_at) : "never"}. Push settings, IP allow-list and alerts are under the Device health & push tab.`}>
        <div className="flex flex-wrap items-center gap-2"><code className="text-xs bg-gray-100 px-2 py-1 rounded">{d.secret_set ? "•••••••• (hidden — shown once)" : "not set"}</code><label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={!!f.rotate_secret} onChange={(e) => set("rotate_secret", e.target.checked)} /><RefreshCw className="w-3 h-3" /> make a new secret on save</label>
          <span className="text-[11px] text-gray-500">The old secret stops working at once; set the new one on the device.</span></div>
        <div className="mt-3 text-xs text-gray-500">Webhook: <code>POST {d.webhook_path}</code> with header <code>X-Device-Secret</code> and body <code>{'{"punches":[{"emp_code":"0012","at":"2026-10-03 09:00:00","direction":"in"}]}'}</code></div>
      </FormSection>}
    </Modal>
  );
}

function CodeMap({ employees, devices, lastUnmatched }: any) {
  const qc = useQueryClient();
  const { data: rows = [] } = useQuery({ queryKey: ["bio-map"], queryFn: () => biometricApi.map() });
  const [edit, setEdit] = useState<any[] | null>(null);
  const list = edit ?? rows;
  const start = () => setEdit([...rows.map((r: any) => ({ ...r })), ...lastUnmatched.filter((c: string) => !rows.some((r: any) => r.device_emp_code === c)).map((c: string) => ({ device_id: "", device_emp_code: c, employee_id: "" }))]);
  const upd = (i: number, k: string, v: any) => setEdit((r) => (r || []).map((x, j) => (j === i ? { ...x, [k]: v } : x)));
  const save = useMutation({ mutationFn: () => biometricApi.saveMap(edit || []), onSuccess: () => { toast.success("Code map saved"); setEdit(null); qc.invalidateQueries({ queryKey: ["bio-map"] }); }, onError: (e) => toast.error(errMsg(e)) });
  const nameOf = (id: string) => { const e = employees.find((x: any) => x.id === id); return e ? empLabel(e) : id; };
  return (
    <div className="card !p-0">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-surface-border">
        <Link2 className="w-4 h-4 text-gray-400" /><span className="text-sm font-semibold text-gray-700">Device code → employee</span>
        <span className="text-xs text-gray-400">Employee numbers match automatically; add rows only for codes that differ.</span>
        <div className="ml-auto flex gap-2">
          {!edit && <button className="btn-secondary !py-1 text-xs" onClick={start}>Edit{lastUnmatched.length ? ` (+${lastUnmatched.length} unmatched)` : ""}</button>}
          {edit && <><button className="btn-secondary !py-1 text-xs" onClick={() => setEdit((r) => [...(r || []), { device_id: "", device_emp_code: "", employee_id: "" }])}>+ Row</button>
            <button className="btn-secondary !py-1 text-xs" onClick={() => setEdit(null)}>Cancel</button><button className="btn-primary !py-1 text-xs" disabled={save.isPending} onClick={() => save.mutate()}>Save</button></>}
        </div>
      </div>
      {!list.length ? <div className="text-center py-8 text-sm text-gray-400">No manual mappings.</div> : (
        <table className="w-full text-xs"><thead className="bg-surface text-gray-400 text-left text-[10px] uppercase"><tr><th className="px-3 py-2">Device</th><th className="px-3 py-2">Code on device</th><th className="px-3 py-2">Employee</th></tr></thead>
          <tbody>{list.map((r: any, i: number) => (<tr key={i} className="border-t border-surface-border">
            <td className="px-3 py-1">{edit ? <Select className="!w-40 !py-0.5" value={r.device_id || ""} onChange={(e) => upd(i, "device_id", e.target.value)}><option value="">all devices</option>{devices.map((d: any) => <option key={d.device_id} value={d.device_id}>{d.name}</option>)}</Select> : (r.device_id || "all")}</td>
            <td className="px-3 py-1">{edit ? <Input className="!w-32 !py-0.5" value={r.device_emp_code} onChange={(e) => upd(i, "device_emp_code", e.target.value)} /> : r.device_emp_code}</td>
            <td className="px-3 py-1">{edit ? <Select className="!w-80 !py-0.5" value={r.employee_id} onChange={(e) => upd(i, "employee_id", e.target.value)}><option value="">—</option>{employees.map((e: any) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}</Select> : nameOf(r.employee_id)}</td>
          </tr>))}</tbody></table>)}
    </div>
  );
}
