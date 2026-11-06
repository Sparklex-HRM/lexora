import { useState, useRef, useEffect } from "react";
import { X, Send, Sparkles } from "lucide-react";
import { aiApi, ChatMessage } from "../api/endpoints";
import { Monogram } from "./Logo";

const SUGGESTIONS = [
  "How many employees do we have?",
  "What's our monthly payroll cost?",
  "Who is paid weekly?",
  "Explain CPP2 for 2026",
];

export default function Chatbot({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const send = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    const history = messages;
    const next = [...messages, { role: "user" as const, content: q }];
    setMessages(next);
    setInput("");
    setBusy(true);
    try {
      const res = await aiApi.chat(q, history);
      setMessages([...next, { role: "assistant", content: res.reply }]);
    } catch {
      setMessages([...next, { role: "assistant", content: "Sorry — I couldn't reach the assistant service just now." }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {open && <div className="fixed inset-0 z-[55] bg-black/30 sm:hidden" onClick={onClose} />}
      <aside className={`fixed right-0 top-0 bottom-0 z-[60] w-full sm:w-[24rem] bg-white border-l border-surface-border shadow-2xl flex flex-col transition-transform duration-200 ${open ? "translate-x-0" : "translate-x-full"}`}>
        {/* Header */}
        <div className="bg-gradient-to-r from-primary-50 to-teal-50 border-b border-surface-border px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="w-8 h-8 rounded-full bg-primary-950 flex items-center justify-center shrink-0 overflow-hidden"><Monogram className="w-7 h-7" title="Lexi" /></span>
            <div>
              <h2 className="font-display font-semibold text-gray-800 leading-tight">Lexi</h2>
              <p className="text-[11px] text-gray-500 leading-tight">HR & payroll copilot</p>
            </div>
          </div>
          <button onClick={onClose} className="icon-btn text-gray-400"><X className="w-4 h-4" /></button>
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3 scrollbar-thin">
          {messages.length === 0 && (
            <div className="text-center py-6">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-500 to-teal-500 flex items-center justify-center mx-auto mb-3">
                <Sparkles className="w-6 h-6 text-white" />
              </div>
              <p className="text-sm font-semibold text-gray-700">Ask Lexi about your workforce</p>
              <p className="text-xs text-gray-400 mt-1 mb-4">Grounded in your org's live employee & payroll data.</p>
              <div className="space-y-2">
                {SUGGESTIONS.map((s) => (
                  <button key={s} onClick={() => send(s)}
                    className="w-full text-left text-xs px-3 py-2 rounded-lg bg-gray-50 hover:bg-primary-50 hover:text-primary-700 text-gray-600 transition-colors border border-surface-border">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] px-3 py-2 rounded-2xl text-sm whitespace-pre-wrap ${
                m.role === "user"
                  ? "bg-primary-600 text-white rounded-br-sm"
                  : "bg-gray-50 text-gray-700 border border-surface-border rounded-bl-sm"}`}>
                {m.content}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="bg-gray-50 border border-surface-border rounded-2xl rounded-bl-sm px-3 py-2.5">
                <span className="flex gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce" style={{ animationDelay: "120ms" }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce" style={{ animationDelay: "240ms" }} />
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Input */}
        <form onSubmit={(e) => { e.preventDefault(); send(input); }}
          className="border-t border-surface-border p-3 flex items-center gap-2">
          <input className="input !py-2" value={input} onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about payroll, people…" disabled={busy} />
          <button type="submit" disabled={busy || !input.trim()}
            className="btn-primary !px-3 !py-2 disabled:opacity-50"><Send className="w-4 h-4" /></button>
        </form>
      </aside>
    </>
  );
}
