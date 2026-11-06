import React, { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ChevronRight, Download, Printer, Search, X } from "lucide-react";
import { printRegion } from "../lib/print";
import { Pager } from "./Pager";
import { Empty } from "./ui";
import AppModal, { type ModalSize } from "./Modal";
import { money } from "../api/endpoints";
import { day } from "../api/platform";

/** One dense grid for every list on the stream-E screens: search, per-column sort,
 *  optional filter chips, CSV export, paging and an optional totals row. */
export interface GridColumn<T = any> {
  key: string; label: string;
  type?: "text" | "number" | "money" | "percent" | "date" | "hours";
  render?: (row: T) => React.ReactNode;       // screen only
  text?: (row: T) => string;                  // value used for search / sort / export when `render` is set
  sortable?: boolean; className?: string; width?: string;
}

const NUMERIC = new Set(["number", "money", "percent", "hours"]);

export function formatCell(v: any, type?: string): string {
  if (v === null || v === undefined || v === "") return "—";
  if (type === "money") return money(Number(v));
  if (type === "percent") return `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  if (type === "hours") return `${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })} h`;
  if (type === "number") return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (type === "date") return day(String(v));
  return String(v);
}

function raw<T>(c: GridColumn<T>, row: any): any {
  return c.text ? c.text(row) : row[c.key];
}

export function toCsv<T>(columns: GridColumn<T>[], rows: T[], totals?: Record<string, any> | null): string {
  const esc = (v: any) => {
    let s = v === null || v === undefined ? "" : String(v);
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;             // no formula injection when opened in a spreadsheet
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [columns.map((c) => esc(c.label)).join(",")];
  for (const r of rows) lines.push(columns.map((c) => esc(raw(c, r))).join(","));
  if (totals) lines.push(columns.map((c) => esc(totals[c.key])).join(","));
  return lines.join("\r\n");
}

export function downloadCsv(name: string, csv: string) {
  const url = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url; a.download = `${name}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type GridDensity = "comfortable" | "compact";

export default function DataGrid<T extends Record<string, any>>({
  columns: allColumns, rows, rowKey, totals, exportName, searchPlaceholder = "Search…", empty = "Nothing to show.",
  filters, toolbar, initialSort, pageSizeDefault = 25, minWidth = 720, onRowClick,
  // ── Optional extras (U3). Every default below reproduces the original grid exactly. ──
  selectable = false, selected, onSelectionChange,
  groupBy, columnVisibility, density = "comfortable", stickyHeader = false, maxHeight,
  initialSearch, searchText, rowClassName,
}: {
  columns: GridColumn<T>[]; rows: T[]; rowKey: (row: T, i: number) => string;
  totals?: Record<string, any> | null; exportName?: string; searchPlaceholder?: string; empty?: string;
  filters?: React.ReactNode; toolbar?: React.ReactNode;
  initialSort?: { key: string; dir: "asc" | "desc" }; pageSizeDefault?: number; minWidth?: number;
  onRowClick?: (row: T) => void;
  /** Checkbox column + "select all on this page". Selection is controlled: pass `selected` (row keys) and `onSelectionChange`. */
  selectable?: boolean; selected?: Set<string>; onSelectionChange?: (keys: Set<string>) => void;
  /** Group rows under collapsible headers (label + count). Rows are ordered by group first, then by the chosen sort. */
  groupBy?: { key: (row: T) => string; label?: string } | null;
  /** `{ [columnKey]: false }` hides a column from the screen AND the export. Missing keys = shown. */
  columnVisibility?: Record<string, boolean>;
  /** Row height. */
  density?: GridDensity;
  /** Keep the header row in view while the body scrolls (needs `maxHeight` so the grid itself scrolls). */
  stickyHeader?: boolean; maxHeight?: number | string;
  /** Pre-fill the search box (e.g. from a `?q=` deep link) — the user can still clear it. */
  initialSearch?: string;
  /** Extra text the search box also matches against (fields that are not columns, e.g. phone). */
  searchText?: (row: T) => string;
  rowClassName?: (row: T) => string;
}) {
  // Global search (2026-10-20): a result link may carry ?hl=<number / name>. The grid starts filtered on it so
  // the record is visible even when it is not on page 1; if no row matches, the filter is dropped again.
  const [hlSeed] = useState(() => { try { return initialSearch === undefined ? new URLSearchParams(window.location.search).get("hl") || "" : ""; } catch { return ""; } });
  const [q, setQ] = useState(initialSearch || hlSeed);
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" } | null>(initialSort || null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(pageSizeDefault);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // Print (2026-10-24): render every filtered row (not just this page), print, then restore.
  // Prints ONLY this grid (printRegion), landscape when it has many columns.
  const [printAll, setPrintAll] = useState(false);
  const gridRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!printAll) return;
    const t = setTimeout(() => { printRegion(gridRef.current, { landscape: allColumns.length > 8 }); setPrintAll(false); }, 50);
    return () => clearTimeout(t);
  }, [printAll]);
  useEffect(() => { if (initialSearch !== undefined) setQ(initialSearch); }, [initialSearch]);
  useEffect(() => {
    if (!hlSeed || q !== hlSeed || !rows.length) return;
    const n = hlSeed.toLowerCase();
    const hit = rows.some((r) => allColumns.some((c) => String(raw(c, r) ?? "").toLowerCase().includes(n)) || (!!searchText && searchText(r).toLowerCase().includes(n)));
    if (!hit) setQ("");
  }, [hlSeed, rows]);   // eslint-disable-line react-hooks/exhaustive-deps

  const columns = useMemo(() => columnVisibility ? allColumns.filter((c) => columnVisibility[c.key] !== false) : allColumns, [allColumns, columnVisibility]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    let out = !needle ? rows : rows.filter((r) =>
      columns.some((c) => String(raw(c, r) ?? "").toLowerCase().includes(needle))
      || (!!searchText && searchText(r).toLowerCase().includes(needle)));
    if (sort) {
      const c = columns.find((x) => x.key === sort.key);
      if (c) {
        const numeric = NUMERIC.has(c.type || "");
        out = [...out].sort((a, b) => {
          const av = raw(c, a), bv = raw(c, b);
          const an = av === null || av === undefined || av === "", bn = bv === null || bv === undefined || bv === "";
          if (an || bn) return an === bn ? 0 : an ? 1 : -1;          // blanks last, whichever way
          const cmp = numeric ? Number(av) - Number(bv) : String(av).localeCompare(String(bv), undefined, { numeric: true });
          return sort.dir === "asc" ? cmp : -cmp;
        });
      }
    }
    if (groupBy) {
      // Stable: keep the sort order inside each group, order the groups A→Z (blank group last).
      const g = (r: T) => groupBy.key(r) || "";
      out = [...out].sort((a, b) => {
        const ga = g(a), gb = g(b);
        if (ga === gb) return 0;
        if (!ga) return 1; if (!gb) return -1;
        return ga.localeCompare(gb, undefined, { numeric: true });
      });
    }
    return out;
  }, [rows, columns, q, sort, groupBy, searchText]);

  const groupSizes = useMemo(() => {
    const m = new Map<string, number>();
    if (groupBy) for (const r of filtered) { const k = groupBy.key(r) || ""; m.set(k, (m.get(k) || 0) + 1); }
    return m;
  }, [filtered, groupBy]);

  useEffect(() => { setPage(1); }, [q, pageSize, rows.length]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const cur = Math.min(page, pageCount);
  const paged = printAll ? filtered : filtered.slice((cur - 1) * pageSize, cur * pageSize);

  const flip = (key: string) => setSort((s) => s?.key !== key ? { key, dir: "asc" } : s.dir === "asc" ? { key, dir: "desc" } : null);

  // ── Selection helpers (only used when `selectable`) ──
  const pageKeys = paged.map((r, i) => rowKey(r, i));
  const sel = selected || new Set<string>();
  const allOnPage = pageKeys.length > 0 && pageKeys.every((k) => sel.has(k));
  const someOnPage = !allOnPage && pageKeys.some((k) => sel.has(k));
  const toggleOne = (k: string) => {
    const next = new Set(sel); next.has(k) ? next.delete(k) : next.add(k); onSelectionChange?.(next);
  };
  const togglePage = () => {
    const next = new Set(sel);
    if (allOnPage) pageKeys.forEach((k) => next.delete(k)); else pageKeys.forEach((k) => next.add(k));
    onSelectionChange?.(next);
  };
  const toggleGroup = (g: string) => setCollapsed((s) => { const n = new Set(s); n.has(g) ? n.delete(g) : n.add(g); return n; });

  const cellPad = density === "compact" ? "px-2.5 py-0.5" : "px-3 py-1.5";
  const textSize = density === "compact" ? "text-[12px]" : "text-[13px]";
  const colSpan = columns.length + (selectable ? 1 : 0);

  const body: React.ReactNode[] = [];
  let lastGroup: string | null = null;
  paged.forEach((r, i) => {
    const k = rowKey(r, i);
    if (groupBy) {
      const g = groupBy.key(r) || "";
      if (g !== lastGroup) {
        lastGroup = g;
        const isCollapsed = collapsed.has(g);
        body.push(
          <tr key={`__g_${g}`} className="border-t border-surface-border bg-surface">
            <td colSpan={colSpan} className="px-3 py-1">
              <button type="button" onClick={() => toggleGroup(g)} className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-700 hover:text-primary-600">
                <ChevronRight className={`w-3.5 h-3.5 transition-transform ${isCollapsed ? "" : "rotate-90"}`} />
                {groupBy.label ? <span className="text-gray-400 font-normal">{groupBy.label}:</span> : null}
                <span>{g || "(none)"}</span>
                <span className="text-[11px] font-medium px-1.5 py-0.5 rounded-full bg-primary-50 text-primary-600 tabular-nums">{groupSizes.get(g) || 0}</span>
              </button>
            </td>
          </tr>,
        );
      }
      if (collapsed.has(g)) return;
    }
    const isSel = selectable && sel.has(k);
    body.push(
      <tr key={k} onClick={onRowClick ? () => onRowClick(r) : undefined} aria-selected={selectable ? isSel : undefined}
        className={`border-t border-surface-border hover:bg-surface/60 ${onRowClick ? "cursor-pointer" : ""} ${isSel ? "bg-primary-50/60" : ""} ${rowClassName ? rowClassName(r) : ""}`}>
        {selectable && (
          <td className={`${cellPad} align-top w-8`} onClick={(e) => e.stopPropagation()}>
            <input type="checkbox" className="rounded border-gray-300 text-primary-600 focus:ring-primary-200" checked={isSel} onChange={() => toggleOne(k)} aria-label="Select row" />
          </td>
        )}
        {columns.map((c) => (
          <td key={c.key} className={`${cellPad} align-top ${NUMERIC.has(c.type || "") ? "text-right tabular-nums" : ""} ${c.className || "text-gray-700"}`}>
            {c.render ? c.render(r) : formatCell(r[c.key], c.type)}
          </td>
        ))}
      </tr>,
    );
  });

  return (
    <div className="card !p-0" ref={gridRef}>
      {exportName && <div className="lx-print-only px-1 pb-2 text-sm font-semibold text-gray-800">{exportName.replace(/[-_]+/g, " ")} · {filtered.length} row{filtered.length === 1 ? "" : "s"}{q.trim() ? ` matching "${q.trim()}"` : ""} · printed {new Date().toLocaleString("en-CA")}</div>}
      <div className="lx-no-print flex flex-wrap items-center gap-2 px-3 py-2.5 border-b border-surface-border">
        <div className="relative w-full sm:w-64">
          <Search className="w-4 h-4 text-gray-300 absolute left-3 top-1/2 -translate-y-1/2" />
          <input className="input !pl-9 !pr-8 !py-1.5 text-sm w-full" placeholder={searchPlaceholder} value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search" />
          {q && <button className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600" onClick={() => setQ("")} aria-label="Clear search"><X className="w-3.5 h-3.5" /></button>}
        </div>
        {filters}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-gray-400 tabular-nums">{filtered.length === rows.length ? `${rows.length} row${rows.length === 1 ? "" : "s"}` : `${filtered.length} of ${rows.length}`}</span>
          {toolbar}
          {exportName && (
            <button className="btn-secondary !py-1.5 !px-2.5 text-xs inline-flex items-center gap-1.5" disabled={!filtered.length}
              onClick={() => downloadCsv(exportName, toCsv(columns, filtered, q.trim() ? null : totals))}
              title="Download what is shown as a CSV file">
              <Download className="w-3.5 h-3.5" /> Export
            </button>
          )}
          {exportName && (
            <button className="btn-secondary !py-1.5 !px-2.5 text-xs inline-flex items-center gap-1.5" disabled={!filtered.length}
              onClick={() => setPrintAll(true)} title="Print every row shown (A4)">
              <Printer className="w-3.5 h-3.5" /> Print
            </button>
          )}
        </div>
      </div>

      {rows.length === 0 ? <Empty>{empty}</Empty>
        : filtered.length === 0 ? <Empty>No rows match your search.</Empty>
        : (
          <>
            <div className={maxHeight ? "overflow-auto" : "overflow-x-auto"} style={maxHeight ? { maxHeight } : undefined}>
              <table className={`w-full ${textSize}`} style={{ minWidth }}>
                <thead className={`bg-surface text-gray-500 text-left text-[11px] uppercase tracking-wide ${stickyHeader ? "sticky top-0 z-10" : ""}`}>
                  <tr>
                    {selectable && (
                      <th className={`${cellPad} w-8`}>
                        <input type="checkbox" className="rounded border-gray-300 text-primary-600 focus:ring-primary-200" checked={allOnPage}
                          ref={(el) => { if (el) el.indeterminate = someOnPage; }} onChange={togglePage} aria-label="Select all on this page" title="Select all on this page" />
                      </th>
                    )}
                    {columns.map((c) => {
                      const numeric = NUMERIC.has(c.type || "");
                      const can = c.sortable !== false;
                      const on = sort?.key === c.key;
                      return (
                        <th key={c.key} style={c.width ? { width: c.width } : undefined}
                          className={`${density === "compact" ? "px-2.5 py-1.5" : "px-3 py-2"} font-semibold whitespace-nowrap ${numeric ? "text-right" : ""}`}
                          aria-sort={on ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}>
                          {can ? (
                            <button className={`inline-flex items-center gap-1 uppercase tracking-wide hover:text-gray-700 ${on ? "text-primary-600" : ""}`}
                              onClick={() => flip(c.key)} title="Sort">
                              {c.label}{on && (sort!.dir === "asc" ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />)}
                            </button>
                          ) : c.label}
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>{body}</tbody>
                {totals && !q.trim() && (
                  <tfoot>
                    <tr className="border-t-2 border-surface-border bg-surface font-semibold text-gray-800">
                      {selectable && <td />}
                      {columns.map((c) => (
                        <td key={c.key} className={`px-3 py-2 ${NUMERIC.has(c.type || "") ? "text-right tabular-nums" : ""}`}>
                          {totals[c.key] === undefined ? "" : formatCell(totals[c.key], c.type)}
                        </td>
                      ))}
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
            <div className="lx-no-print"><Pager total={filtered.length} page={cur} pageSize={pageSize} onPage={setPage} onPageSize={setPageSize} /></div>
          </>
        )}
    </div>
  );
}

/** A wide dialog that uses the screen (never a narrow column). */
// 2026-10-24: thin wrapper over the app-wide AppModal (default "wide" = max-w-6xl).
export function WideModal({ title, onClose, children, footer, size = "wide", subtitle, printable, actions }:
  { title: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; size?: ModalSize; subtitle?: React.ReactNode; printable?: boolean; actions?: React.ReactNode }) {
  return (
    <AppModal title={title} subtitle={subtitle} onClose={onClose} footer={footer} size={size} printable={printable} actions={actions}>
      {children}
    </AppModal>
  );
}

export function Tabs<K extends string>({ tabs, value, onChange }:
  { tabs: { key: K; label: string; count?: number }[]; value: K; onChange: (k: K) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto mb-4 border-b border-surface-border" role="tablist">
      {tabs.map((t) => (
        <button key={t.key} role="tab" aria-selected={value === t.key} onClick={() => onChange(t.key)}
          className={`px-3 py-2 text-sm font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${value === t.key ? "border-primary-500 text-primary-600" : "border-transparent text-gray-500 hover:text-gray-700"}`}>
          {t.label}{t.count ? <span className="ml-1.5 text-[11px] px-1.5 py-0.5 rounded-full bg-primary-50 text-primary-600 tabular-nums">{t.count}</span> : null}
        </button>
      ))}
    </div>
  );
}
