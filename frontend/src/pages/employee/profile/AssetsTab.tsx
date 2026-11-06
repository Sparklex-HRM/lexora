import { Link } from "react-router-dom";
import type { TabProps } from "../EmployeeProfilePage";
import { Denied, EmptyLine, Section, TD, TH, fmtDate, titleCase, tone } from "./_bits";

/** Assets issued to this employee (from the aggregated profile — the same rows as
 *  GET /assets?employee_id=). HR / managers manage them in Engage → Assets. */
export default function AssetsTab({ profile }: TabProps) {
  const rows = profile.assets;
  const canManage = profile.permissions.can_hr_write || ["manager", "payroll_admin"].includes(profile.permissions.role);
  const open = (rows || []).filter((a) => !a.returned).length;
  return (
    <Section title={`Assets${rows ? ` · ${open} in hand, ${(rows.length - open)} returned` : ""}`} flush
      action={canManage ? <Link to="/engage/assets" className="text-xs text-primary-600 font-medium hover:underline">Asset register →</Link>
        : profile.permissions.is_self ? <Link to="/engage/my-assets" className="text-xs text-primary-600 font-medium hover:underline">My assets →</Link> : null}>
      {rows === null ? <Denied what="assets" />
        : !rows.length ? <EmptyLine action={canManage ? "Issue an asset" : undefined} to="/engage/assets">Nothing has been issued to this employee.</EmptyLine>
        : (
          <table className="w-full text-xs min-w-[760px]">
            <thead className="bg-surface text-gray-400 text-[10px] uppercase"><tr>
              <th className={TH}>Item</th><th className={TH}>Tag</th><th className={TH}>Category</th><th className={TH}>Serial</th><th className={TH}>Issued on</th>
              <th className={TH}>Acknowledged</th><th className={TH}>Status</th><th className={TH}>Returned on</th><th className={TH}>Condition</th><th className={TH}>Warranty</th></tr></thead>
            <tbody>{rows.map((a: any) => (
              <tr key={a.id} className="border-t border-surface-border">
                <td className={`${TD} font-medium text-gray-800`}>{a.item}</td>
                <td className={`${TD} font-mono text-[11px]`}>{a.tag || "—"}</td>
                <td className={TD}>{a.category || "—"}</td>
                <td className={`${TD} font-mono text-[11px]`}>{a.serial || "—"}</td>
                <td className={TD}>{fmtDate(a.issued_on)}</td>
                <td className={TD}>{a.acknowledged ? <span className="badge-green">Yes</span> : a.returned ? "—" : <span className="badge-amber">Pending</span>}</td>
                <td className={TD}><span className={tone(a.status)}>{titleCase(a.status)}</span></td>
                <td className={TD}>{fmtDate(a.returned_on)}</td>
                <td className={TD}>{a.return_condition || a.condition || "—"}</td>
                <td className={TD}>{a.warranty_until ? `${fmtDate(a.warranty_until)} (${titleCase(a.warranty_state)})` : "—"}</td>
              </tr>))}</tbody>
          </table>
        )}
    </Section>
  );
}
