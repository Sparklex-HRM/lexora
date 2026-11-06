import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Columns3, Download, Plus, Rows3, Upload } from "lucide-react";
import toast from "react-hot-toast";
import { err, fmtDate, fmtDateTime, hiringApi, Row, SOURCES, stageLabel, STAGES } from "./api";
import { Col, downloadBlob, empLabel, Field, FirstStep, Grid, Input, Modal, Note, Pill, ReasonDialog, Select, Textarea, useEmployees } from "./ui";
import { OfferForm } from "./Offers";
import { CandidateComms, InterviewTools, ScorecardSummary } from "./D2Widgets";
import { CustomFieldsSection, saveCustomValues, useCustomFieldDefs } from "../../components/CustomFieldsSection";   // D1
import { FitPanel, QUAL_LEVELS, RankingModal } from "./AiPanels";   // G6: advisory fit + résumé parsing
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req, normMobile } from "../../lib/validators";
import { aiApi } from "./api_ai";
import { fromDateTimeLocal } from "../../lib/datetime";   // 2026-10-25: UTC store, org-zone today

const MANUAL = ["applied", "screening", "interview", "selected"];
const BOARD = ["applied", "screening", "interview", "selected", "offer", "joined", "on_hold", "rejected", "withdrawn"];
const MODES: Record<string, string> = { in_person: "In person", video: "Video call", phone: "Phone" };
const REC: Record<string, string> = { hire: "Hire", no_hire: "No hire", hold: "Hold" };
/** hire_documents.kind (the backend keeps the same list: hiring_logic.DOC_KINDS). */
const DOC_KINDS: Record<string, string> = { resume: "Résumé", id_proof: "ID proof", education: "Education certificate",
  experience: "Experience / relieving letter", payslip: "Pay stub", document: "Other document" };

export default function Candidates({ isHr }: { isHr: boolean }) {
  const qc = useQueryClient();
  const apps = useQuery({ queryKey: ["hire", "apps"], queryFn: () => hiringApi.applications() });
  const reqs = useQuery({ queryKey: ["hire", "reqs"], queryFn: hiringApi.requisitions });
  const [view, setView] = useState<"grid" | "board">("grid");
  const [reqId, setReqId] = useState("");
  const [stage, setStage] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [add, setAdd] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [bulk, setBulk] = useState<string | null>(null);     // target stage waiting for a reason
  const [rank, setRank] = useState(false);                   // G6: fit overview for the picked requisition
  const rows = useMemo(() => (apps.data || []).filter((a) => (!reqId || a.requisition_id === reqId) && (!stage || a.stage === stage)), [apps.data, reqId, stage]);
  const openReqs = (reqs.data || []).filter((r) => r.status === "open");

  const move = useMutation({
    mutationFn: (v: { ids: string[]; to: string; reason?: string }) => hiringApi.move(v.ids, v.to, v.reason),
    onSuccess: (r, v) => {
      qc.invalidateQueries({ queryKey: ["hire"] }); setBulk(null); setPicked(new Set());
      if (r.moved.length) toast.success(`${r.moved.length} moved to ${stageLabel(v.to)}.`);
      if (r.failed.length) toast.error(`${r.failed.length} not moved. ${r.failed[0].reason}`, { duration: 7000 });
    },
    onError: (e) => toast.error(err(e)),
  });
  const doMove = (ids: string[], to: string) => (to === "rejected" ? setBulk(to) : move.mutate({ ids, to }));
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n; });

  const cols: Col<Row>[] = [
    { k: "_sel", label: "", csv: () => "", render: (a) => <input type="checkbox" checked={picked.has(a.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggle(a.id)} aria-label={`Select ${a.candidate_name}`} /> },
    { k: "candidate_name", label: "Candidate", render: (a) => <span className="font-medium text-gray-800 inline-flex items-center gap-1">{a.candidate_name}{a.possible_duplicate && <span title="Possible duplicate: same e-mail or phone as another record"><AlertTriangle className="w-3.5 h-3.5 text-amber-500" /></span>}</span> },
    { k: "designation", label: "Applied for" },
    { k: "req_number", label: "Requisition" },
    { k: "stage", label: "Stage", render: (a) => <Pill s={a.stage} label={a.stage_label} />, csv: (a) => a.stage_label },
    { k: "days_in_stage", label: "Days in stage", align: "right" },
    { k: "source_type", label: "Source", render: (a) => SOURCES[a.source_type] + (a.referrer_name ? `: ${a.referrer_name}` : a.source_detail ? `: ${a.source_detail}` : ""), csv: (a) => SOURCES[a.source_type] + (a.referrer_name ? `: ${a.referrer_name}` : a.source_detail ? `: ${a.source_detail}` : "") },
    { k: "rounds_done", label: "Rounds done", align: "right", render: (a) => `${a.rounds_done} of ${a.rounds_required}`, csv: (a) => `${a.rounds_done}/${a.rounds_required}` },
    { k: "avg_rating", label: "Avg rating", align: "right", render: (a) => a.avg_rating ?? "" },
    { k: "email", label: "E-mail" },
    { k: "phone", label: "Phone" },
    { k: "current_employer", label: "Current employer" },
    { k: "tags", label: "Tags", render: (a) => (a.tags || []).map((t: string) => <span key={t} className="badge-purple mr-1">{t}</span>), csv: (a) => (a.tags || []).join("; ") },
    { k: "possible_duplicate", label: "Duplicate?", hide: true, csv: (a) => (a.possible_duplicate ? "Possible duplicate" : "") },
    { k: "created_at", label: "Applied on", render: (a) => fmtDate(a.created_at) },
    { k: "stage_reason", label: "Reason", render: (a) => <span className="max-w-[16rem] truncate inline-block align-bottom" title={a.stage_reason || ""}>{a.stage_reason || ""}</span> },
  ];

  if (!apps.isLoading && !(apps.data || []).length) {
    return (<>
      <FirstStep title="No candidates yet">
        {openReqs.length ? "Add a candidate to an Open requisition, or publish a job posting so people can apply on the careers page."
          : "There is no Open requisition yet. Raise a requisition and get it approved first; candidates are always added against a requisition."}
        {isHr && !!openReqs.length && <div className="mt-4"><button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setAdd(true)}><Plus className="w-4 h-4" />Add a candidate</button></div>}
      </FirstStep>
      {add && <AddCandidate reqs={openReqs} onClose={() => setAdd(false)} />}
    </>);
  }

  const toolbar = (<>
    <select className="input !py-1.5 text-xs !w-52" value={reqId} onChange={(e) => setReqId(e.target.value)}>
      <option value="">All requisitions</option>{(reqs.data || []).map((r) => <option key={r.id} value={r.id}>{r.req_number} · {r.designation}</option>)}</select>
    {view === "grid" && <select className="input !py-1.5 text-xs !w-36" value={stage} onChange={(e) => setStage(e.target.value)}>
      <option value="">All stages</option>{STAGES.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select>}
    <div className="inline-flex rounded-lg border border-surface-border overflow-hidden">
      <button className={`px-2 py-1 text-xs inline-flex items-center gap-1 ${view === "grid" ? "bg-primary-50 text-primary-600" : "text-gray-500"}`} onClick={() => setView("grid")}><Rows3 className="w-3.5 h-3.5" />Grid</button>
      <button className={`px-2 py-1 text-xs inline-flex items-center gap-1 ${view === "board" ? "bg-primary-50 text-primary-600" : "text-gray-500"}`} onClick={() => setView("board")}><Columns3 className="w-3.5 h-3.5" />Board</button>
    </div>
    <button className="btn-secondary !py-1 !px-2 text-xs disabled:opacity-50" disabled={!reqId} title={reqId ? "Applications ordered by an advisory fit score" : "Pick a requisition first"} onClick={() => setRank(true)}>Fit overview</button>
  </>);
  const bulkBar = picked.size > 0 && (
    <div className="flex flex-wrap items-center gap-2 mb-2 text-xs bg-primary-50 border border-primary-100 rounded-lg px-3 py-2">
      <b>{picked.size} selected.</b> Move to:
      {MANUAL.map((s) => <button key={s} className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => doMove([...picked], s)}>{stageLabel(s)}</button>)}
      <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => doMove([...picked], "on_hold")}>On hold</button>
      <button className="btn-secondary !py-0.5 !px-2 text-[11px] !text-red-600" onClick={() => doMove([...picked], "rejected")}>Reject</button>
      <button className="underline ml-auto" onClick={() => setPicked(new Set())}>Clear</button>
    </div>);
  const addBtn = isHr && <button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 disabled:opacity-50" disabled={!openReqs.length} title={openReqs.length ? "" : "No Open requisition"} onClick={() => setAdd(true)}><Plus className="w-3.5 h-3.5" />Add a candidate</button>;

  return (
    <div>
      {bulkBar}
      {view === "grid" ? (
        <Grid rows={rows} cols={cols} keyOf={(a) => a.id} loading={apps.isLoading} exportName="candidates.csv" title="Candidates"
          empty="No candidate matches these filters." onRow={(a) => setOpenId(a.id)} filters={toolbar} actions={addBtn} />
      ) : (
        <div>
          <div className="card !p-2 mb-2 flex flex-wrap items-center gap-2">{toolbar}<span className="ml-auto" />{addBtn}</div>
          <div className="flex gap-2 overflow-x-auto pb-2">
            {BOARD.map((s) => {
              const col = rows.filter((a) => a.stage === s);
              return (
                <div key={s} className="w-56 shrink-0 bg-surface rounded-xl border border-surface-border"
                  onDragOver={(e) => { if (MANUAL.includes(s) || s === "on_hold" || s === "rejected") e.preventDefault(); }}
                  onDrop={(e) => { const id = e.dataTransfer.getData("text/plain"); const a = rows.find((x) => x.id === id); if (a && a.stage !== s) { if (s === "rejected") { setPicked(new Set([id])); setBulk(s); } else move.mutate({ ids: [id], to: s }); } }}>
                  <div className="px-2.5 py-2 text-[11px] font-semibold uppercase tracking-wide text-gray-500 flex justify-between"><span>{stageLabel(s)}</span><span className="tabular-nums">{col.length}</span></div>
                  <div className="px-1.5 pb-2 space-y-1.5 min-h-[4rem] max-h-[62vh] overflow-y-auto">
                    {col.map((a) => (
                      <div key={a.id} draggable={isHr || true} onDragStart={(e) => e.dataTransfer.setData("text/plain", a.id)} onClick={() => setOpenId(a.id)}
                        className="bg-white border border-surface-border rounded-lg px-2 py-1.5 text-xs cursor-pointer hover:border-primary-300">
                        <div className="font-medium text-gray-800 flex items-center gap-1"><input type="checkbox" checked={picked.has(a.id)} onClick={(e) => e.stopPropagation()} onChange={() => toggle(a.id)} aria-label={`Select ${a.candidate_name}`} />
                          <span className="truncate">{a.candidate_name}</span>{a.possible_duplicate && <AlertTriangle className="w-3 h-3 text-amber-500 shrink-0" />}</div>
                        <div className="text-gray-500 truncate">{a.designation}</div>
                        <div className="text-[10px] text-gray-400 flex justify-between"><span>{a.days_in_stage} d in stage</span><span>{a.rounds_done} of {a.rounds_required} rounds</span></div>
                      </div>))}
                  </div>
                </div>);
            })}
          </div>
          <p className="text-[11px] text-gray-400">Drag a card to Applied, Screening, Interview, Selected, On hold or Rejected. Offer and Joined are set by the offer and by onboarding.</p>
        </div>
      )}
      {add && <AddCandidate reqs={openReqs} onClose={() => setAdd(false)} />}
      {openId && <ApplicationModal id={openId} isHr={isHr} onClose={() => setOpenId(null)} />}
      {rank && reqId && <RankingModal reqId={reqId} reqLabel={(reqs.data || []).find((r) => r.id === reqId)?.req_number || ""} onOpen={(id) => setOpenId(id)} onClose={() => setRank(false)} />}
      {bulk && <ReasonDialog title={`Reject ${picked.size} candidate${picked.size > 1 ? "s" : ""}`} hint="The reason is saved on each application." confirm="Reject"
        onClose={() => setBulk(null)} onConfirm={(reason) => move.mutate({ ids: [...picked], to: bulk, reason })} />}
    </div>
  );
}

/* ── add a candidate (+ application), with the duplicate warning ───────────── */
const UPLOAD_TYPES = /\.(pdf|docx|jpe?g|png)$/i;
const PROFILE_KEYS = ["experience_years", "notice_period_days", "current_ctc", "expected_ctc"];

function AddCandidate({ reqs, onClose }: { reqs: Row[]; onClose: () => void }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const settings = useQuery({ queryKey: ["hire", "settings"], queryFn: hiringApi.settings });
  const maxMb = Number(settings.data?.resume_max_mb) || 0;
  const [f, setF] = useState<Row>({ requisition_id: reqs[0]?.id || "", source_type: "walk_in" });
  const [file, setFile] = useState<File | null>(null);
  const [dups, setDups] = useState<Row[]>([]);
  const [cfValues, setCfValues] = useState<Record<string, any>>({});
  const [shown, setShown] = useState(false);
  const cfDefs = useCustomFieldDefs("candidate");
  const set = (k: string, val: any) => setF((p) => ({ ...p, [k]: val }));
  const [reading, setReading] = useState(false);
  const [readNote, setReadNote] = useState<string[]>([]);
  const readResume = async () => {           // G6: pre-fill EMPTY fields only; nothing is saved until Add candidate
    if (!file) return;
    setReading(true);
    try {
      const r = await aiApi.parseFile(file);
      const x = r.fields || {};
      const blank = (val: unknown) => val === undefined || val === null || val === "";
      const top = (x.education || [])[0];
      setF((p) => ({ ...p, ...Object.fromEntries(["first_name", "last_name", "email", "phone", "current_employer", "current_title", "city", ...PROFILE_KEYS]
        .filter((k) => blank(p[k]) && !blank(x[k])).map((k) => [k, x[k]])), tags: p.tags || (x.skills || []).slice(0, 8).join(", "),
        ...(top && !p.qual_degree ? { qual_degree: top.degree, qual_level: String(top.level || "") } : {}) }));
      setReadNote([r.advice, ...(x.warnings || [])].filter(Boolean));
    } catch (e) { toast.error(err(e)); } finally { setReading(false); }
  };
  const check = async () => {
    if (!f.email && !f.phone) return setDups([]);
    try { setDups((await hiringApi.checkDuplicate({ email: f.email, phone: f.phone })).possible_duplicates); } catch { /* the save checks again */ }
  };
  const whole = (val: unknown, label: string) => (val !== "" && val != null && !Number.isInteger(Number(val)) ? `${label} must be whole days` : null);
  const errs = collect({
    first_name: v.required(f.first_name, "First name") || v.maxLen(f.first_name, 100, "First name"),
    last_name: v.maxLen(f.last_name, 100, "Last name"),
    email: (!f.email && !f.phone ? "Give an e-mail or a phone number so the person can be reached" : null) || v.email(f.email),
    phone: v.mobile(f.phone),
    current_employer: v.maxLen(f.current_employer, 200, "Current employer"),
    current_title: v.maxLen(f.current_title, 200, "Current job title"),
    city: v.maxLen(f.city, 100, "City"),
    requisition_id: v.required(f.requisition_id, "Requisition"),
    referrer_employee_id: f.source_type === "referral" ? v.required(f.referrer_employee_id, "Referred by") : null,
    source_detail: ["agency", "job_board"].includes(f.source_type) ? (v.required(f.source_detail, f.source_type === "agency" ? "Agency name" : "Job board name") || v.maxLen(f.source_detail, 200, "Name")) : null,
    experience_years: v.range(f.experience_years, 0, 60, "Total experience"),
    notice_period_days: v.range(f.notice_period_days, 0, 365, "Notice period") || whole(f.notice_period_days, "Notice period"),
    current_ctc: v.range(f.current_ctc, 0, 1e9, "Current salary"),
    expected_ctc: v.range(f.expected_ctc, 0, 1e9, "Expected salary"),
    qual_degree: f.qual_level && !String(f.qual_degree || "").trim() ? "Type the qualification for this level" : v.maxLen(f.qual_degree, 80, "Qualification"),
    qual_level: String(f.qual_degree || "").trim() && !f.qual_level ? "Pick the qualification level" : null,
    file: file && !UPLOAD_TYPES.test(file.name) ? "The résumé must be a PDF, DOCX, JPG or PNG file"
      : file && maxMb && file.size > maxMb * 1024 * 1024 ? `The résumé is larger than ${maxMb} MB` : null,
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const lowExpected = f.current_ctc !== undefined && f.current_ctc !== "" && f.expected_ctc !== undefined && f.expected_ctc !== "" && Number(f.expected_ctc) < Number(f.current_ctc);
  const save = useMutation({
    mutationFn: async (existingId?: string) => {
      let candId = existingId;
      if (!candId) {
        const c = await hiringApi.createCandidate({ first_name: f.first_name, last_name: f.last_name, email: f.email, phone: f.phone ? normMobile(f.phone) : "",
          current_employer: f.current_employer, current_title: f.current_title, city: f.city, tags: f.tags || "", confirm_duplicate: dups.length > 0,
          ...(f.consent_given ? { consent_given: true } : {}) });
        candId = c.id;
      } else if (f.consent_given) {
        await hiringApi.updateCandidate(candId, { consent_given: true });
      }
      const a = await hiringApi.createApplication({ candidate_id: candId, requisition_id: f.requisition_id, source_type: f.source_type,
        source_detail: f.source_detail, referrer_employee_id: f.referrer_employee_id });
      if (file) await hiringApi.uploadDocument(candId!, file, "resume", a.id);
      // Experience, pay and availability live on the candidate's résumé profile (status "not checked yet").
      const prof: Row = Object.fromEntries(PROFILE_KEYS.filter((k) => f[k] !== undefined && f[k] !== "").map((k) => [k, f[k]]));
      if (String(f.qual_degree || "").trim()) prof.education = [{ degree: String(f.qual_degree).trim(), level: Number(f.qual_level), line: "Entered by HR" }];
      if (f.current_title) prof.current_title = f.current_title;
      if (PROFILE_KEYS.some((k) => k in prof) || prof.education) {
        try { await aiApi.saveProfile(candId!, prof); }
        catch (e) { toast.error(`Candidate added, but experience / pay were not saved: ${err(e)}`, { duration: 8000 }); }
      }
      if (Object.keys(cfValues).length) {
        const problem = await saveCustomValues("candidate", candId!, cfValues, cfDefs.data?.items || []);
        if (problem) toast.error(`Candidate added, but the custom fields were not saved: ${problem}`, { duration: 8000 });
      }
      return a;
    },
    onSuccess: (a) => { toast.success(`${a.candidate_name} added to ${a.req_number} at Applied.`); qc.invalidateQueries({ queryKey: ["hire"] }); onClose(); },
    onError: (e: any) => {
      const det = e?.response?.data?.detail;
      if (det?.possible_duplicates) { setDups(det.possible_duplicates); toast.error("This person may already be on file. Choose below."); }
      else { toast.error(err(e)); qc.invalidateQueries({ queryKey: ["hire"] }); }
    },
  });
  const trySave = (existingId?: string) => {
    setShown(true);
    // using an existing record: the person's own fields are not re-sent, so only the application checks matter
    const relevant = existingId ? Object.fromEntries(Object.entries(errs).filter(([k]) => !["first_name", "last_name", "email", "phone", "current_employer", "current_title", "city"].includes(k))) : errs;
    if (Object.keys(relevant).length) return void toast.error(firstError(relevant));
    save.mutate(existingId);
  };
  return (
    <Modal title="Add a candidate" subtitle="One record per person. The application is added to the requisition you pick." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={() => trySave(undefined)}>{dups.length ? "Add as a new person anyway" : "Add candidate"}</button></>}>
      {dups.length > 0 && (
        <div className="mb-3 border border-amber-300 bg-amber-50 rounded-lg p-3 text-sm">
          <div className="font-semibold text-amber-800 flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" />Possible duplicate</div>
          <p className="text-xs text-amber-800 mb-2">Someone with the same e-mail or phone is already on file. Nothing is merged for you. Choose one:</p>
          {dups.map((d) => (
            <div key={d.id} className="flex items-center gap-2 text-xs py-1">
              <span className="font-medium text-gray-800">{d.name}</span><span className="text-gray-500">same {(d.match || []).join(" and ")}</span>
              <button className="btn-secondary !py-0.5 !px-2 text-[11px] ml-auto" disabled={save.isPending} onClick={() => trySave(d.id)}>Use this record and add the application</button>
            </div>))}
        </div>)}
      <FormSection title="The person" hint="E-mail or phone is needed so we can check for duplicates.">
        <FormGrid cols={4}>
          <Field label={<>First name<Req /></>}><Input maxLength={100} value={f.first_name || ""} onChange={(e) => set("first_name", e.target.value)} /><FieldError msg={er("first_name")} /></Field>
          <Field label="Last name"><Input maxLength={100} value={f.last_name || ""} onChange={(e) => set("last_name", e.target.value)} /><FieldError msg={er("last_name")} /></Field>
          <Field label="E-mail"><Input type="email" value={f.email || ""} onChange={(e) => set("email", e.target.value)} onBlur={check} /><FieldError msg={er("email")} /></Field>
          <Field label="Mobile" hint="10 digits, no +1."><Input inputMode="numeric" value={f.phone || ""} onChange={(e) => set("phone", e.target.value)} onBlur={check} /><FieldError msg={er("phone")} /></Field>
          <Field label="Current employer"><Input maxLength={200} value={f.current_employer || ""} onChange={(e) => set("current_employer", e.target.value)} /><FieldError msg={er("current_employer")} /></Field>
          <Field label="Current job title"><Input maxLength={200} value={f.current_title || ""} onChange={(e) => set("current_title", e.target.value)} /><FieldError msg={er("current_title")} /></Field>
          <Field label="City"><Input maxLength={100} value={f.city || ""} onChange={(e) => set("city", e.target.value)} /><FieldError msg={er("city")} /></Field>
          <Field label="Tags" hint="Separate with commas."><Input value={f.tags || ""} onChange={(e) => set("tags", e.target.value)} /></Field>
        </FormGrid>
      </FormSection>
      <FormSection title="Experience, pay and availability" hint="Optional. Saved on the candidate's profile (Fit tab) for HR to check. Annual salary in Canadian dollars; pay is never used for the fit score.">
        <FormGrid cols={4}>
          <Field label="Total experience (years)" hint="0.5 steps for half years."><Input type="number" min={0} max={60} step="0.5" value={f.experience_years ?? ""} onChange={(e) => set("experience_years", e.target.value)} /><FieldError msg={er("experience_years")} /></Field>
          <Field label="Notice period (days)" hint="Days of notice still to serve before joining."><Input type="number" min={0} max={365} step={1} value={f.notice_period_days ?? ""} onChange={(e) => set("notice_period_days", e.target.value)} /><FieldError msg={er("notice_period_days")} /></Field>
          <Field label="Current salary ($ a year)"><Input type="number" min={0} className="text-right tabular-nums" value={f.current_ctc ?? ""} onChange={(e) => set("current_ctc", e.target.value)} /><FieldError msg={er("current_ctc")} /></Field>
          <Field label="Expected salary ($ a year)" hint={lowExpected ? "Lower than the current salary: check the figure." : "Compare with the requisition budget."}><Input type="number" min={0} className="text-right tabular-nums" value={f.expected_ctc ?? ""} onChange={(e) => set("expected_ctc", e.target.value)} /><FieldError msg={er("expected_ctc")} /></Field>
          <div className="sm:col-span-2"><Field label="Highest qualification" hint="As written, e.g. B.Com, Diploma in Nursing."><Input maxLength={80} value={f.qual_degree || ""} onChange={(e) => set("qual_degree", e.target.value)} /><FieldError msg={er("qual_degree")} /></Field></div>
          <div className="sm:col-span-2"><Field label="Qualification level"><Select value={f.qual_level || ""} onChange={(e) => set("qual_level", e.target.value)}>
            <option value="">Pick a level</option>{QUAL_LEVELS.map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select><FieldError msg={er("qual_level")} /></Field></div>
        </FormGrid>
      </FormSection>
      <FormSection title="Application">
        <FormGrid cols={4}>
          <Field label={<>Requisition<Req /></>}><Select value={f.requisition_id} onChange={(e) => set("requisition_id", e.target.value)}>
            {reqs.map((r) => <option key={r.id} value={r.id}>{r.req_number} · {r.designation}</option>)}</Select><FieldError msg={er("requisition_id")} /></Field>
          <Field label="Source"><Select value={f.source_type} onChange={(e) => set("source_type", e.target.value)}>
            {Object.entries(SOURCES).filter(([k]) => k !== "careers_page").map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select></Field>
          {f.source_type === "referral" && <Field label={<>Referred by<Req /></>}><Select value={f.referrer_employee_id || ""} onChange={(e) => set("referrer_employee_id", e.target.value)}>
            <option value="">Pick an employee</option>{employees.map((x) => <option key={x.id} value={x.id}>{empLabel(x)}</option>)}</Select><FieldError msg={er("referrer_employee_id")} /></Field>}
          {(f.source_type === "agency" || f.source_type === "job_board") && <Field label={<>{f.source_type === "agency" ? "Agency name" : "Job board name"}<Req /></>} hint="Where this candidate came from."><Input maxLength={200} value={f.source_detail || ""} onChange={(e) => set("source_detail", e.target.value)} /><FieldError msg={er("source_detail")} /></Field>}
          <Field label="Resume" hint={`PDF, DOCX, JPG or PNG${maxMb ? `, up to ${maxMb} MB` : ""}.`}><input type="file" accept=".pdf,.docx,.jpg,.jpeg,.png" className="text-xs" onChange={(e) => setFile(e.target.files?.[0] || null)} />
            {file && <button type="button" className="btn-secondary !py-0.5 !px-2 text-[11px] mt-1 disabled:opacity-50" disabled={reading} onClick={readResume}>{reading ? "Reading…" : "Fill the empty fields from the résumé"}</button>}<FieldError msg={er("file")} /></Field>
        </FormGrid>
        <label className="flex items-start gap-2 text-sm text-gray-700 mt-3">
          <input type="checkbox" className="mt-1" checked={!!f.consent_given} onChange={(e) => set("consent_given", e.target.checked)} />
          <span>The person agreed that we keep their details for hiring. <span className="text-[11px] text-gray-400">Recorded with today's date on the candidate.</span></span>
        </label>
      </FormSection>
      {readNote.length > 0 && <div className="mt-2"><Note>{readNote.join(" ")}</Note></div>}
      <div className="mt-4"><CustomFieldsSection entity="candidate" values={cfValues} onChange={setCfValues} title="Custom fields" /></div>
    </Modal>
  );
}

/* ── one application: stage, interviews, offer, notes, documents, history ──── */
function ApplicationModal({ id, isHr, onClose }: { id: string; isHr: boolean; onClose: () => void }) {
  const qc = useQueryClient();
  const employees = useEmployees();
  const { data, isLoading } = useQuery({ queryKey: ["hire", "app", id], queryFn: () => hiringApi.application(id) });
  const [tab, setTab] = useState("interviews");
  const [reject, setReject] = useState(false);
  const [offerForm, setOfferForm] = useState<Row | "new" | null>(null);
  const [note, setNote] = useState("");
  const [iv, setIv] = useState<Row>({ mode: "in_person", panel: [] });
  const [ivShown, setIvShown] = useState(false);
  const [docKind, setDocKind] = useState("document");
  const [tags, setTags] = useState<string | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["hire"] });
  const ok = (msg: string) => () => { toast.success(msg); refresh(); };
  const fail = (e: any) => toast.error(err(e));
  const a: Row = data?.application || {}, c: Row = data?.candidate || {};

  const move = useMutation({
    mutationFn: (v: { to: string; reason?: string }) => hiringApi.move([id], v.to, v.reason),
    onSuccess: (r, v) => { refresh(); setReject(false); r.moved.length ? toast.success(`Moved to ${stageLabel(v.to)}.`) : toast.error(r.failed[0]?.reason || "Not moved.", { duration: 7000 }); },
    onError: fail,
  });
  const schedule = useMutation({ mutationFn: () => hiringApi.scheduleInterview(id, { ...iv, round_name: String(iv.round_name || "").trim(), place: String(iv.place || "").trim(), scheduled_at: fromDateTimeLocal(iv.scheduled_at) }),
    onSuccess: () => { ok("Interview scheduled. The panel has been told.")(); setIv({ mode: "in_person", panel: [] }); setIvShown(false); }, onError: fail });
  const ivErrs = collect({
    round_name: v.required(String(iv.round_name || "").trim(), "Round name") || v.maxLen(iv.round_name, 120, "Round name"),
    scheduled_at: iv.scheduled_at && new Date(iv.scheduled_at).getTime() <= Date.now() ? "The interview time must be in the future" : null,
    place: v.maxLen(iv.place, 300, "Link or room") || (iv.mode === "video" && iv.place && !/^https:\/\/\S+/i.test(String(iv.place).trim()) ? "For a video call, paste the meeting link (https://…)" : null),
    panel: (iv.panel || []).length ? null : "Pick at least one panel member",
  });
  const ivEr = (k: string) => (ivShown ? ivErrs[k] : undefined);
  const trySchedule = () => { setIvShown(true); if (Object.keys(ivErrs).length) return void toast.error(firstError(ivErrs)); schedule.mutate(); };
  const addNote = useMutation({ mutationFn: () => hiringApi.addNote(id, note.trim()), onSuccess: () => { ok("Note added.")(); setNote(""); }, onError: fail });
  const upload = useMutation({ mutationFn: (file: File) => hiringApi.uploadDocument(c.id, file, docKind, id), onSuccess: ok("Document added."), onError: fail });
  const saveTags = useMutation({ mutationFn: () => hiringApi.updateCandidate(c.id, { tags: tags || "" }), onSuccess: () => { ok("Tags saved.")(); setTags(null); }, onError: fail });
  const tagErr = tags !== null && String(tags).split(",").some((t) => t.trim().length > 40) ? "Each tag can be at most 40 characters" : null;
  const merge = useMutation({ mutationFn: (into: string) => hiringApi.mergeCandidate(c.id, into), onSuccess: () => { ok("Merged into the other record.")(); onClose(); }, onError: fail });
  const download = async (d: Row) => { try { downloadBlob(d.file_name, await hiringApi.documentBlob(d.id)); } catch (e) { fail(e); } };

  const liveOffer = (data?.offers || []).find((o: Row) => ["draft", "approved", "sent", "accepted"].includes(o.status));
  const back = a.prev_stage && MANUAL.includes(a.prev_stage) ? a.prev_stage : a.prev_stage === "offer" ? "selected" : "applied";
  const nextMoves = ["rejected", "withdrawn", "on_hold"].includes(a.stage) ? [back] : MANUAL.includes(a.stage) ? MANUAL.filter((s) => s !== a.stage) : [];
  const TABS = [["interviews", `Interviews (${(data?.interviews || []).length})`], ["scorecard", "Scorecard"], ["fit", "Fit (advisory)"], ["offer", "Offer"], ["notes", `Notes (${(data?.notes || []).length})`],
    ["documents", `Documents (${(data?.documents || []).length})`], ["history", "History"]];

  return (
    <Modal size="screen" title={isLoading ? "Loading" : a.candidate_name} subtitle={isLoading ? "" : `${a.designation} · ${a.req_number} · ${SOURCES[a.source_type] || ""}${a.referrer_name ? ": " + a.referrer_name : a.source_detail ? ": " + a.source_detail : ""}`} onClose={onClose}>
      {isLoading || !data ? <div className="text-sm text-gray-400">Loading…</div> : (
        <div className="grid grid-cols-1 lg:grid-cols-[20rem_1fr] gap-5">
          <div className="space-y-3">
            <div className="card !p-3">
              <div className="flex items-center justify-between mb-2"><span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Stage</span><Pill s={a.stage} label={a.stage_label} /></div>
              <div className="text-xs text-gray-500 mb-2">{a.days_in_stage} days in this stage. Rounds done: {a.rounds_done} of {a.rounds_required}.{a.stage_reason ? ` Reason: ${a.stage_reason}` : ""}</div>
              <div className="flex flex-wrap gap-1">
                {nextMoves.map((s) => <button key={s} className="btn-secondary !py-1 !px-2 text-xs" disabled={move.isPending} onClick={() => move.mutate({ to: s })}>{["rejected", "withdrawn", "on_hold"].includes(a.stage) ? `Bring back to ${stageLabel(s)}` : `Move to ${stageLabel(s)}`}</button>)}
                {MANUAL.includes(a.stage) && <>
                  <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => move.mutate({ to: "on_hold" })}>Put on hold</button>
                  <button className="btn-secondary !py-1 !px-2 text-xs" onClick={() => move.mutate({ to: "withdrawn" })}>Candidate withdrew</button>
                  <button className="btn-secondary !py-1 !px-2 text-xs !text-red-600" onClick={() => setReject(true)}>Reject</button></>}
                {a.stage === "on_hold" && <button className="btn-secondary !py-1 !px-2 text-xs !text-red-600" onClick={() => setReject(true)}>Reject</button>}
              </div>
              {a.stage === "offer" && <p className="text-[11px] text-gray-400 mt-2">This candidate has an offer. Use the Offer tab. Joined is set when Onboarding activates the person.</p>}
            </div>
            <div className="card !p-3 text-sm space-y-1">
              <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-1">Person</div>
              {[["E-mail", c.email], ["Phone", c.phone], ["Current role", [c.current_title, c.current_employer].filter(Boolean).join(" at ")], ["City", c.city],
                ["Consent given", c.consent_at ? fmtDateTime(c.consent_at) : ""]].filter(([, v]) => v).map(([k, v]) => <div key={k} className="flex gap-2 text-xs"><span className="w-24 text-gray-400 shrink-0">{k}</span><span className="text-gray-700 break-all">{v}</span></div>)}
              <div className="pt-1">
                {tags === null ? <div className="text-xs">{(c.tags || []).map((t: string) => <span key={t} className="badge-purple mr-1">{t}</span>)}{isHr && <button className="underline text-primary-600 text-[11px]" onClick={() => setTags((c.tags || []).join(", "))}>{(c.tags || []).length ? "Edit tags" : "Add tags"}</button>}</div>
                  : <div><div className="flex gap-1"><Input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="tag one, tag two" /><button className="btn-primary !py-1 !px-2 text-xs disabled:opacity-50" disabled={!!tagErr || saveTags.isPending} onClick={() => saveTags.mutate()}>Save</button></div><FieldError msg={tagErr} /></div>}
              </div>
            </div>
            <CandidateComms candidate={c} isHr={isHr} />
            {c.id && <div className="card !p-3"><CustomFieldsSection entity="candidate" id={c.id} mode={isHr ? "edit" : "view"} compact title="Custom fields" /></div>}
            {(c.possible_duplicates || []).length > 0 && (
              <div className="border border-amber-300 bg-amber-50 rounded-lg p-3">
                <div className="font-semibold text-amber-800 text-sm flex items-center gap-1.5"><AlertTriangle className="w-4 h-4" />Possible duplicate</div>
                <p className="text-[11px] text-amber-800 mb-1">Another record has the same e-mail or phone. Merge only if it is the same person.</p>
                {(c.possible_duplicates as Row[]).map((d) => <div key={d.id} className="flex items-center gap-2 text-xs py-0.5"><span className="font-medium">{d.name}</span><span className="text-gray-500">same {(d.match || []).join(" and ")}</span>
                  {isHr && <button className="btn-secondary !py-0.5 !px-2 text-[11px] ml-auto" onClick={() => { if (window.confirm(`Merge this record into ${d.name}? This record's applications, notes and documents move to ${d.name}.`)) merge.mutate(d.id); }}>Merge into this one</button>}</div>)}
              </div>)}
          </div>

          <div>
            <div className="flex gap-1 mb-3 overflow-x-auto">{TABS.map(([k, l]) => <button key={k} onClick={() => setTab(k)} className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap ${tab === k ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>{l}</button>)}</div>

            {tab === "interviews" && <div className="space-y-3">
              {["applied", "screening", "interview"].includes(a.stage) && (
                <div className="card !p-3">
                  <div className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold mb-2">Schedule a round</div>
                  <FormGrid cols={3}>
                    <Field label={<>Round name<Req /></>}><Input placeholder="Technical round" maxLength={120} value={iv.round_name || ""} onChange={(e) => setIv({ ...iv, round_name: e.target.value })} /><FieldError msg={ivEr("round_name")} /></Field>
                    <Field label="Date and time" hint="Leave empty to propose slots to the candidate later."><Input type="datetime-local" value={iv.scheduled_at || ""} onChange={(e) => setIv({ ...iv, scheduled_at: e.target.value })} /><FieldError msg={ivEr("scheduled_at")} /></Field>
                    <Field label="Mode"><Select value={iv.mode} onChange={(e) => setIv({ ...iv, mode: e.target.value })}>{Object.entries(MODES).map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select></Field>
                    <div className="sm:col-span-2 lg:col-span-3"><Field label={iv.mode === "video" ? "Meeting link" : iv.mode === "phone" ? "Number to call / notes" : "Room or address"} hint="The candidate and the panel get this with the invite.">
                      <Input maxLength={300} placeholder={iv.mode === "video" ? "https://" : ""} value={iv.place || ""} onChange={(e) => setIv({ ...iv, place: e.target.value })} /><FieldError msg={ivEr("place")} /></Field></div>
                    <div className="sm:col-span-2 lg:col-span-3"><Field label={<>Panel<Req /></>} hint="Hold Ctrl (or Cmd) to pick more than one. Each panel member submits their own feedback.">
                      <select multiple className="input text-sm w-full h-24" value={iv.panel} onChange={(e) => setIv({ ...iv, panel: Array.from(e.target.selectedOptions).map((o) => o.value) })}>
                        {employees.map((x) => <option key={x.id} value={x.id}>{empLabel(x)}</option>)}</select><FieldError msg={ivEr("panel")} /></Field></div>
                  </FormGrid>
                  <div className="flex justify-end mt-2"><button className="btn-primary disabled:opacity-50" disabled={schedule.isPending} onClick={trySchedule}>Schedule</button></div>
                  {!employees.length && <p className="text-xs text-amber-700 mt-2">There are no employees to put on a panel. Add employees first.</p>}
                </div>)}
              {!(data.interviews || []).length ? <div className="text-sm text-gray-400 text-center py-6">No interview rounds yet. {a.rounds_required} round(s) with all feedback are needed before the candidate can be Selected.</div>
                : (data.interviews as Row[]).map((i) => (
                  <div key={i.id} className="card !p-3">
                    <div className="flex flex-wrap items-center gap-2 mb-1.5">
                      <span className="font-semibold text-sm text-gray-800">Round {i.round_no}: {i.round_name}</span>
                      <Pill s={i.status === "cancelled" ? "cancelled" : i.feedback_pending.length ? "pending" : "submitted"} label={i.status === "cancelled" ? "Cancelled" : i.feedback_pending.length ? `Waiting for ${i.feedback_pending.length} feedback` : "Complete"} />
                      <span className="text-xs text-gray-500">{fmtDateTime(i.scheduled_at) || "No time set"} · {MODES[i.mode]}{i.place ? ` · ${i.place}` : ""}</span>
                    </div>
                    <div className="text-xs text-gray-500 mb-1">Panel: {(i.panel || []).map((p: Row) => p.name).join(", ")}</div>
                    {(i.feedback as Row[]).map((fb) => (
                      <div key={fb.id} className="border-t border-surface-border pt-1.5 mt-1.5 text-xs">
                        <div className="flex gap-2 items-center"><b>{fb.interviewer_name}</b><span>Rating {fb.rating} of 5</span><Pill s={fb.recommendation === "hire" ? "accepted" : fb.recommendation === "no_hire" ? "rejected" : "on_hold"} label={REC[fb.recommendation]} /></div>
                        {fb.strengths && <div><span className="text-gray-400">Strengths: </span>{fb.strengths}</div>}
                        {fb.concerns && <div><span className="text-gray-400">Concerns: </span>{fb.concerns}</div>}
                      </div>))}
                    <InterviewTools iv={i} />
                  </div>))}
            </div>}

            {tab === "scorecard" && <ScorecardSummary appId={id} />}
            {tab === "fit" && <FitPanel appId={id} candidateId={c.id} isHr={isHr} />}

            {tab === "offer" && <div className="space-y-3">
              {!isHr ? <Note>Offers are prepared by HR.</Note> : !(data.offers || []).length && a.stage !== "selected"
                ? <Note>An offer can be prepared once the candidate is marked Selected.</Note> : null}
              {isHr && a.stage === "selected" && !liveOffer && <button className="btn-primary" onClick={() => setOfferForm("new")}>Prepare an offer</button>}
              {(data.offers as Row[]).map((o) => (
                <div key={o.id} className="card !p-3">
                  <div className="flex flex-wrap items-center gap-2 mb-2"><span className="font-semibold text-sm">{o.designation}</span><Pill s={o.status} />
                    <span className="text-xs text-gray-500">Joining {fmtDate(o.joining_date) || "not set"} · Approvals {(o.approvals || []).length} of {o.approvals_required}{o.above_budget ? " · above budget" : ""}</span></div>
                  {o.status_reason && <div className="text-xs text-gray-500 mb-2">Reason: {o.status_reason}</div>}
                  <p className="text-xs text-gray-500">Approve, send and record the answer in Hiring, Offers.</p>
                </div>))}
            </div>}

            {tab === "notes" && <div>
              <div className="flex gap-2 mb-3"><Textarea rows={2} maxLength={4000} placeholder="Add a note for the hiring team" value={note} onChange={(e) => setNote(e.target.value)} />
                <button className="btn-primary self-start disabled:opacity-50" disabled={!note.trim() || addNote.isPending} onClick={() => addNote.mutate()}>Add</button></div>
              {!(data.notes || []).length ? <div className="text-sm text-gray-400 text-center py-6">No notes yet.</div>
                : (data.notes as Row[]).map((n) => <div key={n.id} className="border-b border-surface-border py-2 text-sm"><div className="text-gray-700 whitespace-pre-wrap">{n.body}</div><div className="text-[11px] text-gray-400">{n.author_name} · {fmtDateTime(n.created_at)}</div></div>)}
            </div>}

            {tab === "documents" && <div>
              {isHr && <div className="flex flex-wrap items-end gap-2 mb-3">
                <Field label="Document type" hint="Tag the file so onboarding finds ID proof, certificates and letters quickly."><Select className="!w-56" value={docKind} onChange={(e) => setDocKind(e.target.value)}>
                  {Object.entries(DOC_KINDS).map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select></Field>
                <label className="btn-secondary inline-flex items-center gap-1.5 cursor-pointer"><Upload className="w-4 h-4" />Add a document
                  <input type="file" className="hidden" accept=".pdf,.docx,.jpg,.jpeg,.png" onChange={(e) => { const fl = e.target.files?.[0]; if (fl) { if (!UPLOAD_TYPES.test(fl.name)) toast.error("Upload a PDF, DOCX, JPG or PNG file."); else upload.mutate(fl); } e.target.value = ""; }} /></label>
                <span className="text-[11px] text-gray-400">PDF, DOCX, JPG or PNG.</span></div>}
              {!(data.documents || []).length ? <div className="text-sm text-gray-400 text-center py-6">No resume or documents on file.</div>
                : (data.documents as Row[]).map((dd) => <div key={dd.id} className="flex items-center gap-2 border-b border-surface-border py-2 text-sm">
                  <span className="badge-purple">{DOC_KINDS[dd.kind] || dd.kind}</span><span className="text-gray-700 truncate">{dd.file_name}</span>
                  <span className="text-[11px] text-gray-400">{Math.max(1, Math.round(dd.size_bytes / 1024))} KB · {fmtDate(dd.uploaded_at)}{dd.from_candidate ? " · from the candidate" : ""}</span>
                  <button className="btn-secondary !py-0.5 !px-2 text-[11px] ml-auto inline-flex items-center gap-1" onClick={() => download(dd)}><Download className="w-3 h-3" />Download</button></div>)}
            </div>}

            {tab === "history" && <div>{(data.history as Row[]).map((h) => <div key={h.id} className="flex gap-3 border-b border-surface-border py-1.5 text-xs">
              <span className="w-36 text-gray-400 shrink-0">{fmtDateTime(h.at)}</span><span className="text-gray-700">{h.from_stage ? `${stageLabel(h.from_stage)} to ${stageLabel(h.to_stage)}` : stageLabel(h.to_stage)}{h.reason ? `. ${h.reason}` : ""}</span><span className="ml-auto text-gray-400">{h.by_name}</span></div>)}</div>}
          </div>
        </div>
      )}
      {reject && <ReasonDialog title={`Reject ${a.candidate_name}`} hint="The reason is saved on the application." confirm="Reject" onClose={() => setReject(false)} onConfirm={(reason) => move.mutate({ to: "rejected", reason })} />}
      {offerForm && <OfferForm appId={id} defaults={{ candidate_name: a.candidate_name, designation: a.designation, department: a.department }} onClose={() => setOfferForm(null)} />}
    </Modal>
  );
}
