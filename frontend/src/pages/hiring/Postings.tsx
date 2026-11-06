import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, ExternalLink, Globe, Plus } from "lucide-react";
import toast from "react-hot-toast";
import { BOARD_LABELS, EMP_TYPES, err, fmtDate, hiringApi, Row } from "./api";
import { Col, downloadBlob, Field, FirstStep, Grid, Input, Modal, Note, Pill, Select, Textarea } from "./ui";
import { JdAssistModal } from "./AiPanels";   // G6: draft the description
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req } from "../../lib/validators";

export default function Postings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["hire", "postings"], queryFn: hiringApi.postings });
  const reqs = useQuery({ queryKey: ["hire", "reqs"], queryFn: hiringApi.requisitions });
  const settings = useQuery({ queryKey: ["hire", "settings"], queryFn: hiringApi.settings });
  const summary = useQuery({ queryKey: ["hire", "boards"], queryFn: hiringApi.boardsSummary });
  const [edit, setEdit] = useState<Row | "new" | null>(null);
  const [boards, setBoards] = useState<Row | null>(null);
  const open = (reqs.data || []).filter((r) => r.status === "open");
  const s = settings.data;
  const careersUrl = s?.org_code ? `${window.location.origin}/careers/${s.org_code}` : "";

  const publish = useMutation({
    mutationFn: (v: { id: string; on: boolean }) => hiringApi.updatePosting(v.id, { is_published: v.on }),
    onSuccess: (r) => { toast.success(r.is_published ? "Published. It now shows on the careers page." : "Taken down from the careers page."); qc.invalidateQueries({ queryKey: ["hire"] }); },
    onError: (e) => toast.error(err(e)),
  });

  const cols: Col<Row>[] = [
    { k: "title", label: "Job title" },
    { k: "req_number", label: "Requisition" },
    { k: "department", label: "Department" },
    { k: "location", label: "Location" },
    { k: "applications", label: "Applications", align: "right" },
    { k: "published_at", label: "Published on", render: (r) => fmtDate(r.published_at) },
    { k: "live", label: "Careers page", render: (r) => <Pill s={r.live ? "open" : r.is_published ? "on_hold" : "draft"} label={r.live ? "Live" : r.is_published ? "Hidden: requisition not open" : "Not published"} />, csv: (r) => (r.live ? "Live" : "Not live") },
    { k: "_a", label: "Actions", csv: () => "", render: (r) => (
      <div className="flex gap-1" onClick={(e) => e.stopPropagation()}>
        <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => setEdit(r)}>Edit</button>
        <button className={`${r.is_published ? "btn-secondary" : "btn-primary"} !py-0.5 !px-2 text-[11px]`} onClick={() => publish.mutate({ id: r.id, on: !r.is_published })}>{r.is_published ? "Take down" : "Publish"}</button>
        <button className="btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1" onClick={() => setBoards(r)}><Globe className="w-3 h-3" />Job boards</button>
        {r.live && careersUrl && <a className="btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1" href={`${careersUrl}/${r.id}`} target="_blank" rel="noreferrer"><ExternalLink className="w-3 h-3" />View</a>}
      </div>) },
  ];

  return (
    <div>
      {s && (s.careers_page_enabled
        ? <Note>Your public careers page is on: <b>{careersUrl}</b>{" "}
            <button className="underline inline-flex items-center gap-1 ml-1" onClick={() => { navigator.clipboard?.writeText(careersUrl); toast.success("Link copied."); }}><Copy className="w-3 h-3" />Copy link</button>
          </Note>
        : <Note tone="warn">The public careers page is switched off, so published jobs are not visible to anyone outside. An HR head can switch it on in Hiring, Settings.</Note>)}
      {summary.data && <div className="card !p-2 mb-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold text-gray-700">Job boards:</span>
        {(summary.data.boards as Row[]).map((b) => <span key={b.board} className={b.partner_only ? (b.configured ? "badge-teal" : "badge-purple") : "badge-teal"} title={b.partner_only ? (b.configured ? `Partner feed: ${b.feed_url}` : `Partner feed not set up: needs ${b.needs}`) : b.how === "feed" ? "Public XML feed" : "JSON-LD on each job page"}>{b.label}: {b.partner_only && !b.configured ? "not set up (export only)" : `${b.enabled_postings} on`}</span>)}
        <span className="text-gray-500">Indeed feed URL: <code className="select-all">{summary.data.feed_url}</code></span>
        <button className="underline inline-flex items-center gap-1" onClick={() => { navigator.clipboard?.writeText(summary.data!.feed_url); toast.success("Feed URL copied. Give it to Indeed (Employer, XML feed)."); }}><Copy className="w-3 h-3" />Copy</button>
        <button className="underline inline-flex items-center gap-1 ml-auto" onClick={async () => { try { downloadBlob("job-postings-export.csv", await hiringApi.exportCsvBlob()); } catch (e) { toast.error(err(e)); } }}><Download className="w-3 h-3" />Export all live postings (CSV)</button>
      </div>}
      {!isLoading && !(data || []).length ? (
        <FirstStep title="No job postings yet">
          A posting is the public wording for an Open requisition: title, description and requirements.
          {open.length ? <div className="mt-4"><button className="btn-primary inline-flex items-center gap-1.5" onClick={() => setEdit("new")}><Plus className="w-4 h-4" />Write a posting</button></div>
            : <div className="mt-2">There is no Open requisition yet. Raise a requisition and get it approved first.</div>}
        </FirstStep>
      ) : (
        <Grid rows={data || []} cols={cols} keyOf={(r) => r.id} loading={isLoading} exportName="job-postings.csv" title="Job postings"
          actions={<button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 disabled:opacity-50" disabled={!open.length} title={open.length ? "" : "No Open requisition"} onClick={() => setEdit("new")}><Plus className="w-3.5 h-3.5" />Write a posting</button>} />
      )}
      {edit && <PostingForm row={edit === "new" ? null : edit} open={open} onClose={() => setEdit(null)} />}
      {boards && <BoardsModal posting={boards} onClose={() => setBoards(null)} />}
    </div>
  );
}

/** Per-posting publish toggles for the job boards, the feed / JSON-LD state, and the export for partner-only boards. */
function BoardsModal({ posting, onClose }: { posting: Row; onClose: () => void }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ["hire", "boards", posting.id], queryFn: () => hiringApi.boards(posting.id) });
  const [text, setText] = useState<{ board: string; text: string } | null>(null);
  const toggle = useMutation({ mutationFn: (v: { board: string; on: boolean }) => hiringApi.setBoard(posting.id, v.board, v.on), onSuccess: (_r, v) => { toast.success(`${BOARD_LABELS[v.board]} ${v.on ? "on" : "off"}.`); qc.invalidateQueries({ queryKey: ["hire"] }); }, onError: (e) => toast.error(err(e), { duration: 7000 }) });
  const exp = useMutation({ mutationFn: (board: string) => hiringApi.exportText(posting.id, board), onSuccess: (t, board) => setText({ board, text: t }), onError: (e) => toast.error(err(e)) });
  return (
    <Modal title={`Job boards: ${posting.title}`} subtitle={data ? (data.live ? "This posting is live on the careers page." : "Not live: publish it and keep the requisition open, or boards stay hidden.") : ""} onClose={onClose} size="lg"
      footer={<button className="btn-primary" onClick={onClose}>Close</button>}>
      {isLoading || !data ? <div className="text-sm text-gray-400">Loading…</div> : (
        <div className="space-y-3">
          {(data.boards as Row[]).map((b) => (
            <div key={b.board} className="border border-surface-border rounded-lg p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{b.label}</span>
                <Pill s={b.state === "live" || b.state === "in_feed" ? "open" : b.state === "export_only" || b.state === "needs_setup" ? "draft" : b.state === "hidden" ? "on_hold" : "cancelled"} label={b.state.replace(/_/g, " ")} />
                {b.published_at && <span className="text-xs text-gray-400">since {fmtDate(b.published_at)}</span>}
                <span className="ml-auto flex gap-1">
                  {b.how !== "export" && b.state !== "needs_setup" && <button className={`${b.enabled ? "btn-secondary" : "btn-primary"} !py-0.5 !px-2 text-[11px]`} disabled={toggle.isPending} onClick={() => toggle.mutate({ board: b.board, on: !b.enabled })}>{b.enabled ? "Switch off" : b.how === "partner_feed" ? "Add to feed" : "Publish"}</button>}
                  {(b.how === "export" || b.how === "partner_feed") && <button className="btn-secondary !py-0.5 !px-2 text-[11px]" onClick={() => exp.mutate(b.board)}>Export text</button>}
                  <a className="btn-secondary !py-0.5 !px-2 text-[11px]" href={b.docs} target="_blank" rel="noreferrer">Docs</a>
                </span>
              </div>
              <div className="text-xs text-gray-500 mt-1">{b.message}</div>
              {b.board === "indeed" && b.enabled && <div className="text-[11px] text-gray-400 mt-1">Feed: <code className="select-all">{data.feed_url}</code></div>}
              {b.how === "partner_feed" && b.state !== "needs_setup" && b.feed_url && <div className="text-[11px] text-gray-400 mt-1">Feed to give {b.label}: <code className="select-all">{b.feed_url}</code></div>}
              {!!(b.issues || []).length && b.state !== "needs_setup" && <div className="text-[11px] text-amber-700 mt-1">{b.label} would reject this job: {(b.issues as string[]).join("; ")}.</div>}
              {b.board === "google_jobs" && b.enabled && <details className="text-[11px] text-gray-400 mt-1"><summary className="cursor-pointer">JSON-LD on {data.job_url}</summary><pre className="whitespace-pre-wrap bg-surface rounded p-2 mt-1 max-h-48 overflow-auto">{JSON.stringify(data.jsonld, null, 2)}</pre></details>}
            </div>))}
          {text && <div className="border border-surface-border rounded-lg p-3">
            <div className="flex items-center justify-between mb-1"><span className="text-xs font-semibold">{BOARD_LABELS[text.board]}: copy-ready text</span><button className="btn-secondary !py-0.5 !px-2 text-[11px] inline-flex items-center gap-1" onClick={() => { navigator.clipboard?.writeText(text.text); toast.success("Copied."); }}><Copy className="w-3 h-3" />Copy</button></div>
            <pre className="whitespace-pre-wrap text-xs bg-surface rounded p-2 max-h-64 overflow-auto">{text.text}</pre>
          </div>}
        </div>)}
    </Modal>
  );
}

function PostingForm({ row, open, onClose }: { row: Row | null; open: Row[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [f, setF] = useState<Row>(row ? { ...row } : { requisition_id: open[0]?.id || "", title: open[0]?.designation || "" });
  const set = (k: string, v: any) => setF((p) => ({ ...p, [k]: v }));
  const [assist, setAssist] = useState(false);
  const [shown, setShown] = useState(false);
  const req: Row | undefined = row || open.find((x) => x.id === f.requisition_id);   // where, type and department come from the requisition
  const errs = collect({
    requisition_id: v.required(f.requisition_id, "Requisition"),
    title: v.required(f.title, "Public job title") || v.maxLen(f.title, 200, "Public job title"),
    description: v.required(f.description, "Job description")
      || (String(f.description).trim().length < 50 ? "Write at least 50 characters of job description so the public text is meaningful" : null)
      || v.maxLen(f.description, 6000, "Job description"),
    requirements: v.maxLen(f.requirements, 4000, "Requirements"),
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const save = useMutation({
    mutationFn: () => (row ? hiringApi.updatePosting(row.id, { title: f.title, description: f.description, requirements: f.requirements })
      : hiringApi.createPosting({ requisition_id: f.requisition_id, title: f.title, description: f.description, requirements: f.requirements })),
    onSuccess: () => { toast.success("Posting saved. Publish it from the list when it is ready."); qc.invalidateQueries({ queryKey: ["hire"] }); onClose(); },
    onError: (e) => toast.error(err(e)),
  });
  const trySave = () => { setShown(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  return (
    <Modal title={row ? "Edit posting" : "Write a posting"} subtitle="This text is what people outside the company read." onClose={onClose}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={trySave}>Save</button></>}>
      <FormSection title="Job" hint="Location, employment type and department are taken from the requisition and shown with the job.">
        <FormGrid cols={2}>
          <Field label={<>Requisition<Req /></>}><Select disabled={!!row} value={f.requisition_id} onChange={(e) => { const r = open.find((x) => x.id === e.target.value); setF((p) => ({ ...p, requisition_id: e.target.value, title: p.title || r?.designation || "" })); }}>
            {row && <option value={row.requisition_id}>{row.req_number}</option>}
            {!row && open.map((r) => <option key={r.id} value={r.id}>{r.req_number} · {r.designation}</option>)}</Select><FieldError msg={er("requisition_id")} /></Field>
          <Field label={<>Public job title<Req /></>}><div className="flex gap-2"><Input maxLength={200} value={f.title || ""} onChange={(e) => set("title", e.target.value)} />
            <button type="button" className="btn-secondary !py-1 !px-2 text-xs whitespace-nowrap" onClick={() => setAssist(true)}>Draft the text</button></div><FieldError msg={er("title")} /></Field>
        </FormGrid>
        {req && <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
          {[["Location", req.location], ["Employment type", EMP_TYPES[req.employment_type] || req.employment_type], ["Department", req.department]].map(([k, val]) => (
            <div key={k} className="rounded-lg bg-surface border border-surface-border px-2.5 py-1.5"><span className="text-gray-400">{k}: </span><span className="text-gray-700">{val || "not set on the requisition"}</span></div>))}
        </div>}
      </FormSection>
      <FormSection title="Text">
        <FormGrid cols={2}>
          <Field label={<>Job description<Req /></>} hint="What the person will do. Plain text; blank lines make paragraphs. At least 50 characters."><Textarea rows={12} maxLength={6000} value={f.description || ""} onChange={(e) => set("description", e.target.value)} /><FieldError msg={er("description")} /></Field>
          <Field label="Requirements" hint="Experience (years), skills and minimum qualification."><Textarea rows={12} maxLength={4000} value={f.requirements || ""} onChange={(e) => set("requirements", e.target.value)} /><FieldError msg={er("requirements")} /></Field>
        </FormGrid>
      </FormSection>
      {assist && <JdAssistModal requisitionId={f.requisition_id} designation={f.title} onClose={() => setAssist(false)}
        onUse={(d) => setF((p) => ({ ...p, description: d.description, requirements: d.requirements }))} />}
    </Modal>
  );
}
