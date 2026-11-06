import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import toast from "react-hot-toast";
import { Camera, Coffee, LogIn, LogOut, MapPin, RefreshCw, Smartphone, WifiOff } from "lucide-react";
import { deviceSource, mobileApi, type MobilePunchIn, type PunchMeta } from "../../api/mobile";
import { errText } from "../../api/platform";
import { all as queueAll, enqueue, newClientId, onQueueChange, syncQueue, type QueuedPunch } from "../../pwa/queue";
import { useOnline } from "../../pwa/useOnline";
import { useLang, useT } from "../../i18n";
import { fmtTime, fmtWeekday } from "../../i18n/format";
import { BIG, MobileShell, shrinkImage } from "./_mobile";

type Dir = "in" | "out" | "break_start" | "break_end";
type Pos = { lat: number; lng: number; accuracy: number; mock: boolean; at: number };

function readPosition(): Promise<Pos> {
  return new Promise((res, rej) => {
    if (!navigator.geolocation) return rej(new Error("unsupported"));
    navigator.geolocation.getCurrentPosition(
      (p) => res({ lat: p.coords.latitude, lng: p.coords.longitude, accuracy: Math.round(p.coords.accuracy || 0),
        // Browsers do not expose "mock location" on purpose; some Android webviews set `mocked`. We pass what we get, honestly.
        mock: !!((p.coords as any).mocked || (p.coords as any).isMock || (p as any).mocked), at: Date.now() }),
      (e) => rej(e), { enableHighAccuracy: true, timeout: 12000, maximumAge: 15000 });
  });
}
function haversine(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const R = 6371000, r = Math.PI / 180, dLat = (b.lat - a.lat) * r, dLng = (b.lng - a.lng) * r;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLng / 2) ** 2;
  return Math.round(2 * R * Math.asin(Math.sqrt(x)));
}

/** /mobile/punch — GPS + geo-fence + selfie + device binding + offline queue. */
export default function MobilePunchPage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const online = useOnline();
  const [now, setNow] = useState(new Date());
  useEffect(() => { const i = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(i); }, []);

  const ctx = useQuery({ queryKey: ["m-punch-ctx"], queryFn: () => mobileApi.context(), refetchInterval: online ? 60000 : false });
  const [pos, setPos] = useState<Pos | null>(null);
  const [gps, setGps] = useState<"idle" | "wait" | "ok" | "denied" | "off">("idle");
  const [selfie, setSelfie] = useState<string | null>(null);
  const [queued, setQueued] = useState<QueuedPunch[]>([]);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refreshQueue = () => queueAll().then(setQueued);
  useEffect(() => { refreshQueue(); return onQueueChange(refreshQueue); }, []);
  const locate = async () => {
    setGps("wait");
    try { setPos(await readPosition()); setGps("ok"); }
    catch (e: any) { setPos(null); setGps(e?.code === 1 ? "denied" : "off"); }
  };
  useEffect(() => { locate(); }, []);
  useEffect(() => { if (online && queued.length) doSync(true); }, [online]);   // eslint-disable-line react-hooks/exhaustive-deps

  const settings = ctx.data?.settings;
  const sites = ctx.data?.sites || [];
  const fences = useQuery({ queryKey: ["m-sites"], queryFn: () => mobileApi.sites(), enabled: !!ctx.data });
  const nearest = useMemo(() => {
    const fs = (fences.data?.fences || []).filter((f) => f.active);
    if (!pos || !fs.length) return null;
    return fs.map((f) => ({ f, d: haversine(pos, f) })).sort((a, b) => a.d - b.d)[0];
  }, [pos, fences.data]);

  // Today's state = accepted punches (server) + punches still queued on the phone, in time order.
  const accepted: PunchMeta[] = (ctx.data?.today || []).filter((p) => !p.refused);
  const timeline = useMemo(() => [
    ...accepted.map((p) => ({ id: p.id, direction: p.direction, at: p.device_time || p.server_time || "", flags: p.flag_labels, late: p.synced_late, queued: false, refused: false })),
    ...queued.map((q) => ({ id: q.client_id, direction: q.direction, at: q.device_time, flags: [] as string[], late: false, queued: true, refused: false })),
  ].sort((a, b) => a.at.localeCompare(b.at)), [accepted, queued]);
  const last = timeline[timeline.length - 1];
  const state: "out" | "in" | "break" = !last || last.direction === "out" ? "out" : last.direction === "break_start" ? "break" : "in";
  const worked = useMemo(() => {
    let ms = 0, since: number | null = null;
    for (const p of timeline) { const tm = new Date(p.at).getTime(); if (p.direction === "in" || p.direction === "break_end") since = since ?? tm; else if (since !== null) { ms += tm - since; since = null; } }
    if (since !== null) ms += now.getTime() - since;
    const m = Math.max(0, Math.floor(ms / 60000)); return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
  }, [timeline, now]);

  const DIR: Record<Dir, string> = { in: t("punch.in"), out: t("punch.out"), break_start: t("punch.break_start"), break_end: t("punch.break_end") };
  const myDevice = (ctx.data?.my_devices || [])[0];

  const takeSelfie = async (f?: File) => {
    if (!f) return;
    try { setSelfie(await shrinkImage(f)); } catch { toast.error(t("punch.camera_no")); }
  };

  const punch = async (direction: Dir) => {
    if (settings?.selfie === "required" && !selfie) { toast.error(t("punch.selfie_required")); fileRef.current?.click(); return; }
    setBusy(true);
    let p = pos;
    if (!p || Date.now() - p.at > 20000) { try { p = await readPosition(); setPos(p); setGps("ok"); } catch { p = null; } }
    const dev = deviceSource();
    const body: MobilePunchIn = {
      client_id: newClientId(), direction, device_time: new Date().toISOString(),
      lat: p?.lat ?? null, lng: p?.lng ?? null, accuracy_m: p?.accuracy ?? null, mock_location: !!p?.mock,
      device_id: dev.id, device_label: dev.label, selfie_base64: selfie,
    };
    try {
      if (!navigator.onLine) throw Object.assign(new Error("offline"), { offline: true });
      const r = await mobileApi.punch(body);
      setSelfie(null);
      toast.success(t("punch.recorded", { what: DIR[direction], time: fmtTime(r.at || r.device_time, lang) }));
      if (r.inside_fence === false) toast(t("punch.outside_recorded"), { duration: 6000 });
      if (r.synced_late) toast(t("punch.late", { device: fmtTime(r.device_time, lang), server: fmtTime(r.server_time, lang) }), { duration: 6000 });
      qc.invalidateQueries({ queryKey: ["m-punch-ctx"] });
    } catch (e: any) {
      const noResponse = e?.offline || (!e?.response && e?.request) || e?.code === "ERR_NETWORK";
      if (noResponse) { await enqueue(body); setSelfie(null); toast(t("punch.queued"), { icon: "📴", duration: 5000 }); }
      else toast.error(t("punch.refused", { why: errText(e, t("common.error")) }), { duration: 8000 });
    } finally { setBusy(false); }
  };

  const doSync = async (quiet = false) => {
    const r = await syncQueue();
    if (!quiet || r.sent) toast.success(t("punch.synced", { n: r.sent }));
    for (const x of r.results) if (!x.ok && x.status && x.status < 500) toast.error(t("punch.refused", { why: x.error || "" }), { duration: 8000 });
    if (r.kept && !quiet) toast(t("punch.sync_kept", { n: r.kept }));
    qc.invalidateQueries({ queryKey: ["m-punch-ctx"] });
  };

  const stateText = state === "in" ? t("punch.state_in", { time: fmtTime(last?.at, lang) }) : state === "break" ? t("punch.state_break", { time: fmtTime(last?.at, lang) })
    : timeline.length ? t("punch.state_done", { time: fmtTime(last?.at, lang) }) : t("punch.state_out");

  return (
    <MobileShell title={t("punch.title")}>
      <div className="card text-center !py-4">
        <div className="text-xs text-gray-500">{fmtWeekday(now, lang)}</div>
        <div className="text-5xl font-bold text-gray-800 tabular-nums my-2">{now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</div>
        <div className={`inline-block px-3 py-1 rounded-full text-sm font-medium mb-3 ${state === "in" ? "bg-green-100 text-green-700" : state === "break" ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-600"}`} role="status">{stateText}</div>

        {/* location line */}
        <div className="text-xs text-gray-600 flex items-center justify-center gap-1.5 min-h-[1.5rem]">
          <MapPin className="w-3.5 h-3.5 text-gray-400" />
          {gps === "wait" ? t("punch.gps_wait") : gps === "denied" ? t("punch.gps_denied") : gps === "off" ? t("punch.gps_off")
            : pos && nearest ? (nearest.d <= nearest.f.radius_m ? <span className="text-green-700">{t("punch.inside", { site: nearest.f.name, m: nearest.d })}</span> : <span className="text-amber-700">{t("punch.outside", { site: nearest.f.name, m: nearest.d })}</span>)
            : pos ? (sites.length ? t("punch.accuracy", { m: pos.accuracy }) : t("punch.no_site")) : ""}
          <button onClick={locate} className="p-1 text-gray-400" aria-label="refresh location"><RefreshCw className="w-3.5 h-3.5" /></button>
        </div>
        {pos && <div className="text-[11px] text-gray-400">{t("punch.accuracy", { m: pos.accuracy })}{pos.mock && <span className="text-red-600"> · {t("punch.mock")}</span>}</div>}

        {/* selfie */}
        {settings && settings.selfie !== "off" && (
          <div className="mt-3 flex items-center justify-center gap-3">
            {selfie ? <img src={selfie} alt="selfie" className="w-16 h-16 rounded-xl object-cover border border-surface-border" /> : null}
            <button type="button" onClick={() => fileRef.current?.click()} className="inline-flex items-center gap-1.5 text-sm font-semibold px-3 py-2 rounded-xl border border-primary-200 bg-primary-50 text-primary-700">
              <Camera className="w-4 h-4" /> {selfie ? t("punch.selfie_retake") : t("punch.selfie")}
            </button>
            <input ref={fileRef} type="file" accept="image/*" capture="user" className="hidden" onChange={(e) => takeSelfie(e.target.files?.[0])} />
            <span className="text-[11px] text-gray-400">{settings.selfie === "required" ? t("punch.selfie_required") : t("punch.selfie_optional")}</span>
          </div>
        )}

        <div className="space-y-2 mt-4">
          {state === "out" && <button className={`${BIG} bg-primary-600 text-white`} disabled={busy} onClick={() => punch("in")}><LogIn className="w-5 h-5" /> {DIR.in}</button>}
          {state === "in" && <>
            <button className={`${BIG} bg-red-600 text-white`} disabled={busy} onClick={() => punch("out")}><LogOut className="w-5 h-5" /> {DIR.out}</button>
            <button className={`${BIG} !py-3 !text-base btn-secondary`} disabled={busy} onClick={() => punch("break_start")}><Coffee className="w-4 h-4" /> {DIR.break_start}</button>
          </>}
          {state === "break" && <button className={`${BIG} bg-primary-600 text-white`} disabled={busy} onClick={() => punch("break_end")}><LogIn className="w-5 h-5" /> {DIR.break_end}</button>}
        </div>
        <div className="mt-3 text-sm text-gray-600">{t("punch.worked")}: <b className="tabular-nums">{worked}</b></div>
        <div className="mt-2 text-[11px] text-gray-400 flex items-center justify-center gap-1">
          <Smartphone className="w-3 h-3" />
          {settings?.device_binding === false ? "" : myDevice ? (myDevice.status === "active" ? t("punch.device_ok") : t("punch.device_pending")) : t("punch.device_none")}
        </div>
      </div>

      {queued.length > 0 && (
        <div className="card mt-3 !py-3 flex items-center gap-2 bg-amber-50 border-amber-200">
          <WifiOff className="w-4 h-4 text-amber-700 shrink-0" />
          <div className="text-sm text-amber-800 flex-1">{t("punch.queued_count", { n: queued.length })}</div>
          <button className="text-sm font-semibold text-primary-700 disabled:opacity-50" disabled={!online} onClick={() => doSync(false)}>{t("punch.sync_now")}</button>
        </div>
      )}

      <h3 className="font-display font-semibold text-gray-800 mt-4 mb-2">{t("punch.today")}</h3>
      <div className="card !p-0 divide-y divide-surface-border">
        {timeline.length === 0 && <div className="py-6 text-center text-sm text-gray-400">—</div>}
        {[...timeline].reverse().map((p) => (
          <div key={p.id} className="px-3 py-2.5 flex items-center gap-3 text-sm">
            <span className="tabular-nums font-semibold w-14">{fmtTime(p.at, lang)}</span>
            <span className="flex-1">{DIR[p.direction as Dir] || p.direction}</span>
            {p.queued && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800">{t("punch.queued_count", { n: "" }).trim()}</span>}
            {p.late && <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-purple-100 text-purple-800">{t("team.late")}</span>}
            {p.flags.filter((f) => f !== "Synced late").map((f) => <span key={f} className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600">{f}</span>)}
          </div>
        ))}
      </div>
      {(ctx.data?.today || []).filter((p) => p.refused).length > 0 && (
        <div className="mt-3 text-xs text-red-700 space-y-1">
          {(ctx.data?.today || []).filter((p) => p.refused).map((p) => <div key={p.id}>{fmtTime(p.device_time, lang)} · {t("punch.refused", { why: p.refused_reason || "" })}</div>)}
        </div>
      )}
    </MobileShell>
  );
}
