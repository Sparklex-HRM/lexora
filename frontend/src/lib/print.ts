/**
 * Print helpers (2026-10-24 go-live wrap-up).
 *
 *  printPage()      – print the current screen as an A4 document. App chrome
 *                     (sidebar, top bar, banners, buttons marked .lx-no-print)
 *                     is hidden by print.css.
 *  printModal()     – print ONLY the open `printable` AppModal.
 *  printPdfBlob(b)  – print a server PDF (pay stub, letters, final pay, ROE,
 *                     registers, invoices…) through a hidden iframe; falls
 *                     back to opening it in a new tab if the browser blocks
 *                     printing a PDF frame.
 *
 * Dark mode is switched off for the duration of the print so the page comes
 * out black-on-white.
 */

function withLightTheme(bodyClass: string | null, run: () => void) {
  const html = document.documentElement;
  const wasDark = html.classList.contains("dark");
  if (wasDark) html.classList.remove("dark");
  if (bodyClass) document.body.classList.add(bodyClass);
  let restored = false;
  const restore = () => {
    if (restored) return;
    restored = true;
    if (wasDark) html.classList.add("dark");
    if (bodyClass) document.body.classList.remove(bodyClass);
    window.removeEventListener("afterprint", restore);
  };
  window.addEventListener("afterprint", restore);
  try { run(); } finally {
    // window.print() blocks in most browsers; afterprint may not fire everywhere.
    setTimeout(restore, 1000);
  }
}

export function printPage() {
  withLightTheme("lx-print-page", () => window.print());
}

/**
 * Print ONE element of the screen (a grid / register / report card) and
 * nothing else: every sibling along the path from <body> to the element is
 * hidden by print.css, so stats, tabs and other cards stay off the paper.
 * `landscape` turns the sheet for wide registers.
 */
export function printRegion(el: HTMLElement | null, opts: { landscape?: boolean } = {}) {
  if (!el) { printPage(); return; }
  const path: HTMLElement[] = [];
  for (let n = el.parentElement; n && n !== document.documentElement; n = n.parentElement) path.push(n);
  path.forEach((n) => n.classList.add("lx-print-path"));
  el.classList.add("lx-print-target");
  if (opts.landscape) el.classList.add("lx-print-landscape");
  let done = false;
  const undo = () => {
    if (done) return;
    done = true;
    path.forEach((n) => n.classList.remove("lx-print-path"));
    el.classList.remove("lx-print-target");
    if (opts.landscape) el.classList.remove("lx-print-landscape");
    window.removeEventListener("afterprint", undo);
  };
  window.addEventListener("afterprint", undo);
  withLightTheme("lx-print-region", () => window.print());
  setTimeout(undo, 1000);
}

export function printModal() {
  withLightTheme("lx-print-modal", () => window.print());
}

export function printPdfBlob(blob: Blob, fallbackName = "document.pdf") {
  const pdf = blob.type === "application/pdf" ? blob : new Blob([blob], { type: "application/pdf" });
  const url = URL.createObjectURL(pdf);
  const frame = document.createElement("iframe");
  frame.style.position = "fixed";
  frame.style.right = "0";
  frame.style.bottom = "0";
  frame.style.width = "0";
  frame.style.height = "0";
  frame.style.border = "0";
  frame.setAttribute("aria-hidden", "true");
  frame.title = fallbackName;
  const cleanup = () => setTimeout(() => { frame.remove(); URL.revokeObjectURL(url); }, 60_000);
  frame.onload = () => {
    try {
      frame.contentWindow?.focus();
      frame.contentWindow?.print();
    } catch {
      window.open(url, "_blank", "noopener");
    }
    cleanup();
  };
  frame.src = url;
  document.body.appendChild(frame);
}
