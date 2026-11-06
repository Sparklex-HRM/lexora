import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import { Eye, EyeOff, Sun, Moon } from "lucide-react";
import Logo from "../components/Logo";
import DpoFooter from "../components/DpoFooter";   // Stream E: Privacy Officer contact in the login footer
import { publicAuthApi } from "../api/publicAuth";   // WASA 2026-10-24: no API map on /login
import { useAuthStore } from "../store/authStore";
// Stream G3: "Sign in with <IdP>" for org codes that have SSO (publicAuthApi.ssoPublicInfo / ssoStartUrl)
import { useEffect } from "react";
import MfaStep from "../components/MfaStep";          // S4: two-step sign-in
import type { SignInResult } from "../api/saas";
// WASA 2026-10-21: our own proof-of-work "I'm not a robot" box + the forced first-sign-in password change
import CaptchaWidget, { postWithCaptcha, type CaptchaHandle } from "../components/CaptchaWidget";
import ForcePasswordChange from "../components/ForcePasswordChange";

export default function LoginPage() {
  const nav = useNavigate();
  // G3: SSO button for the typed org code + the error an IdP round-trip may bring back
  const [sso, setSso] = useState<{ sso: boolean; required: boolean; display_name: string } | null>(null);
  useEffect(() => {
    // 2026-10-24: the signed UI grant did not verify -> the session was ended (store/authStore.signOutUnverified)
    if (new URLSearchParams(window.location.search).get("reason") === "unverified") {
      toast.error("Session could not be verified. Please sign in again.");
      window.history.replaceState({}, "", "/login");
    }
    const err = new URLSearchParams(window.location.search).get("sso_error");
    if (err) { toast.error(err); window.history.replaceState({}, "", "/login"); }
  }, []);
  // Demo credentials (scripts/seed_demo_canada.py, org MAPLE) are pre-filled on a LOCAL dev build only (never in a production bundle).
  const DEV = import.meta.env.DEV;
  const [orgCode, setOrgCode] = useState(DEV ? "MAPLE" : "");
  const [email, setEmail] = useState(DEV ? "admin@maple.example" : "");
  const [password, setPassword] = useState(DEV ? "Maple@Demo2026" : "");
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  // Platform-operator sign-in (superadmin) skips the org code — they sit above
  // the org layer. Toggling swaps in that mode's demo credentials so the tenant
  // email isn't left behind (which would fail as "org code required").
  const [platformMode, setPlatformMode] = useState(false);
  const [mfaStep, setMfaStep] = useState<SignInResult | null>(null);   // S4
  const [forced, setForced] = useState<SignInResult | null>(null);     // WASA #7: change the password first
  const captcha = useRef<CaptchaHandle | null>(null);
  const [captchaOk, setCaptchaOk] = useState(false);
  const togglePlatform = () => {
    setPlatformMode((v) => {
      const next = !v;
      if (next) { setEmail(DEV ? "lexora@lexora.io" : ""); setPassword(DEV ? "Super123!" : ""); }
      else { setEmail(DEV ? "admin@maple.example" : ""); setPassword(DEV ? "Maple@Demo2026" : ""); setOrgCode(DEV ? "MAPLE" : ""); }
      return next;
    });
  };
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  // Decision 2026-10-21: self sign-up is off unless the server opens it — no "free trial" link then.
  const [signupOpen, setSignupOpen] = useState(false);
  useEffect(() => { publicAuthApi.signupConfig().then((c) => setSignupOpen(c.enabled !== false)).catch(() => setSignupOpen(false)); }, []);
  useEffect(() => {
    if (platformMode || orgCode.length < 2) { setSso(null); return; }
    const t = setTimeout(() => publicAuthApi.ssoPublicInfo(orgCode).then(setSso).catch(() => setSso(null)), 350);
    return () => clearTimeout(t);
  }, [orgCode, platformMode]);

  const toggleTheme = () => {
    const next = !dark; setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("lx-theme", next ? "dark" : "light"); } catch { /* ignore */ }
  };

  const enter = async (t: SignInResult) => {
    useAuthStore.getState().setAuth(t.access_token!, t.refresh_token || null,
      { id: "", org_id: t.org_id, email, role: t.role });
    const me = await publicAuthApi.me();
    useAuthStore.getState().setUser(me);
    toast.success(`Welcome back, ${me.first_name || "there"}`);
    // Platform operator lands on the tenant console; everyone else on their dashboard.
    nav(t.role === "superadmin" ? "/platform" : "/");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const t = await postWithCaptcha(captcha.current, (ct) =>
        publicAuthApi.login(email, password, platformMode ? undefined : orgCode, ct)) as unknown as SignInResult;
      if (t.mfa_required || t.mfa_setup_required) { setMfaStep(t); return; }   // S4: second step first
      // WASA #7: this token only opens the change-password step — it is not stored.
      if (t.password_change_required) { setForced(t); return; }
      await enter(t);
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || "Sign in failed — check your credentials.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-4 py-12
                    bg-gradient-to-br from-primary-950 via-primary-700 to-primary-400">
      <div className="absolute top-5 right-5 z-20">
        <button onClick={toggleTheme}
          className="p-1.5 rounded-lg text-white/80 hover:text-white hover:bg-white/10 transition-colors"
          title="Toggle theme">
          {dark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>
      </div>

      <div className="w-full max-w-lg">
        <div className="bg-white rounded-2xl shadow-2xl p-8 sm:p-9">
          <div className="text-center mb-7">
            <Logo tagline className="h-20 sm:h-24 w-auto max-w-full mx-auto" />
          </div>

          <h2 className="font-display text-2xl font-bold text-gray-800 mb-1 text-center">
            {platformMode ? "Platform sign-in" : "Welcome back"}
          </h2>
          <p className="text-sm text-gray-500 mb-7 text-center">
            {platformMode ? "Lexora HRM operator — above the org layer" : "Sign in to continue"}
          </p>

          {forced ? <ForcePasswordChange result={forced} onDone={enter}
                      onCancel={() => { setForced(null); setMfaStep(null); setPassword(""); }} />
          : mfaStep ? <MfaStep result={mfaStep} email={email} onCancel={() => setMfaStep(null)} onForced={setForced} /> : (
          <form onSubmit={submit} className="space-y-4">
            {!platformMode && (
              <div>
                <label className="block text-xs font-semibold text-gray-500 mb-1">Organization code</label>
                <input className="input py-2.5 uppercase tracking-wider" value={orgCode} autoFocus required
                  onChange={(e) => setOrgCode(e.target.value.toUpperCase().trim())}
                  placeholder="e.g. MAPLE" autoComplete="off" />
              </div>
            )}
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1">Work email</label>
              <input className="input py-2.5" type="email" value={email} autoFocus={platformMode}
                onChange={(e) => setEmail(e.target.value)} placeholder="name@company.ca" required />
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 mb-1">Password</label>
              <div className="relative">
                <input className="input py-2.5 pr-10" type={showPwd ? "text" : "password"} value={password}
                  onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" required />
                <button type="button" onClick={() => setShowPwd(!showPwd)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                  {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
            {!platformMode && sso?.sso && (
              <a href={publicAuthApi.ssoStartUrl(orgCode)} className="btn-primary w-full py-3 text-sm font-semibold block text-center">
                Sign in with {sso.display_name}
              </a>
            )}
            {!platformMode && sso?.sso && sso.required && (
              <p className="text-[11px] text-gray-500 text-center">Your company requires single sign-on. The password form below is for admins only.</p>
            )}
            <CaptchaWidget ref={captcha} purpose="login" account={email} onReadyChange={setCaptchaOk} />
            <button type="submit" disabled={loading || !captchaOk} className={`${sso?.sso ? "btn-secondary" : "btn-primary"} w-full py-3 text-sm font-semibold`}>
              {loading ? "Signing in..." : sso?.sso ? "Sign in with password" : "Sign in"}
            </button>
            {!platformMode && (
              <Link to="/forgot-password" className="block text-center text-xs text-primary-600 hover:text-primary-700">
                Forgot your password?
              </Link>
            )}
            {!platformMode && signupOpen && (
              <Link to="/signup" className="block text-center text-xs text-gray-500 hover:text-primary-700">
                New to Lexora HRM? <span className="font-semibold text-primary-600">Start a free trial</span>
              </Link>
            )}
          </form>
          )}

          <div className="mt-6 pt-5 border-t border-surface-border space-y-2.5">
            {DEV && <div className="text-xs px-3 py-2.5 rounded-lg bg-gradient-to-r from-primary-50 to-teal-50 text-gray-600">
              {platformMode ? (
                <><span className="font-semibold text-primary-700">Platform</span> · lexora@lexora.io · Super123!
                  <span className="text-gray-400"> — Lexora HRM operator</span></>
              ) : (
                <><span className="font-semibold text-primary-700">Demo (local only)</span> · code <span className="font-mono">MAPLE</span> · admin@maple.example · Maple@Demo2026</>
              )}
            </div>}
            <button type="button" onClick={togglePlatform}
              className="w-full text-center text-[11px] text-gray-400 hover:text-primary-600 transition-colors">
              {platformMode ? "← Organization sign-in" : "Lexora HRM platform admin →"}
            </button>
          </div>
        </div>

        <p className="mt-6 text-center text-[11px] text-white/70">
          © {new Date().getFullYear()} Lexora HRM · Canadian HR &amp; payroll · All rights reserved
        </p>
        <DpoFooter orgCode={platformMode ? undefined : orgCode} />
        <p className="mt-2 text-center text-[11px] text-white/70">
          <Link to="/legal/privacy" className="hover:text-white">Privacy Policy</Link> · <Link to="/legal/terms" className="hover:text-white">Terms of Service</Link>
        </p>
      </div>
    </div>
  );
}
