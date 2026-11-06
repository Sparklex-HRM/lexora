import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HardHat, Plus, Trash2, Download } from "lucide-react";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { PageHeader, Spinner } from "../../components/ui";
import { canadaApi, type RateGroup } from "../../api/canada";
import { Th, Td, Panel, Hint, Tile, GateBanner, Modal, Field, cad, inputCls, exportCsv, err } from "./_shared";
import { useUrlTab } from "../../search/useUrlTab";

const PROVS = ["ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "NU", "YT"];

/** /payroll/canada/accruals — WCB/WSIB premium by rate group + Employer Health Tax, accrued per pay run,
 *  with a remittance summary by province / rate group. Rates are the org's; caps/rules are org statutory values. */
export default function EmployerAccrualsPage() {
  const qc = useQueryClient();
  const [year] = useState(2026);
  const [tab, setTab] = useUrlTab<"summary" | "groups" | "run">("summary");   // ?tab= (global search deep link)
  const { data: rg } = useQuery({ queryKey: ["ca-rate-groups", year], queryFn: () => canadaApi.rateGroups(year) });
  const { data: sum, isLoading } = useQuery({ queryKey: ["ca-accrual-summary", year], queryFn: () => canadaApi.accrualSummary(year) });
  const { data: runs } = useQuery({ queryKey: ["runs"], queryFn: canadaApi.runs });
  const [runId, setRunId] = useState("");
  const { data: runAcc } = useQuery({ queryKey: ["ca-accrual-run", runId], queryFn: () => canadaApi.accrualRun(runId), enabled: !!runId });
  const [edit, setEdit] = useState<Partial<RateGroup> | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["ca-rate-groups", year] }); qc.invalidateQueries({ queryKey: ["ca-accrual-summary", year] }); qc.invalidateQueries({ queryKey: ["ca-accrual-run"] }); };
  const del = useMutation({ mutationFn: (id: string) => canadaApi.deleteRateGroup(id), onSuccess: () => { toast.success("Removed"); refresh(); }, onError: (e) => toast.error(err(e)) });
  const assign = useMutation({ mutationFn: (b: { employee_id: string; rate_group_id: string | null }) => canadaApi.assign(b), onSuccess: refresh, onError: (e) => toast.error(err(e)) });

  if (isLoading || !sum || !rg) return <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div>;
  return (
    <div>
      <PageHeader title="Employer accruals — WCB / WSIB + Employer Health Tax" icon={HardHat}
        subtitle="Premium = assessable earnings × your rate group ÷ 100, capped by the board's maximum (an org value). EHT accrues on cumulative provincial payroll."
        actions={<div className="flex gap-1 bg-surface rounded-lg p-1">{(["summary", "groups", "run"] as const).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)} className={`px-3 py-1.5 rounded-md text-xs font-medium ${tab === t ? "bg-white shadow-sm text-primary-700" : "text-gray-500"}`}>
            {t === "summary" ? "Remittance summary" : t === "groups" ? "Rate groups & assignments" : "Per pay run"}</button>))}</div>} />
      <GateBanner gate={sum.compliance} />
      {sum.unassigned_employees?.length > 0 && <div className="mt-2"><Hint tone="warn">{sum.unassigned_employees.length} employee(s) have no rate group — their premium shows as blank: {sum.unassigned_employees.slice(0, 10).join(", ")}</Hint></div>}

      {tab === "summary" && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap gap-2">
            <Tile label="WCB premium YTD" value={cad(Object.values(sum.wcb_by_province || {}).reduce((a: number, p: any) => a + p.premium, 0))} tone="accent" />
            <Tile label="EHT accrued YTD" value={cad(Object.values(sum.eht_by_province || {}).reduce((a: number, v: any) => a + v, 0))} tone="accent" />
            <Tile label="Pay runs" value={sum.runs.length} />
          </div>
          <div className="grid lg:grid-cols-2 gap-3">
            <Panel title="WCB / WSIB by province" sub="assessable earnings (capped) and premium">
              <table className="w-full text-sm"><thead><tr><Th>Prov</Th><Th right>Employees</Th><Th right>Assessable</Th><Th right>Max assessable</Th><Th right>Premium</Th></tr></thead>
                <tbody>{Object.entries(sum.wcb_by_province || {}).map(([p, v]: any) => <tr key={p}><Td mono className="font-semibold">{p}</Td><Td right mono>{v.employees}</Td><Td right mono>{cad(v.assessable)}</Td><Td right mono>{v.max_assessable != null ? cad(v.max_assessable, 0) : <span className="text-amber-600 text-[11px]">not entered</span>}</Td><Td right mono className="font-semibold">{cad(v.premium)}</Td></tr>)}</tbody></table>
            </Panel>
            <Panel title="WCB / WSIB by rate group" sub="what the board's return asks for">
              <table className="w-full text-sm"><thead><tr><Th>Province / group</Th><Th right>Assessable</Th><Th right>Premium</Th></tr></thead>
                <tbody>{Object.entries(sum.wcb_by_group || {}).map(([k, v]: any) => <tr key={k}><Td mono>{k}</Td><Td right mono>{cad(v.assessable)}</Td><Td right mono className="font-semibold">{cad(v.premium)}</Td></tr>)}</tbody></table>
            </Panel>
          </div>
          <Panel title="Employer Health Tax by province" sub="org rule if entered, else the code's 2026 reference stamped 'not org-verified'">
            <table className="w-full text-sm"><thead><tr><Th>Prov</Th><Th right>Accrued YTD</Th></tr></thead>
              <tbody>{Object.entries(sum.eht_by_province || {}).map(([p, v]: any) => <tr key={p}><Td mono className="font-semibold">{p}</Td><Td right mono>{cad(v)}</Td></tr>)}
                {!Object.keys(sum.eht_by_province || {}).length && <tr><Td className="text-gray-400" colSpan={2 as any}>No runs yet.</Td></tr>}</tbody></table>
          </Panel>
          <Panel title="Accrual per pay run" sub="cumulative — caps and exemptions apply in pay-date order"
            actions={<button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => exportCsv(`employer_accruals_${year}.csv`, ["Pay date", "Period end", "Employees", "WCB premium", "EHT accrual"], sum.runs.map((r: any) => [r.pay_date, r.period_end, r.employees, r.wcb_total, r.eht_total]))}><Download className="w-3.5 h-3.5" /> CSV</button>}>
            <table className="w-full text-sm"><thead><tr><Th>Pay date</Th><Th>Period end</Th><Th right>Employees</Th><Th right>WCB premium</Th><Th right>EHT accrual</Th><Th /></tr></thead>
              <tbody>{sum.runs.map((r: any) => <tr key={r.run_id}><Td mono>{r.pay_date}</Td><Td mono>{r.period_end}</Td><Td right mono>{r.employees}</Td><Td right mono>{cad(r.wcb_total)}</Td><Td right mono>{cad(r.eht_total)}</Td><Td><button type="button" className="text-primary-700 text-[11px] underline" onClick={() => { setRunId(r.run_id); setTab("run"); }}>lines</button></Td></tr>)}</tbody></table>
          </Panel>
        </div>)}

      {tab === "groups" && (
        <div className="mt-3 grid lg:grid-cols-2 gap-3">
          <Panel title="Rate groups (your board-assigned rates)" sub="$ per $100 of assessable earnings" actions={<button type="button" className="btn-primary text-xs inline-flex items-center gap-1" onClick={() => setEdit({ province: "ON", group_code: "", rate_per_100: 0, year })}><Plus className="w-3.5 h-3.5" /> Add</button>}>
            <table className="w-full text-sm"><thead><tr><Th>Prov</Th><Th>Group</Th><Th>Description</Th><Th right>Rate /$100</Th><Th>Account</Th><Th /></tr></thead>
              <tbody>{rg.groups.map((g) => <tr key={g.id}><Td mono className="font-semibold">{g.province}</Td><Td mono>{g.group_code}</Td><Td className="text-gray-600">{g.description || "—"}</Td><Td right mono>{g.rate_per_100}</Td><Td mono className="text-[11px]">{g.account_number || "—"}</Td>
                <Td><button type="button" className="text-[11px] text-primary-700 underline mr-2" onClick={() => setEdit(g)}>edit</button><button type="button" className="text-rose-600" onClick={() => del.mutate(g.id)}><Trash2 className="w-3.5 h-3.5" /></button></Td></tr>)}
                {!rg.groups.length && <tr><Td className="text-gray-400" colSpan={6 as any}>No rate groups yet. Add the classification unit and rate from your board's notice.</Td></tr>}</tbody></table>
            <div className="px-3 py-2 text-[11px] text-gray-500 border-t border-surface-border">Maximum assessable earnings per province are entered under <Link className="underline text-primary-700" to="/payroll/canada/statutory-values">Canadian statutory values</Link> (wcb.&lt;PROV&gt;.max_assessable).</div>
          </Panel>
          <Panel title="Employee → rate group" sub="one group per employee">
            <div className="overflow-x-auto max-h-[60vh]"><table className="w-full text-sm"><thead><tr><Th>Emp #</Th><Th>Name</Th><Th>Prov</Th><Th>Rate group</Th></tr></thead>
              <tbody>{rg.employees.map((e: any) => <tr key={e.id}><Td mono>{e.employee_number}</Td><Td>{e.name}</Td><Td mono>{e.province}</Td>
                <Td><select className="input-base h-7 text-xs" value={e.rate_group_id || ""} onChange={(ev) => assign.mutate({ employee_id: e.id, rate_group_id: ev.target.value || null })}>
                  <option value="">— none —</option>{rg.groups.filter((g) => g.province === e.province).map((g) => <option key={g.id} value={g.id}>{g.province} {g.group_code} · {g.rate_per_100}/$100</option>)}</select></Td></tr>)}</tbody></table></div>
          </Panel>
        </div>)}

      {tab === "run" && (
        <div className="mt-3 space-y-3">
          <div className="flex items-center gap-2"><Field label="Pay run"><select className="input-base h-8 text-xs" value={runId} onChange={(e) => setRunId(e.target.value)}>
            <option value="">— pick —</option>{(runs || []).map((r: any) => <option key={r.id} value={r.id}>{r.pay_date} · {r.pay_frequency || ""} · {r.employee_count ?? ""} emp</option>)}</select></Field></div>
          {runAcc && (<>
            <GateBanner gate={runAcc.compliance} />
            <Panel title="WCB line per employee" sub={`${runAcc.pay_date} · premium ${cad(runAcc.wcb_total)}`}>
              <div className="overflow-x-auto max-h-[50vh]"><table className="w-full text-sm"><thead><tr><Th>Emp #</Th><Th>Name</Th><Th>Prov</Th><Th>Group</Th><Th right>Gross</Th><Th right>Assessable</Th><Th>Cap</Th><Th>Formula</Th><Th right>Premium</Th></tr></thead>
                <tbody>{runAcc.wcb_lines.map((l: any) => <tr key={l.employee_id}><Td mono>{l.employee_number}</Td><Td>{l.name}</Td><Td mono>{l.province}</Td><Td mono>{l.rate_group || "—"}</Td><Td right mono>{cad(l.gross)}</Td><Td right mono>{cad(l.assessable)}</Td><Td className="text-[11px]">{l.capped ? "capped" : l.max_assessable == null ? "no cap entered" : ""}</Td><Td mono className="text-[11px] text-gray-500">{l.formula}</Td><Td right mono className="font-semibold">{l.premium != null ? cad(l.premium) : "—"}</Td></tr>)}</tbody></table></div>
            </Panel>
            <Panel title="Employer Health Tax lines" sub={`accrual this run ${cad(runAcc.eht_total)}`}>
              <table className="w-full text-sm"><thead><tr><Th>Prov</Th><Th right>Payroll before</Th><Th right>Payroll after</Th><Th right>Tax before</Th><Th right>Tax after</Th><Th right>Accrual</Th><Th>Source</Th></tr></thead>
                <tbody>{runAcc.eht_lines.map((l: any) => <tr key={l.province}><Td mono className="font-semibold">{l.province}</Td><Td right mono>{cad(l.ytd_before)}</Td><Td right mono>{cad(l.ytd_after)}</Td><Td right mono>{cad(l.tax_before)}</Td><Td right mono>{cad(l.tax_after)}</Td><Td right mono className="font-semibold">{cad(l.accrual)}</Td><Td className="text-[11px]">{l.applicable ? l.source : "no employer health tax"}</Td></tr>)}</tbody></table>
            </Panel>
          </>)}
        </div>)}

      {edit && <RateGroupModal g={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); refresh(); }} />}
    </div>
  );
}

function RateGroupModal({ g, onClose, onSaved }: { g: Partial<RateGroup>; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ province: g.province || "ON", group_code: g.group_code || "", description: g.description || "", rate_per_100: String(g.rate_per_100 ?? ""), account_number: g.account_number || "" });
  const save = useMutation({ mutationFn: () => canadaApi.putRateGroup({ id: g.id, province: f.province, group_code: f.group_code, description: f.description, rate_per_100: Number(f.rate_per_100), year: g.year || 2026, account_number: f.account_number || null }),
    onSuccess: () => { toast.success("Saved"); onSaved(); }, onError: (e) => toast.error(err(e)) });
  return (
    <Modal title={g.id ? "Edit rate group" : "Add rate group"} sub="From your WCB / WSIB / WorkSafe rate notice — the org's own figure" onClose={onClose} width="max-w-3xl"
      footer={<><button type="button" className="btn-secondary text-xs" onClick={onClose}>Cancel</button><button type="button" className="btn-primary text-xs" disabled={!f.group_code || save.isPending} onClick={() => save.mutate()}>Save</button></>}>
      <div className="grid md:grid-cols-3 gap-3">
        <Field label="Province"><select className={inputCls} value={f.province} onChange={(e) => setF({ ...f, province: e.target.value })}>{PROVS.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Rate group / classification code"><input className={inputCls} value={f.group_code} onChange={(e) => setF({ ...f, group_code: e.target.value })} placeholder="e.g. 7050" /></Field>
        <Field label="Rate per $100"><input type="number" step="0.01" className={inputCls} value={f.rate_per_100} onChange={(e) => setF({ ...f, rate_per_100: e.target.value })} /></Field>
        <Field label="Description" className="md:col-span-2"><input className={inputCls} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Board account #"><input className={inputCls} value={f.account_number} onChange={(e) => setF({ ...f, account_number: e.target.value })} /></Field>
      </div>
    </Modal>
  );
}
