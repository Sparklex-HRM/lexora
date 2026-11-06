import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Ban, Plus } from "lucide-react";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Spinner } from "../../components/ui";
import { ScimLogRow, ScimToken, SsoConfig, errText, scimApi, ssoApi, when } from "../../api/integrations";
import { LABEL, SecretBox, Section, Status } from "./shared";

export function SsoTab() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["integrations", "sso"], queryFn: ssoApi.get });
  const [f, setF] = useState<Record<string, any>>({});
  const v = (k: keyof SsoConfig | "client_secret" | "idp_cert_pem") => (k in f ? f[k] : (data as any)?.[k] ?? "");
  const set = (k: string, val: any) => setF({ ...f, [k]: val });
  const save = useMutation({ mutationFn: () => ssoApi.save(f as any),
    onSuccess: (r) => { toast.success(r.note || "Saved"); setF({}); qc.invalidateQueries({ queryKey: ["integrations"] }); },
    onError: (e: any) => toast.error(errText(e, "Could not save")) });
  const test = useMutation({ mutationFn: ssoApi.test, onSuccess: (r) => (r.ok ? toast.success : toast.error)(r.message),
    onError: (e: any) => toast.error(errText(e, "The test could not run")) });
  if (!data) return <div className="grid place-items-center py-16"><Spinner /></div>;
  const proto = v("protocol") || "oidc";
  const configured = data.enabled && data.missing.length === 0;
  return (
    <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
      <div className="xl:col-span-2 card">
        <div className="flex items-center justify-between mb-3">
          <div><h2 className="font-display font-semibold text-gray-800">Single sign-on</h2>
            <p className="text-xs text-gray-500">People sign in with your identity provider. Lexora HRM issues its own session afterwards, exactly as today.</p></div>
          <Status s={configured ? "configured" : "not_configured"} />
        </div>
        {data.missing.length > 0 && <p className="text-xs text-amber-700 mb-3">Missing: {data.missing.join(", ")}</p>}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div><label className={LABEL}>Protocol</label>
            <select className="input" value={proto} onChange={(e) => set("protocol", e.target.value)}>
              {Object.entries(data.protocols).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></div>
          <div><label className={LABEL}>Button text ("Sign in with …")</label>
            <input className="input" value={v("display_name")} placeholder="e.g. Okta, Microsoft, Google" onChange={(e) => set("display_name", e.target.value)} /></div>
          {proto === "oidc" ? (<>
            <div className="md:col-span-2"><label className={LABEL}>Discovery URL (issuer)</label>
              <input className="input" value={v("discovery_url")} placeholder="https://login.microsoftonline.com/<tenant>/v2.0" onChange={(e) => set("discovery_url", e.target.value)} /></div>
            <div><label className={LABEL}>Client id</label><input className="input" value={v("client_id")} onChange={(e) => set("client_id", e.target.value)} /></div>
            <div><label className={LABEL}>Client secret {data.client_secret_set && !("client_secret" in f) ? "(saved — type to replace)" : ""}</label>
              <input className="input" type="password" value={v("client_secret")} onChange={(e) => set("client_secret", e.target.value)} /></div>
          </>) : (<>
            <div><label className={LABEL}>IdP entity id (issuer)</label><input className="input" value={v("idp_entity_id")} onChange={(e) => set("idp_entity_id", e.target.value)} /></div>
            <div><label className={LABEL}>IdP sign-in URL (HTTP-Redirect)</label><input className="input" value={v("idp_sso_url")} onChange={(e) => set("idp_sso_url", e.target.value)} /></div>
            <div className="md:col-span-2"><label className={LABEL}>IdP signing certificate {data.idp_cert_set && !("idp_cert_pem" in f) ? "(saved — paste to replace)" : ""}</label>
              <textarea className="input font-mono text-xs h-24" value={v("idp_cert_pem")} placeholder="-----BEGIN CERTIFICATE----- … or the base64 body from the IdP metadata" onChange={(e) => set("idp_cert_pem", e.target.value)} /></div>
          </>)}
          <div><label className={LABEL}>Attribute → e-mail</label><input className="input" value={v("attr_email")} placeholder="email (OIDC) · emailaddress (SAML)" onChange={(e) => set("attr_email", e.target.value)} /></div>
          <div><label className={LABEL}>Attribute → employee number (optional)</label><input className="input" value={v("attr_employee_number")} placeholder="employeeNumber" onChange={(e) => set("attr_employee_number", e.target.value)} /></div>
          <label className="flex items-start gap-2 text-sm text-gray-700 cursor-pointer p-3 rounded-lg border border-surface-border">
            <input type="checkbox" className="mt-0.5" checked={!!v("jit_enabled")} onChange={(e) => set("jit_enabled", e.target.checked)} />
            <span><b>Create the login on first sign-in</b><br /><span className="text-gray-500 text-xs">Only when the e-mail matches an employee or you accept outside users. Never creates an admin.</span></span>
          </label>
          <div><label className={LABEL}>Default role for new logins</label>
            <select className="input" value={v("jit_default_role") || "employee"} onChange={(e) => set("jit_default_role", e.target.value)}>
              {data.roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
            </select></div>
          <label className="flex items-start gap-2 text-sm text-gray-700 cursor-pointer p-3 rounded-lg border border-surface-border">
            <input type="checkbox" className="mt-0.5" checked={!!v("enabled")} onChange={(e) => set("enabled", e.target.checked)} />
            <span><b>Switched on</b><br /><span className="text-gray-500 text-xs">Shows the "Sign in with" button for your organization code.</span></span>
          </label>
          <label className="flex items-start gap-2 text-sm text-gray-700 cursor-pointer p-3 rounded-lg border border-red-200 bg-red-50/40">
            <input type="checkbox" className="mt-0.5" checked={!!v("required")} onChange={(e) => set("required", e.target.checked)} />
            <span><b>SSO required</b><br /><span className="text-gray-500 text-xs">Password sign-in is refused for everyone except admins (break-glass).</span></span>
          </label>
        </div>
        <div className="flex justify-end gap-2 mt-4">
          <button className="btn-secondary text-xs" disabled={test.isPending} onClick={() => test.mutate()}>Test connection</button>
          <button className="btn-primary text-xs disabled:opacity-60" disabled={!Object.keys(f).length || save.isPending} onClick={() => save.mutate()}>Save</button>
        </div>
      </div>
      <div className="card text-xs space-y-3">
        <h3 className="font-semibold text-gray-800">Give these to your identity provider</h3>
        <div><div className={LABEL}>OIDC redirect URI</div><code className="block break-all text-[11px] bg-surface p-2 rounded">{data.oidc_redirect_uri}</code></div>
        <div><div className={LABEL}>SAML ACS URL (HTTP-POST)</div><code className="block break-all text-[11px] bg-surface p-2 rounded">{data.saml_acs_url}</code></div>
        <div><div className={LABEL}>SAML SP entity id / Audience</div><code className="block break-all text-[11px] bg-surface p-2 rounded">{data.saml_sp_entity_id}</code></div>
        <div><div className={LABEL}>SAML SP metadata</div><a className="text-primary-600 break-all" href={data.saml_metadata_url} target="_blank" rel="noreferrer">{data.saml_metadata_url}</a></div>
        <p className="text-gray-500">Scopes requested: openid, email, profile. The IdP must send the e-mail that matches the person's Lexora HRM login (or the employee number attribute).</p>
        <p className="text-gray-400">Last change: {when(data.updated_at)} {data.updated_by ? `by ${data.updated_by}` : ""}</p>
      </div>
    </div>
  );
}

export function ScimTab() {
  const qc = useQueryClient();
  const { data: toks } = useQuery({ queryKey: ["integrations", "scim", "tokens"], queryFn: scimApi.tokens });
  const { data: log } = useQuery({ queryKey: ["integrations", "scim", "log"], queryFn: scimApi.log });
  const [secret, setSecret] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["integrations"] });
  const create = useMutation({ mutationFn: () => scimApi.create(prompt("Name for this token (e.g. Okta)") || "SCIM token"),
    onSuccess: (r) => { setSecret(r.token); refresh(); }, onError: (e: any) => toast.error(errText(e, "Could not create the token")) });
  const revoke = useMutation({ mutationFn: (id: string) => scimApi.revoke(id), onSuccess: () => { toast.success("Token revoked"); refresh(); } });
  const tcols: GridColumn<ScimToken>[] = [
    { key: "label", label: "Name" }, { key: "status", label: "Status", render: (t) => <Status s={t.status} /> },
    { key: "created_at", label: "Created", text: (t) => t.created_at || "", render: (t) => when(t.created_at) },
    { key: "created_by", label: "By" },
    { key: "last_used_at", label: "Last used", text: (t) => t.last_used_at || "", render: (t) => when(t.last_used_at) },
    { key: "a", label: "", sortable: false, text: () => "", render: (t) => t.status === "active"
      ? <button className="text-xs text-red-600 inline-flex items-center gap-1" onClick={() => revoke.mutate(t.id)}><Ban className="w-3 h-3" /> Revoke</button> : null },
  ];
  const lcols: GridColumn<ScimLogRow>[] = [
    { key: "at", label: "When", text: (r) => r.at || "", render: (r) => when(r.at) },
    { key: "method", label: "Call", text: (r) => `${r.method} ${r.path}`, render: (r) => <span className="font-mono text-[11px]">{r.method} {r.path.replace(/^\/auth\/scim\/v2/, "")}</span> },
    { key: "user_name", label: "User" }, { key: "external_id", label: "External id" },
    { key: "outcome", label: "Outcome", render: (r) => <span className={r.outcome === "rejected" || r.outcome === "error" ? "badge-red" : "badge-green"}>{r.outcome}</span> },
    { key: "status_code", label: "HTTP", type: "number" }, { key: "message", label: "Message" },
  ];
  return (
    <div>
      <Section title="SCIM 2.0 provisioning" sub={`Base URL for your IdP: ${toks?.base_url || "…"} · Users and Groups (groups = roles) · bearer token per organization`}
        right={<button className="btn-primary text-xs flex items-center gap-1" onClick={() => create.mutate()}><Plus className="w-3.5 h-3.5" /> New token</button>}>
        <DataGrid<ScimToken> columns={tcols} rows={toks?.items || []} rowKey={(t) => t.id} empty="No tokens yet. Create one and paste it into your IdP's provisioning settings." minWidth={700} />
      </Section>
      <Section title="Provisioning log" sub="Every create, update and deactivate the IdP sent. Deactivate switches the login off; nothing is deleted.">
        <DataGrid<ScimLogRow> columns={lcols} rows={log?.items || []} rowKey={(r) => r.id} exportName="scim-log" empty="Nothing has been provisioned yet." initialSort={{ key: "at", dir: "desc" }} minWidth={900} />
      </Section>
      {secret && <SecretBox title="SCIM token" value={secret} onClose={() => setSecret(null)} />}
    </div>
  );
}
