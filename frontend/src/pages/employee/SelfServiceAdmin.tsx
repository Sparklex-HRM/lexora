import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import DataGrid, { GridColumn } from "../../components/DataGrid";
import { Field, inputCls, err } from "../canada/_shared";
import { leaveEssApi, permissionApi } from "../../api/ess";

const badge = (s: string) => <span className={s === "approved" ? "badge-green" : s === "pending" ? "badge-amber" : s === "rejected" ? "badge-red" : "badge-purple"}>{s}</span>;

/** HR: comp-off claims, optional-holiday picks, permissions — and the company policy behind them.
 *  Decisions normally happen in Approvals (the org's chain); these grids are the register. */
export default function SelfServiceAdmin() {
  const qc = useQueryClient();
  const co = useQuery({ queryKey: ["lv", "comp-off", "all"], queryFn: () => leaveEssApi.compOffs() });
  const picks = useQuery({ queryKey: ["lv", "picks", "all"], queryFn: () => leaveEssApi.picks() });
  const perms = useQuery({ queryKey: ["perm", "all"], queryFn: () => permissionApi.list() });
  const lp = useQuery({ queryKey: ["lv", "ess-policy"], queryFn: leaveEssApi.policy });
  const pp = useQuery({ queryKey: ["perm-policy"], queryFn: permissionApi.policy });
  const [lf, setLf] = useState<any>(null);
  const [pf, setPf] = useState<any>(null);
  useEffect(() => { if (lp.data && !lf) setLf(lp.data); }, [lp.data, lf]);
  useEffect(() => { if (pp.data && !pf) setPf(pp.data); }, [pp.data, pf]);
  const saveL = useMutation({ mutationFn: () => leaveEssApi.setPolicy({ optional_holidays_per_year: Number(lf.optional_holidays_per_year), comp_off_full_day_hours: Number(lf.comp_off_full_day_hours), comp_off_half_day_hours: Number(lf.comp_off_half_day_hours), comp_off_expiry_days: Number(lf.comp_off_expiry_days) }),
    onSuccess: () => { toast.success("Leave policy saved"); qc.invalidateQueries({ queryKey: ["lv", "ess-policy"] }); }, onError: (e) => toast.error(err(e)) });
  const saveP = useMutation({ mutationFn: () => permissionApi.setPolicy({ max_per_month: Number(pf.max_per_month), max_hours: Number(pf.max_hours) }),
    onSuccess: () => { toast.success("Permission policy saved"); qc.invalidateQueries({ queryKey: ["perm-policy"] }); }, onError: (e) => toast.error(err(e)) });
  const decideCo = useMutation({ mutationFn: ({ id, v }: { id: string; v: "approve" | "reject" }) => leaveEssApi.decideCompOff(id, v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lv"] }), onError: (e) => toast.error(err(e)) });
  const decidePick = useMutation({ mutationFn: ({ id, v }: { id: string; v: "approve" | "reject" }) => leaveEssApi.decidePick(id, v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["lv"] }), onError: (e) => toast.error(err(e)) });
  const act = (status: string, on: (v: "approve" | "reject") => void) => status === "pending" ? (
    <span className="flex gap-2"><button className="text-xs text-green-700 hover:underline" onClick={(e) => { e.stopPropagation(); on("approve"); }}>Approve</button>
      <button className="text-xs text-red-600 hover:underline" onClick={(e) => { e.stopPropagation(); on("reject"); }}>Reject</button></span>) : null;

  const coCols: GridColumn<any>[] = [
    { key: "employee_name", label: "Employee" }, { key: "worked_date", label: "Worked on", type: "date" }, { key: "day_label", label: "Day" },
    { key: "hours_worked", label: "Hours", type: "hours" }, { key: "days", label: "Days", type: "number" }, { key: "expires_on", label: "Use by", type: "date" },
    { key: "status", label: "Status", render: (r) => badge(r.status) }, { key: "reason", label: "Reason" },
    { key: "act", label: "", text: () => "", render: (r) => act(r.status, (v) => decideCo.mutate({ id: r.id, v })) },
  ];
  const pickCols: GridColumn<any>[] = [
    { key: "employee_name", label: "Employee" }, { key: "holiday_name", label: "Optional holiday" }, { key: "holiday_date", label: "Date", type: "date" },
    { key: "status", label: "Status", render: (r) => badge(r.status) },
    { key: "act", label: "", text: () => "", render: (r) => act(r.status, (v) => decidePick.mutate({ id: r.id, v })) },
  ];
  const permCols: GridColumn<any>[] = [
    { key: "employee_name", label: "Employee" }, { key: "work_date", label: "Day", type: "date" }, { key: "from_time", label: "From" }, { key: "to_time", label: "To" },
    { key: "hours", label: "Hours", type: "hours" }, { key: "reason", label: "Reason" }, { key: "status", label: "Status", render: (r) => badge(r.status) },
  ];
  return (
    <div className="space-y-4" data-testid="ess-admin">
      <div className="grid lg:grid-cols-2 gap-4">
        <div className="card space-y-2">
          <div className="font-display font-semibold text-gray-800 text-sm">Leave policy (company)</div>
          {lf && <div className="grid grid-cols-2 gap-2">
            <Field label="Optional holidays a year"><input type="number" min={0} className={inputCls} value={lf.optional_holidays_per_year} onChange={(e) => setLf({ ...lf, optional_holidays_per_year: e.target.value })} /></Field>
            <Field label="Comp-off claim window (days)"><input type="number" min={0} className={inputCls} value={lf.comp_off_expiry_days} onChange={(e) => setLf({ ...lf, comp_off_expiry_days: e.target.value })} /></Field>
            <Field label="Hours for a full comp-off day"><input type="number" min={0} step={0.5} className={inputCls} value={lf.comp_off_full_day_hours} onChange={(e) => setLf({ ...lf, comp_off_full_day_hours: e.target.value })} /></Field>
            <Field label="Hours for half a day"><input type="number" min={0} step={0.5} className={inputCls} value={lf.comp_off_half_day_hours} onChange={(e) => setLf({ ...lf, comp_off_half_day_hours: e.target.value })} /></Field>
          </div>}
          <div className="flex justify-between items-center"><span className="text-[11px] text-gray-400">{lp.data?.source === "default" ? "Not saved yet — these are starting values" : `Saved by ${lp.data?.updated_by || "HR"}`}</span>
            <button className="btn-primary !py-1 text-xs" disabled={!lf || saveL.isPending} onClick={() => saveL.mutate()}>Save</button></div>
        </div>
        <div className="card space-y-2">
          <div className="font-display font-semibold text-gray-800 text-sm">Permission (short hours) policy</div>
          {pf && <div className="grid grid-cols-2 gap-2">
            <Field label="Permissions a month"><input type="number" min={0} className={inputCls} value={pf.max_per_month} onChange={(e) => setPf({ ...pf, max_per_month: e.target.value })} /></Field>
            <Field label="Longest permission (hours)"><input type="number" min={0} step={0.5} className={inputCls} value={pf.max_hours} onChange={(e) => setPf({ ...pf, max_hours: e.target.value })} /></Field>
          </div>}
          <div className="flex justify-between items-center"><span className="text-[11px] text-gray-400">Approvals follow Settings → Approval workflows (Permission).</span>
            <button className="btn-primary !py-1 text-xs" disabled={!pf || saveP.isPending} onClick={() => saveP.mutate()}>Save</button></div>
        </div>
      </div>
      <div className="card !p-0"><div className="px-4 py-3 border-b border-surface-border font-display font-semibold text-sm text-gray-800">Comp-off claims</div>
        <DataGrid columns={coCols} rows={co.data || []} rowKey={(r) => r.id} exportName="comp-off-claims" minWidth={1000} empty="No comp-off claims yet." /></div>
      <div className="grid xl:grid-cols-2 gap-4">
        <div className="card !p-0"><div className="px-4 py-3 border-b border-surface-border font-display font-semibold text-sm text-gray-800">Optional-holiday picks</div>
          <DataGrid columns={pickCols} rows={picks.data || []} rowKey={(r) => r.id} exportName="optional-holiday-picks" minWidth={640} empty="No picks yet." /></div>
        <div className="card !p-0"><div className="px-4 py-3 border-b border-surface-border font-display font-semibold text-sm text-gray-800">Permissions</div>
          <DataGrid columns={permCols} rows={perms.data || []} rowKey={(r) => r.id} exportName="permissions" minWidth={760} empty="No permissions yet." /></div>
      </div>
    </div>
  );
}
