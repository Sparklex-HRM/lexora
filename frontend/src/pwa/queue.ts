// Offline punch queue (Stream G4) — IndexedDB, synced in device-time order when back online.
//
// What is stored, and why it is minimal: client_id (random), direction, the phone's time, GPS
// (lat/lng/accuracy), the mock-location flag, a hashed-at-server device id, and — only if the
// person took one — the selfie image. No name, no employee number, no token. A punch is
// deleted from the phone the moment the server accepts (or finally refuses) it. The queue is
// wiped on logout (clearQueue). It is NOT encrypted at rest beyond what the OS does for app
// storage; the honest mitigation is that nothing identifying is kept and it is short-lived.
import { mobileApi, type MobilePunchIn, type SyncResult } from "../api/mobile";

const DB = "lx-punch-queue", STORE = "punches";
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
export function onQueueChange(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; }

function open(): Promise<IDBDatabase> {
  return new Promise((res, rej) => {
    if (!("indexedDB" in window)) return rej(new Error("no-idb"));
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => { const d = r.result; if (!d.objectStoreNames.contains(STORE)) d.createObjectStore(STORE, { keyPath: "client_id" }); };
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}
function tx<T>(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T> | IDBRequest): Promise<T> {
  return open().then((d) => new Promise<T>((res, rej) => {
    const t = d.transaction(STORE, mode); const req = fn(t.objectStore(STORE));
    req.onsuccess = () => res(req.result as T); req.onerror = () => rej(req.error);
  }));
}

export type QueuedPunch = MobilePunchIn & { queued_at: string; attempts?: number; last_error?: string };

export const newClientId = () => (crypto?.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);

export async function enqueue(p: MobilePunchIn): Promise<void> {
  await tx("readwrite", (s) => s.put({ ...p, queued_at: new Date().toISOString(), attempts: 0 } as QueuedPunch));
  emit();
}
export async function all(): Promise<QueuedPunch[]> {
  try { const rows = await tx<QueuedPunch[]>("readonly", (s) => s.getAll()); return rows.sort((a, b) => (a.device_time || "").localeCompare(b.device_time || "")); }
  catch { return []; }
}
export async function count(): Promise<number> { return (await all()).length; }
export async function remove(id: string) { await tx("readwrite", (s) => s.delete(id)); emit(); }
export async function clearQueue() { try { await tx("readwrite", (s) => s.clear()); } catch { /* ignore */ } emit(); }

let syncing = false;
/** Send everything queued, oldest first. Returns per-punch results; keeps items the network could not deliver. */
export async function syncQueue(): Promise<{ sent: number; kept: number; results: SyncResult[] }> {
  if (syncing || !navigator.onLine) return { sent: 0, kept: 0, results: [] };
  syncing = true;
  try {
    const rows = await all();
    if (!rows.length) return { sent: 0, kept: 0, results: [] };
    const results: SyncResult[] = [];
    for (let i = 0; i < rows.length; i += 25) {
      const batch = rows.slice(i, i + 25);
      let r;
      try { r = await mobileApi.sync(batch.map(({ queued_at, attempts, last_error, ...p }) => p)); }
      catch (e: any) {
        // Network / server down: keep the batch, remember the attempt, stop for now.
        for (const q of batch) await tx("readwrite", (s) => s.put({ ...q, attempts: (q.attempts || 0) + 1, last_error: "network" }));
        emit();
        return { sent: results.filter((x) => x.ok).length, kept: rows.length - i, results };
      }
      for (const res of r.results) {
        results.push(res);
        // Accepted, duplicate, or refused for a business reason (4xx) → it is settled, drop it.
        if (res.ok || (res.status && res.status < 500)) await remove(res.client_id);
        else await tx("readwrite", (s) => s.put({ ...batch.find((b) => b.client_id === res.client_id)!, attempts: 1, last_error: res.error }));
      }
    }
    emit();
    return { sent: results.filter((x) => x.ok).length, kept: (await all()).length, results };
  } finally { syncing = false; }
}

let wired = false;
/** Auto-sync when the browser comes back online (idempotent). */
export function wireAutoSync(onDone?: (r: { sent: number; kept: number; results: SyncResult[] }) => void) {
  if (wired) return; wired = true;
  window.addEventListener("online", () => { syncQueue().then((r) => onDone?.(r)).catch(() => null); });
}
