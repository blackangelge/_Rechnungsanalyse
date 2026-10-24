"use client";

import { useCallback, useEffect, useState } from "react";
import { downloadLinkHandler } from "@/lib/download";
import MergeModal from "@/components/suppliers/MergeModal";
import CompareModal from "@/components/suppliers/CompareModal";
import OrphanModal from "@/components/suppliers/OrphanModal";
import {
  DuplicateGroup, DuplicateSearchOptions, Vendor, VendorMergeResult, VendorUpdate, VendorBankAccount,
  vendorsApi, extractApiError,
} from "@/lib/api";

function VendorEditModal({
  vendor,
  onSaved,
  onCancel,
}: {
  vendor: Vendor;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<VendorUpdate>({
    name: vendor.name,
    street: vendor.street ?? "",
    postal_code: vendor.postal_code ?? "",
    city: vendor.city ?? "",
    country: vendor.country ?? "",
    hrb_number: vendor.hrb_number ?? "",
    tax_number: vendor.tax_number ?? "",
    vat_id: vendor.vat_id ?? "",
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave() {
    setLoading(true);
    setError(null);
    try {
      await vendorsApi.update(vendor.id, form);
      onSaved();
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Speichern"));
    } finally {
      setLoading(false);
    }
  }

  function field(label: string, key: keyof VendorUpdate) {
    return (
      <div>
        <label className="mb-1 block text-xs font-medium text-gray-600">{label}</label>
        <input
          className="w-full rounded border border-gray-300 px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
          value={(form[key] as string) ?? ""}
          onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
        />
      </div>
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-lg border bg-white p-6 shadow-xl">
        <h2 className="mb-4 text-base font-semibold text-gray-900">
          Lieferant bearbeiten — {vendor.name}
        </h2>
        {error && <p className="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>}
        <div className="space-y-3">
          {field("Name *", "name")}
          <div className="grid grid-cols-3 gap-3">
            {field("Straße + Hausnummer", "street")}
            {field("PLZ", "postal_code")}
            {field("Stadt", "city")}
          </div>
          {field("Land", "country")}
          <div className="grid grid-cols-2 gap-3">
            {field("USt-IdNr.", "vat_id")}
            {field("Steuernummer", "tax_number")}
          </div>
          {field("HRB-Nummer", "hrb_number")}
        </div>
        <div className="mt-5 flex gap-3">
          <button
            onClick={handleSave}
            disabled={loading}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {loading ? "Speichern..." : "Speichern"}
          </button>
          <button
            onClick={onCancel}
            className="rounded border px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50"
          >
            Abbrechen
          </button>
        </div>
      </div>
    </div>
  );
}

function firstBank(accounts: VendorBankAccount[]): VendorBankAccount | null {
  return accounts.length > 0 ? accounts[0] : null;
}

export default function LieferantenPage() {
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editVendor, setEditVendor] = useState<Vendor | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null);
  const [duplicates, setDuplicates] = useState<DuplicateGroup[] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [exportHidden, setExportHidden] = useState(false);
  const [mergeVendors, setMergeVendors] = useState<Vendor[] | null>(null);
  const [compareVendors, setCompareVendors] = useState<Vendor[] | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [dupeLoading, setDupeLoading] = useState(false);
  const [showDupeOptions, setShowDupeOptions] = useState(false);
  const [showOrphans, setShowOrphans] = useState(false);
  const [dupeOpts, setDupeOpts] = useState<DuplicateSearchOptions>({
    criteria: ["iban", "vat_id", "tax_number", "name", "name_similar"],
    nameCheck: true, minNameSimilarity: 0.5, similarity: 0.88, maxKeyShare: 10,
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await vendorsApi.list();
      setVendors(data);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Laden der Lieferanten"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function toggleSelected(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function handleMerged(result: VendorMergeResult) {
    setMergeVendors(null);
    setSelected(new Set());
    setSuccessMsg(
      `${result.merged_ids.length} Lieferant(en) in „${result.supplier.name}" zusammengeführt — ` +
      `${result.moved_documents} Beleg(e) zugeordnet, ${result.moved_bank_accounts} Bankverbindung(en) übernommen.`,
    );
    await load();
    if (duplicates !== null) {          // Dublettenliste aktuell halten
      try { setDuplicates(await vendorsApi.duplicateGroups(dupeOpts)); } catch { /* bleibt sichtbar */ }
    }
  }

  async function handleFindDuplicates() {
    setDupeLoading(true);
    setSuccessMsg(null);
    try {
      setDuplicates(await vendorsApi.duplicateGroups(dupeOpts));
    } catch (err) {
      setError(extractApiError(err, "Fehler bei der Dublettensuche"));
    } finally {
      setDupeLoading(false);
    }
  }

  async function handleDelete(id: number) {
    try {
      await vendorsApi.delete(id);
      setDeleteConfirm(null);
      await load();
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Löschen"));
    }
  }

  return (
    <main className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-gray-900">Lieferanten</h1>
          <p className="mt-1 text-sm text-gray-500">{vendors.length} Lieferant(en)</p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowDupeOptions((v) => !v)}
            data-testid="dupe-options-toggle"
            className="rounded border border-amber-400 px-3 py-2 text-sm font-medium text-amber-700 hover:bg-amber-50"
          >
            ⚙ Suchoptionen
          </button>
          <button
            onClick={() => setShowOrphans(true)}
            data-testid="orphans-open"
            className="rounded border border-blue-400 px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-50"
          >
            Verwaiste Belege prüfen
          </button>
          <button
            onClick={handleFindDuplicates}
            disabled={dupeLoading || dupeOpts.criteria.length === 0}
            className="rounded bg-amber-500 px-4 py-2 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
          >
            {dupeLoading ? "Suche..." : "🔍 Dubletten suchen"}
          </button>
        </div>
      </div>

      {showDupeOptions && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-white p-4 text-sm" data-testid="dupe-options">
          <h2 className="mb-2 font-semibold text-gray-800">Was soll als Dublette gelten?</h2>
          <div className="mb-3 grid gap-1 sm:grid-cols-2">
            {([
              ["iban", "Gleiche IBAN"],
              ["vat_id", "Gleiche USt-IdNr."],
              ["tax_number", "Gleiche Steuernummer"],
              ["name", "Gleicher Name (ohne Rechtsform wie GmbH, KG …)"],
              ["name_similar", "Ähnlicher Name (Tippfehler, andere Schreibweise)"],
            ] as const).map(([key, label]) => (
              <label key={key} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={dupeOpts.criteria.includes(key)}
                  onChange={(e) => setDupeOpts((o) => ({
                    ...o,
                    criteria: e.target.checked ? [...o.criteria, key] : o.criteria.filter((c) => c !== key),
                  }))}
                />
                {label}
              </label>
            ))}
          </div>
          <label className="mb-2 flex items-center gap-2">
            <input
              type="checkbox" checked={dupeOpts.nameCheck}
              onChange={(e) => setDupeOpts((o) => ({ ...o, nameCheck: e.target.checked }))}
            />
            Bei IBAN / USt-IdNr. / Steuernummer zusätzlich ähnlichen Namen verlangen
            <span className="text-xs text-gray-500">(verhindert Fehltreffer durch falsch gelesene Nummern)</span>
          </label>
          {dupeOpts.nameCheck && (
            <label className="mb-2 ml-6 flex items-center gap-2">
              Mindest-Namensähnlichkeit: <strong>{Math.round(dupeOpts.minNameSimilarity * 100)} %</strong>
              <input
                type="range" min={0.1} max={0.95} step={0.05} value={dupeOpts.minNameSimilarity}
                onChange={(e) => setDupeOpts((o) => ({ ...o, minNameSimilarity: Number(e.target.value) }))}
              />
            </label>
          )}
          {dupeOpts.criteria.includes("name_similar") && (
            <label className="mb-2 flex items-center gap-2">
              Schwelle „ähnlicher Name“: <strong>{Math.round(dupeOpts.similarity * 100)} %</strong>
              <input
                type="range" min={0.6} max={1} step={0.01} value={dupeOpts.similarity}
                onChange={(e) => setDupeOpts((o) => ({ ...o, similarity: Number(e.target.value) }))}
              />
            </label>
          )}
          <label className="flex items-center gap-2">
            Nummern ignorieren, die bei mehr als
            <input
              type="number" min={2} max={1000} value={dupeOpts.maxKeyShare}
              onChange={(e) => setDupeOpts((o) => ({ ...o, maxKeyShare: Math.max(2, Number(e.target.value) || 2) }))}
              className="w-16 rounded border border-gray-300 px-1 py-0.5"
            />
            Lieferanten vorkommen
          </label>
        </div>
      )}

      {error && (
        <div className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
      )}

      {successMsg && (
        <div className="mb-4 rounded bg-green-50 px-4 py-3 text-sm text-green-700" data-testid="merge-success">{successMsg}</div>
      )}

      {/* Dubletten-Anzeige */}
      {duplicates !== null && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-4" data-testid="duplicates-panel">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold text-amber-800">
              {duplicates.length === 0
                ? "✓ Keine Dubletten gefunden"
                : `⚠ ${duplicates.length} Dublettengruppe(n) gefunden`}
            </h2>
            <button onClick={() => setDuplicates(null)} className="text-xs text-amber-600 hover:underline">
              Schließen
            </button>
          </div>
          {duplicates.length > 0 && (
            <p className="mb-3 text-xs text-amber-700">
              Gesucht nach: {dupeOpts.criteria.map((c) => ({
                iban: "IBAN", vat_id: "USt-IdNr.", tax_number: "Steuernummer",
                name: "Name", name_similar: "ähnlicher Name",
              }[c] ?? c)).join(", ")}
              {dupeOpts.nameCheck ? " (Nummern nur bei ähnlichem Namen)" : ""}.
              Es wird nichts automatisch zusammengeführt — du entscheidest je Gruppe.
            </p>
          )}
          {duplicates.map((group, gi) => {
            const conf = {
              high: { label: "sehr wahrscheinlich", cls: "bg-red-100 text-red-700" },
              medium: { label: "wahrscheinlich", cls: "bg-amber-200 text-amber-900" },
              low: { label: "möglich", cls: "bg-gray-200 text-gray-700" },
            }[group.confidence];
            return (
              <div key={gi} className="mb-3 rounded border border-amber-300 bg-white p-3" data-testid="duplicate-group">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-amber-700">Gruppe {gi + 1}</span>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${conf.cls}`}>{conf.label}</span>
                  {group.reason_labels.map((l) => (
                    <span key={l} className="rounded bg-blue-50 px-1.5 py-0.5 text-xs text-blue-700">{l}</span>
                  ))}
                  <button
                    onClick={() => setCompareVendors(group.suppliers)}
                    data-testid="compare-group"
                    className="ml-auto rounded border border-blue-300 px-3 py-1 text-xs font-medium text-blue-700 hover:bg-blue-50"
                  >
                    Vergleichen
                  </button>
                  <button
                    onClick={() => setMergeVendors(group.suppliers)}
                    className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700"
                  >
                    Zusammenführen…
                  </button>
                </div>
                <div className="space-y-1">
                  {group.suppliers.map((v) => (
                    <div key={v.id} className="flex items-center justify-between text-sm">
                      <span>
                        <strong>#{v.id}</strong> {v.name} —{" "}
                        {[v.street, v.postal_code, v.city].filter(Boolean).join(", ") || "—"}
                        {firstBank(v.bank_accounts)?.iban ? ` • ${firstBank(v.bank_accounts)?.iban}` : ""} • {v.document_count ?? 0} Beleg(e)
                      </span>
                      <button
                        onClick={() => setEditVendor(v)}
                        className="ml-3 rounded bg-blue-100 px-2 py-0.5 text-xs text-blue-700 hover:bg-blue-200"
                      >
                        Bearbeiten
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Mehrfachauswahl → manuell zusammenführen */}
      {selected.size > 0 && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2.5" data-testid="selection-bar">
          <span className="text-sm font-medium text-blue-800">{selected.size} Lieferant(en) ausgewählt</span>
          <button
            onClick={() => setCompareVendors(vendors.filter((v) => selected.has(v.id)))}
            disabled={selected.size < 2}
            data-testid="compare-selected"
            className="rounded border border-blue-400 bg-white px-3 py-1 text-sm font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-40"
          >
            Vergleichen
          </button>
          <button
            onClick={() => setMergeVendors(vendors.filter((v) => selected.has(v.id)))}
            disabled={selected.size < 2}
            className="rounded bg-blue-600 px-3 py-1 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
          >
            Zusammenführen…
          </button>
          <label className="flex items-center gap-1 text-xs text-blue-800">
            <input type="checkbox" checked={exportHidden} onChange={(e) => setExportHidden(e.target.checked)} data-testid="export-hidden" />
            Ausgeblendete Imports einbeziehen
          </label>
          <a
            href={vendorsApi.exportUrl([...selected], exportHidden)}
            download
            onClick={downloadLinkHandler(vendorsApi.exportUrl([...selected], exportHidden), "Export_Lieferanten.xlsx")}
            data-testid="export-selected"
            className="rounded bg-green-600 px-3 py-1 text-sm font-medium text-white hover:bg-green-700"
          >
            ↓ Excel exportieren
          </a>
          {selected.size < 2 && <span className="text-xs text-blue-700">Wähle mindestens zwei aus.</span>}
          <button onClick={() => setSelected(new Set())} className="ml-auto text-xs text-blue-700 hover:underline">Auswahl aufheben</button>
        </div>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Lade Lieferanten...</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="w-8 px-3 py-3"></th>
                <th className="px-4 py-3 text-left">Name</th>
                <th className="px-4 py-3 text-left">Straße</th>
                <th className="px-4 py-3 text-left">PLZ</th>
                <th className="px-4 py-3 text-left">Stadt</th>
                <th className="px-4 py-3 text-left">USt-IdNr.</th>
                <th className="px-4 py-3 text-left">IBAN</th>
                <th className="px-4 py-3 text-left">BIC</th>
                <th className="px-4 py-3 text-center">Belege</th>
                <th className="px-4 py-3 text-right">Aktionen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {vendors.length === 0 && (
                <tr>
                  <td colSpan={11} className="px-4 py-6 text-center text-gray-400">
                    Noch keine Lieferanten vorhanden. Starte eine KI-Analyse um Lieferanten zu extrahieren.
                  </td>
                </tr>
              )}
              {vendors.map((v) => {
                const bank = firstBank(v.bank_accounts);
                return (
                  <tr key={v.id} className={selected.has(v.id) ? "bg-blue-50" : "hover:bg-gray-50"}>
                    <td className="px-3 py-3">
                      <input type="checkbox" checked={selected.has(v.id)} onChange={() => toggleSelected(v.id)}
                        aria-label={`${v.name} auswählen`} className="rounded border-gray-300 text-blue-600" />
                    </td>
                    <td className="px-4 py-3 font-medium text-gray-900">{v.name}</td>
                    <td className="px-4 py-3 text-gray-600">{v.street ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">{v.postal_code ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">{v.city ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600 text-xs font-mono">{v.vat_id ?? "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">{bank?.iban ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">{bank?.bic ?? "—"}</td>
                    <td className="px-4 py-3 text-center">
                      <span className="inline-block rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">
                        {v.document_count ?? 0}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => setEditVendor(v)}
                          className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-700"
                        >
                          Bearbeiten
                        </button>
                        {deleteConfirm === v.id ? (
                          <>
                            <button
                              onClick={() => handleDelete(v.id)}
                              className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-700"
                            >
                              Bestätigen
                            </button>
                            <button
                              onClick={() => setDeleteConfirm(null)}
                              className="rounded border px-3 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50"
                            >
                              Abbrechen
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => setDeleteConfirm(v.id)}
                            className="rounded border border-red-300 px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
                          >
                            Löschen
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {compareVendors && (
        <CompareModal
          suppliers={compareVendors}
          onClose={() => setCompareVendors(null)}
          onMerge={(sup) => { setCompareVendors(null); setMergeVendors(sup); }}
        />
      )}

      {showOrphans && (
        <OrphanModal
          vendors={vendors}
          onClose={() => setShowOrphans(false)}
          onDone={async (msg) => { setShowOrphans(false); setSuccessMsg(msg); await load(); }}
        />
      )}

      {mergeVendors && (
        <MergeModal
          suppliers={mergeVendors}
          onClose={() => setMergeVendors(null)}
          onMerged={handleMerged}
        />
      )}

      {editVendor && (
        <VendorEditModal
          vendor={editVendor}
          onSaved={async () => {
            setEditVendor(null);
            await load();
          }}
          onCancel={() => setEditVendor(null)}
        />
      )}
    </main>
  );
}
