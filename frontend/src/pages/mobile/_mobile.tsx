import React, { useEffect } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ChevronRight, LucideIcon } from "lucide-react";
import InstallPrompt from "../../pwa/InstallPrompt";
import AppModal from "../../components/Modal";
import { registerPwa } from "../../pwa/register";
import { wireAutoSync } from "../../pwa/queue";
import LanguagePicker from "../../i18n/LanguagePicker";
import ReadPage from "../../i18n/ReadPage";
export { useMe } from "../common/_shared";

/* Shared bits for the phone-first self-service screens (Stream G4).
   Designed at 390 px first: one column, big tap targets, no horizontal scroll; grows to a
   centred 28rem card on a desktop. */

export function MobileShell({ title, back = "/mobile/home", children, actions, wide = false }:
  { title: string; back?: string | null; children: React.ReactNode; actions?: React.ReactNode; wide?: boolean }) {
  useEffect(() => { registerPwa(); wireAutoSync(); }, []);
  return (
    <div className={`${wide ? "max-w-5xl" : "max-w-md"} mx-auto`}>
      <div className="flex items-center gap-2 mb-3">
        {back && <Link to={back} className="p-2 -ml-2 rounded-lg text-gray-500 hover:bg-gray-50" aria-label="Back"><ArrowLeft className="w-5 h-5" /></Link>}
        <h1 className="font-display text-lg font-bold text-gray-800 flex-1 min-w-0 truncate">{title}</h1>
        {actions}
        <ReadPage />
        <LanguagePicker compact />
      </div>
      <InstallPrompt />
      {children}
    </div>
  );
}

export function Tile({ to, icon: Icon, title, sub, color, onClick, badge }:
  { to?: string; icon: LucideIcon; title: string; sub?: string; color: string; onClick?: () => void; badge?: number | string | null }) {
  const inner = (
    <div className="card flex items-center gap-3 !py-3.5 !px-3.5 hover:shadow-md transition-shadow">
      <div className={`w-12 h-12 rounded-2xl ${color} grid place-items-center text-white shrink-0`}><Icon className="w-6 h-6" /></div>
      <div className="min-w-0 flex-1">
        <div className="text-[15px] font-semibold text-gray-800 truncate">{title}</div>
        {sub && <div className="text-xs text-gray-500 truncate">{sub}</div>}
      </div>
      {badge ? <span className="text-[11px] font-bold px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 shrink-0">{badge}</span> : null}
      <ChevronRight className="w-5 h-5 text-gray-300 shrink-0" />
    </div>
  );
  if (onClick) return <button type="button" className="text-left w-full" onClick={onClick}>{inner}</button>;
  return <Link to={to || "#"} className="block">{inner}</Link>;
}

export const BIG = "w-full py-4 rounded-2xl text-lg font-semibold inline-flex items-center justify-center gap-2 disabled:opacity-50 active:scale-[0.99]";
export const FIELD = "w-full rounded-xl border border-surface-border bg-white px-3 py-2.5 text-[15px]";
export const LABEL = "block text-xs font-semibold text-gray-500 mb-1";

/** A modal that uses the screen: full-screen sheet on phones, wide on desktop. */
// 2026-10-24: thin wrapper over the app-wide AppModal (full-height sheet on phones).
export function Sheet({ title, onClose, children, wide = true }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  return <AppModal title={title} onClose={onClose} size={wide ? "form" : "confirm"}>{children}</AppModal>;
}

/** Downscale a camera photo to a small JPEG data URL (keeps selfies well under the server limit). */
export function shrinkImage(file: File, max = 640, quality = 0.72): Promise<string> {
  return new Promise((res, rej) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
      const ctx = c.getContext("2d");
      if (!ctx) { URL.revokeObjectURL(url); return rej(new Error("no-canvas")); }
      ctx.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      res(c.toDataURL("image/jpeg", quality));
    };
    img.onerror = () => { URL.revokeObjectURL(url); rej(new Error("bad-image")); };
    img.src = url;
  });
}

/** An <img> that loads through the API (auth header) and shows a blob URL. */
export function AuthImg({ load, className, alt }: { load: () => Promise<string>; className?: string; alt?: string }) {
  const [src, setSrc] = React.useState<string | null>(null);
  const [bad, setBad] = React.useState(false);
  useEffect(() => { let u: string | null = null; load().then((x) => { u = x; setSrc(x); }).catch(() => setBad(true)); return () => { if (u) URL.revokeObjectURL(u); }; }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  if (bad) return <span className="text-[10px] text-gray-400">—</span>;
  if (!src) return <span className={`inline-block bg-gray-100 animate-pulse rounded ${className || ""}`} />;
  return <img src={src} className={className} alt={alt || ""} />;
}
