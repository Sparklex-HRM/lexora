import { Fragment } from "react";
import type { Mode } from "../../../api/agentic";

export const STATUS_TONE: Record<string, string> = {
  proposed: "bg-amber-100 text-amber-700", second_approval: "bg-orange-100 text-orange-700", edited: "bg-amber-100 text-amber-700",
  approved: "bg-sky-100 text-sky-700", executed: "bg-green-100 text-green-700", denied: "bg-red-100 text-red-700", failed: "bg-red-100 text-red-700",
  expired: "bg-gray-100 text-gray-500", shadow: "bg-violet-100 text-violet-700",
  open: "bg-amber-100 text-amber-700", waiting: "bg-sky-100 text-sky-700", escalated: "bg-red-100 text-red-700", done: "bg-green-100 text-green-700",
  cancelled: "bg-gray-100 text-gray-500", running: "bg-sky-100 text-sky-700", error: "bg-red-100 text-red-700",
};
export const Pill = ({ v, map = STATUS_TONE }: { v: string | null | undefined; map?: Record<string, string> }) =>
  v ? <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${map[v] || "bg-gray-100 text-gray-600"}`}>{v.replace(/_/g, " ")}</span> : <span className="text-gray-300">—</span>;

const TIER_TONE: Record<number, string> = { 0: "bg-gray-100 text-gray-600", 1: "bg-sky-100 text-sky-700", 2: "bg-red-100 text-red-700" };
const TIER_TEXT: Record<number, string> = { 0: "Tier 0 · auto, logged", 1: "Tier 1 · one approver", 2: "Tier 2 · two approvers" };
export const TierBadge = ({ tier, untrusted }: { tier: number; untrusted?: boolean }) => (
  <span className="inline-flex items-center gap-1">
    <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${TIER_TONE[tier] || "bg-gray-100 text-gray-600"}`}>{TIER_TEXT[tier] || `Tier ${tier}`}</span>
    {untrusted && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-orange-50 text-orange-700 border border-orange-200" title="The run contained untrusted content (uploaded file, inbound mail, portal text) — every outbound action was raised one tier.">+1 untrusted</span>}
  </span>
);

export const ModeBadge = ({ mode, enabled = true }: { mode: Mode; enabled?: boolean }) => (
  !enabled ? <span className="badge-amber">Add-on off</span>
    : mode === "live" ? <span className="badge-green">Live</span> : <span className="badge-purple">Shadow</span>
);

export const fmtVal = (v: any): string => v === null || v === undefined || v === "" ? "—" : typeof v === "number" ? v.toLocaleString("en-CA", { maximumFractionDigits: 2 }) : typeof v === "boolean" ? (v ? "yes" : "no") : typeof v === "object" ? JSON.stringify(v) : String(v);

/** Before / after side by side — only the keys that changed are highlighted. */
export function Diff({ before, after }: { before: Record<string, any> | null; after: Record<string, any> | null }) {
  if (!before && !after) return <p className="text-xs text-gray-400">No record change — this action prepares or reads only.</p>;
  const keys = Array.from(new Set([...Object.keys(before || {}), ...Object.keys(after || {})]));
  return (
    <div className="overflow-x-auto card !p-0">
      <table className="w-full text-[13px]">
        <thead className="bg-surface text-[11px] uppercase tracking-wide text-gray-500 text-left"><tr><th className="px-3 py-2">Field</th><th className="px-3 py-2">Before</th><th className="px-3 py-2">After</th></tr></thead>
        <tbody>{keys.map((k) => { const ch = JSON.stringify(before?.[k]) !== JSON.stringify(after?.[k]); return (
          <tr key={k} className={`border-t border-surface-border ${ch ? "bg-amber-50/50" : ""}`}>
            <td className="px-3 py-1.5 text-gray-600">{k.replace(/_/g, " ")}</td>
            <td className="px-3 py-1.5 tabular-nums text-gray-700">{fmtVal(before?.[k])}</td>
            <td className={`px-3 py-1.5 tabular-nums ${ch ? "font-semibold text-gray-900" : "text-gray-700"}`}>{fmtVal(after?.[k])}</td>
          </tr>); })}</tbody>
      </table>
    </div>
  );
}

export function KV({ obj, empty = "—" }: { obj: Record<string, any> | null | undefined; empty?: string }) {
  const entries = Object.entries(obj || {});
  if (!entries.length) return <span className="text-xs text-gray-400">{empty}</span>;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      {entries.map(([k, v]) => <Fragment key={k}><dt className="text-gray-500 whitespace-nowrap">{k.replace(/_/g, " ")}</dt><dd className="text-gray-800 font-mono break-all">{fmtVal(v)}</dd></Fragment>)}
    </dl>
  );
}
