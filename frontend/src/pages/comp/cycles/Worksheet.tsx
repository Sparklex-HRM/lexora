import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Lock, Unlock, Upload } from "lucide-react";
import toast from "react-hot-toast";
import { money } from "../../../api/endpoints";
import { err } from "../../hiring/api";
import { Col, Grid, Input, Modal, Note, Select, Stat, Textarea, downloadBlob } from "../../hiring/ui";
import { ccApi, cr, pct, Row } from "./api";
import { FormGrid, FormSection } from "../../../components/Modal";
import { collect, FieldError, firstError, Req, v } from "../../../lib/validators";

const LAB = "text-[10px] uppercase tracking-wide text-gray-400 font-semibold";
const HINT = "block text-[11px] text-gray-500 mt-0.5";
const MAX_CSV_BYTES = 2 * 1024 * 1024;

/* Manager worksheet (own reports) / HR worksheet (everyone). Each row is editable in place:
   proposed % or $, lump sum, promotion, comment. Budget used vs allowed updates live. */
export function BudgetBar({ u, label }: { u: Row; label?: string }) {
  if (!u) return null;
  const share = u.allowed ? Math.min(100, (u.used / u.allowed) * 100) : 0;
  return (
    <div className="card !py-2 !px-3 min-w-[14rem]">
      <div className="flex justify-between text-[10px] font-semibold uppercase tracking-wide text-gray-400"><span>{label || (u.scope === "total" ? "Whole cycle" : u.scope === "promotion" ? "Promotions" : `${u.scope} · ${u.key}`)}</span><span>{u.people ?? ""}{u.people != null ? " ppl" : ""}</span></div>
      <div className="flex items-baseline gap-2 mt-0.5"><span className={`text-sm font-bold tabular-nums ${u.over ? "text-red-600" : "text-gray-800"}`}>{money(u.used || 0)}</span>
        <span className="text-[11px] text-gray-500">of {u.allowed == null ? "no limit" : money(u.allowed)}{u.allowed_pct != null ? ` (${pct(u.allowed_pct)})` : ""}</span></div>
      <div className="h-1.5 bg-gray-100 rounded mt-1"><div className={`h-1.5 rounded ${u.over ? "bg-red-500" : share > 90 ? "bg-amber-500" : "bg-primary-500"}`} style={{ width: `${share}%` }} /></div>
      <div className="text-[11px] text-gray-500 mt-0.5">{u.over ? <span className="text-red-600 font-semibold">Over by {money(-(u.remaining || 0))}</span> : u.remaining == null ? "" : `Remaining ${money(u.remaining)}`}{u.used_pct != null ? ` · ${pct(u.used_pct)} of base` : ""}</div>
    </div>
  );
}

export default function Worksheet({ cycleId }: { cycleId: string }) {
  const qc = useQueryClient();
  const [dept, setDept] = useState("");
  const [mgr, setMgr] = useState("");
  const [rating, setRating] = useState("");
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const q = useQuery({ queryKey: ["comp", "worksheet", cycleId, dept, mgr], queryFn: () => ccApi.worksheet(cycleId, { department: dept || undefined, manager_id: mgr || undefined }) });
  const [edit, setEdit] = useState<Row | null>(null);
  const [imp, setImp] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const invalidate = () => qc.invalidateQueries({ queryKey: ["comp"] });
  const save = useMutation({
    mutationFn: (v: { id: string; body: Row }) => ccApi.saveLine(cycleId, v.id, v.body),
    onSuccess: (r) => { invalidate(); setEdit(null); const f = (r.line?.flags || []).map((x: Row) => x.text).join("; "); toast.success(f ? `Saved. Flags: ${f}` : "Saved.", { duration: f ? 7000 : 3000 }); },
    onError: (e) => toast.error(err(e), { duration: 8000 }),
  });
  const lock = useMutation({ mutationFn: (v: { ids: string[]; lock: boolean }) => ccApi.lock(cycleId, v.ids, v.lock), onSuccess: () => { invalidate(); toast.success("Done."); }, onError: (e) => toast.error(err(e)) });
  const importCsv = useMutation({
    mutationFn: (csv: string) => ccApi.importCsv(cycleId, csv),
    onSuccess: (r) => { invalidate(); setImp(false); toast.success(`${r.changed} line(s) updated.${r.errors?.length ? ` ${r.errors.length} row(s) skipped: ${r.errors.slice(0, 3).join(" | ")}` : ""}`, { duration: 9000 }); },
    onError: (e) => toast.error(err(e)),
  });
  if (q.isError) return <Note tone="warn">{err(q.error, "The worksheet could not be loaded.")}</Note>;
  if (!q.data) return <div className="text-sm text-gray-400">Loading…</div>;
  const { cycle, lines: allLines, team, budget_usage: bu, departments, managers } = q.data;
  const ratings: string[] = Array.from(new Set<string>((allLines as Row[]).map((l) => l.rating || "none"))).sort();
  const lines = (allLines as Row[]).filter((l) => (!rating || (l.rating || "none") === rating) && (!flaggedOnly || (l.flags || []).length > 0));
  const canEdit = !!cycle.can_edit;
  const isHr = !!cycle.is_hr;
  const flagCell = (l: Row) => (l.flags || []).length ? <span className="text-amber-700" title={(l.flags || []).map((f: Row) => f.text).join("\n")}>{(l.flags || []).map((f: Row) => f.text).join("; ")}</span> : "";
  const cols: Col<Row>[] = [
    { k: "employee_number", label: "Emp no" },
    { k: "employee_name", label: "Name", render: (l) => <span className="font-medium text-gray-800">{l.employee_name}{l.locked && <Lock className="w-3 h-3 inline ml-1 text-gray-400" />}</span> },
    { k: "department", label: "Dept" }, { k: "designation", label: "Designation" }, { k: "grade", label: "Grade" },
    ...(isHr ? [{ k: "manager_name", label: "Manager" } as Col<Row>] : []),
    { k: "rating", label: "Rating", render: (l) => l.rating || <span className="text-gray-400">none</span> },
    { k: "current_annual", label: "Current $/yr", align: "right", render: (l) => money(l.current_annual) },
    { k: "compa_ratio", label: "Compa", align: "right", render: (l) => <span title={l.midpoint ? `Midpoint ${money(l.midpoint)}` : "No midpoint for this grade"}>{cr(l.compa_ratio)}{l.compa_band ? ` ${l.compa_band}` : ""}</span> },
    { k: "rec_pct", label: "Matrix", align: "right", render: (l) => l.rec_pct == null ? "—" : <span title={`Range ${pct(l.min_pct)} to ${pct(l.max_pct)}`}>{pct(l.rec_pct)} <span className="text-gray-400">({pct(l.min_pct)}–{pct(l.max_pct)})</span></span>, csv: (l) => l.rec_pct },
    { k: "proposed_pct", label: "Proposed %", align: "right", render: (l) => <b className={l.include ? "" : "line-through text-gray-400"}>{pct(l.proposed_pct)}</b> },
    { k: "proposed_amount", label: "Increase $", align: "right", render: (l) => money(l.proposed_amount) },
    { k: "new_annual", label: "New $/yr", align: "right", render: (l) => money(l.new_annual) },
    ...(cycle.allow_lump_sum ? [{ k: "lump_sum", label: "Lump sum", align: "right", render: (l: Row) => l.lump_sum ? money(l.lump_sum) : "" } as Col<Row>] : []),
    { k: "promotion", label: "Promotion", render: (l) => l.promotion ? `${l.promo_designation || ""}${l.promo_grade ? ` (${l.promo_grade})` : ""}` || "Yes" : "", csv: (l) => l.promotion ? "yes" : "no" },
    { k: "manager_comment", label: "Manager note" },
    ...(isHr ? [{ k: "hr_comment", label: "HR note", render: (l: Row) => <span>{l.hr_comment}{l.hr_adjusted && <span className="ml-1 badge-purple">HR</span>}</span> } as Col<Row>] : []),
    { k: "flags", label: "Flags", render: flagCell, csv: (l) => (l.flags || []).map((f: Row) => f.text).join("; ") },
    ...(isHr && canEdit ? [{ k: "_lock", label: "", csv: () => "", render: (l: Row) => <button className="icon-btn" title={l.locked ? "Unlock" : "Lock"} onClick={(e) => { e.stopPropagation(); lock.mutate({ ids: [l.id], lock: !l.locked }); }}>{l.locked ? <Unlock className="w-3.5 h-3.5" /> : <Lock className="w-3.5 h-3.5" />}</button> } as Col<Row>] : []),
  ];
  return (
    <div>
      {cycle.guidelines && <Note><b>Guidelines:</b> {cycle.guidelines}</Note>}
      {!canEdit && <Note tone="warn">The worksheet is read-only while the cycle is {cycle.status_label}.</Note>}
      <div className="flex flex-wrap gap-2 mb-3">
        <Stat label={isHr ? "In view" : "Your team"} value={team.people} sub={`base ${money(team.base)}`} />
        <Stat label="Proposed" value={money(team.used)} sub={`${pct(team.used_pct)} of base`} accent />
        {Object.values(bu || {}).filter((u: any) => u.allowed != null || u.scope === "total").map((u: any) => <BudgetBar key={`${u.scope}:${u.key}`} u={u} />)}
      </div>
      <Grid rows={lines} cols={cols} keyOf={(l) => l.id} title={isHr ? "All eligible people" : "Your reports"} exportName={undefined}
        onRow={canEdit ? (l) => { if (l.locked && !isHr) toast("HR has locked this line."); else setEdit(l); } : undefined}
        filters={<>
          {isHr && <>
            <Select className="!w-40" value={dept} onChange={(e) => setDept(e.target.value)}><option value="">All departments</option>{(departments || []).map((d: string) => <option key={d}>{d}</option>)}</Select>
            <Select className="!w-44" value={mgr} onChange={(e) => setMgr(e.target.value)}><option value="">All managers</option>{(managers || []).map((m: any) => <option key={m[0]} value={m[0]}>{m[1] || m[0]}</option>)}</Select>
          </>}
          <Select className="!w-36" value={rating} onChange={(e) => setRating(e.target.value)} title="Filter by performance rating"><option value="">All ratings</option>{ratings.map((r) => <option key={r} value={r}>{r === "none" ? "No rating" : r}</option>)}</Select>
          <label className="flex items-center gap-1 text-xs text-gray-600" title="Lines outside the matrix range, over budget or missing a midpoint"><input type="checkbox" checked={flaggedOnly} onChange={(e) => setFlaggedOnly(e.target.checked)} />Flagged only</label>
        </>}
        actions={<>
          <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={async () => downloadBlob("comp-worksheet.csv", await ccApi.exportCsv(cycleId))}><Download className="w-3.5 h-3.5" />Export CSV</button>
          {canEdit && <button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => setImp(true)}><Upload className="w-3.5 h-3.5" />Import CSV</button>}
          {isHr && canEdit && <><button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => lock.mutate({ ids: lines.map((l: Row) => l.id), lock: true })}>Lock all</button>
            <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => lock.mutate({ ids: lines.map((l: Row) => l.id), lock: false })}>Unlock all</button></>}
        </>} />
      {edit && <LineEditor line={edit} cycle={cycle} isHr={isHr} saving={save.isPending} onClose={() => setEdit(null)} onSave={(body) => save.mutate({ id: edit.id, body })} />}
      {imp && <Modal title="Import worksheet CSV" size="lg" onClose={() => setImp(false)} subtitle="Export first, change proposed_pct (or proposed_amount), lump_sum, promotion (yes/no), promo_designation, promo_grade and comment, then upload the same file. Rows outside your team are skipped and reported.">
        <input ref={file} type="file" accept=".csv,text/csv" className="text-sm" disabled={importCsv.isPending} onChange={async (e) => {
          const f = e.target.files?.[0]; e.target.value = "";
          if (!f) return;
          if (!/\.csv$/i.test(f.name)) { toast.error("Pick a .csv file (export the worksheet first and edit that)."); return; }
          if (f.size > MAX_CSV_BYTES) { toast.error("The file is larger than 2 MB. Export again and keep only the columns you changed."); return; }
          const text = await f.text();
          if (!/^\s*employee_number\s*,/i.test(text)) { toast.error("The first column must be employee_number. Use the exported file as the template."); return; }
          importCsv.mutate(text);
        }} />
        <span className={HINT}>CSV only, up to 2 MB. The first row must be the header from Export CSV.</span>
      </Modal>}
    </div>
  );
}

function LineEditor({ line, cycle, isHr, saving, onClose, onSave }: { line: Row; cycle: Row; isHr: boolean; saving: boolean; onClose: () => void; onSave: (b: Row) => void }) {
  const [mode, setMode] = useState<"pct" | "amount">("pct");
  const [showErr, setShowErr] = useState(false);
  const [f, setF] = useState<Row>({ proposed_pct: line.proposed_pct, proposed_amount: line.proposed_amount, lump_sum: line.lump_sum || 0, promotion: !!line.promotion,
    promo_designation: line.promo_designation || "", promo_grade: line.promo_grade || "", comment: (isHr ? line.hr_comment : line.manager_comment) || "", include: line.include !== false });
  const cur = Number(line.current_annual) || 0;
  const livePct = mode === "pct" ? Number(f.proposed_pct) || 0 : cur ? (Number(f.proposed_amount) || 0) * 100 / cur : 0;
  const liveAmt = mode === "pct" ? cur * (Number(f.proposed_pct) || 0) / 100 : Number(f.proposed_amount) || 0;
  const out = line.max_pct != null && livePct > Number(line.max_pct) ? `Above the matrix maximum (${pct(line.max_pct)})` : line.min_pct != null && livePct > 0 && livePct < Number(line.min_pct) ? `Below the matrix minimum (${pct(line.min_pct)})` : "";
  const errs = collect({
    proposed_pct: mode === "pct" ? (v.required(f.proposed_pct, "The proposed %") || v.percent(f.proposed_pct, "The proposed %")) : null,
    proposed_amount: mode === "amount" ? (v.required(f.proposed_amount, "The increase") || v.range(f.proposed_amount, 0, undefined, "The increase")) : null,
    lump_sum: cycle.allow_lump_sum ? v.range(f.lump_sum, 0, undefined, "The lump sum") : null,
    promo: f.promotion && !String(f.promo_designation).trim() && !String(f.promo_grade).trim() ? "Give the new designation or the new grade for the promotion" : null,
    promo_designation: f.promotion ? v.maxLen(f.promo_designation, 120, "The designation") : null,
    promo_grade: f.promotion ? v.maxLen(f.promo_grade, 60, "The grade") : null,
    comment: (out && !String(f.comment).trim() ? "Add a comment: this % is outside the matrix range" : null) || v.maxLen(f.comment, 600, "The comment"),
  });
  const shown: Record<string, string> = showErr ? errs : {};
  const body: Row = { lump_sum: Number(f.lump_sum) || 0, promotion: f.promotion, promo_designation: f.promo_designation, promo_grade: f.promo_grade, comment: f.comment, ...(isHr ? { include: f.include } : {}) };
  if (mode === "pct") body.proposed_pct = Number(f.proposed_pct); else body.proposed_amount = Number(f.proposed_amount);
  const submit = () => { if (Object.keys(errs).length) { setShowErr(true); toast.error(firstError(errs)); return; } onSave(body); };
  return (
    <Modal title={`${line.employee_name} · ${line.employee_number}`} size="screen" onClose={onClose}
      subtitle={`${line.designation || ""} · ${line.department || ""} · grade ${line.grade || "—"} · rating ${line.rating || "none"} · compa ${cr(line.compa_ratio)}${line.compa_band ? ` (${line.compa_band})` : ""}`}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={saving} onClick={submit}>Save</button></>}>
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-3">
        <Stat label="Current" value={money(cur)} sub={`${money(cur / 12)} a month`} />
        <Stat label="Matrix" value={line.rec_pct == null ? "—" : pct(line.rec_pct)} sub={line.rec_pct == null ? "no row for this rating" : `allowed ${pct(line.min_pct)} to ${pct(line.max_pct)}`} />
        <Stat label="Increase" value={money(liveAmt)} sub={pct(livePct)} accent />
        <Stat label="New annual" value={money(cur + liveAmt)} />
        <Stat label="New monthly" value={money((cur + liveAmt) / 12)} sub="annual ÷ 12" />
        <Stat label="Midpoint" value={line.midpoint ? money(line.midpoint) : "—"} sub={line.midpoint ? `compa after ${cr((cur + liveAmt) / Number(line.midpoint))}` : "no midpoint for this grade"} />
      </div>
      {out && <Note tone="warn">{out}. Add a comment below: calibration will see this as an outlier.</Note>}
      <FormSection title="Pay change" hint="Percentage increase on current annual fixed pay. Stay within the matrix range unless you explain why.">
        <FormGrid cols={4}>
          <div><label className={LAB}>Enter as</label><Select value={mode} onChange={(e) => setMode(e.target.value as any)}><option value="pct">% of current pay</option><option value="amount">$ per year</option></Select></div>
          {mode === "pct"
            ? <div><label className={LAB}>Proposed %<Req /></label><Input type="number" step="0.25" min={0} max={100} autoFocus value={f.proposed_pct ?? ""} onChange={(e) => setF({ ...f, proposed_pct: e.target.value })} /><span className={HINT}>0 to 100.</span><FieldError msg={shown.proposed_pct} /></div>
            : <div><label className={LAB}>Increase $ per year<Req /></label><Input type="number" min={0} autoFocus value={f.proposed_amount ?? ""} onChange={(e) => setF({ ...f, proposed_amount: e.target.value })} /><span className={HINT}>Annual dollar increase; the % is worked out from it.</span><FieldError msg={shown.proposed_amount} /></div>}
          {cycle.allow_lump_sum && <div><label className={LAB}>One-time lump sum $</label><Input type="number" min={0} value={f.lump_sum} onChange={(e) => setF({ ...f, lump_sum: e.target.value })} /><span className={HINT}>Paid once; does not change annual pay.</span><FieldError msg={shown.lump_sum} /></div>}
          {isHr && <div><label className={LAB}>In this cycle</label><label className="flex items-center gap-2 text-sm mt-1.5"><input type="checkbox" checked={f.include} onChange={(e) => setF({ ...f, include: e.target.checked })} />Include this person</label><span className={HINT}>Untick to leave them out without deleting the line.</span></div>}
        </FormGrid>
      </FormSection>
      <FormSection title="Promotion">
        <FormGrid cols={4}>
          <div><label className={LAB}>Promotion</label><label className="flex items-center gap-2 text-sm mt-1.5"><input type="checkbox" checked={f.promotion} onChange={(e) => setF({ ...f, promotion: e.target.checked })} />Recommend a promotion</label></div>
          {f.promotion && <>
            <div><label className={LAB}>New designation<Req /></label><Input maxLength={120} value={f.promo_designation} onChange={(e) => setF({ ...f, promo_designation: e.target.value })} placeholder={line.designation || ""} /><FieldError msg={shown.promo_designation || shown.promo} /></div>
            <div><label className={LAB}>New grade<Req /></label><Input maxLength={60} value={f.promo_grade} onChange={(e) => setF({ ...f, promo_grade: e.target.value })} placeholder={line.grade || ""} /><span className={HINT}>Give the designation, the grade or both.</span><FieldError msg={shown.promo_grade} /></div>
          </>}
        </FormGrid>
      </FormSection>
      <FormSection title="Comment">
        <label className={LAB}>{isHr ? "HR comment" : "Your comment"}{out && <Req />}</label>
        <Textarea rows={2} maxLength={600} value={f.comment} onChange={(e) => setF({ ...f, comment: e.target.value })} placeholder="Why this %, especially outside the matrix" />
        <span className={HINT}>Calibration reads this. Required when the % is outside the matrix range (max 600 characters).</span>
        <FieldError msg={shown.comment} />
        {isHr && line.manager_comment && <div className="mt-2 text-xs text-gray-500">Manager wrote: "{line.manager_comment}"</div>}
      </FormSection>
    </Modal>
  );
}
