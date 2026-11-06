import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatCell } from "./DataGrid";
import type { ChartSpec, ColType } from "../api/platform";

// Two categorical hues in a fixed order (teal = the app's brand colour, then burnt
// orange). Checked for colour-blind separation; a chart here never has more than two
// series. One series → no legend (the card title names it). One y-axis, always.
const SERIES = ["#4f46e5", "#c2410c"];
const axis = { fontSize: 11, fill: "#6b7280" };

export default function SimpleChart({ spec, data, unit = "number", height = 240 }:
  { spec: ChartSpec; data: any[]; unit?: ColType; height?: number }) {
  const series = spec.series.slice(0, 2);
  const fmt = (v: any) => formatCell(v, unit);
  const short = (v: number) => {
    if (unit === "percent") return `${v}%`;
    const a = Math.abs(v);
    return a >= 1e7 ? `${(v / 1e7).toFixed(1)}Cr` : a >= 1e5 && unit === "money" ? `${(v / 1e5).toFixed(1)}L`
      : a >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : a >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : String(v);
  };
  const common = { data, margin: { top: 8, right: 12, left: 0, bottom: 0 } };
  const parts = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" vertical={false} />
      <XAxis dataKey={spec.x} tick={axis} axisLine={false} tickLine={false} interval="preserveStartEnd" />
      <YAxis tick={axis} axisLine={false} tickLine={false} allowDecimals={unit !== "number"} tickFormatter={short} width={48} />
      <Tooltip formatter={(v: any, name: any) => [fmt(v), name]} cursor={{ fill: "rgba(13,148,136,0.06)" }}
        contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }} />
      {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
    </>
  );
  return (
    <ResponsiveContainer width="100%" height={height}>
      {spec.type === "line" ? (
        <LineChart {...common}>
          {parts}
          {series.map((s, i) => <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={SERIES[i]} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls={false} />)}
        </LineChart>
      ) : (
        <BarChart {...common} barGap={2}>
          {parts}
          {series.map((s, i) => <Bar key={s.key} dataKey={s.key} name={s.label} fill={SERIES[i]} radius={[4, 4, 0, 0]} maxBarSize={28} />)}
        </BarChart>
      )}
    </ResponsiveContainer>
  );
}
