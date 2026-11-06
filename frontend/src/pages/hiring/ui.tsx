import React, { useState } from "react";
import { NavLink } from "react-router-dom";
import { LucideIcon } from "lucide-react";
import { Modal, Textarea } from "../common/_shared";
import { tone } from "./api";

export { Grid, Modal, Field, Input, Select, Textarea, Stat, useEmployees, empLabel, today, downloadBlob } from "../common/_shared";
export type { Col } from "../common/_shared";

export const Pill = ({ s, label }: { s?: string; label?: string }) =>
  <span className={`${tone(s)} whitespace-nowrap`}>{label || (s || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</span>;

/** Route-driven tab strip: each tab is a real URL, so the side menu can point at it. */
export function Tabs({ base, tabs }: { base: string; tabs: { key: string; label: string; icon: LucideIcon; badge?: number }[] }) {
  return (
    <div className="flex gap-1 mb-4 overflow-x-auto">
      {tabs.map((t) => (
        <NavLink key={t.key} to={`${base}/${t.key}`}
          className={({ isActive }) => `flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition-colors ${isActive ? "bg-primary-50 text-primary-600" : "text-gray-500 hover:bg-gray-100"}`}>
          <t.icon className="w-4 h-4" />{t.label}
          {!!t.badge && <span className="text-[10px] bg-amber-100 text-amber-700 rounded-full px-1.5 py-0.5">{t.badge}</span>}
        </NavLink>
      ))}
    </div>
  );
}

/** Ask for a reason before an action that needs one. */
export function ReasonDialog({ title, hint, label = "Reason", confirm = "Confirm", required = true, onClose, onConfirm }:
  { title: string; hint?: string; label?: string; confirm?: string; required?: boolean; onClose: () => void; onConfirm: (reason: string) => void }) {
  const [reason, setReason] = useState("");
  return (
    <Modal title={title} subtitle={hint} onClose={onClose} size="lg"
      footer={<>
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary disabled:opacity-50" disabled={required && !reason.trim()} onClick={() => onConfirm(reason.trim())}>{confirm}</button>
      </>}>
      <label className="block">
        <span className="text-[10px] uppercase tracking-wide text-gray-400 font-semibold">{label}</span>
        <Textarea rows={4} autoFocus value={reason} onChange={(e) => setReason(e.target.value)} className="mt-1" />
      </label>
    </Modal>
  );
}

export const Note = ({ children, tone: t = "info" }: { children: React.ReactNode; tone?: "info" | "warn" }) =>
  <div className={`text-xs rounded-lg px-3 py-2 mb-3 border ${t === "warn" ? "bg-amber-50 border-amber-200 text-amber-800" : "bg-primary-50 border-primary-100 text-primary-700"}`}>{children}</div>;

export const FirstStep = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div className="card text-center py-10">
    <h3 className="font-display font-semibold text-gray-800">{title}</h3>
    <div className="text-sm text-gray-500 mt-1 max-w-xl mx-auto">{children}</div>
  </div>
);
