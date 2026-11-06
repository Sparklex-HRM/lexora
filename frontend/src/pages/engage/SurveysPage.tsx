import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { ClipboardList } from "lucide-react";
import DataGrid, { Tabs, WideModal, type GridColumn } from "../../components/DataGrid";
import { PageHeader, StatTile } from "../../components/ui";
import { employeeApi } from "../../api/endpoints";
import { engageApi, type Survey, type SurveyQ } from "../../api/engage";
import { errText } from "../../api/platform";
import { useLang, useT } from "../../i18n";
import { fmtDate } from "../../i18n/format";
import ReadPage from "../../i18n/ReadPage";
import { useUrlTab } from "../../search/useUrlTab";

const FIELD = "input w-full";
const LABEL = "block text-xs font-semibold text-gray-500 mb-1";

/** /engage/surveys — employees answer (phone-first); HR creates from templates, opens/closes, sees results + eNPS trend. */
export default function SurveysPage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const openId = sp.get("id");
  const [mode, setMode] = useUrlTab<"list" | "new" | "trend">("list", "mode");   // ?mode= (global search deep link)
  const list = useQuery({ queryKey: ["eng-surveys"], queryFn: () => engageApi.surveys() });
  const manage = !!list.data?.can_manage;
  const surveys = list.data?.surveys || [];
  const patch = useMutation({ mutationFn: ({ id, body }: { id: string; body: any }) => engageApi.updateSurvey(id, body), onSuccess: () => { toast.success(t("common.saved")); qc.invalidateQueries({ queryKey: ["eng-surveys"] }); }, onError: (e) => toast.error(errText(e)) });

  const cols: GridColumn<Survey>[] = [
    { key: "title", label: t("survey.title") }, { key: "kind", label: "Kind", width: "70px" },
    { key: "anonymous", label: "Anonymous", text: (r) => (r.anonymous ? "yes" : "no"), render: (r) => (r.anonymous ? t("common.yes") : t("common.no")), width: "90px" },
    { key: "status", label: t("common.status"), width: "80px" },
    { key: "opens_on", label: t("leave.from"), render: (r) => fmtDate(r.opens_on, lang), text: (r) => r.opens_on || "" }, { key: "closes_on", label: t("leave.to"), render: (r) => fmtDate(r.closes_on, lang), text: (r) => r.closes_on || "" },
    { key: "responses", label: t("survey.responses", { n: "" }).trim(), type: "number" }, { key: "threshold", label: "Min. responses", type: "number" },
    { key: "act", label: "", render: (r) => <div className="flex gap-1">
      {r.status === "draft" && <button className="btn-primary !py-0.5 !px-2 text-xs" onClick={(e) => { e.stopPropagation(); patch.mutate({ id: r.id, body: { status: "open" } }); }}>{t("common.open")}</button>}
      {r.status === "open" && <button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={(e) => { e.stopPropagation(); patch.mutate({ id: r.id, body: { status: "closed" } }); }}>{t("help.closed")}</button>}
      <button className="text-xs text-primary-700" onClick={(e) => { e.stopPropagation(); setSp({ id: r.id, results: "1" }); }}>{t("survey.results")}</button></div> },
  ];

  // Employee view: the surveys open to me, one row each; a click opens the answer form.
  const myCols: GridColumn<Survey>[] = [
    { key: "title", label: t("survey.title"), className: "text-gray-800 font-medium" },
    { key: "anonymous", label: "Anonymous", width: "130px", text: (r) => (r.anonymous ? "yes" : "no"), render: (r) => (r.anonymous ? t("survey.anonymous") : t("survey.named")) },
    { key: "opens_on", label: t("leave.from"), width: "120px", render: (r) => fmtDate(r.opens_on, lang), text: (r) => r.opens_on || "" },
    { key: "closes_on", label: t("leave.to"), width: "120px", render: (r) => fmtDate(r.closes_on, lang), text: (r) => r.closes_on || "" },
    { key: "answered", label: t("common.status"), width: "120px", text: (r) => (r.answered ? "answered" : "to answer"),
      render: (r) => <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${r.answered ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-800"}`}>{r.answered ? t("survey.answered") : t("survey.answer")}</span> },
    { key: "act", label: "", sortable: false, width: "110px", render: (r) => r.answered ? null : <button className="btn-primary !py-1 !px-2.5 text-xs whitespace-nowrap" onClick={(e) => { e.stopPropagation(); setSp({ id: r.id }); }}>{t("survey.answer")}</button> },
  ];

  return (
    <div>
      <PageHeader title={t("survey.title")} icon={ClipboardList} subtitle={manage ? "Pulse, eNPS and exit surveys · open / close · results behind the anonymity threshold" : "Surveys open to you"}
        actions={<div className="flex items-center gap-2 shrink-0"><ReadPage />{manage && <Link to="/engage/surveys/admin" className="btn-secondary text-sm whitespace-nowrap">Surveys admin</Link>}</div>} />
      {manage && <Tabs value={mode} onChange={setMode} tabs={[{ key: "list", label: t("common.all") }, { key: "new", label: "+ " + t("common.add") }, { key: "trend", label: t("survey.trend") }]} />}
      {manage && mode === "list" && <DataGrid columns={cols} rows={surveys} rowKey={(r) => r.id} minWidth={900} exportName="surveys" onRowClick={(r) => setSp({ id: r.id, results: "1" })} initialSort={{ key: "created_at", dir: "desc" }} />}
      {manage && mode === "new" && <NewSurvey onDone={() => { setMode("list"); qc.invalidateQueries({ queryKey: ["eng-surveys"] }); }} />}
      {manage && mode === "trend" && <Trend />}
      {!manage && <>
        <div className="grid grid-cols-3 gap-3 mb-4">
          <StatTile label={t("survey.title")} value={list.data ? surveys.length : "—"} accent />
          <StatTile label="To answer" value={list.data ? surveys.filter((x) => !x.answered).length : "—"} />
          <StatTile label={t("survey.answered")} value={list.data ? surveys.filter((x) => x.answered).length : "—"} />
        </div>
        <DataGrid columns={myCols} rows={surveys} rowKey={(r) => r.id} minWidth={760} exportName="my-surveys" empty={list.isLoading ? t("common.loading") : t("survey.none")} onRowClick={(r) => setSp({ id: r.id })} />
      </>}
      {openId && (sp.get("results") ? <Results id={openId} onClose={() => setSp({})} /> : <Answer id={openId} onClose={() => { setSp({}); qc.invalidateQueries({ queryKey: ["eng-surveys"] }); }} />)}
    </div>
  );
}

function Answer({ id, onClose }: { id: string; onClose: () => void }) {
  const t = useT();
  const q = useQuery({ queryKey: ["eng-survey", id], queryFn: () => engageApi.survey(id) });
  const [a, setA] = useState<Record<string, any>>({});
  const m = useMutation({ mutationFn: () => engageApi.respond(id, a), onSuccess: () => { toast.success(t("survey.thanks")); onClose(); }, onError: (e) => toast.error(errText(e)) });
  const s = q.data;
  const pick = (k: string, v: any) => setA({ ...a, [k]: v });
  const Scale = ({ q: qq, n, from }: { q: SurveyQ; n: number; from: number }) => (
    <div className="flex flex-wrap gap-1.5">{Array.from({ length: n - from + 1 }, (_, i) => from + i).map((v) => <button type="button" key={v} onClick={() => pick(qq.key, v)} className={`w-10 h-10 rounded-xl text-sm font-semibold border ${a[qq.key] === v ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border text-gray-700"}`}>{v}</button>)}</div>
  );
  return (
    <WideModal title={s?.title || t("common.loading")} onClose={onClose}
      footer={s && !s.answered ? <><button type="button" className="btn-secondary" onClick={onClose}>{t("common.cancel")}</button><button type="submit" form="survey-answer" className="btn-primary" disabled={m.isPending}>{t("common.submit")}</button></> : undefined}>
      {s && (s.answered ? <div className="text-sm text-gray-600">{t("survey.answered")}</div> : (
        <form id="survey-answer" className="space-y-5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <p className="text-xs text-gray-500">{s.anonymous ? t("survey.anonymous") : t("survey.named")}</p>
          {s.questions.map((qq) => (
            <div key={qq.key}><label className="block text-sm font-medium text-gray-800 mb-1.5">{qq.text}</label>
              {qq.type === "scale" && <Scale q={qq} n={5} from={1} />}
              {qq.type === "nps" && <Scale q={qq} n={10} from={0} />}
              {qq.type === "choice" && <div className="flex flex-wrap gap-1.5">{(qq.options || []).map((o) => <button type="button" key={o} onClick={() => pick(qq.key, o)} className={`px-3 py-2 rounded-xl text-sm border ${a[qq.key] === o ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border"}`}>{o}</button>)}</div>}
              {qq.type === "text" && <textarea className={FIELD} rows={2} value={a[qq.key] || ""} onChange={(e) => pick(qq.key, e.target.value)} />}
            </div>
          ))}
        </form>
      ))}
    </WideModal>
  );
}

function Results({ id, onClose }: { id: string; onClose: () => void }) {
  const t = useT();
  const q = useQuery({ queryKey: ["eng-results", id], queryFn: () => engageApi.results(id) });
  const r = q.data?.results;
  const Bar = ({ dist }: { dist: Record<string, number> }) => { const max = Math.max(1, ...Object.values(dist)); return (
    <div className="space-y-0.5">{Object.entries(dist).map(([k, v]) => <div key={k} className="flex items-center gap-2 text-xs"><span className="w-24 truncate text-gray-600">{k}</span><div className="flex-1 h-3 bg-gray-100 rounded"><div className="h-3 bg-primary-500 rounded" style={{ width: `${(v / max) * 100}%` }} /></div><span className="w-6 text-right tabular-nums">{v}</span></div>)}</div>); };
  return (
    <WideModal title={`${t("survey.results")} · ${q.data?.survey.title || ""}`} onClose={onClose}>
      {r && (!r.visible ? <div className="card text-sm text-amber-800 bg-amber-50 border-amber-200">{r.message}</div> : (
        <div className="space-y-4">
          <div className="text-sm text-gray-600">{t("survey.responses", { n: r.responses })}</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {r.questions.map((qq: any) => (
              <div key={qq.key} className="card !py-3">
                <div className="text-sm font-medium text-gray-800 mb-1">{qq.text}</div>
                {qq.type === "nps" && <div className="text-2xl font-bold text-primary-700 mb-1">eNPS {qq.enps ?? "—"} <span className="text-xs text-gray-500 font-normal">({qq.promoters} promoters · {qq.passives} passives · {qq.detractors} detractors)</span></div>}
                {qq.type === "scale" && <div className="text-2xl font-bold text-primary-700 mb-1">{qq.average ?? "—"} <span className="text-xs text-gray-500 font-normal">/ 5</span></div>}
                {qq.distribution && <Bar dist={qq.distribution} />}
                {qq.texts && <ul className="text-xs text-gray-700 list-disc pl-4 max-h-40 overflow-y-auto">{qq.texts.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>}
              </div>
            ))}
          </div>
          {q.data?.named && <DataGrid columns={[{ key: "name", label: t("common.name") }, { key: "at", label: t("common.date"), type: "date" }, { key: "answers", label: "Answers", text: (x: any) => JSON.stringify(x.answers), render: (x: any) => <span className="text-xs">{Object.entries(x.answers).map(([k, v]) => `${k}: ${v}`).join(" · ")}</span> }]} rows={q.data.named} rowKey={(x: any, i: number) => String(i)} minWidth={600} exportName="survey-answers" />}
        </div>
      ))}
    </WideModal>
  );
}

function NewSurvey({ onDone }: { onDone: () => void }) {
  const t = useT();
  const tpls = useQuery({ queryKey: ["eng-tpl"], queryFn: () => engageApi.surveyTemplates() });
  const emps = useQuery({ queryKey: ["employees"], queryFn: () => employeeApi.list() });
  const [f, setF] = useState<any>({ template: "enps", title: "", anonymous: true, opens_on: "", closes_on: "", departments: [] as string[], threshold: "", status: "open", questions: null as SurveyQ[] | null });
  const tpl = (tpls.data || []).find((x) => x.key === f.template);
  const qs: SurveyQ[] = f.questions || tpl?.questions || [];
  const deps = Array.from(new Set(((emps.data || []) as any[]).map((e) => e.department).filter(Boolean))) as string[];
  const m = useMutation({
    mutationFn: () => engageApi.createSurvey({ template: f.template, title: f.title || tpl?.title, kind: tpl?.kind, anonymous: f.anonymous, opens_on: f.opens_on || null, closes_on: f.closes_on || null, target: f.departments.length ? { departments: f.departments } : {}, threshold: f.threshold ? Number(f.threshold) : null, status: f.status, questions: qs }),
    onSuccess: onDone, onError: (e) => toast.error(errText(e)),
  });
  const setQ = (i: number, patch: Partial<SurveyQ>) => setF({ ...f, questions: qs.map((q, j) => (j === i ? { ...q, ...patch } : q)) });
  return (
    <form className="card grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <div><label className={LABEL}>Template</label><select className={FIELD} value={f.template} onChange={(e) => setF({ ...f, template: e.target.value, questions: null })}>{(tpls.data || []).map((x) => <option key={x.key} value={x.key}>{x.title}</option>)}</select></div>
      <div><label className={LABEL}>{t("survey.title")}</label><input className={FIELD} value={f.title} placeholder={tpl?.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></div>
      <div><label className={LABEL}>{t("leave.from")}</label><input type="date" className={FIELD} value={f.opens_on} onChange={(e) => setF({ ...f, opens_on: e.target.value })} /></div>
      <div><label className={LABEL}>{t("leave.to")}</label><input type="date" className={FIELD} value={f.closes_on} onChange={(e) => setF({ ...f, closes_on: e.target.value })} /></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.anonymous} onChange={(e) => setF({ ...f, anonymous: e.target.checked })} /> Anonymous</label>
      <div><label className={LABEL}>Min. responses before results show (blank = org default)</label><input type="number" className={FIELD} min={1} value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} /></div>
      <div className="md:col-span-2 xl:col-span-4"><label className={LABEL}>Target departments (none = everyone)</label><div className="flex flex-wrap gap-1.5">{deps.map((d) => <button type="button" key={d} onClick={() => setF({ ...f, departments: f.departments.includes(d) ? f.departments.filter((x: string) => x !== d) : [...f.departments, d] })} className={`px-2 py-1 rounded-lg text-xs border ${f.departments.includes(d) ? "bg-primary-600 text-white border-primary-600" : "bg-white border-surface-border"}`}>{d}</button>)}</div></div>
      <div className="md:col-span-2 xl:col-span-4 space-y-2"><label className={LABEL}>Questions</label>
        {qs.map((q, i) => <div key={i} className="grid grid-cols-12 gap-2 items-center"><select className={`${FIELD} !py-1 col-span-3`} value={q.type} onChange={(e) => setQ(i, { type: e.target.value as any })}><option value="scale">1-5</option><option value="nps">0-10 (NPS)</option><option value="choice">choice</option><option value="text">text</option></select><input className={`${FIELD} !py-1 col-span-8`} value={q.text} onChange={(e) => setQ(i, { text: e.target.value })} /><button type="button" className="text-xs text-red-600" onClick={() => setF({ ...f, questions: qs.filter((_, j) => j !== i) })}>✕</button>
          {q.type === "choice" && <input className={`${FIELD} !py-1 col-span-12`} placeholder="Options, comma separated" value={(q.options || []).join(", ")} onChange={(e) => setQ(i, { options: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} />}</div>)}
        <button type="button" className="btn-secondary text-xs" onClick={() => setF({ ...f, questions: [...qs, { key: `q${qs.length + 1}`, type: "scale", text: "" }] })}>+ question</button></div>
      <div className="md:col-span-2 xl:col-span-4 flex gap-2"><select className="input !w-auto" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="open">Open now</option><option value="draft">Save as draft</option></select><button className="btn-primary" disabled={m.isPending}>{t("common.save")}</button></div>
    </form>
  );
}

function Trend() {
  const t = useT();
  const [kind, setKind] = useState("enps");
  const q = useQuery({ queryKey: ["eng-trend", kind], queryFn: () => engageApi.trend(kind) });
  const cols: GridColumn[] = [{ key: "date", label: t("common.date"), type: "date" }, { key: "title", label: t("survey.title") }, { key: "responses", label: t("survey.responses", { n: "" }).trim(), type: "number" }, { key: "score", label: kind === "enps" ? "eNPS" : "Average", type: "number", render: (r) => r.visible ? (r.score ?? "—") : <span className="text-gray-400 text-xs">hidden (below threshold)</span> }];
  return (
    <div className="space-y-2">
      <select className="input !w-auto" value={kind} onChange={(e) => setKind(e.target.value)}><option value="enps">eNPS</option><option value="pulse">Pulse</option><option value="exit">Exit</option></select>
      <DataGrid columns={cols} rows={q.data?.points || []} rowKey={(r) => r.survey_id} minWidth={600} exportName={`survey-trend-${kind}`} />
    </div>
  );
}
