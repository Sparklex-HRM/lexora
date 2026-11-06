import React from "react";
import clsx from "clsx";
import { Check } from "lucide-react";

/** Small, dense building blocks for the migration wizard (app vocabulary: .card / badge-* / btn-*). */

export const STEPS = [
  { key: "templates", label: "Download templates" },
  { key: "upload", label: "Upload" },
  { key: "map", label: "Map columns" },
  { key: "fix", label: "Fix errors" },
  { key: "preview", label: "Preview" },
  { key: "import", label: "Import" },
  { key: "summary", label: "Summary" },
] as const;
export type StepKey = (typeof STEPS)[number]["key"];

export function StepBar({ current, done, onJump }: { current: StepKey; done: Set<StepKey>; onJump?: (k: StepKey) => void }) {
  const idx = STEPS.findIndex((s) => s.key === current);
  return (
    <ol className="flex flex-wrap items-center gap-1.5 text-xs">
      {STEPS.map((s, i) => {
        const isDone = done.has(s.key) && i < idx;
        const active = i === idx;
        return (
          <li key={s.key} className="flex items-center gap-1.5">
            <button type="button" disabled={!onJump || (!isDone && !active)} onClick={() => onJump?.(s.key)}
              className={clsx("flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium transition",
                active && "bg-primary-600 text-white shadow-sm",
                isDone && !active && "bg-emerald-50 text-emerald-700 hover:bg-emerald-100",
                !active && !isDone && "bg-gray-100 text-gray-400")}>
              <span className={clsx("w-4 h-4 rounded-full grid place-items-center text-[10px] font-bold",
                active ? "bg-white/20" : isDone ? "bg-emerald-500 text-white" : "bg-white text-gray-400")}>
                {isDone ? <Check className="w-3 h-3" /> : i + 1}
              </span>
              {s.label}
            </button>
            {i < STEPS.length - 1 && <span className="text-gray-300">›</span>}
          </li>
        );
      })}
    </ol>
  );
}

export function Th({ children, className = "", right = false, title }: { children?: React.ReactNode; className?: string; right?: boolean; title?: string }) {
  return <th title={title} className={clsx("px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 whitespace-nowrap bg-surface sticky top-0 z-[1]", right && "text-right", className)}>{children}</th>;
}
export function Td({ children, className = "", right = false, mono = false, title }: { children?: React.ReactNode; className?: string; right?: boolean; mono?: boolean; title?: string }) {
  return <td title={title} className={clsx("px-2 py-1 text-[12px] text-gray-700 whitespace-nowrap border-t border-surface-border/60", right && "text-right", mono && "tabular-nums", className)}>{children}</td>;
}

export function Tile({ label, value, tone = "default", sub }: { label: string; value: React.ReactNode; tone?: "default" | "ok" | "warn" | "bad" | "accent"; sub?: string }) {
  const toneCls = { default: "text-gray-800", ok: "text-emerald-600", warn: "text-amber-600", bad: "text-rose-600", accent: "text-primary-600" }[tone];
  return (
    <div className="rounded-lg border border-surface-border bg-surface-card px-3 py-2 min-w-[120px]">
      <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{label}</div>
      <div className={clsx("text-lg font-bold tabular-nums leading-tight mt-0.5", toneCls)}>{value}</div>
      {sub && <div className="text-[11px] text-gray-500">{sub}</div>}
    </div>
  );
}

export function Panel({ title, sub, children, actions, className = "" }: { title: string; sub?: string; children: React.ReactNode; actions?: React.ReactNode; className?: string }) {
  return (
    <section className={clsx("card !p-0 overflow-hidden", className)}>
      <header className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-surface-border bg-gradient-to-r from-primary-50/60 to-transparent">
        <div className="min-w-0">
          <h3 className="font-display font-semibold text-gray-800 text-sm leading-tight">{title}</h3>
          {sub && <p className="text-[11px] text-gray-500 truncate">{sub}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function Hint({ children, tone = "info" }: { children: React.ReactNode; tone?: "info" | "warn" | "bad" | "ok" }) {
  const cls = { info: "bg-sky-50 text-sky-800 border-sky-200", warn: "bg-amber-50 text-amber-800 border-amber-200",
    bad: "bg-rose-50 text-rose-800 border-rose-200", ok: "bg-emerald-50 text-emerald-800 border-emerald-200" }[tone];
  return <div className={clsx("text-[12px] rounded-lg border px-3 py-2 leading-snug", cls)}>{children}</div>;
}

/** Simple client-side filter + sort + CSV export for a dense grid. */
export function exportCsv(filename: string, header: string[], rows: (string | number | null | undefined)[][]) {
  const esc = (v: any) => { const s = v === null || v === undefined ? "" : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const text = [header, ...rows].map((r) => r.map(esc).join(",")).join("\n");
  const blob = new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 500);
}

export function fmtDate(s: string | null | undefined) {
  if (!s) return "";
  try { return new Date(s).toLocaleString("en-CA", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); } catch { return s; }
}
