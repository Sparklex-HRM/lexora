import { useEffect, useState } from "react";
import { Square, Volume2 } from "lucide-react";
import toast from "react-hot-toast";
import { LANG_NAMES, LOCALE } from "./format";
import { useLang, useT } from "./index";

/** "Read this page" — browser speech synthesis. Honest: voices depend on the device; most phones
 *  have English and French (Canada) voices; when none exists we say so and use the default voice. */
export default function ReadPage({ selector = "main" }: { selector?: string }) {
  const t = useT();
  const lang = useLang();
  const [speaking, setSpeaking] = useState(false);
  const supported = typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;

  useEffect(() => () => { if (supported) window.speechSynthesis.cancel(); }, [supported]);

  const stop = () => { window.speechSynthesis.cancel(); setSpeaking(false); };
  const read = () => {
    if (!supported) { toast(t("i18n.voice_unsupported")); return; }
    const el = document.querySelector(selector) as HTMLElement | null;
    const text = (el?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 4000);
    if (!text) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const want = LOCALE[lang];
    const voices = window.speechSynthesis.getVoices();
    const v = voices.find((x) => x.lang.toLowerCase() === want.toLowerCase())
      || voices.find((x) => x.lang.toLowerCase().startsWith(lang + "-")) || voices.find((x) => x.lang.toLowerCase().startsWith(lang));
    if (v) { u.voice = v; u.lang = v.lang; } else { u.lang = want; if (lang !== "en") toast(t("i18n.voice_none", { lang: LANG_NAMES[lang] }), { duration: 5000 }); }
    u.rate = 0.95;
    u.onend = () => setSpeaking(false); u.onerror = () => setSpeaking(false);
    setSpeaking(true);
    window.speechSynthesis.speak(u);
  };
  return speaking ? (
    <button onClick={stop} className="inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1.5 rounded-lg bg-red-50 text-red-700 border border-red-200">
      <Square className="w-3.5 h-3.5" /> {t("i18n.stop")}
    </button>
  ) : (
    <button onClick={read} className="inline-flex items-center gap-1.5 text-xs font-semibold px-2.5 py-1.5 rounded-lg bg-primary-50 text-primary-700 border border-primary-200" title={supported ? "" : t("i18n.voice_unsupported")}>
      <Volume2 className="w-3.5 h-3.5" /> {t("home.read_page")}
    </button>
  );
}
