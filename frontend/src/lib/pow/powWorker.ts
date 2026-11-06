/// <reference lib="webworker" />
// Proof-of-work solver off the main thread, so typing and the page stay
// responsive while it searches. One message in ({id, salt, difficulty}),
// one message out ({id, number, ms}).
import { searchPow } from "./powCore";

const CHUNK = 50_000;
const LIMIT = 2 ** 40;

self.onmessage = (ev: MessageEvent<{ id: number; salt: string; difficulty: number }>) => {
  const { id, salt, difficulty } = ev.data;
  const t0 = performance.now();
  for (let start = 0; start < LIMIT; start += CHUNK) {
    const n = searchPow(salt, difficulty, start, start + CHUNK);
    if (n >= 0) {
      (self as unknown as Worker).postMessage({ id, number: n, ms: Math.round(performance.now() - t0) });
      return;
    }
  }
  (self as unknown as Worker).postMessage({ id, number: -1, ms: Math.round(performance.now() - t0) });
};
