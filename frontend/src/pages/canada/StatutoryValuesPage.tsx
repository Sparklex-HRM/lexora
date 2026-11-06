import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, Pencil, Check, Undo2, Download } from "lucide-react";
import toast from "react-hot-toast";
import clsx from "clsx";
import { PageHeader, Spinner } from "../../components/ui";
import { canadaApi, type StatValueRow } from "../../api/canada";
import { Th, Td, Panel, Hint, Tile, Modal, Field, StatusPill, SearchBox, inputCls, exportCsv, err } from "./_shared";

const GROUPS: Record<string, string> = { remittance: "CRA / RQ remittance", yearend: "Year-end", wcb: "WCB / WSIB", eht: "Employer health tax", vacation: "Vacation pay", stat_holiday: "Statutory holidays" };

/** /payroll/canada/statutory-values — every Canadian rate / threshold / due date the org relies on.
 *  Nothing is pre-filled: the admin enters each value with its source URL, then marks it verified.
 *  An unverified value stamps every estimate that uses it as TEST (same gate mechanism as the payroll run). */
export default function StatutoryValuesPage() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["ca-stat-values"], queryFn: canadaApi.statutoryValues });
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("all");
  const [status, setStatus] = useState("all");
  const [edit, setEdit] = useState<StatValueRow | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["ca-stat-values"] });

  const rows = useMemo(() => (data?.rows || []).filter((r) =>
    (group === "all" || r.group === group) && (status === "all" || r.status === status) &&
    (!q || `${r.key} ${r.label} ${r.province || ""}`.toLowerCase().includes(q.toLowerCase()))), [data, q, group, status]);

  const verify = useMutation({
    mutationFn: (body: { key: string; status: "verified" | "estimated" }) => canadaApi.verifyValue(body),
    onSuccess: () => { toast.success("Saved"); refresh(); }, onError: (e) => toast.error(err(e)),
  });

  if (isLoading || !data) return <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div>;
  return (
    <div>
      <PageHeader title="Canadian statutory values" icon={ShieldCheck}
        subtitle="Every CRA / Revenu Québec / provincial figure Lexora relies on, as YOUR data: value, source link, effective date, verified tick. Blank until you enter it."
        actions={<button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() =>
          exportCsv("canadian_statutory_values.csv", ["Key", "Label", "Province", "Kind", "Value", "Source", "Effective from", "Status", "Verified by", "Verified on"],
            rows.map((r) => [r.key, r.label, r.province, r.kind, r.value, r.source_url, r.effective_from, r.status, r.verified_by, r.verified_on]))}>
          <Download className="w-3.5 h-3.5" /> Export</button>} />
      <Hint tone="warn">Lexora ships <b>no</b> Canadian figure here. Type each value from the official page, paste the page URL, set the date it took effect, then click <b>Mark verified</b>. Until then every schedule, accrual and holiday-pay estimate that uses it is stamped <b>TEST</b>.</Hint>
      <div className="flex flex-wrap gap-2 my-3">
        <Tile label="Total" value={data.total} /><Tile label="Verified" value={data.verified} tone="ok" />
        <Tile label="Entered, not verified" value={data.entered} tone="warn" /><Tile label="Changed since verified" value={data.stale} tone="bad" />
        <Tile label="Blank" value={data.blank} />
      </div>
      <Panel title="Values" sub={`${rows.length} of ${data.total}`} actions={<>
        <SearchBox value={q} onChange={setQ} placeholder="Search key, label, province" />
        <select className="input-base h-8 text-xs" value={group} onChange={(e) => setGroup(e.target.value)}>
          <option value="all">All groups</option>{Object.entries(GROUPS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        <select className="input-base h-8 text-xs" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="all">Any status</option>{["blank", "entered", "verified", "stale"].map((s) => <option key={s} value={s}>{s}</option>)}</select>
      </>}>
        <div className="overflow-x-auto max-h-[65vh]">
          <table className="w-full text-sm">
            <thead><tr><Th>Group</Th><Th>Prov</Th><Th>Value name</Th><Th>Key</Th><Th right>Value</Th><Th>Source</Th><Th>Effective</Th><Th>Status</Th><Th>Verified</Th><Th /></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className={clsx(r.status === "stale" && "bg-rose-50/50", r.status === "blank" && "text-gray-400")}>
                  <Td>{GROUPS[r.group] || r.group}</Td><Td mono>{r.province || "—"}</Td>
                  <Td className="!whitespace-normal max-w-[22rem]"><span className="text-gray-800">{r.label}</span></Td>
                  <Td mono className="text-[11px] text-gray-500">{r.key}</Td>
                  <Td right mono title={r.value || ""}>{r.value ? (r.kind === "json" ? <span className="text-[11px]">{r.value.slice(0, 40)}{r.value.length > 40 ? "…" : ""}</span> : r.value) : "—"}</Td>
                  <Td>{r.source_url ? <a className="text-primary-700 underline text-[11px]" href={r.source_url} target="_blank" rel="noreferrer">source</a> : <span className="text-gray-300">—</span>}</Td>
                  <Td mono>{r.effective_from || "—"}</Td>
                  <Td><StatusPill status={r.status} /></Td>
                  <Td className="text-[11px] text-gray-500">{r.verified_by ? `${r.verified_by} · ${(r.verified_on || "").slice(0, 10)}` : "—"}</Td>
                  <Td>
                    <div className="flex items-center gap-1">
                      <button type="button" className="btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1" onClick={() => setEdit(r)}><Pencil className="w-3 h-3" /> Edit</button>
                      {(r.status === "entered" || r.status === "stale") && <button type="button" className="btn-primary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1" onClick={() => verify.mutate({ key: r.key, status: "verified" })}><Check className="w-3 h-3" /> Mark verified</button>}
                      {r.status === "verified" && <button type="button" className="text-[11px] text-gray-500 inline-flex items-center gap-1" onClick={() => verify.mutate({ key: r.key, status: "estimated" })}><Undo2 className="w-3 h-3" /> Undo</button>}
                    </div>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {edit && <EditModal row={edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); refresh(); }} />}
    </div>
  );
}

function EditModal({ row, onClose, onSaved }: { row: StatValueRow; onClose: () => void; onSaved: () => void }) {
  const [value, setValue] = useState(row.value || (row.kind === "json" && row.shape ? JSON.stringify(row.shape, null, 2) : ""));
  const [source, setSource] = useState(row.source_url || "");
  const [eff, setEff] = useState(row.effective_from || "2026-01-01");
  const [note, setNote] = useState(row.note || "");
  const save = useMutation({
    mutationFn: (clear: boolean) => canadaApi.putValue({ key: row.key, value: clear ? null : value, source_url: source, effective_from: eff, note }),
    onSuccess: () => { toast.success("Saved — now mark it verified"); onSaved(); }, onError: (e) => toast.error(err(e)),
  });
  return (
    <Modal title={row.label} sub={`${row.key} · ${row.kind}${row.unit ? " · " + row.unit : ""}`} onClose={onClose}
      footer={<>
        {row.value && <button type="button" className="btn-secondary text-xs" onClick={() => save.mutate(true)}>Clear value</button>}
        <button type="button" className="btn-secondary text-xs" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary text-xs" disabled={save.isPending} onClick={() => save.mutate(false)}>Save</button>
      </>}>
      <div className="grid md:grid-cols-2 gap-4">
        <div className="space-y-3">
          <Hint tone="info">{row.help || "Enter the figure exactly as the official page shows it."}{row.kind === "json" && <> The JSON shape is pre-filled with zeros — <b>replace every number</b> from the source page; leave none at 0.</>}</Hint>
          <Field label="Source URL (required)"><input className={inputCls} placeholder="https://www.canada.ca/…" value={source} onChange={(e) => setSource(e.target.value)} /></Field>
          <Field label="Effective from"><input type="date" className={inputCls} value={eff} onChange={(e) => setEff(e.target.value)} /></Field>
          <Field label="Note"><input className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. 2026 edition, checked by …" /></Field>
        </div>
        <div>
          <Field label={row.kind === "json" ? "Rule (JSON)" : "Value"}>
            {row.kind === "json"
              ? <textarea className="input-base text-xs w-full font-mono" rows={14} value={value} onChange={(e) => setValue(e.target.value)} />
              : <input className={inputCls} type={row.kind === "date" ? "date" : "text"} value={value} onChange={(e) => setValue(e.target.value)} placeholder={row.kind === "number" ? "e.g. 25000" : ""} />}
          </Field>
        </div>
      </div>
    </Modal>
  );
}
