import React, { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { Printer, X } from "lucide-react";
import { printModal } from "../lib/print";

export { printModal };

/**
 * AppModal — the ONE modal shell of Lexora HRM (2026-10-24 go-live wrap-up).
 *
 * Every dialog in the app renders through this (the older per-module shells —
 * DataGrid WideModal, UsersPage Dialog, exit WideModal, canada Modal — are thin
 * wrappers now).
 *
 * Rules it enforces (feedback_modals_wider):
 *  - It USES THE SCREEN: smallest preset is max-w-3xl (a short confirm);
 *    forms start at max-w-5xl with two columns on desktop; line-item editors
 *    use `screen` (full viewport, dense grid).
 *  - Phone: a full-height sheet, one column (callers use sm:/md: grid ladders).
 *  - Header and footer stay put; only the body scrolls.
 *  - Esc closes the TOP-most dialog only; page scroll is locked while open.
 *  - Rendered into <body> through a portal, so `printable` dialogs print as a
 *    clean A4 document with no app chrome behind them.
 */
export type ModalSize = "confirm" | "form" | "wide" | "xwide" | "screen";

const WIDTH: Record<ModalSize, string> = {
  confirm: "sm:max-w-3xl",
  form: "sm:max-w-5xl",
  wide: "sm:max-w-6xl",
  xwide: "sm:max-w-7xl",
  screen: "sm:max-w-none sm:w-[97vw]",
};

// Top-most-first stack so Esc only closes the dialog on top.
const stack: string[] = [];
let scrollLocks = 0;

/** For hand-built full-screen overlays that are not on AppModal (EmployeeForm, PayslipModal, RolesPage):
 *  joins the same top-most stack so Esc closes only the dialog on top, and locks page scroll. */
export function useModalLayer(onClose: () => void) {
  const id = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    stack.push(id);
    scrollLocks += 1;
    const prevOverflow = document.body.style.overflow;
    if (scrollLocks === 1) document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || stack[stack.length - 1] !== id) return;
      e.stopPropagation();
      closeRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      const i = stack.lastIndexOf(id);
      if (i >= 0) stack.splice(i, 1);
      scrollLocks = Math.max(0, scrollLocks - 1);
      if (scrollLocks === 0) document.body.style.overflow = prevOverflow === "hidden" ? "" : prevOverflow;
    };
  }, [id]);
}

export interface AppModalProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
  footer?: React.ReactNode;
  size?: ModalSize;
  /** Show a Print button and let the body print as an A4 document. */
  printable?: boolean;
  /** Extra buttons in the header (left of Print / Close). */
  actions?: React.ReactNode;
  bodyClassName?: string;
  headerClassName?: string;
  /** Close when the backdrop is clicked (default true). */
  closeOnBackdrop?: boolean;
  testId?: string;
  ariaLabel?: string;
}

export default function AppModal({
  title, subtitle, onClose, children, footer, size = "form", printable, actions,
  bodyClassName = "", headerClassName = "", closeOnBackdrop = true, testId, ariaLabel,
}: AppModalProps) {
  const id = useId();
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    stack.push(id);
    scrollLocks += 1;
    const prevOverflow = document.body.style.overflow;
    if (scrollLocks === 1) document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || stack[stack.length - 1] !== id) return;
      e.stopPropagation();
      closeRef.current();
    };
    window.addEventListener("keydown", onKey);
    // Focus the dialog unless something inside already has focus (autoFocus inputs).
    const t = setTimeout(() => {
      if (panel.current && !panel.current.contains(document.activeElement)) panel.current.focus({ preventScroll: true });
    }, 0);
    return () => {
      clearTimeout(t);
      window.removeEventListener("keydown", onKey);
      const i = stack.lastIndexOf(id);
      if (i >= 0) stack.splice(i, 1);
      scrollLocks = Math.max(0, scrollLocks - 1);
      if (scrollLocks === 0) document.body.style.overflow = prevOverflow === "hidden" ? "" : prevOverflow;
    };
  }, [id]);

  const screen = size === "screen";
  const node = (
    <div className={`lx-modal-host fixed inset-0 z-[60] bg-black/45 flex items-stretch sm:items-center justify-center p-0 sm:p-3 ${printable ? "lx-print-host" : ""}`}
      onMouseDown={(e) => { if (closeOnBackdrop && e.target === e.currentTarget) onClose(); }}>
      <div ref={panel} role="dialog" aria-modal="true" tabIndex={-1} data-testid={testId}
        aria-label={ariaLabel || (typeof title === "string" ? title : undefined)}
        onMouseDown={(e) => e.stopPropagation()}
        className={`lx-modal-panel bg-white shadow-2xl flex flex-col w-full min-h-0 outline-none
          h-[100dvh] ${screen ? "sm:h-[95vh]" : "sm:h-auto sm:max-h-[94vh]"} rounded-none sm:rounded-xl ${WIDTH[size]}`}>
        <div className={`lx-modal-head shrink-0 grid grid-cols-[minmax(0,1fr)_auto] items-start gap-3 px-4 sm:px-5 py-3 border-b border-surface-border ${headerClassName}`}>
          <div className="min-w-0">
            <h2 className="font-display font-semibold text-gray-800 truncate">{title}</h2>
            {subtitle && <div className="text-[11px] text-gray-500 mt-0.5">{subtitle}</div>}
          </div>
          <div className="flex items-center gap-1.5 lx-no-print">
            {actions}
            {printable && (
              <button type="button" className="btn-secondary !px-2.5 !py-1.5 text-xs inline-flex items-center gap-1" onClick={printModal} title="Print (A4)">
                <Printer className="w-3.5 h-3.5" /> Print
              </button>
            )}
            <button type="button" className="p-1.5 rounded-lg text-gray-400 hover:text-gray-700 hover:bg-gray-50" onClick={onClose} title="Close (Esc)" aria-label="Close">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
        <div className={`lx-modal-body flex-1 min-h-0 overflow-y-auto px-4 sm:px-5 py-4 ${bodyClassName}`}>{children}</div>
        {footer && (
          <div className="lx-modal-foot lx-no-print shrink-0 px-4 sm:px-5 py-3 border-t border-surface-border bg-surface flex flex-wrap items-center justify-end gap-2 sm:rounded-b-xl"
            style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  );
  return typeof document === "undefined" ? node : createPortal(node, document.body);
}

/** Two-column (desktop) / one-column (phone) field grid for modal forms. */
export function FormGrid({ children, cols = 2, className = "" }: { children: React.ReactNode; cols?: 2 | 3 | 4; className?: string }) {
  const c = cols === 4 ? "sm:grid-cols-2 lg:grid-cols-4" : cols === 3 ? "sm:grid-cols-2 lg:grid-cols-3" : "sm:grid-cols-2";
  return <div className={`grid grid-cols-1 ${c} gap-x-4 gap-y-3 ${className}`}>{children}</div>;
}

/** Section heading inside a modal form. */
export function FormSection({ title, hint, children, className = "" }: { title: string; hint?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={`mb-4 ${className}`}>
      <div className="mb-2 pb-1 border-b border-surface-border">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-primary-700">{title}</h3>
        {hint && <p className="text-[11px] text-gray-500 mt-0.5">{hint}</p>}
      </div>
      {children}
    </section>
  );
}
