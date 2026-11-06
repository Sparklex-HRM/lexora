import { useEffect, useState } from "react";
import { publicAuthApi } from "../api/publicAuth";   // WASA 2026-10-24: no API map on /login

/** Login-page footer: the org's published Privacy Officer contact (PIPEDA Principle 4.1 — accountability),
 *  falling back to Lexora HRM's own contact as service provider. Public endpoint; no token needed. */
export default function DpoFooter({ orgCode }: { orgCode?: string }) {
  const [info, setInfo] = useState<any>(null);
  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      publicAuthApi.loginInfo(orgCode || undefined).then((d) => { if (alive) setInfo(d); }).catch(() => { if (alive) setInfo(null); });
    }, 400);
    return () => { alive = false; clearTimeout(t); };
  }, [orgCode]);
  const c = info?.contact;
  const p = info?.platform_contact;
  return (
    <div className="mt-3 text-center text-[11px] text-white/70 leading-relaxed">
      {c ? (
        <>Privacy Officer{info?.org_name ? ` · ${info.org_name}` : ""}: <span className="text-white/90">{c.dpo_name || c.grievance_officer_name}</span>
          {(c.dpo_email || c.grievance_officer_email) && <> · <a className="underline" href={`mailto:${c.dpo_email || c.grievance_officer_email}`}>{c.dpo_email || c.grievance_officer_email}</a></>}
          {c.dpo_phone && <> · {c.dpo_phone}</>}</>
      ) : (
        <>Service provider: Lexora HRM
          {p?.dpo_email ? <> · Privacy Officer <a className="underline" href={`mailto:${p.dpo_email}`}>{p.dpo_email}</a></> : null}
          {p?.grievance_email ? <> · Privacy questions <a className="underline" href={`mailto:${p.grievance_email}`}>{p.grievance_email}</a></> : null}
          {!p?.dpo_email && !p?.grievance_email ? <> · ask your employer's HR for its Privacy Officer</> : null}</>
      )}
      <div className="text-white/50">{info?.law || "PIPEDA (Personal Information Protection and Electronic Documents Act)"} · access requests answered within 30 days</div>
    </div>
  );
}
