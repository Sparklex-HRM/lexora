import { useQuery } from "@tanstack/react-query";
import { entitiesApi, type LegalEntity } from "../api/entities";
import { useAuthStore } from "../store/authStore";

/**
 * Paper-only letterhead + signature block (2026-10-24 print wrap-up).
 * Rendered with .lx-print-only, so it shows ONLY when the screen / printable
 * dialog is printed. Company name, address, CRA Business Number / Revenu Québec number and logo come from
 * the legal-entity master (Settings → Legal entities); a person who may not read
 * the master still gets the organisation name from their session.
 */
function useEntity(entityId?: string | null) {
  const { data } = useQuery({
    queryKey: ["print-letterhead-entities"],
    queryFn: () => entitiesApi.list(),
    staleTime: 10 * 60_000,
    retry: false,
  });
  const list = (data?.entities || []) as LegalEntity[];
  return (entityId && list.find((e) => e.id === entityId)) || list.find((e) => e.is_default) || list[0] || null;
}

export function PrintLetterhead({ title, subtitle, entityId }: {
  title: string; subtitle?: string; entityId?: string | null;
}) {
  const orgName = useAuthStore((s) => s.user?.org_name) || "";
  const ent = useEntity(entityId);
  const { data: logo } = useQuery({
    queryKey: ["print-letterhead-logo", ent?.id],
    queryFn: () => entitiesApi.logo(ent!.id),
    enabled: !!ent?.id && !!ent?.has_logo,
    staleTime: 10 * 60_000,
    retry: false,
  });
  const name = ent?.name || orgName;
  const address = ent ? [ent.address_line1, ent.address_line2, ent.city, [ent.province, ent.postal_code].filter(Boolean).join(" ")].filter(Boolean).join(", ") : "";
  const ids = [ent?.business_number && `BN ${ent.business_number}`, ent?.rq_identification && `RQ ${ent.rq_identification}`].filter(Boolean).join(" · ");
  const contact = [ent?.phone, ent?.email, ent?.website].filter(Boolean).join(" · ");
  return (
    <div className="lx-print-only lx-keep mb-4" data-testid="print-letterhead">
      <div className="flex items-start justify-between gap-4 border-b-2 border-gray-800 pb-2">
        <div className="min-w-0">
          <div className="text-lg font-bold text-gray-900">{name}</div>
          {address && <div className="text-[9pt] text-gray-700">{address}</div>}
          {ids && <div className="text-[8.5pt] text-gray-600">{ids}</div>}
          {contact && <div className="text-[8.5pt] text-gray-600">{contact}</div>}
        </div>
        {logo?.logo_data_url && <img src={logo.logo_data_url} alt="" className="h-12 max-w-[40mm] object-contain" />}
      </div>
      <div className="mt-2 flex items-baseline justify-between gap-4">
        <div className="text-base font-semibold text-gray-900">{title}</div>
        <div className="text-[8.5pt] text-gray-500">Printed {new Date().toLocaleString("en-CA")}</div>
      </div>
      {subtitle && <div className="text-[9pt] text-gray-700">{subtitle}</div>}
    </div>
  );
}

/** Paper-only signature lines, e.g. ["Prepared by", "Checked by", "Authorised signatory"]. */
export function PrintSignatures({ labels, note }: { labels: string[]; note?: string }) {
  return (
    <div className="lx-print-only lx-keep mt-10">
      <div className="grid gap-8" style={{ gridTemplateColumns: `repeat(${Math.max(1, labels.length)}, minmax(0, 1fr))` }}>
        {labels.map((l) => (
          <div key={l} className="text-center">
            <div className="border-t border-gray-700 pt-1 text-[9pt] text-gray-800">{l}</div>
          </div>
        ))}
      </div>
      {note && <div className="mt-4 text-[8pt] text-gray-500">{note}</div>}
    </div>
  );
}
