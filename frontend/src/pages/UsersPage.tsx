import { useMemo, useState, ReactNode } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import {
  KeyRound, Plus, X, Send, RotateCcw, Ban, CheckCircle2, UserCog, Copy, Settings2, Loader2,
} from "lucide-react";
import { PageHeader, Spinner } from "../components/ui";
import DataGrid, { GridColumn } from "../components/DataGrid";
import AppModal from "../components/Modal";
import {
  usersApi, LoginUser, LinkResult, errText, IdentitySettings,
} from "../api/identity";
import { useAuthStore, ADMIN_ROLES } from "../store/authStore";
import { can } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const STATUS_BADGE: Record<string, string> = { active: "badge-green", invited: "badge-amber", disabled: "badge-red" };
const STATUS_TEXT: Record<string, string> = { active: "Active", invited: "Invite sent", disabled: "Disabled" };
const LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1";

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, {
  day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "Never");

export default function UsersPage() {
  const qc = useQueryClient();
  const me = useAuthStore((s) => s.user);
  const isAdmin = can(me, "users.admin", ADMIN_ROLES);
  /* Deep link from the employee profile: /users?q=<employee number> pre-fills the search box
     so the matching login row is the one shown. The user can clear it as usual. */
  const [params] = useSearchParams();
  const initialSearch = params.get("q") || "";
  const { data, isLoading } = useQuery({ queryKey: ["logins"], queryFn: () => usersApi.list() });
  const [fRole, setFRole] = useState("");
  const [fStatus, setFStatus] = useState("");
  const [creating, setCreating] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [link, setLink] = useState<{ title: string; res: LinkResult } | null>(null);
  const [disabling, setDisabling] = useState<LoginUser | null>(null);
  const [roleFor, setRoleFor] = useState<LoginUser | null>(null);

  const refresh = () => qc.invalidateQueries({ queryKey: ["logins"] });
  const fail = (fallback: string) => (e: any) => toast.error(errText(e, fallback));
  const showLink = (title: string) => (res: LinkResult) => {
    refresh();
    if (res.email_sent) toast.success(`E-mail sent to ${res.email}`);
    else setLink({ title, res });
  };

  const resend = useMutation({ mutationFn: (u: LoginUser) => usersApi.resend(u.id),
    onSuccess: showLink("Invite link"), onError: fail("Could not send the invite") });
  const reset = useMutation({ mutationFn: (u: LoginUser) => usersApi.reset(u.id),
    onSuccess: showLink("Password reset link"), onError: fail("Could not send the reset link") });
  const enable = useMutation({ mutationFn: (u: LoginUser) => usersApi.enable(u.id),
    onSuccess: () => { toast.success("Login enabled"); refresh(); }, onError: fail("Could not enable the login") });

  const rows = useMemo(() => (data?.items || []).filter((u) =>
    (!fRole || u.role === fRole) && (!fStatus || u.status === fStatus)), [data, fRole, fStatus]);
  const navigate = useNavigate();
  const roleOpts = useMemo(() => [...new Map((data?.items || []).map((u) => [u.role, u.role_label])).entries()], [data]);

  const columns: GridColumn<LoginUser>[] = [
    { key: "name", label: "Name", render: (u) => <span className="font-medium text-gray-800">{u.name}</span> },
    { key: "employee", label: "Employee", text: (u) => [u.employee_number, u.employee_name].filter(Boolean).join(" "),
      render: (u) => u.employee_id
        ? <span>{u.employee_number || "—"}<span className="text-gray-400"> · {u.employee_name || "—"}</span></span>
        : <span className="text-gray-400">Not an employee</span> },
    { key: "email", label: "E-mail" },
    { key: "role_label", label: "Role", render: (u) => <span className="badge-purple">{u.role_label}</span> },
    { key: "status", label: "Status", text: (u) => STATUS_TEXT[u.status] || u.status,
      render: (u) => <span className={STATUS_BADGE[u.status] || "badge-purple"} title={u.disabled_reason || undefined}>
        {STATUS_TEXT[u.status] || u.status}</span> },
    { key: "last_login", label: "Last login", text: (u) => u.last_login || "", render: (u) => when(u.last_login) },
    { key: "actions", label: "Actions", sortable: false, text: () => "", render: (u) => {
      const self = u.id === me?.id;
      return (
        <div className="flex items-center gap-1 justify-end whitespace-nowrap">
          {u.status === "invited" && <Act title="Send the invite again" onClick={() => resend.mutate(u)} icon={<Send className="w-3.5 h-3.5" />}>Resend</Act>}
          {u.status === "active" && <Act title="Send a link to choose a new password" onClick={() => reset.mutate(u)} icon={<RotateCcw className="w-3.5 h-3.5" />}>Reset</Act>}
          {u.status !== "disabled" && !self && <Act title="Change role" onClick={() => setRoleFor(u)} icon={<UserCog className="w-3.5 h-3.5" />}>Role</Act>}
          {/* 2026-10-24 Roles & Permissions: custom roles + effective permissions of this login */}
          {!self && u.role !== "org_admin" && can(me, "users.edit", ["org_admin", "hr_admin"]) &&
            <Act title="Custom roles and effective permissions" onClick={() => navigate(`/roles?tab=people&user=${u.id}`)} icon={<UserCog className="w-3.5 h-3.5" />}>Access</Act>}
          {u.status !== "disabled" && !self && <Act title="Switch this login off" danger onClick={() => setDisabling(u)} icon={<Ban className="w-3.5 h-3.5" />}>Disable</Act>}
          {u.status === "disabled" && <Act title="Switch this login back on" onClick={() => enable.mutate(u)} icon={<CheckCircle2 className="w-3.5 h-3.5" />}>Enable</Act>}
        </div>
      );
    } },
  ];

  return (
    <div>
      <PageHeader title="Users & Logins" icon={KeyRound}
        subtitle="Who can sign in, and as what. Create a login, send an invite, reset a password or switch a login off."
        actions={
          <div className="flex items-center gap-2">
            <button className="btn-secondary flex items-center gap-1.5" onClick={() => setShowSettings(true)}>
              <Settings2 className="w-4 h-4" /> Settings</button>
            <button className="btn-primary flex items-center gap-1.5" onClick={() => setCreating(true)}>
              <Plus className="w-4 h-4" /> Create login</button>
          </div>} />

      {isLoading ? <div className="grid place-items-center py-20"><Spinner className="h-8 w-8" /></div> : (
        <DataGrid<LoginUser> columns={columns} rows={rows} rowKey={(u) => u.id} exportName="logins"
          searchPlaceholder="Search name, employee, e-mail, role…" empty="No logins match."
          initialSort={{ key: "name", dir: "asc" }} minWidth={980} initialSearch={initialSearch || undefined}
          filters={
            <>
              <select className="input !py-1.5 !w-auto text-sm" value={fRole} onChange={(e) => setFRole(e.target.value)}>
                <option value="">All roles</option>{roleOpts.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <select className="input !py-1.5 !w-auto text-sm" value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
                <option value="">All statuses</option>
                {Object.entries(STATUS_TEXT).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </>} />
      )}

      {creating && <CreateLogin roles={data?.roles || []} onClose={() => setCreating(false)}
        onDone={(res) => { setCreating(false); refresh();
          if (!res.created) toast("This employee already has a login.");
          else if (res.email_sent) toast.success(`Login created. Invite sent to ${res.email}`);
          else if (res.link || res.invited) setLink({ title: "Invite not sent", res: { email: res.email, email_sent: false, link: res.link } });
          else toast.success("Login created. Send the invite when you are ready."); }} />}
      {link && <LinkBox title={link.title} res={link.res} onClose={() => setLink(null)} />}
      {disabling && <DisableBox user={disabling} onClose={() => setDisabling(null)} onDone={() => { setDisabling(null); refresh(); }} />}
      {roleFor && <RoleBox user={roleFor} roles={data?.roles || []} onClose={() => setRoleFor(null)} onDone={() => { setRoleFor(null); refresh(); }} />}
      {showSettings && <SettingsBox canEdit={isAdmin} onClose={() => setShowSettings(false)} />}
    </div>
  );
}

function Act({ children, icon, onClick, title, danger }:
  { children: ReactNode; icon: ReactNode; onClick: () => void; title: string; danger?: boolean }) {
  return (
    <button title={title} onClick={(e) => { e.stopPropagation(); onClick(); }}
      className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded-md border border-surface-border hover:border-primary-300 ${
        danger ? "text-red-600 hover:bg-red-50" : "text-gray-600 hover:bg-primary-50"}`}>
      {icon}{children}
    </button>
  );
}

/* Wide dialog shell — uses the screen, two columns on desktop, one on a phone. */
// 2026-10-24: thin wrapper over the app-wide AppModal (wide = max-w-6xl).
export function Dialog({ title, sub, children, onClose, footer }:
  { title: string; sub?: string; children: ReactNode; onClose: () => void; footer?: ReactNode }) {
  return <AppModal title={title} subtitle={sub} onClose={onClose} footer={footer} size="wide">{children}</AppModal>;
}

function CreateLogin({ roles, onClose, onDone }: { roles: { key: string; label: string }[]; onClose: () => void; onDone: (r: any) => void }) {
  const { data: emps, isLoading } = useQuery({ queryKey: ["logins", "unlinked"], queryFn: usersApi.unlinked });
  const [employeeId, setEmployeeId] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("employee");
  const [sendInvite, setSendInvite] = useState(true);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const emp = (emps || []).find((e) => e.id === employeeId);
  const create = useMutation({
    mutationFn: () => usersApi.create({ employee_id: employeeId || null, email: email.trim(), role, send_invite: sendInvite,
      first_name: employeeId ? undefined : first.trim(), last_name: employeeId ? undefined : last.trim() }),
    onSuccess: onDone, onError: (e: any) => toast.error(errText(e, "Could not create the login")) });
  const bad = !email.trim() || (!employeeId && !first.trim());

  return (
    <Dialog title="Create login" sub="One login per employee. The person chooses their own password from the invite e-mail."
      onClose={onClose}
      footer={<>
        <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs flex items-center gap-1.5 disabled:opacity-60" disabled={bad || create.isPending}
          onClick={() => create.mutate()}>
          {create.isPending && <Loader2 className="w-3.5 h-3.5 animate-spin" />} Create login</button>
      </>}>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="md:col-span-2">
          <label className={LABEL}>Employee</label>
          <select className="input" value={employeeId} disabled={isLoading}
            onChange={(e) => { setEmployeeId(e.target.value);
              const x = (emps || []).find((y) => y.id === e.target.value); setEmail(x?.email || ""); }}>
            <option value="">{isLoading ? "Loading…" : "— Not an employee (outside user) —"}</option>
            {(emps || []).map((e) => <option key={e.id} value={e.id}>
              {e.employee_number ? `${e.employee_number} · ` : ""}{e.employee_name}{e.department ? ` · ${e.department}` : ""}</option>)}
          </select>
          <p className="text-[11px] text-gray-400 mt-1">Only employees who do not have a login yet are listed.</p>
        </div>
        {!employeeId && <>
          <div><label className={LABEL}>First name</label><input className="input" value={first} onChange={(e) => setFirst(e.target.value)} /></div>
          <div><label className={LABEL}>Last name</label><input className="input" value={last} onChange={(e) => setLast(e.target.value)} /></div>
        </>}
        <div>
          <label className={LABEL}>E-mail to sign in with</label>
          <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
          {emp && !emp.email && <p className="text-[11px] text-amber-600 mt-1">This employee has no e-mail on file. Type one.</p>}
        </div>
        <div>
          <label className={LABEL}>Role</label>
          <select className="input" value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
        </div>
        <label className="md:col-span-2 flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
          <input type="checkbox" checked={sendInvite} onChange={(e) => setSendInvite(e.target.checked)} />
          Send the invite e-mail now
        </label>
      </div>
    </Dialog>
  );
}

export function LinkBox({ title, res, onClose }: { title: string; res: LinkResult; onClose: () => void }) {
  const copy = async () => {
    try { await navigator.clipboard.writeText(res.link || ""); toast.success("Link copied"); }
    catch { toast.error("Could not copy. Select the link and copy it."); }
  };
  // Decision 2026-10-21: the server never hands the link out on a real installation — the
  // person must get it by e-mail. Only a local test machine (LEXORA_DEV_SHOW_LINKS=1) sends it.
  if (!res.link) return (
    <Dialog title={title} sub={`For ${res.email}`} onClose={onClose}
      footer={<button className="btn-primary text-xs" onClick={onClose}>OK</button>}>
      <div className="rounded-lg border border-amber-200 bg-amber-50 text-amber-900 text-sm p-3" data-testid="link-not-sent">
        <p className="font-semibold">The e-mail could not be sent.</p>
        <p className="mt-1">For safety, Lexora HRM does not show the link on screen — only the person may receive it.
          Ask your admin to set up e-mail (Company settings → E-mail sender), then press <b>Resend</b> here.</p>
      </div>
    </Dialog>
  );
  return (
    <Dialog title={title} sub={`For ${res.email}`} onClose={onClose}
      footer={<button className="btn-primary text-xs" onClick={onClose}>Done</button>}>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-amber-700 mb-1">Test set-up only</p>
      <p className="text-sm text-gray-700 mb-3">
        The e-mail could not be sent (no mail server is set up). Give this link to the person yourself.
        It works once{res.expires_in_hours ? ` and expires in ${res.expires_in_hours >= 1 ? `${Math.round(res.expires_in_hours)} hours` : `${Math.round(res.expires_in_hours * 60)} minutes`}` : ""}.
      </p>
      <div className="flex flex-col sm:flex-row gap-2">
        <input className="input font-mono text-xs flex-1" readOnly value={res.link || ""} onFocus={(e) => e.target.select()} />
        <button className="btn-secondary flex items-center justify-center gap-1.5 text-xs" onClick={copy}><Copy className="w-3.5 h-3.5" /> Copy link</button>
      </div>
    </Dialog>
  );
}

function DisableBox({ user, onClose, onDone }: { user: LoginUser; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const m = useMutation({ mutationFn: () => usersApi.disable(user.id, reason.trim()),
    onSuccess: () => { toast.success("Login disabled"); onDone(); },
    onError: (e: any) => toast.error(errText(e, "Could not disable the login")) });
  return (
    <Dialog title={`Disable login — ${user.name}`} sub={user.email} onClose={onClose}
      footer={<>
        <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs !bg-red-600 hover:!bg-red-700 disabled:opacity-60" disabled={m.isPending} onClick={() => m.mutate()}>Disable login</button>
      </>}>
      <p className="text-sm text-gray-700 mb-3">This person will be signed out within a few seconds and cannot sign in again until you enable the login.</p>
      <label className={LABEL}>Reason (kept in the audit log)</label>
      <input className="input" value={reason} autoFocus onChange={(e) => setReason(e.target.value)} placeholder="e.g. Left the company on 31 Oct" />
    </Dialog>
  );
}

function RoleBox({ user, roles, onClose, onDone }: { user: LoginUser; roles: { key: string; label: string }[]; onClose: () => void; onDone: () => void }) {
  const [role, setRole] = useState(user.role);
  const m = useMutation({ mutationFn: () => usersApi.setRole(user.id, role),
    onSuccess: () => { toast.success("Role changed"); onDone(); },
    onError: (e: any) => toast.error(errText(e, "Could not change the role")) });
  return (
    <Dialog title={`Change role — ${user.name}`} sub={user.email} onClose={onClose}
      footer={<>
        <button className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button className="btn-primary text-xs disabled:opacity-60" disabled={m.isPending || role === user.role} onClick={() => m.mutate()}>Save role</button>
      </>}>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {roles.map((r) => (
          <button key={r.key} onClick={() => setRole(r.key)}
            className={`text-left rounded-lg border px-3 py-2.5 text-sm ${role === r.key ? "border-primary-500 bg-primary-50 text-primary-700 font-semibold" : "border-surface-border text-gray-700 hover:border-primary-300"}`}>
            {r.label}{r.key === user.role && <span className="text-[11px] text-gray-400 font-normal"> · now</span>}
          </button>
        ))}
      </div>
      <p className="text-xs text-gray-500 mt-3">The person is signed out and gets the new role when they sign in again.</p>
    </Dialog>
  );
}

function SettingsBox({ canEdit, onClose }: { canEdit: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["logins", "settings"], queryFn: usersApi.settings });
  const [form, setForm] = useState<Partial<IdentitySettings> | null>(null);
  const f = form || data;
  const save = useMutation({ mutationFn: () => usersApi.saveSettings({
      auto_login_on_onboarding: f!.auto_login_on_onboarding, invite_expiry_hours: Number(f!.invite_expiry_hours),
      reset_expiry_minutes: Number(f!.reset_expiry_minutes) }),
    onSuccess: () => { toast.success("Settings saved"); qc.invalidateQueries({ queryKey: ["logins", "settings"] }); onClose(); },
    onError: (e: any) => toast.error(errText(e, "Could not save the settings")) });
  const set = (k: keyof IdentitySettings, v: any) => setForm({ ...(f as any), [k]: v });
  return (
    <Dialog title="Login settings" sub={canEdit ? "Applies to the whole company" : "Only an admin can change these"} onClose={onClose}
      footer={<>
        <button className="btn-secondary text-xs" onClick={onClose}>Close</button>
        {canEdit && <button className="btn-primary text-xs disabled:opacity-60" disabled={!f || save.isPending} onClick={() => save.mutate()}>Save</button>}
      </>}>
      {!f ? <div className="grid place-items-center py-10"><Spinner /></div> : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <label className="md:col-span-2 flex items-start gap-2.5 p-3 rounded-lg border border-surface-border cursor-pointer">
            <input type="checkbox" className="mt-0.5" disabled={!canEdit} checked={!!f.auto_login_on_onboarding}
              onChange={(e) => set("auto_login_on_onboarding", e.target.checked)} />
            <span className="text-sm text-gray-700"><b>Create a login when a new joiner is approved.</b><br />
              <span className="text-gray-500">The joiner gets an e-mail to choose a password. Switch off to create logins by hand.</span></span>
          </label>
          <div>
            <label className={LABEL}>Invite link works for (hours)</label>
            <input className="input" type="number" min={1} max={720} disabled={!canEdit} value={f.invite_expiry_hours ?? 72}
              onChange={(e) => set("invite_expiry_hours", e.target.value)} />
          </div>
          <div>
            <label className={LABEL}>Reset link works for (minutes)</label>
            <input className="input" type="number" min={10} max={1440} disabled={!canEdit} value={f.reset_expiry_minutes ?? 60}
              onChange={(e) => set("reset_expiry_minutes", e.target.value)} />
          </div>
          <p className="md:col-span-2 text-xs text-gray-500">{data?.password_policy}</p>
        </div>
      )}
    </Dialog>
  );
}
