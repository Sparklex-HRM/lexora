import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import toast from "react-hot-toast";
import Logo from "../../components/Logo";
import { Spinner } from "../../components/ui";
import { authApi } from "../../api/endpoints";
import { useAuthStore } from "../../store/authStore";
import { chatApi, errText, esignApi, ssoApi } from "../../api/integrations";

const Shell = ({ children }: { children: React.ReactNode }) => (
  <div className="min-h-screen flex items-start justify-center px-4 py-8 bg-gradient-to-br from-primary-950 via-primary-700 to-primary-400">
    <div className="w-full max-w-5xl bg-white rounded-2xl shadow-2xl p-5 sm:p-7">
      <Logo className="h-10 w-auto mb-4" />
      {children}
    </div>
  </div>
);

/** /sign/:token — the signer's page (no login). */
export function SignPage() {
  const { token = "" } = useParams();
  const { data, error, refetch } = useQuery({ queryKey: ["sign", token], queryFn: () => esignApi.pub(token), retry: false });
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [agree, setAgree] = useState(false);
  const [sentMsg, setSentMsg] = useState<string | null>(null);
  const send = useMutation({ mutationFn: () => esignApi.sendCode(token),
    onSuccess: (r) => { setSentMsg(r.message + (r.dev_code ? ` Test set-up only — code: ${r.dev_code}` : "")); if (r.dev_code) setCode(r.dev_code); },
    onError: (e: any) => toast.error(errText(e, "Could not send the code")) });
  const sign = useMutation({ mutationFn: () => esignApi.sign(token, { code: code.trim(), typed_name: name.trim(), agree }),
    onSuccess: (r) => { toast.success(r.message); refetch(); }, onError: (e: any) => toast.error(errText(e, "Could not sign")) });
  const decline = useMutation({ mutationFn: () => esignApi.decline(token, prompt("Why are you declining? (optional)") || ""),
    onSuccess: () => { toast("Declined"); refetch(); }, onError: (e: any) => toast.error(errText(e, "Could not decline")) });
  if (error) return <Shell><p className="text-sm text-red-600">{errText(error, "This signing link is not valid.")}</p></Shell>;
  if (!data) return <Shell><div className="grid place-items-center py-10"><Spinner /></div></Shell>;
  return (
    <Shell>
      <h1 className="font-display text-xl font-bold text-gray-800">{data.title}</h1>
      <p className="text-sm text-gray-500 mb-3">For {data.signer_name} · {data.kind} · status <b>{data.status}</b></p>
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3">
          <iframe title="document" src={esignApi.pubPdfUrl(token)} className="w-full h-[60vh] border border-surface-border rounded-lg" />
          <p className="text-[11px] text-gray-400 mt-1">File fingerprint (SHA-256): <code className="break-all">{data.pdf_sha256}</code></p>
        </div>
        <div className="lg:col-span-2 space-y-3 text-sm">
          {data.status === "signed" && <div className="p-3 rounded-lg bg-green-50 border border-green-200 text-green-800">
            Signed on {data.signed_at ? new Date(data.signed_at).toLocaleString() : ""}. <a className="underline" href={esignApi.pubPdfUrl(token)} target="_blank" rel="noreferrer">Download your signed copy</a>.</div>}
          {data.status === "declined" && <div className="p-3 rounded-lg bg-red-50 border border-red-200 text-red-800">You declined to sign this document.</div>}
          {data.status === "sent" && (<>
            <div className="p-3 rounded-lg border border-surface-border">
              <p className="text-xs text-gray-600 mb-2">Step 1 — we send a one-time code to your {data.contact}.</p>
              <button className="btn-secondary text-xs" disabled={send.isPending} onClick={() => send.mutate()}>Send me the code</button>
              {sentMsg && <p className="text-xs mt-2 text-gray-700">{sentMsg}</p>}
            </div>
            <div className="p-3 rounded-lg border border-surface-border space-y-2">
              <p className="text-xs text-gray-600">Step 2 — enter the code and type your full name.</p>
              <input className="input font-mono tracking-widest" value={code} onChange={(e) => setCode(e.target.value)} placeholder="6-digit code" maxLength={6} />
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your full name" />
              <label className="flex items-start gap-2 text-xs text-gray-700 cursor-pointer">
                <input type="checkbox" className="mt-0.5" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
                I have read the document and agree to sign it electronically. My name, contact, IP address and the time will be recorded.</label>
              <div className="flex gap-2">
                <button className="btn-primary text-xs flex-1 disabled:opacity-60" disabled={!code || !name || !agree || sign.isPending} onClick={() => sign.mutate()}>Sign</button>
                <button className="btn-secondary text-xs" onClick={() => decline.mutate()}>Decline</button>
              </div>
            </div>
          </>)}
          <p className="text-[11px] text-gray-400">{data.legal_note}</p>
        </div>
      </div>
    </Shell>
  );
}

/** /sso/done?code=… — swaps the one-time code for the normal session. */
export function SsoDonePage() {
  const [sp] = useSearchParams();
  const nav = useNavigate();
  const [msg, setMsg] = useState("Finishing your sign-in…");
  useEffect(() => {
    const code = sp.get("code") || "";
    (async () => {
      try {
        const t = await ssoApi.exchange(code);
        useAuthStore.getState().setAuth(t.access_token, t.refresh_token, { id: "", org_id: t.org_id, email: "", role: t.role });
        const me = await authApi.me();
        useAuthStore.getState().setUser(me);
        toast.success(`Welcome, ${me.first_name || "there"}`);
        nav("/", { replace: true });
      } catch (e: any) {
        setMsg(errText(e, "The sign-in could not be completed. Try again."));
      }
    })();
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  return <Shell><div className="py-10 text-center text-sm text-gray-700">{msg}{msg.startsWith("Finishing") && <div className="mt-4 grid place-items-center"><Spinner /></div>}
    {!msg.startsWith("Finishing") && <div className="mt-4"><a className="text-primary-600 underline" href="/login">Back to sign-in</a></div>}</div></Shell>;
}

/** /me/chat-link — a signed-in person links their Slack / Teams account. */
export function ChatLinkPage() {
  const { data: cmds } = useQuery({ queryKey: ["chat", "commands"], queryFn: chatApi.commands });
  const m = useMutation({ mutationFn: chatApi.linkCode, onError: (e: any) => toast.error(errText(e, "Could not get a code")) });
  return (
    <div className="max-w-3xl">
      <h1 className="font-display text-xl font-bold text-gray-800 mb-1">Chat link (Slack / Teams)</h1>
      <p className="text-sm text-gray-500 mb-4">Link your chat account once; then use the commands below from Slack or Teams. Works only if your admin switched the connector on.</p>
      <div className="card mb-4">
        <button className="btn-primary text-xs" disabled={m.isPending} onClick={() => m.mutate()}>Get a link code</button>
        {m.data && <div className="mt-3 text-sm">
          <p>In Slack or Teams type: <code className="px-2 py-1 bg-surface rounded font-mono">/lexora link {m.data.code}</code></p>
          <p className="text-xs text-gray-400 mt-1">The code works for {Math.round(m.data.expires_in_seconds / 60)} minutes.</p></div>}
      </div>
      <div className="card text-sm">
        <table className="w-full text-xs"><tbody>{(cmds?.commands || []).map((c) => (
          <tr key={c.command} className="border-t border-surface-border first:border-t-0"><td className="py-1.5 pr-3 font-mono whitespace-nowrap">/lexora {c.command}</td><td className="py-1.5 text-gray-600">{c.what}</td></tr>))}</tbody></table>
      </div>
    </div>
  );
}
