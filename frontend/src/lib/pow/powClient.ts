/**
 * Our own CAPTCHA client (WASA hardening 2026-10-21): fetch a signed
 * challenge from GET /auth/challenge, solve it in a Web Worker, return the
 * captcha_token. Falls back to solving on the main thread in small slices when
 * Workers are unavailable — slower to paint, still correct.
 */
import api from "../../api/client";
import { encodePowToken, searchPow, type PowChallenge } from "./powCore";
import { PUBLIC_AUTH } from "../../api/publicAuth";

export type PowPurpose = "login" | "mfa" | "forgot" | "signup";

/** GET /auth/challenge also says whether the check is on at all. */
export interface ChallengeReply extends Partial<PowChallenge> {
  enabled: boolean;
  mode: "pow" | "off";
  interactive: boolean;
}

export interface SolvedPow {
  token: string;
  expires: number;     // unix seconds
  ms: number;          // solve time
  difficulty: number;
}

let worker: Worker | null = null;
let workerBroken = false;
let seq = 0;
const pending = new Map<number, (r: { number: number; ms: number }) => void>();

function getWorker(): Worker | null {
  if (workerBroken || typeof Worker === "undefined") return null;
  if (worker) return worker;
  try {
    worker = new Worker(new URL("./powWorker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (ev: MessageEvent<{ id: number; number: number; ms: number }>) => {
      const cb = pending.get(ev.data.id);
      pending.delete(ev.data.id);
      cb?.({ number: ev.data.number, ms: ev.data.ms });
    };
    worker.onerror = () => {
      workerBroken = true;
      worker = null;
      for (const [, cb] of pending) cb({ number: -2, ms: 0 });   // callers fall back to the main thread
      pending.clear();
    };
    return worker;
  } catch {
    workerBroken = true;
    return null;
  }
}

function solveOnMainThread(salt: string, difficulty: number): Promise<{ number: number; ms: number }> {
  const t0 = performance.now();
  return new Promise((resolve) => {
    let start = 0;
    const step = () => {
      const n = searchPow(salt, difficulty, start, start + 20_000);
      if (n >= 0) return resolve({ number: n, ms: Math.round(performance.now() - t0) });
      start += 20_000;
      setTimeout(step, 0);
    };
    step();
  });
}

async function solveChallenge(ch: PowChallenge): Promise<{ number: number; ms: number }> {
  const w = getWorker();
  if (w) {
    const id = ++seq;
    const r = await new Promise<{ number: number; ms: number }>((resolve) => {
      pending.set(id, resolve);
      w.postMessage({ id, salt: ch.salt, difficulty: ch.difficulty });
    });
    if (r.number !== -2) return r;
  }
  return solveOnMainThread(ch.salt, ch.difficulty);
}

export async function fetchChallenge(purpose: PowPurpose, account?: string): Promise<ChallengeReply> {
  const params: Record<string, string> = { purpose };
  if (account) params.account = account;
  const { data } = await api.get<ChallengeReply>(PUBLIC_AUTH.powChallenge, { params });
  return data;
}

/** Fetch + solve one challenge. `account` (optional) lets the server raise the
 *  work for an account with recent failures. null = the check is off. */
export async function solvePow(purpose: PowPurpose, account?: string): Promise<SolvedPow | null> {
  const data = await fetchChallenge(purpose, account);
  if (!data.enabled || data.mode === "off" || !data.salt) return null;
  const ch = data as PowChallenge;
  const r = await solveChallenge(ch);
  if (r.number < 0) throw new Error("pow unsolved");
  return { token: encodePowToken(ch, r.number), expires: ch.expires, ms: r.ms, difficulty: ch.difficulty };
}

/** The refusal code of a CAPTCHA answer ({"detail", "code"}, HTTP 400), if any. */
export function captchaCode(err: unknown): string | undefined {
  const d = (err as { response?: { data?: { code?: string; detail?: { code?: string } } } })?.response?.data;
  const code = d?.code ?? (typeof d?.detail === "object" && d?.detail ? d.detail.code : undefined);
  return typeof code === "string" && code.startsWith("captcha_") ? code : undefined;
}

/** True for a refused CAPTCHA that a fresh solve can fix. */
export function isCaptchaRefusal(err: unknown): boolean {
  const c = captchaCode(err);
  return c === "captcha_harder" || c === "captcha_expired" || c === "captcha_invalid"
    || c === "captcha_required" || c === "captcha_not_ticked";
}
