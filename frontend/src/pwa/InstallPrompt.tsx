import { useEffect, useState } from "react";
import { Download, WifiOff } from "lucide-react";
import { canInstall, isStandalone, onInstallChange, promptInstall } from "./register";
import { useOnline } from "./useOnline";
import { useT } from "../i18n";

/** Phone-first banner: offline notice + "Install" when the browser offers it. */
export default function InstallPrompt() {
  const t = useT();
  const online = useOnline();
  const [can, setCan] = useState(canInstall());
  useEffect(() => onInstallChange(() => setCan(canInstall())), []);
  const standalone = isStandalone();
  if (online && !can && standalone) return null;
  return (
    <div className="space-y-2 mb-3">
      {!online && (
        <div className="rounded-xl bg-amber-50 border border-amber-200 text-amber-800 text-sm px-3 py-2 flex items-center gap-2" role="status">
          <WifiOff className="w-4 h-4 shrink-0" /><span>{t("offline.body")}</span>
        </div>
      )}
      {can && !standalone && (
        <button onClick={() => promptInstall()} className="w-full rounded-xl bg-primary-600 text-white text-sm font-semibold px-3 py-2.5 inline-flex items-center justify-center gap-2">
          <Download className="w-4 h-4" /> {t("home.install_app")}
        </button>
      )}
      {!can && !standalone && online && (
        <p className="text-[11px] text-gray-400 px-1">{t("home.install_hint")}</p>
      )}
    </div>
  );
}
