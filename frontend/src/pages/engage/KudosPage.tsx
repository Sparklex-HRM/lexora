import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { Award, Plus } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { engageApi, type Kudos } from "../../api/engage";
import { errText } from "../../api/platform";
import { useLang, useT } from "../../i18n";
import { fmtDateTime } from "../../i18n/format";
import ReadPage from "../../i18n/ReadPage";
import { useMe } from "../mobile/_mobile";

const LABEL = "block text-xs font-semibold text-gray-500 mb-1";

/** /engage/kudos — the recognition wall, give kudos with a value tag, HR moderation, monthly summary.
 *  A desktop page (2026-10-21): header + tabs + dense grid; giving kudos opens a wide modal. */
export default function KudosPage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const { me, employees } = useMe();
  const tab = sp.get("tab") || "feed";
  const [give, setGive] = useState(false);
  const [f, setF] = useState({ to_employee_id: "", value_tag: "", message: "" });
  const feed = useQuery({ queryKey: ["kudos", tab], queryFn: () => engageApi.kudos(tab === "pending" ? "pending" : undefined) });
  const canMod = !!feed.data?.can_moderate;
  const summary = useQuery({ queryKey: ["kudos-summary"], queryFn: () => engageApi.kudosSummary(), enabled: tab === "monthly" });
  const m = useMutation({ mutationFn: () => engageApi.giveKudos(f), onSuccess: () => { toast.success(t("kudos.sent")); setGive(false); setF({ to_employee_id: "", value_tag: "", message: "" }); qc.invalidateQueries({ queryKey: ["kudos"] }); }, onError: (e) => toast.error(errText(e)) });
  const mod = useMutation({ mutationFn: ({ id, d }: { id: string; d: "approve" | "reject" }) => engageApi.moderateKudos(id, d), onSuccess: () => qc.invalidateQueries({ queryKey: ["kudos"] }), onError: (e) => toast.error(errText(e)) });
  // The org directory (names only): GET /employees is scoped to the caller, so an employee would see nobody to thank.
  const emps = useQuery({ queryKey: ["engage-people"], queryFn: () => engageApi.people(), enabled: give });
  const people = ((emps.data && emps.data.length ? emps.data : employees) as any[]).filter((e) => e.status !== "terminated" && e.id !== me?.id);
  const values = feed.data?.values || [];
  const tabs: { key: string; label: string }[] = [{ key: "feed", label: t("kudos.feed") }, { key: "monthly", label: t("kudos.monthly") }, ...(canMod ? [{ key: "pending", label: t("kudos.pending") }] : [])];

  const cols: GridColumn<Kudos>[] = [
    { key: "created_at", label: t("common.date"), width: "150px", render: (k) => fmtDateTime(k.created_at, lang), text: (k) => k.created_at || "" },
    { key: "from_name", label: "From", width: "170px", className: "text-gray-800 font-medium" },
    { key: "to_name", label: t("kudos.to"), width: "170px", className: "text-gray-800 font-medium" },
    { key: "value_tag", label: t("kudos.value"), width: "140px", render: (k) => k.value_tag ? <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-primary-50 text-primary-700 whitespace-nowrap">{k.value_tag}</span> : "—" },
    { key: "message", label: t("kudos.message"), render: (k) => <span className="text-gray-700">{k.message}</span> },
    { key: "status", label: t("common.status"), width: "100px" },
    ...(canMod && tab === "pending" ? [{ key: "act", label: "", sortable: false, width: "150px", render: (k: Kudos) => k.status === "pending" ? <div className="flex gap-1.5">
      <button className="btn-primary !py-1 !px-2 text-xs" onClick={(e) => { e.stopPropagation(); mod.mutate({ id: k.id, d: "approve" }); }}>{t("team.approve")}</button>
      <button className="btn-secondary !py-1 !px-2 text-xs" onClick={(e) => { e.stopPropagation(); mod.mutate({ id: k.id, d: "reject" }); }}>{t("team.reject")}</button></div> : null } as GridColumn<Kudos>] : []),
  ];
  const s = summary.data;
  return (
    <div>
      <PageHeader title={t("kudos.title")} icon={Award} subtitle="Recognise colleagues for living the company values"
        actions={<div className="flex items-center gap-2 shrink-0"><ReadPage /><button className="btn-primary text-sm inline-flex items-center gap-1 whitespace-nowrap" onClick={() => setGive(true)}><Plus className="w-4 h-4" /> {t("kudos.give")}</button></div>} />
      <Tabs value={tab} onChange={(k) => setSp({ tab: k })} tabs={tabs} />
      {(tab === "feed" || tab === "pending") && (
        <DataGrid columns={cols} rows={feed.data?.kudos || []} rowKey={(k) => k.id} minWidth={960} exportName={tab === "pending" ? "kudos-pending" : "kudos"}
          empty={feed.isLoading ? t("common.loading") : t("kudos.none")} initialSort={{ key: "created_at", dir: "desc" }} />
      )}
      {tab === "monthly" && !s && <div className="card text-sm text-gray-500 text-center py-8">{summary.isError ? errText(summary.error) : t("common.loading")}</div>}
      {tab === "monthly" && s && (
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <StatTile label={t("kudos.title")} value={s.total} accent />
            <StatTile label={t("team.people")} value={s.people_recognised} />
            <StatTile label={t("kudos.value")} value={Object.keys(s.by_value || {}).length} />
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div><h3 className="font-display font-semibold text-gray-800 mb-2">Top</h3>
              <DataGrid columns={[{ key: "rank", label: "#", type: "number", width: "60px" }, { key: "name", label: t("common.name"), className: "text-gray-800" }, { key: "count", label: t("kudos.title"), type: "number" }]}
                rows={(s.top || []).map((p: any, i: number) => ({ ...p, rank: i + 1 }))} rowKey={(p: any) => p.employee_id} minWidth={360} exportName="kudos-top" density="compact" /></div>
            <div><h3 className="font-display font-semibold text-gray-800 mb-2">{t("kudos.value")}</h3>
              <DataGrid columns={[{ key: "value", label: t("kudos.value"), className: "text-gray-800" }, { key: "count", label: t("kudos.title"), type: "number" }]}
                rows={Object.entries(s.by_value || {}).map(([value, count]: any) => ({ value, count }))} rowKey={(r: any) => r.value} minWidth={320} exportName="kudos-by-value" density="compact" /></div>
          </div>
        </div>
      )}
      {give && (
        <WideModal title={t("kudos.give")} onClose={() => setGive(false)}
          footer={<><button type="button" className="btn-secondary" onClick={() => setGive(false)}>{t("common.cancel")}</button><button type="submit" form="kudos-give" className="btn-primary" disabled={m.isPending}>{t("common.send")}</button></>}>
          <form id="kudos-give" className="grid grid-cols-1 md:grid-cols-2 gap-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
            <div><label className={LABEL}>{t("kudos.to")}</label><select className="input w-full" required value={f.to_employee_id} onChange={(e) => setF({ ...f, to_employee_id: e.target.value })}><option value="">—</option>{people.map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name}{e.department ? ` · ${e.department}` : ""}</option>)}</select></div>
            <div><label className={LABEL}>{t("kudos.value")}</label><div className="flex flex-wrap gap-1.5">{values.map((v) => <button type="button" key={v} onClick={() => setF({ ...f, value_tag: f.value_tag === v ? "" : v })} className={`px-3 py-1.5 rounded-lg text-sm border ${f.value_tag === v ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border text-gray-700"}`}>{v}</button>)}</div></div>
            <div className="md:col-span-2"><label className={LABEL}>{t("kudos.message")}</label><textarea className="input w-full" rows={4} required maxLength={600} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></div>
            {feed.data?.needs_approval && <p className="md:col-span-2 text-xs text-gray-500">{t("kudos.pending")}</p>}
          </form>
        </WideModal>
      )}
    </div>
  );
}
