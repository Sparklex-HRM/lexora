// Stream D4 — Settings → Legal entities: the group companies under this organisation.
// Each entity carries its own Canadian registrations (CRA Business Number, payroll program
// account RP, Revenu Québec number, WCB / WSIB / CNESST account per province, licences),
// payroll bank accounts (encrypted, masked here), the authorised signatory and a letterhead.
// T4 / RL-1 / ROE, remittances, direct-deposit files and letters use the entity of the
// employee, not the organisation.
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate } from "react-router-dom";
import toast from "react-hot-toast";
import { Building2, Plus, Pencil, Star, Eye, EyeOff, Trash2, Upload, Landmark, ShieldCheck, AlertTriangle, Users } from "lucide-react";
import { PageHeader, Spinner, Badge } from "../../components/ui";
import DataGrid, { GridColumn, WideModal } from "../../components/DataGrid";
import { useAuthStore, ADMIN_ROLES } from "../../store/authStore";
import { entitiesApi, type LegalEntity, type BankAccount, type WcbAccount, ENTITY_TYPE_LABEL, LICENCE_KIND_LABEL, WCB_BOARD } from "../../api/entities";
import { PROVINCE_NAMES } from "../../api/regions";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today
import { can } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const errText = (e: any, fb: string) => {
  const d = e?.response?.data?.detail;
  return typeof d === "string" ? d : d?.message || fb;
};

const PROVINCES: { code: string; name: string }[] = Object.entries(PROVINCE_NAMES)
  .map(([code, name]) => ({ code, name })).sort((a, b) => a.name.localeCompare(b.name));

// Canadian identifier shapes (format only — the backend re-checks).
const RE = {
  bn: /^[0-9]{9}$/,
  rp: /^[0-9]{9}RP[0-9]{4}$/,
  rq: /^[0-9]{10}(TQ[0-9]{4})?$/,
  postal: /^[ABCEGHJ-NPRSTVXY][0-9][ABCEGHJ-NPRSTV-Z] ?[0-9][ABCEGHJ-NPRSTV-Z][0-9]$/,
  institution: /^[0-9]{3}$/,
  transit: /^[0-9]{5}$/,
  account: /^[0-9]{7,12}$/,
};
const strip = (s: unknown) => String(s ?? "").replace(/[\s-]/g, "").toUpperCase();
const fmt = (re: RegExp, msg: string) => (s: unknown) => (!strip(s) || re.test(strip(s)) ? null : msg);
const chk = {
  bn: fmt(RE.bn, "Business Number is 9 digits"),
  rp: fmt(RE.rp, "Payroll account looks like 123456789RP0001"),
  rq: fmt(RE.rq, "Revenu Québec number is 10 digits (optionally TQ + 4 digits)"),
  postal: (s: unknown) => (!String(s ?? "").trim() || RE.postal.test(String(s).trim().toUpperCase()) ? null : "Postal code looks like A1A 1A1"),
  institution: fmt(RE.institution, "Institution number is 3 digits"),
  transit: fmt(RE.transit, "Transit number is 5 digits"),
  account: fmt(RE.account, "Account number is 7 to 12 digits"),
};

export default function LegalEntitiesPage() {
  const user = useAuthStore((s) => s.user);
  const qc = useQueryClient();
  const [editing, setEditing] = useState<LegalEntity | null>(null);
  const [creating, setCreating] = useState(false);
  const [showInactive, setShowInactive] = useState(false);
  const { data, isLoading } = useQuery({ queryKey: ["entities", "list", showInactive], queryFn: () => entitiesApi.list(showInactive) });
  const invalidate = () => { qc.invalidateQueries({ queryKey: ["entities"] }); };
  const makeDefault = useMutation({ mutationFn: entitiesApi.makeDefault, onSuccess: (e) => { toast.success(`${e.short_code} is now the default entity`); invalidate(); }, onError: (e: any) => toast.error(errText(e, "Could not change the default")) });
  const deactivate = useMutation({ mutationFn: entitiesApi.deactivate, onSuccess: () => { toast.success("Entity deactivated"); invalidate(); }, onError: (e: any) => toast.error(errText(e, "Could not deactivate")) });
  if (!can(user, "settings.admin", ADMIN_ROLES)) return <Navigate to="/settings" replace />;

  const rows = data?.entities || [];
  const columns: GridColumn<LegalEntity>[] = [
    { key: "short_code", label: "Code", className: "font-mono text-[12px]", render: (e) => <span className="font-mono text-[12px]">{e.short_code}{e.is_default && <Star className="inline w-3 h-3 ml-1 text-amber-500" aria-label="default" />}</span> },
    { key: "name", label: "Legal name", render: (e) => <div className="min-w-0"><div className="font-medium text-gray-800 truncate">{e.name}</div><div className="text-[11px] text-gray-400">{ENTITY_TYPE_LABEL[e.entity_type || ""] || e.entity_type}{e.city ? ` · ${e.city}` : ""}{e.province ? ` · ${e.province}` : ""}</div></div> },
    { key: "business_number", label: "Business Number", className: "font-mono text-[12px]" },
    { key: "payroll_account", label: "Payroll account (RP)", className: "font-mono text-[12px]" },
    { key: "rq_identification", label: "Revenu Québec no.", className: "font-mono text-[12px]" },
    { key: "wcb", label: "WCB provinces", text: (e) => Object.keys(e.wcb_accounts || {}).join(" "), render: (e) => Object.keys(e.wcb_accounts || {}).join(", ") || <span className="text-gray-300">—</span> },
    { key: "bank", label: "Payroll a/c", text: (e) => (e.bank_accounts || []).map((b) => b.account_masked || "").join(" "), render: (e) => { const p = (e.bank_accounts || []).find((b) => b.is_primary) || e.bank_accounts?.[0]; return p ? <span className="font-mono text-[12px]">{p.account_masked}<span className="text-gray-400"> {p.institution}-{p.transit}</span></span> : <span className="text-gray-300">—</span>; } },
    { key: "signatory", label: "Signatory", text: (e) => e.signatory?.name || "", render: (e) => e.signatory?.name ? <span>{e.signatory.name}<span className="text-gray-400 text-[11px]"> {e.signatory.designation}</span></span> : <span className="text-gray-300">—</span> },
    { key: "employee_count", label: "People", type: "number", className: "text-right tabular-nums" },
    { key: "gaps", label: "Missing", text: (e) => (e.registration_gaps || []).join(", "), render: (e) => e.registration_gaps?.length ? <span className="badge-amber" title={e.registration_gaps.join(", ")}>{e.registration_gaps.length} missing</span> : <span className="badge-green">complete</span> },
    { key: "is_active", label: "Status", text: (e) => (e.is_active ? "active" : "inactive"), render: (e) => <Badge tone={e.is_active ? "active" : "terminated"}>{e.is_active ? "active" : "inactive"}</Badge> },
    { key: "actions", label: "", sortable: false, text: () => "", render: (e) => (
      <div className="flex items-center gap-1 justify-end" onClick={(ev) => ev.stopPropagation()}>
        <button className="p-1 rounded hover:bg-gray-100 text-gray-500" title="Edit" onClick={() => setEditing(e)}><Pencil className="w-3.5 h-3.5" /></button>
        {!e.is_default && e.is_active && <button className="p-1 rounded hover:bg-gray-100 text-gray-500" title="Make default" onClick={() => makeDefault.mutate(e.id)}><Star className="w-3.5 h-3.5" /></button>}
        {!e.is_default && e.is_active && <button className="p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-600" title="Deactivate" onClick={() => { if (confirm(`Deactivate ${e.name}? People must be transferred first.`)) deactivate.mutate(e.id); }}><Trash2 className="w-3.5 h-3.5" /></button>}
      </div>) },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Legal entities" icon={Building2}
        subtitle="Group companies under this organisation. Each one remits source deductions to CRA under its own payroll account, files its own T4 / RL-1 and ROEs, reports to its own workers' compensation board and signs letters on its own letterhead."
        actions={<button className="btn-primary text-xs inline-flex items-center gap-1.5" onClick={() => setCreating(true)}><Plus className="w-3.5 h-3.5" /> New entity</button>} />
      <div className="card !p-0">
        {isLoading ? <div className="grid place-items-center py-10"><Spinner /></div> : (
          <DataGrid<LegalEntity> columns={columns} rows={rows} rowKey={(e) => e.id} exportName="legal-entities" minWidth={1200}
            density="compact" onRowClick={(e) => setEditing(e)} initialSort={{ key: "name", dir: "asc" }}
            filters={<label className="inline-flex items-center gap-1.5 text-xs text-gray-600"><input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} /> show inactive</label>}
            empty="No legal entity yet — the default one is created from your company profile on first start." />
        )}
        <p className="px-4 py-2 text-[11px] text-gray-400 border-t border-surface-border/70">
          The default entity is where new employees go unless another is chosen. Moving a person between entities is a dated transfer on their profile (the old entity issues the ROE when employment with it ends). Bank account numbers are stored encrypted and shown masked.
        </p>
      </div>
      {(editing || creating) && <EntityModal entity={editing} onClose={() => { setEditing(null); setCreating(false); invalidate(); }} />}
    </div>
  );
}

// ── editor ───────────────────────────────────────────────────────────────────
type WcbRow = { province: string } & WcbAccount;
type Form = Partial<LegalEntity> & { wcb_rows?: WcbRow[] };

function toForm(e: LegalEntity | null): Form {
  if (!e) return { name: "", short_code: "", entity_type: "corporation", country: "CA", licences: [], signatory: {}, letterhead: {}, wcb_rows: [] };
  return { ...e, wcb_rows: Object.entries(e.wcb_accounts || {}).map(([province, w]) => ({ province, ...w })) };
}

function Field({ label, children, hint, wide, req, err }: { label: string; children: React.ReactNode; hint?: string; wide?: boolean; req?: boolean; err?: string | null }) {
  return (
    <label className={`block ${wide ? "sm:col-span-2" : ""}`}>
      <span className="block text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-0.5">{label}{req && <Req />}</span>
      {children}
      <FieldError msg={err} />
      {hint && <span className="block text-[11px] text-gray-500 mt-0.5">{hint}</span>}
    </label>
  );
}

const todayIso = () => todayISO();

/** Format checks for the entity editor (format only — the backend re-checks). */
function entityErrors(f: Form, isEdit: boolean): Record<string, string> {
  const bn = strip(f.business_number);
  const rp = strip(f.payroll_account);
  const out = collect({
    name: v.required(f.name, "Legal name") || v.maxLen(f.name, 200, "Legal name"),
    short_code: isEdit ? v.required(f.short_code, "Short code") : null,
    business_number: chk.bn(f.business_number),
    payroll_account: chk.rp(f.payroll_account) || (bn && rp && !rp.startsWith(bn) ? "The payroll account must start with the entity's Business Number" : null),
    rq_identification: chk.rq(f.rq_identification),
    postal_code: chk.postal(f.postal_code),
    email: v.email(f.email),
    phone: v.maxLen(f.phone, 20, "Phone"),
    website: v.maxLen(f.website, 200, "Website"),
    sig_email: v.email(f.signatory?.email),
    sig_phone: v.maxLen(f.signatory?.phone, 20, "Phone"),
    header_text: v.maxLen(f.letterhead?.header_text, 400, "Header line"),
    footer_text: v.maxLen(f.letterhead?.footer_text, 400, "Footer line"),
  });
  const wcb = f.wcb_rows || [];
  wcb.forEach((r, i) => {
    if (!r.province && (r.account_no || r.rate_group)) out[`wcb_${i}`] = "Pick the province for this workers' compensation account";
    else if (r.province && wcb.findIndex((x) => x.province === r.province) < i) out[`wcb_${i}`] = "This province is already listed";
  });
  return out;
}

function Section({ title, icon: Icon, children }: { title: string; icon: any; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-surface-border p-3">
      <div className="flex items-center gap-2 mb-2 text-[12px] font-semibold text-gray-700"><Icon className="w-3.5 h-3.5 text-primary-500" />{title}</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-2">{children}</div>
    </div>
  );
}

function EntityModal({ entity, onClose }: { entity: LegalEntity | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Form>(() => toForm(entity));
  const [id, setId] = useState<string | null>(entity?.id || null);
  const [banks, setBanks] = useState<BankAccount[]>(entity?.bank_accounts || []);
  const [revealed, setRevealed] = useState<BankAccount[] | null>(null);
  const [newBank, setNewBank] = useState({ label: "Payroll account", bank_name: "", institution: "", transit: "", account_no: "", account_type: "chequing", is_primary: !(entity?.bank_accounts || []).length });
  const [tried, setTried] = useState(false);
  const errs = useMemo(() => entityErrors(f, !!id), [f, id]);
  const shown = (k: string) => (tried || !["name", "short_code"].includes(k) ? errs[k] : null);
  const bankErrs = collect({ institution: chk.institution(newBank.institution), transit: chk.transit(newBank.transit), account_no: chk.account(newBank.account_no), label: v.maxLen(newBank.label, 60, "Label") });
  const logo = useQuery({ queryKey: ["entities", "logo", id], queryFn: () => entitiesApi.logo(id!), enabled: !!id && !!f.has_logo, staleTime: 60_000 });
  const set = (k: keyof Form, v: any) => setF((p) => ({ ...p, [k]: v }));
  const setSig = (k: string, v: string) => setF((p) => ({ ...p, signatory: { ...(p.signatory || {}), [k]: v } }));
  const setLh = (k: string, v: string) => setF((p) => ({ ...p, letterhead: { ...(p.letterhead || {}), [k]: v } }));
  const setWcb = (i: number, patch: Partial<WcbRow>) => { const a = [...(f.wcb_rows || [])]; a[i] = { ...a[i], ...patch }; set("wcb_rows", a); };
  const body = useMemo(() => {
    const wcb: Record<string, WcbAccount> = {};
    (f.wcb_rows || []).forEach(({ province, ...w }) => { if (province) wcb[province] = { ...w, board: w.board || WCB_BOARD[province] }; });
    const { wcb_rows, bank_accounts, registration_gaps, employee_count, has_logo, logo_needs_reupload: _lr, id: _i, org_id, is_default, is_active, created_at, updated_at, updated_by, ...rest } = f as any; // eslint-disable-line
    return { ...rest, wcb_accounts: wcb, letterhead: { header_text: f.letterhead?.header_text, footer_text: f.letterhead?.footer_text } };
  }, [f]);
  const save = useMutation({
    mutationFn: () => (id ? entitiesApi.update(id, body) : entitiesApi.create(body)),
    onSuccess: (e) => { toast.success(id ? "Entity saved" : "Entity created"); setId(e.id); setF(toForm(e)); setBanks(e.bank_accounts || []); qc.invalidateQueries({ queryKey: ["entities"] }); },
    onError: (e: any) => toast.error(errText(e, "Could not save")),
  });
  const addBank = useMutation({
    mutationFn: () => entitiesApi.addBank(id!, newBank),
    onSuccess: (r) => { setBanks(r.bank_accounts); setRevealed(null); setNewBank({ ...newBank, institution: "", transit: "", account_no: "", is_primary: false }); toast.success("Bank account added (stored encrypted)"); },
    onError: (e: any) => toast.error(errText(e, "Could not add the account")),
  });
  const removeBank = useMutation({ mutationFn: (aid: string) => entitiesApi.removeBank(id!, aid), onSuccess: (r) => { setBanks(r.bank_accounts); setRevealed(null); } });
  const reveal = useMutation({ mutationFn: () => entitiesApi.revealBank(id!), onSuccess: (r) => setRevealed(r.bank_accounts), onError: (e: any) => toast.error(errText(e, "Only an org admin can reveal")) });
  const upload = useMutation({ mutationFn: (file: File) => entitiesApi.uploadLogo(id!, file), onSuccess: () => { toast.success("Logo uploaded"); set("has_logo", true); set("logo_needs_reupload", false); qc.invalidateQueries({ queryKey: ["entities", "logo", id] }); }, onError: (e: any) => toast.error(errText(e, "Upload failed")) });
  const trySave = () => {
    setTried(true);
    if (Object.keys(errs).length) { toast.error(firstError(errs)); return; }
    save.mutate();
  };
  const tryAddBank = () => {
    if (Object.keys(bankErrs).length) { toast.error(firstError(bankErrs)); return; }
    addBank.mutate();
  };
  const digits = (s: string, n: number) => s.replace(/\D/g, "").slice(0, n);

  const title = id ? `${f.short_code} · ${f.name}` : "New legal entity";
  return (
    <WideModal title={title} onClose={onClose}
      footer={<>
        <button className="btn-secondary text-sm" onClick={onClose}>Close</button>
        <button className="btn-primary text-sm" disabled={save.isPending} onClick={trySave}>{save.isPending ? "Saving…" : id ? "Save changes" : "Create entity"}</button>
      </>}>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <Section title="Identity" icon={Building2}>
          <Field label="Legal name" wide req err={shown("name")}><input className="input" maxLength={200} value={f.name || ""} onChange={(e) => set("name", e.target.value)} placeholder="As on the articles of incorporation" /></Field>
          <Field label="Short code" req={!!id} err={shown("short_code")} hint={id ? "Used in file names and the entity picker" : "Used in file names and the entity picker. Left blank, it is made from the name."}><input className="input uppercase" maxLength={12} value={f.short_code || ""} onChange={(e) => set("short_code", e.target.value.toUpperCase().replace(/\s/g, ""))} placeholder="e.g. MAPLE" /></Field>
          <Field label="Type"><select className="input" value={f.entity_type || "corporation"} onChange={(e) => set("entity_type", e.target.value)}>{Object.entries(ENTITY_TYPE_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
          <Field label="Numbering prefix" hint="Optional — prefixes employee / letter numbers of this entity, e.g. MAPLE-0001"><input className="input uppercase" maxLength={12} value={f.numbering_prefix || ""} onChange={(e) => set("numbering_prefix", e.target.value.toUpperCase().replace(/\s/g, ""))} /></Field>
        </Section>

        <Section title="Registered office" icon={Landmark}>
          <Field label="Address line 1" wide><input className="input" maxLength={200} value={f.address_line1 || ""} onChange={(e) => set("address_line1", e.target.value)} placeholder="Street address" /></Field>
          <Field label="Address line 2" wide><input className="input" maxLength={200} value={f.address_line2 || ""} onChange={(e) => set("address_line2", e.target.value)} placeholder="Suite, unit" /></Field>
          <Field label="City"><input className="input" maxLength={100} value={f.city || ""} onChange={(e) => set("city", e.target.value)} /></Field>
          <Field label="Province / territory" hint="Also the default province for new licences"><select className="input" value={f.province || ""} onChange={(e) => set("province", e.target.value)}><option value="">—</option>{PROVINCES.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}</select></Field>
          <Field label="Postal code" err={errs.postal_code}><input className="input font-mono uppercase" maxLength={7} value={f.postal_code || ""} onChange={(e) => set("postal_code", e.target.value.toUpperCase())} placeholder="A1A 1A1" /></Field>
          <Field label="E-mail" err={errs.email}><input className="input" type="email" maxLength={200} value={f.email || ""} onChange={(e) => set("email", e.target.value.trim())} placeholder="hr@company.ca" /></Field>
          <Field label="Phone" err={errs.phone}><input className="input" type="tel" maxLength={20} value={f.phone || ""} onChange={(e) => set("phone", e.target.value)} placeholder="416-555-0100" /></Field>
          <Field label="Website" err={errs.website}><input className="input" type="url" maxLength={200} value={f.website || ""} onChange={(e) => set("website", e.target.value.trim())} placeholder="https://" /></Field>
        </Section>

        <Section title="Registrations" icon={ShieldCheck}>
          <Field label="CRA Business Number (BN)" err={errs.business_number} hint="9 digits"><input className="input font-mono" inputMode="numeric" maxLength={9} value={f.business_number || ""} onChange={(e) => set("business_number", digits(e.target.value, 9))} placeholder="123456789" /></Field>
          <Field label="CRA payroll account (RP)" err={errs.payroll_account} hint="BN + RP + 4 digits — used for PD7A remittances and T4"><input className="input font-mono uppercase" maxLength={15} value={f.payroll_account || ""} onChange={(e) => set("payroll_account", e.target.value.toUpperCase().replace(/[\s-]/g, ""))} placeholder="123456789RP0001" /></Field>
          <Field label="Revenu Québec identification no." err={errs.rq_identification} hint="Québec employers only — RL-1 and source deductions"><input className="input font-mono uppercase" maxLength={16} value={f.rq_identification || ""} onChange={(e) => set("rq_identification", e.target.value.toUpperCase().replace(/[\s-]/g, ""))} placeholder="1234567890TQ0001" /></Field>
          <div className="sm:col-span-2">
            <div className="flex items-center justify-between mb-1"><span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Workers' compensation (per province)</span><button className="text-[11px] text-primary-600" onClick={() => set("wcb_rows", [...(f.wcb_rows || []), { province: f.province || "", board: "", account_no: "", rate_group: "" }])}>+ add province</button></div>
            {(f.wcb_rows || []).length > 0 && <div className="hidden sm:grid grid-cols-[90px_1fr_1fr_1fr_24px] gap-1.5 text-[10px] uppercase text-gray-400 mb-0.5"><span>Province</span><span>Board</span><span>Account no.</span><span>Rate group / class</span><span /></div>}
            {(f.wcb_rows || []).map((r, i) => (
              <div key={i} className="mb-1">
                <div className="grid grid-cols-2 sm:grid-cols-[90px_1fr_1fr_1fr_24px] gap-1.5 items-center">
                  <select className="input !px-1" aria-label="Province" value={r.province} onChange={(e) => setWcb(i, { province: e.target.value })}><option value="">province</option>{PROVINCES.map((s) => <option key={s.code} value={s.code}>{s.code}</option>)}</select>
                  <input className="input" maxLength={60} placeholder={WCB_BOARD[r.province] || "Board"} value={r.board || ""} onChange={(e) => setWcb(i, { board: e.target.value })} />
                  <input className="input font-mono" maxLength={60} placeholder="Account number" value={r.account_no || ""} onChange={(e) => setWcb(i, { account_no: e.target.value })} />
                  <input className="input font-mono" maxLength={60} placeholder="Rate group" value={r.rate_group || ""} onChange={(e) => setWcb(i, { rate_group: e.target.value })} />
                  <button className="text-gray-400 hover:text-red-600 justify-self-start" aria-label="Remove province" onClick={() => set("wcb_rows", (f.wcb_rows || []).filter((_, j) => j !== i))}><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
                <FieldError msg={errs[`wcb_${i}`]} />
              </div>))}
          </div>
          <div className="sm:col-span-2">
            <div className="flex items-center justify-between mb-1"><span className="text-[11px] font-semibold text-gray-500 uppercase tracking-wide">Licences (business licence, extra-provincial registration …)</span><button className="text-[11px] text-primary-600" onClick={() => set("licences", [...(f.licences || []), { kind: "business_licence", number: "", province: f.province || "", valid_to: "", label: "" }])}>+ add</button></div>
            {(f.licences || []).length > 0 && <div className="hidden sm:grid grid-cols-[150px_1fr_80px_130px_24px] gap-1.5 text-[10px] uppercase text-gray-400 mb-0.5"><span>Kind</span><span>Number</span><span>Province</span><span>Valid until</span><span /></div>}
            {(f.licences || []).map((l, i) => {
              const expired = !!l.valid_to && l.valid_to < todayIso();
              const upd = (patch: Partial<typeof l>) => { const a = [...(f.licences || [])]; a[i] = { ...a[i], ...patch }; set("licences", a); };
              return (
              <div key={i} className="mb-1.5">
                <div className="grid grid-cols-2 sm:grid-cols-[150px_1fr_80px_130px_24px] gap-1.5 items-center">
                  <select className="input !px-1" aria-label="Kind" value={l.kind} onChange={(e) => upd({ kind: e.target.value })}>{Object.entries(LICENCE_KIND_LABEL).map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</select>
                  <input className="input font-mono" maxLength={60} placeholder="Licence / registration number" value={l.number} onChange={(e) => upd({ number: e.target.value })} />
                  <select className="input !px-1" aria-label="Province" value={l.province || ""} onChange={(e) => upd({ province: e.target.value })}><option value="">prov.</option>{PROVINCES.map((s) => <option key={s.code} value={s.code}>{s.code}</option>)}</select>
                  <input className="input" type="date" aria-label="Valid until" value={l.valid_to || ""} onChange={(e) => upd({ valid_to: e.target.value })} />
                  <button className="text-gray-400 hover:text-red-600 justify-self-start" aria-label="Remove licence" onClick={() => set("licences", (f.licences || []).filter((_, j) => j !== i))}><Trash2 className="w-3.5 h-3.5" /></button>
                  <input className="input col-span-2 sm:col-span-4" maxLength={80} placeholder="Label — e.g. the premises or unit it covers (optional)" value={l.label || ""} onChange={(e) => upd({ label: e.target.value })} />
                </div>
                {expired && <span className="block text-[11px] text-amber-700 mt-0.5">This licence expired on {l.valid_to} — renew it and update the date.</span>}
              </div>); })}
            {(f.licences || []).length > 0 && <p className="text-[11px] text-gray-500">Leave the number blank to drop a row on save.</p>}
          </div>
        </Section>

        <div className="space-y-3">
          <Section title="Authorised signatory" icon={Users}>
            <Field label="Name" hint="Signs letters, T4 / RL-1 summaries and ROEs"><input className="input" maxLength={160} value={f.signatory?.name || ""} onChange={(e) => setSig("name", e.target.value)} /></Field>
            <Field label="Title"><input className="input" maxLength={160} value={f.signatory?.designation || ""} onChange={(e) => setSig("designation", e.target.value)} placeholder="Director / CFO" /></Field>
            <Field label="E-mail" err={errs.sig_email}><input className="input" type="email" maxLength={160} value={f.signatory?.email || ""} onChange={(e) => setSig("email", e.target.value.trim())} /></Field>
            <Field label="Phone" err={errs.sig_phone}><input className="input" type="tel" maxLength={20} value={f.signatory?.phone || ""} onChange={(e) => setSig("phone", e.target.value)} placeholder="416-555-0100" /></Field>
          </Section>

          <Section title="Letterhead" icon={Upload}>
            <Field label="Header line" wide err={errs.header_text}><input className="input" maxLength={400} value={f.letterhead?.header_text || ""} onChange={(e) => setLh("header_text", e.target.value)} placeholder="Legal name · Business Number · registered office" /></Field>
            <Field label="Footer line" wide err={errs.footer_text}><input className="input" maxLength={400} value={f.letterhead?.footer_text || ""} onChange={(e) => setLh("footer_text", e.target.value)} placeholder="e-mail · phone · website" /></Field>
            <div className="sm:col-span-2 flex flex-wrap items-center gap-2 text-[12px]">
              {id && f.has_logo && logo.data?.logo_data_url && <img src={logo.data.logo_data_url} alt="Current logo" data-testid="logo-preview" className="h-10 max-w-[160px] object-contain border border-surface-border rounded bg-white p-0.5" />}
              {id ? (<>
                <label className="btn-secondary text-xs inline-flex items-center gap-1.5 cursor-pointer"><Upload className="w-3.5 h-3.5" /> {f.has_logo ? "Replace logo" : "Upload logo"}
                  <input type="file" accept="image/png,image/jpeg" className="hidden" onChange={(e) => { const file = e.target.files?.[0]; if (file) upload.mutate(file); }} /></label>
                {f.has_logo && <button className="text-[11px] text-red-600" onClick={() => entitiesApi.removeLogo(id).then(() => set("has_logo", false))}>remove logo</button>}
                <span className="text-gray-400">PNG / JPEG, up to 300 KB</span>
                {f.logo_needs_reupload && <span className="text-amber-700" data-testid="logo-reupload">The saved logo is an SVG, which is no longer accepted. Upload it again as PNG or JPEG.</span>}
              </>) : <span className="text-gray-400">Save the entity first to upload a logo.</span>}
            </div>
          </Section>
        </div>

        <div className="lg:col-span-2 rounded-xl border border-surface-border p-3">
          <div className="flex items-center justify-between gap-2 mb-2">
            <div className="flex items-center gap-2 text-[12px] font-semibold text-gray-700"><Landmark className="w-3.5 h-3.5 text-primary-500" />Payroll bank accounts <span className="text-gray-400 font-normal">(encrypted at rest · the primary account funds direct deposits)</span></div>
            {id && banks.length > 0 && (revealed
              ? <button className="text-[11px] text-gray-500 inline-flex items-center gap-1" onClick={() => setRevealed(null)}><EyeOff className="w-3.5 h-3.5" /> hide numbers</button>
              : <button className="text-[11px] text-primary-600 inline-flex items-center gap-1" onClick={() => reveal.mutate()}><Eye className="w-3.5 h-3.5" /> reveal numbers (audited)</button>)}
          </div>
          {!id ? <p className="text-[12px] text-gray-400">Save the entity first, then add its bank accounts.</p> : (
            <>
              <table className="w-full text-[12px] mb-2">
                <thead className="text-[10px] uppercase text-gray-400"><tr><th className="text-left py-1">Label</th><th className="text-left">Bank</th><th className="text-left">Institution</th><th className="text-left">Transit</th><th className="text-left">Account</th><th /></tr></thead>
                <tbody>
                  {banks.map((b) => { const r = revealed?.find((x) => x.id === b.id); return (
                    <tr key={b.id} className="border-t border-surface-border/70">
                      <td className="py-1">{b.label}{b.is_primary && <span className="badge-teal ml-1">primary</span>}</td><td>{b.bank_name}</td>
                      <td className="font-mono">{b.institution}</td><td className="font-mono">{b.transit}</td>
                      <td className="font-mono">{r?.account_no || b.account_masked}{b.account_type && <span className="text-gray-400 font-sans"> · {b.account_type}</span>}</td>
                      <td className="text-right"><button className="text-gray-400 hover:text-red-600" onClick={() => removeBank.mutate(b.id)}><Trash2 className="w-3.5 h-3.5" /></button></td>
                    </tr>); })}
                  {banks.length === 0 && <tr><td colSpan={6} className="py-2 text-gray-400">No account yet — the organisation's payroll bank account is used until one is added.</td></tr>}
                </tbody>
              </table>
              <div className="grid grid-cols-1 sm:grid-cols-3 lg:grid-cols-4 gap-x-3 gap-y-2 items-start">
                <Field label="Label" err={newBank.label ? bankErrs.label : null}><input className="input" maxLength={60} value={newBank.label} onChange={(e) => setNewBank({ ...newBank, label: e.target.value })} /></Field>
                <Field label="Bank"><input className="input" maxLength={80} value={newBank.bank_name} onChange={(e) => setNewBank({ ...newBank, bank_name: e.target.value })} placeholder="Royal Bank of Canada" /></Field>
                <Field label="Account type"><select className="input" value={newBank.account_type} onChange={(e) => setNewBank({ ...newBank, account_type: e.target.value })}><option value="chequing">Chequing</option><option value="savings">Savings</option></select></Field>
                <Field label="Institution no." req err={newBank.institution ? bankErrs.institution : null}><input className="input font-mono" inputMode="numeric" maxLength={3} value={newBank.institution} onChange={(e) => setNewBank({ ...newBank, institution: digits(e.target.value, 3) })} placeholder="003" /></Field>
                <Field label="Transit no." req err={newBank.transit ? bankErrs.transit : null}><input className="input font-mono" inputMode="numeric" maxLength={5} value={newBank.transit} onChange={(e) => setNewBank({ ...newBank, transit: digits(e.target.value, 5) })} placeholder="12345" /></Field>
                <Field label="Account number" req err={newBank.account_no ? bankErrs.account_no : null}><input className="input font-mono" inputMode="numeric" maxLength={12} value={newBank.account_no} onChange={(e) => setNewBank({ ...newBank, account_no: digits(e.target.value, 12) })} placeholder="7 to 12 digits" /></Field>
                <label className="inline-flex items-center gap-1.5 text-[12px] text-gray-600 sm:mt-5"><input type="checkbox" checked={newBank.is_primary} onChange={(e) => setNewBank({ ...newBank, is_primary: e.target.checked })} /> Primary — funds direct deposits</label>
                <button className="btn-secondary text-xs h-9 sm:mt-4" disabled={!newBank.institution || !newBank.transit || !newBank.account_no || addBank.isPending} onClick={tryAddBank}>{addBank.isPending ? "Adding…" : "Add account"}</button>
              </div>
            </>
          )}
        </div>

        {f.registration_gaps && f.registration_gaps.length > 0 && (
          <div className="lg:col-span-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800 inline-flex items-start gap-2"><AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" /><span>Still missing for CRA / Revenu Québec filings: {f.registration_gaps.join(", ")}. Slips and remittances for this entity will print blanks until these are entered.</span></div>
        )}
      </div>
    </WideModal>
  );
}
