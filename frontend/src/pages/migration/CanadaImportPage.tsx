import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { DatabaseZap, Download, Upload, ArrowRight, Undo2, Trash2 } from "lucide-react";
import toast from "react-hot-toast";
import clsx from "clsx";
import { PageHeader, Spinner } from "../../components/ui";
import { migrationKitApi, cellText, type CaBatch } from "../../api/migrationKit";
import { Th, Td, Panel, Hint, Tile, Field, StatusPill, inputCls, err } from "../canada/_shared";
import { formatDateTime, localDateOf } from "../../lib/datetime";   // 2026-10-25: instants shown in the viewer's zone

/** /migration/canada — Canada import kit: template → upload → map → check → import → undo (batch-tagged). */
export default function CanadaImportPage() {
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const { data: cat, isLoading } = useQuery({ queryKey: ["ca-mig-catalog"], queryFn: migrationKitApi.caCatalog });
  const { data: batches } = useQuery({ queryKey: ["ca-mig-batches"], queryFn: migrationKitApi.caBatches });
  const [ft, setFt] = useState(params.get("type") || "ca_employees");
  const [vendor, setVendor] = useState("lexora");
  const [file, setFile] = useState<File | null>(null);
  const [batch, setBatch] = useState<CaBatch | null>(null);
  useEffect(() => { const t = params.get("type"); if (t) setFt(t); }, [params]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["ca-mig-batches"] });
  const upload = useMutation({ mutationFn: () => migrationKitApi.caUpload(file!, ft, vendor), onSuccess: (b) => { toast.success(`${b.row_count} rows read`); setBatch(b); refresh(); }, onError: (e) => toast.error(err(e)) });
  const rollback = useMutation({ mutationFn: (id: string) => migrationKitApi.caRollback(id), onSuccess: (b) => { toast.success("Undone"); setBatch(b); refresh(); }, onError: (e) => toast.error(err(e)) });
  const discard = useMutation({ mutationFn: (id: string) => migrationKitApi.caDiscard(id), onSuccess: () => { toast.success("Discarded"); setBatch(null); refresh(); }, onError: (e) => toast.error(err(e)) });
  if (isLoading || !cat) return <div className="grid place-items-center py-20"><Spinner className="h-7 w-7" /></div>;
  const def = cat.file_types.find((f) => f.key === ft)!;
  return (
    <div>
      <PageHeader title="Data migration — Canada" icon={DatabaseZap} subtitle="Employees · TD1 claims · salary history · YTD per month · leave balances. Every import is a batch you can undo." />
      <div className="grid lg:grid-cols-3 gap-3">
        <Panel title="1. Template + upload" sub="download, fill, upload">
          <div className="p-3 space-y-2">
            <Field label="File type"><select className={inputCls} value={ft} onChange={(e) => { setFt(e.target.value); setBatch(null); }}>{cat.file_types.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}</select></Field>
            <p className="text-[11px] text-gray-500">{def.help}</p>
            <div className="flex gap-2"><button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => migrationKitApi.caTemplate(ft, "xlsx")}><Download className="w-3.5 h-3.5" /> Excel</button><button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => migrationKitApi.caTemplate(ft, "csv")}><Download className="w-3.5 h-3.5" /> CSV</button></div>
            <Field label="From"><select className={inputCls} value={vendor} onChange={(e) => setVendor(e.target.value)}>{cat.vendors.map((v) => <option key={v.code} value={v.code}>{v.name}{v.verified ? "" : " (unverified mapping)"}</option>)}</select></Field>
            <input type="file" accept=".csv,.xlsx" className="text-xs" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            <button type="button" className="btn-primary text-xs inline-flex items-center gap-1" disabled={!file || upload.isPending} onClick={() => upload.mutate()}><Upload className="w-3.5 h-3.5" /> Upload</button>
            <div className="text-[11px] text-gray-400 border-t border-surface-border pt-2">Not built: {Object.values(cat.not_built).join(" ")}</div>
          </div>
        </Panel>
        <Panel title="Batches" sub="newest first" className="lg:col-span-2">
          <div className="overflow-x-auto max-h-[40vh]"><table className="w-full text-sm"><thead><tr><Th>When</Th><Th>Type</Th><Th>File</Th><Th right>Rows</Th><Th right>Errors</Th><Th>Status</Th><Th /></tr></thead>
            <tbody>{(batches || []).map((b) => <tr key={b.id} className={batch?.id === b.id ? "bg-primary-50/40" : ""}><Td mono className="text-[11px]">{formatDateTime(b.created_at)}</Td><Td>{b.file_type_label}</Td><Td className="text-gray-600">{b.file_name}</Td><Td right mono>{b.row_count}</Td><Td right mono>{b.error_count}</Td><Td><StatusPill status={b.status} /></Td>
              <Td><div className="flex gap-2"><button type="button" className="text-[11px] text-primary-700 underline" onClick={async () => { const full = await migrationKitApi.caBatch(b.id); setBatch(full); setFt(full.file_type); }}>open</button>
                {b.status === "committed" && <button type="button" className="text-[11px] text-rose-600 inline-flex items-center gap-1" onClick={() => { if (confirm("Undo this import batch?")) rollback.mutate(b.id); }}><Undo2 className="w-3 h-3" /> undo</button>}
                {(b.status === "uploaded" || b.status === "validated") && <button type="button" className="text-[11px] text-gray-500 inline-flex items-center gap-1" onClick={() => discard.mutate(b.id)}><Trash2 className="w-3 h-3" /></button>}</div></Td></tr>)}
              {!(batches || []).length && <tr><Td className="text-gray-400" colSpan={7 as any}>No Canada imports yet.</Td></tr>}</tbody></table></div>
        </Panel>
      </div>
      {batch && <BatchWork batch={batch} fields={cat.file_types.find((f) => f.key === batch.file_type)!.fields} onChange={(b) => { setBatch(b); refresh(); }} />}
    </div>
  );
}

function BatchWork({ batch, fields, onChange }: { batch: CaBatch; fields: any[]; onChange: (b: CaBatch) => void }) {
  const headers = batch.headers || [];
  const [mapping, setMapping] = useState<Record<string, string | null>>({ ...(batch.mapping || {}) });
  const [savePreset, setSavePreset] = useState(batch.vendor !== "lexora");
  const [partial, setPartial] = useState(false);
  const [showErr, setShowErr] = useState(true);
  useEffect(() => setMapping({ ...(batch.mapping || {}) }), [batch.id]);
  const used = useMemo(() => new Set(Object.values(mapping).filter(Boolean) as string[]), [mapping]);
  const missing = fields.filter((f) => f.required && !mapping[f.key] && !(f.key === "first_name" && mapping["full_name"]));
  const sample: Record<string, any> = batch.sample?.[0] || {};
  const validate = useMutation({ mutationFn: () => migrationKitApi.caValidate(batch.id, { mapping, save_preset: savePreset }), onSuccess: (b) => { toast.success(`${b.summary.rows} rows checked — ${b.summary.with_errors} with problems`); onChange(b); }, onError: (e) => toast.error(err(e)) });
  const commit = useMutation({ mutationFn: () => migrationKitApi.caCommit(batch.id, partial), onSuccess: (b) => { toast.success(`Imported ${b.result?.rows_written} rows`); onChange(b); }, onError: (e) => toast.error(err(e)) });
  const rows = (batch.rows || []).filter((r) => !showErr || r.errors.length || r.warnings.length);
  const locked = batch.status === "committed" || batch.status === "rolled_back";
  return (
    <div className="mt-3 space-y-3">
      {batch.preset_note && <Hint tone="warn">{batch.preset_note}</Hint>}
      <Panel title={`2. Map columns — ${batch.file_type_label} · ${batch.file_name}`} sub={`${batch.row_count} rows · ${headers.length} columns in your file`}
        actions={<><label className="flex items-center gap-1 text-xs text-gray-600"><input type="checkbox" className="accent-primary-600" checked={savePreset} onChange={(e) => setSavePreset(e.target.checked)} /> remember for {batch.vendor}</label>
          <button type="button" className="btn-primary text-xs inline-flex items-center gap-1" disabled={locked || missing.length > 0 || validate.isPending} onClick={() => validate.mutate()}><ArrowRight className="w-3.5 h-3.5" /> Check the rows</button></>}>
        {missing.length > 0 && <div className="p-3"><Hint tone="warn">Still needed: <b>{missing.map((f) => f.label).join(", ")}</b></Hint></div>}
        <div className="overflow-x-auto max-h-[45vh]"><table className="w-full text-sm"><thead><tr><Th>Lexora HRM column</Th><Th>Must fill?</Th><Th>Column in your file</Th><Th>First row shows</Th><Th>Format</Th></tr></thead>
          <tbody>{fields.map((f) => { const src = mapping[f.key] || ""; const miss = f.required && !src && !(f.key === "first_name" && mapping["full_name"]); return (
            <tr key={f.key} className={clsx(miss ? "bg-rose-50/60" : src ? "" : "bg-gray-50/40")}><Td className="font-medium text-gray-800">{f.label}<div className="text-[10px] text-gray-400 font-normal">{f.key}</div></Td><Td>{f.required ? <span className="badge-red">yes</span> : <span className="text-gray-400 text-[11px]">optional</span>}</Td>
              <Td><select disabled={locked} value={src} onChange={(e) => setMapping((m) => { const n: Record<string, string | null> = { ...m, [String(f.key)]: e.target.value || null }; if (e.target.value) for (const k of Object.keys(n)) if (k !== f.key && n[k] === e.target.value) n[k] = null; return n; })} className={clsx("input-base h-8 text-xs min-w-[220px]", miss && "border-rose-400")}><option value="">— not in my file —</option>{headers.map((h) => <option key={h} value={h}>{h}{used.has(h) && h !== src ? "  (used)" : ""}</option>)}</select></Td>
              <Td mono className="text-gray-600 max-w-[16rem] truncate">{src ? cellText(sample[src]) : <span className="text-gray-300">—</span>}</Td><Td className="text-[11px] text-gray-500 !whitespace-normal max-w-[22rem]">{f.kind}{f.choices?.length ? ` (${f.choices.join(" / ")})` : ""}{f.help ? ` · ${f.help}` : ""}</Td></tr>); })}</tbody></table></div>
      </Panel>
      {batch.status !== "uploaded" && (
        <Panel title="3. Check + import" sub={`${batch.summary.ok} OK · ${batch.summary.with_errors} with problems · ${batch.summary.with_warnings} with warnings`}
          actions={<>
            <button type="button" className={clsx("text-xs px-2 py-1 rounded", showErr ? "bg-primary-600 text-white" : "bg-gray-100")} onClick={() => setShowErr(!showErr)}>{showErr ? "Problems only" : "All rows"}</button>
            {!locked && <><label className="flex items-center gap-1 text-xs"><input type="checkbox" className="accent-primary-600" checked={partial} onChange={(e) => setPartial(e.target.checked)} /> import the good rows only</label>
              <button type="button" className="btn-primary text-xs" disabled={commit.isPending || (batch.error_count > 0 && !partial)} onClick={() => commit.mutate()}>Import {batch.summary.ok} rows</button></>}
            {batch.status === "committed" && <span className="badge-green">imported {localDateOf(batch.committed_at)}</span>}
            {batch.status === "rolled_back" && <span className="badge-red">undone</span>}</>}>
          <div className="flex flex-wrap gap-2 p-3">{batch.summary.to_create != null && <><Tile label="To create" value={batch.summary.to_create} /><Tile label="To update" value={batch.summary.to_update} /></>}
            {(batch.summary.totals_by_month || []).map((t: any) => <Tile key={t.month} label={t.month} value={`${t.employees} emp · gross ${t.gross}`} />)}
            {batch.result?.rows_written != null && <Tile label="Written" value={batch.result.rows_written} tone="ok" />}{batch.result?.note && <Hint tone="info">{batch.result.note}</Hint>}</div>
          <div className="overflow-x-auto max-h-[45vh]"><table className="w-full text-sm"><thead><tr><Th>Row</Th><Th>Problems</Th><Th>Warnings</Th>{fields.slice(0, 8).map((f) => <Th key={f.key}>{f.label}</Th>)}</tr></thead>
            <tbody>{rows.slice(0, 400).map((r) => <tr key={r.row_no} className={r.errors.length ? "bg-rose-50/60" : r.warnings.length ? "bg-amber-50/40" : ""}><Td mono>{r.row_no}</Td><Td className="!whitespace-normal text-rose-700 max-w-[20rem]">{r.errors.join(" · ")}</Td><Td className="!whitespace-normal text-amber-700 max-w-[20rem]">{r.warnings.join(" · ")}</Td>{fields.slice(0, 8).map((f) => <Td key={f.key} mono>{cellText(r.data[f.key])}</Td>)}</tr>)}
              {!rows.length && <tr><Td className="text-gray-400" colSpan={11 as any}>{showErr ? "No problems." : "No rows."}</Td></tr>}</tbody></table></div>
        </Panel>)}
    </div>
  );
}
