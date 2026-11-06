import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, Briefcase, ClipboardCheck, ClipboardList, FileSignature, Gift, Megaphone, MessageSquareText, Settings, Users } from "lucide-react";
import toast from "react-hot-toast";
import { PageHeader } from "../../components/ui";
import { HR_WRITE, useAuthStore } from "../../store/authStore";
import { err, hiringApi, Row, SOURCES } from "./api";
import { Col, Field, FirstStep, Grid, Input, Note, Pill, Select, Stat, Tabs, Textarea } from "./ui";
import Requisitions from "./Requisitions";
import Postings from "./Postings";
import Candidates from "./Candidates";
import Offers from "./Offers";
import { CalendarSettings, HrReferrals, Messages, Scorecards } from "./D2Tabs";
import { can } from "../../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

const TABS = [
  { key: "requisitions", label: "Requisitions", icon: ClipboardList, hr: false },
  { key: "postings", label: "Job postings", icon: Megaphone, hr: true },
  { key: "candidates", label: "Candidates", icon: Users, hr: false },
  { key: "offers", label: "Offers", icon: FileSignature, hr: true },
  { key: "referrals", label: "Referrals", icon: Gift, hr: true },
  { key: "scorecards", label: "Scorecards", icon: ClipboardCheck, hr: true },
  { key: "messages", label: "Messages", icon: MessageSquareText, hr: true },
  { key: "reports", label: "Reports", icon: BarChart3, hr: true },
  { key: "settings", label: "Settings", icon: Settings, hr: true },
];

export default function HiringPage() {
  const { tab = "requisitions" } = useParams();
  const user = useAuthStore((s) => s.user);
  const isHr = can(user, ["hiring.create", "hiring.edit"], HR_WRITE);
  const canRequisition = isHr || can(user, "hiring.create", ["manager"]);
  const tabs = TABS.filter((t) => isHr || !t.hr);
  if (!tabs.some((t) => t.key === tab)) return <Navigate to="/hiring/requisitions" replace />;
  return (
    <div>
      <PageHeader title="Hiring" icon={Briefcase} subtitle={isHr ? "Requisition, posting, candidates, interviews, scorecards, offer, referrals, onboarding" : "Requisitions you raised or manage, and their candidates"} />
      <Tabs base="/hiring" tabs={tabs} />
      {tab === "requisitions" && <Requisitions canCreate={canRequisition} />}
      {tab === "postings" && <Postings />}
      {tab === "candidates" && <Candidates isHr={isHr} />}
      {tab === "offers" && <Offers />}
      {tab === "referrals" && <HrReferrals />}
      {tab === "scorecards" && <Scorecards />}
      {tab === "messages" && <Messages />}
      {tab === "reports" && <Reports />}
      {tab === "settings" && <HiringSettings />}
    </div>
  );
}

function Reports() {
  const { data, isLoading } = useQuery({ queryKey: ["hire", "reports"], queryFn: hiringApi.reports });
  if (isLoading || !data) return <div className="text-sm text-gray-400">Loading…</div>;
  if (!data.openings.length) return <FirstStep title="Nothing to report yet">Reports are built from your requisitions, applications and offers. Raise a requisition first.</FirstStep>;
  const t = data.totals, oa = data.offer_acceptance, ttf = data.time_to_fill;
  const stageCols = ["applied", "screening", "interview", "selected", "offer", "joined", "on_hold", "rejected", "withdrawn"];
  const openCols: Col<Row>[] = [
    { k: "req_number", label: "Requisition" }, { k: "designation", label: "Designation" }, { k: "department", label: "Department" },
    { k: "status", label: "Status", render: (r) => <Pill s={r.status} />, csv: (r) => r.status },
    { k: "openings", label: "Openings", align: "right" }, { k: "accepted", label: "Offers accepted", align: "right" },
    { k: "joined", label: "Joined", align: "right" }, { k: "remaining", label: "Still open", align: "right" },
    { k: "days_open", label: "Days open", align: "right", render: (r) => r.days_open ?? "" },
    ...stageCols.map((s) => ({ k: `p_${s}`, label: s.replace(/_/g, " "), align: "right" as const, render: (r: Row) => r.pipeline[s] || "", csv: (r: Row) => r.pipeline[s] || 0, sortVal: (r: Row) => r.pipeline[s] || 0 })),
  ];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Stat label="Openings" value={t.openings} sub={`${t.joined} joined`} accent />
        <Stat label="Offers accepted" value={t.accepted} sub={`${oa.pending} waiting for an answer`} />
        <Stat label="Offer acceptance" value={oa.rate_pct == null ? "No answers yet" : `${oa.rate_pct}%`} sub={`${oa.accepted} accepted, ${oa.declined} declined, ${oa.expired} expired`} />
        <Stat label="Time to fill" value={ttf.avg_days == null ? "None filled yet" : `${ttf.avg_days} days`} sub={`${ttf.filled_requisitions} filled requisition(s), opened to filled`} />
        <Stat label="Applications" value={t.applications} />
      </div>
      <Grid rows={data.openings} cols={openCols} keyOf={(r) => r.requisition_id} title="Openings vs filled, and pipeline by requisition" exportName="hiring-openings-pipeline.csv" />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Grid rows={data.time_in_stage} keyOf={(r) => r.stage} title="Time in stage" exportName="hiring-time-in-stage.csv" empty="No stage moves yet."
          cols={[{ k: "label", label: "Stage" }, { k: "avg_days", label: "Average days", align: "right", render: (r) => r.avg_days ?? "" }, { k: "moves", label: "Stays counted", align: "right" }]} />
        <Grid rows={data.source_mix} keyOf={(r) => r.source} title="Source mix" exportName="hiring-source-mix.csv" empty="No applications yet."
          cols={[{ k: "source", label: "Source", render: (r) => SOURCES[r.source] || r.source, csv: (r) => SOURCES[r.source] || r.source }, { k: "applications", label: "Applications", align: "right" },
            { k: "share_pct", label: "Share %", align: "right" }, { k: "joined", label: "Joined", align: "right" }]} />
      </div>
      <p className="text-[11px] text-gray-400">Offer acceptance = accepted / (accepted + declined + expired). Time to fill = days from Open to Filled. Time in stage = average days between stage moves.</p>
    </div>
  );
}

const D2_KEYS = ["careers_tagline", "careers_about", "careers_primary_color", "careers_whatsapp_text", "scorecard_hide_names", "interview_slot_link_days",
  "interview_reminder_hours", "referral_default_bonus", "referral_default_days", "offer_approval_chain",
  "ai_assist", "linkedin_company_id", "linkedin_poster_email", "naukri_partner_id"];   // G6

function HiringSettings() {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["hire", "settings"], queryFn: hiringApi.settings });
  const [f, setF] = useState<Row | null>(null);
  useEffect(() => { if (data && !f) setF(data); }, [data, f]);
  const save = useMutation({
    mutationFn: () => hiringApi.saveSettings({ requisition_approval: f!.requisition_approval, offer_second_approver_above_budget: f!.offer_second_approver_above_budget,
      required_interview_rounds: f!.required_interview_rounds, offer_valid_days: f!.offer_valid_days, careers_page_enabled: f!.careers_page_enabled,
      privacy_text: f!.privacy_text, resume_max_mb: f!.resume_max_mb, ...Object.fromEntries(D2_KEYS.map((k) => [k, f![k]])) }),
    onSuccess: () => { toast.success("Hiring settings saved."); qc.invalidateQueries({ queryKey: ["hire"] }); },
    onError: (e) => toast.error(err(e)),
  });
  if (!f) return <div className="text-sm text-gray-400">Loading…</div>;
  const ro = !f.can_edit;
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  const careersUrl = f.org_code ? `${window.location.origin}/careers/${f.org_code}` : "";
  return (
    <div className="max-w-6xl">
      <div className="card">
        {ro && <Note>Only an HR head or an org admin can change these settings.</Note>}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Requisition approval"><Select disabled={ro} value={f.requisition_approval} onChange={(e) => set("requisition_approval", e.target.value)}>
            <option value="manager_then_hr">Hiring manager, then HR head</option><option value="hr_only">HR head only</option><option value="none">No approval: opens on submit</option></Select></Field>
          <Field label="Interview rounds needed before Selected" hint="A round counts when every panel member has given feedback."><Input type="number" min={0} max={10} disabled={ro} value={f.required_interview_rounds} onChange={(e) => set("required_interview_rounds", e.target.value)} /></Field>
          <Field label="Offer link valid for (days)"><Input type="number" min={1} max={90} disabled={ro} value={f.offer_valid_days} onChange={(e) => set("offer_valid_days", e.target.value)} /></Field>
          <Field label="Offer above the requisition budget"><Select disabled={ro} value={f.offer_second_approver_above_budget ? "1" : "0"} onChange={(e) => set("offer_second_approver_above_budget", e.target.value === "1")}>
            <option value="1">Needs a second, different approver</option><option value="0">One approver is enough</option></Select></Field>
          <Field label="Offer approval chain" hint="Hiring manager, then HR admin (or the chain set in Approvals for 'hiring_offer')."><Select disabled={ro} value={f.offer_approval_chain ? "1" : "0"} onChange={(e) => set("offer_approval_chain", e.target.value === "1")}><option value="1">On: submit offers to the chain</option><option value="0">Off: HR head approves directly</option></Select></Field>
          <Field label="Resume size limit (MB)"><Input type="number" min={1} max={10} disabled={ro} value={f.resume_max_mb} onChange={(e) => set("resume_max_mb", e.target.value)} /></Field>
        </div>
      </div>

      <div className="card mt-4">
        <div className="font-display font-semibold text-gray-800 mb-3">Public careers page</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Careers page" hint={careersUrl || "Your organisation has no login code, so the careers page has no address yet."}>
            <Select disabled={ro} value={f.careers_page_enabled ? "1" : "0"} onChange={(e) => set("careers_page_enabled", e.target.value === "1")}><option value="0">Off</option><option value="1">On</option></Select></Field>
          <Field label="Tagline" hint="One line under the company name."><Input disabled={ro} value={f.careers_tagline || ""} onChange={(e) => set("careers_tagline", e.target.value)} /></Field>
          <Field label="Accent colour" hint="Buttons and highlights. The logo comes from Company settings."><div className="flex gap-2"><input type="color" disabled={ro} value={f.careers_primary_color || "#4338ca"} onChange={(e) => set("careers_primary_color", e.target.value)} className="h-9 w-12 rounded border border-surface-border" /><Input disabled={ro} value={f.careers_primary_color || ""} onChange={(e) => set("careers_primary_color", e.target.value)} /></div></Field>
          <div className="sm:col-span-2 lg:col-span-3"><Field label="About the company" hint="Shown above the job list. Plain text; blank lines make paragraphs."><Textarea rows={3} disabled={ro} value={f.careers_about || ""} onChange={(e) => set("careers_about", e.target.value)} /></Field></div>
          <div className="sm:col-span-2 lg:col-span-3"><Field label="Consent text on the apply form (privacy notice — PIPEDA consent)" hint="The applicant must tick a box next to this text. The exact text they agreed to is saved with their record.">
            <Textarea rows={3} disabled={ro} value={f.privacy_text} onChange={(e) => set("privacy_text", e.target.value)} /></Field></div>
          <div className="sm:col-span-2 lg:col-span-3"><Field label="WhatsApp opt-in text" hint="A second, optional tick box when the applicant gives a phone number."><Textarea rows={2} disabled={ro} value={f.careers_whatsapp_text || ""} onChange={(e) => set("careers_whatsapp_text", e.target.value)} /></Field></div>
        </div>
      </div>

      <div className="card mt-4">
        <div className="font-display font-semibold text-gray-800 mb-3">Interviews, scorecards and referrals</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <Field label="Bias guard: hide candidate names from interviewers" hint="Org-wide. A scorecard can also set it per requisition."><Select disabled={ro} value={f.scorecard_hide_names ? "1" : "0"} onChange={(e) => set("scorecard_hide_names", e.target.value === "1")}><option value="0">Show names</option><option value="1">Hide names</option></Select></Field>
          <Field label="Self-schedule link valid for (days)"><Input type="number" min={1} max={30} disabled={ro} value={f.interview_slot_link_days} onChange={(e) => set("interview_slot_link_days", e.target.value)} /></Field>
          <Field label="Interview reminder (hours before)"><Input type="number" min={1} max={168} disabled={ro} value={f.interview_reminder_hours} onChange={(e) => set("interview_reminder_hours", e.target.value)} /></Field>
          <Field label="Default referral bonus (CAD)" hint="Used by jobs without a rule of their own. 0 = no bonus."><Input type="number" min={0} disabled={ro} value={f.referral_default_bonus} onChange={(e) => set("referral_default_bonus", e.target.value)} /></Field>
          <Field label="Referral bonus payable after (days of joining)"><Input type="number" min={0} max={365} disabled={ro} value={f.referral_default_days} onChange={(e) => set("referral_default_days", e.target.value)} /></Field>
        </div>
        {!f.letters_available && <div className="mt-3"><Note tone="warn">The letter engine is not installed, so offer letters cannot be issued from here.</Note></div>}
      </div>

      <div className="card mt-4">
        <div className="font-display font-semibold text-gray-800 mb-1">Job boards and hiring assist</div>
        <p className="text-xs text-gray-500 mb-3">LinkedIn and Naukri read a feed of your jobs only after THEY onboard it for your account (partner credentials). Fill these in, switch jobs on per posting (Job boards), then send the feed address to the board. Indeed and Google for Jobs need nothing here.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <Field label="LinkedIn company id" hint="The number in your LinkedIn Page admin address."><Input disabled={ro} inputMode="numeric" value={f.linkedin_company_id || ""} onChange={(e) => set("linkedin_company_id", e.target.value)} /></Field>
          <Field label="LinkedIn poster e-mail" hint="A shared recruiting mailbox. It appears in the public feed."><Input disabled={ro} type="email" value={f.linkedin_poster_email || ""} onChange={(e) => set("linkedin_poster_email", e.target.value)} /></Field>
          <Field label="Naukri client id" hint="Given by Naukri after feed onboarding. Feed field names are unverified until Naukri accepts it."><Input disabled={ro} value={f.naukri_partner_id || ""} onChange={(e) => set("naukri_partner_id", e.target.value)} /></Field>
          <Field label="Hiring assist" hint="Rules always run. On: the AI platform (when it has a key) adds skills, a summary and a second opinion, with names, contacts and money removed."><Select disabled={ro} value={f.ai_assist === false ? "0" : "1"} onChange={(e) => set("ai_assist", e.target.value === "1")}><option value="1">On: rules + AI when available</option><option value="0">Off: rules only</option></Select></Field>
        </div>
        {f.org_code && <div className="text-[11px] text-gray-500 mt-3 space-y-0.5">
          <div>LinkedIn feed: <code className="select-all">{`/api/hiring/public/careers/${f.org_code}/feed/linkedin.xml`}</code></div>
          <div>Naukri feed: <code className="select-all">{`/api/hiring/public/careers/${f.org_code}/feed/naukri.xml`}</code></div>
          <div>Applications that come through a feed or the careers page are tagged with their source (Job board: LinkedIn / Naukri / Indeed, or Careers page) on the candidate.</div>
        </div>}
      </div>
      {!ro && <div className="mt-4 flex justify-end"><button className="btn-primary disabled:opacity-50" disabled={save.isPending} onClick={() => save.mutate()}>Save settings</button></div>}
      <CalendarSettings canEdit={!ro} />
    </div>
  );
}
