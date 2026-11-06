import React from "react";
import { LucideIcon } from "lucide-react";

/** Reusable primitives in the exact Medfidelity HMS vocabulary
 *  (.card / font-display headings / badge-* / primary-tinted tiles). */

export function Card({ children, className = "", padded = true }:
  { children: React.ReactNode; className?: string; padded?: boolean }) {
  return <div className={`card ${padded ? "" : "!p-0"} ${className}`}>{children}</div>;
}

export function PageHeader({ title, subtitle, icon: Icon, actions }:
  { title: string; subtitle?: string; icon?: LucideIcon; actions?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 mb-5">
      <div className="flex items-center gap-3 min-w-0">
        {Icon && (
          <div className="w-10 h-10 rounded-xl bg-primary-50 flex items-center justify-center flex-shrink-0">
            <Icon className="w-5 h-5 text-primary-600" />
          </div>
        )}
        <div className="min-w-0">
          <h1 className="font-display text-xl font-bold text-gray-800 truncate">{title}</h1>
          {subtitle && <p className="text-sm text-gray-500">{subtitle}</p>}
        </div>
      </div>
      {actions}
    </div>
  );
}

export function StatTile({ label, value, sub, icon: Icon, accent }:
  { label: string; value: React.ReactNode; sub?: string; icon?: LucideIcon; accent?: boolean }) {
  return (
    <div className="card">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold text-gray-400 uppercase tracking-wide">{label}</span>
        {Icon && <Icon className={`w-4 h-4 ${accent ? "text-primary-500" : "text-gray-300"}`} />}
      </div>
      {/* Below 480px two tiles share a phone row (~130px of text each): a smaller size that
          never wraps, so "$9,440,741.89" is not broken across lines. */}
      <div className={`text-base xs:text-xl sm:text-2xl whitespace-nowrap font-bold mt-1.5 tabular-nums ${accent ? "text-primary-600" : "text-gray-800"}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  );
}

const TONE_CLASS: Record<string, string> = {
  active: "badge-green",
  onboarding: "badge-teal",
  on_leave: "badge-amber",
  terminated: "badge-red",
  default: "badge-purple",
};

export function Badge({ children, tone }: { children: React.ReactNode; tone?: string }) {
  const cls = TONE_CLASS[tone || "default"] || TONE_CLASS.default;
  return <span className={`${cls} capitalize`}>{String(children).replace(/_/g, " ")}</span>;
}

export function Spinner({ className = "h-6 w-6" }: { className?: string }) {
  return <div className={`animate-spin rounded-full border-[3px] border-primary-500 border-t-transparent ${className}`} />;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="text-center py-12 text-sm text-gray-400">{children}</div>;
}

/** "Coming in Phase X" panel used by the module stubs. */
export function ComingSoon({ icon: Icon, title, phase, points }:
  { icon: LucideIcon; title: string; phase: string; points: string[] }) {
  return (
    <div className="card max-w-2xl">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-11 h-11 rounded-xl bg-primary-100 flex items-center justify-center">
          <Icon className="w-5.5 h-5.5 text-primary-600" />
        </div>
        <div>
          <h2 className="font-display text-lg font-bold text-gray-800">{title}</h2>
          <span className="badge-purple mt-1">{phase}</span>
        </div>
      </div>
      <p className="text-sm text-gray-500 mb-3">
        Scaffolded and wired into the gateway. The build plan for this module:
      </p>
      <ul className="space-y-2">
        {points.map((p, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-gray-700">
            <span className="text-primary-500 mt-0.5">▸</span><span>{p}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
