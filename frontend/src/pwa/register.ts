// PWA wiring (Stream G4): service worker registration, "new version" toast, install prompt.
import { createElement } from "react";
import toast from "react-hot-toast";

let deferredPrompt: any = null;
const listeners = new Set<() => void>();
function emit() { listeners.forEach((l) => l()); }

/** Subscribe to install-availability changes (returns unsubscribe). */
export function onInstallChange(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; }
export function canInstall(): boolean { return !!deferredPrompt; }
export function isStandalone(): boolean {
  try { return window.matchMedia("(display-mode: standalone)").matches || (navigator as any).standalone === true; } catch { return false; }
}
export async function promptInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  if (!deferredPrompt) return "unavailable";
  const p = deferredPrompt; deferredPrompt = null; emit();
  try { p.prompt(); const r = await p.userChoice; return r?.outcome === "accepted" ? "accepted" : "dismissed"; }
  catch { return "dismissed"; }
}

/** Wipe every offline cache (called on logout so no personal data stays in Cache Storage). */
export async function clearOfflineCaches() {
  try {
    if ("caches" in window) { const ks = await caches.keys(); await Promise.all(ks.map((k) => caches.delete(k))); }
    navigator.serviceWorker?.controller?.postMessage("CLEAR_CACHES");
  } catch { /* ignore */ }
}

let registered = false;
export function registerPwa() {
  if (registered) return; registered = true;
  window.addEventListener("beforeinstallprompt", (e: any) => { e.preventDefault(); deferredPrompt = e; emit(); });
  window.addEventListener("appinstalled", () => { deferredPrompt = null; emit(); toast.success("Lexora HRM is installed on this device."); });
  if (!("serviceWorker" in navigator)) return;
  // Vite dev serves /sw.js from public/ as-is; production copies it to dist/. Same path both ways.
  window.addEventListener("load", async () => {
    try {
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      const announce = (w: ServiceWorker) => {
        w.addEventListener("statechange", () => {
          if (w.state === "installed" && navigator.serviceWorker.controller) {
            toast((tt) => createElement("span", null, "A new version of Lexora HRM is ready. ",
              createElement("button", { style: { marginLeft: 8, fontWeight: 600, color: "#4338ca" },
                onClick: () => { w.postMessage("SKIP_WAITING"); toast.dismiss(tt.id); } }, "Reload")),
              { duration: 15000, id: "sw-update" });
          }
        });
      };
      if (reg.waiting) announce(reg.waiting);
      reg.addEventListener("updatefound", () => { if (reg.installing) announce(reg.installing); });
      let reloading = false;
      navigator.serviceWorker.addEventListener("controllerchange", () => { if (!reloading) { reloading = true; window.location.reload(); } });
    } catch (e) {
      // No service worker (http on a LAN IP, private mode): the app still works online.
    }
  });
}
