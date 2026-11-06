import { useEffect } from "react";
import { Link } from "react-router-dom";
import { Languages } from "lucide-react";
import { engageApi } from "../api/engage";
import { useAuthStore } from "../store/authStore";
import { LANGS, LANG_NAMES, type Lang } from "./format";
import { getLang, setLang, useLang, useMissingCount, useT } from "./index";

/** Language picker: saved on the server (per login) and mirrored in localStorage for instant paint.
 *  Shows how many texts on the current page fell back to English, linking to the report. */
export default function LanguagePicker({ compact = false }: { compact?: boolean }) {
  const t = useT();
  const lang = useLang();
  const missing = useMissingCount();
  const user = useAuthStore((s) => s.user);

  // Once per login: take the server-side preference if it differs from the local one.
  useEffect(() => {
    if (!user) return;
    engageApi.prefs().then((p) => { if (p?.language && p.language !== getLang()) setLang(p.language as Lang); }).catch(() => null);
  }, [user?.id]);   // eslint-disable-line react-hooks/exhaustive-deps

  const change = (l: Lang) => {
    setLang(l);
    engageApi.savePrefs({ language: l }).catch(() => null);   // best-effort; the local copy already applied
  };
  return (
    <div className="inline-flex items-center gap-1.5">
      <Languages className="w-4 h-4 text-gray-400 shrink-0" aria-hidden />
      <select aria-label={t("home.language")} value={lang} onChange={(e) => change(e.target.value as Lang)}
        className={`rounded-lg border border-surface-border bg-white text-gray-700 ${compact ? "text-xs py-1 px-1.5" : "text-sm py-1.5 px-2"}`}>
        {LANGS.map((l) => <option key={l} value={l}>{LANG_NAMES[l]}</option>)}
      </select>
      {missing > 0 && (
        <Link to="/mobile/translations" title={t("i18n.page_missing", { n: missing })}
          className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800">{missing}</Link>
      )}
    </div>
  );
}
