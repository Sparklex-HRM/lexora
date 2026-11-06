import React, { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowLeft, Briefcase, CalendarClock, CheckCircle2, MapPin, Search } from "lucide-react";
import { money } from "../../api/endpoints";
import { EMP_TYPES, err, fmtDate, fmtDateTime, publicApi, Row } from "./api";
import { safeHref } from "../../lib/safeUrl";

/* Public pages: no login, no app menu. Built phone-first (390 px): one column, large
   tap targets, nothing wider than the screen. D2: org branding (logo + colour), filters,
   Google for Jobs JSON-LD on the job page, captcha hook, WhatsApp opt-in, source tracking,
   and the candidate's interview-slot pick page. */

type Brand = { org_name?: string; tagline?: string; about?: string; primary_color?: string; logo_url?: string | null; website?: string };

function Shell({ org, brand, children }: { org?: string; brand?: Brand; children: React.ReactNode }) {
  const color = brand?.primary_color || "#4338ca";
  return (
    <div className="min-h-screen bg-surface" style={{ ["--brand" as any]: color }}>
      <header className="bg-white border-b border-surface-border">
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center gap-3">
          {brand?.logo_url ? <img src={brand.logo_url} alt="" className="w-10 h-10 rounded-xl object-contain shrink-0" />
            : <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0" style={{ background: color + "22" }}><Briefcase className="w-5 h-5" style={{ color }} /></div>}
          <div className="min-w-0"><div className="font-display font-bold text-gray-800 truncate">{brand?.org_name || org || "Careers"}</div>
            <div className="text-xs text-gray-500 truncate">{brand?.tagline || "Careers"}</div></div>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-4 py-5">{children}</main>
      <footer className="max-w-3xl mx-auto px-4 pb-6 text-[11px] text-gray-400">{brand?.website && (safeHref(brand.website) ? <a href={safeHref(brand.website)} className="underline" target="_blank" rel="noopener noreferrer">{brand.website}</a> : <span>{brand.website}</span>)}</footer>
    </div>
  );
}
const Msg = ({ title, children }: { title: string; children?: React.ReactNode }) =>
  <div className="card text-center py-10"><h2 className="font-display font-semibold text-gray-800">{title}</h2>{children && <p className="text-sm text-gray-500 mt-1">{children}</p>}</div>;
const Para = ({ text }: { text?: string | null }) => <>{(text || "").split(/\n{2,}/).filter(Boolean).map((p, i) => <p key={i} className="text-sm text-gray-700 whitespace-pre-line mb-3">{p}</p>)}</>;
const PField = ({ label, children }: { label: string; children: React.ReactNode }) =>
  <label className="block"><span className="text-xs font-medium text-gray-600">{label}</span><div className="mt-1">{children}</div></label>;
const BrandBtn = ({ children, className = "", ...p }: React.ButtonHTMLAttributes<HTMLButtonElement>) =>
  <button {...p} className={`rounded-lg text-white font-semibold text-sm px-4 py-2.5 disabled:opacity-50 ${className}`} style={{ background: "var(--brand)" }}>{children}</button>;

/** Captcha hook: renders the provider's widget when the server has a site key; otherwise nothing (the server skips the check). */
function CaptchaWidget({ cfg, onToken }: { cfg?: Row; onToken: (t: string) => void }) {
  useEffect(() => {
    if (!cfg?.configured || !cfg.site_key) return;
    const cb = "lexoraCaptchaDone";
    (window as any)[cb] = (t: string) => onToken(t);
    const src = cfg.provider === "turnstile" ? "https://challenges.cloudflare.com/turnstile/v0/api.js" : "https://js.hcaptcha.com/1/api.js";
    if (!document.querySelector(`script[src="${src}"]`)) { const s = document.createElement("script"); s.src = src; s.async = true; document.head.appendChild(s); }
    return () => { delete (window as any)[cb]; };
  }, [cfg?.configured, cfg?.site_key, cfg?.provider, onToken]);
  if (!cfg?.configured) return null;
  if (!cfg.site_key) return <div className="text-[11px] text-amber-700">A human check ({cfg.provider}) is required on this server, but its site key is not set. Applications cannot be sent until it is.</div>;
  return <div className={cfg.provider === "turnstile" ? "cf-turnstile" : "h-captcha"} data-sitekey={cfg.site_key} data-callback="lexoraCaptchaDone" />;
}

export function CareersPage() {
  const { orgCode = "" } = useParams();
  const [sp] = useSearchParams();
  const [q, setQ] = useState("");
  const [dept, setDept] = useState("");
  const [loc, setLoc] = useState("");
  const [typ, setTyp] = useState("");
  const { data, isLoading, error } = useQuery({ queryKey: ["careers", orgCode, q, dept, loc, typ], queryFn: () => publicApi.careers(orgCode, { q: q || undefined, department: dept || undefined, location: loc || undefined, employment_type: typ || undefined }), retry: false, placeholderData: (p) => p });
  const src = sp.get("src") || "";
  const f: Row = data?.filters || {};
  return (
    <Shell org={data?.org_name} brand={data?.branding}>
      {isLoading ? <Msg title="Loading" /> : error ? <Msg title="This careers page is not available">{err(error, "Check the link and try again.")}</Msg> : (
        <div className="space-y-3">
          {data!.branding?.about && <div className="card"><Para text={data!.branding.about} /></div>}
          <h1 className="font-display text-xl font-bold text-gray-800">Open jobs <span className="text-sm font-normal text-gray-400">({data!.jobs.length} of {data!.total})</span></h1>
          <div className="card !p-2 grid grid-cols-1 sm:grid-cols-4 gap-2">
            <label className="relative sm:col-span-1"><Search className="w-4 h-4 absolute left-2 top-2.5 text-gray-400" /><input className="input !pl-8 w-full" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} /></label>
            <select className="input w-full" value={dept} onChange={(e) => setDept(e.target.value)}><option value="">All departments</option>{(f.departments || []).map((x: string) => <option key={x}>{x}</option>)}</select>
            <select className="input w-full" value={loc} onChange={(e) => setLoc(e.target.value)}><option value="">All locations</option>{(f.locations || []).map((x: string) => <option key={x}>{x}</option>)}</select>
            <select className="input w-full" value={typ} onChange={(e) => setTyp(e.target.value)}><option value="">All types</option>{(f.employment_types || []).map((x: string) => <option key={x} value={x}>{EMP_TYPES[x] || x}</option>)}</select>
          </div>
          {!data!.jobs.length ? <Msg title={data!.total ? "No job matches these filters" : "No open jobs right now"}>{data!.total ? "Clear a filter to see more." : "Please check again later."}</Msg>
            : (data!.jobs as Row[]).map((j) => (
              <Link key={j.id} to={`/careers/${orgCode}/${j.id}${src ? `?src=${encodeURIComponent(src)}` : ""}`} className="card block hover:border-primary-300 transition-colors">
                <div className="font-semibold text-gray-800">{j.title}</div>
                <div className="text-xs text-gray-500 mt-1 flex flex-wrap gap-x-3 gap-y-1">
                  {j.department && <span>{j.department}</span>}{j.location && <span className="inline-flex items-center gap-1"><MapPin className="w-3 h-3" />{j.location}</span>}
                  <span>{EMP_TYPES[j.employment_type] || ""}</span>{j.posted_on && <span>Posted {fmtDate(j.posted_on)}</span>}
                </div>
              </Link>))}
        </div>)}
    </Shell>
  );
}

export function CareerJobPage() {
  const { orgCode = "", jobId = "" } = useParams();
  const [sp] = useSearchParams();
  const src = sp.get("src") || "";
  const { data, isLoading, error } = useQuery({ queryKey: ["careers", orgCode, jobId], queryFn: () => publicApi.job(orgCode, jobId), retry: false });
  const [f, setF] = useState<Row>({});
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [wa, setWa] = useState(false);
  const [captcha, setCaptcha] = useState("");
  const [done, setDone] = useState<Row | null>(null);
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));
  // Google for Jobs: JobPosting structured data on the page itself (only when the posting is switched on for Google).
  useEffect(() => {
    if (!data?.jsonld) return;
    const s = document.createElement("script"); s.type = "application/ld+json"; s.id = "lexora-jobposting"; s.text = JSON.stringify(data.jsonld);
    document.head.appendChild(s);
    return () => { document.getElementById("lexora-jobposting")?.remove(); };
  }, [data?.jsonld]);
  const apply = useMutation({
    mutationFn: () => {
      const form = new FormData();
      ["first_name", "last_name", "email", "phone", "city", "current_employer", "current_title"].forEach((k) => form.append(k, f[k] || ""));
      form.append("consent", consent ? "true" : "false"); form.append("whatsapp_opt_in", wa ? "true" : "false");
      form.append("src", src); form.append("captcha_token", captcha);
      if (file) form.append("resume", file);
      return publicApi.apply(orgCode, jobId, form);
    },
    onSuccess: (r) => { setDone(r); window.scrollTo({ top: 0 }); },
  });
  const tooBig = !!file && !!data && file.size > data.resume_max_mb * 1024 * 1024;
  const captchaOk = !data?.captcha?.configured || !!captcha;
  const ready = f.first_name && f.email && file && consent && !tooBig && captchaOk;
  const onToken = useMemo(() => (t: string) => setCaptcha(t), []);
  return (
    <Shell org={data?.org_name} brand={data?.branding}>
      {isLoading ? <Msg title="Loading" /> : error ? <Msg title="This job is not available">{err(error, "It may have been filled or closed.")}</Msg>
        : done ? <div className="card text-center py-10"><CheckCircle2 className="w-10 h-10 text-green-500 mx-auto mb-2" /><h2 className="font-display font-semibold text-gray-800">{done.message}</h2>
          <p className="text-sm text-gray-500 mt-1">{done.confirmation_email === "sent" ? "A confirmation e-mail is on its way." : "The hiring team will contact you if your profile fits."}</p><Link className="btn-secondary inline-block mt-4" to={`/careers/${orgCode}`}>See other jobs</Link></div> : (
          <div className="space-y-4">
            <Link to={`/careers/${orgCode}`} className="text-xs text-primary-600 inline-flex items-center gap-1"><ArrowLeft className="w-3 h-3" />All open jobs</Link>
            <div className="card">
              <h1 className="font-display text-xl font-bold text-gray-800">{data!.title}</h1>
              <div className="text-xs text-gray-500 mt-1 mb-4 flex flex-wrap gap-x-3 gap-y-1">{data!.department && <span>{data!.department}</span>}
                {data!.location && <span className="inline-flex items-center gap-1"><MapPin className="w-3 h-3" />{data!.location}</span>}<span>{EMP_TYPES[data!.employment_type] || ""}</span>{data!.reference && <span>Ref {data!.reference}</span>}</div>
              <Para text={data!.description} />
              {data!.requirements && <><h2 className="font-display font-semibold text-gray-800 mt-4 mb-2">Requirements</h2><Para text={data!.requirements} /></>}
            </div>
            <div className="card">
              <h2 className="font-display font-semibold text-gray-800 mb-3">Apply for this job</h2>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <PField label="First name *"><input className="input w-full" autoComplete="given-name" value={f.first_name || ""} onChange={(e) => set("first_name", e.target.value)} /></PField>
                <PField label="Last name"><input className="input w-full" autoComplete="family-name" value={f.last_name || ""} onChange={(e) => set("last_name", e.target.value)} /></PField>
                <PField label="E-mail *"><input className="input w-full" type="email" autoComplete="email" inputMode="email" value={f.email || ""} onChange={(e) => set("email", e.target.value)} /></PField>
                <PField label="Phone"><input className="input w-full" type="tel" autoComplete="tel" inputMode="tel" value={f.phone || ""} onChange={(e) => set("phone", e.target.value)} /></PField>
                <PField label="Current employer"><input className="input w-full" value={f.current_employer || ""} onChange={(e) => set("current_employer", e.target.value)} /></PField>
                <PField label="Current job title"><input className="input w-full" value={f.current_title || ""} onChange={(e) => set("current_title", e.target.value)} /></PField>
                <PField label="City"><input className="input w-full" value={f.city || ""} onChange={(e) => set("city", e.target.value)} /></PField>
                <PField label={`Resume * (${data!.accepted_files}, up to ${data!.resume_max_mb} MB)`}>
                  <input type="file" accept=".pdf,.docx,.jpg,.jpeg,.png" className="block w-full text-sm" onChange={(e) => setFile(e.target.files?.[0] || null)} />
                  {tooBig && <span className="text-xs text-red-600">This file is larger than {data!.resume_max_mb} MB.</span>}</PField>
              </div>
              <label className="flex items-start gap-2 mt-4 text-sm text-gray-700">
                <input type="checkbox" className="mt-1 w-4 h-4 shrink-0" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                <span><b>I agree.</b> {data!.privacy_text}</span>
              </label>
              {!!f.phone && <label className="flex items-start gap-2 mt-2 text-sm text-gray-700">
                <input type="checkbox" className="mt-1 w-4 h-4 shrink-0" checked={wa} onChange={(e) => setWa(e.target.checked)} />
                <span>{data!.whatsapp_text}</span>
              </label>}
              <div className="mt-3"><CaptchaWidget cfg={data!.captcha} onToken={onToken} /></div>
              {apply.isError && <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err(apply.error, "Your application could not be sent. Please try again.")}</div>}
              <BrandBtn className="w-full sm:w-auto mt-4" disabled={!ready || apply.isPending} onClick={() => apply.mutate()}>{apply.isPending ? "Sending" : "Send application"}</BrandBtn>
            </div>
          </div>)}
    </Shell>
  );
}

/** The candidate picks one of the proposed interview slots (single-use link). */
export function InterviewPickPage() {
  const { token = "" } = useParams();
  const { data, isLoading, error } = useQuery({ queryKey: ["interview-pick", token], queryFn: () => publicApi.interview(token), retry: false });
  const [picked, setPicked] = useState<number | null>(null);
  const [msg, setMsg] = useState("");
  const [result, setResult] = useState<Row | null>(null);
  const pick = useMutation({ mutationFn: () => publicApi.pickSlot(token, { slot_index: picked, message: msg }), onSuccess: setResult });
  const brand: Brand = { org_name: data?.org_name, primary_color: data?.primary_color, tagline: "Interview scheduling" };
  return (
    <Shell org={data?.org_name} brand={brand}>
      {isLoading ? <Msg title="Loading" /> : error ? <Msg title="This link is not valid">{err(error, "Check the link in your e-mail, or contact the hiring team.")}</Msg>
        : result ? <div className="card text-center py-10"><CheckCircle2 className="w-10 h-10 text-green-500 mx-auto mb-2" /><h2 className="font-display font-semibold text-gray-800">Confirmed: {result.when}</h2>
          <p className="text-sm text-gray-500 mt-1">{result.message}</p>{result.place && <p className="text-sm mt-2 break-all">{safeHref(result.place) ? <a className="underline" href={safeHref(result.place)} target="_blank" rel="noopener noreferrer">{result.place}</a> : result.place}</p>}</div>
          : data!.state !== "ok" ? <Msg title={data!.state === "picked" ? `Your interview is on ${fmtDateTime(data!.scheduled_at)}` : "This link is not open"}>{data!.message}</Msg> : (
            <div className="space-y-4">
              <div className="card">
                <h1 className="font-display text-xl font-bold text-gray-800">Hello {data!.first_name}, pick a time</h1>
                <p className="text-sm text-gray-500 mt-1">{data!.round_name} for {data!.job_title} · {data!.duration_min} minutes · {data!.mode === "video" ? "video call" : data!.mode === "phone" ? "phone" : "in person"}. Times are shown in your device's time zone.</p>
                <div className="mt-4 space-y-2">
                  {(data!.slots as Row[]).map((s) => (
                    <button key={s.index} onClick={() => setPicked(s.index)} className={`w-full text-left rounded-lg border px-3 py-3 text-sm flex items-center gap-2 ${picked === s.index ? "border-primary-500 bg-primary-50" : "border-surface-border bg-white"}`}>
                      <CalendarClock className="w-4 h-4 text-gray-400 shrink-0" /><span className="font-medium text-gray-800">{fmtDateTime(s.start)}</span><span className="text-gray-400">to {new Date(s.end).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</span>
                    </button>))}
                  {!(data!.slots || []).length && <div className="text-sm text-gray-500">All offered times have passed. Please contact the hiring team.</div>}
                </div>
                <PField label="A note for the team (optional)"><textarea className="input w-full" rows={2} value={msg} onChange={(e) => setMsg(e.target.value)} /></PField>
                {pick.isError && <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err(pick.error)}</div>}
                <BrandBtn className="w-full sm:w-auto mt-4" disabled={picked === null || pick.isPending} onClick={() => pick.mutate()}>Confirm this time</BrandBtn>
                <p className="text-[11px] text-gray-400 mt-2">This link works once and is valid until {fmtDateTime(data!.valid_until)}.</p>
              </div>
            </div>)}
    </Shell>
  );
}

export function OfferAnswerPage() {
  const { token = "" } = useParams();
  const { data, isLoading, error } = useQuery({ queryKey: ["offer-link", token], queryFn: async () => { return publicApi.offer(token); }, retry: false });
  const [name, setName] = useState("");
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const [result, setResult] = useState<Row | null>(null);
  const answer = useMutation({
    mutationFn: (accept: boolean) => publicApi.answer(token, accept, accept ? { signed_name: name } : { reason }),
    onSuccess: setResult,
  });
  return (
    <Shell org={data?.org_name}>
      {isLoading ? <Msg title="Loading" /> : error ? <Msg title="This offer link is not valid">{err(error, "Check the link in your e-mail, or contact the HR team.")}</Msg>
        : result ? <div className="card text-center py-10"><CheckCircle2 className="w-10 h-10 text-green-500 mx-auto mb-2" /><h2 className="font-display font-semibold text-gray-800">{result.answer === "accepted" ? "Offer accepted" : "Offer declined"}</h2><p className="text-sm text-gray-500 mt-1">{result.message}</p></div>
          : data!.state !== "ok" ? <Msg title={data!.answer === "accepted" ? "You have accepted this offer" : data!.answer === "declined" ? "You declined this offer" : "This offer is not open"}>{data!.message}</Msg> : (
            <div className="space-y-4">
              <div className="card">
                <h1 className="font-display text-xl font-bold text-gray-800">Hello {data!.first_name}, here is your offer</h1>
                <p className="text-sm text-gray-500 mt-1">Please answer by {fmtDate(data!.valid_until)}. This link works once.</p>
                <dl className="mt-4 divide-y divide-surface-border text-sm">
                  {[["Position", data!.designation], ["Department", data!.department], ["Work location", data!.location], ["Joining date", fmtDate(data!.joining_date)],
                    ["Reports to", data!.reporting_to], ["Annual salary", money(data!.annual_ctc)]].filter(([, v]) => v).map(([k, v]) => (
                    <div key={k} className="flex justify-between gap-4 py-2"><dt className="text-gray-500">{k}</dt><dd className="text-gray-800 font-medium text-right">{v}</dd></div>))}
                </dl>
              </div>
              {(data!.breakup || []).length > 0 && (
                <div className="card !p-0 overflow-hidden">
                  <div className="px-4 py-3 font-display font-semibold text-gray-800">Pay break-up</div>
                  <div className="overflow-x-auto"><table className="w-full text-sm">
                    <thead className="bg-surface text-gray-400 text-[11px] uppercase"><tr><th className="px-4 py-2 text-left">Component</th><th className="px-4 py-2 text-right">Monthly</th><th className="px-4 py-2 text-right">Annual</th></tr></thead>
                    <tbody>{(data!.breakup as Row[]).map((l) => <tr key={l.code} className="border-t border-surface-border"><td className="px-4 py-2">{l.name}</td><td className="px-4 py-2 text-right tabular-nums">{money(l.monthly)}</td><td className="px-4 py-2 text-right tabular-nums">{money(l.annual)}</td></tr>)}</tbody>
                  </table></div>
                  <p className="px-4 py-2 text-[11px] text-gray-400 border-t border-surface-border">Statutory deductions are applied by payroll as the law requires and are not shown here.</p>
                </div>)}
              <div className="card">
                {!declining ? <>
                  <PField label="To accept, type your full name"><input className="input w-full" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} /></PField>
                  {answer.isError && <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err(answer.error)}</div>}
                  <div className="flex flex-col sm:flex-row gap-2 mt-4">
                    <button className="btn-primary !py-2.5 flex-1 disabled:opacity-50" disabled={name.trim().length < 2 || answer.isPending} onClick={() => answer.mutate(true)}>Accept offer</button>
                    <button className="btn-secondary !py-2.5 flex-1" onClick={() => setDeclining(true)}>Decline</button>
                  </div></> : <>
                  <PField label="You are declining this offer. You may tell us why (optional)"><textarea className="input w-full" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} /></PField>
                  {answer.isError && <div className="mt-3 text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{err(answer.error)}</div>}
                  <div className="flex flex-col sm:flex-row gap-2 mt-4">
                    <button className="btn-primary !py-2.5 flex-1 disabled:opacity-50" disabled={answer.isPending} onClick={() => answer.mutate(false)}>Confirm decline</button>
                    <button className="btn-secondary !py-2.5 flex-1" onClick={() => setDeclining(false)}>Go back</button>
                  </div></>}
              </div>
            </div>)}
    </Shell>
  );
}
