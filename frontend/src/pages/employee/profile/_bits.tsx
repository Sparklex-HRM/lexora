import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { Lock } from "lucide-react";

/** Shared pieces for the Employee 360 tabs (stream U1): the dense label:value
 *  grid, section cards, the deterministic avatar, and a query hook that turns a
 *  403/404 into a plain "no access" sentence instead of a crash. */

export const LABEL = "block text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-0.5";
export const TH = "px-3 py-2 text-left font-semibold whitespace-nowrap";
export const TD = "px-3 py-1.5 whitespace-nowrap text-gray-700";

export const fmtDate = (s?: string | null) => {
  if (!s) return "—";
  const dt = new Date(s.length <= 10 ? `${s}T00:00:00` : s);
  return isNaN(dt.getTime()) ? s : dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
};
export const fmtDateTime = (s?: string | null) => {
  if (!s) return "—";
  const dt = new Date(s);
  return isNaN(dt.getTime()) ? s : dt.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
};
export const titleCase = (s?: string | null) => (s ? s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "");
export const dash = (v: any): ReactNode => (v === null || v === undefined || v === "" ? <span className="text-gray-300">—</span> : v);

/* ── Avatar: initials on a colour picked from the employee number ── */
const PALETTE = ["bg-primary-600", "bg-teal-500", "bg-emerald-600", "bg-amber-500", "bg-rose-500", "bg-violet-600", "bg-sky-600", "bg-orange-500"];
export const avatarColor = (seed: string) => {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
};
export const initials = (first?: string | null, last?: string | null) =>
  `${(first || "").trim()[0] || ""}${(last || "").trim()[0] || ""}`.toUpperCase() || "?";
export function Avatar({ first, last, seed, size = "lg" }: { first?: string | null; last?: string | null; seed: string; size?: "sm" | "lg" }) {
  const cls = size === "lg" ? "w-16 h-16 text-xl" : "w-8 h-8 text-xs";
  return <div className={`${cls} ${avatarColor(seed)} rounded-2xl text-white font-display font-bold grid place-items-center shrink-0 select-none`}>{initials(first, last)}</div>;
}

/* ── Dense label : value grid (2 / 3 / 4 columns) ── */
export function Field({ label, children, wide, mono }: { label: string; children?: ReactNode; wide?: boolean; mono?: boolean }) {
  const isText = typeof children === "string" || typeof children === "number";   // placeholders stay in the normal font
  return (
    <div className={`min-w-0 ${wide ? "col-span-2" : ""}`}>
      <span className={LABEL}>{label}</span>
      <div className={`text-sm text-gray-800 break-words ${mono && isText ? "font-mono text-[13px]" : ""}`}>{dash(children)}</div>
    </div>
  );
}
export function Grid({ children, cols = 4 }: { children: ReactNode; cols?: 2 | 3 | 4 }) {
  const c = cols === 2 ? "grid-cols-2" : cols === 3 ? "grid-cols-2 md:grid-cols-3" : "grid-cols-2 md:grid-cols-3 xl:grid-cols-4";
  return <div className={`grid ${c} gap-x-5 gap-y-3`}>{children}</div>;
}

/* ── Section card with a title row and an optional right-hand action ── */
export function Section({ title, children, action, flush, className = "" }:
  { title: string; children: ReactNode; action?: ReactNode; flush?: boolean; className?: string }) {
  return (
    <div className={`card !p-0 ${className}`}>
      <div className="px-4 py-2.5 border-b border-surface-border flex items-center justify-between gap-3">
        <h3 className="font-display font-semibold text-gray-800 text-sm">{title}</h3>
        {action}
      </div>
      <div className={flush ? "overflow-x-auto" : "p-4"}>{children}</div>
    </div>
  );
}

/** Plain empty state: one sentence + the action that creates the data. */
export function EmptyLine({ children, action, to }: { children: ReactNode; action?: string; to?: string }) {
  return (
    <div className="text-center py-6 text-sm text-gray-400">
      {children}
      {action && to && <> <Link to={to} className="text-primary-600 font-medium hover:underline">{action}</Link></>}
    </div>
  );
}

/** "You cannot see this" line — the API said 403 / 404 for this section. */
export function Denied({ what = "this section" }: { what?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-6 text-sm text-gray-400">
      <Lock className="w-4 h-4" /> You do not have access to {what}.
    </div>
  );
}

export function Loading() {
  return <div className="py-6 text-center text-sm text-gray-400">Loading…</div>;
}

/** A query whose 403 / 404 becomes `denied` (the tab renders a sentence, not an error). */
export function useTabQuery<T>(key: any[], fn: () => Promise<T>, enabled = true, extra: Partial<UseQueryOptions<T>> = {}) {
  const q = useQuery<T>({ queryKey: key, queryFn: fn, enabled, retry: false, ...(extra as any) });
  const status = (q.error as any)?.response?.status;
  return { ...q, denied: status === 403 || status === 404, failed: !!q.error && status !== 403 && status !== 404 };
}

export const STATUS_TEXT: Record<string, string> = { onboarding: "Onboarding", active: "Active", on_leave: "On leave", terminated: "Left" };
export const tone = (s?: string | null) =>
  s === "applied" || s === "issued" || s === "approved" || s === "active" || s === "returned" || s === "present" ? "badge-green"
  : s === "rejected" || s === "cancelled" || s === "void" || s === "absent" || s === "lost" ? "badge-red"
  : s === "submitted" || s === "pending" || s === "draft" || s === "late" ? "badge-amber" : "badge-purple";
