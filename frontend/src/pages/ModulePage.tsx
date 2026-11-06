import { useParams } from "react-router-dom";
import { PackageOpen } from "lucide-react";
import { PageHeader, ComingSoon } from "../components/ui";
import { MODULES } from "../moduleCatalog";

export default function ModulePage() {
  const { slug = "" } = useParams();
  const m = MODULES[slug];
  if (!m) {
    return (
      <div>
        <PageHeader title="Module" icon={PackageOpen} subtitle="This module isn't defined yet." />
        <div className="card max-w-xl text-sm text-gray-500">Unknown module <code className="font-mono">{slug}</code>.</div>
      </div>
    );
  }
  return (
    <div>
      <PageHeader title={m.title} icon={m.icon} subtitle={m.blurb} />
      <ComingSoon icon={m.icon} title={m.title} phase={m.phase} points={m.points} />
    </div>
  );
}
