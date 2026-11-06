import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { CheckCircle2, Package } from "lucide-react";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { assetsApi, type AssetRow } from "../../api/assets";
import { errText } from "../../api/platform";
import { useLang, useT } from "../../i18n";
import { fmtDate, fmtDateTime } from "../../i18n/format";
import ReadPage from "../../i18n/ReadPage";
import { AuthImg } from "../mobile/_mobile";

/** /engage/my-assets — what is issued to me, with the "I acknowledge" click (time + login recorded, no e-sign).
 *  A desktop page (2026-10-21): header + KPI strip + one dense grid; the phone layout lives under /mobile/*. */
export default function MyAssetsPage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["m-assets"], queryFn: () => assetsApi.mine() });
  const ack = useMutation({ mutationFn: (id: string) => assetsApi.acknowledge(id), onSuccess: () => { toast.success(t("common.done")); qc.invalidateQueries({ queryKey: ["m-assets"] }); }, onError: (e) => toast.error(errText(e)) });
  const rows = q.data?.assets || [];
  const cols: GridColumn<AssetRow>[] = [
    { key: "item", label: t("assets.item"), className: "text-gray-800 font-medium" },
    { key: "tag", label: t("assets.tag"), width: "100px" },
    { key: "category", label: "Category", width: "120px" },
    { key: "serial", label: "Serial", className: "font-mono text-xs" },
    { key: "issued_on", label: t("assets.issued_on"), width: "110px", render: (r) => fmtDate(r.issued_on, lang), text: (r) => r.issued_on || "" },
    { key: "returned_on", label: t("assets.returned_on"), width: "110px", render: (r) => fmtDate(r.returned_on, lang), text: (r) => r.returned_on || "" },
    { key: "return_condition", label: t("assets.condition"), width: "110px" },
    { key: "status", label: t("common.status"), width: "100px", text: (r) => r.status.replace("_", " "),
      render: (r) => <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${r.status === "assigned" ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-600"}`}>{r.status.replace("_", " ")}</span> },
    { key: "photo_doc_ids", label: "Photos", sortable: false, text: (r) => String(r.photo_doc_ids.length),
      render: (r) => r.photo_doc_ids.length ? <div className="flex gap-1">{r.photo_doc_ids.map((d) => <AuthImg key={d} load={() => assetsApi.photoBlob(d)} className="w-9 h-9 rounded object-cover border border-surface-border" />)}</div> : "—" },
    { key: "acknowledged", label: "Acknowledgement", sortable: false, text: (r) => (r.acknowledged ? `acknowledged ${r.ack_at || ""}` : r.status === "assigned" ? "pending" : ""),
      render: (r) => r.status !== "assigned" ? "—" : r.acknowledged
        ? <span className="text-xs text-green-700 inline-flex items-center gap-1"><CheckCircle2 className="w-3.5 h-3.5 shrink-0" /> {t("assets.acknowledged", { when: fmtDateTime(r.ack_at, lang) })}</span>
        : <button className="btn-primary !py-1 !px-2.5 text-xs whitespace-nowrap" disabled={ack.isPending} onClick={(e) => { e.stopPropagation(); ack.mutate(r.id); }}>{t("assets.acknowledge")}</button> },
  ];
  const assigned = rows.filter((r) => r.status === "assigned").length;
  return (
    <div>
      <PageHeader title={t("assets.title")} icon={Package} subtitle="Company assets issued to you · acknowledge what you received" actions={<ReadPage />} />
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatTile label={t("common.all")} value={q.data ? rows.length : "—"} accent />
        <StatTile label="Assigned" value={q.data ? assigned : "—"} />
        <StatTile label="To acknowledge" value={q.data ? q.data.pending_ack : "—"} />
        <StatTile label="Returned" value={q.data ? rows.filter((r) => r.status === "returned").length : "—"} />
      </div>
      {q.data && q.data.pending_ack > 0 && <div className="rounded-xl bg-amber-50 border border-amber-200 text-amber-800 text-sm px-3 py-2 mb-3">{t("assets.pending", { n: q.data.pending_ack })}</div>}
      <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} minWidth={1000} exportName="my-assets" empty={q.isLoading ? t("common.loading") : t("assets.none")}
        initialSort={{ key: "issued_on", dir: "desc" }} />
    </div>
  );
}
