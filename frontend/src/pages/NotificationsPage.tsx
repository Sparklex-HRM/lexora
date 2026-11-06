import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Bell, CheckCheck, Mail, MessageCircle, RefreshCw, RotateCcw, Send } from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../components/DataGrid";
import { errText, notificationsApi, when, type Delivery, type Note, type NoteTemplate } from "../api/platform";
import { useAuthStore } from "../store/authStore";
import api from "../api/client";

/* The caller's own WhatsApp consent — integrations service /integrations/whatsapp/optin* (whatsapp_routes.py). */
interface WaOptin { id: string; employee_id: string; phone: string; status: string; source: string; events: string[]; consent_text: string; at: string | null }
const waOptinApi = {
  me: () => api.get<{ optin: WaOptin | null; default_consent: string; agent_chase_consent?: string; agent_chase_label?: string }>("/integrations/whatsapp/optin/me").then((r) => r.data),
  optIn: (body: { events: string[]; consent_text: string }) => api.post<WaOptin>("/integrations/whatsapp/optin", body).then((r) => r.data),
  optOut: () => api.post<WaOptin>("/integrations/whatsapp/optout", {}).then((r) => r.data),
};

type Tab = "inbox" | "preferences" | "log" | "templates";
// Roles that may open the delivery log and templates (the server checks again).
const ADMIN = ["org_admin", "superadmin", "hr", "hr_admin", "hr_manager", "hr_staff"];
const LOG_READERS = ["org_admin", "superadmin"];

const STATUS: Record<string, { label: string; cls: string }> = {
  delivered: { label: "Delivered", cls: "badge-green" },
  sent: { label: "Sent", cls: "badge-green" },
  queued: { label: "Waiting to send", cls: "badge-amber" },
  sending: { label: "Sending", cls: "badge-amber" },
  failed: { label: "Failed", cls: "badge-red" },
  not_configured: { label: "Not configured", cls: "badge-amber" },
  off_by_user: { label: "Switched off by the person", cls: "badge-purple" },
  no_address: { label: "No e-mail address", cls: "badge-purple" },
  no_login: { label: "No login yet", cls: "badge-purple" },
};
const statusText = (s: string) => STATUS[s]?.label || s;

export default function NotificationsPage() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const admin = ADMIN.includes(role);
  // Decision 2026-10-21: the delivery log is an audit record — org admin only (HR keeps the templates).
  const logReader = LOG_READERS.includes(role);
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "inbox";
  const setTab = (t: Tab) => setSp(t === "inbox" ? {} : { tab: t }, { replace: true });
  const tabs: { key: Tab; label: string }[] = [
    { key: "inbox", label: "My notifications" }, { key: "preferences", label: "My preferences" },
    ...(logReader ? [{ key: "log" as Tab, label: "Delivery log" }] : []),
    ...(admin ? [{ key: "templates" as Tab, label: "E-mail templates" }] : []),
  ];
  return (
    <div>
      <PageHeader title="Notifications" icon={Bell} subtitle="What needs your attention, and how you want to hear about it" />
      <Tabs tabs={tabs} value={tab} onChange={setTab} />
      {tab === "inbox" && <Inbox />}
      {tab === "preferences" && <><Preferences /><AgentRemindersWhatsApp /></>}
      {tab === "log" && logReader && <DeliveryLog />}
      {tab === "templates" && admin && <Templates />}
    </div>
  );
}

function Inbox() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [kind, setKind] = useState("");
  const [unread, setUnread] = useState(false);
  const q = useQuery({ queryKey: ["ntf", "list", kind, unread], queryFn: () => notificationsApi.list({ kind: kind || undefined, unread: unread || undefined, limit: 200 }) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["ntf"] });
  const readAll = useMutation({ mutationFn: () => notificationsApi.readAll(kind || undefined), onSuccess: (r) => { toast.success(`${r.marked} marked as read`); refresh(); } });
  const toggle = useMutation({ mutationFn: (n: Note) => (n.read ? notificationsApi.unread(n.id) : notificationsApi.read(n.id)), onSuccess: refresh });

  const cols: GridColumn<Note>[] = [
    { key: "title", label: "Notification", text: (n) => `${n.title} ${n.body}`,
      render: (n) => (
        <div className="min-w-0">
          <div className={`flex items-center gap-2 ${n.read ? "text-gray-600" : "text-gray-900 font-semibold"}`}>
            {!n.read && <span className="w-2 h-2 rounded-full bg-primary-500 shrink-0" title="Unread" />}{n.title}
          </div>
          {n.body && <div className="text-xs text-gray-500 whitespace-pre-line line-clamp-2">{n.body}</div>}
        </div>) },
    { key: "kind_label", label: "Type", width: "150px" },
    { key: "created_at", label: "Received", width: "170px", render: (n) => when(n.created_at) },
    { key: "read", label: "State", width: "90px", text: (n) => (n.read ? "Read" : "Unread"), render: (n) => <span className={n.read ? "badge-purple" : "badge-teal"}>{n.read ? "Read" : "Unread"}</span> },
    { key: "act", label: "", sortable: false, width: "190px", text: () => "",
      render: (n) => (
        <span className="inline-flex gap-1.5 justify-end w-full">
          {n.link?.startsWith("/") && <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => { if (!n.read) toggle.mutate(n); nav(n.link); }}>Open</button>}
          <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => toggle.mutate(n)}>{n.read ? "Mark unread" : "Mark read"}</button>
        </span>) },
  ];
  if (q.isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (q.isError) return <div className="card text-sm text-red-600">Could not load notifications. {errText(q.error)}</div>;
  return (
    <DataGrid columns={cols} rows={q.data?.items || []} rowKey={(n) => n.id} exportName="my-notifications"
      searchPlaceholder="Search notifications…" empty={unread || kind ? "Nothing matches these filters." : "No notifications yet."} minWidth={760}
      filters={
        <>
          <select className="input !py-1.5 text-sm !w-auto" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Type">
            <option value="">All types</option>
            {(q.data?.kinds || []).map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
          </select>
          <label className="inline-flex items-center gap-1.5 text-sm text-gray-600"><input type="checkbox" checked={unread} onChange={(e) => setUnread(e.target.checked)} /> Unread only</label>
        </>}
      toolbar={<button className="btn-secondary !py-1.5 !px-2.5 text-xs inline-flex items-center gap-1.5" disabled={!q.data?.unread || readAll.isPending} onClick={() => readAll.mutate()}><CheckCheck className="w-3.5 h-3.5" /> Mark all read</button>} />
  );
}

function Preferences() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["ntf", "prefs"], queryFn: notificationsApi.preferences });
  const save = useMutation({
    mutationFn: (v: Record<string, boolean>) => notificationsApi.savePreferences(v),
    onSuccess: () => { toast.success("Saved"); qc.invalidateQueries({ queryKey: ["ntf", "prefs"] }); },
    onError: (e) => toast.error(errText(e)),
  });
  if (q.isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (!q.data) return <div className="card text-sm text-red-600">Could not load your preferences.</div>;
  return (
    <div className="card max-w-3xl">
      <p className="text-sm text-gray-600 mb-1">In-app notifications are always on. Choose which types also reach you by e-mail.</p>
      {!q.data.email_available.configured && <p className="text-xs rounded-lg bg-amber-50 text-amber-800 px-3 py-2 my-2">{q.data.email_available.message}</p>}
      <div className="grid sm:grid-cols-2 gap-x-6 mt-3">
        {q.data.kinds.map((k) => (
          <label key={k.kind} className="flex items-center justify-between gap-3 py-2 border-b border-surface-border text-sm text-gray-700">
            <span>{k.label}</span>
            <span className="inline-flex items-center gap-2 text-xs text-gray-500">
              E-mail <input type="checkbox" className="w-4 h-4" checked={k.email_on} disabled={save.isPending}
                onChange={(e) => save.mutate({ [k.kind]: e.target.checked })} aria-label={`E-mail for ${k.label}`} />
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

// The employee's own opt-in for reminders from Lexora HRM's agents on WhatsApp ("agent_chase").
// Explicit (a general WhatsApp opt-in does not cover it), revocable here at any time, audited on the server.
const NOTIFY_EVENTS = ["payslip_ready", "leave_approved", "attendance_missing", "invite"];
function AgentRemindersWhatsApp() {
  const qc = useQueryClient();
  const [agree, setAgree] = useState(false);
  const q = useQuery({ queryKey: ["wa-optin-me"], queryFn: waOptinApi.me });
  const o = q.data?.optin;
  const active = o?.status === "opted_in";
  const on = active && (o?.events || []).includes("agent_chase");
  const done = (msg: string) => { toast.success(msg); setAgree(false); qc.invalidateQueries({ queryKey: ["wa-optin-me"] }); };
  const optIn = useMutation({
    mutationFn: () => {
      // keep what the person already agreed to; an old blanket consent ([] = every notification) is written out in full
      const prev = active ? (o!.events.length ? o!.events : NOTIFY_EVENTS) : [];
      const text = [active ? o!.consent_text : "", q.data?.agent_chase_consent || ""].filter(Boolean).join("\n\n");
      return waOptinApi.optIn({ events: Array.from(new Set([...prev, "agent_chase"])), consent_text: text });
    },
    onSuccess: () => done("Agent reminders on WhatsApp switched on"), onError: (e) => toast.error(errText(e)) });
  const optOut = useMutation({
    mutationFn: () => {
      const rest = (o?.events || []).filter((e) => e !== "agent_chase");
      return rest.length ? waOptinApi.optIn({ events: rest, consent_text: o!.consent_text }) : waOptinApi.optOut();
    },
    onSuccess: () => done("Agent reminders on WhatsApp stopped"), onError: (e) => toast.error(errText(e)) });
  if (q.isLoading || !q.data) return null;
  return (
    <div className="card max-w-3xl mt-4" data-testid="agent-chase-optin">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <MessageCircle className="w-4 h-4 text-primary-600" />
        <b className="text-sm">Reminders from Lexora HRM's agents on WhatsApp</b>
        <span className={`ml-auto ${on ? "badge-green" : "badge-purple"}`}>{on ? `On · ${o?.phone}` : "Off"}</span>
      </div>
      <p className="text-sm text-gray-600">The Onboarding, Exit and Attendance agents remind you when a task is waiting on you — in the app first, then by e-mail. If you agree, a later reminder can also reach you on WhatsApp.</p>
      <ul className="text-xs text-gray-600 list-disc pl-5 my-2 space-y-0.5">
        <li>The message names the task and links into Lexora HRM. It never carries amounts, ID numbers or anyone else's data.</li>
        <li>It goes to the mobile number on your employee record. If that number changes, you will need to agree again.</li>
        <li>Nothing is sent unless your company has switched WhatsApp on. You can stop it here at any time; every change is recorded.</li>
      </ul>
      {on ? (
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn-secondary" disabled={optOut.isPending} onClick={() => optOut.mutate()}>Stop agent reminders on WhatsApp</button>
          {o?.at && <span className="text-[11px] text-gray-400">Agreed {when(o.at)}</span>}
        </div>
      ) : (
        <>
          <p className="text-xs text-gray-700 bg-gray-50 rounded px-3 py-2 whitespace-pre-line">{q.data.agent_chase_consent}</p>
          <label className="flex items-start gap-2 text-sm text-gray-700 my-2">
            <input type="checkbox" className="w-4 h-4 mt-0.5" checked={agree} onChange={(e) => setAgree(e.target.checked)} aria-label="I agree to agent reminders on WhatsApp" />
            <span>I have read this and I agree.</span>
          </label>
          <button className="btn-primary" disabled={!agree || optIn.isPending} onClick={() => optIn.mutate()}>Send me agent reminders on WhatsApp</button>
          {active && <p className="text-[11px] text-gray-400 mt-2">Your other WhatsApp messages ({(o!.events.length ? o!.events : NOTIFY_EVENTS).filter((e) => e !== "agent_chase").join(", ").replace(/_/g, " ")}) stay as they are.</p>}
        </>
      )}
    </div>
  );
}

function DeliveryLog() {
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [channel, setChannel] = useState("");
  const q = useQuery({ queryKey: ["ntf", "log", status, channel], queryFn: () => notificationsApi.deliveries({ status: status || undefined, channel: channel || undefined, limit: 1000 }) });
  const refresh = () => qc.invalidateQueries({ queryKey: ["ntf", "log"] });
  const retry = useMutation({
    mutationFn: (d: Delivery) => notificationsApi.retry(d.id),
    onSuccess: (r) => { r.status === "sent" ? toast.success("The mail server accepted the e-mail") : toast.error(`Still not sent: ${statusText(r.status)}`); refresh(); },
    onError: (e) => toast.error(errText(e)),
  });
  const test = useMutation({
    mutationFn: notificationsApi.testEmail,
    onSuccess: (r) => { r.email === "sent" ? toast.success("Test e-mail accepted by the mail server") : toast(`In-app delivered. E-mail: ${statusText(r.email)}${r.reason ? ` — ${r.reason}` : ""}`, { duration: 7000 }); refresh(); qc.invalidateQueries({ queryKey: ["ntf", "bell"] }); },
    onError: (e) => toast.error(errText(e)),
  });
  const cols: GridColumn<Delivery>[] = [
    { key: "created_at", label: "When", width: "160px", render: (d) => when(d.created_at) },
    { key: "recipient_name", label: "Who", text: (d) => `${d.recipient_name} ${d.recipient_email}`,
      render: (d) => <div><div className="text-gray-800">{d.recipient_name || "—"}</div><div className="text-[11px] text-gray-400">{d.recipient_email}</div></div> },
    { key: "title", label: "What", text: (d) => `${d.title} ${d.kind_label}`, render: (d) => <div><div>{d.title}</div><div className="text-[11px] text-gray-400">{d.kind_label}</div></div> },
    { key: "channel", label: "Channel", width: "90px", text: (d) => (d.channel === "email" ? "E-mail" : "In-app"), render: (d) => (d.channel === "email" ? "E-mail" : "In-app") },
    { key: "status", label: "Status", width: "170px", text: (d) => statusText(d.status), render: (d) => <span className={STATUS[d.status]?.cls || "badge-purple"} style={{ textTransform: "none" }}>{statusText(d.status)}</span> },
    { key: "reason", label: "Reason", className: "text-gray-500 max-w-[280px]" },
    { key: "attempts", label: "Tries", type: "number", width: "60px" },
    { key: "act", label: "", sortable: false, width: "90px", text: () => "",
      render: (d) => d.can_retry ? <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" disabled={retry.isPending} onClick={() => retry.mutate(d)}><RotateCcw className="w-3 h-3" /> Retry</button> : null },
  ];
  const smtp = q.data?.smtp;
  return (
    <div>
      {smtp && (
        <div className={`rounded-lg px-3 py-2 mb-3 text-sm flex flex-wrap items-center gap-3 ${smtp.configured ? "bg-green-50 text-green-800" : "bg-amber-50 text-amber-800"}`}>
          <Mail className="w-4 h-4 shrink-0" /><span className="flex-1 min-w-[200px]">{smtp.message} “Sent” means the mail server accepted the message — nothing else is ever shown as sent.</span>
          <button className="btn-secondary !py-1 !px-2.5 text-xs inline-flex items-center gap-1.5" disabled={test.isPending} onClick={() => test.mutate()}><Send className="w-3.5 h-3.5" /> Send me a test</button>
        </div>
      )}
      {q.isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>
        : q.isError ? <div className="card text-sm text-red-600">Could not load the delivery log. {errText(q.error)}</div>
        : <DataGrid columns={cols} rows={q.data?.items || []} rowKey={(d) => d.id} exportName="notification-delivery-log" minWidth={1020}
            searchPlaceholder="Search person or message…" empty="Nothing has been sent yet."
            filters={
              <>
                <select className="input !py-1.5 text-sm !w-auto" value={channel} onChange={(e) => setChannel(e.target.value)} aria-label="Channel">
                  <option value="">All channels</option><option value="email">E-mail</option><option value="in_app">In-app</option>
                </select>
                <select className="input !py-1.5 text-sm !w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
                  <option value="">All statuses</option>
                  {Object.entries(STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}{q.data?.by_status?.[k] ? ` (${q.data.by_status[k]})` : ""}</option>)}
                </select>
              </>}
            toolbar={<button className="btn-secondary !py-1.5 !px-2.5 text-xs inline-flex items-center gap-1.5" onClick={refresh}><RefreshCw className="w-3.5 h-3.5" /> Refresh</button>} />}
    </div>
  );
}

function Templates() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["ntf", "templates"], queryFn: notificationsApi.templates });
  const [edit, setEdit] = useState<NoteTemplate | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const open = (t: NoteTemplate) => { setEdit(t); setSubject(t.subject); setBody(t.body); };
  const done = () => { setEdit(null); qc.invalidateQueries({ queryKey: ["ntf", "templates"] }); };
  const save = useMutation({ mutationFn: () => notificationsApi.saveTemplate(edit!.kind, subject, body), onSuccess: () => { toast.success("Template saved"); done(); }, onError: (e) => toast.error(errText(e), { duration: 7000 }) });
  const reset = useMutation({ mutationFn: () => notificationsApi.resetTemplate(edit!.kind), onSuccess: () => { toast.success("Back to the standard wording"); done(); }, onError: (e) => toast.error(errText(e)) });
  const cols = useMemo<GridColumn<NoteTemplate>[]>(() => [
    { key: "label", label: "Type", width: "200px" },
    { key: "subject", label: "Subject" },
    { key: "customised", label: "Wording", width: "130px", text: (t) => (t.customised ? "Your own" : "Standard"), render: (t) => <span className={t.customised ? "badge-teal" : "badge-purple"}>{t.customised ? "Your own" : "Standard"}</span> },
    { key: "updated_at", label: "Last changed", width: "220px", text: (t) => t.updated_at || "", render: (t) => t.updated_at ? `${when(t.updated_at)} · ${t.updated_by || ""}` : "—" },
    { key: "act", label: "", sortable: false, width: "70px", text: () => "", render: (t) => <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => open(t)}>Edit</button> },
  ], []);
  if (q.isLoading) return <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div>;
  if (!q.data) return <div className="card text-sm text-red-600">Could not load the templates.</div>;
  return (
    <div>
      <p className="text-xs rounded-lg bg-primary-50 text-primary-700 px-3 py-2 mb-3">{q.data.note}</p>
      <DataGrid columns={cols} rows={q.data.templates} rowKey={(t) => t.kind} exportName="email-templates" minWidth={760} pageSizeDefault={50} />
      {edit && (
        <WideModal title={`E-mail template — ${edit.label}`} onClose={() => setEdit(null)}
          footer={<>
            {edit.customised && <button className="btn-secondary text-sm" disabled={reset.isPending} onClick={() => reset.mutate()}>Use the standard wording</button>}
            <button className="btn-secondary text-sm" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn-primary text-sm" disabled={save.isPending} onClick={() => save.mutate()}>Save template</button>
          </>}>
          <div className="grid lg:grid-cols-3 gap-4">
            <div className="lg:col-span-2 space-y-3">
              <div><label className="block text-xs font-semibold text-gray-500 mb-1">Subject</label>
                <input className="input" value={subject} maxLength={300} onChange={(e) => setSubject(e.target.value)} /></div>
              <div><label className="block text-xs font-semibold text-gray-500 mb-1">Body</label>
                <textarea className="input min-h-[220px] font-mono text-[13px]" value={body} maxLength={5000} onChange={(e) => setBody(e.target.value)} /></div>
            </div>
            <div>
              <div className="text-xs font-semibold text-gray-500 mb-1">Placeholders you can use</div>
              <ul className="space-y-1.5">
                {q.data.placeholders.map((p) => (
                  <li key={p.name} className="text-xs text-gray-600">
                    <button className="font-mono text-primary-600 hover:underline" title="Add to the body" onClick={() => setBody((b) => `${b}{{${p.name}}}`)}>{`{{${p.name}}}`}</button>
                    <span className="block text-gray-400">{p.meaning}</span>
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-gray-400 mt-3">Only these placeholders work. Anything else is rejected when you save.</p>
            </div>
          </div>
        </WideModal>
      )}
    </div>
  );
}
