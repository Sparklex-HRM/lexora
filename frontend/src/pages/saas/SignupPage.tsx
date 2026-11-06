import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { CheckCircle2, MailCheck, Building2, Eye, EyeOff } from "lucide-react";
import Logo from "../../components/Logo";
import MfaStep, { useFinishSignIn } from "../../components/MfaStep";
import { signupApi, errText, type SignInResult, type SignupBody, type SignupConfig } from "../../api/saas";
import CaptchaWidget, { postWithCaptcha, type CaptchaHandle } from "../../components/CaptchaWidget";   // WASA 2026-10-21

const cad = (n: number) => new Intl.NumberFormat("en-CA", { style: "currency", currency: "CAD", maximumFractionDigits: 2 }).format(n || 0);
/** The auth service's sign-up config lists Canadian provinces / territories (`provinces`). */
type CaSignupConfig = SignupConfig & { provinces?: { code: string; name: string }[] };

function Shell({ children, wide = false }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4 py-10 bg-gradient-to-br from-primary-950 via-primary-700 to-primary-400">
      <div className={`w-full ${wide ? "max-w-4xl" : "max-w-lg"}`}>
        <div className="bg-white rounded-2xl shadow-2xl p-6 sm:p-9">
          <div className="text-center mb-6">
            <Logo tagline className="h-16 sm:h-20 w-auto max-w-full mx-auto" />
            <p className="mt-2 text-[11px] text-gray-400 tracking-[0.2em] uppercase">Canadian HR &amp; payroll</p>
          </div>
          {children}
        </div>
        <p className="mt-5 text-center text-[11px] text-white/80">
          <Link to="/legal/privacy" className="hover:text-white">Privacy Policy</Link> · <Link to="/legal/terms" className="hover:text-white">Terms of Service</Link> · <Link to="/login" className="hover:text-white">Sign in</Link>
        </p>
      </div>
    </div>
  );
}

/** /signup — company + admin details, plan, terms → e-mail confirmation link. */
export default function SignupPage() {
  const { data: cfgRaw } = useQuery({ queryKey: ["signup-config"], queryFn: signupApi.config });
  const cfg = cfgRaw as CaSignupConfig | undefined;
  const [f, setF] = useState<SignupBody>({ org_name: "", admin_email: "", admin_password: "", admin_first_name: "", admin_last_name: "",
    phone: "", state: "", plan: "growth", addons: [], accept_terms: false, website: "" });
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<{ message: string; email: string; email_sent: boolean; dev_link?: string } | null>(null);
  const set = (k: keyof SignupBody, v: any) => setF((x) => ({ ...x, [k]: v }));
  const captcha = useRef<CaptchaHandle | null>(null);
  const [captchaOk, setCaptchaOk] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      // Canada only: the form's region is sent as `province` (the auth service field); no country picker.
      setSent(await postWithCaptcha(captcha.current, (captcha_token) => signupApi.start({
        ...f, province: f.state, employees_estimate: f.employees_estimate ? Number(f.employees_estimate) : undefined, captcha_token } as unknown as SignupBody)));
    } catch (err) {
      toast.error(errText(err, "Could not start the sign-up"));
    } finally {
      setBusy(false);
    }
  };

  if (cfg && cfg.enabled === false) {
    // Decision 2026-10-21: self sign-up is off — accounts are opened by Lexora HRM or by invite.
    return (
      <Shell>
        <div className="text-center space-y-3" data-testid="signup-closed">
          <h2 className="font-display text-xl font-bold text-gray-800">Sign-up is by invitation</h2>
          <p className="text-sm text-gray-600">{cfg.message || "Contact Lexora HRM to open an account for your company."}</p>
          <Link to="/login" className="btn-primary inline-block px-5 py-2.5 text-sm">Back to sign in</Link>
        </div>
      </Shell>
    );
  }

  if (sent) {
    return (
      <Shell>
        <div className="text-center space-y-3">
          <MailCheck className="w-12 h-12 mx-auto text-primary-600" />
          <h2 className="font-display text-xl font-bold text-gray-800">{sent.email_sent ? "Check your inbox" : "One more step"}</h2>
          <p className="text-sm text-gray-600">{sent.message}</p>
          {sent.email_sent && <p className="text-xs text-gray-400">Sent to {sent.email}</p>}
          {sent.dev_link && (
            <a href={sent.dev_link.replace(/^https?:\/\/[^/]+/, "")} className="btn-primary inline-block px-5 py-2.5 text-sm" data-testid="dev-link">
              Open the confirmation link (local set-up)
            </a>
          )}
        </div>
      </Shell>
    );
  }

  const plans = cfg?.plans || [];
  return (
    <Shell wide>
      <h2 className="font-display text-2xl font-bold text-gray-800 text-center">Start your {cfg?.trial_days ?? 14}-day free trial</h2>
      <p className="text-sm text-gray-500 text-center mb-6">No card needed. Canadian payroll (CPP/QPP, EI, federal and provincial tax), T4 / RL-1 / ROE and your people — set up in minutes.</p>
      <form onSubmit={submit} className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">Your company</div>
          <label className="block"><span className="text-xs font-semibold text-gray-500">Company name</span>
            <input className="input py-2" required value={f.org_name} onChange={(e) => set("org_name", e.target.value)} placeholder="Maple Leaf Technologies Inc." /></label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block"><span className="text-xs font-semibold text-gray-500">Province / territory</span>
              <select className="input py-2" required value={f.state} onChange={(e) => set("state", e.target.value)}>
                <option value="">Choose…</option>
                {(cfg?.provinces || []).map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
              </select></label>
            <label className="block"><span className="text-xs font-semibold text-gray-500">Employees (approx.)</span>
              <input className="input py-2" type="number" min={1} value={f.employees_estimate ?? ""} onChange={(e) => set("employees_estimate", e.target.value)} placeholder="40" /></label>
          </div>
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-400 pt-2">You (the first admin)</div>
          <div className="grid grid-cols-2 gap-3">
            <label className="block"><span className="text-xs font-semibold text-gray-500">First name</span>
              <input className="input py-2" required value={f.admin_first_name} onChange={(e) => set("admin_first_name", e.target.value)} /></label>
            <label className="block"><span className="text-xs font-semibold text-gray-500">Last name</span>
              <input className="input py-2" value={f.admin_last_name} onChange={(e) => set("admin_last_name", e.target.value)} /></label>
          </div>
          <label className="block"><span className="text-xs font-semibold text-gray-500">Work e-mail</span>
            <input className="input py-2" type="email" required value={f.admin_email} onChange={(e) => set("admin_email", e.target.value)} placeholder="you@company.ca" /></label>
          <label className="block"><span className="text-xs font-semibold text-gray-500">Mobile (optional)</span>
            <input className="input py-2" value={f.phone} onChange={(e) => set("phone", e.target.value)} placeholder="+1 416 555 0123" /></label>
          <label className="block"><span className="text-xs font-semibold text-gray-500">Password</span>
            <div className="relative">
              <input className="input py-2 pr-10" type={showPw ? "text" : "password"} required value={f.admin_password} onChange={(e) => set("admin_password", e.target.value)} />
              <button type="button" onClick={() => setShowPw(!showPw)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">{showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}</button>
            </div>
            <span className="text-[11px] text-gray-400">{cfg?.password_policy}</span></label>
          {/* honeypot — hidden from people, bots fill it */}
          <input tabIndex={-1} autoComplete="off" aria-hidden="true" className="hidden" value={f.website} onChange={(e) => set("website", e.target.value)} name="website" />
        </div>
        <div className="space-y-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-gray-400">Plan (you can change it any time)</div>
          {plans.map((p) => (
            <label key={p.key} className={`block border rounded-xl p-3 cursor-pointer transition ${f.plan === p.key ? "border-primary-500 bg-primary-50/60 ring-1 ring-primary-300" : "border-surface-border hover:border-primary-300"}`}>
              <div className="flex items-center gap-2">
                <input type="radio" name="plan" checked={f.plan === p.key} onChange={() => set("plan", p.key)} />
                <span className="font-semibold text-gray-800">{p.label}</span>
                <span className="ml-auto text-sm font-semibold text-primary-700">{p.custom ? "quoted" : `${cad(p.pepm)} / employee / month`}</span>
              </div>
              <div className="text-xs text-gray-500 mt-1 pl-6">{p.includes}</div>
              <div className="text-[11px] text-gray-400 mt-0.5 pl-6">Minimum {cad(p.min_monthly)} / month + GST/HST{p.max_employees ? ` · up to ${p.max_employees} employees` : ""}</div>
            </label>
          ))}
          {!plans.length && <div className="text-sm text-gray-400">Loading plans…</div>}
          {(cfg?.addons || []).map((a) => {
            const on = (f.addons || []).includes(a.key);
            return (
              <label key={a.key} className={`block border rounded-xl p-3 cursor-pointer transition ${on ? "border-teal-500 bg-teal-50/60 ring-1 ring-teal-300" : "border-surface-border hover:border-teal-300"}`}>
                <div className="flex items-center gap-2">
                  <input type="checkbox" checked={on} onChange={(e) => set("addons", e.target.checked ? [...(f.addons || []), a.key] : (f.addons || []).filter((k) => k !== a.key))} />
                  <span className="font-semibold text-gray-800">Add agentic operations</span>
                  <span className="ml-auto text-sm font-semibold text-teal-700">+{cad(a.price)} / employee / month</span>
                </div>
                <div className="text-xs text-gray-500 mt-1 pl-6">{a.agents.join(" · ")} — agents that prepare, chase and finish the work; you approve. Works on any plan.</div>
                <div className="text-[11px] text-gray-400 mt-0.5 pl-6">{a.note}</div>
              </label>
            );
          })}
          <label className="flex items-start gap-2 text-xs text-gray-600 pt-2">
            <input type="checkbox" checked={f.accept_terms} onChange={(e) => set("accept_terms", e.target.checked)} className="mt-0.5" />
            <span>I accept the <Link to="/legal/terms" target="_blank" className="text-primary-700 underline">Terms of Service</Link> and the <Link to="/legal/privacy" target="_blank" className="text-primary-700 underline">Privacy Policy</Link> on behalf of my company.</span>
          </label>
          {cfg?.captcha?.configured && <div className="text-[11px] text-amber-700">A human check ({cfg.captcha.provider}) is required on this server — the widget appears here once its site key is set.</div>}
          <CaptchaWidget ref={captcha} purpose="signup" account={f.admin_email} onReadyChange={setCaptchaOk} />
          <button type="submit" disabled={busy || !captchaOk} className="btn-primary w-full py-3 text-sm font-semibold">{busy ? "Sending…" : "Create my trial"}</button>
          <p className="text-[11px] text-gray-400 text-center">Already on Lexora HRM? <Link to="/login" className="text-primary-700">Sign in</Link></p>
        </div>
      </form>
    </Shell>
  );
}

/** /signup/verify/:token — confirm e-mail → org created → MFA set-up (admins) → setup checklist. */
export function SignupVerifyPage() {
  const { token = "" } = useParams();
  const finish = useFinishSignIn();
  const [peek, setPeek] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SignInResult | null>(null);
  useEffect(() => { signupApi.peek(token).then(setPeek).catch((e) => setErr(errText(e, "This link is not valid"))); }, [token]);

  const confirm = async () => {
    setBusy(true);
    try {
      const r = await signupApi.verify(token);
      if (r.mfa_required || r.mfa_setup_required) setResult(r);
      else await finish(r, peek?.email || "");
    } catch (e) {
      setErr(errText(e, "Could not create the organization"));
    } finally {
      setBusy(false);
    }
  };

  if (err) return <Shell><div className="text-center space-y-3"><h2 className="font-display text-xl font-bold text-gray-800">Link problem</h2><p className="text-sm text-gray-600">{err}</p><Link to="/signup" className="btn-primary inline-block px-5 py-2 text-sm">Sign up again</Link></div></Shell>;
  if (result) {
    return (
      <Shell>
        <div className="mb-4 rounded-lg bg-emerald-50 border border-emerald-200 px-3 py-2 text-sm text-emerald-800 flex items-start gap-2">
          <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
          <span><b>{result.org_name}</b> is ready. Your organization code is <b className="font-mono" data-testid="org-code">{result.org_code}</b> — you will type it at every sign-in.
            {(result as SignInResult & { canada_defaults?: { ok: boolean } }).canada_defaults?.ok === false && " Leave types / provincial statutory holidays could not be added yet — use the setup checklist to retry."}</span>
        </div>
        <MfaStep result={result} email={peek?.email || ""} onCancel={() => window.location.assign("/login")} />
      </Shell>
    );
  }
  return (
    <Shell>
      <div className="text-center space-y-3">
        <Building2 className="w-11 h-11 mx-auto text-primary-600" />
        <h2 className="font-display text-xl font-bold text-gray-800">Confirm and create {peek?.org_name || "your organization"}</h2>
        {peek && <p className="text-sm text-gray-600">Admin: {peek.email} · {peek.trial_days}-day free trial on the {peek.plan} plan.</p>}
        <button onClick={confirm} disabled={!peek || busy} className="btn-primary w-full py-3 text-sm font-semibold">{busy ? "Creating…" : "Confirm my e-mail and create the organization"}</button>
      </div>
    </Shell>
  );
}
