import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Plus, Save, Settings, Trash2, Upload } from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import DataGrid, { Tabs, type GridColumn } from "../components/DataGrid";
import { errText, settingsApi, when, type SettingsAudit } from "../api/platform";
import { useAuthStore } from "../store/authStore";
import { useUrlTab } from "../search/useUrlTab";
import { setOrgTimeZone, zoneOptions } from "../lib/datetime";   // 2026-10-25: UTC store, org-zone today

type Tab = "company" | "work" | "numbering" | "notifications" | "history";
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const PREFIXES: [string, string][] = [["employee_prefix", "Employee number"], ["requisition_prefix", "Requisition"], ["offer_prefix", "Offer"],
  ["letter_prefix", "Letter"], ["revision_prefix", "Salary revision"], ["exit_prefix", "Exit case"]];

const Label = ({ children }: { children: React.ReactNode }) => <label className="block text-xs font-semibold text-gray-500 mb-1">{children}</label>;
const Section = ({ title, hint, children, onSave, saving, canEdit }:
  { title: string; hint?: string; children: React.ReactNode; onSave?: () => void; saving?: boolean; canEdit: boolean }) => (
  <div className="card">
    <div className="flex items-start justify-between gap-3 mb-3">
      <div><h3 className="font-display font-semibold text-gray-800">{title}</h3>{hint && <p className="text-xs text-gray-500 mt-0.5">{hint}</p>}</div>
      {onSave && canEdit && <button className="btn-primary text-xs inline-flex items-center gap-1.5 shrink-0" disabled={saving} onClick={onSave}><Save className="w-3.5 h-3.5" /> Save</button>}
    </div>
    {children}
  </div>
);

export default function CompanySettingsPage() {
  const qc = useQueryClient();
  const orgName = useAuthStore((s) => s.user?.org_name) || "";
  const q = useQuery({ queryKey: ["settings"], queryFn: settingsApi.all });
  const [tab, setTab] = useUrlTab<Tab>("company");   // ?tab= (global search deep link)
  const [v, setV] = useState<Record<string, any>>({});
  useEffect(() => {
    if (q.data) setV(Object.fromEntries(Object.entries(q.data.groups).map(([k, g]) => [k, JSON.parse(JSON.stringify(g.value))])));
  }, [q.data]);
  const canEdit = !!q.data?.can_edit;
  const set = (group: string, field: string, value: any) => setV((p) => ({ ...p, [group]: { ...p[group], [field]: value } }));
  const save = useMutation({
    mutationFn: (group: string) => settingsApi.save(group, v[group]),
    onSuccess: (_r, group) => { toast.success("Saved"); qc.invalidateQueries({ queryKey: ["settings"] }); if (group === "locale") { setOrgTimeZone(v.locale?.timezone); qc.invalidateQueries({ queryKey: ["org-locale-tz"] }); } },
    onError: (e) => toast.error(errText(e), { duration: 7000 }),
  });
  const input = (group: string, field: string, props: React.InputHTMLAttributes<HTMLInputElement> = {}) => (
    <input className="input" disabled={!canEdit} value={v[group]?.[field] ?? ""} onChange={(e) => set(group, field, e.target.value)} {...props} />
  );

  // The old screen kept its values in this browser only. Nothing is imported — say so.
  const legacy = useMemo(() => { try { return !!localStorage.getItem(`lx-company-${orgName || "Lexora"}`); } catch { return false; } }, [orgName]);

  if (q.isLoading) return <div className="grid place-items-center py-20"><Spinner className="h-8 w-8" /></div>;
  if (q.isError || !q.data) return <div className="card text-sm text-red-600">Could not load company settings. {errText(q.error)}</div>;
  const has = (g: string) => g in q.data.groups;
  const tabs: { key: Tab; label: string }[] = [
    { key: "company", label: "Company" }, { key: "work", label: "Work week and hours" }, { key: "numbering", label: "Numbering" },
    ...(has("notification_sender") ? [{ key: "notifications" as Tab, label: "Notifications and approvals" }] : []),
    ...(canEdit ? [{ key: "history" as Tab, label: "Change history" }] : []),
  ];
  const addresses: any[] = v.company?.addresses || [];
  const regs: any[] = v.registrations?.items || [];
  const toggleDay = (d: number, to: "working_days" | "weekly_offs") => {
    const other = to === "working_days" ? "weekly_offs" : "working_days";
    const cur: number[] = v.work_week?.[to] || [], oth: number[] = v.work_week?.[other] || [];
    setV((p) => ({ ...p, work_week: { [to]: cur.includes(d) ? cur.filter((x) => x !== d) : [...cur, d].sort(), [other]: oth.filter((x) => x !== d) } }));
  };

  return (
    <div>
      <PageHeader title="Company Settings" icon={Settings} subtitle={canEdit ? "Saved on the server for everyone in your company" : "Only an org admin can change these"} />
      {legacy && (
        <div className="rounded-lg bg-amber-50 text-amber-800 text-sm px-3 py-2 mb-3">
          This browser still holds values typed into the old version of this screen. They were kept on this device only and are not copied here. Enter them again below so everyone sees them.
        </div>
      )}
      <Tabs tabs={tabs} value={tab} onChange={setTab} />

      {tab === "company" && (
        <div className="grid xl:grid-cols-2 gap-4">
          <Section title="Company profile" canEdit={canEdit} saving={save.isPending} onSave={() => save.mutate("company")}>
            <div className="grid sm:grid-cols-2 gap-3">
              <div className="sm:col-span-2"><Label>Legal name</Label>{input("company", "legal_name", { placeholder: "Example Private Limited" })}</div>
              <div><Label>Trade name</Label>{input("company", "trade_name")}</div>
              <div><Label>Phone</Label>{input("company", "phone")}</div>
              <div><Label>E-mail</Label>{input("company", "email", { type: "email", placeholder: "hr@example.com" })}</div>
              <div><Label>Website</Label>{input("company", "website")}</div>
            </div>
            <div className="mt-4 flex items-center justify-between"><span className="text-xs font-semibold text-gray-500">Addresses</span>
              {canEdit && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => set("company", "addresses", [...addresses, { label: "", line1: "", line2: "", city: "", state: "", postal_code: "", country: "" }])}><Plus className="w-3 h-3" /> Add address</button>}</div>
            {!addresses.length && <p className="text-sm text-gray-400 mt-2">No address added yet.</p>}
            {addresses.map((a, i) => {
              const upd = (k: string, val: string) => set("company", "addresses", addresses.map((x, j) => (j === i ? { ...x, [k]: val } : x)));
              const f = (k: string, ph: string, span = "") => <input className={`input ${span}`} disabled={!canEdit} placeholder={ph} value={a[k] || ""} onChange={(e) => upd(k, e.target.value)} aria-label={ph} />;
              return (
                <div key={i} className="grid grid-cols-2 sm:grid-cols-6 gap-2 mt-2 p-2 rounded-lg border border-surface-border">
                  {f("label", "Name, e.g. Registered office", "col-span-2 sm:col-span-2")}{f("line1", "Address line 1", "col-span-2 sm:col-span-4")}
                  {f("line2", "Address line 2", "col-span-2 sm:col-span-3")}{f("city", "City", "sm:col-span-3")}
                  {f("state", "Province / territory", "sm:col-span-2")}{f("postal_code", "Postal code", "sm:col-span-2")}{f("country", "Country", "sm:col-span-1")}
                  {canEdit && <button className="btn-secondary !py-1 text-xs text-red-600 inline-flex items-center justify-center gap-1" onClick={() => set("company", "addresses", addresses.filter((_, j) => j !== i))}><Trash2 className="w-3 h-3" /> Remove</button>}
                </div>
              );
            })}
          </Section>

          <div className="space-y-4">
            <LogoCard canEdit={canEdit} hasLogo={q.data.has_logo} needsReupload={q.data.logo_needs_reupload} />
            {has("registrations") && (
              <Section title="Registration numbers" hint="Stored and shown exactly as you type them. This screen does not check them against any authority."
                canEdit={canEdit} saving={save.isPending} onSave={() => save.mutate("registrations")}>
                {!regs.length && <p className="text-sm text-gray-400">None added yet.</p>}
                {regs.map((r, i) => (
                  <div key={i} className="grid grid-cols-5 gap-2 mb-2">
                    <input className="input col-span-2" disabled={!canEdit} placeholder="Name, e.g. CRA Business Number" value={r.label || ""} aria-label="Registration name"
                      onChange={(e) => set("registrations", "items", regs.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
                    <input className="input col-span-2" disabled={!canEdit} placeholder="Number" value={r.value || ""} aria-label="Registration number"
                      onChange={(e) => set("registrations", "items", regs.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
                    {canEdit && <button className="btn-secondary text-xs text-red-600" onClick={() => set("registrations", "items", regs.filter((_, j) => j !== i))}>Remove</button>}
                  </div>
                ))}
                {canEdit && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => set("registrations", "items", [...regs, { label: "", value: "" }])}><Plus className="w-3 h-3" /> Add number</button>}
              </Section>
            )}
            <Section title="Country, currency and financial year" canEdit={canEdit} saving={save.isPending} onSave={() => save.mutate("locale")}>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <div><Label>Country</Label><input className="input" disabled value="Canada (CA)" aria-label="Country" /></div>
                <div><Label>Currency</Label><input className="input" disabled value="Canadian dollar (CAD)" aria-label="Currency" /></div>
                <div><Label>Financial year starts</Label>
                  <select className="input" disabled={!canEdit} value={v.locale?.fy_start_month || 1} onChange={(e) => set("locale", "fy_start_month", Number(e.target.value))}>
                    {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}</select></div>
                <div><Label>Time zone</Label>
                  <select className="input" disabled={!canEdit} value={v.locale?.timezone || "America/Toronto"} onChange={(e) => set("locale", "timezone", e.target.value)} aria-label="Time zone">
                    {zoneOptions(v.locale?.timezone).map(([z, l]) => <option key={z} value={z}>{l}</option>)}</select></div>
              </div>
              <p className="text-xs text-gray-400 mt-2">The time zone decides your company's &quot;today&quot; (attendance day, leave, due dates, pay month, filing deadlines). Times are stored in UTC and shown on each person's own clock.</p>
              {!q.data.groups.locale?.saved && <p className="text-xs text-gray-400 mt-2">Not saved yet — showing the defaults from sign-up.</p>}
            </Section>
          </div>
        </div>
      )}

      {tab === "work" && (
        <div className="grid xl:grid-cols-2 gap-4">
          <Section title="Working week" hint="Tick each day once: a working day or a weekly off." canEdit={canEdit} saving={save.isPending} onSave={() => save.mutate("work_week")}>
            <table className="w-full text-sm"><thead className="text-left text-[11px] uppercase text-gray-500"><tr><th className="py-1">Day</th><th>Working day</th><th>Weekly off</th></tr></thead>
              <tbody>{DAYS.map((d, i) => (
                <tr key={d} className="border-t border-surface-border"><td className="py-1.5 text-gray-700">{d}</td>
                  <td><input type="checkbox" className="w-4 h-4" disabled={!canEdit} checked={(v.work_week?.working_days || []).includes(i)} onChange={() => toggleDay(i, "working_days")} aria-label={`${d} working day`} /></td>
                  <td><input type="checkbox" className="w-4 h-4" disabled={!canEdit} checked={(v.work_week?.weekly_offs || []).includes(i)} onChange={() => toggleDay(i, "weekly_offs")} aria-label={`${d} weekly off`} /></td></tr>))}</tbody></table>
          </Section>
          <div className="space-y-4">
            <Section title="Default work hours" canEdit={canEdit} saving={save.isPending} onSave={() => save.mutate("work_hours")}>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div><Label>Start</Label>{input("work_hours", "start", { type: "time" })}</div>
                <div><Label>End</Label>{input("work_hours", "end", { type: "time" })}</div>
                <div><Label>Hours per day</Label>{input("work_hours", "hours_per_day", { type: "number", min: 1, max: 24, step: 0.5 })}</div>
                <div><Label>Break (minutes)</Label>{input("work_hours", "break_minutes", { type: "number", min: 0, max: 480 })}</div>
              </div>
              <p className="text-xs text-gray-400 mt-2">These are company defaults. Shift patterns used for attendance are set on the Scheduling screen and are not changed from here.</p>
            </Section>
            <div className="card">
              <h3 className="font-display font-semibold text-gray-800 mb-2">Kept on their own screens</h3>
              <p className="text-sm text-gray-500 mb-2">These are not repeated here, so there is only one place to change each of them.</p>
              <ul className="text-sm space-y-1.5">
                <li><Link className="text-primary-600 hover:underline" to="/payroll">Pay groups and the pay calendar</Link> — Run Payroll</li>
                <li><Link className="text-primary-600 hover:underline" to="/stat-holidays">Holidays</Link> — Holidays</li>
                <li><Link className="text-primary-600 hover:underline" to="/masters">Departments, locations, leave types, financial years</Link> — Master Data</li>
                <li><Link className="text-primary-600 hover:underline" to="/scheduling">Shift patterns</Link> — Scheduling</li>
                <li><Link className="text-primary-600 hover:underline" to="/settings/entities">CRA Business Number, payroll account and WCB accounts</Link> — Legal entities</li>
              </ul>
            </div>
          </div>
        </div>
      )}

      {tab === "numbering" && (
        <Section title="Numbering prefixes" hint="Other screens read these when they create a new number. Changing a prefix does not rename anything that already exists."
          canEdit={canEdit} saving={save.isPending} onSave={() => save.mutate("numbering")}>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {PREFIXES.map(([k, label]) => (
              <div key={k}><Label>{label}</Label>{input("numbering", k, { maxLength: 12 })}
                <div className="text-[11px] text-gray-400 mt-1">Example: {(v.numbering?.[k] || "") + "1".padStart(Number(v.numbering?.padding) || 4, "0")}</div></div>
            ))}
            <div><Label>Digits in the number</Label>{input("numbering", "padding", { type: "number", min: 1, max: 10 })}</div>
          </div>
        </Section>
      )}

      {tab === "notifications" && has("notification_sender") && (
        <div className="grid xl:grid-cols-2 gap-4">
          <Section title="E-mail sender" hint="The name and reply address people see on e-mails from this company. The mail server itself is set up on the installation, not here."
            canEdit={canEdit} saving={save.isPending} onSave={() => save.mutate("notification_sender")}>
            <div className="grid sm:grid-cols-2 gap-3">
              <div><Label>Sender name</Label>{input("notification_sender", "sender_name", { placeholder: "Example HR", maxLength: 100 })}</div>
              <div><Label>Reply-to address</Label>{input("notification_sender", "reply_to", { type: "email", placeholder: "hr@example.com" })}</div>
            </div>
            <p className="text-xs text-gray-400 mt-2">See what was really sent on <Link className="text-primary-600 hover:underline" to="/notifications?tab=log">Notifications → Delivery log</Link>.</p>
          </Section>
          <Section title="Approval reminders" canEdit={canEdit} saving={save.isPending} onSave={() => save.mutate("approvals")}>
            <div className="max-w-xs"><Label>Remind approvers after this many days</Label>
              <input className="input" type="number" min={0} max={60} disabled={!canEdit} value={v.approvals?.escalation_days ?? 3} onChange={(e) => set("approvals", "escalation_days", e.target.value === "" ? "" : Number(e.target.value))} /></div>
            <p className="text-xs text-gray-400 mt-2">0 switches reminders off. A reminder repeats each time the same number of days passes. From the second reminder, HR is told as well.</p>
          </Section>
        </div>
      )}

      {tab === "history" && canEdit && <History />}
    </div>
  );
}

function LogoCard({ canEdit, hasLogo, needsReupload }: { canEdit: boolean; hasLogo: boolean; needsReupload?: boolean }) {
  const qc = useQueryClient();
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    let made: string | null = null, live = true;
    if (hasLogo) settingsApi.logoBlob().then((b) => { if (live) { made = URL.createObjectURL(b); setUrl(made); } }).catch(() => setUrl(null));
    else setUrl(null);
    return () => { live = false; if (made) URL.revokeObjectURL(made); };
  }, [hasLogo]);
  const done = () => qc.invalidateQueries({ queryKey: ["settings"] });
  const pick = (file?: File) => {
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) return void toast.error("The logo must be a PNG, JPG or WEBP image");
    if (file.size > 512 * 1024) return void toast.error("The logo must be 512 KB or smaller");
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        await settingsApi.saveLogo(file.type, String(reader.result).split(",")[1] || "");
        toast.success("Logo saved"); setUrl(null); done();
        const b = await settingsApi.logoBlob(); setUrl(URL.createObjectURL(b));
      } catch (e) { toast.error(errText(e)); }
    };
    reader.readAsDataURL(file);
  };
  const remove = useMutation({ mutationFn: settingsApi.deleteLogo, onSuccess: () => { toast.success("Logo removed"); done(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <div className="card">
      <h3 className="font-display font-semibold text-gray-800 mb-3">Logo</h3>
      <div className="flex items-center gap-4">
        <div className="w-28 h-20 rounded-lg border border-surface-border bg-surface grid place-items-center overflow-hidden shrink-0">
          {url ? <img src={url} alt="Company logo" className="max-w-full max-h-full object-contain" /> : <span className="text-xs text-gray-400">No logo</span>}
        </div>
        {canEdit ? (
          <div className="flex flex-wrap gap-2">
            <label className="btn-secondary text-xs inline-flex items-center gap-1.5 cursor-pointer"><Upload className="w-3.5 h-3.5" /> {hasLogo ? "Replace" : "Upload"}
              <input type="file" className="hidden" accept="image/png,image/jpeg,image/webp" onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} /></label>
            {hasLogo && <button className="btn-secondary text-xs text-red-600" disabled={remove.isPending} onClick={() => remove.mutate()}>Remove</button>}
            <p className="basis-full text-[11px] text-gray-400">PNG, JPG or WEBP, up to 512 KB.</p>
            {needsReupload && <p className="basis-full text-[12px] text-amber-700" data-testid="logo-reupload">The saved logo is an SVG, which is no longer accepted. Upload it again as PNG or JPG.</p>}
          </div>
        ) : <p className="text-sm text-gray-500">Only an org admin can change the logo.</p>}
      </div>
    </div>
  );
}

const show = (x: any) => (x === null || x === undefined || x === "" ? "—" : typeof x === "object" ? JSON.stringify(x) : String(x));

function History() {
  const q = useQuery({ queryKey: ["settings", "audit"], queryFn: settingsApi.audit });
  const rows = useMemo(() => (q.data || []).flatMap((a: SettingsAudit) =>
    (a.changes.length ? a.changes : [{ field: "", before: null, after: null }]).map((c, i) => ({
      id: `${a.id}:${i}`, at: a.at, by: a.by, section: a.label === "logo" ? "Logo" : a.label, field: c.field.replace(/_/g, " "), before: show(c.before), after: show(c.after) }))), [q.data]);
  const cols: GridColumn<(typeof rows)[number]>[] = [
    { key: "at", label: "When", width: "170px", render: (r) => when(r.at) }, { key: "by", label: "Who", width: "220px" },
    { key: "section", label: "Section", width: "220px" }, { key: "field", label: "Field", width: "160px", className: "capitalize text-gray-700" },
    { key: "before", label: "Before", className: "text-xs text-gray-500 break-all" }, { key: "after", label: "After", className: "text-xs text-gray-700 break-all" },
  ];
  if (q.isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (q.isError) return <div className="card text-sm text-red-600">Could not load the change history. {errText(q.error)}</div>;
  return <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName="company-settings-history" minWidth={1000}
    empty="No settings have been changed yet." searchPlaceholder="Search changes…" />;
}
