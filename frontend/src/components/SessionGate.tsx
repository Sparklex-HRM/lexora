import { useEffect } from "react";
import api from "../api/client";
import { Spinner } from "./ui";
import { signOutUnverified, useAuthStore, type User } from "../store/authStore";

/**
 * 2026-10-24 (HMS Entersoft HIGH "UI role tampering"): the signed-in shell renders only after the current token's
 * /auth/me answer carried a ui_grant that verified with a PINNED key (store/authStore.setUser). On a page reload
 * the tokens come back from storage but the role never does, so it is fetched + verified here first. A missing /
 * forged grant ends the session: "Session could not be verified".
 */
export default function SessionGate({ children }: { children: JSX.Element }) {
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const verifying = useAuthStore((s) => s.verifying);
  useEffect(() => {
    if (!token || user || verifying) return;
    const st = useAuthStore.getState();
    st.setVerifying(true);
    api.get<User>("/auth/me")
      .then((r) => useAuthStore.getState().setUser(r.data))
      .catch((e) => {
        useAuthStore.getState().setVerifying(false);
        if (e?.response?.status !== 401) signOutUnverified();   // 401 = the client's own "session ended" path
      });
  }, [token, user, verifying]);
  if (!user) {
    return <div className="min-h-[60vh] grid place-items-center"><Spinner className="h-10 w-10" /></div>;
  }
  return children;
}
