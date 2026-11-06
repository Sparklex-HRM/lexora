import { Printer } from "lucide-react";
import { printRegion } from "../lib/print";

/**
 * Print the card / section this button sits in (2026-10-24 print wrap-up).
 * Drop it in a card header: it prints the nearest `target` ancestor (default
 * `.card`) and nothing else on the screen. Pair it with a `.lx-print-only`
 * heading inside the card so the paper says what it is.
 */
export default function PrintRegionButton({
  target = ".card", landscape, label = "Print", title = "Print this list (A4)",
  className = "btn-secondary !py-1 !px-2 text-xs inline-flex items-center gap-1", testId,
}: { target?: string; landscape?: boolean; label?: string; title?: string; className?: string; testId?: string }) {
  return (
    <button type="button" className={`${className} lx-no-print`} title={title} data-testid={testId}
      onClick={(e) => printRegion(e.currentTarget.closest<HTMLElement>(target), { landscape })}>
      <Printer className="w-3.5 h-3.5" />{label && ` ${label}`}
    </button>
  );
}

/** Paper-only title line for a printed list. */
export function PrintHeading({ children }: { children: React.ReactNode }) {
  return <div className="lx-print-only px-1 pb-2 text-sm font-semibold text-gray-800">{children} · printed {new Date().toLocaleString("en-CA")}</div>;
}
