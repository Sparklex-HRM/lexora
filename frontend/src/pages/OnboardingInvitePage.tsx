import { useEffect, useRef, useState, ReactNode } from "react";
import { useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast, { Toaster } from "react-hot-toast";
import {
  User, Landmark, HeartPulse, FileText, ShieldCheck, BadgeCheck, CheckCircle2, AlertTriangle,
  ArrowLeft, ArrowRight, Loader2, Upload, Trash2, Send, LucideIcon,
} from "lucide-react";
import Logo from "../components/Logo";
import { inviteApi, InvitePage, errText } from "../api/identity";

/* The new hire's own page. No login — the secret link is the key.
   One column, big touch targets: it is filled on a phone more often than not. */

const STEP_META: Record<string, { label: string; icon: LucideIcon }> = {
  personal: { label: "About you", icon: User },
  statutory: { label: "SIN and TD1", icon: BadgeCheck },
  bank: { label: "Bank account", icon: Landmark },
  emergency: { label: "Emergency contact", icon: HeartPulse },
  documents: { label: "Documents", icon: FileText },
  consents: { label: "Agreements", icon: ShieldCheck },
};

const LABEL = "block text-xs font-semibold text-gray-500 mb-1";

function F({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className={LABEL}>{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-gray-400 mt-1">{hint}</span>}
    </label>
  );
}

function Frame({ company, children }: { company?: string | null; children: ReactNode }) {
  return (
    <div className="min-h-screen bg-gradient-to-b from-primary-50 to-white">
      <Toaster position="top-center" toastOptions={{ style: { fontSize: 14 } }} />
      <header className="bg-white border-b border-surface-border">
        <div className="max-w-2xl mx-auto px-4 py-3 flex items-center justify-between gap-3">
          <Logo className="h-7 w-auto" />
          {company && <span className="text-sm font-semibold text-gray-700 truncate">{company}</span>}
        </div>
      </header>
      <main className="max-w-2xl mx-auto px-4 py-5 pb-28">{children}</main>
    </div>
  );
}

function Banner({ tone, title, children }: { tone: "ok" | "bad" | "info"; title: string; children?: ReactNode }) {
  const cls = tone === "ok" ? "bg-green-50 border-green-200 text-green-800"
    : tone === "bad" ? "bg-amber-50 border-amber-200 text-amber-800" : "bg-primary-50 border-primary-100 text-primary-800";
  const Icon = tone === "ok" ? CheckCircle2 : AlertTriangle;
  return (
    <div className={`rounded-xl border p-4 ${cls}`}>
      <div className="flex items-center gap-2 font-semibold"><Icon className="w-4 h-4 shrink-0" />{title}</div>
      {children && <div className="text-sm mt-1.5">{children}</div>}
    </div>
  );
}

export default function OnboardingInvitePage() {
  const { token = "" } = useParams();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["invite", token], queryFn: () => inviteApi.get(token), retry: false, staleTime: 0 });
  const [idx, setIdx] = useState(0);
  const [form, setForm] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const seeded = useRef(false);

  useEffect(() => {
    if (data && !seeded.current) {
      seeded.current = true;
      setForm({ ...data.data });
      const first = data.steps.findIndex((s) => !data.steps_completed.includes(s));
      setIdx(first < 0 ? 0 : first);
    }
  }, [data]);

  if (isLoading) return <Frame><div className="grid place-items-center py-24"><Loader2 className="w-7 h-7 animate-spin text-primary-500" /></div></Frame>;
  if (error || !data) return (
    <Frame><Banner tone="bad" title="This link cannot be opened">{errText(error, "Ask your HR team for a new link.")}</Banner></Frame>
  );
  if (data.locked) return (
    <Frame company={data.company}>
      <Banner tone="ok" title={`Thank you, ${data.first_name || ""}. Your details are with HR.`}>
        Nothing more is needed from you now. HR will contact you if something has to be corrected.
        After approval you will get an e-mail to set your password.
      </Banner>
    </Frame>
  );

  const steps = data.steps;
  const step = steps[idx];
  const set = (k: string, v: any) => setForm((p) => ({ ...p, [k]: v }));
  const refresh = () => qc.invalidateQueries({ queryKey: ["invite", token] });
  const val = (k: string) => form[k] ?? "";
  const text = (k: string, props: Record<string, any> = {}) =>
    <input className="input py-2.5" value={val(k)} onChange={(e) => set(k, e.target.value)} {...props} />;
  /* An ID / bank number already saved is never sent back to the browser — only its mask. */
  const secret = (k: string, props: Record<string, any> = {}) =>
    <input {...props} className="input py-2.5" value={val(k)} onChange={(e) => set(k, e.target.value)} autoComplete="off"
      placeholder={form[`has_${k}`] ? `Saved: ${form[`${k}_masked`]} — type to change` : props.placeholder} />;

  const FIELDS: Record<string, string[]> = {
    personal: ["middle_name", "preferred_name", "date_of_birth", "gender", "marital_status",
      "phone", "email", "address_line1", "address_line2", "city", "postal_code", "province_of_residence"],
    statutory: ["sin", "work_eligibility", "work_permit_number", "work_permit_expiry", "td1_federal_claim", "td1_provincial_claim"],
    bank: ["bank_institution", "bank_transit", "bank_account"],
    emergency: ["emergency_name", "emergency_relationship", "emergency_phone"],
    documents: [], consents: [],
  };

  const saveStep = async (): Promise<boolean> => {
    const payload: Record<string, any> = {};
    for (const k of FIELDS[step] || []) if (form[k] !== undefined) payload[k] = form[k] === "" ? null : form[k];
    setBusy(true);
    try {
      const r = await inviteApi.saveStep(token, step, payload);
      setForm((p) => ({ ...p, ...(r.data || {}), ...Object.fromEntries(["sin", "bank_account"]
        .filter((k) => (FIELDS[step] || []).includes(k)).map((k) => [k, ""])) }));
      await refresh();
      return true;
    } catch (e) { toast.error(errText(e, "Could not save. Check the fields and try again.")); return false; }
    finally { setBusy(false); }
  };
  const next = async () => { if (await saveStep()) { if (idx < steps.length - 1) setIdx(idx + 1); else setReviewing(true); window.scrollTo(0, 0); } };
  const submit = async () => {
    setBusy(true);
    try { await inviteApi.submit(token); toast.success("Sent to HR"); await refresh(); }
    catch (e) { toast.error(errText(e, "Could not submit.")); }
    finally { setBusy(false); }
  };

  return (
    <Frame company={data.company}>
      <h1 className="font-display text-xl font-bold text-gray-800">Welcome, {data.first_name}</h1>
      <p className="text-sm text-gray-500 mt-0.5">
        {data.job_title ? `${data.job_title} · ` : ""}Please fill in your joining details. You can stop and come back with the same link.
      </p>
      {data.review_note && <div className="mt-3"><Banner tone="bad" title="HR asked you to correct something">{data.review_note}</Banner></div>}

      {/* Step chips */}
      <div className="flex gap-1.5 overflow-x-auto mt-4 pb-1 -mx-1 px-1">
        {steps.map((s, i) => {
          const done = data.steps_completed.includes(s); const M = STEP_META[s];
          return (
            <button key={s} onClick={() => { setReviewing(false); setIdx(i); }}
              className={`flex items-center gap-1.5 whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-medium border ${
                !reviewing && i === idx ? "bg-primary-600 text-white border-primary-600" : "bg-white text-gray-600 border-surface-border"}`}>
              {done ? <CheckCircle2 className="w-3.5 h-3.5 text-teal-400" /> : <M.icon className="w-3.5 h-3.5" />}{M.label}
            </button>
          );
        })}
      </div>

      <div className="card mt-3">
        {reviewing ? (
          <div className="space-y-3">
            <h2 className="font-semibold text-gray-800">Check and send</h2>
            {data.missing.length > 0
              ? <Banner tone="bad" title="Still needed">{data.missing.join(", ")}</Banner>
              : <Banner tone="ok" title="All the needed details are filled in" />}
            <ul className="text-sm text-gray-700 space-y-1">
              <li>Documents uploaded: <b>{data.documents.length}</b></li>
              <li>Agreements ticked: <b>{data.consents_given.length}</b> of {data.consent_types.length}</li>
            </ul>
            <p className="text-xs text-gray-500">After you send, you cannot change the form unless HR sends it back to you.</p>
          </div>
        ) : (
          <div className="space-y-4">
            <h2 className="font-semibold text-gray-800 flex items-center gap-2">
              {(() => { const I = STEP_META[step].icon; return <I className="w-4 h-4 text-primary-600" />; })()}{STEP_META[step].label}
            </h2>

            {step === "personal" && <>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <F label="Date of birth">{text("date_of_birth", { type: "date" })}</F>
                <F label="Gender"><select className="input py-2.5" value={val("gender")} onChange={(e) => set("gender", e.target.value)}>
                  <option value="">—</option><option>Female</option><option>Male</option><option>Non-binary</option><option>Prefer not to say</option></select></F>
                <F label="Middle name">{text("middle_name")}</F>
                <F label="Name you go by">{text("preferred_name")}</F>
                <F label="Marital status"><select className="input py-2.5" value={val("marital_status")} onChange={(e) => set("marital_status", e.target.value)}>
                  <option value="">—</option><option value="single">Single</option><option value="married">Married</option>
                  <option value="divorced">Divorced</option><option value="widowed">Widowed</option></select></F>
                <F label="Mobile number">{text("phone", { inputMode: "tel", placeholder: "(416) 555-0123" })}</F>
                <F label="Personal e-mail">{text("email", { type: "email" })}</F>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="sm:col-span-2"><F label="Address">{text("address_line1", { placeholder: "House / street" })}</F></div>
                <div className="sm:col-span-2"><F label="Address line 2">{text("address_line2", { placeholder: "Unit / suite" })}</F></div>
                <F label="City">{text("city")}</F>
                <F label="Postal code">{text("postal_code", { placeholder: "M5V 2T6", maxLength: 7 })}</F>
              </div>
            </>}

            {step === "statutory" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <F label="SIN" hint="9 digits. Stored encrypted; only the last 3 digits are shown.">{secret("sin", { inputMode: "numeric", maxLength: 11 })}</F>
                <F label="Work eligibility"><select className="input py-2.5" value={val("work_eligibility")} onChange={(e) => set("work_eligibility", e.target.value)}>
                  <option value="">—</option><option value="citizen">Canadian citizen</option><option value="permanent_resident">Permanent resident</option>
                  <option value="work_permit">Work permit</option><option value="study_permit">Study permit</option></select></F>
                {["work_permit", "study_permit"].includes(val("work_eligibility")) && <>
                  <F label="Permit number">{text("work_permit_number")}</F>
                  <F label="Permit expiry">{text("work_permit_expiry", { type: "date" })}</F>
                </>}
                <F label="TD1 federal claim amount" hint="The total from your federal TD1 form.">
                  <input className="input py-2.5" type="number" inputMode="decimal" value={val("td1_federal_claim")} onChange={(e) => set("td1_federal_claim", e.target.value === "" ? "" : Number(e.target.value))} /></F>
                <F label="TD1 provincial claim amount" hint="The total from your provincial TD1 form.">
                  <input className="input py-2.5" type="number" inputMode="decimal" value={val("td1_provincial_claim")} onChange={(e) => set("td1_provincial_claim", e.target.value === "" ? "" : Number(e.target.value))} /></F>
              </div>
            )}

            {step === "bank" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <F label="Institution number" hint="3 digits.">{text("bank_institution", { inputMode: "numeric", maxLength: 3 })}</F>
                <F label="Transit number" hint="5 digits.">{text("bank_transit", { inputMode: "numeric", maxLength: 5 })}</F>
                <F label="Account number" hint="Stored encrypted; only the last 4 digits are shown.">{secret("bank_account", { inputMode: "numeric", maxLength: 20 })}</F>
                <p className="sm:col-span-2 text-xs text-gray-500">Your salary is paid to this account. Upload a void cheque or a direct-deposit form from your bank under Documents.</p>
              </div>
            )}

            {step === "emergency" && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <F label="Contact name">{text("emergency_name")}</F>
                <F label="Relationship">{text("emergency_relationship", { placeholder: "Father, spouse, friend…" })}</F>
                <F label="Contact phone">{text("emergency_phone", { inputMode: "tel" })}</F>
              </div>
            )}

            {step === "documents" && <Documents token={token} page={data} onChange={refresh} />}
            {step === "consents" && <Consents token={token} page={data} onChange={refresh} />}
          </div>
        )}
      </div>

      {/* Sticky action bar — always reachable with a thumb */}
      <div className="fixed bottom-0 inset-x-0 bg-white border-t border-surface-border">
        <div className="max-w-2xl mx-auto px-4 py-3 flex items-center gap-2">
          <button className="btn-secondary flex items-center gap-1.5 py-2.5" disabled={busy || (!reviewing && idx === 0)}
            onClick={() => { if (reviewing) setReviewing(false); else setIdx(Math.max(0, idx - 1)); window.scrollTo(0, 0); }}>
            <ArrowLeft className="w-4 h-4" /> Back</button>
          {reviewing
            ? <button className="btn-primary flex-1 flex items-center justify-center gap-1.5 py-2.5 disabled:opacity-60" disabled={busy || data.missing.length > 0} onClick={submit}>
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />} Send to HR</button>
            : <button className="btn-primary flex-1 flex items-center justify-center gap-1.5 py-2.5 disabled:opacity-60" disabled={busy} onClick={next}>
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ArrowRight className="w-4 h-4" />}
                {idx === steps.length - 1 ? "Save and review" : "Save and continue"}</button>}
        </div>
      </div>
    </Frame>
  );
}

function Documents({ token, page, onChange }: { token: string; page: InvitePage; onChange: () => void }) {
  const [docType, setDocType] = useState(page.doc_types[0]?.value || "other");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const label = (v: string) => page.doc_types.find((d) => d.value === v)?.label || v;
  const pick = async (file?: File) => {
    if (!file) return;
    if (file.size > page.doc_rules.max_mb * 1024 * 1024) return toast.error(`The file is too large. The limit is ${page.doc_rules.max_mb} MB.`);
    setBusy(true);
    try { await inviteApi.upload(token, file, docType); toast.success("Uploaded"); onChange(); }
    catch (e) { toast.error(errText(e, "Could not upload the file.")); }
    finally { setBusy(false); if (input.current) input.current.value = ""; }
  };
  const remove = async (id: string) => {
    try { await inviteApi.removeDoc(token, id); onChange(); } catch (e) { toast.error(errText(e, "Could not remove the file.")); }
  };
  return (
    <div className="space-y-3">
      <p className="text-xs text-gray-500">{page.doc_rules.allowed} only, up to {page.doc_rules.max_mb} MB each. You can take a photo with your phone.</p>
      <div className="flex flex-col sm:flex-row gap-2">
        <select className="input py-2.5 sm:flex-1" value={docType} onChange={(e) => setDocType(e.target.value)}>
          {page.doc_types.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
        </select>
        <input ref={input} type="file" className="hidden" accept="image/jpeg,image/png,application/pdf" onChange={(e) => pick(e.target.files?.[0])} />
        <button className="btn-primary flex items-center justify-center gap-1.5 py-2.5 disabled:opacity-60" disabled={busy} onClick={() => input.current?.click()}>
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />} Choose file</button>
      </div>
      {page.documents.length === 0 ? <p className="text-sm text-gray-400 py-3 text-center">No files uploaded yet.</p> : (
        <ul className="divide-y divide-surface-border border border-surface-border rounded-lg">
          {page.documents.map((d) => (
            <li key={d.id} className="flex items-center justify-between gap-3 px-3 py-2.5">
              <div className="min-w-0">
                <div className="text-sm font-medium text-gray-800 truncate">{label(d.doc_type)}</div>
                <div className="text-xs text-gray-400 truncate">{d.file_name} · {Math.max(1, Math.round(d.size_bytes / 1024))} KB</div>
              </div>
              <button className="p-2 text-gray-400 hover:text-red-500" title="Remove" onClick={() => remove(d.id)}><Trash2 className="w-4 h-4" /></button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Consents({ token, page, onChange }: { token: string; page: InvitePage; onChange: () => void }) {
  const [busy, setBusy] = useState("");
  const name = `${page.first_name || ""} ${page.last_name || ""}`.trim();
  const tick = async (key: string) => {
    setBusy(key);
    try { await inviteApi.consent(token, key, name, page.notice_version); onChange(); }
    catch (e) { toast.error(errText(e, "Could not save.")); }
    finally { setBusy(""); }
  };
  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-500">Read each line and tick it. Lines marked * are needed to join. Each tick is recorded with the date and time.</p>
      {page.consent_types.map((c) => {
        const given = page.consents_given.includes(c.key);
        return (
          <label key={c.key} className={`flex items-start gap-3 p-3 rounded-lg border cursor-pointer ${given ? "border-teal-200 bg-teal-50/50" : "border-surface-border"}`}>
            <input type="checkbox" className="mt-1 w-4 h-4" checked={given} disabled={given || busy === c.key} onChange={() => tick(c.key)} />
            <span className="text-sm text-gray-700"><b>{c.label}{c.required ? " *" : ""}</b><br /><span className="text-gray-500">{c.text}</span></span>
          </label>
        );
      })}
    </div>
  );
}
