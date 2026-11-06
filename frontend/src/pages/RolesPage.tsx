/**
 * Settings → Roles & Permissions (go-live wrap-up 2026-10-24).
 *
 * Tabs: Roles (system roles read-only + clone; custom roles create / edit / retire / restore / delete, members),
 * People (give one or more custom roles to a login; effective-permission preview), Change history.
 * Every rule is enforced by the server (auth rbac_routes + shared/utils/rbac_grant, gateway rbac_gate); this
 * screen only greys out what the server would refuse and shows the server's refusal (with its code) otherwise.
 */
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import {
  ShieldCheck, Copy, Plus, Pencil, Archive, RotateCcw, Trash2, Users, History, Search, X, Lock, Eye, Check,
} from "lucide-react";
import { PageHeader, Spinner, Empty } from "../components/ui";
import { rbacApi, rbacError, type Catalogue, type CustomRole, type RoleInput, type RoleUser, type SystemRole } from "../api/rbac";
import { useAuthStore, ROLE_LABEL } from "../store/authStore";
import { useModalLayer } from "../components/Modal";   // Esc closes (top-most only), scroll lock
import { can } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

type Tab = "roles" | "people" | "history";

const actingRole = (base: string, reach: string) =>
  reach === "own" ? "employee" : reach === "team" ? (base === "employee" ? "employee" : "manager") : base;

function fmt(ts?: string | null) {
  if (!ts) return "—";
  try { return new Date(ts).toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short" }); } catch { return ts; }
}

/** Full-screen-ish modal (wide by design: modals use the screen). */
function Shell({ title, sub, onClose, children, footer }: {
  title: string; sub?: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode;
}) {
  useModalLayer(onClose);
  return (
    <div className="fixed inset-0 z-[60] bg-black/40 flex items-center justify-center p-2 sm:p-4" role="dialog" aria-modal="true">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-[1400px] h-[94vh] flex flex-col">
        <div className="flex items-start justify-between gap-3 px-5 py-3 border-b border-surface-border">
          <div className="min-w-0">
            <h2 className="font-display font-semibold text-gray-800 text-lg truncate">{title}</h2>
            {sub && <p className="text-xs text-gray-500">{sub}</p>}
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-gray-100" aria-label="Close"><X className="w-5 h-5" /></button>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <div className="px-5 py-3 border-t border-surface-border flex flex-wrap items-center justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

// ── permission matrix (read-only or editable) ─────────────────────────────────

function Matrix({ cat, value, onChange, allowed, filter, why }: {
  cat: Catalogue; value: Set<string>; onChange?: (next: Set<string>) => void;
  /** permissions that may be ticked (editable mode) */
  allowed?: Set<string>;
  filter: string;
  why?: (perm: string) => string | null;
}) {
  const cols = useMemo(() => {
    const extra = new Set<string>();
    cat.modules.forEach((m) => m.actions.forEach((a) => extra.add(a)));
    return ["view", "create", "edit", "approve", "delete", "export", "admin", ...[...extra].filter((a) =>
      !["view", "create", "edit", "approve", "delete", "export", "admin"].includes(a))];
  }, [cat]);
  const f = filter.trim().toLowerCase();
  const mods = cat.modules.filter((m) => !m.platform_only && (!f || `${m.label} ${m.group} ${m.key} ${m.desc}`.toLowerCase().includes(f)));
  const groups = [...new Set(mods.map((m) => m.group))];
  const edit = !!onChange;
  const can = (p: string) => !edit || !allowed || allowed.has(p);
  const toggle = (p: string) => {
    if (!onChange || !can(p)) return;
    const n = new Set(value); n.has(p) ? n.delete(p) : n.add(p); onChange(n);
  };
  const toggleRow = (mkey: string, actions: string[]) => {
    if (!onChange) return;
    const ps = actions.map((a) => `${mkey}.${a}`).filter(can);
    const all = ps.length > 0 && ps.every((p) => value.has(p));
    const n = new Set(value); ps.forEach((p) => (all ? n.delete(p) : n.add(p))); onChange(n);
  };
  return (
    <div className="overflow-x-auto border border-surface-border rounded-lg">
      <table className="min-w-full text-xs">
        <thead className="bg-gray-50 sticky top-0 z-10">
          <tr>
            <th className="text-left px-2 py-1.5 font-semibold text-gray-600 w-72">Module</th>
            {edit && <th className="px-1 py-1.5 font-semibold text-gray-600">All</th>}
            {cols.map((c) => <th key={c} className="px-1 py-1.5 font-semibold text-gray-600 whitespace-nowrap">{cat.action_labels[c] || c}</th>)}
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <FragmentRows key={g} label={g} span={cols.length + (edit ? 2 : 1)}>
              {mods.filter((m) => m.group === g).map((m) => {
                const ps = m.actions.map((a) => `${m.key}.${a}`);
                const rowAll = ps.filter(can).length > 0 && ps.filter(can).every((p) => value.has(p));
                return (
                  <tr key={m.key} className="border-t border-surface-border hover:bg-primary-50/30">
                    <td className="px-2 py-1"><div className="font-medium text-gray-800">{m.label}</div>
                      <div className="text-[10px] text-gray-400 truncate max-w-[18rem]" title={m.desc}>{m.desc}</div></td>
                    {edit && <td className="text-center"><input type="checkbox" aria-label={`All of ${m.label}`} checked={rowAll}
                      disabled={!ps.some(can)} onChange={() => toggleRow(m.key, m.actions)} /></td>}
                    {cols.map((c) => {
                      if (!m.actions.includes(c)) return <td key={c} className="text-center text-gray-200">·</td>;
                      const p = `${m.key}.${c}`;
                      const on = value.has(p);
                      if (!edit) return <td key={c} className="text-center">{on ? <Check className="w-3.5 h-3.5 text-emerald-600 inline" /> : <span className="text-gray-200">—</span>}</td>;
                      const reason = why?.(p) || null;
                      return (
                        <td key={c} className="text-center">
                          <input type="checkbox" checked={on} disabled={!can(p) && !on} onChange={() => toggle(p)}
                            title={reason || p} aria-label={p} className={!can(p) ? "opacity-30" : ""} />
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </FragmentRows>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FragmentRows({ label, span, children }: { label: string; span: number; children: React.ReactNode }) {
  return (
    <>
      <tr className="bg-gray-100/70"><td colSpan={span} className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-gray-500">{label}</td></tr>
      {children}
    </>
  );
}

function DataScopes({ cat, value, onChange, allowed, why }: {
  cat: Catalogue; value: Set<string>; onChange?: (n: Set<string>) => void; allowed?: Set<string>; why?: (p: string) => string | null;
}) {
  return (
    <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-2">
      {cat.data_scopes.map((d) => {
        const on = value.has(d.key);
        const can = !onChange || !allowed || allowed.has(d.key);
        return (
          <label key={d.key} title={why?.(d.key) || d.desc}
            className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${on ? "border-amber-400 bg-amber-50" : "border-surface-border"} ${!can ? "opacity-50" : ""}`}>
            {onChange ? <input type="checkbox" className="mt-0.5" checked={on} disabled={!can && !on}
              onChange={() => { const n = new Set(value); n.has(d.key) ? n.delete(d.key) : n.add(d.key); onChange(n); }} />
              : on ? <Check className="w-4 h-4 text-amber-600 mt-0.5" /> : <Lock className="w-4 h-4 text-gray-300 mt-0.5" />}
            <span><span className="font-semibold text-gray-800">{d.label}</span><br /><span className="text-gray-500">{d.desc}</span></span>
          </label>
        );
      })}
    </div>
  );
}

// ── role editor ───────────────────────────────────────────────────────────────

function RoleEditor({ cat, initial, onClose, onSaved }: {
  cat: Catalogue; initial: Partial<CustomRole> & { cloned_from?: string | null }; onClose: () => void; onSaved: () => void;
}) {
  const me = useAuthStore((s) => s.user);
  const [name, setName] = useState(initial.name || "");
  const [desc, setDesc] = useState(initial.description || "");
  const [base, setBase] = useState(initial.base_role || "hr_manager");
  const [reach, setReach] = useState(initial.reach || "org");
  const [perms, setPerms] = useState<Set<string>>(new Set(initial.permissions || []));
  const [filter, setFilter] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const reaches = cat.reach_by_base[base] || ["org"];
  const act = actingRole(base, reaches.includes(reach) ? reach : reaches[reaches.length - 1]);
  const ceiling = useMemo(() => new Set(cat.system_roles.find((r) => r.key === act)?.permissions || []), [cat, act]);
  const mine = useMemo(() => new Set(me?.role === "superadmin" ? [...ceiling] : me?.permissions || []), [me, ceiling]);
  const nonDel = useMemo(() => new Set(cat.non_delegable), [cat]);
  const narrow = cat.reaches.find((r) => r.key === reach)?.narrow;
  const narrowOk = useMemo(() => new Set(cat.narrow_reach_permissions), [cat]);
  const allowed = useMemo(() => {
    const out = new Set<string>();
    ceiling.forEach((p) => { if (mine.has(p) && !nonDel.has(p) && (!narrow || narrowOk.has(p))) out.add(p); });
    return out;
  }, [ceiling, mine, nonDel, narrow, narrowOk]);
  const why = (p: string) => nonDel.has(p) ? "Never on a custom role (logins, roles, platform)"
    : !ceiling.has(p) ? `The ${ROLE_LABEL[act] || act} system role this acts as does not have it`
    : !mine.has(p) ? "You do not have this permission yourself"
    : narrow && !narrowOk.has(p) ? "Department / location / entity reach covers the People directory only" : null;
  const outside = [...perms].filter((p) => !allowed.has(p));
  const save = useMutation({
    mutationFn: () => {
      const b: RoleInput = { name, description: desc || null, base_role: base, reach, permissions: [...perms], cloned_from: initial.cloned_from || null };
      return initial.id ? rbacApi.update(initial.id, b) : rbacApi.create(b);
    },
    onSuccess: () => { toast.success(initial.id ? "Role saved" : "Role created"); onSaved(); },
    onError: (e) => setErr(rbacError(e)),
  });
  const count = (prefix: string) => [...perms].filter((p) => !p.startsWith("data.") && (!prefix || p.startsWith(prefix))).length;
  return (
    <Shell title={initial.id ? `Edit role — ${initial.name}` : initial.cloned_from ? `New role from ${ROLE_LABEL[initial.cloned_from] || initial.cloned_from}` : "New custom role"}
      sub="A custom role can only narrow the system role it is built on. You can only give permissions you have yourself."
      onClose={onClose}
      footer={<>
        {err && <span className="text-xs text-red-600 mr-auto max-w-[60%]">{err}</span>}
        <span className="text-xs text-gray-500">{count("")} module permissions · {[...perms].filter((p) => p.startsWith("data.")).length} data scopes</span>
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={save.isPending || name.trim().length < 2 || perms.size === 0 || outside.length > 0}
          onClick={() => { setErr(null); save.mutate(); }}>{save.isPending ? "Saving…" : "Save role"}</button>
      </>}>
      <div className="grid lg:grid-cols-12 gap-3 mb-4">
        <label className="lg:col-span-4 text-xs font-medium text-gray-600">Role name *
          <input className="input mt-1" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Leave desk officer" /></label>
        <label className="lg:col-span-8 text-xs font-medium text-gray-600">Description (what this person does)
          <input className="input mt-1" maxLength={1000} value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="e.g. Checks and approves leave for all locations; no pay data" /></label>
        <label className="lg:col-span-4 text-xs font-medium text-gray-600">Built on (system role) *
          <select className="input mt-1" value={base} onChange={(e) => { setBase(e.target.value); const r = cat.reach_by_base[e.target.value] || ["org"]; if (!r.includes(reach)) setReach(r[r.length - 1]); }}>
            {cat.custom_bases.map((b) => <option key={b} value={b}>{ROLE_LABEL[b] || b}</option>)}
          </select>
          <span className="text-[10px] text-gray-400">Org Admin cannot be used — it is managed by the Lexora platform operator.</span></label>
        <label className="lg:col-span-4 text-xs font-medium text-gray-600">Data reach *
          <select className="input mt-1" value={reach} onChange={(e) => setReach(e.target.value)}>
            {cat.reaches.filter((r) => reaches.includes(r.key)).map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
          </select>
          <span className="text-[10px] text-gray-400">{narrow ? "Department / location / entity reach applies to the People directory and profiles only." : `Acts as ${ROLE_LABEL[act] || act} on the server.`}</span></label>
        <label className="lg:col-span-4 text-xs font-medium text-gray-600">Find a module
          <div className="relative mt-1"><Search className="w-4 h-4 absolute left-2 top-2.5 text-gray-400" />
            <input className="input pl-8" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="payroll, leave, books…" /></div></label>
      </div>
      {outside.length > 0 && (
        <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {outside.length} ticked permission(s) are not allowed with this base / reach / your own access — untick them or change the base:
          {" "}{outside.slice(0, 10).join(", ")}{outside.length > 10 ? "…" : ""}
          <button className="ml-2 underline" onClick={() => setPerms(new Set([...perms].filter((p) => allowed.has(p))))}>Remove them</button>
        </div>
      )}
      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">Sensitive data</h3>
      <div className="mb-4"><DataScopes cat={cat} value={perms} onChange={setPerms} allowed={allowed} why={why} /></div>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">Modules × actions</h3>
      <Matrix cat={cat} value={perms} onChange={setPerms} allowed={allowed} filter={filter} why={why} />
    </Shell>
  );
}

// ── members ───────────────────────────────────────────────────────────────────

function Members({ role, onClose }: { role: CustomRole; onClose: () => void }) {
  const qc = useQueryClient();
  const detail = useQuery({ queryKey: ["rbac-role", role.id], queryFn: () => rbacApi.role(role.id) });
  const [q, setQ] = useState("");
  const users = useQuery({ queryKey: ["rbac-users"], queryFn: () => rbacApi.users() });
  const [pick, setPick] = useState<Set<string>>(new Set());
  const refresh = () => { qc.invalidateQueries({ queryKey: ["rbac-role", role.id] }); qc.invalidateQueries({ queryKey: ["rbac-roles"] }); qc.invalidateQueries({ queryKey: ["rbac-users"] }); };
  const add = useMutation({ mutationFn: () => rbacApi.addMembers(role.id, [...pick]),
    onSuccess: (r: any) => { toast.success(`${r.added} added`); setPick(new Set()); refresh(); }, onError: (e) => toast.error(rbacError(e)) });
  const rm = useMutation({ mutationFn: (uid: string) => rbacApi.removeMember(role.id, uid),
    onSuccess: () => { toast.success("Removed"); refresh(); }, onError: (e) => toast.error(rbacError(e)) });
  const memberIds = new Set((detail.data?.members || []).map((m) => m.user_id));
  const cands = (users.data?.items || []).filter((u) => !memberIds.has(u.user_id) && u.is_active && u.system_role !== "org_admin"
    && (!q || `${u.name} ${u.email} ${u.employee_number || ""} ${u.department || ""}`.toLowerCase().includes(q.toLowerCase())));
  return (
    <Shell title={`People with "${role.name}"`} sub={`${ROLE_LABEL[role.acting_role || ""] || role.acting_role} access · ${role.reach_label || role.reach}`} onClose={onClose}
      footer={<><span className="text-xs text-gray-500 mr-auto">{pick.size} selected</span>
        <button className="btn-secondary" onClick={onClose}>Close</button>
        <button className="btn-primary" disabled={!pick.size || add.isPending || role.status !== "active"} onClick={() => add.mutate()}>Give role to selected</button></>}>
      <div className="grid lg:grid-cols-2 gap-4">
        <div>
          <h3 className="text-xs font-semibold uppercase text-gray-500 mb-2">Has this role ({detail.data?.members.length ?? "…"})</h3>
          <table className="w-full text-xs border border-surface-border rounded-lg">
            <thead className="bg-gray-50"><tr><th className="text-left px-2 py-1">Person</th><th className="text-left px-2">System role</th><th className="text-left px-2">Given</th><th /></tr></thead>
            <tbody>{(detail.data?.members || []).map((m) => (
              <tr key={m.user_id} className="border-t border-surface-border">
                <td className="px-2 py-1"><div className="font-medium">{m.name}</div><div className="text-gray-400">{m.email}</div></td>
                <td className="px-2">{ROLE_LABEL[m.system_role] || m.system_role}</td>
                <td className="px-2 text-gray-500">{fmt(m.assigned_at)}<br />{m.assigned_by}</td>
                <td className="px-2 text-right"><button className="text-red-600 hover:underline" onClick={() => rm.mutate(m.user_id)}>Remove</button></td>
              </tr>))}
              {!detail.data?.members.length && <tr><td colSpan={4} className="px-2 py-4 text-center text-gray-400">Nobody yet</td></tr>}
            </tbody>
          </table>
        </div>
        <div>
          <h3 className="text-xs font-semibold uppercase text-gray-500 mb-2">Add people</h3>
          <div className="relative mb-2"><Search className="w-4 h-4 absolute left-2 top-2.5 text-gray-400" />
            <input className="input pl-8" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, e-mail, employee no., department" /></div>
          <div className="max-h-[60vh] overflow-y-auto border border-surface-border rounded-lg">
            {cands.slice(0, 300).map((u) => (
              <label key={u.user_id} className="flex items-center gap-2 px-2 py-1.5 border-b border-surface-border text-xs hover:bg-gray-50">
                <input type="checkbox" checked={pick.has(u.user_id)} onChange={() => { const n = new Set(pick); n.has(u.user_id) ? n.delete(u.user_id) : n.add(u.user_id); setPick(n); }} />
                <span className="flex-1"><span className="font-medium">{u.name}</span> <span className="text-gray-400">{u.email}</span></span>
                <span className="text-gray-500">{u.employee_number || ""} {u.department ? `· ${u.department}` : ""}</span>
                <span className="badge-teal">{u.system_role_label}</span>
              </label>))}
            {!cands.length && <div className="p-4 text-center text-xs text-gray-400">No one else to add (Org Admin logins are changed only by the Lexora platform operator)</div>}
          </div>
        </div>
      </div>
    </Shell>
  );
}

// ── people tab: a login's roles + effective permissions ───────────────────────

export function UserRolesEditor({ cat, user, onClose }: { cat: Catalogue; user: Pick<RoleUser, "user_id" | "name" | "email">; onClose: () => void }) {
  const qc = useQueryClient();
  const roles = useQuery({ queryKey: ["rbac-roles"], queryFn: rbacApi.roles });
  const pv = useQuery({ queryKey: ["rbac-preview", user.user_id], queryFn: () => rbacApi.preview(user.user_id) });
  const [sel, setSel] = useState<Set<string> | null>(null);
  const current = new Set((pv.data?.custom_roles || []).map((c) => c.id));
  const chosen = sel ?? current;
  const save = useMutation({ mutationFn: () => rbacApi.setUserRoles(user.user_id, [...chosen]),
    onSuccess: () => { toast.success("Roles saved — applies within seconds"); setSel(null); qc.invalidateQueries({ queryKey: ["rbac-preview", user.user_id] }); qc.invalidateQueries({ queryKey: ["rbac-users"] }); qc.invalidateQueries({ queryKey: ["rbac-roles"] }); },
    onError: (e) => toast.error(rbacError(e)) });
  const perms = new Set(pv.data?.perms || []);
  return (
    <Shell title={`Roles of ${user.name}`} sub={user.email} onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Close</button>
        <button className="btn-primary" disabled={!sel || save.isPending} onClick={() => save.mutate()}>Save roles</button></>}>
      {!pv.data ? <Spinner /> : (
        <div className="grid xl:grid-cols-12 gap-4">
          <div className="xl:col-span-4 space-y-3">
            <div className="card">
              <div className="text-xs text-gray-500">System role (Setup → Users &amp; Logins)</div>
              <div className="font-semibold text-gray-800">{pv.data.system_role_label}</div>
              <div className="text-xs text-gray-500 mt-1">Widest data reach: {pv.data.reach_label}</div>
            </div>
            <div className="card">
              <div className="text-xs font-semibold uppercase text-gray-500 mb-2">Custom roles</div>
              {(roles.data?.custom || []).map((r) => (
                <label key={r.id} className={`flex items-start gap-2 py-1 text-sm ${r.status !== "active" ? "opacity-50" : ""}`}>
                  <input type="checkbox" className="mt-1" checked={chosen.has(r.id)} disabled={r.status !== "active" && !chosen.has(r.id)}
                    onChange={() => { const n = new Set(chosen); n.has(r.id) ? n.delete(r.id) : n.add(r.id); setSel(n); }} />
                  <span><span className="font-medium">{r.name}</span>{r.status !== "active" && <span className="badge-amber ml-1">retired</span>}
                    <br /><span className="text-xs text-gray-500">{ROLE_LABEL[r.acting_role || ""] || r.acting_role} · {r.reach_label} · {r.permissions.length} permissions</span></span>
                </label>))}
              {!roles.data?.custom.length && <p className="text-xs text-gray-400">No custom roles yet — create one on the Roles tab.</p>}
            </div>
          </div>
          <div className="xl:col-span-8">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 flex items-center gap-1"><Eye className="w-3.5 h-3.5" /> Effective permissions (saved state)</h3>
            <div className="mb-3"><DataScopes cat={cat} value={perms} /></div>
            <Matrix cat={cat} value={perms} filter="" />
          </div>
        </div>
      )}
    </Shell>
  );
}

// ── page ──────────────────────────────────────────────────────────────────────

export default function RolesPage() {
  const [sp, setSp] = useSearchParams();
  const tab = (sp.get("tab") as Tab) || "roles";
  const setTab = (t: Tab) => { const n = new URLSearchParams(sp); n.set("tab", t); setSp(n, { replace: true }); };
  const me = useAuthStore((s) => s.user);
  // writes stay with the SYSTEM roles (the server checks the system role from the database; custom roles never
  // carry roles.* writes — rbac_grant non-delegable); a custom role with roles.view reads only.
  const canWrite = can(me, "roles.edit", ["org_admin", "hr_admin", "superadmin"]);
  const qc = useQueryClient();
  const cat = useQuery({ queryKey: ["rbac-catalogue"], queryFn: rbacApi.catalogue, staleTime: 300_000 });
  const roles = useQuery({ queryKey: ["rbac-roles"], queryFn: rbacApi.roles });
  const [sel, setSel] = useState<string>("sys:hr_admin");
  const [editing, setEditing] = useState<(Partial<CustomRole> & { cloned_from?: string | null }) | null>(null);
  const [members, setMembers] = useState<CustomRole | null>(null);
  const [filter, setFilter] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["rbac-roles"] }); qc.invalidateQueries({ queryKey: ["rbac-history"] }); };
  const act = useMutation({
    mutationFn: ({ kind, id }: { kind: "retire" | "restore" | "delete"; id: string }) =>
      kind === "retire" ? rbacApi.retire(id) : kind === "restore" ? rbacApi.restore(id) : rbacApi.remove(id),
    onSuccess: (_d, v) => { toast.success(v.kind === "delete" ? "Role deleted" : v.kind === "retire" ? "Role retired — it stops working at once" : "Role restored"); if (v.kind === "delete") setSel("sys:hr_admin"); refresh(); },
    onError: (e) => toast.error(rbacError(e)),
  });
  if (cat.isLoading || roles.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (!cat.data || !roles.data) return <Empty>Roles could not be loaded. {rbacError(cat.error || roles.error, "")}</Empty>;
  const C = cat.data;
  const sysSel = sel.startsWith("sys:") ? roles.data.system.find((r) => `sys:${r.key}` === sel) : undefined;
  const cusSel = sel.startsWith("cus:") ? roles.data.custom.find((r) => `cus:${r.id}` === sel) : undefined;
  const shown: SystemRole | CustomRole | undefined = sysSel || cusSel;
  const perms = new Set(shown?.permissions || []);
  return (
    <div>
      <PageHeader title="Roles & Permissions" icon={ShieldCheck}
        subtitle={`System roles are fixed; custom roles narrow them. ${C.route_count} API routes each need a permission from this catalogue — the server refuses anything else.`}
        actions={canWrite ? <button className="btn-primary inline-flex items-center gap-1" onClick={() => setEditing({ base_role: "hr_manager", reach: "org", permissions: [] })}><Plus className="w-4 h-4" /> New custom role</button> : undefined} />
      <div className="flex gap-1 border-b border-surface-border mb-4">
        {([["roles", "Roles", ShieldCheck], ["people", "People & their roles", Users], ["history", "Change history", History]] as const).map(([k, l, I]) => (
          <button key={k} onClick={() => setTab(k)} className={`px-3 py-2 text-sm inline-flex items-center gap-1 border-b-2 -mb-px ${tab === k ? "border-primary-600 text-primary-700 font-semibold" : "border-transparent text-gray-500 hover:text-gray-700"}`}>
            <I className="w-4 h-4" />{l}</button>))}
      </div>

      {tab === "roles" && (
        <div className="grid xl:grid-cols-12 gap-4">
          <div className="xl:col-span-3 space-y-3">
            <div className="card !p-0 overflow-hidden">
              <div className="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-gray-500 bg-gray-50">System roles (read-only)</div>
              {roles.data.system.map((r) => (
                <button key={r.key} onClick={() => setSel(`sys:${r.key}`)} className={`w-full text-left px-3 py-2 border-t border-surface-border text-sm ${sel === `sys:${r.key}` ? "bg-primary-50" : "hover:bg-gray-50"}`}>
                  <div className="flex items-center justify-between gap-2"><span className="font-medium text-gray-800">{r.label}</span>
                    <span className="text-xs text-gray-400">{r.members ?? 0} <Users className="w-3 h-3 inline" /></span></div>
                  <div className="text-[11px] text-gray-500">{r.reach_label}{r.platform_only ? " · platform-managed" : ""}</div>
                </button>))}
            </div>
            <div className="card !p-0 overflow-hidden">
              <div className="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-gray-500 bg-gray-50">Custom roles ({roles.data.custom.length})</div>
              {roles.data.custom.map((r) => (
                <button key={r.id} onClick={() => setSel(`cus:${r.id}`)} className={`w-full text-left px-3 py-2 border-t border-surface-border text-sm ${sel === `cus:${r.id}` ? "bg-primary-50" : "hover:bg-gray-50"}`}>
                  <div className="flex items-center justify-between gap-2"><span className="font-medium text-gray-800">{r.name}</span>
                    {r.status === "retired" ? <span className="badge-amber">retired</span> : <span className="text-xs text-gray-400">{r.members ?? 0} <Users className="w-3 h-3 inline" /></span>}</div>
                  <div className="text-[11px] text-gray-500">{ROLE_LABEL[r.acting_role || ""] || r.acting_role} · {r.reach_label}</div>
                </button>))}
              {!roles.data.custom.length && <div className="px-3 py-4 text-xs text-gray-400 border-t border-surface-border">None yet. Clone a system role or press “New custom role”.</div>}
            </div>
          </div>
          <div className="xl:col-span-9">
            {shown && (
              <div className="card">
                <div className="flex flex-wrap items-start justify-between gap-3 mb-3">
                  <div>
                    <h2 className="font-display font-semibold text-lg text-gray-800">{"label" in shown ? shown.label : shown.name}
                      {sysSel && <span className="badge-teal ml-2 align-middle">system</span>}
                      {cusSel && <span className={`${cusSel.status === "active" ? "badge-green" : "badge-amber"} ml-2 align-middle`}>{cusSel.status}</span>}</h2>
                    <p className="text-sm text-gray-500">{sysSel ? sysSel.desc : cusSel?.description || "—"}</p>
                    <p className="text-xs text-gray-400 mt-1">
                      {sysSel ? `Data reach: ${sysSel.reach_label}` : `Built on ${ROLE_LABEL[cusSel!.base_role] || cusSel!.base_role} · acts as ${ROLE_LABEL[cusSel!.acting_role || ""] || cusSel!.acting_role} · ${cusSel!.reach_label} · v${cusSel!.version} · changed ${fmt(cusSel!.updated_at)} by ${cusSel!.updated_by || "—"}`}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {sysSel && canWrite && (sysSel.can_clone
                      ? <button className="btn-secondary inline-flex items-center gap-1" onClick={() => setEditing({ name: `${sysSel.label} (custom)`, base_role: sysSel.key, reach: sysSel.reach === "own" ? "own" : sysSel.reach, permissions: sysSel.permissions.filter((p) => !C.non_delegable.includes(p) && (me?.role === "superadmin" || (me?.permissions || []).includes(p))), cloned_from: sysSel.key })}><Copy className="w-4 h-4" /> Clone to custom role</button>
                      : <span className="text-xs text-gray-500 inline-flex items-center gap-1"><Lock className="w-3.5 h-3.5" /> Managed by the Lexora platform operator</span>)}
                    {cusSel && canWrite && <>
                      <button className="btn-secondary inline-flex items-center gap-1" onClick={() => setMembers(cusSel)}><Users className="w-4 h-4" /> People ({cusSel.members ?? 0})</button>
                      <button className="btn-secondary inline-flex items-center gap-1" onClick={() => setEditing(cusSel)}><Pencil className="w-4 h-4" /> Edit</button>
                      <button className="btn-secondary inline-flex items-center gap-1" onClick={() => setEditing({ ...cusSel, id: undefined, name: `${cusSel.name} (copy)`, cloned_from: cusSel.key })}><Copy className="w-4 h-4" /> Copy</button>
                      {cusSel.status === "active"
                        ? <button className="btn-secondary inline-flex items-center gap-1" onClick={() => window.confirm(`Retire "${cusSel.name}"? It stops working for its ${cusSel.members ?? 0} people at once (they keep their system role).`) && act.mutate({ kind: "retire", id: cusSel.id })}><Archive className="w-4 h-4" /> Retire</button>
                        : <button className="btn-secondary inline-flex items-center gap-1" onClick={() => act.mutate({ kind: "restore", id: cusSel.id })}><RotateCcw className="w-4 h-4" /> Restore</button>}
                      <button className="btn-secondary inline-flex items-center gap-1 text-red-600" title="Only a role that was never given to anyone"
                        onClick={() => window.confirm(`Delete "${cusSel.name}" for good?`) && act.mutate({ kind: "delete", id: cusSel.id })}><Trash2 className="w-4 h-4" /> Delete</button>
                    </>}
                  </div>
                </div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2">Sensitive data</h3>
                <div className="mb-4"><DataScopes cat={C} value={perms} /></div>
                <div className="flex items-center justify-between gap-2 mb-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-500">Modules × actions ({[...perms].filter((p) => !p.startsWith("data.")).length})</h3>
                  <div className="relative"><Search className="w-4 h-4 absolute left-2 top-2 text-gray-400" />
                    <input className="input !py-1 pl-8 text-xs w-56" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Find a module" /></div>
                </div>
                <Matrix cat={C} value={perms} filter={filter} />
                {sysSel && sysSel.reach !== "org" && <p className="text-xs text-gray-500 mt-2">{sysSel.label}: these actions apply to {sysSel.reach_label.toLowerCase()} only — the server narrows every answer.</p>}
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "people" && <PeopleTab cat={C} canWrite={canWrite} />}
      {tab === "history" && <HistoryTab />}

      <p className="text-xs text-gray-400 mt-4">
        The system role of a login (and creating, resetting or switching off a login) is set in{" "}
        <Link to="/users" className="text-primary-600 hover:text-primary-700">Setup → Users &amp; Logins</Link>. Org Admin logins are changed only by the Lexora platform operator.
      </p>

      {editing && <RoleEditor cat={C} initial={editing} onClose={() => setEditing(null)}
        onSaved={() => { setEditing(null); refresh(); }} />}
      {members && <Members role={members} onClose={() => { setMembers(null); refresh(); }} />}
    </div>
  );
}

function PeopleTab({ cat, canWrite }: { cat: Catalogue; canWrite: boolean }) {
  const [q, setQ] = useState("");
  const users = useQuery({ queryKey: ["rbac-users"], queryFn: () => rbacApi.users() });
  const [open, setOpen] = useState<RoleUser | null>(null);
  const [sp, setSp] = useSearchParams();
  const wanted = sp.get("user");
  useEffect(() => {        // deep link from Setup → Users & Logins ("Access")
    if (!wanted || !users.data) return;
    const u = users.data.items.find((x) => x.user_id === wanted);
    if (u && u.system_role !== "org_admin") setOpen(u);
    const n = new URLSearchParams(sp); n.delete("user"); setSp(n, { replace: true });
  }, [wanted, users.data]);  // eslint-disable-line react-hooks/exhaustive-deps
  const rows = (users.data?.items || []).filter((u) => !q || `${u.name} ${u.email} ${u.employee_number || ""} ${u.department || ""} ${u.system_role_label} ${u.custom_roles.map((c) => c.name).join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="card">
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="relative"><Search className="w-4 h-4 absolute left-2 top-2.5 text-gray-400" />
          <input className="input pl-8 w-80" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, e-mail, department, role" /></div>
        <span className="text-xs text-gray-500">{rows.length} logins</span>
      </div>
      {users.isLoading ? <Spinner /> : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead className="bg-gray-50"><tr>
              <th className="text-left px-2 py-1.5">Person</th><th className="text-left px-2">Emp. no.</th><th className="text-left px-2">Department</th>
              <th className="text-left px-2">System role</th><th className="text-left px-2">Custom roles</th><th className="text-left px-2">Status</th><th />
            </tr></thead>
            <tbody>{rows.map((u) => (
              <tr key={u.user_id} className="border-t border-surface-border hover:bg-gray-50">
                <td className="px-2 py-1"><div className="font-medium text-gray-800">{u.name}</div><div className="text-gray-400">{u.email}</div></td>
                <td className="px-2">{u.employee_number || "—"}</td><td className="px-2">{u.department || "—"}</td>
                <td className="px-2">{u.system_role_label}</td>
                <td className="px-2">{u.custom_roles.length ? u.custom_roles.map((c) => <span key={c.id} className={`${c.status === "active" ? "badge-purple" : "badge-amber"} mr-1`}>{c.name}</span>) : <span className="text-gray-300">—</span>}</td>
                <td className="px-2">{u.is_active ? "Active" : "Disabled"}</td>
                <td className="px-2 text-right whitespace-nowrap">
                  {u.system_role === "org_admin" ? <span className="text-gray-400" title="Changed only by the Lexora platform operator"><Lock className="w-3.5 h-3.5 inline" /></span>
                    : <button className="text-primary-600 hover:underline" onClick={() => setOpen(u)}>{canWrite ? "Roles & access" : "View access"}</button>}
                </td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
      {open && <UserRolesEditor cat={cat} user={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

const ACTION_TEXT: Record<string, string> = {
  "role.create": "Role created", "role.edit": "Role edited", "role.retire": "Role retired", "role.restore": "Role restored",
  "role.delete": "Role deleted", "user.assign": "Role given", "user.unassign": "Role taken away",
};

function HistoryTab() {
  const h = useQuery({ queryKey: ["rbac-history"], queryFn: () => rbacApi.history() });
  const [open, setOpen] = useState<string | null>(null);
  if (h.isLoading) return <Spinner />;
  return (
    <div className="card overflow-x-auto">
      <table className="min-w-full text-xs">
        <thead className="bg-gray-50"><tr><th className="text-left px-2 py-1.5">When</th><th className="text-left px-2">Who</th><th className="text-left px-2">What</th><th className="text-left px-2">Details</th></tr></thead>
        <tbody>{(h.data || []).map((c) => (
          <tr key={c.id} className="border-t border-surface-border align-top">
            <td className="px-2 py-1 whitespace-nowrap">{fmt(c.at)}</td>
            <td className="px-2">{c.actor_email}<div className="text-gray-400">{ROLE_LABEL[c.actor_role || ""] || c.actor_role}</div></td>
            <td className="px-2 whitespace-nowrap">{ACTION_TEXT[c.action] || c.action}</td>
            <td className="px-2">{c.summary}
              {(c.before || c.after) && <button className="ml-2 text-primary-600 hover:underline" onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? "hide" : "before / after"}</button>}
              {open === c.id && <pre className="mt-1 p-2 bg-gray-50 rounded text-[10px] whitespace-pre-wrap max-h-64 overflow-auto">{JSON.stringify({ before: c.before, after: c.after }, null, 2)}</pre>}
            </td>
          </tr>))}
          {!h.data?.length && <tr><td colSpan={4} className="px-2 py-6 text-center text-gray-400">No changes yet</td></tr>}
        </tbody>
      </table>
    </div>
  );
}
