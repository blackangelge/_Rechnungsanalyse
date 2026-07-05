"use client";
import { useEffect, useState } from "react";
import { ExportConfig, exportSettingsApi } from "@/lib/api";

function FieldCheckboxGroup({
  title,
  allFields,
  labels,
  selected,
  onChange,
}: {
  title: string;
  allFields: string[];
  labels: Record<string, string>;
  selected: string[];
  onChange: (fields: string[]) => void;
}) {
  function toggle(field: string) {
    if (selected.includes(field)) {
      onChange(selected.filter((f) => f !== field));
    } else {
      // Insert at original position
      onChange(allFields.filter((f) => f === field || selected.includes(f)));
    }
  }
  return (
    <div className="rounded-lg border bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-semibold">{title}</h2>
        <div className="flex gap-2">
          <button onClick={() => onChange([...allFields])} className="text-xs text-blue-600 hover:underline">Alle</button>
          <button onClick={() => onChange([])} className="text-xs text-gray-500 hover:underline">Keine</button>
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {allFields.map((field) => (
          <label key={field} className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={selected.includes(field)}
              onChange={() => toggle(field)}
              className="h-4 w-4 rounded border-gray-300 text-blue-600"
            />
            <span className="text-sm text-gray-700">{labels[field] ?? field}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

export default function ExportSettingsPage() {
  const [config, setConfig] = useState<ExportConfig | null>(null);
  const [invoiceFields, setInvoiceFields] = useState<string[]>([]);
  const [positionFields, setPositionFields] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    exportSettingsApi.get().then((cfg) => {
      setConfig(cfg);
      setInvoiceFields(cfg.invoice_fields);
      setPositionFields(cfg.position_fields);
    }).catch(() => setError("Fehler beim Laden"));
  }, []);

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await exportSettingsApi.update({ invoice_fields: invoiceFields, position_fields: positionFields });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      setError("Fehler beim Speichern");
    } finally {
      setSaving(false);
    }
  }

  if (!config) return <div className="text-sm text-gray-400">Lade...</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Excel-Export-Einstellungen</h1>
          <p className="text-sm text-gray-500">Lege fest, welche Felder im Excel-Export erscheinen sollen.</p>
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? "Speichern..." : saved ? "✓ Gespeichert" : "Speichern"}
        </button>
      </div>
      {error && <p className="text-sm text-red-500">{error}</p>}
      <FieldCheckboxGroup
        title="Sheet: Rechnungen"
        allFields={config.invoice_fields_all}
        labels={config.invoice_field_labels}
        selected={invoiceFields}
        onChange={setInvoiceFields}
      />
      <FieldCheckboxGroup
        title="Sheet: Positionen"
        allFields={config.position_fields_all}
        labels={config.position_field_labels}
        selected={positionFields}
        onChange={setPositionFields}
      />
    </div>
  );
}
