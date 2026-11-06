import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Gift, MapPin, Share2 } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader } from "../../components/ui";
import { money } from "../../api/endpoints";
import { EMP_TYPES, err, fmtDate, hiringApi, Row } from "./api";
import { Col, Field, FirstStep, Grid, Input, Modal, Note, Pill, Textarea } from "./ui";
import { useUrlTab } from "../../search/useUrlTab";
import { FormGrid, FormSection } from "../../components/Modal";
import { v, collect, firstError, FieldError, Req, normMobile } from "../../lib/validators";

const BONUS: Record<string, string> = { none: "No bonus on this job", waiting_join: "Paid after the person joins", waiting_days: "Joined: waiting for the qualifying days",
  due: "Due: with payroll", exported: "Sent to payroll", paid: "Paid", void: "Not payable" };

/** ESS: refer a friend for an open job, follow the referral through the stages. */
export default function ReferralsPage() {
  const qc = useQueryClient();
  const jobs = useQuery({ queryKey: ["hire", "ref-jobs"], queryFn: hiringApi.referralJobs });
  const mine = useQuery({ queryKey: ["hire", "ref-mine"], queryFn: hiringApi.myReferrals });
  const [tab, setTab] = useUrlTab<"jobs" | "mine">("jobs");   // ?tab= (global search deep link)
  const [refer, setRefer] = useState<Row | null>(null);
  const linked = jobs.data?.linked !== false;
  const items = mine.data?.items || [];
  const cols: Col<Row>[] = [
    { k: "first_name", label: "Person", render: (r) => `${r.first_name} ${r.last_name || ""}`.trim(), csv: (r) => `${r.first_name} ${r.last_name || ""}`.trim() },
    { k: "designation", label: "Job" },
    { k: "created_at", label: "Referred on", render: (r) => fmtDate(r.created_at) },
    { k: "status", label: "Referral", render: (r) => <Pill s={r.status === "hired" ? "joined" : r.status === "duplicate" ? "rejected" : r.status === "closed" ? "withdrawn" : "screening"} label={r.status === "duplicate" ? "Already applied" : r.status.replace(/_/g, " ")} />, csv: (r) => r.status },
    { k: "stage_label", label: "Stage", render: (r) => r.stage ? <Pill s={r.stage} label={r.stage_label} /> : "" , csv: (r) => r.stage_label || "" },
    { k: "bonus_amount", label: "Bonus", align: "right", render: (r) => (r.bonus_amount ? money(r.bonus_amount) : ""), csv: (r) => r.bonus_amount || "" },
    { k: "bonus_state", label: "Bonus status", render: (r) => BONUS[r.bonus_state] || r.bonus_state, csv: (r) => BONUS[r.bonus_state] || r.bonus_state },
    { k: "bonus_due_on", label: "Payable from", render: (r) => fmtDate(r.bonus_due_on) },
    { k: "status_note", label: "Note" },
  ];
  const copyLink = () => { const u = `${jobs.data?.careers_url}${jobs.data?.share_hint || ""}`; navigator.clipboard?.writeText(u); toast.success("Careers link copied. Anyone who applies through it is tracked as a referral."); };
  return (
    <div>
      <PageHeader title="Refer a Friend" icon={Gift} subtitle="Open jobs you can refer people for, and how your referrals are doing" />
      <div className="flex gap-1 mb-4">{(["jobs", "mine"] as const).map((t) => <button key={t} onClick={() => setTab(t)} className={`px-3.5 py-2 rounded-lg text-sm font-medium ${tab === t ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>{t === "jobs" ? "Open jobs" : `My referrals (${items.length})`}</button>)}</div>
      {!linked && <Note tone="warn">Your login is not linked to an employee record, so referrals cannot be recorded under your name. Ask HR to link it.</Note>}
      {tab === "jobs" && (jobs.isLoading ? <div className="text-sm text-gray-400">Loading…</div> : !(jobs.data?.jobs || []).length
        ? <FirstStep title="No open jobs right now">When HR publishes a job, it appears here with its referral bonus.</FirstStep> : (
          <div>
            {jobs.data?.careers_url && <Note>{jobs.data.note} <button className="underline inline-flex items-center gap-1 ml-1" onClick={copyLink}><Copy className="w-3 h-3" />Copy the careers link to share</button></Note>}
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {(jobs.data!.jobs as Row[]).map((j) => (
                <div key={j.posting_id} className="card flex flex-col">
                  <div className="font-semibold text-gray-800">{j.title}</div>
                  <div className="text-xs text-gray-500 mt-1 flex flex-wrap gap-x-3">{j.department && <span>{j.department}</span>}{j.location && <span className="inline-flex items-center gap-1"><MapPin className="w-3 h-3" />{j.location}</span>}<span>{EMP_TYPES[j.employment_type] || ""}</span></div>
                  <p className="text-xs text-gray-600 mt-2 line-clamp-4 whitespace-pre-line">{j.description}</p>
                  <div className="mt-auto pt-3 flex items-center justify-between gap-2">
                    <span className="text-xs">{j.bonus?.active && j.bonus.bonus_amount > 0 ? <span className="badge-teal">Bonus {money(j.bonus.bonus_amount)} after {j.bonus.payable_after_days} days</span> : <span className="text-gray-400">No referral bonus</span>}</span>
                    <button className="btn-primary !py-1 !px-2.5 text-xs inline-flex items-center gap-1 disabled:opacity-50" disabled={!linked} onClick={() => setRefer(j)}><Share2 className="w-3.5 h-3.5" />Refer someone</button>
                  </div>
                </div>))}
            </div>
          </div>))}
      {tab === "mine" && (!mine.isLoading && !items.length ? <FirstStep title="You have not referred anyone yet">Pick an open job and press Refer someone. You will see each referral move through the stages here.</FirstStep>
        : <Grid rows={items} cols={cols} keyOf={(r) => r.id} loading={mine.isLoading} exportName="my-referrals.csv" title="My referrals" />)}
      {refer && <ReferModal job={refer} onClose={() => setRefer(null)} onDone={() => { qc.invalidateQueries({ queryKey: ["hire"] }); setRefer(null); setTab("mine"); }} />}
    </div>
  );
}

const RESUME_TYPES = /\.(pdf|docx|jpe?g|png)$/i;

function ReferModal({ job, onClose, onDone }: { job: Row; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState<Row>({});
  const [file, setFile] = useState<File | null>(null);
  const [shown, setShown] = useState(false);
  const set = (k: string, val: string) => setF((p) => ({ ...p, [k]: val }));
  const errs = collect({
    first_name: v.required(f.first_name, "First name") || v.maxLen(f.first_name, 100, "First name"),
    last_name: v.maxLen(f.last_name, 100, "Last name"),
    email: (!f.email && !f.phone ? "Give an e-mail or a phone number so HR can reach the person" : null) || v.email(f.email),
    phone: v.mobile(f.phone),
    link_url: f.link_url && !/^https?:\/\/\S+\.\S+/i.test(String(f.link_url).trim()) ? "The profile link must start with http:// or https://" : null,
    current_employer: v.maxLen(f.current_employer, 200, "Current employer"),
    city: v.maxLen(f.city, 100, "City"),
    resume: file && !RESUME_TYPES.test(file.name) ? "The résumé must be a PDF, DOCX, JPG or PNG file" : null,
    note: v.maxLen(f.note, 1000, "Why this person"),
    consent: f.consent ? null : "Confirm that the person agreed to be contacted about this job",
  });
  const er = (k: string) => (shown ? errs[k] : undefined);
  const save = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append("requisition_id", job.requisition_id); ["first_name", "last_name", "email", "link_url", "note", "current_employer", "city"].forEach((k) => fd.append(k, (f[k] || "").trim())); fd.append("phone", f.phone ? normMobile(f.phone) : ""); if (file) fd.append("resume", file); return hiringApi.refer(fd); },
    onSuccess: (r) => { toast.success(r.message || "Referral recorded.", { duration: 6000 }); onDone(); },
    onError: (e) => toast.error(err(e)),
  });
  const trySave = () => { setShown(true); if (Object.keys(errs).length) return void toast.error(firstError(errs)); save.mutate(); };
  return (
    <Modal title={`Refer someone for ${job.title}`} subtitle="HR sees who referred whom. The person is told nothing automatically until HR records their consent." onClose={onClose} size="lg"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={trySave}>Send referral</button></>}>
      <FormSection title="The person">
        <FormGrid cols={2}>
          <Field label={<>First name<Req /></>}><Input maxLength={100} value={f.first_name || ""} onChange={(e) => set("first_name", e.target.value)} /><FieldError msg={er("first_name")} /></Field>
          <Field label="Last name"><Input maxLength={100} value={f.last_name || ""} onChange={(e) => set("last_name", e.target.value)} /><FieldError msg={er("last_name")} /></Field>
          <Field label="E-mail" hint="E-mail or phone is required."><Input type="email" value={f.email || ""} onChange={(e) => set("email", e.target.value)} /><FieldError msg={er("email")} /></Field>
          <Field label="Mobile" hint="10-digit Canadian phone number, no +1 or spaces."><Input inputMode="numeric" value={f.phone || ""} onChange={(e) => set("phone", e.target.value)} /><FieldError msg={er("phone")} /></Field>
        </FormGrid>
      </FormSection>
      <FormSection title="Quick screening" hint="Helps HR decide whom to call first. All optional.">
        <FormGrid cols={2}>
          <Field label="Current employer"><Input maxLength={200} value={f.current_employer || ""} onChange={(e) => set("current_employer", e.target.value)} /><FieldError msg={er("current_employer")} /></Field>
          <Field label="City"><Input maxLength={100} value={f.city || ""} onChange={(e) => set("city", e.target.value)} /><FieldError msg={er("city")} /></Field>
          <Field label="Profile link" hint="LinkedIn or a portfolio."><Input value={f.link_url || ""} onChange={(e) => set("link_url", e.target.value)} placeholder="https://" /><FieldError msg={er("link_url")} /></Field>
          <Field label="Resume" hint="PDF, DOCX, JPG or PNG."><input type="file" accept=".pdf,.docx,.jpg,.jpeg,.png" className="text-xs" onChange={(e) => setFile(e.target.files?.[0] || null)} /><FieldError msg={er("resume")} /></Field>
          <div className="sm:col-span-2"><Field label="Why this person?" hint="How you know them, experience, notice period: anything that helps HR. Up to 1000 characters."><Textarea rows={3} maxLength={1000} value={f.note || ""} onChange={(e) => set("note", e.target.value)} /><FieldError msg={er("note")} /></Field></div>
        </FormGrid>
      </FormSection>
      <label className="flex items-start gap-2 text-sm text-gray-700">
        <input type="checkbox" className="mt-1" checked={!!f.consent} onChange={(e) => setF((p) => ({ ...p, consent: e.target.checked }))} />
        <span>The person agreed that I share their details with HR for this job.<Req /></span>
      </label>
      <FieldError msg={er("consent")} />
    </Modal>
  );
}
