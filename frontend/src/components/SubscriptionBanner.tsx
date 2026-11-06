import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, Clock, CheckCircle2, Circle } from "lucide-react";
import api from "../api/client";
import { signupApi, type Banner } from "../api/saas";
import { useAuthStore, ADMIN_ROLES } from "../store/authStore";
import { useCan } from "../config/permissions";   // 2026-10-26: UI gates = permission + role (custom roles)

/** S4: trial days left / read-only strip shown on every screen (from /auth/me). */
export default function SubscriptionBanner() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const canBilling = useCan("settings.view", ADMIN_ROLES);
  const { data } = useQuery({
    queryKey: ["me-subscription"], enabled: !!role && role !== "superadmin", staleTime: 5 * 60_000,
    queryFn: () => api.get<{ subscription: { banner: Banner | null } | null }>("/auth/me").then((r) => r.data.subscription),
  });
  const b = data?.banner;
  if (!b) return null;
  const tone = b.level === "error" ? "bg-red-50 border-red-200 text-red-800"
    : b.level === "warning" ? "bg-amber-50 border-amber-200 text-amber-900" : "bg-primary-50 border-primary-200 text-primary-900";
  return (
    <div className={`border-b px-4 md:px-6 py-2 text-xs sm:text-sm flex items-start gap-2 ${tone}`} role="status" data-testid="subscription-banner">
      {b.level === "error" ? <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> : <Clock className="w-4 h-4 mt-0.5 shrink-0" />}
      <span className="flex-1">{b.text}</span>
      {canBilling && <Link to="/plan-billing" className="font-semibold underline whitespace-nowrap">Plan &amp; billing</Link>}
    </div>
  );
}

/** S4: the new-org setup checklist (org admins, until everything is done). */
export function SetupChecklistCard() {
  const role = useAuthStore((s) => s.user?.role) || "";
  const canSetup = useCan("settings.edit", ADMIN_ROLES);
  const { data, refetch } = useQuery({ queryKey: ["setup-checklist"], queryFn: signupApi.checklist, enabled: canSetup && role !== "superadmin" });
  if (!data || data.done === data.total) return null;
  return (
    <div className="card mb-4" data-testid="setup-checklist">
      <div className="flex items-center gap-2 mb-2">
        <span className="font-display font-semibold text-gray-800">Get your organization ready</span>
        <span className="text-xs text-gray-400">{data.done} of {data.total} done</span>
        <div className="ml-auto w-32 h-1.5 rounded bg-gray-100 overflow-hidden"><div className="h-full bg-primary-500" style={{ width: `${data.pct}%` }} /></div>
      </div>
      <ul className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1">
        {data.items.map((i) => (
          <li key={i.key} className="flex items-center gap-2 text-sm">
            {i.done ? <CheckCircle2 className="w-4 h-4 text-emerald-600" /> : <Circle className="w-4 h-4 text-gray-300" />}
            <Link to={i.link} className={i.done ? "text-gray-400 line-through" : "text-gray-700 hover:text-primary-700"}>{i.label}</Link>
            {!i.done && i.action === "seed_defaults" && (
              <button className="text-[11px] text-primary-700 underline" onClick={() => signupApi.seedDefaults().then(() => refetch())}>add now</button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
