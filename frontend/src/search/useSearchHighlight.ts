import { useEffect } from "react";
import { useLocation } from "react-router-dom";

/** Global search "highlight the record": a search result link may carry `?hl=<text>` (an employee
 *  number, ticket no., bill no., a name…). Once the page has rendered its rows, the smallest
 *  row-like element whose text contains it is scrolled into view and flashed (class
 *  `lx-search-hl`, index.css). Mounted ONCE in DashboardLayout — no per-page code. Gives up
 *  quietly after 6 s (the record may be on another page of a list or filtered out). */
const ROW_SEL = "tr, li, [role=row], [data-row], .card, [class*='rounded-xl'], [class*='rounded-lg']";

export function findHighlightTarget(root: ParentNode, needle: string): HTMLElement | null {
  const n = needle.trim().toLowerCase();
  if (!n) return null;
  let best: HTMLElement | null = null;
  let bestLen = Infinity;
  root.querySelectorAll<HTMLElement>(ROW_SEL).forEach((el) => {
    if (el.closest("[data-testid=command-palette], header, aside, nav")) return;   // not the search box / menu itself
    const txt = (el.textContent || "").toLowerCase();
    if (!txt.includes(n)) return;
    if (txt.length < bestLen) { best = el; bestLen = txt.length; }
  });
  return best;
}

export function useSearchHighlight() {
  const loc = useLocation();
  useEffect(() => {
    const hl = new URLSearchParams(loc.search).get("hl");
    if (!hl) return;
    let done = false;
    const main = document.querySelector("main") || document.body;
    const tryFind = () => {
      if (done) return;
      const el = findHighlightTarget(main, hl);
      if (!el) return;
      done = true;
      el.classList.add("lx-search-hl");
      el.setAttribute("data-search-hl", "1");
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      window.setTimeout(() => el.classList.remove("lx-search-hl"), 4000);
    };
    const mo = new MutationObserver(() => tryFind());
    mo.observe(main, { childList: true, subtree: true });
    const t0 = window.setTimeout(tryFind, 50);
    const stop = window.setTimeout(() => { done = true; mo.disconnect(); }, 6000);
    return () => { done = true; mo.disconnect(); window.clearTimeout(t0); window.clearTimeout(stop); };
  }, [loc.pathname, loc.search]);
}
