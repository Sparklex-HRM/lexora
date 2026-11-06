import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { ProviderInfo, WaMessage, chatApi, errText, when } from "../../api/integrations";
import { LABEL, ProviderPanel, Section, Status } from "./shared";

export function ChatTab({ providers, onChanged }: { providers: ProviderInfo[]; onChanged: () => void }) {
  const { data: wa, refetch } = useQuery({ queryKey: ["integrations", "whatsapp", "messages"], queryFn: chatApi.waMessages });
  const { data: cmds } = useQuery({ queryKey: ["integrations", "chat", "commands"], queryFn: chatApi.commands });
  const [to, setTo] = useState("");
  const test = useMutation({ mutationFn: () => chatApi.waTest(to.trim()),
    onSuccess: (r) => { (r.status === "sent" ? toast.success : toast.error)(`${r.status}${r.error ? `: ${r.error}` : ""}`); refetch(); onChanged(); },
    onError: (e: any) => toast.error(errText(e, "The test could not run")) });
  const cols: GridColumn<WaMessage>[] = [
    { key: "created_at", label: "When", text: (m) => m.created_at || "", render: (m) => when(m.created_at) },
    { key: "to", label: "To" }, { key: "kind", label: "Type" }, { key: "template", label: "Template" }, { key: "provider", label: "Via" },
    { key: "status", label: "Status", render: (m) => <Status s={m.status} /> }, { key: "error", label: "Detail" },
    { key: "updated_at", label: "Updated", text: (m) => m.updated_at || "", render: (m) => when(m.updated_at) },
  ];
  return (
    <div>
      <Section title="Slack, Microsoft Teams and WhatsApp" sub="Mirror in-app notifications (title + link only) by type, and take a few commands from chat. Nothing is sent until a provider is switched on.">
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {providers.filter((p) => ["slack", "teams", "whatsapp"].includes(p.key)).map((p) => <ProviderPanel key={p.key} p={p} onChanged={onChanged} />)}
        </div>
      </Section>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 mb-5">
        <div className="card text-xs">
          <h3 className="font-semibold text-gray-800 mb-2">Chat commands</h3>
          <table className="w-full"><tbody>{(cmds?.commands || []).map((c) => (
            <tr key={c.command} className="border-t border-surface-border"><td className="py-1 pr-3 font-mono whitespace-nowrap">/lexora {c.command}</td><td className="py-1 text-gray-600">{c.what}</td></tr>))}</tbody></table>
          <p className="text-gray-500 mt-2">{cmds?.link} Each person links their own chat account once from <b>Self-Service → Chat link</b>.</p>
          <p className="text-gray-400 mt-1">Slack: create a slash command pointing at <code>…/api/notifications/chatops/slack/command</code> and paste the signing secret above. Teams: an outgoing webhook pointing at <code>…/api/notifications/chatops/teams/command</code> with its security token.</p>
        </div>
        <div className="card text-xs">
          <h3 className="font-semibold text-gray-800 mb-2">WhatsApp test</h3>
          <label className={LABEL}>Send the notification template to</label>
          <div className="flex gap-2">
            <input className="input flex-1" value={to} onChange={(e) => setTo(e.target.value)} placeholder="+1 416 555 0123" />
            <button className="btn-primary text-xs" disabled={!to.trim() || test.isPending} onClick={() => test.mutate()}>Send test</button>
          </div>
          <p className="text-gray-400 mt-2">Receipt webhook (Meta or Twilio): <code>…/api/notifications/whatsapp/webhook</code>. Status moves to delivered / read only from receipts.</p>
        </div>
      </div>
      <Section title="WhatsApp message log" sub="'Sent' only after the provider accepted the message. Delivered and read come from the receipt webhook.">
        <DataGrid<WaMessage> columns={cols} rows={wa?.items || []} rowKey={(m) => m.id} exportName="whatsapp-log" empty="No WhatsApp messages yet." minWidth={900} initialSort={{ key: "created_at", dir: "desc" }} />
      </Section>
    </div>
  );
}
