import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Package } from "lucide-react";
import DataGrid, { WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { employeeApi, money } from "../../api/endpoints";
import { assetsApi, type AssetRow } from "../../api/assets";
import { errText } from "../../api/platform";
import { useLang, useT } from "../../i18n";
import { fmtDate, fmtDateTime } from "../../i18n/format";
import { useAuthStore } from "../../store/authStore";
import ReadPage from "../../i18n/ReadPage";
import { AuthImg } from "../mobile/_mobile";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

const FIELD = "input w-full";
const LABEL = "block text-xs font-semibold text-gray-500 mb-1";
import { CustomFieldsSection, saveCustomValues, useCustomFieldDefs } from "../../components/CustomFieldsSection";   // D1
import { todayISO } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const HR = ["superadmin", "org_admin", "hr_admin", "hr_manager"];

/** /engage/assets — the asset register: master, assign, acknowledge state, return with condition + photos, events. */
export default function AssetsPage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const role = useAuthStore((s) => s.user?.role) || "";
  const hr = HR.includes(role);
  const [status, setStatus] = useState("");
  const list = useQuery({ queryKey: ["assets", status], queryFn: () => assetsApi.list({ status: status || undefined }) });
  const summary = useQuery({ queryKey: ["assets-summary"], queryFn: () => assetsApi.summary() });
  const [edit, setEdit] = useState<Partial<AssetRow> | null>(null);
  const [open, setOpen] = useState<AssetRow | null>(null);
  const refresh = () => { qc.invalidateQueries({ queryKey: ["assets"] }); qc.invalidateQueries({ queryKey: ["assets-summary"] }); };
  const TONE: Record<string, string> = { in_stock: "bg-gray-100 text-gray-600", assigned: "bg-green-100 text-green-700", returned: "bg-blue-100 text-blue-700", lost: "bg-red-100 text-red-700", retired: "bg-gray-200 text-gray-600" };
  const cols: GridColumn<AssetRow>[] = [
    { key: "item", label: t("assets.item"), render: (r) => <div><div className="font-medium">{r.item}</div><div className="text-[11px] text-gray-400">{r.category || ""}{r.serial ? ` · ${r.serial}` : ""}</div></div> },
    { key: "tag", label: t("assets.tag"), width: "90px" },
    { key: "status", label: t("common.status"), render: (r) => <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${TONE[r.status] || ""}`}>{r.status.replace("_", " ")}</span> },
    { key: "employee_name", label: t("team.person"), render: (r) => r.employee_name ? <span>{r.employee_name}{r.status === "assigned" && <span className={`ml-1 text-[10px] ${r.acknowledged ? "text-green-700" : "text-amber-700"}`}>{r.acknowledged ? "✓ ack" : "ack pending"}</span>}</span> : "—" },
    { key: "issued_on", label: t("assets.issued_on"), render: (r) => fmtDate(r.issued_on, lang), text: (r) => r.issued_on || "" },
    { key: "returned_on", label: t("assets.returned_on"), render: (r) => fmtDate(r.returned_on, lang), text: (r) => r.returned_on || "" },
    { key: "location", label: "Location" }, { key: "purchase_cost", label: "Cost", type: "money" },
    { key: "warranty_until", label: "Warranty", render: (r) => <span className={r.warranty_state === "expired" ? "text-red-600" : r.warranty_state === "expiring" ? "text-amber-700" : ""}>{fmtDate(r.warranty_until, lang)}</span>, text: (r) => r.warranty_until || "" },
  ];
  const s = summary.data;
  return (
    <div>
      <PageHeader title={t("nav.Assets")} icon={Package} subtitle="Asset register · issue, acknowledgement, returns and warranty" actions={<ReadPage />} />
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
        <StatTile label={t("common.all")} value={s?.total ?? "—"} accent /><StatTile label="In stock" value={s?.by_status?.in_stock ?? "—"} /><StatTile label="Assigned" value={s?.by_status?.assigned ?? "—"} />
        <StatTile label="Total cost" value={s ? money(s.total_cost) : "—"} /><StatTile label="Warranty ending (30 d)" value={s?.warranty_expiring_30d ?? "—"} />
      </div>
      <DataGrid columns={cols} rows={list.data?.assets || []} rowKey={(r) => r.id} minWidth={1000} exportName="asset-register" onRowClick={(r) => setOpen(r)}
        toolbar={<div className="flex gap-2 items-center flex-wrap">{hr && <button className="btn-primary text-sm" onClick={() => setEdit({})}>+ {t("common.add")}</button>}
          <select className={`input !w-auto !py-1.5 text-sm`} value={status} onChange={(e) => setStatus(e.target.value)}><option value="">{t("common.all")}</option>{(list.data?.states || []).map((x) => <option key={x} value={x}>{x.replace("_", " ")}</option>)}</select></div>} />
      {edit && <EditSheet a={edit} categories={list.data?.categories || []} onClose={() => { setEdit(null); refresh(); }} />}
      {open && <DetailSheet id={open.id} hr={hr} conditions={list.data?.conditions || []} onClose={() => { setOpen(null); refresh(); }} onEdit={(a) => { setOpen(null); setEdit(a); }} />}
    </div>
  );
}

function EditSheet({ a, categories, onClose }: { a: Partial<AssetRow>; categories: string[]; onClose: () => void }) {
  const t = useT();
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list(), enabled: !a.id });
  // editing: keep the stored category even when it is blank (it used to fall back to "Laptop" and overwrite it)
  const [f, setF] = useState<any>({ item: a.item || "", category: a.id ? a.category || "" : a.category || "Laptop", tag: a.tag || "", serial: a.serial || "", purchase_date: a.purchase_date || "", purchase_cost: a.purchase_cost ?? "", warranty_until: a.warranty_until || "", location: a.location || "", note: a.note || "", employee_id: "", issued_on: "", status: "" });
  const today = todayISO();
  const errs = collect({
    item: v.required(String(f.item).trim(), t("assets.item")) || v.maxLen(f.item, 200, t("assets.item")),
    tag: v.maxLen(f.tag, 60, t("assets.tag")), serial: v.maxLen(f.serial, 80, "Serial"),
    purchase_date: v.notFuture(f.purchase_date, "Purchase date"),
    purchase_cost: v.range(f.purchase_cost, 0, undefined, "Purchase cost"),
    warranty_until: v.dateOrder(f.purchase_date, f.warranty_until, "Warranty end cannot be before the purchase date"),
    issued_on: f.employee_id ? (v.notFuture(f.issued_on, t("assets.issued_on")) || v.dateOrder(f.purchase_date, f.issued_on, "Issue date cannot be before the purchase date")) : null,
  });
  // an issued asset is closed through Record return (incl. lost) on the detail sheet, not here
  const statusChoices: [string, string][] = !a.id || a.status === "assigned" ? [] : [["", `Keep — ${String(a.status || "").replace("_", " ")}`], ...(a.status !== "retired" ? [["retired", "Retire (no longer in use)"] as [string, string]] : []), ...(a.status !== "lost" ? [["lost", "Mark lost"] as [string, string]] : []), ...(a.status !== "assigned" && a.status !== "in_stock" ? [["in_stock", "Back to stock"] as [string, string]] : [])];
  const [cfValues, setCfValues] = useState<Record<string, any>>({});
  const cfDefs = useCustomFieldDefs("asset");
  const m = useMutation({ mutationFn: async () => {
    const body = { ...f, item: String(f.item).trim(), category: f.category || a.category || undefined, purchase_cost: f.purchase_cost === "" ? null : Number(f.purchase_cost), purchase_date: f.purchase_date || null, warranty_until: f.warranty_until || null, employee_id: f.employee_id || undefined, issued_on: f.issued_on || undefined, status: a.id && f.status ? f.status : undefined };
    const row = a.id ? await assetsApi.update(a.id, body) : await assetsApi.create(body);
    if (!a.id && Object.keys(cfValues).length) { const problem = await saveCustomValues("asset", row.id, cfValues, cfDefs.data?.items || []); if (problem) toast.error(`Saved, but the custom fields were not: ${problem}`, { duration: 8000 }); }
    return row;
  }, onSuccess: () => { toast.success(t("common.saved")); onClose(); }, onError: (e) => toast.error(errText(e)) });
  return (
    <WideModal title={a.id ? a.item || "" : `+ ${t("assets.item")}`} onClose={onClose}>
      <form className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3" onSubmit={(e) => { e.preventDefault(); if (Object.keys(errs).length) { toast.error(firstError(errs)); return; } m.mutate(); }}>
        <div className="sm:col-span-2 lg:col-span-3 text-[11px] font-semibold uppercase tracking-wide text-primary-700 border-b border-surface-border pb-1">Asset details</div>
        <div><label className={LABEL}>{t("assets.item")}<Req /></label><input className={FIELD} required maxLength={200} value={f.item} onChange={(e) => setF({ ...f, item: e.target.value })} placeholder="e.g. Dell Latitude 5440" /><FieldError msg={errs.item} /></div>
        <div><label className={LABEL}>Category<Req /></label><select className={FIELD} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{a.id && !f.category && <option value="">— not set —</option>}{categories.map((c) => <option key={c}>{c}</option>)}</select></div>
        <div><label className={LABEL}>{t("assets.tag")}</label><input className={FIELD} maxLength={60} value={f.tag} onChange={(e) => setF({ ...f, tag: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">Your sticker / asset tag, not the maker's serial</span><FieldError msg={errs.tag} /></div>
        <div><label className={LABEL}>Serial</label><input className={FIELD} maxLength={80} value={f.serial} onChange={(e) => setF({ ...f, serial: e.target.value })} /><FieldError msg={errs.serial} /></div>
        <div><label className={LABEL}>Location</label><input className={FIELD} value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} /></div>
        {a.id && statusChoices.length > 1 && <div><label className={LABEL}>{t("common.status")}</label><select className={FIELD} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>{statusChoices.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>{f.status === "lost" && <span className="block text-[11px] text-amber-700 mt-0.5">Marks it lost — recover any cost through the exit / payroll process</span>}</div>}
        <div className="sm:col-span-2 lg:col-span-3 text-[11px] font-semibold uppercase tracking-wide text-primary-700 border-b border-surface-border pb-1 mt-1">Purchase &amp; warranty</div>
        <div><label className={LABEL}>Purchase date</label><input type="date" max={today} className={FIELD} value={f.purchase_date} onChange={(e) => setF({ ...f, purchase_date: e.target.value })} /><FieldError msg={errs.purchase_date} /></div>
        <div><label className={LABEL}>Purchase cost ($)</label><input type="number" min={0} step="0.01" className={FIELD} value={f.purchase_cost} onChange={(e) => setF({ ...f, purchase_cost: e.target.value })} /><FieldError msg={errs.purchase_cost} /></div>
        <div><label className={LABEL}>Warranty until</label><input type="date" min={f.purchase_date || undefined} className={FIELD} value={f.warranty_until} onChange={(e) => setF({ ...f, warranty_until: e.target.value })} /><span className="block text-[11px] text-gray-400 mt-0.5">Maker's or AMC warranty end</span><FieldError msg={errs.warranty_until} /></div>
        <div className="sm:col-span-2 lg:col-span-3"><label className={LABEL}>{t("common.note")}</label><input className={FIELD} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="Vendor, invoice no, accessories…" /></div>
        {!a.id && <><div className="sm:col-span-2 lg:col-span-3 text-[11px] font-semibold uppercase tracking-wide text-primary-700 border-b border-surface-border pb-1 mt-1">Issue now (optional)</div>
          <div><label className={LABEL}>Issue now to (optional)</label><select className={FIELD} value={f.employee_id} onChange={(e) => setF({ ...f, employee_id: e.target.value, issued_on: e.target.value && !f.issued_on ? today : f.issued_on })}><option value="">— in stock —</option>{((emps.data || []) as any[]).filter((e) => e.status !== "terminated").map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name}</option>)}</select><span className="block text-[11px] text-gray-400 mt-0.5">Leave in stock if it is not handed out yet</span></div>
          <div><label className={LABEL}>{t("assets.issued_on")}</label><input type="date" max={today} className={FIELD} value={f.issued_on} disabled={!f.employee_id} onChange={(e) => setF({ ...f, issued_on: e.target.value })} /><FieldError msg={errs.issued_on} /></div></>}
        <div className="md:col-span-3">{a.id ? <CustomFieldsSection entity="asset" id={a.id} title="Custom fields" /> : <CustomFieldsSection entity="asset" values={cfValues} onChange={setCfValues} title="Custom fields" />}</div>
        <div className="md:col-span-3"><button className="btn-primary" disabled={m.isPending}>{t("common.save")}</button></div>
      </form>
    </WideModal>
  );
}

function DetailSheet({ id, hr, conditions, onClose, onEdit }: { id: string; hr: boolean; conditions: string[]; onClose: () => void; onEdit: (a: AssetRow) => void }) {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["asset", id], queryFn: () => assetsApi.detail(id) });
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const [assignTo, setAssignTo] = useState(""); const [ret, setRet] = useState({ condition: "good", note: "", returned_on: "", lost: false });
  const [issue, setIssue] = useState({ issued_on: "", note: "" });
  const todayIso = todayISO();
  const fileRef = useRef<HTMLInputElement>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["asset", id] });
  const assign = useMutation({ mutationFn: () => assetsApi.assign(id, { employee_id: assignTo, issued_on: issue.issued_on || undefined, note: issue.note.trim() || undefined } as any), onSuccess: () => { toast.success(t("common.done")); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const doReturn = useMutation({ mutationFn: () => assetsApi.doReturn(id, { ...ret, returned_on: ret.returned_on || undefined }), onSuccess: () => { toast.success(t("common.done")); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const photo = useMutation({ mutationFn: (f: File) => assetsApi.addPhoto(id, f), onSuccess: refresh, onError: (e) => toast.error(errText(e)) });
  const a = q.data?.asset;
  const retErr = !a ? null : v.notFuture(ret.returned_on, t("assets.returned_on")) || v.dateOrder(String(a.issued_on || "").slice(0, 10), ret.returned_on, "Return date cannot be before the issue date");
  const issueErr = !a ? null : v.notFuture(issue.issued_on, t("assets.issued_on")) || v.dateOrder(String(a.purchase_date || "").slice(0, 10), issue.issued_on, "Issue date cannot be before the purchase date");
  return (
    <WideModal title={a ? `${a.item}${a.tag ? ` · ${a.tag}` : ""}` : t("common.loading")} onClose={onClose}>
      {a && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div className="md:col-span-2 space-y-3">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-xs">
              {[["Category", a.category], ["Serial", a.serial], ["Location", a.location], ["Purchase", fmtDate(a.purchase_date, lang)], ["Cost", a.purchase_cost != null ? money(a.purchase_cost) : "—"], ["Warranty", `${fmtDate(a.warranty_until, lang)} (${a.warranty_state})`],
                [t("common.status"), a.status], [t("team.person"), a.employee_name || "—"], [t("assets.issued_on"), fmtDate(a.issued_on, lang)], ["Acknowledged", a.ack_at ? `${fmtDateTime(a.ack_at, lang)} by ${a.ack_by}` : (a.status === "assigned" ? "pending" : "—")],
                [t("assets.returned_on"), fmtDate(a.returned_on, lang)], [t("assets.condition"), a.return_condition || a.condition || "—"]].map(([k, v]) => <div key={k as string}><span className="text-gray-400">{k}:</span> <b className="text-gray-700">{v as any}</b></div>)}
            </div>
            <CustomFieldsSection entity="asset" id={a.id} mode="view" compact title="Custom fields" />
            {a.photo_doc_ids.length > 0 && <div className="flex flex-wrap gap-2">{a.photo_doc_ids.map((d) => <AuthImg key={d} load={() => assetsApi.photoBlob(d)} className="w-24 h-24 rounded-lg object-cover border border-surface-border" />)}</div>}
            <div><div className="text-xs font-semibold text-gray-600 mb-1">History</div><div className="divide-y divide-surface-border border border-surface-border rounded-xl">{(q.data?.events || []).map((e) => <div key={e.id} className="px-3 py-1.5 text-xs"><span className="text-gray-400">{fmtDateTime(e.at, lang)} · {e.actor}</span> · <b>{e.action}</b> {e.note ? `· ${e.note}` : ""}</div>)}</div></div>
          </div>
          <div className="space-y-3">
            {hr && <button className="btn-secondary text-xs w-full" onClick={() => onEdit(a)}>Edit details</button>}
            {a.status !== "assigned" && a.status !== "lost" && a.status !== "retired" && <div className="card !py-3 space-y-2"><label className={LABEL}>Issue to</label><select className={FIELD} value={assignTo} onChange={(e) => setAssignTo(e.target.value)}><option value="">—</option>{((emps.data || []) as any[]).filter((e) => e.status !== "terminated").map((e) => <option key={e.id} value={e.id}>{e.first_name} {e.last_name}</option>)}</select>
              <label className={LABEL}>{t("assets.issued_on")}</label><input type="date" max={todayIso} className={FIELD} value={issue.issued_on} onChange={(e) => setIssue({ ...issue, issued_on: e.target.value })} /><span className="block text-[11px] text-gray-400">Blank = today</span><FieldError msg={issueErr} />
              <input className={FIELD} placeholder="Note for the history (e.g. with charger, bag)" maxLength={300} value={issue.note} onChange={(e) => setIssue({ ...issue, note: e.target.value })} />
              <button className="btn-primary w-full" disabled={!assignTo || assign.isPending || !!issueErr} onClick={() => assign.mutate()}>Assign</button></div>}
            {a.status === "assigned" && <div className="card !py-3 space-y-2"><label className={LABEL}>Record return</label>
              <select className={FIELD} value={ret.condition} onChange={(e) => setRet({ ...ret, condition: e.target.value })}>{conditions.filter((c) => c !== "lost").map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}</select>
              <input type="date" max={todayIso} min={String(a.issued_on || "").slice(0, 10) || undefined} className={FIELD} value={ret.returned_on} onChange={(e) => setRet({ ...ret, returned_on: e.target.value })} /><span className="block text-[11px] text-gray-400">Blank = today</span><FieldError msg={retErr} />
              <input className={FIELD} placeholder={t("common.note")} maxLength={300} value={ret.note} onChange={(e) => setRet({ ...ret, note: e.target.value })} />
              <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={ret.lost} onChange={(e) => setRet({ ...ret, lost: e.target.checked })} /> Lost / not returned</label>
              {ret.lost && <span className="block text-[11px] text-amber-700">Tick only if it was not physically handed back</span>}
              <button className="btn-primary w-full" disabled={doReturn.isPending || !!retErr} onClick={() => doReturn.mutate()}>{ret.lost ? "Mark lost" : "Return"}</button></div>}
            <div><button className="btn-secondary text-xs w-full" onClick={() => fileRef.current?.click()}>+ Photo (JPG/PNG)</button><input ref={fileRef} type="file" accept="image/jpeg,image/png" capture="environment" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) photo.mutate(f); }} /></div>
          </div>
        </div>
      )}
    </WideModal>
  );
}
