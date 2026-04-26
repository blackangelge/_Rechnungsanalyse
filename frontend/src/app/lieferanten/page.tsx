"use client";

import { useCallback, useEffect, useState } from "react";
import { Vendor, VendorUpdate, VendorBankAccount, vendorsApi, extractApiError } from "@/lib/api";

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
          <h1 className="text-2xl font-bold text-gray-900">Lieferanten</h1>
          <p className="mt-1 text-sm text-gray-500">{vendors.length} Lieferant(en)</p>
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-600">{error}</div>
      )}

      {loading ? (
        <p className="text-sm text-gray-500">Lade Lieferanten...</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-4 py-3 text-left">Name</th>
                <th className="px-4 py-3 text-left">Straße</th>
                <th className="px-4 py-3 text-left">PLZ</th>
                <th className="px-4 py-3 text-left">Stadt</th>
                <th className="px-4 py-3 text-left">USt-IdNr.</th>
                <th className="px-4 py-3 text-left">IBAN</th>
                <th className="px-4 py-3 text-left">BIC</th>
                <th className="px-4 py-3 text-right">Aktionen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {vendors.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-6 text-center text-gray-400">
                    Noch keine Lieferanten vorhanden. Starte eine KI-Analyse um Lieferanten zu extrahieren.
                  </td>
                </tr>
              )}
              {vendors.map((v) => {
                const bank = firstBank(v.bank_accounts);
                return (
                  <tr key={v.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-900">{v.name}</td>
                    <td className="px-4 py-3 text-gray-600">{v.street ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">{v.postal_code ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">{v.city ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600 text-xs font-mono">{v.vat_id ?? "—"}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-600">{bank?.iban ?? "—"}</td>
                    <td className="px-4 py-3 text-gray-600">{bank?.bic ?? "—"}</td>
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
