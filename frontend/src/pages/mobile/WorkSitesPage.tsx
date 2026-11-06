import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import toast from "react-hot-toast";
import DataGrid, { type GridColumn } from "../../components/DataGrid";
import { errText } from "../../api/platform";
import { mobileApi, type Device, type Fence, type MobileSettings } from "../../api/mobile";
import { useLang, useT } from "../../i18n";
import { fmtDateTime } from "../../i18n/format";
import { FIELD, LABEL, MobileShell } from "./_mobile";

/** /mobile/sites — HR: work sites (geo-fences per location), registered phones, punch rules. */
export default function WorkSitesPage() {
  const t = useT();
  const lang = useLang();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "sites";
  const sites = useQuery({ queryKey: ["m-sites"], queryFn: () => mobileApi.sites() });
  const devices = useQuery({ queryKey: ["m-devices"], queryFn: () => mobileApi.devices() });
  const settings = useQuery({ queryKey: ["m-settings"], queryFn: () => mobileApi.settings() });
  const canEdit = sites.data?.can_edit;
  const [f, setF] = useState<{ name: string; lat: string; lng: string; radius_m: string; location_id?: string }>({ name: "", lat: "", lng: "", radius_m: "150" });
  const [rules, setRules] = useState<MobileSettings | null>(null);
  useEffect(() => { if (settings.data && !rules) setRules(settings.data.value); }, [settings.data]);  // eslint-disable-line react-hooks/exhaustive-deps

  const add = useMutation({
    mutationFn: () => mobileApi.createSite({ name: f.name, lat: Number(f.lat), lng: Number(f.lng), radius_m: Number(f.radius_m), location_id: f.location_id }),
    onSuccess: () => { toast.success(t("common.saved")); setF({ name: "", lat: "", lng: "", radius_m: "150" }); qc.invalidateQueries({ queryKey: ["m-sites"] }); },
    onError: (e) => toast.error(errText(e)),
  });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: string; body: Partial<Fence> }) => mobileApi.updateSite(id, body), onSuccess: () => qc.invalidateQueries({ queryKey: ["m-sites"] }), onError: (e) => toast.error(errText(e)) });
  const decide = useMutation({ mutationFn: ({ id, d }: { id: string; d: "approve" | "reject" | "revoke" }) => (d === "revoke" ? mobileApi.revokeDevice(id) : mobileApi.decideDevice(id, d)), onSuccess: () => { toast.success(t("common.done")); qc.invalidateQueries({ queryKey: ["m-devices"] }); }, onError: (e) => toast.error(errText(e)) });
  const saveRules = useMutation({ mutationFn: () => mobileApi.saveSettings(rules!), onSuccess: () => toast.success(t("common.saved")), onError: (e) => toast.error(errText(e)) });

  const useHere = () => navigator.geolocation?.getCurrentPosition((p) => setF({ ...f, lat: p.coords.latitude.toFixed(6), lng: p.coords.longitude.toFixed(6) }), () => toast.error(t("punch.gps_denied")), { enableHighAccuracy: true, timeout: 10000 });

  const fenceCols: GridColumn<Fence>[] = [
    { key: "name", label: t("sites.name") }, { key: "lat", label: t("sites.lat"), type: "number" }, { key: "lng", label: t("sites.lng"), type: "number" },
    { key: "radius_m", label: t("sites.radius"), type: "number", render: (r) => canEdit ? <input type="number" className="w-20 rounded border border-surface-border px-1 py-0.5 text-sm" defaultValue={r.radius_m} onBlur={(e) => { const v = Number(e.target.value); if (v !== r.radius_m) patch.mutate({ id: r.id, body: { radius_m: v } }); }} /> : r.radius_m },
    { key: "active", label: t("common.status"), text: (r) => (r.active ? "active" : "off"), render: (r) => canEdit ? <button className="text-xs text-primary-700" onClick={() => patch.mutate({ id: r.id, body: { active: !r.active } })}>{r.active ? "Active · switch off" : "Off · switch on"}</button> : (r.active ? "Active" : "Off") },
  ];
  const devCols: GridColumn<Device>[] = [
    { key: "employee_name", label: t("team.person") }, { key: "label", label: t("sites.devices") },
    { key: "status", label: t("common.status"), render: (r) => <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${r.status === "active" ? "bg-green-100 text-green-700" : r.status === "pending" ? "bg-amber-100 text-amber-800" : "bg-gray-100 text-gray-600"}`}>{r.status}</span> },
    { key: "registered_at", label: t("common.date"), render: (r) => fmtDateTime(r.registered_at, lang), text: (r) => r.registered_at || "" },
    { key: "decided_by", label: "Decided by" },
    { key: "act", label: "", render: (r) => canEdit ? <div className="flex gap-1">
      {r.status === "pending" && <><button className="btn-primary !py-0.5 !px-2 text-xs" onClick={() => decide.mutate({ id: r.id, d: "approve" })}>{t("sites.approve")}</button><button className="btn-secondary !py-0.5 !px-2 text-xs" onClick={() => decide.mutate({ id: r.id, d: "reject" })}>{t("sites.reject")}</button></>}
      {r.status === "active" && <button className="text-xs text-red-600" onClick={() => decide.mutate({ id: r.id, d: "revoke" })}>{t("sites.revoke")}</button>}</div> : null },
  ];
  const TABS = [["sites", t("sites.sites")], ["devices", t("sites.devices")], ["rules", t("sites.rules")]];
  const pending = (devices.data || []).filter((d) => d.status === "pending").length;

  return (
    <MobileShell title={t("sites.title")} wide>
      <div className="flex gap-1 mb-3 flex-wrap">
        {TABS.map(([k, l]) => <button key={k} onClick={() => setSp({ tab: k })} className={`px-3 py-1.5 rounded-lg text-sm font-medium ${tab === k ? "bg-primary-600 text-white" : "bg-white border border-surface-border text-gray-600"}`}>{l}{k === "devices" && pending ? ` (${pending})` : ""}</button>)}
      </div>

      {tab === "sites" && <>
        {canEdit && (
          <form className="card grid grid-cols-2 md:grid-cols-6 gap-2 items-end mb-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            <div className="col-span-2"><label className={LABEL}>{t("sites.name")}</label><input className={FIELD} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
            <div><label className={LABEL}>{t("sites.lat")}</label><input className={FIELD} inputMode="decimal" value={f.lat} onChange={(e) => setF({ ...f, lat: e.target.value })} required /></div>
            <div><label className={LABEL}>{t("sites.lng")}</label><input className={FIELD} inputMode="decimal" value={f.lng} onChange={(e) => setF({ ...f, lng: e.target.value })} required /></div>
            <div><label className={LABEL}>{t("sites.radius")}</label><input className={FIELD} type="number" min={10} max={5000} value={f.radius_m} onChange={(e) => setF({ ...f, radius_m: e.target.value })} /></div>
            <div className="flex gap-1"><button type="button" className="btn-secondary !px-2 text-xs whitespace-nowrap" onClick={useHere}>{t("sites.use_here")}</button><button className="btn-primary text-sm" disabled={add.isPending}>{t("sites.add")}</button></div>
          </form>
        )}
        <DataGrid columns={fenceCols} rows={sites.data?.fences || []} rowKey={(r) => r.id} minWidth={640} exportName="work-sites" empty={t("punch.no_site")} />
        {(sites.data?.locations_without_fence || []).length > 0 && (
          <div className="card mt-3 text-sm">
            <div className="font-semibold text-gray-700 mb-1">{t("sites.unlinked")}</div>
            <div className="flex flex-wrap gap-1.5">{sites.data!.locations_without_fence.map((l) => <button key={l.id} className="px-2 py-1 rounded-lg border border-surface-border text-xs hover:bg-gray-50" onClick={() => setF({ ...f, name: l.name, location_id: l.id })}>{l.name}{l.city ? ` · ${l.city}` : ""} +</button>)}</div>
          </div>
        )}
      </>}

      {tab === "devices" && <DataGrid columns={devCols} rows={devices.data || []} rowKey={(r) => r.id} minWidth={720} exportName="registered-phones" empty="—" initialSort={{ key: "registered_at", dir: "desc" }} />}

      {tab === "rules" && rules && (
        <form className="card grid grid-cols-1 md:grid-cols-2 gap-3 max-w-3xl" onSubmit={(e) => { e.preventDefault(); saveRules.mutate(); }}>
          <div><label className={LABEL}>{t("sites.outside_fence")}</label>
            <select className={FIELD} value={rules.outside_fence} onChange={(e) => setRules({ ...rules, outside_fence: e.target.value as any })}><option value="flag">{t("sites.flag")}</option><option value="refuse">{t("sites.refuse")}</option></select></div>
          <div><label className={LABEL}>{t("sites.selfie")}</label>
            <select className={FIELD} value={rules.selfie} onChange={(e) => setRules({ ...rules, selfie: e.target.value as any })}><option value="off">{t("sites.off")}</option><option value="optional">{t("sites.optional")}</option><option value="required">{t("sites.required")}</option></select></div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={rules.device_binding} onChange={(e) => setRules({ ...rules, device_binding: e.target.checked })} /> {t("sites.binding")}</label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={rules.refuse_mock} onChange={(e) => setRules({ ...rules, refuse_mock: e.target.checked })} /> {t("sites.mock")}</label>
          <div><label className={LABEL}>{t("sites.accuracy")}</label><input type="number" className={FIELD} min={0} value={rules.max_accuracy_m} onChange={(e) => setRules({ ...rules, max_accuracy_m: Number(e.target.value) })} /></div>
          <label className="flex items-center gap-2 text-sm md:mt-6"><input type="checkbox" checked={rules.refuse_poor_accuracy} onChange={(e) => setRules({ ...rules, refuse_poor_accuracy: e.target.checked })} /> {t("sites.refuse_accuracy")}</label>
          <div><label className={LABEL}>{t("sites.late")}</label><input type="number" className={FIELD} min={0} value={rules.late_sync_minutes} onChange={(e) => setRules({ ...rules, late_sync_minutes: Number(e.target.value) })} /></div>
          <div><label className={LABEL}>{t("sites.selfie_kb")}</label><input type="number" className={FIELD} min={64} max={5120} value={rules.selfie_max_kb} onChange={(e) => setRules({ ...rules, selfie_max_kb: Number(e.target.value) })} /></div>
          {settings.data?.can_edit && <div className="md:col-span-2"><button className="btn-primary" disabled={saveRules.isPending}>{t("common.save")}</button></div>}
        </form>
      )}
    </MobileShell>
  );
}
