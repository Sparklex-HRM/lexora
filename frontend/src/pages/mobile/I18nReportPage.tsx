import { useMemo, useState } from "react";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { StatTile } from "../../components/ui";
import { catalogReport, runtimeMissing, useT } from "../../i18n";
import { LANG_NAMES } from "../../i18n/format";
import { MobileShell } from "./_mobile";

/** /mobile/translations — every English text and which languages lack it; plus keys that fell back at runtime. */
export default function I18nReportPage() {
  const t = useT();
  const [only, setOnly] = useState<"all" | "fr" | "runtime">("all");
  const rep = useMemo(() => catalogReport(), []);
  const rt = runtimeMissing();
  const rows = useMemo(() => {
    const base = rep.rows.map((r) => ({ ...r, runtime: (["fr"] as const).filter((l) => rt[l].includes(r.key)).join(", "), missing_s: r.missing.join(", ") }));
    if (only === "all") return base;
    if (only === "runtime") return base.filter((r) => r.runtime);
    return base.filter((r) => r.missing.includes(only));
  }, [rep, only, rt]);
  const cols: GridColumn[] = [
    { key: "key", label: "Key", className: "font-mono text-[11px]" }, { key: "en", label: "English" },
    { key: "fr", label: LANG_NAMES.fr },
    { key: "missing_s", label: t("i18n.missing"), className: "text-red-600" }, { key: "runtime", label: "Fell back on this device" },
  ];
  return (
    <MobileShell title={t("i18n.title")} wide>
      <div className="grid grid-cols-2 gap-2 mb-3">
        <StatTile label={t("i18n.keys")} value={rep.totals.keys} accent />
        <StatTile label={`${LANG_NAMES.fr} · ${t("i18n.missing")}`} value={rep.totals.fr} />
      </div>
      <div className="flex gap-1 mb-3 flex-wrap">
        {(["all", "fr", "runtime"] as const).map((k) => <button key={k} onClick={() => setOnly(k)} className={`px-3 py-1.5 rounded-lg text-sm ${only === k ? "bg-primary-600 text-white" : "bg-white border border-surface-border text-gray-600"}`}>{k === "all" ? t("common.all") : k === "runtime" ? "Fell back at runtime" : `${t("i18n.missing")}: ${LANG_NAMES[k]}`}</button>)}
      </div>
      {rep.extra.some((e) => e.keys.length > 0) && <div className="card text-xs text-amber-800 bg-amber-50 border-amber-200 mb-3">Keys that exist only in the French file (not in English): {rep.extra.filter((e) => e.keys.length).map((e) => `${e.lang}: ${e.keys.join(", ")}`).join(" · ")}</div>}
      <DataGrid columns={cols} rows={rows} rowKey={(r) => r.key} minWidth={1100} exportName="translations" pageSizeDefault={50} />
      <p className="text-[11px] text-gray-400 mt-2">A text that falls back to English is still shown in English on the screen — but it is counted here and in the language picker, so it is never silent. In development builds it is also prefixed with ⚠.</p>
    </MobileShell>
  );
}
