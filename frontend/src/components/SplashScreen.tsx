/**
 * Post-login splash — a short branded loader shown once, right after a user
 * signs in (not on page reload). The dashboard loads behind it, then it fades.
 */
import { useEffect, useState } from "react";
import Logo from "./Logo";

export function SplashScreen({ onDone }: { onDone: () => void }) {
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    const fade = window.setTimeout(() => setLeaving(true), 3500); // start fade
    const done = window.setTimeout(onDone, 4000);                 // unmount ~4s
    return () => { window.clearTimeout(fade); window.clearTimeout(done); };
  }, [onDone]);

  return (
    <div
      role="status" aria-label="Loading Lexora HRM"
      className={`fixed inset-0 z-[200] flex flex-col items-center justify-center transition-opacity duration-500 ${leaving ? "opacity-0" : "opacity-100"}`}
      style={{ background: "linear-gradient(140deg, #1e1b4b 0%, #232065 45%, #3730a3 100%)" }}
    >
      {/* the full Lexora lockup (white wordmark reads on the dark indigo) */}
      <Logo tagline onDark className="h-24 sm:h-28 w-auto max-w-[80vw] animate-[splashIn_0.7s_ease-out]" />

      {/* slim progress bar */}
      <div className="mt-9 h-1 w-48 max-w-[60vw] overflow-hidden rounded-full bg-white/15">
        <div className="h-full w-1/3 rounded-full bg-white/80 animate-[splashBar_1.2s_ease-in-out_infinite]" />
      </div>
      <p className="mt-3 text-[11px] text-white/60">Preparing your workspace…</p>

      <style>{`
        @keyframes splashIn { 0% { opacity:0; transform: translateY(10px) scale(0.96); } 100% { opacity:1; transform: translateY(0) scale(1); } }
        @keyframes splashBar { 0% { transform: translateX(-120%); } 100% { transform: translateX(320%); } }
      `}</style>
    </div>
  );
}
