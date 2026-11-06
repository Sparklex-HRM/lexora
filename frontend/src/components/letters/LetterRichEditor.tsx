// D1 — a light rich-text editor over the letter engine's SAFE syntax (no HTML is ever sent to the
// server): headings, bold, bullet / numbered lists, tables, rules and {{merge fields}} as chips.
// The contentEditable HTML is converted back to the engine's text on every change, so what the
// server validates and renders is exactly the plain template text.
import { useEffect, useMemo, useRef, useState } from "react";
import { Bold, Heading1, Heading2, List, ListOrdered, Minus, Pilcrow, Table2, Code2, Braces } from "lucide-react";

export interface MergeField { field: string; label: string; group: string; block?: boolean; }

// ── body (engine text) → HTML ─────────────────────────────────────────────────
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
function inlineHtml(s: string): string {
  const parts = s.split("**");
  return parts.map((seg, i) => {
    const withFields = esc(seg).replace(/\{\{\s*([a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)\s*\}\}/g,
      (_m, f) => `<span class="mf" contenteditable="false" data-field="${f}">{{${f}}}</span>`);
    return i % 2 === 1 ? `<b>${withFields}</b>` : withFields;
  }).join("");
}
export function bodyToHtml(body: string): string {
  const lines = (body || "").replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  let para: string[] = []; let list: { kind: "ul" | "ol"; items: string[] } | null = null; let table: string[] = [];
  const flush = () => {
    if (para.length) { out.push(`<p>${inlineHtml(para.join(" "))}</p>`); para = []; }
    if (list) { out.push(`<${list.kind}>${list.items.map((x) => `<li>${inlineHtml(x)}</li>`).join("")}</${list.kind}>`); list = null; }
    if (table.length) { out.push(`<table>${table.join("")}</table>`); table = []; }
  };
  for (const raw of lines) {
    const line = raw.trim();
    const compact = line.replace(/\s/g, "");
    if (compact === "{{salary.breakup_table}}") { flush(); out.push(`<p class="block-field"><span class="mf" contenteditable="false" data-field="salary.breakup_table">{{salary.breakup_table}}</span></p>`); continue; }
    if (line.startsWith("|")) {
      if (para.length || list) flush();
      if (/^\|?\s*:?-{2,}/.test(line)) continue;
      table.push(`<tr>${line.replace(/^\||\|$/g, "").split("|").map((c) => `<td>${inlineHtml(c.trim())}</td>`).join("")}</tr>`); continue;
    }
    if (table.length) flush();
    if (!line) { flush(); continue; }
    if (line.startsWith("## ")) { flush(); out.push(`<h2>${inlineHtml(line.slice(3))}</h2>`); continue; }
    if (line.startsWith("# ")) { flush(); out.push(`<h1>${inlineHtml(line.slice(2))}</h1>`); continue; }
    if (line === "---" || line === "___" || line === "***") { flush(); out.push("<hr>"); continue; }
    if (line.startsWith("- ") || line.startsWith("* ")) { if (para.length || table.length || (list && list.kind !== "ul")) flush(); list = list || { kind: "ul", items: [] }; list.items.push(line.slice(2)); continue; }
    const num = line.match(/^(\d+)[.)]\s+(.*)$/);
    if (num) { if (para.length || table.length || (list && list.kind !== "ol")) flush(); list = list || { kind: "ol", items: [] }; list.items.push(num[2]); continue; }
    if (list) flush();
    para.push(line);
  }
  flush();
  return out.join("") || "<p><br></p>";
}

// ── HTML → body (engine text) ─────────────────────────────────────────────────
function inlineText(node: Node): string {
  let s = "";
  node.childNodes.forEach((n) => {
    if (n.nodeType === Node.TEXT_NODE) { s += (n.textContent || "").replace(/ /g, " "); return; }
    const el = n as HTMLElement;
    const tag = el.tagName;
    if (el.classList?.contains("mf")) { s += `{{${el.dataset.field}}}`; return; }
    if (tag === "BR") { s += " "; return; }
    const inner = inlineText(el);
    if (tag === "B" || tag === "STRONG" || (el.style?.fontWeight && Number(el.style.fontWeight) >= 600)) s += inner.trim() ? `**${inner}**` : inner;
    else s += inner;
  });
  return s;
}
export function htmlToBody(root: HTMLElement): string {
  const blocks: string[] = [];
  const walk = (el: Element) => {
    el.childNodes.forEach((n) => {
      if (n.nodeType === Node.TEXT_NODE) { const t = (n.textContent || "").trim(); if (t) blocks.push(t); return; }
      const e = n as HTMLElement; const tag = e.tagName;
      if (tag === "H1") blocks.push("# " + inlineText(e).trim());
      else if (tag === "H2" || tag === "H3") blocks.push("## " + inlineText(e).trim());
      else if (tag === "HR") blocks.push("---");
      else if (tag === "UL") blocks.push(Array.from(e.querySelectorAll(":scope > li")).map((li) => "- " + inlineText(li).trim()).join("\n"));
      else if (tag === "OL") blocks.push(Array.from(e.querySelectorAll(":scope > li")).map((li, i) => `${i + 1}. ` + inlineText(li).trim()).join("\n"));
      else if (tag === "TABLE") blocks.push(Array.from(e.querySelectorAll("tr")).map((tr) => "| " + Array.from(tr.querySelectorAll("td,th")).map((td) => inlineText(td).trim()).join(" | ") + " |").join("\n"));
      else if (tag === "P" || tag === "DIV" || tag === "SECTION" || tag === "BLOCKQUOTE") {
        if (e.querySelector("h1,h2,ul,ol,table,hr,div,p")) { walk(e); return; }
        const t = inlineText(e).replace(/\s+/g, " ").trim(); if (t) blocks.push(t);
      } else { const t = inlineText(e).replace(/\s+/g, " ").trim(); if (t) blocks.push(t); }
    });
  };
  walk(root);
  return blocks.join("\n\n").trim() + (blocks.length ? "\n" : "");
}

const BTN = "icon-btn text-gray-500 hover:text-primary-600 disabled:opacity-40";

export default function LetterRichEditor({ value, onChange, fields, disabled = false, minHeight = "52vh" }:
  { value: string; onChange: (body: string) => void; fields: MergeField[]; disabled?: boolean; minHeight?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const last = useRef<string>("");
  const [source, setSource] = useState(false);
  const [pick, setPick] = useState("");
  const groups = useMemo(() => { const g: Record<string, MergeField[]> = {}; fields.forEach((f) => (g[f.group] = g[f.group] || []).push(f)); return g; }, [fields]);

  // external value (template switch, rollback, picker insert in source mode) → re-render when not focused
  useEffect(() => {
    if (source || !ref.current) return;
    if (value === last.current) return;
    if (document.activeElement === ref.current) return;
    ref.current.innerHTML = bodyToHtml(value);
    last.current = value;
  }, [value, source]);
  useEffect(() => { if (!source && ref.current) { ref.current.innerHTML = bodyToHtml(value); last.current = value; } }, [source]);   // eslint-disable-line react-hooks/exhaustive-deps

  const emit = () => { if (!ref.current) return; const b = htmlToBody(ref.current); last.current = b; onChange(b); };
  const cmd = (c: string, arg?: string) => { ref.current?.focus(); document.execCommand(c, false, arg); emit(); };
  const insertHtml = (html: string) => { ref.current?.focus(); document.execCommand("insertHTML", false, html); emit(); };
  const insertField = (f: string) => {
    if (!f) return;
    if (source) { onChange(value + `{{${f}}}`); }
    else if (f === "salary.breakup_table") insertHtml(`<p class="block-field"><span class="mf" contenteditable="false" data-field="${f}">{{${f}}}</span></p><p><br></p>`);
    else insertHtml(`<span class="mf" contenteditable="false" data-field="${f}">{{${f}}}</span>&nbsp;`);
    setPick("");
  };
  const insertTable = () => insertHtml("<table><tr><td>Item</td><td>Details</td></tr><tr><td>…</td><td>…</td></tr></table><p><br></p>");

  return (
    <div className="flex flex-col h-full">
      <div className="flex flex-wrap items-center gap-0.5 border border-surface-border rounded-t-lg bg-surface px-1.5 py-1">
        <button type="button" className={BTN} title="Bold" disabled={disabled || source} onClick={() => cmd("bold")}><Bold className="w-4 h-4" /></button>
        <button type="button" className={BTN} title="Heading" disabled={disabled || source} onClick={() => cmd("formatBlock", "h1")}><Heading1 className="w-4 h-4" /></button>
        <button type="button" className={BTN} title="Sub-heading" disabled={disabled || source} onClick={() => cmd("formatBlock", "h2")}><Heading2 className="w-4 h-4" /></button>
        <button type="button" className={BTN} title="Paragraph" disabled={disabled || source} onClick={() => cmd("formatBlock", "p")}><Pilcrow className="w-4 h-4" /></button>
        <button type="button" className={BTN} title="Bullet list" disabled={disabled || source} onClick={() => cmd("insertUnorderedList")}><List className="w-4 h-4" /></button>
        <button type="button" className={BTN} title="Numbered list" disabled={disabled || source} onClick={() => cmd("insertOrderedList")}><ListOrdered className="w-4 h-4" /></button>
        <button type="button" className={BTN} title="Table (2 columns)" disabled={disabled || source} onClick={insertTable}><Table2 className="w-4 h-4" /></button>
        <button type="button" className={BTN} title="Horizontal line" disabled={disabled || source} onClick={() => cmd("insertHorizontalRule")}><Minus className="w-4 h-4" /></button>
        <span className="mx-1 h-5 w-px bg-surface-border" />
        <label className="inline-flex items-center gap-1 text-xs text-gray-600"><Braces className="w-3.5 h-3.5" />
          <select className="input !py-0.5 !w-56 text-xs" value={pick} disabled={disabled} onChange={(e) => insertField(e.target.value)} aria-label="Insert merge field">
            <option value="">Insert field…</option>
            {Object.entries(groups).map(([g, list]) => <optgroup key={g} label={g === "custom" ? "custom fields" : g}>{list.map((f) => <option key={f.field} value={f.field}>{f.label}</option>)}</optgroup>)}
          </select></label>
        <button type="button" className={`${BTN} ml-auto ${source ? "!text-primary-600" : ""}`} title={source ? "Rich text" : "Source text"} onClick={() => setSource((s) => !s)}><Code2 className="w-4 h-4" /></button>
      </div>
      {source ? (
        <textarea className="input font-mono text-xs w-full flex-1 rounded-t-none" style={{ minHeight }} value={value} disabled={disabled} onChange={(e) => { last.current = e.target.value; onChange(e.target.value); }} spellCheck />
      ) : (
        <div ref={ref} contentEditable={!disabled} suppressContentEditableWarning onInput={emit} onBlur={emit}
          className="letter-editor border border-t-0 border-surface-border rounded-b-lg bg-white px-5 py-4 text-sm text-gray-800 outline-none overflow-y-auto flex-1 focus:ring-2 focus:ring-primary-200"
          style={{ minHeight }} spellCheck data-testid="letter-rich-editor" />
      )}
      <style>{`
        .letter-editor h1{font-size:1.15rem;font-weight:700;margin:.4rem 0 .5rem}
        .letter-editor h2{font-size:1rem;font-weight:700;margin:.4rem 0 .4rem}
        .letter-editor p{margin:0 0 .6rem}
        .letter-editor ul{list-style:disc;padding-left:1.4rem;margin:0 0 .6rem}
        .letter-editor ol{list-style:decimal;padding-left:1.4rem;margin:0 0 .6rem}
        .letter-editor table{border-collapse:collapse;margin:.3rem 0 .7rem;width:100%}
        .letter-editor td{border:1px solid #d8e2e0;padding:3px 8px;min-width:4rem}
        .letter-editor tr:first-child td{background:#f3f6f5;font-weight:600}
        .letter-editor hr{border:0;border-top:1px solid #d8e2e0;margin:.6rem 0}
        .letter-editor .mf{display:inline-block;background:#e6f4f1;color:#0f766e;border:1px solid #99d5c9;border-radius:9999px;padding:0 .45rem;font-size:.75rem;font-family:ui-monospace,monospace;margin:0 1px;user-select:all}
        .letter-editor .block-field .mf{display:block;text-align:center;padding:.25rem}
      `}</style>
      <p className="text-[11px] text-gray-400 mt-1">Headings, bold, lists, tables, lines and fields only — the letter engine refuses anything else. Switch to source text to see exactly what is saved.</p>
    </div>
  );
}
