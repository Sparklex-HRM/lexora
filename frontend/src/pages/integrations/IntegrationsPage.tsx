import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Plug } from "lucide-react";
import { PageHeader, Spinner } from "../../components/ui";
import DataGrid, { GridColumn, Tabs } from "../../components/DataGrid";
import { AuditRow, integrationsApi, when } from "../../api/integrations";
import { ProviderPanel, Section, Status } from "./shared";
import { ScimTab, SsoTab } from "./SsoScimTabs";
import { ApiKeysTab, WebhooksTab } from "./KeysWebhooksTabs";
import { EsignTab, VerifyTab } from "./DocsVerifyTabs";
import { ChatTab } from "./ChatTab";
import { ProviderCallsTab, WhatsAppConsentSection } from "./S5Tabs";

type Tab = "overview" | "sso" | "scim" | "keys" | "webhooks" | "chat" | "esign" | "verify" | "calls" | "audit";
const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" }, { key: "sso", label: "Single sign-on" }, { key: "scim", label: "SCIM provisioning" },
  { key: "keys", label: "API keys" }, { key: "webhooks", label: "Webhooks" }, { key: "chat", label: "Slack · Teams · WhatsApp" },
  { key: "esign", label: "E-sign" }, { key: "verify", label: "Background checks" },
  { key: "calls", label: "Provider calls" }, { key: "audit", label: "Audit" },
];

export default function IntegrationsPage() {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const tab = TABS.some((t) => t.key === sp.get("tab")) ? (sp.get("tab") as Tab) : "overview";
  const setTab = (t: Tab) => setSp(t === "overview" ? {} : { tab: t });
  const { data, isLoading } = useQuery({ queryKey: ["integrations", "overview"], queryFn: integrationsApi.overview });
  const changed = () => qc.invalidateQueries({ queryKey: ["integrations"] });
  const providers = data?.providers || [];
  return (
    <div>
      <PageHeader title="Integrations" icon={Plug}
        subtitle="Single sign-on, provisioning, API keys, webhooks, chat (Slack, Teams, WhatsApp), e-sign and background-check providers. Every status is what the provider confirmed — never a guess." />
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {isLoading || !data ? <div className="grid place-items-center py-16"><Spinner /></div> : (
        <>
          {tab === "overview" && <Overview data={data} onChanged={changed} />}
          {tab === "sso" && <SsoTab />}
          {tab === "scim" && <ScimTab />}
          {tab === "keys" && <ApiKeysTab />}
          {tab === "webhooks" && <WebhooksTab />}
          {tab === "chat" && <><ChatTab providers={providers} onChanged={changed} /><WhatsAppConsentSection /></>}
          {tab === "esign" && <EsignTab providers={providers} onChanged={changed} />}
          {tab === "verify" && <VerifyTab providers={providers} onChanged={changed} />}
          {tab === "calls" && <ProviderCallsTab />}
          {tab === "audit" && <AuditTab />}
        </>
      )}
    </div>
  );
}

function Overview({ data, onChanged }: { data: NonNullable<ReturnType<typeof useOverview>>; onChanged: () => void }) {
  const rows = [
    { k: "Single sign-on", s: data.sso.status, d: data.sso.status === "configured" ? `${(data.sso.protocol || "").toUpperCase()} · ${data.sso.display_name}${data.sso.required ? " · required" : ""}` : "No identity provider set up", t: "sso" },
    { k: "SCIM provisioning", s: data.scim.status, d: `${data.scim.active_tokens} active token(s)`, t: "scim" },
    { k: "API keys", s: data.api_keys.active ? "configured" : "not_configured", d: `${data.api_keys.active} active key(s)`, t: "keys" },
    { k: "Webhooks", s: data.webhooks.active ? "configured" : "not_configured", d: `${data.webhooks.active} switched on`, t: "webhooks" },
  ];
  const [, setSp] = useSearchParams();
  return (
    <div>
      <Section title="Platform">
        <div className="card !p-0 overflow-x-auto">
          <table className="w-full text-[13px]"><tbody>{rows.map((r) => (
            <tr key={r.k} className="border-t border-surface-border first:border-t-0 hover:bg-surface/60 cursor-pointer" onClick={() => r.t && setSp({ tab: r.t })}>
              <td className="px-3 py-2 font-medium text-gray-800 whitespace-nowrap">{r.k}</td><td className="px-3 py-2"><Status s={r.s} /></td><td className="px-3 py-2 text-gray-600">{r.d}</td>
            </tr>))}</tbody></table>
        </div>
      </Section>
      <Section title="Providers" sub="Configured = credentials saved and the provider is switched on. Not configured = nothing is sent, verified or signed.">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {data.providers.map((p) => <ProviderPanel key={p.key} p={p} onChanged={onChanged} />)}
        </div>
      </Section>
    </div>
  );
}
function useOverview() { return useQuery({ queryKey: ["integrations", "overview"], queryFn: integrationsApi.overview }).data; }

function AuditTab() {
  const { data } = useQuery({ queryKey: ["integrations", "audit"], queryFn: integrationsApi.audit });
  const cols: GridColumn<AuditRow>[] = [
    { key: "at", label: "When", text: (r) => r.at || "", render: (r) => when(r.at) }, { key: "actor", label: "Who" }, { key: "role", label: "Role" },
    { key: "action", label: "Action", render: (r) => <span className="font-mono text-[11px]">{r.action}</span> },
    { key: "summary", label: "What" }, { key: "entity_id", label: "Record", render: (r) => <span className="font-mono text-[11px]">{r.entity_type}{r.entity_id ? ` ${r.entity_id.slice(0, 8)}…` : ""}</span> },
  ];
  return <Section title="Audit of changes" sub="Every integration change, test, key, token and sign-in through SSO, from the tamper-evident audit trail.">
    <DataGrid<AuditRow> columns={cols} rows={data?.items || []} rowKey={(r) => r.id} exportName="integrations-audit" empty="No changes recorded yet." minWidth={900} initialSort={{ key: "at", dir: "desc" }} />
  </Section>;
}
