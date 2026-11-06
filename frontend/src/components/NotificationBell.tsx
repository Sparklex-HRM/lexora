import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CheckCheck } from "lucide-react";
import { notificationsApi, when, type Note } from "../api/platform";

/** Header bell: unread count + the latest few. Refreshes every minute. */
export default function NotificationBell() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const { data } = useQuery({
    queryKey: ["ntf", "bell"], queryFn: notificationsApi.unreadCount,
    refetchInterval: 60_000, refetchOnWindowFocus: true, retry: false, staleTime: 15_000,
  });
  const refresh = () => qc.invalidateQueries({ queryKey: ["ntf"] });
  const readAll = useMutation({ mutationFn: () => notificationsApi.readAll(), onSuccess: refresh });

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);

  const unread = data?.unread ?? 0;
  const openNote = async (n: Note) => {
    setOpen(false);
    if (!n.read) { try { await notificationsApi.read(n.id); } catch { /* still navigate */ } refresh(); }
    nav(n.link && n.link.startsWith("/") ? n.link : "/notifications");
  };

  return (
    <div className="relative shrink-0" ref={box}>
      <button onClick={() => setOpen((o) => !o)} aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open} title="Notifications"
        className="relative p-2 sm:p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-50">
        <Bell className="w-5 h-5 sm:w-4 sm:h-4" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-bold leading-4 text-center tabular-nums">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="fixed inset-x-2 top-12 sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-96 z-[70] card !p-0 shadow-2xl">
          <div className="flex items-center justify-between px-3 py-2 border-b border-surface-border">
            <span className="text-sm font-semibold text-gray-800">Notifications{unread ? ` · ${unread} unread` : ""}</span>
            <button className="text-xs text-primary-600 hover:underline inline-flex items-center gap-1 disabled:opacity-40"
              disabled={!unread || readAll.isPending} onClick={() => readAll.mutate()}>
              <CheckCheck className="w-3.5 h-3.5" /> Mark all read
            </button>
          </div>
          <div className="max-h-[60vh] overflow-y-auto divide-y divide-surface-border">
            {!data?.latest?.length ? <div className="px-3 py-8 text-center text-sm text-gray-400">No notifications yet.</div>
              : data.latest.map((n) => (
                <button key={n.id} onClick={() => openNote(n)} className="w-full text-left px-3 py-2.5 hover:bg-surface/60 flex gap-2">
                  <span className={`mt-1.5 w-2 h-2 rounded-full shrink-0 ${n.read ? "bg-transparent" : "bg-primary-500"}`} aria-hidden="true" />
                  <span className="min-w-0">
                    <span className={`block text-sm truncate ${n.read ? "text-gray-600" : "text-gray-900 font-semibold"}`}>{n.title}</span>
                    <span className="block text-[11px] text-gray-400">{n.kind_label} · {when(n.created_at)}</span>
                  </span>
                </button>
              ))}
          </div>
          <button className="w-full px-3 py-2.5 text-sm font-medium text-primary-600 hover:bg-surface/60 border-t border-surface-border"
            onClick={() => { setOpen(false); nav("/notifications"); }}>See all notifications</button>
        </div>
      )}
    </div>
  );
}
