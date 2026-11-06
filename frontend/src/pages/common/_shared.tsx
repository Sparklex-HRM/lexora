import React, { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Search, ArrowUpDown, Printer } from "lucide-react";
import { printRegion } from "../../lib/print";
import { useAuthStore } from "../../store/authStore";
import AppModal from "../../components/Modal";
import { employeeApi, money } from "../../api/endpoints";
import { thisMonthISO, todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/* ── Shared bits for the dense feature pages (Stream F) ──────────────────────
   Grid: dense Excel-style rows with search / filter / sort / CSV export.
   Modal: WIDE (uses the screen), 2-column dense forms.
   useMe: the employee record of the logged-in user.                          */

export const thisMonth = () => thisMonthISO();
export const addMonths = (m: string, n: number) => {
  const y = +m.slice(0, 4), mo = +m.slice(5, 7);
  const idx = y * 12 + (mo - 1) + n;
  return `${String(Math.floor(idx / 12)).padStart(4, "0")}-${String((idx % 12) + 1).padStart(2, "0")}`;
};
export const today = () => todayISO();
export const fmtMonth = (m?: string | null) => {
  if (!m) return "—";
  const d = new Date(+m.slice(0, 4), +m.slice(5, 7) - 1, 1);
  return d.toLocaleDateString("en-CA", { month: "short", year: "numeric" });
};
export const cad = (n: any) => money(Number(n) || 0);
export const errMsg = (e: any, fallback = "Something went wrong") => e?.response?.data?.detail || e?.message || fallback;

export function useMe() {
  const user = useAuthStore((s) => s.user);
  const { data: emps, isLoading } = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const email = (user?.email || "").toLowerCase();
  const list = (emps || []) as any[];
  const me: any = (user?.employee_id && list.find((e) => e.id === user.employee_id)) ||
    list.find((e) => (e.email || "").toLowerCase() === email || (e.work_email || "").toLowerCase() === email);
  return { me, isLoading, user, employees: list };
}

export function useEmployees() {
  const { data } = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const list = ((data || []) as any[]).filter((e) => e.status !== "terminated");
  return list.sort((a, b) => `${a.first_name} ${a.last_name}`.localeCompare(`${b.first_name} ${b.last_name}`));
}
export const empLabel = (e: any) => `${e.first_name} ${e.last_name} · ${e.employee_number}`;

/* ── Grid ─────────────────────────────────────────────────────────────────── */
export interface Col<T = any> {
  k: string; label: string; w?: string; align?: "left" | "right" | "center";
  render?: (row: T) => React.ReactNode; csv?: (row: T) => any; sortVal?: (row: T) => any; hide?: boolean;
}
export function toCsv(rows: any[], cols: Col[]): string {
  const esc = (v: any) => { const s = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const head = cols.map((c) => esc(c.label)).join(",");
  const body = rows.map((r) => cols.map((c) => esc(c.csv ? c.csv(r) : r[c.k])).join(",")).join("\n");
  return head + "\n" + body;
}
export function downloadText(name: string, text: string, type = "text/csv") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
export function downloadBlob(name: string, blob: Blob) {
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export function Grid<T extends Record<string, any>>({ rows, cols, keyOf, title, empty = "Nothing here yet.", filters, actions, onRow, exportName, dense = true, loading }:
  { rows: T[]; cols: Col<T>[]; keyOf: (r: T) => string; title?: string; empty?: string; filters?: React.ReactNode;
    actions?: React.ReactNode; onRow?: (r: T) => void; exportName?: string; dense?: boolean; loading?: boolean }) {
  const [qText, setQ] = useState("");
  const [sort, setSort] = useState<{ k: string; dir: 1 | -1 } | null>(null);
  const visible = cols.filter((c) => !c.hide);
  const shown = useMemo(() => {
    let out = rows;
    if (qText.trim()) {
      const t = qText.toLowerCase();
      out = out.filter((r) => visible.some((c) => String(c.csv ? c.csv(r) : r[c.k] ?? "").toLowerCase().includes(t)));
    }
    if (sort) {
      const c = visible.find((x) => x.k === sort.k);
      const val = (r: T) => (c?.sortVal ? c.sortVal(r) : c?.csv ? c.csv(r) : r[sort.k]);
      out = [...out].sort((a, b) => { const va = val(a), vb = val(b); return (va > vb ? 1 : va < vb ? -1 : 0) * sort.dir; });
    }
    return out;
  }, [rows, qText, sort, visible]);
  const py = dense ? "py-1.5" : "py-2.5";
  const gridRef = useRef<HTMLDivElement>(null);   // Print = this grid only, every filtered row
  const printTitle = title || (exportName ? exportName.replace(/\.[a-z]+$/i, "").replace(/[-_]+/g, " ") : "");
  return (
    <div className="card !p-0" ref={gridRef}>
      {exportName && <div className="lx-print-only px-1 pb-2 text-sm font-semibold text-gray-800">{printTitle} · {shown.length} row{shown.length === 1 ? "" : "s"}{qText.trim() ? ` matching "${qText.trim()}"` : ""} · printed {new Date().toLocaleString("en-CA")}</div>}
      <div className="lx-no-print flex flex-wrap items-center gap-2 px-3 py-2 border-b border-surface-border">
        {title && <h3 className="font-display font-semibold text-gray-800 mr-2">{title}</h3>}
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2 top-2.5 text-gray-400" />
          <input className="input !py-1.5 !pl-7 text-xs w-44" placeholder="Search rows…" value={qText} onChange={(e) => setQ(e.target.value)} />
        </div>
        {filters}
        <span className="text-[11px] text-gray-400 ml-auto tabular-nums">{shown.length} / {rows.length} rows</span>
        {exportName && (
          <button className="btn-secondary !py-1 !px-2 text-xs flex items-center gap-1" onClick={() => downloadText(exportName, toCsv(shown, visible))}>
            <Download className="w-3.5 h-3.5" /> CSV
          </button>
        )}
        {exportName && (
          <button className="btn-secondary !py-1 !px-2 text-xs flex items-center gap-1" disabled={!shown.length}
            onClick={() => printRegion(gridRef.current, { landscape: visible.length > 8 })} title="Print every row shown (A4)">
            <Printer className="w-3.5 h-3.5" /> Print
          </button>
        )}
        {actions}
      </div>
      {loading ? <div className="text-center py-10 text-sm text-gray-400">Loading…</div>
        : !shown.length ? <div className="text-center py-10 text-sm text-gray-400">{empty}</div> : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-surface text-gray-400 text-left text-[10px] uppercase tracking-wide">
                <tr>
                  {visible.map((c) => (
                    <th key={c.k} style={c.w ? { width: c.w } : undefined}
                      className={`px-2.5 py-2 font-semibold whitespace-nowrap cursor-pointer select-none ${c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : ""}`}
                      onClick={() => setSort((s) => (s?.k === c.k ? (s.dir === 1 ? { k: c.k, dir: -1 } : null) : { k: c.k, dir: 1 }))}>
                      <span className="inline-flex items-center gap-1">{c.label}{sort?.k === c.k && <ArrowUpDown className="w-3 h-3 text-primary-500" />}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={keyOf(r)} onClick={onRow ? () => onRow(r) : undefined}
                    className={`border-t border-surface-border ${onRow ? "hover:bg-primary-50/40 cursor-pointer" : "hover:bg-gray-50"}`}>
                    {visible.map((c) => (
                      <td key={c.k} className={`px-2.5 ${py} whitespace-nowrap ${c.align === "right" ? "text-right tabular-nums" : c.align === "center" ? "text-center" : ""} text-gray-700`}>
                        {c.render ? c.render(r) : (r[c.k] ?? "—")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </div>
  );
}

/* ── Modal (wide, uses the screen) ─────────────────────────────────────────── */
// 2026-10-24: thin wrapper over the app-wide AppModal (wider presets, sticky
// header/footer, phone sheet, Esc = top-most only, `printable`).
//   lg → form (max-w-5xl) · xl → xwide (max-w-7xl) · screen → full viewport
export function Modal({ title, subtitle, onClose, children, footer, size = "xl", printable, actions }:
  { title: string; subtitle?: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; size?: "lg" | "xl" | "screen"; printable?: boolean; actions?: React.ReactNode }) {
  return (
    <AppModal title={title} subtitle={subtitle} onClose={onClose} footer={footer} printable={printable} actions={actions}
      size={size === "screen" ? "screen" : size === "xl" ? "xwide" : "form"}>
      {children}
    </AppModal>
  );
}

/* ── Form primitives ───────────────────────────────────────────────────────── */
export function Field({ label, children, hint, span }: { label: React.ReactNode; children: React.ReactNode; hint?: React.ReactNode; span?: number }) {
  return (
    <label className={`block ${span ? `sm:col-span-${span}` : ""}`}>
      <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="text-[11px] text-gray-400">{hint}</span>}
    </label>
  );
}
export const Input = (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} className={`input !py-1.5 text-sm w-full ${p.className || ""}`} />;
export const Select = (p: React.SelectHTMLAttributes<HTMLSelectElement>) => <select {...p} className={`input !py-1.5 text-sm w-full ${p.className || ""}`} />;
export const Textarea = (p: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...p} className={`input !py-1.5 text-sm w-full ${p.className || ""}`} />;

export function EmployeePicker({ value, onChange, employees, className = "" }:
  { value: string; onChange: (id: string) => void; employees: any[]; className?: string }) {
  const src = employees;
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} className={className}>
      <option value="">— pick an employee —</option>
      {src.map((e) => <option key={e.id} value={e.id}>{empLabel(e)}</option>)}
    </Select>
  );
}

const TONE: Record<string, string> = {
  submitted: "badge-amber", requested: "badge-amber", preview: "badge-amber", recorded: "badge-amber", pending: "badge-amber",
  approved: "badge-teal", active: "badge-green", posted: "badge-green", paid: "badge-green", done: "badge-green", consumed: "badge-green", locked: "badge-purple",
  rejected: "badge-red", cancelled: "badge-red", failed: "badge-red", closed: "badge-purple", partial: "badge-amber", draft: "badge-purple",
};
export const Status = ({ s }: { s?: string }) => <span className={`${TONE[s || ""] || "badge-purple"} capitalize`}>{(s || "—").replace(/_/g, " ")}</span>;

export function Stat({ label, value, sub, accent }: { label: string; value: React.ReactNode; sub?: string; accent?: boolean }) {
  return (
    <div className="card !py-3">
      <div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">{label}</div>
      <div className={`text-xl font-bold mt-1 tabular-nums ${accent ? "text-primary-600" : "text-gray-800"}`}>{value}</div>
      {sub && <div className="text-[11px] text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export function MonthInput({ value, onChange, className = "" }: { value: string; onChange: (m: string) => void; className?: string }) {
  return <input type="month" className={`input !py-1.5 text-sm ${className}`} value={value} onChange={(e) => onChange(e.target.value)} />;
}
