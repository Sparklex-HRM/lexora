import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Sun, CalendarDays } from "lucide-react";
import { PageHeader, Spinner, StatTile } from "../components/ui";
import DataGrid, { GridColumn } from "../components/DataGrid";
import { mastersApi } from "../api/endpoints";
import { PROVINCE_NAMES } from "../api/regions";
import { useAuthStore } from "../store/authStore";
import OptionalHolidaysPanel from "./employee/OptionalHolidays";   // floating / optional holidays (leave service)

const fmt = (d?: string) => d ? new Date(d + "T00:00:00").toLocaleDateString("en-CA", { weekday: "short", year: "numeric", month: "long", day: "numeric" }) : "—";
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

/** Statutory holidays by province (default = the viewer's province); rows without a province are federal. */
export default function HolidaysPage({ extra }: { extra?: React.ReactNode }) {
  const user = useAuthStore((s) => s.user);
  const regions = PROVINCE_NAMES;
  const regionWord = "Province";
  const [region, setRegion] = useState<string>(user?.province || "All");
  const { data, isLoading } = useQuery({ queryKey: ["m", "stat-holidays"], queryFn: () => mastersApi.list("stat-holidays") });
  const today = localToday();

  const inData = useMemo(() => [...new Set((data || []).map((h: any) => h.province).filter(Boolean))].sort() as string[], [data]);
  const rows = useMemo(() => {
    const list = (data || []).filter((h: any) => region === "All" || !h.province || h.province === region);
    return [...list].sort((a: any, b: any) => (a.holiday_date || "").localeCompare(b.holiday_date || ""))
      .map((h: any) => ({ ...h, kind: h.is_optional ? "Optional" : "Public",
        state: h.province ? (regions[h.province] || h.province) : "Federal",
        when: (h.holiday_date || "") < today ? "passed" : h.holiday_date === today ? "today" : "upcoming" }));
  }, [data, region, regions, today]);
  const upcoming = rows.filter((h: any) => (h.holiday_date || "") >= today && !h.is_optional);
  const next = upcoming[0];

  const cols: GridColumn<any>[] = [
    { key: "name", label: "Holiday" },
    { key: "holiday_date", label: "Date", text: (r) => r.holiday_date, render: (r) => fmt(r.holiday_date) },
    { key: "state", label: regionWord },
    { key: "kind", label: "Type", render: (r) => r.is_optional ? <span className="badge-purple">Optional</span> : <span className="badge-teal">Public</span> },
    { key: "when", label: "Status", render: (r) => r.when === "passed" ? <span className="text-xs text-gray-400">passed</span>
      : r.when === "today" ? <span className="badge-green">today</span> : <span className="badge-amber">upcoming</span> },
  ];

  return (
    <div>
      <PageHeader title="Statutory Holidays" icon={Sun}
        subtitle="Provincial public holidays that drive stat-pay eligibility — from your Master Data" />

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
        <StatTile label="Holidays on file" value={rows.length} icon={CalendarDays} />
        <StatTile label="Still upcoming" value={upcoming.length} accent />
        <StatTile label="Next holiday" value={next ? next.name : "—"} sub={next ? fmt(next.holiday_date) : ""} />
      </div>
      {extra}
      <OptionalHolidaysPanel />
      {isLoading ? <div className="grid place-items-center py-16"><Spinner className="h-7 w-7" /></div> : (
        <DataGrid columns={cols} rows={rows} rowKey={(r) => r.id} exportName="holidays" minWidth={640} pageSizeDefault={50}
          empty="No statutory holidays yet — add them under Master Data → Stat Holidays."
          filters={<label className="flex items-center gap-2 text-xs text-gray-500">{regionWord}
            <select className="input-base h-8 text-xs" value={region} onChange={(e) => setRegion(e.target.value)} aria-label={regionWord}>
              <option value="All">All provinces</option>
              {(inData.length ? inData : Object.keys(regions)).map((p) => <option key={p} value={p}>{regions[p] || p}</option>)}
            </select></label>} />
      )}
    </div>
  );
}
