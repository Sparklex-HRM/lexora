import { useMemo, type ComponentType } from "react";
import { Link } from "react-router-dom";
import { UserPlus, Wallet, ArrowUpRight, FileSignature, BadgeCheck, UserRoundX, Package, CalendarClock } from "lucide-react";
import { cur, REASONS } from "../../../api/lifecycle";
import type { TabProps } from "../EmployeeProfilePage";
import { Section, fmtDate, titleCase } from "./_bits";

/** Everything that happened to this employee, newest first, from the data the
 *  profile already holds: joining, salary rows, career changes, probation steps,
 *  letters, assets, leaving. HR sees drafts too; the employee sees applied / issued. */
type Ev = { at: string; kind: string; title: string; detail?: string; by?: string | null; icon: ComponentType<{ className?: string }>; color: string; status?: string };

export default function TimelineTab({ profile }: TabProps) {
  const e = profile.employee;
  const events = useMemo<Ev[]>(() => {
    const out: Ev[] = [];
    if (e.hire_date) out.push({ at: e.hire_date, kind: "hire", title: "Joined", detail: `${e.job_title || "No designation"} · ${e.department || "No department"}`, icon: UserPlus, color: "bg-emerald-500" });
    if (e.status === "terminated" && e.termination_date) out.push({ at: e.termination_date, kind: "exit", title: "Left the company", icon: UserRoundX, color: "bg-red-500" });
    const currency = profile.compensation?.current?.currency;
    for (const h of profile.compensation?.history || []) {
      const hourly = h.pay_type === "hourly";
      const amt = cur(hourly ? h.hourly_rate : h.annual_amount, h.currency || currency) + (hourly ? " / hour" : " / year");
      const change = h.change_amount !== undefined && h.change_amount !== null ? ` (${h.change_amount >= 0 ? "+" : ""}${cur(h.change_amount, h.currency || currency)}${h.change_percent != null ? `, ${Number(h.change_percent).toFixed(1)}%` : ""})` : "";
      out.push({ at: h.effective_from, kind: "pay", title: h.reason === "opening" ? `Opening salary ${amt}` : `Salary ${amt}${change}`,
        detail: `${REASONS[h.reason] || titleCase(h.reason)}${h.note ? ` · ${h.note}` : ""}`, by: h.approved_by || h.created_by, icon: Wallet, color: "bg-primary-500" });
    }
    for (const c of profile.career_changes || []) {
      out.push({ at: c.effective_date, kind: "career", title: c.type_label, detail: c.summary || c.reason || undefined, by: c.decided_by || c.created_by, icon: ArrowUpRight, color: "bg-violet-500", status: c.status });
    }
    for (const p of profile.career?.probation_history || []) {
      if (!p.at) continue;
      out.push({ at: p.at, kind: "probation", title: `Probation: ${titleCase(p.action)}${p.recommendation ? ` (${titleCase(p.recommendation)})` : ""}`,
        detail: p.confirmation_date ? `Confirmed from ${fmtDate(p.confirmation_date)}` : p.new_end_date ? `End date moved to ${fmtDate(p.new_end_date)}` : p.reason || undefined, by: p.actor, icon: BadgeCheck, color: "bg-teal-500" });
    }
    if (profile.career?.confirmation_date) out.push({ at: profile.career.confirmation_date, kind: "confirmed", title: "Confirmed in service", icon: BadgeCheck, color: "bg-emerald-500" });
    for (const l of profile.letters || []) {
      out.push({ at: l.issued_at || l.letter_date || l.created_at, kind: "letter", title: `${l.name || "Letter"}${l.number ? ` · ${l.number}` : ""}`, detail: l.status === "issued" ? undefined : titleCase(l.status), by: l.issued_by || l.created_by, icon: FileSignature, color: "bg-amber-500", status: l.status });
    }
    for (const a of profile.assets || []) {
      if (a.issued_on) out.push({ at: a.issued_on, kind: "asset", title: `Asset issued: ${a.item}`, detail: a.tag || a.serial || undefined, by: a.assigned_by, icon: Package, color: "bg-sky-500" });
      if (a.returned_on) out.push({ at: a.returned_on, kind: "asset", title: `Asset returned: ${a.item}`, detail: a.return_condition || undefined, by: a.returned_to, icon: Package, color: "bg-gray-400" });
    }
    return out.filter((x) => x.at).sort((a, b) => String(b.at).localeCompare(String(a.at)));
  }, [profile, e]);

  if (!events.length) {
    return <Section title="Timeline"><p className="text-center py-8 text-sm text-gray-400">Nothing recorded yet.</p></Section>;
  }
  return (
    <Section title={`Timeline · ${events.length} ${events.length === 1 ? "event" : "events"}`}
      action={(profile.permissions.can_hr_write || profile.permissions.role === "payroll_admin") && <Link to={`/employee-history/${e.id}`} className="text-xs text-primary-600 font-medium hover:underline">Full history tables →</Link>}>
      <ol className="relative border-l border-surface-border ml-3">
        {events.map((ev, i) => (
          <li key={i} className="ml-6 pb-4 last:pb-0">
            <span className={`absolute -left-[11px] mt-0.5 w-5 h-5 rounded-full ${ev.color} text-white grid place-items-center ring-4 ring-white`}><ev.icon className="w-3 h-3" /></span>
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="text-xs text-gray-400 tabular-nums flex items-center gap-1"><CalendarClock className="w-3 h-3" />{fmtDate(ev.at)}</span>
              <span className="text-sm font-medium text-gray-800">{ev.title}</span>
              {ev.status && ev.status !== "applied" && ev.status !== "issued" && <span className="badge-amber">{ev.status}</span>}
            </div>
            {(ev.detail || ev.by) && <div className="text-xs text-gray-500 mt-0.5">{ev.detail}{ev.detail && ev.by ? " · " : ""}{ev.by && <span className="text-gray-400">by {ev.by}</span>}</div>}
          </li>
        ))}
      </ol>
    </Section>
  );
}
