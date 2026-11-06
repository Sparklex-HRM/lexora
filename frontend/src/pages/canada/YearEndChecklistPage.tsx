import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CalendarCheck, ExternalLink, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { PageHeader, Spinner } from "../../components/ui";
import { canadaApi } from "../../api/canada";
import { Th, Td, Panel, Hint, Tile, GateBanner, StatusPill, cad } from "./_shared";

/** /payroll/canada/year-end — what is due, by when (org-entered dates), what is outstanding, plus an ROE draft validator. */
export default function YearEndChecklistPage() {
  const [year] = useState(2026);
  const { data, isLoading } = useQuery({ queryKey: ["ca-year-end", year], queryFn: () => canadaApi.yearEnd(year) });
  const [roeEmp, setRoeEmp] = useState("");
  const { data: roe, isFetching } = useQuery({ queryKey: ["ca-roe-validate", roeEmp], queryFn: () => canadaApi.roeValidate(roeEmp), enabled: !!roeEmp });
  if (isLoading || !data) return <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div>;
  const roeItem = data.items.find((i: any) => i.key === "roe");
  const remitItem = data.items.find((i: any) => i.key === "remit");
  return (
    <div>
      <PageHeader title={`Year-end checklist · ${year}`} icon={CalendarCheck} subtitle="Slips, summaries, ROEs and remittances — due dates come from the values you entered, never from Lexora." />
      <GateBanner gate={data.compliance} />
      <div className="flex flex-wrap gap-2 my-3"><Tile label="Canada employees" value={data.employees} /><Tile label="YTD ledger rows" value={data.ytd_rows} /><Tile label="ROEs outstanding" value={roeItem?.count ?? 0} tone={roeItem?.count ? "warn" : "ok"} /><Tile label="Remittance periods past due" value={remitItem?.count ?? 0} tone={remitItem?.count ? "warn" : "ok"} /></div>
      <Panel title="Checklist" sub="fixed order — slips, summaries, ROEs, remittances, board returns">
        <table className="w-full text-sm"><thead><tr><Th>#</Th><Th>Item</Th><Th right>Count</Th><Th>Due</Th><Th>Status of due date</Th><Th>What to do</Th><Th /></tr></thead>
          <tbody>{data.items.map((it: any, i: number) => (
            <tr key={it.key}><Td mono>{i + 1}</Td><Td className="font-medium text-gray-800">{it.title}</Td><Td right mono>{it.count ?? "—"}</Td>
              <Td mono>{it.due ? (it.due.value || <span className="text-amber-600 text-[11px]">not entered</span>) : "—"}{it.due?.source_url && <a className="ml-1 text-primary-700" href={it.due.source_url} target="_blank" rel="noreferrer"><ExternalLink className="w-3 h-3 inline" /></a>}</Td>
              <Td>{it.due ? <StatusPill status={it.due.status} /> : "—"}</Td><Td className="!whitespace-normal text-gray-600 max-w-[30rem]">{it.how}</Td>
              <Td><Link className="text-[11px] text-primary-700 underline" to={it.link}>open</Link></Td></tr>))}</tbody></table>
      </Panel>
      <div className="grid lg:grid-cols-2 gap-3 mt-3">
        <Panel title="ROEs outstanding" sub="terminated this year">
          <table className="w-full text-sm"><thead><tr><Th>Emp #</Th><Th>Name</Th><Th>Terminated</Th><Th>ROE due</Th><Th /></tr></thead>
            <tbody>{(roeItem?.rows || []).map((r: any) => <tr key={r.employee_id} className={r.overdue ? "bg-rose-50/60" : ""}><Td mono>{r.employee_number}</Td><Td>{r.name}</Td><Td mono>{r.terminated_on}</Td><Td mono>{r.roe_due || <span className="text-amber-600 text-[11px]">enter yearend.roe_due_days</span>}</Td><Td><button type="button" className="text-[11px] text-primary-700 underline" onClick={() => setRoeEmp(r.employee_id)}>validate draft</button></Td></tr>)}
              {!(roeItem?.rows || []).length && <tr><Td className="text-gray-400" colSpan={5 as any}>No terminations this year.</Td></tr>}</tbody></table>
        </Panel>
        <Panel title="ROE draft validation" sub="field checks on the blocks Lexora models + the XML package (not the Service Canada XSD)">
          <div className="p-3 space-y-2">
            {!roeEmp && <Hint tone="info">Click <b>validate draft</b> on an ROE row. You can also validate any employee with pay history by employee id in the URL of Records of Employment.</Hint>}
            {isFetching && <Spinner className="h-5 w-5" />}
            {roe && (<>
              <Hint tone={roe.ok ? "ok" : "bad"}><ShieldCheck className="w-3.5 h-3.5 inline mr-1" />{roe.ok ? "Draft passes the field checks and the XML package is well-formed." : "Draft has problems — fix them before filing."} {roe.employee?.name && <span className="text-gray-500">· {roe.employee.name} · reason {roe.reason?.code}</span>}</Hint>
              <ul className="text-[12px] list-disc pl-4 text-rose-700">{[...(roe.fields?.errors || []), ...(roe.xml?.errors || [])].map((e: string, i: number) => <li key={i}>{e}</li>)}</ul>
              <ul className="text-[12px] list-disc pl-4 text-amber-700">{[...(roe.fields?.warnings || []), ...(roe.xml?.warnings || [])].map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
              <div className="text-[11px] text-gray-500">{roe.fields?.note}</div>
            </>)}
          </div>
        </Panel>
      </div>
      <div className="mt-3"><Panel title="Remittance periods past their due date" sub="confirm each was paid — Lexora does not record the payment">
        <table className="w-full text-sm"><thead><tr><Th>Period</Th><Th right>Employees</Th><Th right>Total</Th><Th>Due</Th></tr></thead>
          <tbody>{(remitItem?.rows || []).map((p: any) => <tr key={p.period}><Td mono>{p.period}</Td><Td right mono>{p.employees}</Td><Td right mono>{cad(p.total_remittance)}</Td><Td mono>{p.due_date}</Td></tr>)}
            {!(remitItem?.rows || []).length && <tr><Td className="text-gray-400" colSpan={4 as any}>Nothing past due (or no due-date rule entered).</Td></tr>}</tbody></table></Panel></div>
    </div>
  );
}
