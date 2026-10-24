"use client";

import { useEffect, useState } from "react";
import { Alert, Modal, Tabs } from "@/components/ui";
import NormalizerChain from "@/components/schema/NormalizerChain";
import {
  RegistryItem, SchemaDataType, SchemaField, SchemaFieldInput, UnitGroupItem, extractApiError, schemaApi, unitsApi,
} from "@/lib/api";

export const TYPE_LABELS: Record<SchemaDataType, string> = {
  text: "Text", integer: "Ganzzahl", decimal: "Dezimalzahl", date: "Datum", boolean: "Ja/Nein", enum: "Auswahl", quantity: "Menge mit Einheit",
};

const NAME_RE = /^[a-z][a-z0-9_]{0,59}$/;

/** Feld anlegen oder bearbeiten (Standardfelder sind schreibgeschützt). */
export default function FieldModal({
  field, onClose, onSave, readOnly,
}: {
  field: SchemaField | null;                 // null = neues Feld
  onClose: () => void;
  onSave: (data: SchemaFieldInput) => Promise<void>;
  readOnly: boolean;
}) {
  const locked = readOnly || Boolean(field?.is_locked);
  const [tab, setTab] = useState<"general" | "chain" | "source">("general");
  const [name, setName] = useState(field?.name ?? "");
  const [display, setDisplay] = useState(field?.display_name ?? "");
  const [type, setType] = useState<SchemaDataType>(field?.data_type ?? "text");
  const [required, setRequired] = useState(field?.required ?? false);
  const [description, setDescription] = useState(field?.description ?? "");
  const [format, setFormat] = useState(field?.output_format ?? "");
  const [enumText, setEnumText] = useState((field?.enum_values ?? []).join(", "));
  const [groupId, setGroupId] = useState<number | null>(field?.unit_group_id ?? null);
  const [source, setSource] = useState<"llm" | "system">(field?.source ?? "llm");
  const [fn, setFn] = useState(field?.system_function ?? "");
  const [chain, setChain] = useState(field?.normalizers ?? []);
  const [groups, setGroups] = useState<UnitGroupItem[]>([]);
  const [functions, setFunctions] = useState<RegistryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    unitsApi.list().then(setGroups).catch(() => setGroups([]));
    schemaApi.functions().then(setFunctions).catch(() => setFunctions([]));
  }, []);

  async function save() {
    if (!NAME_RE.test(name)) { setError("Technischer Name: Kleinbuchstaben, Ziffern und _, beginnt mit einem Buchstaben."); setTab("general"); return; }
    if (!display.trim()) { setError("Anzeigename fehlt."); setTab("general"); return; }
    setBusy(true);
    setError(null);
    try {
      await onSave({
        name, display_name: display.trim(), data_type: type, required, description: description || null,
        output_format: format || null, source,
        enum_values: type === "enum" ? enumText.split(",").map((s) => s.trim()).filter(Boolean) : null,
        unit_group_id: type === "quantity" ? groupId : null,
        system_function: source === "system" ? fn || null : null,
        normalizers: chain,
      });
    } catch (err) {
      setError(extractApiError(err, "Feld konnte nicht gespeichert werden"));
      setBusy(false);
    }
  }

  return (
    <Modal title={field ? `Feld „${field.display_name}“` : "Neues Feld"} onClose={onClose} wide
      footer={<>
        <button type="button" className="btn btn-secondary" onClick={onClose}>{locked ? "Schließen" : "Abbrechen"}</button>
        {!locked && <button type="button" className="btn btn-primary" disabled={busy} onClick={save}>Speichern</button>}
      </>}>
      {field?.is_locked && <div className="mb-4"><Alert tone="info">Standardfeld — wird vom System verwaltet und kann nicht geändert werden.</Alert></div>}
      {error && <div className="mb-4"><Alert tone="error" onClose={() => setError(null)}>{error}</Alert></div>}
      <Tabs tabs={[{ id: "general", label: "Allgemein" }, { id: "source", label: "Quelle" }, { id: "chain", label: "Normalisierer", badge: chain.length ? <span className="badge badge-blue">{chain.length}</span> : null }]} value={tab} onChange={setTab} />

      {tab === "general" && (
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="f-name">Technischer Name</label>
            <input id="f-name" className="input font-mono" disabled={locked} value={name} onChange={(e) => setName(e.target.value)} placeholder="rechnungsnummer" />
          </div>
          <div>
            <label className="label" htmlFor="f-display">Anzeigename</label>
            <input id="f-display" className="input" disabled={locked} value={display} onChange={(e) => setDisplay(e.target.value)} placeholder="Rechnungsnummer" />
          </div>
          <div>
            <label className="label" htmlFor="f-type">Datentyp</label>
            <select id="f-type" className="input" disabled={locked} value={type} onChange={(e) => setType(e.target.value as SchemaDataType)}>
              {Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <div className="flex items-end">
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" disabled={locked} checked={required} onChange={(e) => setRequired(e.target.checked)} /> Pflichtfeld (die KI soll einen Wert liefern)</label>
          </div>
          {type === "enum" && (
            <div className="sm:col-span-2">
              <label className="label" htmlFor="f-enum">Erlaubte Werte (Komma-getrennt)</label>
              <input id="f-enum" className="input" disabled={locked} value={enumText} onChange={(e) => setEnumText(e.target.value)} placeholder="EUR, USD, CHF" />
            </div>
          )}
          {type === "quantity" && (
            <div className="sm:col-span-2">
              <label className="label" htmlFor="f-group">Einheiten-Gruppe</label>
              <select id="f-group" className="input" disabled={locked} value={groupId ?? ""} onChange={(e) => setGroupId(e.target.value ? Number(e.target.value) : null)}>
                <option value="">— beliebige Einheit —</option>
                {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select>
              <p className="hint">Unbekannte Einheiten landen auf der Prüfliste (Seite „Einheiten“), nichts wird geraten.</p>
            </div>
          )}
          <div className="sm:col-span-2">
            <label className="label" htmlFor="f-desc">Beschreibung für die KI</label>
            <textarea id="f-desc" rows={3} className="input" disabled={locked} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Was soll in dieses Feld? Tipps zur Fundstelle auf dem Beleg …" />
            <p className="hint">Fließt in den Systemprompt — je genauer, desto besser die Auslesung.</p>
          </div>
          <div className="sm:col-span-2">
            <label className="label" htmlFor="f-format">Excel-Format</label>
            <input id="f-format" className="input font-mono" disabled={locked} value={format} onChange={(e) => setFormat(e.target.value)} placeholder={type === "date" ? "DD.MM.YYYY" : "#,##0.00"} />
          </div>
        </div>
      )}

      {tab === "source" && (
        <div className="space-y-4">
          <div className="flex gap-4 text-sm">
            <label className="flex items-center gap-2"><input type="radio" disabled={locked} checked={source === "llm"} onChange={() => setSource("llm")} /> Von der KI gelesen</label>
            <label className="flex items-center gap-2"><input type="radio" disabled={locked} checked={source === "system"} onChange={() => setSource("system")} /> Vom System berechnet</label>
          </div>
          {source === "system" && (
            <div>
              <label className="label" htmlFor="f-fn">Systemfunktion</label>
              <select id="f-fn" className="input" disabled={locked} value={fn} onChange={(e) => setFn(e.target.value)}>
                <option value="">— wählen —</option>
                {functions.map((f) => <option key={f.name} value={f.name}>{f.label ?? f.name}</option>)}
              </select>
              {fn && <p className="hint">{functions.find((f) => f.name === fn)?.description}</p>}
              <p className="hint">Funktionen mit Parametern (z. B. Konstante) lassen sich über das Daten-Backup/API setzen; hier gelten Standardwerte.</p>
            </div>
          )}
          {source === "llm" && <p className="text-sm text-gray-500">Das Feld erscheint im JSON-Schema, das die KI per Structured Output ausfüllt.</p>}
        </div>
      )}

      {tab === "chain" && <NormalizerChain chain={chain} onChange={setChain} readOnly={locked} />}
    </Modal>
  );
}
