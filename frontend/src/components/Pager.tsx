import { ChevronsLeft, ChevronLeft, ChevronRight, ChevronsRight } from "lucide-react";

/**
 * Shared pagination bar — First / Prev / page N of M / Next / Last + page-size
 * select + "showing X–Y of N". Matches the inline bar on EmployeesPage so every
 * list page reads the same. Renders nothing when there's nothing to page.
 */
export function Pager({ total, page, pageSize, onPage, onPageSize }: {
  total: number; page: number; pageSize: number;
  onPage: (p: number) => void; onPageSize: (n: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const cur = Math.min(Math.max(1, page), pageCount);
  if (total === 0) return null;
  return (
    <div className="flex flex-col sm:flex-row items-center justify-between gap-2 px-4 py-3 border-t border-surface-border text-xs text-gray-500">
      <div className="flex items-center gap-2">
        <span>Rows per page</span>
        <select className="input !py-1 !w-auto text-xs" value={pageSize} onChange={(e) => onPageSize(+e.target.value)}>
          {[10, 25, 50, 100].map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>
      <div className="tabular-nums">Showing {(cur - 1) * pageSize + 1}–{Math.min(cur * pageSize, total)} of {total}</div>
      <div className="flex items-center gap-1">
        <button disabled={cur <= 1} onClick={() => onPage(1)} className="icon-btn disabled:opacity-30" title="First page"><ChevronsLeft className="w-4 h-4" /></button>
        <button disabled={cur <= 1} onClick={() => onPage(cur - 1)} className="icon-btn disabled:opacity-30" title="Previous"><ChevronLeft className="w-4 h-4" /></button>
        <span className="px-2 tabular-nums">Page {cur} / {pageCount}</span>
        <button disabled={cur >= pageCount} onClick={() => onPage(cur + 1)} className="icon-btn disabled:opacity-30" title="Next"><ChevronRight className="w-4 h-4" /></button>
        <button disabled={cur >= pageCount} onClick={() => onPage(pageCount)} className="icon-btn disabled:opacity-30" title="Last page"><ChevronsRight className="w-4 h-4" /></button>
      </div>
    </div>
  );
}
