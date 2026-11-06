import { forwardRef, useCallback, useEffect, useId, useImperativeHandle, useRef, useState } from "react";
import { Check, Loader2 } from "lucide-react";
import { captchaCode, fetchChallenge, solvePow, type PowPurpose, type SolvedPow } from "../lib/pow/powClient";
import { Monogram } from "./Logo";

/**
 * Lexora HRM's own CAPTCHA (WASA hardening 2026-10-21) for the staff login, the
 * MFA step, forgot password and sign-up. The auth service decides the mode —
 * GET /auth/challenge answers {enabled, mode, interactive}:
 *
 *  - pow + interactive (default, CAPTCHA_VISIBLE=true): an "I'm not a robot"
 *    box in Lexora's look. Nothing is solved until a person ticks it with a
 *    real pointer / keyboard event (event.isTrusted — a scripted .click() does
 *    nothing). Ticking fetches a challenge, solves it in a Web Worker (spinner
 *    in the box) and shows a green tick "Verified". The token lives 2 minutes;
 *    then the box un-ticks itself: "Expired, tick again". One tick = one submit.
 *  - pow, not interactive: solved in the background; a one-line status says so.
 *  - off: renders nothing; getToken() resolves undefined.
 *
 * `onReadyChange(ok)` tells the form whether it may enable its submit button.
 * Honest limit: a browser-automation script can tick the box too — it is a
 * visible deterrent plus a work cost per try; rate limits and lockout remain
 * the real guard.
 */
export interface CaptchaHandle {
  /** A fresh token for this submit (undefined when the check is off). */
  getToken: () => Promise<string | undefined>;
  /** The server wants a fresh / harder one: same tick, longer spinner. */
  refresh: () => Promise<string | undefined>;
  /** Un-tick after a submit (a token works once). */
  reset: () => void;
  enabled: () => boolean;
}

type Mode = "loading" | "off" | "box" | "background";
type Tick = "unticked" | "solving" | "verified" | "expired" | "error";

interface Props {
  purpose: PowPurpose;
  /** The account being signed in, if known: the server raises the work after failures. */
  account?: string;
  onReadyChange?: (ok: boolean) => void;
}

const fresh = (s: SolvedPow | null) => !!s && s.expires * 1000 - Date.now() > 8_000;

const CaptchaWidget = forwardRef<CaptchaHandle, Props>(function CaptchaWidget({ purpose, account, onReadyChange }, ref) {
  const statusId = useId();
  const [mode, setMode] = useState<Mode>("loading");
  const [tick, setTick] = useState<Tick>("unticked");
  const [bg, setBg] = useState<"working" | "ready" | "error">("working");
  const ready = useRef<SolvedPow | null>(null);
  const alive = useRef(true);
  const expiryTimer = useRef<number | undefined>(undefined);
  const ticked = useRef(false);
  const solveSeq = useRef(0);
  const accountRef = useRef<string | undefined>(undefined);
  accountRef.current = account?.trim() || undefined;

  useEffect(() => {
    alive.current = true;
    fetchChallenge(purpose)
      .then((c) => { if (alive.current) setMode(!c.enabled || c.mode === "off" ? "off" : c.interactive ? "box" : "background"); })
      // An auth service that cannot answer: show the box anyway — the server decides on submit.
      .catch(() => { if (alive.current) setMode("box"); });
    return () => { alive.current = false; window.clearTimeout(expiryTimer.current); };
  }, [purpose]);

  const ok = mode === "off" || mode === "background" || (mode === "box" && tick === "verified");
  useEffect(() => { onReadyChange?.(ok); }, [ok, onReadyChange]);

  const clearBox = useCallback((next: Tick) => {
    solveSeq.current += 1;
    window.clearTimeout(expiryTimer.current);
    ready.current = null;
    ticked.current = false;
    setTick(next);
  }, []);

  const solve = useCallback(async (): Promise<SolvedPow | null> => {
    const seq = ++solveSeq.current;
    window.clearTimeout(expiryTimer.current);
    ready.current = null;
    setTick("solving");
    setBg("working");
    try {
      const s = await solvePow(purpose, accountRef.current);
      if (!alive.current || seq !== solveSeq.current) throw new Error("superseded");
      if (!s) { setMode("off"); return null; }
      ready.current = s;
      setTick("verified");
      setBg("ready");
      const ms = Math.max(5_000, (s.expires - 5) * 1000 - Date.now());
      expiryTimer.current = window.setTimeout(() => {
        if (!alive.current || seq !== solveSeq.current) return;
        if (ticked.current) clearBox("expired");
        else { ready.current = null; void solve().catch(() => undefined); }   // background: keep one in stock
      }, ms);
      return s;
    } catch (e) {
      if (alive.current && seq === solveSeq.current) { ticked.current = false; setTick("error"); setBg("error"); }
      throw e;
    }
  }, [purpose, clearBox]);

  // background mode: keep one solved token ready
  useEffect(() => {
    if (mode === "background") void solve().catch(() => undefined);
  }, [mode, solve]);

  const onBoxClick = useCallback((e: React.MouseEvent<HTMLButtonElement>) => {
    // Only a real pointer / keyboard action counts; element.click() is isTrusted=false.
    if (!e.nativeEvent.isTrusted) return;
    if (tick === "solving" || tick === "verified") return;
    ticked.current = true;
    void solve().catch(() => undefined);
  }, [tick, solve]);

  useImperativeHandle(ref, () => ({
    enabled: () => mode !== "off",
    getToken: async () => {
      if (mode === "off") return undefined;
      if (mode === "box" && !ticked.current) return undefined;        // button was disabled anyway
      let s = fresh(ready.current) ? ready.current : null;
      if (!s) s = await solve();
      ready.current = null;
      if (mode === "background") void solve().catch(() => undefined);   // next one in stock
      return s?.token;
    },
    refresh: async () => {
      if (mode === "off") return undefined;
      if (mode === "box" && !ticked.current) return undefined;
      const s = await solve();
      ready.current = null;
      return s?.token;
    },
    reset: () => { if (mode === "box") clearBox("unticked"); },
  }), [mode, solve, clearBox]);

  if (mode === "off" || mode === "loading") return mode === "loading" ? <div className="min-h-[58px]" aria-hidden="true" /> : null;

  if (mode === "background") {
    return (
      <p role="status" aria-live="polite" className="text-[11px] text-center text-gray-500 dark:text-gray-400 min-h-[16px]">
        {bg === "ready" ? <span className="text-emerald-600 dark:text-emerald-400">Verified you're human ✓</span>
          : bg === "error" ? <span className="text-amber-600">Security check is retrying…</span>
          : <span>Verifying you're human…</span>}
      </p>
    );
  }

  const checked = tick === "verified";
  const busy = tick === "solving";
  const line =
    tick === "solving" ? "Checking…"
    : tick === "verified" ? "Verified"
    : tick === "expired" ? "Expired, tick again"
    : tick === "error" ? "Could not verify, tick again"
    : "Tick the box to continue";
  const lineColor =
    tick === "verified" ? "text-emerald-600 dark:text-emerald-400"
    : tick === "expired" || tick === "error" ? "text-amber-600 dark:text-amber-400"
    : "text-gray-500 dark:text-gray-400";

  return (
    <div data-testid="captcha-box"
         className="flex items-center justify-between gap-3 rounded-lg border-2 border-primary-200 dark:border-primary-800 bg-primary-50/60 dark:bg-slate-800/70 px-3 py-2.5 shadow-sm">
      <button
        type="button"
        role="checkbox"
        aria-checked={checked}
        aria-busy={busy}
        aria-describedby={statusId}
        onClick={onBoxClick}
        className="group flex items-center gap-3 min-w-0 flex-1 text-left rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-slate-800"
      >
        <span className={
          "w-7 h-7 shrink-0 rounded-md border-2 flex items-center justify-center transition-colors " +
          (checked ? "border-emerald-500 bg-emerald-500 text-white"
            : busy ? "border-primary-300 bg-white dark:bg-slate-900"
            : "border-primary-400 dark:border-primary-500 bg-white dark:bg-slate-900 group-hover:border-primary-600")}>
          {busy && <Loader2 className="w-4 h-4 text-primary-600 animate-spin" aria-hidden="true" />}
          {checked && <Check className="w-5 h-5" strokeWidth={3} aria-hidden="true" />}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-gray-700 dark:text-gray-100 leading-tight">I'm not a robot</span>
          <span id={statusId} role="status" aria-live="polite" className={"block text-[11px] leading-tight mt-0.5 " + lineColor}>
            {line}
          </span>
        </span>
      </button>
      <div className="flex flex-col items-center shrink-0 select-none" aria-hidden="true">
        <span className="w-8 h-8 rounded-full bg-primary-950 flex items-center justify-center overflow-hidden">
          <Monogram className="w-7 h-7" title="Lexora HRM" />
        </span>
        <span className="text-[8px] font-bold uppercase tracking-[0.12em] text-primary-700 dark:text-primary-300 mt-0.5">Lexora</span>
        <span className="text-[8px] text-gray-400 dark:text-gray-500">Secure check</span>
      </div>
    </div>
  );
});

export default CaptchaWidget;

/** POST with a captcha_token from `h`. On captcha_harder / captcha_expired the
 *  widget re-solves under the same tick (longer spinner) and the call is retried ONCE. */
export async function postWithCaptcha<T>(h: CaptchaHandle | null, send: (captcha_token?: string) => Promise<T>): Promise<T> {
  const tok = h ? await h.getToken() : undefined;
  try {
    return await send(tok);
  } catch (err) {
    const c = captchaCode(err);
    if (!h || !h.enabled() || (c !== "captcha_harder" && c !== "captcha_expired")) throw err;
    return await send(await h.refresh());
  } finally {
    h?.reset();                // one tick = one submit
  }
}
