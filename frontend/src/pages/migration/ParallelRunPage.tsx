import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitCompare, Upload, Play, PenLine, Download } from "lucide-react";
import toast from "react-hot-toast";
import clsx from "clsx";
import { PageHeader, Spinner } from "../../components/ui";
import { useAuthStore } from "../../store/authStore";
import { migrationKitApi, type ParallelRun } from "../../api/migrationKit";
import { Th, Td, Panel, Hint, Tile, Field, StatusPill, inputCls, exportCsv, err } from "../canada/_shared";
import { localDateOf, thisMonthISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

/** /migration/parallel-run — upload the old system's register for a month, compare with Lexora HRM's stubs for that month,
 *  line by line per employee per component with a tolerance, and sign off "matched within CA$X". */
export default function ParallelRunPage() {
  const qc = useQueryClient();
  const country = "CA";
  const cur = "CA$";
  const { data: runs } = useQuery({ queryKey: ["pr-list"], queryFn: migrationKitApi.prList });
  const { data: comps } = useQuery({ queryKey: ["pr-components", country], queryFn: () => migrationKitApi.prComponents(country) });
  const [open, setOpen] = useState<ParallelRun | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [month, setMonth] = useState(thisMonthISO());
  const [vendor, setVendor] = useState("custom");
  const [tol, setTol] = useState("1");
  const refresh = () => qc.invalidateQueries({ queryKey: ["pr-list"] });
  const upload = useMutation({ mutationFn: () => migrationKitApi.prUpload(file!, month, country, vendor, Number(tol) || 0),
    onSuccess: (p) => { toast.success("Register uploaded — map the columns"); setOpen(p); refresh(); }, onError: (e) => toast.error(err(e)) });
  return (
    <div>
      <PageHeader title="Parallel-run reconciliation" icon={GitCompare} subtitle="Run the same month in both systems. Upload the old register, compare line by line, sign off the match. This is the migration proof." />
      <div className="grid lg:grid-cols-3 gap-3">
        <Panel title="1. Upload the old system's register" sub="CSV or XLSX, one row per employee (or per pay)">
          <div className="p-3 space-y-2">
            <Field label="Month (pay dates in this month)"><input type="month" className={inputCls} value={month} onChange={(e) => setMonth(e.target.value)} /></Field>
            <Field label="From"><select className={inputCls} value={vendor} onChange={(e) => setVendor(e.target.value)}><option value="custom">Other / custom</option>{Object.keys(comps?.presets || {}).map((v) => <option key={v} value={v}>{v} (unverified mapping)</option>)}</select></Field>
            <Field label={`Tolerance per line (${cur})`}><input type="number" step="0.01" className={inputCls} value={tol} onChange={(e) => setTol(e.target.value)} /></Field>
            <input type="file" accept=".csv,.xlsx" className="text-xs" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            <button type="button" className="btn-primary text-xs inline-flex items-center gap-1" disabled={!file || upload.isPending} onClick={() => upload.mutate()}><Upload className="w-3.5 h-3.5" /> Upload</button>
          </div>
        </Panel>
        <Panel title="Parallel runs" sub={`${(runs || []).length} so far`} className="lg:col-span-2">
          <table className="w-full text-sm"><thead><tr><Th>Month</Th><Th>File</Th><Th right>Tolerance</Th><Th>Status</Th><Th>Result</Th><Th>Signed</Th><Th /></tr></thead>
            <tbody>{(runs || []).map((p) => <tr key={p.id}><Td mono>{p.month}</Td><Td className="text-gray-600">{p.file_name}</Td><Td right mono>{cur}{p.tolerance}</Td><Td><StatusPill status={p.status} /></Td>
              <Td className="!whitespace-normal text-[11px] max-w-[22rem]">{p.summary?.headline || "—"}</Td><Td className="text-[11px] text-gray-500">{p.signed_by ? `${p.signed_by} · ${localDateOf(p.signed_at)}` : "—"}</Td>
              <Td><button type="button" className="text-[11px] text-primary-700 underline" onClick={async () => setOpen(await migrationKitApi.prGet(p.id))}>open</button></Td></tr>)}
              {!(runs || []).length && <tr><Td className="text-gray-400" colSpan={7 as any}>No parallel run yet.</Td></tr>}</tbody></table>
        </Panel>
      </div>
      {open && comps && <RunDetail pr={open} comps={comps.components} cur={cur} onChange={(p) => { setOpen(p); refresh(); }} />}
    </div>
  );
}

function RunDetail({ pr, comps, cur, onChange }: { pr: ParallelRun; comps: { key: string; label: string }[]; cur: string; onChange: (p: ParallelRun) => void }) {
  const headers = pr.headers || [];
  const [mapping, setMapping] = useState<Record<string, string>>({ ...(pr.mapping || {}) });
  const [tol, setTol] = useState(String(pr.tolerance));
  const [filter, setFilter] = useState<"all" | "mismatch">("mismatch");
  const [note, setNote] = useState("");
  const compare = useMutation({ mutationFn: () => migrationKitApi.prCompare(pr.id, { mapping, tolerance: Number(tol) }), onSuccess: (p) => { toast.success(p.summary?.headline || "Compared"); onChange(p); }, onError: (e) => toast.error(err(e)) });
  const sign = useMutation({ mutationFn: () => migrationKitApi.prSign(pr.id, note), onSuccess: (p) => { toast.success("Signed off"); onChange(p); }, onError: (e) => toast.error(err(e)) });
  const res = pr.result || {};
  const lines = useMemo(() => (res.lines || []).filter((l: any) => filter === "all" || l.status === "mismatch"), [res, filter]);
  const sample = pr.sample?.[0] || {};
  const rows = [{ key: "employee_code", label: "Employee Code (required)" }, ...comps];
  return (
    <div className="mt-3 space-y-3">
      <Panel title={`2. Map columns — ${pr.month} · ${pr.file_name}`} sub="Lexora HRM component on the left, the column in their register on the right. Unmapped components are skipped."
        actions={<><Field label={`Tolerance ${cur}`}><input type="number" step="0.01" className="input-base h-7 text-xs w-24" value={tol} onChange={(e) => setTol(e.target.value)} /></Field>
          <button type="button" className="btn-primary text-xs inline-flex items-center gap-1" disabled={!mapping.employee_code || compare.isPending || pr.status === "signed"} onClick={() => compare.mutate()}><Play className="w-3.5 h-3.5" /> Compare</button></>}>
        <div className="grid md:grid-cols-2 gap-x-6 p-3">
          {rows.map((c) => (
            <div key={c.key} className="flex items-center gap-2 py-1 border-b border-surface-border/60 text-xs">
              <span className="w-44 font-medium text-gray-700">{c.label}</span>
              <select className="input-base h-7 text-xs flex-1" value={mapping[c.key] || ""} onChange={(e) => setMapping({ ...mapping, [c.key]: e.target.value })}><option value="">— not in their file —</option>{headers.map((h) => <option key={h} value={h}>{h}</option>)}</select>
              <span className="w-24 truncate text-gray-400 tabular-nums">{mapping[c.key] ? String(sample[mapping[c.key]] ?? "") : ""}</span>
            </div>))}
        </div>
      </Panel>
      {res.summary && (<>
        <div className="flex flex-wrap gap-2">
          <Tile label="Employees compared" value={res.summary.employees_compared} /><Tile label="Lines matched" value={res.summary.lines_matched} tone="ok" /><Tile label="Lines differ" value={res.summary.lines_mismatched} tone={res.summary.lines_mismatched ? "bad" : "ok"} />
          <Tile label="Only in register" value={res.summary.missing_in_lexora} tone={res.summary.missing_in_lexora ? "warn" : "default"} /><Tile label="Only in Lexora HRM" value={res.summary.missing_in_register} tone={res.summary.missing_in_register ? "warn" : "default"} />
          <Tile label="Max difference" value={`${cur}${res.summary.max_abs_diff}`} /><Tile label="Lexora HRM stubs in month" value={res.lexora_stubs_in_month ?? "—"} />
        </div>
        <Hint tone={res.summary.matched_within_tolerance ? "ok" : "bad"}>{res.summary.headline}</Hint>
        <Panel title="3. Line by line" sub={`${(res.lines || []).length} lines`} actions={<>
          <button type="button" className={clsx("text-xs px-2 py-1 rounded", filter === "mismatch" ? "bg-primary-600 text-white" : "bg-gray-100")} onClick={() => setFilter("mismatch")}>Differences</button>
          <button type="button" className={clsx("text-xs px-2 py-1 rounded", filter === "all" ? "bg-primary-600 text-white" : "bg-gray-100")} onClick={() => setFilter("all")}>All</button>
          <button type="button" className="btn-secondary text-xs inline-flex items-center gap-1" onClick={() => exportCsv(`parallel_run_${pr.month}.csv`, ["Employee", "Component", "Old system", "Lexora HRM", "Difference", "Status"], (res.lines || []).map((l: any) => [l.employee_code, l.component, l.theirs, l.ours, l.diff, l.status]))}><Download className="w-3.5 h-3.5" /> CSV</button></>}>
          <div className="overflow-x-auto max-h-[50vh]"><table className="w-full text-sm"><thead><tr><Th>Employee</Th><Th>Component</Th><Th right>Old system</Th><Th right>Lexora HRM</Th><Th right>Difference</Th><Th>Status</Th></tr></thead>
            <tbody>{lines.map((l: any, i: number) => <tr key={i} className={l.status === "mismatch" ? "bg-rose-50/50" : ""}><Td mono>{l.employee_code}</Td><Td>{comps.find((c) => c.key === l.component)?.label || l.component}</Td><Td right mono>{l.theirs ?? "—"}</Td><Td right mono>{l.ours}</Td><Td right mono className={l.diff ? "font-semibold" : ""}>{l.diff ?? "—"}</Td><Td><StatusPill status={l.status} /></Td></tr>)}
              {!lines.length && <tr><Td className="text-gray-400" colSpan={6 as any}>{filter === "mismatch" ? "No differences above the tolerance." : "No lines."}</Td></tr>}</tbody></table></div>
          <div className="px-3 py-2 border-t border-surface-border text-[12px]">Employees only on one side: {(res.employees || []).filter((e: any) => e.status.startsWith("missing")).map((e: any) => `${e.employee_code} (${e.status.replace(/_/g, " ")})`).join(", ") || "none"}</div>
        </Panel>
        <Panel title="4. Sign-off" sub="recorded with your name and the exact tolerance">
          <div className="p-3 flex items-center gap-2">
            {pr.status === "signed" ? <Hint tone="ok">{pr.sign_note} — {pr.signed_by} · {localDateOf(pr.signed_at)}</Hint> : (<>
              <input className="input-base h-8 text-xs flex-1" placeholder="note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
              <button type="button" className="btn-primary text-xs inline-flex items-center gap-1" disabled={!res.summary.matched_within_tolerance || sign.isPending} onClick={() => sign.mutate()}><PenLine className="w-3.5 h-3.5" /> Sign: matched within {cur}{tol}</button></>)}
          </div>
        </Panel>
      </>)}
    </div>
  );
}
