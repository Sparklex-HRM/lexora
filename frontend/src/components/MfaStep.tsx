import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { ShieldCheck, Copy, KeyRound } from "lucide-react";
import { publicAuthApi } from "../api/publicAuth";   // WASA 2026-10-24: no API map on /login
import type { MfaSetup, SignInResult } from "../api/saas";
import { errText } from "../lib/errText";
import { useAuthStore } from "../store/authStore";
import CaptchaWidget, { postWithCaptcha, type CaptchaHandle } from "./CaptchaWidget";   // WASA 2026-10-21

/** Store the tokens, load /me and land on the right home. */
export function useFinishSignIn() {
  const nav = useNavigate();
  return async (t: SignInResult, email: string) => {
    useAuthStore.getState().setAuth(t.access_token!, t.refresh_token || null, { id: "", org_id: t.org_id, email, role: t.role });
    const me = await publicAuthApi.me();
    useAuthStore.getState().setUser(me);
    toast.success(`Welcome, ${me.first_name || "there"}`);
    nav(t.role === "superadmin" ? "/platform" : "/");
  };
}

/** The second sign-in step: enter a code, or set two-step sign-in up (mandatory roles). */
export default function MfaStep({ result, email, onCancel, onForced }: {
  result: SignInResult; email: string; onCancel: () => void;
  /** WASA #7: the tokens say the password must be changed first. */
  onForced?: (t: SignInResult) => void;
}) {
  const finishSignIn = useFinishSignIn();
  const finish = async (t: SignInResult, mail: string) => {
    if (t.password_change_required && onForced) { onForced(t); return; }
    await finishSignIn(t, mail);
  };
  const captcha = useRef<CaptchaHandle | null>(null);       // our own proof-of-work "I'm not a robot" box
  const [captchaOk, setCaptchaOk] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [pending, setPending] = useState<SignInResult | null>(null);
  const enrol = !!result.mfa_setup_required;

  useEffect(() => {
    if (enrol && result.mfa_token) publicAuthApi.mfaEnrollStart(result.mfa_token).then(setSetup).catch((e) => toast.error(errText(e)));
  }, [enrol, result.mfa_token]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (enrol) {
        const t = await postWithCaptcha(captcha.current, (ct) => publicAuthApi.mfaEnrollFinish(result.mfa_token!, code, ct));
        setCodes(t.recovery_codes || []);
        setPending(t);
      } else {
        await finish(await postWithCaptcha(captcha.current, (ct) => publicAuthApi.mfaVerify(result.mfa_token!, code, ct)), email);
      }
    } catch (err) {
      toast.error(errText(err, "That code did not work"));
    } finally {
      setBusy(false);
    }
  };

  if (codes && pending) {
    return (
      <div className="space-y-3">
        <h3 className="font-display text-lg font-bold text-gray-800 flex items-center gap-2"><KeyRound className="w-5 h-5 text-primary-600" /> Save your recovery codes</h3>
        <p className="text-sm text-gray-500">Each code signs you in ONCE if you lose your phone. Store them somewhere safe — they are not shown again.</p>
        <div className="grid grid-cols-2 gap-1.5 font-mono text-sm bg-gray-50 border border-surface-border rounded-lg p-3" data-testid="recovery-codes">
          {codes.map((c) => <span key={c}>{c}</span>)}
        </div>
        <button type="button" className="btn-secondary w-full py-2 text-sm inline-flex items-center justify-center gap-1.5"
          onClick={() => { navigator.clipboard?.writeText(codes.join("\n")); toast.success("Copied"); }}>
          <Copy className="w-4 h-4" /> Copy codes
        </button>
        <button type="button" className="btn-primary w-full py-3 text-sm font-semibold" onClick={() => finish(pending, email)}>
          I have saved them — continue
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <h3 className="font-display text-lg font-bold text-gray-800 flex items-center gap-2">
        <ShieldCheck className="w-5 h-5 text-primary-600" /> {enrol ? "Set up two-step sign-in" : "Two-step sign-in"}
      </h3>
      {enrol ? (
        <>
          <p className="text-sm text-gray-500">{result.reason || "Your role must use two-step sign-in."} Open an authenticator app (Google Authenticator, Microsoft Authenticator, Authy …) and add this account.</p>
          {setup?.qr_svg && <div className="mx-auto w-44 h-44 [&>svg]:w-full [&>svg]:h-full" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />}
          {setup && (
            <div className="text-xs text-gray-600 bg-gray-50 border border-surface-border rounded-lg p-3">
              <div className="font-semibold mb-1">{setup.qr_svg ? "Or type this setup key:" : "Type this setup key into the app:"}</div>
              <div className="font-mono text-sm break-all select-all" data-testid="mfa-secret">{setup.secret_grouped}</div>
              <div className="mt-1 text-gray-400">Time-based · {setup.digits} digits · every {setup.period} s</div>
            </div>
          )}
          <label className="block text-xs font-semibold text-gray-500">Then enter the 6-digit code the app shows</label>
        </>
      ) : (
        <p className="text-sm text-gray-500">Enter the 6-digit code from your authenticator app. Lost your phone? Type one of your recovery codes instead.</p>
      )}
      <input className="input py-2.5 tracking-[0.3em] text-center font-mono" value={code} autoFocus required
        onChange={(e) => setCode(e.target.value.trim())} placeholder={enrol ? "123456" : "123456 or ABCDE-FGHJK"} autoComplete="one-time-code" />
      <CaptchaWidget ref={captcha} purpose="mfa" account={email} onReadyChange={setCaptchaOk} />
      <button type="submit" disabled={busy || (enrol && !setup) || !captchaOk} className="btn-primary w-full py-3 text-sm font-semibold">
        {busy ? "Checking…" : enrol ? "Turn on and continue" : "Verify"}
      </button>
      <button type="button" onClick={onCancel} className="block w-full text-center text-xs text-gray-400 hover:text-primary-600">← Back to sign in</button>
    </form>
  );
}
