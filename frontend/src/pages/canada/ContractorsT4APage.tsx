import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Briefcase, Plus, FileText, Download } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader, Spinner } from "../../components/ui";
import { canadaApi, type Contractor } from "../../api/canada";
import { Th, Td, Panel, Hint, Tile, GateBanner, Modal, Field, SearchBox, cad, inputCls, exportCsv, err } from "./_shared";
import { useUrlTab } from "../../search/useUrlTab";
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/** /payroll/canada/contractors — contractors paid alongside employees, their payments, and the T4A slips/summary. */
export default function ContractorsT4APage() {
  const qc = useQueryClient();
  const [year] = useState(2026);
  const [tab, setTab] = useUrlTab<"contractors" | "payments" | "t4a">("contractors");   // ?tab= (global search deep link)
  const { data: cs, isLoading } = useQuery({ queryKey: ["ca-contractors", year], queryFn: () => canadaApi.contractors(year, 1) });
  const { data: ps } = useQuery({ queryKey: ["ca-contractor-payments", year], queryFn: () => canadaApi.payments(year) });
  const { data: t4a } = useQuery({ queryKey: ["ca-t4a", year], queryFn: () => canadaApi.t4a(year), enabled: tab === "t4a" });
  const [q, setQ] = useState("");
  const [edit, setEdit] = useState<Partial<Contractor> | null>(null);
  const [pay, setPay] = useState<Contractor | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["ca-contractors"] }); qc.invalidateQueries({ queryKey: ["ca-contractor-payments"] }); qc.invalidateQueries({ queryKey: ["ca-t4a"] }); };
  const cancel = useMutation({ mutationFn: (id: string) => canadaApi.cancelPayment(id), onSuccess: () => { toast.success("Cancelled"); refresh(); }, onError: (e) => toast.error(err(e)) });
  const byId = useMemo(() => Object.fromEntries((cs || []).map((c) => [c.id, c])), [cs]);
  const list = (cs || []).filter((c) => !q || `${c.code} ${c.name} ${c.business_number || ""}`.toLowerCase().includes(q.toLowerCase()));
  if (isLoading || !cs) return <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div>;
  return (
    <div>
      <PageHeader title="Contractors (T4A)" icon={Briefcase} subtitle="Self-employed payees paid alongside employees. Fees → box 048, commissions → box 020, any tax actually withheld → box 022. GST/HST is kept out of the slip."
        actions={<div className="flex gap-1 bg-surface rounded-lg p-1">{(["contractors", "payments", "t4a"] as const).map((t) => (
          <button key={t} type="button" onClick={() => setTab(t)} className={`px-3 py-1.5 rounded-md text-xs font-medium ${tab === t ? "bg-white shadow-sm text-primary-700" : "text-gray-500"}`}>{t === "t4a" ? "T4A slips" : t[0].toUpperCase() + t.slice(1)}</button>))}</div>} />
      <div className="flex flex-wrap gap-2 mb-3">
        <Tile label="Contractors" value={cs.filter((c) => c.active).length} /><Tile label="Paid this year" value={cad(cs.reduce((a, c) => a + (c.ytd_paid || 0), 0))} tone="accent" />
        <Tile label="Payments" value={(ps || []).filter((p) => p.status !== "cancelled").length} />
      </div>
      {tab === "contractors" && (
        <Panel title="Contractors" actions={<><SearchBox value={q} onChange={setQ} /><button type="button" className="btn-primary text-xs inline-flex items-center gap-1" onClick={() => setEdit({ default_box: "048", active: true })}><Plus className="w-3.5 h-3.5" /> Add</button></>}>
          <table className="w-full text-sm"><thead><tr><Th>Code</Th><Th>Name</Th><Th>Business #</Th><Th>Prov</Th><Th>Default box</Th><Th right>Paid YTD</Th><Th right>Tax withheld</Th><Th right>Payments</Th><Th>Active</Th><Th /></tr></thead>
            <tbody>{list.map((c) => <tr key={c.id}><Td mono>{c.code}</Td><Td className="font-medium text-gray-800">{c.name}</Td><Td mono>{c.business_number || "—"}</Td><Td mono>{c.province || "—"}</Td><Td mono>{c.default_box}</Td><Td right mono>{cad(c.ytd_paid)}</Td><Td right mono>{cad(c.ytd_tax)}</Td><Td right mono>{c.payments}</Td><Td>{c.active ? "yes" : "no"}</Td>
              <Td><button type="button" className="text-[11px] text-primary-700 underline mr-2" onClick={() => setEdit(c)}>edit</button><button type="button" className="text-[11px] text-primary-700 underline" onClick={() => setPay(c)}>record payment</button></Td></tr>)}
              {!list.length && <tr><Td className="text-gray-400" colSpan={10 as any}>No contractors yet.</Td></tr>}</tbody></table>
        </Panel>)}
      {tab === "payments" && (
        <Panel title={`Payments · ${year}`} actions={<button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => exportCsv(`contractor_payments_${year}.csv`, ["Date", "Contractor", "Invoice", "Description", "Amount", "GST/HST", "Tax withheld", "Box", "Status"], (ps || []).map((p) => [p.pay_date, byId[p.contractor_id]?.name, p.invoice_no, p.description, p.amount, p.gst_hst, p.tax_withheld, p.box, p.status]))}><Download className="w-3.5 h-3.5" /> CSV</button>}>
          <table className="w-full text-sm"><thead><tr><Th>Date</Th><Th>Contractor</Th><Th>Invoice</Th><Th>Description</Th><Th right>Amount</Th><Th right>GST/HST</Th><Th right>Tax withheld</Th><Th>Box</Th><Th>Status</Th><Th /></tr></thead>
            <tbody>{(ps || []).map((p) => <tr key={p.id} className={p.status === "cancelled" ? "text-gray-400 line-through" : ""}><Td mono>{p.pay_date}</Td><Td>{byId[p.contractor_id]?.name || p.contractor_id}</Td><Td mono>{p.invoice_no || "—"}</Td><Td className="text-gray-600">{p.description || "—"}</Td><Td right mono>{cad(p.amount)}</Td><Td right mono>{cad(p.gst_hst)}</Td><Td right mono>{cad(p.tax_withheld)}</Td><Td mono>{p.box}</Td><Td>{p.status}</Td>
              <Td>{p.status !== "cancelled" && <button type="button" className="text-[11px] text-rose-600 underline" onClick={() => cancel.mutate(p.id)}>cancel</button>}</Td></tr>)}
              {!(ps || []).length && <tr><Td className="text-gray-400" colSpan={10 as any}>No payments this year.</Td></tr>}</tbody></table>
        </Panel>)}
      {tab === "t4a" && t4a && (
        <div className="space-y-3">
          <GateBanner gate={t4a.compliance} />
          <Hint tone="info">Filing due date: <b>{t4a.due_date || "not entered"}</b> {t4a.due_source && <a className="underline" href={t4a.due_source} target="_blank" rel="noreferrer">(source)</a>}. Box numbers must be confirmed against the current CRA T4A guide before filing.</Hint>
          <div className="flex flex-wrap gap-2"><Tile label="Slips" value={t4a.summary.slip_count} /><Tile label="Box 048 fees" value={cad(t4a.summary.box_048)} /><Tile label="Box 020 commissions" value={cad(t4a.summary.box_020)} /><Tile label="Box 022 tax deducted" value={cad(t4a.summary.box_022)} /><Tile label="Total income" value={cad(t4a.summary.total_income)} tone="accent" /></div>
          <Panel title={`T4A slips · ${year}`} actions={<button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => exportCsv(`t4a_${year}.csv`, ["Code", "Name", "Business #", "Province", "Box 048", "Box 020", "Box 022", "Payments", "GST/HST excluded"], t4a.slips.map((s: any) => [s.code, s.name, s.business_number, s.province, s.boxes.box_048, s.boxes.box_020, s.boxes.box_022, s.payments, s.gst_hst_excluded]))}><FileText className="w-3.5 h-3.5" /> Export slips</button>}>
            <table className="w-full text-sm"><thead><tr><Th>Code</Th><Th>Name</Th><Th>Business #</Th><Th>Prov</Th><Th right>048 Fees</Th><Th right>020 Commissions</Th><Th right>022 Tax</Th><Th right>Payments</Th><Th right>GST/HST excluded</Th></tr></thead>
              <tbody>{t4a.slips.map((s: any) => <tr key={s.contractor_id}><Td mono>{s.code}</Td><Td className="font-medium">{s.name}</Td><Td mono>{s.business_number || "—"}</Td><Td mono>{s.province || "—"}</Td><Td right mono>{cad(s.boxes.box_048)}</Td><Td right mono>{cad(s.boxes.box_020)}</Td><Td right mono>{cad(s.boxes.box_022)}</Td><Td right mono>{s.payments}</Td><Td right mono className="text-gray-400">{cad(s.gst_hst_excluded)}</Td></tr>)}
                {!t4a.slips.length && <tr><Td className="text-gray-400" colSpan={9 as any}>No contractor payments this year.</Td></tr>}</tbody>
              <tfoot><tr className="bg-surface font-semibold"><Td colSpan={4 as any}>T4A Summary</Td><Td right mono>{cad(t4a.summary.box_048)}</Td><Td right mono>{cad(t4a.summary.box_020)}</Td><Td right mono>{cad(t4a.summary.box_022)}</Td><Td /><Td /></tr></tfoot></table>
          </Panel>
        </div>)}
      {edit && <ContractorModal c={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); refresh(); }} />}
      {pay && <PaymentModal c={pay} onClose={() => setPay(null)} onSaved={() => { setPay(null); refresh(); setTab("payments"); }} />}
    </div>
  );
}

function ContractorModal({ c, onClose, onSaved }: { c: Partial<Contractor>; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ code: c.code || "", name: c.name || "", business_number: c.business_number || "", email: c.email || "", address: c.address || "", province: c.province || "", default_box: c.default_box || "048", active: c.active ?? true });
  const save = useMutation({ mutationFn: () => canadaApi.putContractor({ id: c.id, ...f, province: f.province || null }), onSuccess: () => { toast.success("Saved"); onSaved(); }, onError: (e) => toast.error(err(e)) });
  return (
    <Modal title={c.id ? "Edit contractor" : "Add contractor"} onClose={onClose} width="max-w-4xl"
      footer={<><button type="button" className="btn-secondary text-xs" onClick={onClose}>Cancel</button><button type="button" className="btn-primary text-xs" disabled={!f.code || !f.name || save.isPending} onClick={() => save.mutate()}>Save</button></>}>
      <div className="grid md:grid-cols-3 gap-3">
        <Field label="Code"><input className={inputCls} value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
        <Field label="Name / business name" className="md:col-span-2"><input className={inputCls} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Business number (BN)"><input className={inputCls} value={f.business_number} onChange={(e) => setF({ ...f, business_number: e.target.value })} /></Field>
        <Field label="Email"><input className={inputCls} value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
        <Field label="Province"><input className={inputCls} value={f.province} onChange={(e) => setF({ ...f, province: e.target.value.toUpperCase().slice(0, 2) })} placeholder="ON" /></Field>
        <Field label="Address" className="md:col-span-2"><input className={inputCls} value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} /></Field>
        <Field label="Default T4A box"><select className={inputCls} value={f.default_box} onChange={(e) => setF({ ...f, default_box: e.target.value })}><option value="048">048 — fees for services</option><option value="020">020 — self-employed commissions</option></select></Field>
        <label className="flex items-center gap-2 text-xs md:col-span-3"><input type="checkbox" className="accent-primary-600" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label>
      </div>
    </Modal>
  );
}

function PaymentModal({ c, onClose, onSaved }: { c: Contractor; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ pay_date: todayISO(), invoice_no: "", description: "", amount: "", gst_hst: "0", tax_withheld: "0", box: c.default_box });
  const save = useMutation({ mutationFn: () => canadaApi.addPayment({ contractor_id: c.id, pay_date: f.pay_date, invoice_no: f.invoice_no, description: f.description, amount: Number(f.amount), gst_hst: Number(f.gst_hst) || 0, tax_withheld: Number(f.tax_withheld) || 0, box: f.box }),
    onSuccess: () => { toast.success("Payment recorded"); onSaved(); }, onError: (e) => toast.error(err(e)) });
  return (
    <Modal title={`Record payment — ${c.name}`} sub="Amount before GST/HST. Tax withheld only if you actually withheld it." onClose={onClose} width="max-w-4xl"
      footer={<><button type="button" className="btn-secondary text-xs" onClick={onClose}>Cancel</button><button type="button" className="btn-primary text-xs" disabled={!f.amount || save.isPending} onClick={() => save.mutate()}>Record</button></>}>
      <div className="grid md:grid-cols-4 gap-3">
        <Field label="Pay date"><input type="date" className={inputCls} value={f.pay_date} onChange={(e) => setF({ ...f, pay_date: e.target.value })} /></Field>
        <Field label="Invoice #"><input className={inputCls} value={f.invoice_no} onChange={(e) => setF({ ...f, invoice_no: e.target.value })} /></Field>
        <Field label="Description" className="md:col-span-2"><input className={inputCls} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
        <Field label="Amount (CA$)"><input type="number" step="0.01" className={inputCls} value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
        <Field label="GST/HST on invoice"><input type="number" step="0.01" className={inputCls} value={f.gst_hst} onChange={(e) => setF({ ...f, gst_hst: e.target.value })} /></Field>
        <Field label="Tax withheld"><input type="number" step="0.01" className={inputCls} value={f.tax_withheld} onChange={(e) => setF({ ...f, tax_withheld: e.target.value })} /></Field>
        <Field label="T4A box"><select className={inputCls} value={f.box} onChange={(e) => setF({ ...f, box: e.target.value })}><option value="048">048 fees</option><option value="020">020 commissions</option></select></Field>
      </div>
    </Modal>
  );
}
