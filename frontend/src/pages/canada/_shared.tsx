import React from "react";
import clsx from "clsx";
import AppModal from "../../components/Modal";
export { Th, Panel, Hint, Tile, exportCsv, fmtDate } from "../../components/migration/shared";

/** Dense cell (same look as the migration grid) + colSpan for footer / empty rows. */
export function Td({ children, className = "", right = false, mono = false, title, colSpan }:
  { children?: React.ReactNode; className?: string; right?: boolean; mono?: boolean; title?: string; colSpan?: number }) {
  return <td title={title} colSpan={colSpan} className={clsx("px-2 py-1 text-[12px] text-gray-700 whitespace-nowrap border-t border-surface-border/60", right && "text-right", mono && "tabular-nums", className)}>{children}</td>;
}

/** Canada pages: dense grids, wide modals, plain sentences. */
export const cad = (n: number | null | undefined, d = 2) =>
  "CA$" + (Number(n) || 0).toLocaleString("en-CA", { minimumFractionDigits: d, maximumFractionDigits: d });

export function StatusPill({ status }: { status: string }) {
  const cls: Record<string, string> = { verified: "badge-green", entered: "badge-amber", stale: "badge-red", blank: "badge-purple",
    live: "badge-green", test: "badge-amber", match: "badge-green", mismatch: "badge-red", signed: "badge-green", compared: "badge-teal",
    uploaded: "badge-amber", committed: "badge-green", rolled_back: "badge-red", validated: "badge-teal",
    missing_in_lexora: "badge-red", missing_in_register: "badge-amber", not_in_register: "badge-purple" };
  return <span className={clsx(cls[status] || "badge-purple", "whitespace-nowrap")}>{status.replace(/_/g, " ")}</span>;
}

/** The gate stamp every estimate carries (same shape as the payroll run stamp). */
export function GateBanner({ gate }: { gate?: { verified: boolean; mode: string; constants_verified: number; constants_total: number; reasons: string[] } | null }) {
  if (!gate) return null;
  return (
    <div className={clsx("text-[12px] rounded-lg border px-3 py-2 leading-snug", gate.verified ? "bg-emerald-50 text-emerald-800 border-emerald-200" : "bg-amber-50 text-amber-800 border-amber-200")}>
      <b>{gate.verified ? "Verified values" : "TEST mode"}</b> · {gate.constants_verified}/{gate.constants_total} org values verified.
      {!gate.verified && gate.reasons?.length > 0 && <ul className="mt-1 list-disc pl-4">{gate.reasons.slice(0, 6).map((r, i) => <li key={i}>{r}</li>)}</ul>}
    </div>
  );
}

// 2026-10-24: thin wrapper over the app-wide AppModal (width string kept for callers).
export function Modal({ title, sub, onClose, children, footer, width = "max-w-5xl" }:
  { title: string; sub?: string; onClose: () => void; children: React.ReactNode; footer?: React.ReactNode; width?: string }) {
  const size = /7xl|screen|none/.test(width) ? "xwide" : /6xl|5xl/.test(width) ? "wide" : "form";
  return <AppModal title={title} subtitle={sub} onClose={onClose} footer={footer} size={size}>{children}</AppModal>;
}

export function Field({ label, children, className = "" }: { label: string; children: React.ReactNode; className?: string }) {
  return <label className={clsx("block", className)}><span className="block text-[11px] font-semibold text-gray-500 uppercase tracking-wide mb-1">{label}</span>{children}</label>;
}

export const inputCls = "input-base h-8 text-xs w-full";

export function SearchBox({ value, onChange, placeholder = "Search…" }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return <input className="input-base h-8 text-xs w-56" placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />;
}

export function err(e: any, fallback = "Something went wrong") {
  const d = e?.response?.data?.detail;
  return typeof d === "string" ? d : d?.message || fallback;
}
