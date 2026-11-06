import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { LifeBuoy, Paperclip } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { engageApi, type HdCategory, type Ticket } from "../../api/engage";
import { errText } from "../../api/platform";
import { useLang, useT } from "../../i18n";
import { fmtDateTime } from "../../i18n/format";
import { useAuthStore } from "../../store/authStore";
import ReadPage from "../../i18n/ReadPage";
import { useUrlTab } from "../../search/useUrlTab";
import { can, viewRoleFor } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const AGENT = ["superadmin", "org_admin", "hr_admin", "hr_manager", "payroll_admin"];
const FIELD = "input w-full";
const LABEL = "block text-xs font-semibold text-gray-500 mb-1";

/** /engage/helpdesk — Ask HR (phone-first form), My tickets, and the HR desk (all / assigned / report / categories). */
export default function HelpdeskPage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const user = useAuthStore((s) => s.user);
  const role = viewRoleFor(user, "engage.view", [...AGENT, "manager"]);
  const agent = can(user, ["engage.edit", "engage.admin"], AGENT);
  const [sp, setSp] = useSearchParams();
  const openId = sp.get("id");
  const [tab, setTab] = useUrlTab<string>(sp.get("new") ? "new" : agent ? "all" : "mine");   // ?tab= (global search deep link)
  const settings = useQuery({ queryKey: ["eng-settings"], queryFn: () => engageApi.settings() });
  const list = useQuery({ queryKey: ["hd-tickets", tab], queryFn: () => engageApi.tickets({ scope: tab === "new" ? "mine" : tab }), enabled: tab !== "report" && tab !== "settings" });
  const cats = settings.data?.helpdesk_categories || [];
  const STATUS: Record<string, string> = { open: t("help.open"), in_progress: t("help.in_progress"), waiting: t("help.waiting"), resolved: t("help.resolved"), closed: t("help.closed") };
  const TONE: Record<string, string> = { open: "bg-blue-100 text-blue-700", in_progress: "bg-amber-100 text-amber-800", waiting: "bg-purple-100 text-purple-800", resolved: "bg-green-100 text-green-700", closed: "bg-gray-100 text-gray-600" };

  const cols: GridColumn<Ticket>[] = [
    { key: "number", label: "#", width: "90px", className: "font-mono text-xs" },
    { key: "subject", label: t("help.subject"), render: (r) => <div><div className="font-medium">{r.subject}</div><div className="text-[11px] text-gray-400">{r.employee_name}{r.department ? ` · ${r.department}` : ""}</div></div> },
    { key: "category", label: t("help.category"), text: (r) => cats.find((c) => c.key === r.category)?.label || r.category, render: (r) => cats.find((c) => c.key === r.category)?.label || r.category },
    { key: "priority", label: t("help.priority"), width: "80px" },
    { key: "status", label: t("common.status"), text: (r) => STATUS[r.status] || r.status, render: (r) => <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${TONE[r.status] || ""}`}>{STATUS[r.status] || r.status}</span> },
    { key: "due_at", label: t("help.due"), render: (r) => <span className={r.sla_state === "breached" ? "text-red-600 font-semibold" : r.sla_state === "at_risk" ? "text-amber-700" : ""}>{fmtDateTime(r.due_at, lang)}{r.sla_state === "breached" ? ` · ${t("help.breached")}` : r.sla_state === "at_risk" ? ` · ${t("help.at_risk")}` : ""}</span>, text: (r) => r.due_at || "" },
    { key: "age_hours", label: "Age (h)", type: "number" },
    { key: "assignee_name", label: t("help.assign") },
    { key: "created_at", label: t("common.date"), render: (r) => fmtDateTime(r.created_at, lang), text: (r) => r.created_at || "" },
  ];
  const tabs: [string, string][] = agent
    ? [["all", t("help.all")], ["assigned", t("help.assigned")], ["mine", t("help.mine")], ["new", t("help.ask")], ["report", t("help.report")], ["settings", t("help.settings")]]
    : [["mine", t("help.mine")], ["new", t("help.ask")], ...(role === "manager" ? [["team", t("help.team")] as [string, string]] : [])];
  const counts = list.data?.counts || {};

  return (
    <div>
      <PageHeader title={t("help.title")} icon={LifeBuoy} subtitle={agent ? "HR desk · tickets, SLA, reports and categories" : "Ask HR · track your tickets"}
        actions={<div className="flex items-center gap-2 shrink-0"><ReadPage />{tab !== "new" && <button className="btn-primary text-sm whitespace-nowrap" onClick={() => setSp({ tab: "new" })}>+ {t("help.ask")}</button>}</div>} />
      <Tabs value={tab} onChange={(k) => setSp(k === (agent ? "all" : "mine") ? {} : { tab: k })} tabs={tabs.map(([k, l]) => ({ key: k, label: l }))} />
      {tab === "new" && <AskHr cats={cats} onDone={(tk) => { toast.success(t("help.sent", { n: tk.number })); setTab("mine"); qc.invalidateQueries({ queryKey: ["hd-tickets"] }); }} />}
      {(tab === "mine" || tab === "all" || tab === "assigned" || tab === "team") && <>
        {agent && <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
          <StatTile label={t("help.open")} value={counts.open || 0} accent /><StatTile label={t("help.in_progress")} value={counts.in_progress || 0} />
          <StatTile label={t("help.waiting")} value={counts.waiting || 0} /><StatTile label={t("help.resolved")} value={counts.resolved || 0} />
          <StatTile label={t("help.breached")} value={counts.breached || 0} />
        </div>}
        <DataGrid columns={cols} rows={list.data?.tickets || []} rowKey={(r) => r.id} minWidth={agent ? 1000 : 640} exportName="hr-tickets" empty={t("help.none")} onRowClick={(r) => setSp((p) => { const n = new URLSearchParams(p); n.set("id", r.id); return n; })} initialSort={{ key: "created_at", dir: "desc" }} />
      </>}
      {tab === "report" && <Report cats={cats} />}
      {tab === "settings" && settings.data && <CategorySettings cats={cats} threshold={settings.data.anonymity_threshold} />}
      {openId && <TicketSheet id={openId} agent={agent} cats={cats} onClose={() => { setSp((p) => { const n = new URLSearchParams(p); n.delete("id"); n.delete("hl"); return n; }); qc.invalidateQueries({ queryKey: ["hd-tickets"] }); }} />}
    </div>
  );
}

function AskHr({ cats, onDone }: { cats: HdCategory[]; onDone: (t: Ticket) => void }) {
  const t = useT();
  const [f, setF] = useState({ category: "", subject: "", body: "", priority: "normal" });
  const [file, setFile] = useState<File | null>(null);
  const cat = f.category || cats[0]?.key || "";
  const m = useMutation({
    mutationFn: async () => {
      const tk = await engageApi.createTicket({ ...f, category: cat, source: window.innerWidth < 640 ? "mobile" : "web" });
      if (file) { try { await engageApi.attach(tk.id, file); } catch (e) { toast.error(errText(e)); } }
      return tk;
    },
    onSuccess: onDone, onError: (e) => toast.error(errText(e)),
  });
  const sla = cats.find((c) => c.key === cat)?.sla_hours;
  return (
    <form className="card grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <div><label className={LABEL}>{t("help.category")}</label>
        <select className={FIELD} value={cat} onChange={(e) => setF({ ...f, category: e.target.value })}>{cats.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select>
        {sla ? <p className="text-[11px] text-gray-400 mt-1">{t("help.due")}: {sla} h</p> : null}</div>
      <div><label className={LABEL}>{t("help.priority")}</label><select className={FIELD} value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}><option value="low">low</option><option value="normal">normal</option><option value="high">high</option><option value="urgent">urgent</option></select></div>
      <div className="md:col-span-2"><label className={LABEL}>{t("help.subject")}</label><input className={FIELD} value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })} required maxLength={200} /></div>
      <div className="md:col-span-2 xl:col-span-4"><label className={LABEL}>{t("help.details")}</label><textarea className={FIELD} rows={5} value={f.body} onChange={(e) => setF({ ...f, body: e.target.value })} /></div>
      <div className="md:col-span-2"><label className={LABEL}>{t("help.attach")}</label><input type="file" accept="image/jpeg,image/png,application/pdf" className="text-sm max-w-full" onChange={(e) => setFile(e.target.files?.[0] || null)} /></div>
      <div className="md:col-span-2 xl:col-span-2 flex md:justify-end items-end"><button className="btn-primary" disabled={m.isPending || !cat}>{t("common.send")}</button></div>
    </form>
  );
}

function TicketSheet({ id, agent, cats, onClose }: { id: string; agent: boolean; cats: HdCategory[]; onClose: () => void }) {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["hd-ticket", id], queryFn: () => engageApi.ticket(id) });
  const agents = useQuery({ queryKey: ["hd-agents"], queryFn: () => engageApi.agents(), enabled: agent });
  const [body, setBody] = useState(""); const [internal, setInternal] = useState(false);
  // resolving / closing asks for a resolution note first (sent as `note`: it goes into the status comment and the employee's notification)
  const [closing, setClosing] = useState<{ status: string; note: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const MAX_FILE = 10 * 1024 * 1024;
  const pickStatus = (s: string) => { if (s === "resolved" || s === "closed") setClosing({ status: s, note: "" }); else patch.mutate({ status: s }); };
  const confirmClose = () => {
    if (!closing) return;
    if (agent && closing.note.trim().length < 5) { toast.error("Write how it was resolved (at least a few words) — the employee sees it"); return; }
    if (closing.note.length > 2000) { toast.error("Resolution note can be at most 2000 characters"); return; }
    patch.mutate({ status: closing.status, note: closing.note.trim() || undefined }, { onSuccess: () => setClosing(null) });
  };
  const attachChecked = (f: File) => { if (f.size > MAX_FILE) { toast.error("Attachments can be up to 10 MB"); return; } attach.mutate(f); };
  const refresh = () => qc.invalidateQueries({ queryKey: ["hd-ticket", id] });
  const comment = useMutation({ mutationFn: () => engageApi.comment(id, body, internal), onSuccess: () => { setBody(""); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const patch = useMutation({ mutationFn: (b: any) => engageApi.updateTicket(id, b), onSuccess: () => { toast.success(t("common.saved")); refresh(); }, onError: (e) => toast.error(errText(e)) });
  const attach = useMutation({ mutationFn: (f: File) => engageApi.attach(id, f), onSuccess: refresh, onError: (e) => toast.error(errText(e)) });
  const tk = q.data?.ticket;
  const STATUS: Record<string, string> = { open: t("help.open"), in_progress: t("help.in_progress"), waiting: t("help.waiting"), resolved: t("help.resolved"), closed: t("help.closed") };
  return (
    <WideModal title={tk ? `${tk.number} · ${tk.subject}` : t("common.loading")} onClose={onClose}>
      {tk && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="md:col-span-2 space-y-3">
            <div className="text-sm text-gray-700 whitespace-pre-wrap">{tk.body || "—"}</div>
            {tk.doc_ids.length > 0 && <div className="flex flex-wrap gap-2">{tk.doc_ids.map((d) => <button key={d} className="text-xs inline-flex items-center gap-1 text-primary-700 border border-primary-200 rounded-lg px-2 py-1" onClick={() => engageApi.fileBlob(d).then((u) => window.open(u, "_blank"))}><Paperclip className="w-3 h-3" /> {t("common.open")}</button>)}</div>}
            <div className="divide-y divide-surface-border border border-surface-border rounded-xl">
              {(q.data?.comments || []).map((c) => (
                <div key={c.id} className={`px-3 py-2 text-sm ${c.internal ? "bg-amber-50" : c.kind === "status" ? "bg-gray-50 text-gray-500 text-xs" : ""}`}>
                  <div className="text-[11px] text-gray-400">{c.author_name} · {fmtDateTime(c.at, lang)}{c.internal ? ` · ${t("help.internal")}` : ""}</div>
                  <div className="whitespace-pre-wrap">{c.body}</div>
                  {c.doc_ids.length > 0 && <button className="text-xs text-primary-700" onClick={() => engageApi.fileBlob(c.doc_ids[0]).then((u) => window.open(u, "_blank"))}>{t("common.open")} ↗</button>}
                </div>
              ))}
            </div>
            {tk.status !== "closed" && (
              <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); if (!body.trim()) { toast.error("Type a message first"); return; } if (body.length > 5000) { toast.error("A message can be at most 5000 characters"); return; } comment.mutate(); }}>
                <textarea className={FIELD} rows={3} maxLength={5000} placeholder={t("help.comment")} value={body} onChange={(e) => setBody(e.target.value)} />
                {agent && internal && <span className="block text-[11px] text-amber-700">Internal note — only agents see it, never the employee</span>}
                <div className="flex items-center gap-2 flex-wrap">
                  <button className="btn-primary" disabled={comment.isPending}>{t("common.send")}</button>
                  {agent && <label className="text-xs flex items-center gap-1"><input type="checkbox" checked={internal} onChange={(e) => setInternal(e.target.checked)} /> {t("help.internal")}</label>}
                  <button type="button" className="btn-secondary text-xs" onClick={() => fileRef.current?.click()}><Paperclip className="w-3 h-3 inline" /> {t("help.attach")}</button>
                  <input ref={fileRef} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf" onChange={(e) => { const f = e.target.files?.[0]; if (f) attachChecked(f); e.target.value = ""; }} />
                  <span className="text-[11px] text-gray-400">JPG, PNG or PDF up to 10 MB</span>
                  {!agent && <button type="button" className="text-xs text-gray-500 ml-auto" onClick={() => setClosing({ status: "closed", note: "" })}>{t("help.closed")}</button>}
                </div>
              </form>
            )}
            {closing && <div className="card !py-3 space-y-2 border-primary-200">
              <label className={LABEL}>{agent ? <>Resolution note<span className="text-red-500"> *</span></> : "Why are you closing it? (optional)"}</label>
              <textarea className={FIELD} rows={3} maxLength={2000} autoFocus value={closing.note} onChange={(e) => setClosing({ ...closing, note: e.target.value })} placeholder={agent ? "What was done — e.g. PF transfer approved on the portal, claim ID …" : "e.g. sorted out, thank you"} />
              <span className="block text-[11px] text-gray-400">{agent ? "Sent to the employee with the status change and kept on the ticket." : "Kept on the ticket."}</span>
              <div className="flex gap-2"><button type="button" className="btn-primary" disabled={patch.isPending} onClick={confirmClose}>{closing.status === "resolved" ? "Mark resolved" : "Close ticket"}</button><button type="button" className="btn-secondary" onClick={() => setClosing(null)}>Cancel</button></div>
            </div>}
          </div>
          <div className="space-y-2 text-sm">
            <div><span className={LABEL}>{t("common.status")}</span>{agent ? <select className={FIELD} value={closing?.status || tk.status} onChange={(e) => pickStatus(e.target.value)}>{(q.data?.states || []).map((s) => <option key={s} value={s}>{STATUS[s] || s}</option>)}</select> : <b>{STATUS[tk.status] || tk.status}</b>}</div>
            {agent && <div><span className={LABEL}>{t("help.assign")}</span><select className={FIELD} value={tk.assignee_user_id || ""} onChange={(e) => patch.mutate({ assignee_user_id: e.target.value || null })}><option value="">—</option>{(agents.data || []).map((a) => <option key={a.user_id} value={a.user_id}>{a.name}</option>)}</select></div>}
            {agent && <div><span className={LABEL}>{t("help.priority")}</span><select className={FIELD} value={tk.priority} onChange={(e) => patch.mutate({ priority: e.target.value })}>{["low", "normal", "high", "urgent"].map((p) => <option key={p}>{p}</option>)}</select><span className="block text-[11px] text-gray-400 mt-0.5">Urgent = a payroll / statutory deadline is at risk</span></div>}
            {agent && <div><span className={LABEL}>{t("help.category")}</span><select className={FIELD} value={tk.category} onChange={(e) => patch.mutate({ category: e.target.value })}>{cats.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select></div>}
            <div className="text-xs text-gray-500 space-y-1 pt-1">
              <div>{t("help.due")}: <b className={tk.sla_state === "breached" ? "text-red-600" : ""}>{fmtDateTime(tk.due_at, lang)}</b> ({tk.sla_hours} h)</div>
              <div>{t("common.date")}: {fmtDateTime(tk.created_at, lang)}</div>
              <div>{t("team.person")}: {tk.employee_name}</div>
              {tk.resolved_at && <div>{t("help.resolved")}: {fmtDateTime(tk.resolved_at, lang)}</div>}
            </div>
          </div>
        </div>
      )}
    </WideModal>
  );
}

function Report({ cats }: { cats: HdCategory[] }) {
  const t = useT();
  const lang = useLang();
  const [days, setDays] = useState(90);
  const q = useQuery({ queryKey: ["hd-report", days], queryFn: () => engageApi.report(days) });
  const r = q.data;
  const byCat = Object.entries(r?.by_category || {}).map(([k, v]: any) => ({ category: cats.find((c) => c.key === k)?.label || k, ...v }));
  const catCols: GridColumn[] = [{ key: "category", label: t("help.category") }, { key: "open", label: t("help.open"), type: "number" }, { key: "resolved", label: t("help.resolved"), type: "number" }, { key: "breached", label: t("help.breached"), type: "number" }, { key: "avg_hours", label: "Avg hours to resolve", type: "number" }];
  const brCols: GridColumn<Ticket>[] = [{ key: "number", label: "#" }, { key: "subject", label: t("help.subject") }, { key: "employee_name", label: t("team.person") }, { key: "status", label: t("common.status") }, { key: "due_at", label: t("help.due"), render: (x) => fmtDateTime(x.due_at, lang), text: (x) => x.due_at || "" }, { key: "age_hours", label: "Age (h)", type: "number" }, { key: "assignee_name", label: t("help.assign") }];
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2"><label className="text-sm">Last</label><select className="input !w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}>{[30, 90, 180, 365].map((d) => <option key={d} value={d}>{d} days</option>)}</select></div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <StatTile label={t("help.open")} value={r?.open ?? "—"} accent /><StatTile label={t("help.resolved")} value={r?.resolved ?? "—"} />
        <StatTile label={`${t("help.breached")} (open)`} value={r?.breached_open ?? "—"} /><StatTile label={`${t("help.breached")} (all)`} value={r?.breached_total ?? "—"} />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">{Object.entries(r?.buckets || {}).map(([k, v]: any) => <div key={k} className="card !py-2 text-center"><div className="text-[11px] text-gray-500">{k}</div><div className="text-lg font-bold">{v}</div></div>)}</div>
      <DataGrid columns={catCols} rows={byCat} rowKey={(x) => x.category} minWidth={600} exportName="helpdesk-by-category" />
      <h3 className="font-display font-semibold text-gray-800">{t("help.breached")}</h3>
      <DataGrid columns={brCols} rows={r?.breaches || []} rowKey={(x) => x.id} minWidth={800} exportName="sla-breaches" />
    </div>
  );
}

function CategorySettings({ cats, threshold }: { cats: HdCategory[]; threshold: number }) {
  const t = useT();
  const qc = useQueryClient();
  const [rows, setRows] = useState<HdCategory[]>(cats);
  const [thr, setThr] = useState(threshold);
  useEffect(() => setRows(cats), [cats]);
  const save = useMutation({ mutationFn: () => engageApi.saveSettings({ helpdesk_categories: rows, anonymity_threshold: thr }), onSuccess: () => { toast.success(t("common.saved")); qc.invalidateQueries({ queryKey: ["eng-settings"] }); }, onError: (e) => toast.error(errText(e)) });
  const set = (i: number, k: keyof HdCategory, v: any) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  return (
    <div className="card space-y-3 overflow-x-auto">
      <table className="w-full text-sm min-w-[640px]"><thead><tr className="text-left text-xs text-gray-500"><th>Key</th><th>Label</th><th>SLA hours</th><th>Goes to</th><th></th></tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>
          <td><input className={`${FIELD} !py-1`} value={r.key} onChange={(e) => set(i, "key", e.target.value)} /></td>
          <td><input className={`${FIELD} !py-1`} value={r.label} onChange={(e) => set(i, "label", e.target.value)} /></td>
          <td><input type="number" className={`${FIELD} !py-1 w-24`} value={r.sla_hours} onChange={(e) => set(i, "sla_hours", Number(e.target.value))} /></td>
          <td><select className={`${FIELD} !py-1`} value={r.assign_role} onChange={(e) => set(i, "assign_role", e.target.value)}><option value="hr">HR</option><option value="payroll">Payroll</option><option value="org_admin">Admin</option></select></td>
          <td><button className="text-xs text-red-600" onClick={() => setRows(rows.filter((_, j) => j !== i))}>✕</button></td></tr>)}</tbody></table>
      <button className="btn-secondary text-xs" onClick={() => setRows([...rows, { key: "", label: "", sla_hours: 48, assign_role: "hr" }])}>+ {t("common.add")}</button>
      <div><label className={LABEL}>Survey anonymity threshold (minimum responses before results show)</label><input type="number" min={1} max={100} className={`${FIELD} w-32`} value={thr} onChange={(e) => setThr(Number(e.target.value))} /></div>
      <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>{t("common.save")}</button>
    </div>
  );
}
