import { useRef, useState, ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Eye, EyeOff, CheckCircle2, AlertTriangle, Loader2 } from "lucide-react";
import Logo from "../components/Logo";
import { linkApi, errText } from "../api/identity";
import CaptchaWidget, { postWithCaptcha, type CaptchaHandle } from "../components/CaptchaWidget";   // WASA 2026-10-21

/* Public pages (no login): set first password from an invite, reset a password
   from a link, and ask for a reset link. Built for a phone first. */

function Shell({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4 py-10
                    bg-gradient-to-br from-primary-950 via-primary-700 to-primary-400">
      <div className="w-full max-w-md bg-white rounded-2xl shadow-2xl p-6 sm:p-8">
        <div className="text-center mb-5"><Logo className="h-10 w-auto mx-auto" /></div>
        <h1 className="font-display text-xl font-bold text-gray-800 text-center">{title}</h1>
        {sub && <p className="text-sm text-gray-500 text-center mt-1 mb-5">{sub}</p>}
        {children}
      </div>
    </div>
  );
}

function Notice({ tone, children }: { tone: "ok" | "bad"; children: ReactNode }) {
  const Icon = tone === "ok" ? CheckCircle2 : AlertTriangle;
  return (
    <div className={`flex items-start gap-2.5 rounded-lg p-3 text-sm ${tone === "ok" ? "bg-green-50 text-green-800" : "bg-amber-50 text-amber-800"}`}>
      <Icon className="w-4 h-4 mt-0.5 shrink-0" /><div>{children}</div>
    </div>
  );
}

const LABEL = "block text-xs font-semibold text-gray-500 mb-1";

function PasswordForm({ kind }: { kind: "invite" | "reset" }) {
  const { token = "" } = useParams();
  const nav = useNavigate();
  const { data, isLoading, error } = useQuery({
    queryKey: ["link", kind, token], queryFn: () => linkApi.info(kind, token), retry: false, staleTime: 0 });
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ email: string; org_code: string | null } | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (pw !== pw2) return toast.error("The two passwords do not match.");
    setBusy(true);
    try { setDone(await linkApi.setPassword(kind, token, pw)); }
    catch (err) { toast.error(errText(err, "Could not save the password.")); }
    finally { setBusy(false); }
  };

  const title = kind === "invite" ? "Set your password" : "Choose a new password";
  if (isLoading) return <Shell title={title}><div className="grid place-items-center py-8"><Loader2 className="w-6 h-6 animate-spin text-primary-500" /></div></Shell>;
  if (error || !data) return (
    <Shell title={title}>
      <Notice tone="bad">{errText(error, "This link is not valid. Ask your HR team for a new one.")}</Notice>
      <Link to="/login" className="btn-secondary w-full mt-4 block text-center py-2.5">Go to sign in</Link>
    </Shell>
  );
  if (done) return (
    <Shell title="Password saved">
      <Notice tone="ok">You can now sign in with <b>{done.email}</b>.
        {done.org_code && <> Your organization code is <b className="font-mono">{done.org_code}</b>.</>}</Notice>
      <button className="btn-primary w-full mt-4 py-3" onClick={() => nav("/login")}>Go to sign in</button>
    </Shell>
  );

  return (
    <Shell title={title} sub={data.org_name ? `${data.org_name} · ${data.email}` : data.email}>
      <form onSubmit={submit} className="space-y-4">
        {data.first_name && kind === "invite" && <p className="text-sm text-gray-700">Welcome, {data.first_name}. Choose a password to open your account.</p>}
        <div>
          <label className={LABEL}>New password</label>
          <div className="relative">
            <input className="input py-2.5 pr-10" type={show ? "text" : "password"} value={pw} autoFocus required
              autoComplete="new-password" onChange={(e) => setPw(e.target.value)} />
            <button type="button" onClick={() => setShow(!show)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400">
              {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}</button>
          </div>
        </div>
        <div>
          <label className={LABEL}>Type it again</label>
          <input className="input py-2.5" type={show ? "text" : "password"} value={pw2} required autoComplete="new-password"
            onChange={(e) => setPw2(e.target.value)} />
        </div>
        <p className="text-xs text-gray-500">{data.password_policy}</p>
        <button type="submit" disabled={busy || !pw || !pw2} className="btn-primary w-full py-3 text-sm font-semibold disabled:opacity-60">
          {busy ? "Saving…" : "Save password"}</button>
      </form>
    </Shell>
  );
}

export default function SetPasswordPage() { return <PasswordForm kind="invite" />; }
export function ResetPasswordPage() { return <PasswordForm kind="reset" />; }

export function ForgotPasswordPage() {
  const [orgCode, setOrgCode] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<{ message: string; dev_link?: string } | null>(null);
  const captcha = useRef<CaptchaHandle | null>(null);
  const [captchaOk, setCaptchaOk] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { setSent(await postWithCaptcha(captcha.current, (ct) => linkApi.forgot(email.trim(), orgCode.trim(), ct))); }
    catch (err) { toast.error(errText(err, "Could not send the link. Try again.")); }
    finally { setBusy(false); }
  };
  return (
    <Shell title="Forgot your password?" sub="We will e-mail you a link to choose a new one.">
      {sent ? (
        <>
          <Notice tone="ok">{sent.message} The link works once. No e-mail? Ask your HR team to reset it for you.</Notice>
          {sent.dev_link && <a className="block mt-3 text-xs text-primary-600 break-all underline" href={sent.dev_link}>Test set-up only: open the reset link</a>}
          <Link to="/login" className="btn-secondary w-full mt-4 block text-center py-2.5">Back to sign in</Link>
        </>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className={LABEL}>Organization code</label>
            <input className="input py-2.5 uppercase tracking-wider" value={orgCode} required autoFocus autoComplete="off"
              onChange={(e) => setOrgCode(e.target.value.toUpperCase().trim())} placeholder="e.g. MAPLE" />
          </div>
          <div>
            <label className={LABEL}>Work e-mail</label>
            <input className="input py-2.5" type="email" value={email} required onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" />
          </div>
          <CaptchaWidget ref={captcha} purpose="forgot" account={email} onReadyChange={setCaptchaOk} />
          <button type="submit" disabled={busy || !captchaOk} className="btn-primary w-full py-3 text-sm font-semibold disabled:opacity-60">
            {busy ? "Sending…" : "Send me the link"}</button>
          <Link to="/login" className="block text-center text-xs text-gray-500 hover:text-primary-600">Back to sign in</Link>
        </form>
      )}
    </Shell>
  );
}
