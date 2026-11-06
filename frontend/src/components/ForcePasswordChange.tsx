import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import api from "../api/client";
import type { SignInResult } from "../api/saas";
import { errText } from "../lib/errText";
import { PUBLIC_AUTH } from "../api/publicAuth";

/**
 * WASA #7 (2026-10-21): the first sign-in of a login whose password someone
 * else chose (the platform "New tenant" admin). The sign-in token carries
 * "pwchg" — the gateway refuses it everywhere except the change-password
 * routes — so it is NOT stored: it is used here only, and the fresh token the
 * server returns after the change is what signs the person in.
 */
export default function ForcePasswordChange({ result, onDone, onCancel }: {
  result: SignInResult; onDone: (fresh: SignInResult) => void | Promise<void>; onCancel: () => void;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [policy, setPolicy] = useState<string>("");
  const auth = { headers: { Authorization: `Bearer ${result.access_token}` } };

  useEffect(() => {
    api.get<{ text: string }>(PUBLIC_AUTH.passwordPolicy).then((r) => setPolicy(r.data.text)).catch(() => undefined);
  }, []);

  const mismatch = confirm.length > 0 && next !== confirm;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (next !== confirm) { toast.error("The new passwords do not match"); return; }
    setBusy(true);
    try {
      const { data } = await api.post<SignInResult & { ok: boolean }>(PUBLIC_AUTH.changePassword,
        { current_password: current, new_password: next }, auth);
      if (!data.access_token) throw new Error("no token");
      toast.success("Password changed");
      await onDone(data);
    } catch (err) {
      toast.error(errText(err, "The password could not be changed"));
    } finally {
      setBusy(false);
    }
  };

  const type = show ? "text" : "password";
  return (
    <form onSubmit={submit} className="space-y-4" data-testid="force-password-change">
      <h3 className="font-display text-lg font-bold text-gray-800 flex items-center gap-2">
        <KeyRound className="w-5 h-5 text-primary-600" /> Choose a new password
      </h3>
      <p className="text-sm text-gray-500">
        Your password was set by someone else. Choose your own before you continue — only you will know it.
      </p>
      {policy && <p className="text-xs text-gray-600 bg-primary-50 border border-primary-100 rounded-lg p-2.5">{policy}</p>}
      <div>
        <label className="block text-xs font-semibold text-gray-500 mb-1">Current (temporary) password</label>
        <input className="input py-2.5" type={type} value={current} onChange={(e) => setCurrent(e.target.value)}
          autoComplete="current-password" required autoFocus />
      </div>
      <div>
        <label className="block text-xs font-semibold text-gray-500 mb-1">New password</label>
        <div className="relative">
          <input className="input py-2.5 pr-10" type={type} value={next} onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password" required />
          <button type="button" onClick={() => setShow(!show)} aria-label={show ? "Hide passwords" : "Show passwords"}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
            {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
      </div>
      <div>
        <label className="block text-xs font-semibold text-gray-500 mb-1">Confirm new password</label>
        <input className="input py-2.5" type={type} value={confirm} onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password" required aria-invalid={mismatch} />
        {mismatch && <p className="text-[11px] text-red-600 mt-1">The new passwords do not match.</p>}
      </div>
      <button type="submit" disabled={busy || !current || !next || mismatch} className="btn-primary w-full py-3 text-sm font-semibold">
        {busy ? "Saving…" : "Save and continue"}
      </button>
      <button type="button" onClick={onCancel} className="block w-full text-center text-xs text-gray-400 hover:text-primary-600">← Back to sign in</button>
    </form>
  );
}
