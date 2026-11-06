/**
 * safeHref — the only way a URL that came from DATA (a setting, a candidate, an interview
 * place, a learning module…) may become an href or a window.open target.
 *
 * Allowed: http:, https:, mailto:, tel:. Anything else — javascript:, data:, vbscript:,
 * file:, blob:, a relative path, junk — returns undefined, and the caller shows the value
 * as plain text instead of a link. Browsers forgive tabs / newlines / leading control
 * characters inside a scheme ("java\tscript:"), so the value is normalised the same way
 * before the check.
 */
const ALLOWED = new Set(["http:", "https:", "mailto:", "tel:"]);

export function safeHref(u: unknown): string | undefined {
  if (typeof u !== "string") return undefined;
  // eslint-disable-next-line no-control-regex
  const s = u.replace(/[\t\n\r]/g, "").replace(/^[\u0000-  ]+|[\u0000-  ]+$/g, "");
  if (!s) return undefined;
  let parsed: URL;
  try {
    parsed = new URL(s);            // no base: a relative / scheme-less value throws → not a link
  } catch {
    return undefined;
  }
  if (!ALLOWED.has(parsed.protocol)) return undefined;
  if ((parsed.protocol === "http:" || parsed.protocol === "https:") && !parsed.hostname) return undefined;
  return s;
}

/** window.open for a data-supplied URL: does nothing unless safeHref allows it. */
export function openSafe(u: unknown): void {
  const h = safeHref(u);
  if (h) window.open(h, "_blank", "noopener,noreferrer");
}
