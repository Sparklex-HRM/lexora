import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileSearch, RefreshCw, Sparkles, Wand2 } from "lucide-react";
import toast from "react-hot-toast";
import { EMP_TYPES, err, fmtDateTime, Row } from "./api";
import { aiApi, BAND_TONE, cad } from "./api_ai";
import { Col, Field, Grid, Input, Modal, Note, Select, Textarea } from "./ui";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

/* G6 (2026-10-23): hiring assist panels. Everything is ADVISORY — a person decides; no button here
   moves or rejects a candidate. Résumé data stays in component state only (no browser storage). */

/** Qualification level as the fit score reads it (1 school ... 5 doctorate). */
export const QUAL_LEVELS: [string, string][] = [["1", "School (Class 10 / 12)"], ["2", "Diploma / ITI"], ["3", "Bachelor's degree"], ["4", "Master's / professional (CA, CS, CMA)"], ["5", "Doctorate"]];

const ADVISORY = "Advisory only. A person reviews every candidate; nobody is moved or rejected because of a score.";

/** The application's "Fit" tab: the parsed profile (check + confirm) and the fit score with its working. */
export function FitPanel({ appId, candidateId, isHr }: { appId: string; candidateId: string; isHr: boolean }) {
  const qc = useQueryClient();
  const prof = useQuery({ queryKey: ["hire", "ai", "profile", candidateId], queryFn: () => aiApi.profile(candidateId), enabled: !!candidateId });
  const fit = useQuery({ queryKey: ["hire", "ai", "match", appId], queryFn: () => aiApi.latestMatch(appId) });
  const [edit, setEdit] = useState<Row | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["hire", "ai"] });
  const parse = useMutation({
    mutationFn: () => aiApi.parseCandidate(candidateId),
    onSuccess: (r) => { toast.success(`Résumé read (${r.profile.method === "rules+ai" ? "rules + AI" : "rules"}). Check the fields, then confirm.`); (r.warnings || []).forEach((w: string) => toast(w, { icon: "ℹ️" })); refresh(); },
    onError: (e) => toast.error(err(e), { duration: 7000 }),
  });
  const save = useMutation({
    mutationFn: (body: Row) => aiApi.saveProfile(candidateId, body),
    onSuccess: () => { toast.success("Profile saved."); setEdit(null); refresh(); },
    onError: (e) => toast.error(err(e)),
  });
  const score = useMutation({ mutationFn: () => aiApi.match(appId), onSuccess: () => { toast.success("Fit worked out."); refresh(); }, onError: (e) => toast.error(err(e), { duration: 7000 }) });
  const p: Row | null = prof.data?.profile || null;
  const m: Row | null = fit.data || null;
  const [shown, setShown] = useState(false);
  const [errMode, setErrMode] = useState(false);       // the confirm-only checks apply after "Save as checked"
  const errsFor = (x: Row, confirm: boolean) => collect({
    skills: confirm ? v.required(String(x.skills || "").replace(/[\s,]/g, ""), "Skills") : null,
    experience_years: (confirm ? v.required(x.experience_years, "Experience") : null) || v.range(x.experience_years, 0, 60, "Experience"),
    notice_period_days: v.range(x.notice_period_days, 0, 365, "Notice period") || (x.notice_period_days !== "" && x.notice_period_days != null && !Number.isInteger(Number(x.notice_period_days)) ? "Notice period must be whole days" : null),
    current_ctc: v.range(x.current_ctc, 0, 1e9, "Current salary"),
    expected_ctc: v.range(x.expected_ctc, 0, 1e9, "Expected salary"),
    qual_degree: x.qual_level && !String(x.qual_degree || "").trim() ? "Type the qualification for this level" : v.maxLen(x.qual_degree, 80, "Qualification"),
    qual_level: String(x.qual_degree || "").trim() && !x.qual_level ? "Pick the qualification level" : null,
    summary: v.maxLen(x.summary, 800, "Summary"),
  });
  const errs = edit ? errsFor(edit, errMode) : {};
  const er = (k: string) => (shown ? errs[k] : undefined);
  const warnCtc = edit && edit.current_ctc !== "" && edit.expected_ctc !== "" && Number(edit.expected_ctc) < Number(edit.current_ctc)
    ? "Lower than the current salary: check the figure." : "Annual expected salary.";
  const trySave = (confirm: boolean) => {
    const x = edit!;
    const e2 = errsFor(x, confirm);
    setShown(true); setErrMode(confirm);
    if (Object.keys(e2).length) return void toast.error(firstError(e2));
    const skills = [...new Set(String(x.skills || "").split(",").map((t: string) => t.trim()).filter(Boolean))].join(", ");
    const rest = (p?.education || []).slice(1);
    const education = String(x.qual_degree || "").trim()
      ? [{ degree: String(x.qual_degree).trim(), level: Number(x.qual_level), line: "Entered by HR" }, ...rest] : rest;
    const { qual_degree: _d, qual_level: _l, ...body } = x;
    save.mutate({ ...body, skills, education, ...(confirm ? { confirm: true } : {}) });
  };
  return (
    <div className="space-y-3">
      <Note>{ADVISORY} Names, contacts, money, employer and college are never used for the score and never sent to an AI model.</Note>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <div className="card !p-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Profile from the résumé</span>
            {p && <span className={p.status === "confirmed" ? "badge-green" : "badge-amber"}>{p.status === "confirmed" ? "Checked by HR" : "Not checked yet"}</span>}
            <span className="ml-auto flex gap-1">
              {isHr && <button className="btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1 disabled:opacity-50" disabled={parse.isPending} onClick={() => parse.mutate()}><FileSearch className="w-3 h-3" />{p ? "Read again" : "Read the résumé"}</button>}
              {isHr && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => { setShown(false); setEdit({ skills: (p?.skills || []).join(", "), experience_years: p?.experience_years ?? "", notice_period_days: p?.notice_period_days ?? "",
                current_ctc: p?.current_ctc ?? "", expected_ctc: p?.expected_ctc ?? "", current_title: p?.current_title || "", summary: p?.summary || "",
                qual_degree: p?.education?.[0]?.degree || "", qual_level: p?.education?.[0]?.level ? String(p.education[0].level) : "" }); }}>{p ? "Edit" : "Fill in by hand"}</button>}
            </span>
          </div>
          {!p ? <div className="text-xs text-gray-400 py-4 text-center">{isHr ? "No profile yet. Press Read the résumé (the file must be under Documents), or fill it in by hand." : "HR has not read this résumé yet."}</div> : (
            <div className="text-xs space-y-1">
              {[["Skills", (p.skills || []).join(", ")], ["Experience", p.experience_years != null ? `${p.experience_years} years` : ""], ["Qualification", (p.education || []).map((e: Row) => e.degree).join(", ")],
                ["Notice period", p.notice_period_days != null ? `${p.notice_period_days} days` : ""], ["Current title", p.current_title],
                ...(isHr ? [["Current salary", cad(p.current_ctc)], ["Expected salary", cad(p.expected_ctc)]] : []), ["Summary", p.summary],
                ["Read by", `${p.method === "rules+ai" ? "rules + AI" : p.method}, ${fmtDateTime(p.parsed_at)}`]].filter(([, v]) => v).map(([k, v]) => (
                <div key={k as string} className="flex gap-2"><span className="w-28 shrink-0 text-gray-400">{k}</span><span className="text-gray-700">{v}</span></div>))}
              {Object.keys(p.evidence || {}).length > 0 && <details className="text-[11px] text-gray-500 pt-1"><summary className="cursor-pointer">Where each figure was found</summary>
                {Object.entries(p.evidence).map(([k, v]) => <div key={k}><b>{k.replace(/_/g, " ")}</b>: {String(v)}</div>)}</details>}
            </div>)}
        </div>
        <div className="card !p-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">Fit to this job</span>
            {m && <span className={BAND_TONE[m.band] || "badge-purple"}>{m.score} / 100 · {m.band}</span>}
            <button className="btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1 ml-auto disabled:opacity-50" disabled={score.isPending || !p} title={p ? "" : "A profile is needed first"} onClick={() => score.mutate()}><RefreshCw className="w-3 h-3" />{m ? "Work it out again" : "Work out the fit"}</button>
          </div>
          {!m ? <div className="text-xs text-gray-400 py-4 text-center">No fit worked out yet.</div> : (
            <div className="text-xs">
              <table className="w-full"><thead className="text-[10px] uppercase text-gray-400"><tr><th className="text-left py-1">Part</th><th className="text-right">Points</th><th className="text-left pl-3">Why</th></tr></thead>
                <tbody>{(m.detail?.lines || []).map((ln: Row) => <tr key={ln.part} className="border-t border-surface-border"><td className="py-1">{ln.part}</td><td className="text-right tabular-nums">{ln.got} / {ln.weight}</td><td className="pl-3 text-gray-600">{ln.why}</td></tr>)}</tbody></table>
              {m.detail?.ai_view && <div className="mt-2 border-t border-surface-border pt-2"><div className="font-semibold text-gray-700 inline-flex items-center gap-1"><Sparkles className="w-3 h-3" />AI second opinion</div>
                <div className="text-gray-600">{m.detail.ai_view.summary}</div>
                {!!m.detail.ai_view.strengths?.length && <div><b>Strengths:</b> {m.detail.ai_view.strengths.join("; ")}</div>}
                {!!m.detail.ai_view.gaps?.length && <div><b>Gaps:</b> {m.detail.ai_view.gaps.join("; ")}</div>}</div>}
              {m.detail?.note && <div className="text-[11px] text-gray-400 mt-1">{m.detail.note}</div>}
              <div className="text-[11px] text-gray-400 mt-1">Worked out {fmtDateTime(m.at)} ({m.method === "rules+ai" ? "rules + AI opinion" : "rules"}).</div>
            </div>)}
        </div>
      </div>
      {edit && (
        <Modal title="Candidate profile" subtitle="Check every field against the résumé. Saving with 'Checked' marks the profile as confirmed by you." onClose={() => setEdit(null)}
          footer={<><button className="btn-secondary" onClick={() => setEdit(null)}>Cancel</button>
            <button className="btn-secondary disabled:opacity-50" disabled={save.isPending} onClick={() => trySave(false)}>Save</button>
            <button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={() => trySave(true)}>Save as checked</button></>}>
          <FormSection title="Skills and background">
            <FormGrid cols={4}>
              <div className="sm:col-span-2 lg:col-span-4"><Field label={<>Skills<Req /></>} hint="Separate with commas. Duplicates are removed; at most 60."><Input value={edit.skills} onChange={(e) => setEdit({ ...edit, skills: e.target.value })} /><FieldError msg={er("skills")} /></Field></div>
              <Field label={<>Experience (years)<Req /></>} hint="Total relevant work experience; 0.5 steps for half years."><Input type="number" min={0} max={60} step="0.5" value={edit.experience_years} onChange={(e) => setEdit({ ...edit, experience_years: e.target.value })} /><FieldError msg={er("experience_years")} /></Field>
              <Field label="Current title"><Input maxLength={200} value={edit.current_title} onChange={(e) => setEdit({ ...edit, current_title: e.target.value })} /></Field>
              <Field label="Highest qualification" hint="As written, e.g. B.Tech (Mechanical)."><Input maxLength={80} value={edit.qual_degree} onChange={(e) => setEdit({ ...edit, qual_degree: e.target.value })} /><FieldError msg={er("qual_degree")} /></Field>
              <Field label="Qualification level" hint="Used by the fit score."><Select value={edit.qual_level} onChange={(e) => setEdit({ ...edit, qual_level: e.target.value })}>
                <option value="">Pick a level</option>{QUAL_LEVELS.map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select><FieldError msg={er("qual_level")} /></Field>
            </FormGrid>
          </FormSection>
          <FormSection title="Pay and availability" hint="Annual figures in Canadian dollars. Used for offer planning only, never for the fit score.">
            <FormGrid cols={3}>
              <Field label="Current salary ($ a year)"><Input type="number" min={0} className="text-right tabular-nums" value={edit.current_ctc} onChange={(e) => setEdit({ ...edit, current_ctc: e.target.value })} /><FieldError msg={er("current_ctc")} /></Field>
              <Field label="Expected salary ($ a year)" hint={warnCtc}><Input type="number" min={0} className="text-right tabular-nums" value={edit.expected_ctc} onChange={(e) => setEdit({ ...edit, expected_ctc: e.target.value })} /><FieldError msg={er("expected_ctc")} /></Field>
              <Field label="Notice period (days)" hint="Days of notice still to serve at the current employer."><Input type="number" min={0} max={365} step={1} value={edit.notice_period_days} onChange={(e) => setEdit({ ...edit, notice_period_days: e.target.value })} /><FieldError msg={er("notice_period_days")} /></Field>
            </FormGrid>
          </FormSection>
          <Field label="Summary" hint={`Up to 800 characters (${String(edit.summary || "").length} used).`}><Textarea rows={3} maxLength={800} value={edit.summary} onChange={(e) => setEdit({ ...edit, summary: e.target.value })} /><FieldError msg={er("summary")} /></Field>
        </Modal>)}
    </div>
  );
}

/** Applications of one requisition ordered by the advisory score (a reading aid, not a decision). */
export function RankingModal({ reqId, reqLabel, onOpen, onClose }: { reqId: string; reqLabel: string; onOpen: (appId: string) => void; onClose: () => void }) {
  const [refresh, setRefresh] = useState(false);
  const q = useQuery({ queryKey: ["hire", "ai", "ranking", reqId, refresh], queryFn: () => aiApi.ranking(reqId, refresh) });
  const cols: Col<Row>[] = [
    { k: "candidate_name", label: "Candidate", render: (r) => <span className="font-medium text-gray-800">{r.candidate_name}</span> },
    { k: "stage", label: "Stage" },
    { k: "score", label: "Fit (advisory)", align: "right", render: (r) => (r.score == null ? <span className="text-gray-400">{r.why_none}</span> : <span className={BAND_TONE[r.band] || ""}>{r.score}</span>), csv: (r) => r.score ?? "" },
    { k: "matched_skills", label: "Matched skills", render: (r) => (r.matched_skills || []).join(", "), csv: (r) => (r.matched_skills || []).join("; ") },
    { k: "missing_skills", label: "Missing skills", render: (r) => (r.missing_skills || []).join(", "), csv: (r) => (r.missing_skills || []).join("; ") },
    { k: "scored_at", label: "Worked out", render: (r) => fmtDateTime(r.scored_at) },
  ];
  return (
    <Modal size="xl" title={`Fit overview: ${reqLabel}`} subtitle={q.data?.advisory || ADVISORY} onClose={onClose} footer={<button className="btn-primary" onClick={onClose}>Close</button>}>
      <Grid rows={q.data?.items || []} cols={cols} keyOf={(r) => r.application_id} loading={q.isLoading} exportName="fit-overview.csv" title="Applications"
        empty="No applications for this requisition yet." onRow={(r) => onOpen(r.application_id)}
        actions={<button className="btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1" onClick={() => setRefresh(true)}><RefreshCw className="w-3 h-3" />Re-score with rules</button>} />
    </Modal>
  );
}

/** Draft a job description from the requisition (rules always; AI wording when the AI platform has a key). */
export function JdAssistModal({ requisitionId, designation, onUse, onClose }: { requisitionId?: string; designation?: string; onUse: (d: { description: string; requirements: string }) => void; onClose: () => void }) {
  const [f, setF] = useState<Row>({ skills: "", experience_min: "", experience_max: "", responsibilities: "", about: "", designation: designation || "", department: "", location: "", employment_type: "" });
  const [out, setOut] = useState<Row | null>(null);
  const [shown, setShown] = useState(false);
  useEffect(() => { setF((p) => ({ ...p, designation: designation || p.designation })); }, [designation]);
  const whole = (x: unknown, label: string) => (x !== "" && x != null && !Number.isInteger(Number(x)) ? `${label} must be whole years` : null);
  const errs = collect({
    designation: v.required(f.designation, "Job title") || v.maxLen(f.designation, 120, "Job title"),
    experience_min: v.range(f.experience_min, 0, 60, "Experience from") || whole(f.experience_min, "Experience from"),
    experience_max: v.range(f.experience_max, 0, 60, "Experience to") || whole(f.experience_max, "Experience to")
      || (f.experience_min !== "" && f.experience_max !== "" && Number(f.experience_max) < Number(f.experience_min) ? "Experience 'to' cannot be less than 'from'" : null),
    department: v.maxLen(f.department, 120, "Department"),
    location: v.maxLen(f.location, 120, "Location"),
    responsibilities: v.maxLen(f.responsibilities, 3000, "What the person will do"),
    about: v.maxLen(f.about, 1000, "About the team"),
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const run = useMutation({
    mutationFn: () => aiApi.jd({ requisition_id: requisitionId || undefined, designation: f.designation, skills: f.skills, experience_min: f.experience_min || undefined,
      experience_max: f.experience_max || undefined, responsibilities: f.responsibilities, about: f.about,
      department: f.department || undefined, location: f.location || undefined, employment_type: f.employment_type || undefined }),
    onSuccess: (r) => setOut(r), onError: (e) => toast.error(err(e)),
  });
  const tryRun = () => { setShown(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); run.mutate(); };
  const fromReq = requisitionId ? "Leave empty to use the requisition's." : undefined;
  return (
    <Modal size="xl" title="Draft the job description" subtitle="A starting draft from the requisition. Edit it before you save the posting; nothing is published from here." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-secondary inline-flex items-center gap-1 disabled:opacity-50" disabled={run.isPending} onClick={tryRun}><Wand2 className="w-3.5 h-3.5" />{out ? "Draft again" : "Draft"}</button>
        <button className="btn-primary disabled:opacity-50" disabled={!out} onClick={() => { onUse({ description: out!.description, requirements: out!.requirements }); onClose(); }}>Use this draft</button></>}>
      <div className="grid grid-cols-1 lg:grid-cols-[24rem_1fr] gap-4">
        <div className="space-y-2">
          <Field label={<>Job title<Req /></>}><Input maxLength={120} value={f.designation} onChange={(e) => setF({ ...f, designation: e.target.value })} /><FieldError msg={er("designation")} /></Field>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <Field label="Department" hint={fromReq}><Input maxLength={120} value={f.department} onChange={(e) => setF({ ...f, department: e.target.value })} /><FieldError msg={er("department")} /></Field>
            <Field label="Location" hint={fromReq || "City, or remote / hybrid."}><Input maxLength={120} value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} /><FieldError msg={er("location")} /></Field>
          </div>
          <Field label="Employment type"><Select value={f.employment_type} onChange={(e) => setF({ ...f, employment_type: e.target.value })}>
            <option value="">{requisitionId ? "As on the requisition" : "Not stated"}</option>{Object.entries(EMP_TYPES).map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}</Select></Field>
          <Field label="Skills" hint="Separate with commas."><Input value={f.skills} onChange={(e) => setF({ ...f, skills: e.target.value })} placeholder="python, postgresql" /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Experience from (years)"><Input type="number" min={0} max={60} step={1} value={f.experience_min} onChange={(e) => setF({ ...f, experience_min: e.target.value })} /><FieldError msg={er("experience_min")} /></Field>
            <Field label="to (years)" hint="Leave empty for open-ended."><Input type="number" min={0} max={60} step={1} value={f.experience_max} onChange={(e) => setF({ ...f, experience_max: e.target.value })} /><FieldError msg={er("experience_max")} /></Field>
          </div>
          <Field label="What the person will do" hint="One per line (optional; a sensible list is used otherwise)."><Textarea rows={4} maxLength={3000} value={f.responsibilities} onChange={(e) => setF({ ...f, responsibilities: e.target.value })} /><FieldError msg={er("responsibilities")} /></Field>
          <Field label="About the team (optional)"><Textarea rows={3} maxLength={1000} value={f.about} onChange={(e) => setF({ ...f, about: e.target.value })} /><FieldError msg={er("about")} /></Field>
        </div>
        <div>
          {!out ? <div className="text-sm text-gray-400 py-10 text-center">Fill in what you know and press Draft.</div> : (<>
            <div className="text-[11px] text-gray-500 mb-2">{out.method === "ai" ? "Worded by the AI assistant (check it)." : "Built from rules (no AI key, or AI is off)."} {out.note || ""}</div>
            <Field label="Description"><Textarea rows={12} value={out.description} onChange={(e) => setOut({ ...out, description: e.target.value })} /></Field>
            <Field label="Requirements"><Textarea rows={7} value={out.requirements} onChange={(e) => setOut({ ...out, requirements: e.target.value })} /></Field>
          </>)}
        </div>
      </div>
    </Modal>
  );
}
