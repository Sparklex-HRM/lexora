import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { ShieldAlert, CheckCircle2, Lock } from "lucide-react";
import DataGrid, { type GridColumn } from "../../../components/DataGrid";
import { PageHeader, Spinner, Empty } from "../../../components/ui";
import { errText, when } from "../../../api/platform";
import { deskApi, type IcReferral } from "./api";

/** Sexual-harassment referrals — sealed inbox of the designated recipient(s) the workplace harassment policy names.
 *  Each row = "this employee asked HR Desk about a sexual-harassment matter at this time". No text exists anywhere; the
 *  harassment investigator reaches out under the policy's own procedure. Access = per-org recipient list (internal ids keep `ic`) (Company settings → ai_desk.ic.members)
 *  or the custom role `ic_member`; HR admins / org admins / managers get 404 by role. Every view is audited (desk.ic.view).
 *  Route to wire (stream A / phase 2): <Route path="ai/desk/ic" element={<IcReferralsPage />} /> — open to ALL_STAFF; the page shows
 *  "not a member" unless /ai/desk/ic-referrals/access says ic_member. Nav (optional, ai: true): { to: "/ai/desk/ic", label: "IC referrals", icon: ShieldAlert, roles: ALL_STAFF }
 *  — better: show the entry only when icAccess() is true (DashboardLayout can query it once). */
export default function IcReferralsPage() {
  const qc = useQueryClient();
  const access = useQuery({ queryKey: ["desk", "ic", "access"], queryFn: deskApi.icAccess });
  const list = useQuery({ queryKey: ["desk", "ic", "list"], queryFn: deskApi.icReferrals, enabled: !!access.data?.ic_member });
  const ack = useMutation({ mutationFn: (id: string) => deskApi.icAcknowledge(id), onSuccess: () => { toast.success("Acknowledged"); qc.invalidateQueries({ queryKey: ["desk", "ic"] }); }, onError: (e) => toast.error(errText(e)) });

  if (access.isLoading) return <div className="grid place-items-center py-24"><Spinner className="h-8 w-8" /></div>;
  if (!access.data?.ic_member) {
    return (
      <div>
        <PageHeader title="Harassment referrals" icon={Lock} subtitle="Sealed inbox of the designated recipient(s) for sexual-harassment complaints." />
        <div className="card text-sm text-gray-700 max-w-2xl">
          <p className="font-medium text-gray-800 mb-1">You are not listed as a designated recipient for harassment complaints.</p>
          <p>Only designated recipients (Company settings → HR Desk → designated recipients, or the custom role <code className="font-mono bg-gray-100 px-1 rounded">ic_member</code>) can open this inbox — not HR, not managers, not the org admin by role. This keeps complaints confidential, as the workplace harassment policy (e.g. Ontario OHSA Part III.0.1, the federal Work Place Harassment and Violence Prevention Regulations) requires, inside Lexora HRM.</p>
        </div>
      </div>
    );
  }
  const rows = list.data?.items || [];
  const open = rows.filter((r) => !r.acknowledged_at).length;
  const cols: GridColumn<IcReferral>[] = [
    { key: "created_at", label: "Asked at", type: "date", sortable: true, render: (r) => <span className="tabular-nums text-xs">{r.created_at ? when(r.created_at) : "—"}</span>, text: (r) => r.created_at || "" },
    { key: "employee_id", label: "Employee id", render: (r) => <span className="font-mono text-xs">{r.employee_id || "(login not linked to an employee)"}</span>, text: (r) => r.employee_id || "" },
    { key: "channel", label: "Channel", sortable: true },
    { key: "acknowledged_at", label: "Acknowledged", render: (r) => r.acknowledged_at ? <span className="text-xs text-green-700 inline-flex items-center gap-1"><CheckCircle2 className="w-3.5 h-3.5" />{when(r.acknowledged_at)}</span> : <span className="text-xs text-amber-700">open</span>, text: (r) => r.acknowledged_at || "open" },
    { key: "_", label: "", render: (r) => !r.acknowledged_at ? <button className="btn-secondary text-xs" onClick={() => ack.mutate(r.id)}>Acknowledge</button> : null },
  ];
  return (
    <div>
      <PageHeader title="Harassment referrals" icon={ShieldAlert}
        subtitle={`${open} open · ${rows.length} total. Each row means a person asked HR Desk about a sexual-harassment matter at that time. Nothing they wrote was stored. Reach out under the workplace harassment policy's own investigation procedure.`} />
      {list.isLoading ? <div className="grid place-items-center py-16"><Spinner /></div> : rows.length ? (
        <DataGrid<IcReferral> columns={cols} rows={rows} rowKey={(r) => r.id} exportName="ic-referrals" empty="No referrals." minWidth={760} />
      ) : <Empty>No referrals. When an employee raises a sexual-harassment matter with HR Desk, a sealed row appears here within seconds.</Empty>}
      {list.data?.note && <p className="text-[11px] text-gray-500 mt-3">{list.data.note} Every opening of this inbox is written to the audit trail.</p>}
    </div>
  );
}
