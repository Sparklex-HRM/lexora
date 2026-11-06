import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { compApi, careerApi, lettersApi, openPdf, cur, fmtDate, pct, REASONS } from "../../api/lifecycle";
import toast from "react-hot-toast";
import { printPdfBlob } from "../../lib/print";

/** Employee history (Stream B): salary timeline, career changes, probation, letters.
 *  Drop it on any employee profile:  <EmployeeLifecyclePanel employeeId={id} />
 *  The API decides what the viewer may see (HR: everything · the employee: own, applied only). */

const SRC: Record<string, string> = { opening: "Opening salary", revision: "Salary revision", arrears: "Revision with arrears", direct_edit: "Edited on the employee record" };

function Section({ title, children, empty }: { title: string; children?: ReactNode; empty?: boolean }) {
  return (
    <div className="card !p-0 mb-4">
      <div className="px-4 py-2.5 border-b border-surface-border font-display font-semibold text-gray-800 text-sm">{title}</div>
      {empty ? <div className="text-center py-6 text-sm text-gray-400">Nothing here yet.</div> : <div className="overflow-x-auto">{children}</div>}
    </div>
  );
}
const th = "px-3 py-2 text-left font-semibold whitespace-nowrap";
const td = "px-3 py-1.5 whitespace-nowrap text-gray-700";

export function SalaryTimeline({ history, currency }: { history: any[]; currency?: string }) {
  return (
    <Section title="Salary history" empty={!history.length}>
      <table className="w-full text-xs">
        <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr>
          <th className={th}>From</th><th className={th}>To</th><th className={`${th} text-right`}>Salary</th><th className={`${th} text-right`}>Change</th>
          <th className={th}>Why</th><th className={th}>Recorded as</th><th className={th}>By</th><th className={th}>Approved by</th><th className={th}>Recorded on</th></tr></thead>
        <tbody>{history.map((h) => {
          const hourly = h.pay_type === "hourly";
          return (
            <tr key={h.id} className="border-t border-surface-border">
              <td className={td}>{fmtDate(h.effective_from)}</td><td className={td}>{h.effective_to ? fmtDate(h.effective_to) : "Now"}</td>
              <td className={`${td} text-right tabular-nums font-semibold`}>{cur(hourly ? h.hourly_rate : h.annual_amount, h.currency || currency)}{hourly ? " / hour" : " / year"}</td>
              <td className={`${td} text-right tabular-nums`}>{h.change_amount === undefined ? "—" : <span className={h.change_amount < 0 ? "text-red-600" : "text-emerald-700"}>{cur(h.change_amount, h.currency || currency)} ({pct(h.change_percent)})</span>}</td>
              <td className={td}>{REASONS[h.reason] || (h.reason || "").replace(/_/g, " ")}{h.note ? <span className="text-gray-400 ml-1" title={h.note}>ⓘ</span> : null}</td>
              <td className={td}>{SRC[h.source] || h.source}</td><td className={td}>{h.created_by || "—"}</td><td className={td}>{h.approved_by || "—"}</td><td className={td}>{fmtDate(h.created_at)}</td>
            </tr>);
        })}</tbody>
      </table>
    </Section>
  );
}

export function LetterList({ letters }: { letters: any[] }) {
  const open = async (l: any) => openPdf(await lettersApi.pdf(l.id), `${l.number || "letter"}.pdf`);
  const print = async (l: any) => { try { printPdfBlob(await lettersApi.pdf(l.id), `${l.number || "letter"}.pdf`); } catch { toast.error("Could not open the letter for printing"); } };
  return (
    <Section title="Letters" empty={!letters.length}>
      <table className="w-full text-xs">
        <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className={th}>Letter no</th><th className={th}>Date</th><th className={th}>Letter</th><th className={th}>Effective</th><th className={th}>Status</th><th className={th}></th></tr></thead>
        <tbody>{letters.map((l) => (
          <tr key={l.id} className="border-t border-surface-border">
            <td className={`${td} font-mono text-[11px]`}>{l.number || "draft"}</td><td className={td}>{fmtDate(l.letter_date)}</td><td className={td}>{l.name}</td>
            <td className={td}>{fmtDate(l.effective_date)}</td>
            <td className={td}><span className={l.status === "issued" ? "badge-green" : l.status === "void" ? "badge-red" : "badge-purple"}>{l.status}</span></td>
            <td className={td}><span className="inline-flex gap-1"><button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => open(l)}>Open PDF</button><button className="btn-secondary !py-0.5 !px-2 text-xs" title="Print this letter (A4)" onClick={() => print(l)}>Print</button></span></td>
          </tr>))}</tbody>
      </table>
    </Section>
  );
}

export default function EmployeeLifecyclePanel({ employeeId }: { employeeId: string }) {
  const { data: comp } = useQuery({ queryKey: ["emp-comp", employeeId], queryFn: () => compApi.employee(employeeId), enabled: !!employeeId });
  const { data: changes = [] } = useQuery({ queryKey: ["emp-career", employeeId], queryFn: () => careerApi.forEmployee(employeeId), enabled: !!employeeId });
  const { data: career } = useQuery({ queryKey: ["emp-career-summary", employeeId], queryFn: () => careerApi.summary(employeeId), enabled: !!employeeId });
  const { data: letters = [] } = useQuery({ queryKey: ["emp-letters", employeeId], queryFn: () => lettersApi.forEmployee(employeeId), enabled: !!employeeId });
  const prob = career?.probation_history || [];
  return (
    <div>
      {career && <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        {[["Grade", career.grade || "—"], ["Employment status", career.employment_status ? career.employment_status[0].toUpperCase() + career.employment_status.slice(1) : "Not set"],
          ["Probation ends", fmtDate(career.probation_end_date)], ["Confirmed on", fmtDate(career.confirmation_date)]].map(([l, v]) => (
          <div key={l} className="card !py-3"><div className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">{l}</div><div className="text-base font-bold mt-1 text-gray-800">{v}</div></div>))}
      </div>}
      <SalaryTimeline history={comp?.history || []} currency={comp?.current?.currency} />
      <Section title="Career changes" empty={!changes.length}>
        <table className="w-full text-xs">
          <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className={th}>Effective</th><th className={th}>Type</th><th className={th}>Change (old → new)</th><th className={th}>Reason</th><th className={th}>Status</th><th className={th}>By</th><th className={th}>Approved by</th></tr></thead>
          <tbody>{changes.map((c: any) => (
            <tr key={c.id} className="border-t border-surface-border"><td className={td}>{fmtDate(c.effective_date)}</td><td className={td}>{c.type_label}</td><td className={`${td} !whitespace-normal`}>{c.summary}</td>
              <td className={td}>{c.reason || "—"}</td><td className={td}><span className={c.status === "applied" ? "badge-green" : c.status === "rejected" || c.status === "cancelled" ? "badge-red" : "badge-amber"}>{c.status}</span></td>
              <td className={td}>{c.created_by || "—"}</td><td className={td}>{c.decided_by || "—"}</td></tr>))}</tbody>
        </table>
      </Section>
      {prob.length > 0 && <Section title="Probation">
        <table className="w-full text-xs">
          <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr><th className={th}>When</th><th className={th}>Step</th><th className={th}>Details</th><th className={th}>Reason</th><th className={th}>By</th></tr></thead>
          <tbody>{prob.map((p: any, i: number) => (
            <tr key={i} className="border-t border-surface-border"><td className={td}>{fmtDate(p.at)}</td><td className={td}>{(p.action || "").replace(/_/g, " ")}{p.recommendation ? `: ${p.recommendation.replace(/_/g, " ")}` : ""}</td>
              <td className={td}>{p.confirmation_date ? `Confirmed from ${fmtDate(p.confirmation_date)}` : p.new_end_date ? `End date ${fmtDate(p.old_end_date)} → ${fmtDate(p.new_end_date)}` : p.exit_handover || "—"}</td>
              <td className={`${td} !whitespace-normal`}>{p.reason || "—"}</td><td className={td}>{p.actor || "—"}</td></tr>))}</tbody>
        </table>
      </Section>}
      <LetterList letters={letters} />
    </div>
  );
}
