import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Link } from "react-router-dom";
import { ShieldCheck, Smartphone, KeyRound, MonitorSmartphone, LogOut, Lock } from "lucide-react";
import { PageHeader, Spinner, Badge } from "../../components/ui";
import { mfaApi, sessionApi, errText, type MfaSetup } from "../../api/saas";
import { securityApi } from "../../api/privacy";

const th = "px-3 py-2 text-left text-[11px] uppercase tracking-wide text-gray-400 font-semibold";
const td = "px-3 py-1.5 text-sm text-gray-700 border-t border-surface-border/70";
const fmt = (s: string | null) => (s ? new Date(s).toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short" }) : "—");

function Codes({ codes }: { codes: string[] }) {
  return (
    <div className="mt-2">
      <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1 mb-2">Save these recovery codes now — each works once and they are not shown again.</div>
      <div className="grid grid-cols-2 gap-1 font-mono text-sm bg-gray-50 border border-surface-border rounded-lg p-3">{codes.map((c) => <span key={c}>{c}</span>)}</div>
      <button className="mt-2 text-xs text-primary-700" onClick={() => { navigator.clipboard?.writeText(codes.join("\n")); toast.success("Copied"); }}>Copy all</button>
    </div>
  );
}

/** /security/account — my two-step sign-in, where I am signed in, my password. */
export default function AccountSecurityPage() {
  const qc = useQueryClient();
  const { data: st, isLoading } = useQuery({ queryKey: ["mfa"], queryFn: mfaApi.status });
  const { data: sess } = useQuery({ queryKey: ["sessions-active"], queryFn: () => sessionApi.active() });
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [pwForm, setPwForm] = useState({ current: "", next: "" });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["mfa"] }); qc.invalidateQueries({ queryKey: ["sessions-active"] }); };
  const onErr = (e: any) => toast.error(errText(e));

  const start = useMutation({ mutationFn: mfaApi.setup, onSuccess: setSetup, onError: onErr });
  const enable = useMutation({ mutationFn: () => mfaApi.enable(code), onSuccess: (r) => { setCodes(r.recovery_codes); setSetup(null); setCode(""); refresh(); toast.success("Two-step sign-in is on"); }, onError: onErr });
  const disable = useMutation({ mutationFn: () => mfaApi.disable(code, pw), onSuccess: () => { setCode(""); setPw(""); refresh(); toast.success("Two-step sign-in is off"); }, onError: onErr });
  const regen = useMutation({ mutationFn: () => mfaApi.newCodes(code), onSuccess: (r) => { setCodes(r.recovery_codes); setCode(""); refresh(); }, onError: onErr });
  const revoke = useMutation({ mutationFn: (id: string) => sessionApi.revoke(id), onSuccess: () => { refresh(); toast.success("Session ended"); }, onError: onErr });
  const others = useMutation({ mutationFn: sessionApi.revokeOthers, onSuccess: (r) => { refresh(); toast.success(`Signed out of ${r.ended} other session(s)`); }, onError: onErr });
  const changePw = useMutation({ mutationFn: () => securityApi.changePassword(pwForm.current, pwForm.next), onSuccess: () => { setPwForm({ current: "", next: "" }); refresh(); toast.success("Password changed — other sessions were signed out"); }, onError: onErr });

  if (isLoading || !st) return <div className="grid place-items-center py-16"><Spinner /></div>;
  return (
    <div>
      <PageHeader title="Account security" icon={ShieldCheck} subtitle="Two-step sign-in, where you are signed in, and your password"
        actions={<Link to="/security/sessions" className="btn-secondary inline-flex items-center gap-1.5 text-sm"><KeyRound className="w-4 h-4" />Sign-in history</Link>} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card">
          <div className="flex items-center gap-2 mb-2">
            <Smartphone className="w-4 h-4 text-primary-600" /><span className="font-display font-semibold text-gray-800">Two-step sign-in (authenticator app)</span>
            <span className="ml-auto">{st.enabled ? <Badge tone="green">On</Badge> : <Badge tone="amber">Off</Badge>}</span>
          </div>
          {st.required && <p className="text-xs text-gray-500 mb-2">Mandatory for your role ({st.required_roles.join(", ")}).</p>}
          {!st.enforced && <p className="text-xs text-amber-700 mb-2">Note: mandatory enforcement is switched off on this server (LEXORA_MFA_ENFORCE=0).</p>}
          {st.enabled ? (
            <div className="space-y-2 text-sm">
              <div className="text-gray-600">Turned on {fmt(st.enrolled_at)} · last used {fmt(st.last_used_at)} · <b>{st.recovery_codes_left}</b> recovery codes left</div>
              <input className="input py-2 font-mono tracking-widest" placeholder="Current 6-digit code" value={code} onChange={(e) => setCode(e.target.value.trim())} />
              <div className="flex flex-wrap gap-2">
                <button className="btn-secondary text-xs px-3 py-1.5" disabled={!code || regen.isPending} onClick={() => regen.mutate()}><KeyRound className="w-3.5 h-3.5 inline mr-1" />New recovery codes</button>
                {!st.required && <>
                  <input className="input py-1.5 text-xs w-40" type="password" placeholder="Password" value={pw} onChange={(e) => setPw(e.target.value)} />
                  <button className="text-xs px-3 py-1.5 rounded-lg border border-red-200 text-red-700" disabled={!code || !pw} onClick={() => disable.mutate()}>Turn off</button>
                </>}
              </div>
            </div>
          ) : setup ? (
            <div className="space-y-2">
              <p className="text-sm text-gray-600">Add Lexora HRM to your authenticator app, then type the code it shows.</p>
              {setup.qr_svg && <div className="w-40 h-40 [&>svg]:w-full [&>svg]:h-full" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />}
              <div className="text-xs bg-gray-50 border border-surface-border rounded p-2">Setup key: <span className="font-mono select-all">{setup.secret_grouped}</span></div>
              <div className="flex gap-2">
                <input className="input py-2 font-mono tracking-widest" placeholder="123456" value={code} onChange={(e) => setCode(e.target.value.trim())} />
                <button className="btn-primary px-4 text-sm" disabled={code.length < 6 || enable.isPending} onClick={() => enable.mutate()}>Turn on</button>
              </div>
            </div>
          ) : (
            <button className="btn-primary px-4 py-2 text-sm" onClick={() => start.mutate()}>Set up two-step sign-in</button>
          )}
          {codes && <Codes codes={codes} />}
        </div>

        <div className="card">
          <div className="flex items-center gap-2 mb-2"><Lock className="w-4 h-4 text-primary-600" /><span className="font-display font-semibold text-gray-800">Change password</span></div>
          <div className="space-y-2">
            <input className="input py-2" type="password" placeholder="Current password" value={pwForm.current} onChange={(e) => setPwForm({ ...pwForm, current: e.target.value })} />
            <input className="input py-2" type="password" placeholder="New password" value={pwForm.next} onChange={(e) => setPwForm({ ...pwForm, next: e.target.value })} />
            <p className="text-[11px] text-gray-400">10+ characters with upper and lower case, a number and a symbol; not a breached password; not one of your last 5. Changing it signs out your other sessions.</p>
            <button className="btn-primary px-4 py-2 text-sm" disabled={!pwForm.current || !pwForm.next || changePw.isPending} onClick={() => changePw.mutate()}>Change password</button>
          </div>
        </div>
      </div>

      <div className="card !p-0 mt-4">
        <div className="px-4 py-2.5 border-b border-surface-border flex items-center gap-2">
          <MonitorSmartphone className="w-4 h-4 text-primary-600" /><span className="font-display font-semibold text-gray-800">Where you are signed in</span>
          <button className="ml-auto text-xs rounded-lg border border-surface-border px-3 py-1.5 inline-flex items-center gap-1" onClick={() => others.mutate()}><LogOut className="w-3.5 h-3.5" />Sign out everywhere else</button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead><tr><th className={th}>Device</th><th className={th}>IP address</th><th className={th}>How</th><th className={th}>Signed in</th><th className={th}>Expires</th><th className={th}></th></tr></thead>
            <tbody>{(sess?.items || []).map((s) => (
              <tr key={s.id}>
                <td className={td}>{s.device}{s.current && <Badge tone="teal"> this device</Badge>}</td>
                <td className={`${td} font-mono text-xs`}>{s.ip || "—"}</td>
                <td className={td}>{s.method}</td>
                <td className={td}>{fmt(s.created_at)}</td>
                <td className={td}>{fmt(s.expires_at)}</td>
                <td className={`${td} text-right`}>{!s.current && <button className="text-xs text-red-700 hover:underline" onClick={() => revoke.mutate(s.id)}>Sign out</button>}</td>
              </tr>))}
              {!sess?.items?.length && <tr><td colSpan={6} className={`${td} text-center text-gray-400`}>No active sessions recorded (sessions started before this update are not listed).</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
