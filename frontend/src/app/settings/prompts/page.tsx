/**
 * Seite: Systemprompts verwalten (/settings/prompts)
 *
 * CRUD für KI-Systemprompts. Zwei Prompt-Typen:
 *   type=0 — Dokumententyp-Erkennungsprompt (violett markiert)
 *             Aktiviert die zweistufige Analyse: erst Typ erkennen, dann ggf. extrahieren.
 *             Nur ein Prompt kann gleichzeitig type=0 sein.
 *   type=1 — Standard-Extraktionsprompt für Eingangsrechnungen (blau markiert)
 *
 * Beim Erstellen/Bearbeiten: Dropdown für Prompt-Typ.
 * Liste zeigt farbige Typ-Badges und Vorschau des Prompt-Inhalts.
 */

"use client";

import { useEffect, useState } from "react";
import { SystemPrompt, systemPromptsApi } from "@/lib/api";

const TYPE_LABEL: Record<number, string> = {
  0: "Dokumententyp-Erkennung",
  1: "Standard-Extraktion (Eingangsrechnung)",
};

const TYPE_COLOR: Record<number, string> = {
  0: "bg-violet-100 text-violet-700",
  1: "bg-blue-100 text-blue-700",
};

export default function SystemPromptsPage() {
  const [prompts, setPrompts] = useState<SystemPrompt[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<number | "new" | null>(null);
  const [formName, setFormName] = useState("");
  const [formContent, setFormContent] = useState("");
  const [formType, setFormType] = useState<number>(0);
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      setError(null);
      const data = await systemPromptsApi.list();
      setPrompts(data);
    } catch {
      setError("Fehler beim Laden der Systemprompts");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  function openNew() {
    setEditingId("new");
    setFormName("");
    setFormContent("");
    setFormType(0);
  }

  function openEdit(p: SystemPrompt) {
    setEditingId(p.id);
    setFormName(p.name);
    setFormContent(p.content);
    setFormType(p.type ?? 0);
  }

  async function save() {
    if (!formName.trim() || !formContent.trim()) return;
    setSaving(true);
    try {
      if (editingId === "new") {
        await systemPromptsApi.create({ name: formName, content: formContent, type: formType });
      } else if (editingId !== null) {
        await systemPromptsApi.update(editingId, { name: formName, content: formContent, type: formType });
      }
      setEditingId(null);
      await load();
    } catch {
      alert("Fehler beim Speichern");
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: number) {
    if (!confirm("Systemprompt löschen?")) return;
    try {
      await systemPromptsApi.delete(id);
      await load();
    } catch {
      alert("Fehler beim Löschen");
    }
  }

  async function copyPrompt(p: SystemPrompt) {
    try {
      await systemPromptsApi.create({
        name: `Kopie von ${p.name}`,
        content: p.content,
        type: p.type,
      });
      await load();
    } catch {
      alert("Fehler beim Kopieren");
    }
  }

  return (
    <div className="max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Systemprompts</h1>
          <p className="text-sm text-gray-500">
            Typ 0 = Dokumententyp-Erkennung · Typ 1 = Standard-Extraktion (Eingangsrechnung)
          </p>
        </div>
        <button
          onClick={openNew}
          disabled={editingId !== null}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          + Neuer Prompt
        </button>
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}
      {loading && <p className="text-sm text-gray-500">Lade...</p>}

      {/* Formular */}
      {editingId !== null && (
        <div className="rounded-lg border bg-white p-5 shadow-sm space-y-4">
          <h2 className="text-sm font-semibold">
            {editingId === "new" ? "Neuer Systemprompt" : "Systemprompt bearbeiten"}
          </h2>

          {/* Name */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Name</label>
            <input
              className="input"
              placeholder="z.B. Rechnungsextraktion Standard"
              value={formName}
              onChange={(e) => setFormName(e.target.value)}
            />
          </div>

          {/* Typ */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Typ</label>
            <select
              value={formType}
              onChange={(e) => setFormType(parseInt(e.target.value))}
              className="rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none w-full"
            >
              <option value={0}>0 — Dokumententyp-Erkennung</option>
              <option value={1}>1 — Standard-Extraktion (Eingangsrechnung)</option>
            </select>
          </div>

          {formType === 0 && (
            <div className="rounded-lg border border-violet-200 bg-violet-50 p-4 space-y-3 text-xs">
              <p className="font-semibold text-violet-800">ℹ️ Erwartetes KI-Antwortformat — Dokumententyp-Erkennung</p>
              <pre className="rounded bg-violet-100 px-3 py-2 font-mono text-violet-900 whitespace-pre-wrap">
{`{"dokumententyp_id": 1, "dokumententyp_name": "Eingangsrechnung"}`}
              </pre>
              <p className="text-violet-700">
                <strong>ID 1 (Eingangsrechnung)</strong> löst die vollständige Rechnungsextraktion aus.
                Alle anderen IDs (2–15) markieren das Dokument nur mit dem Dokumententyp.
              </p>
              <p className="text-violet-600 font-medium">Verfügbare Dokumententypen:</p>
              <ul className="text-violet-700 space-y-0.5 list-none">
                <li><strong>1 — Eingangsrechnung</strong> → vollständige Extraktion</li>
                <li>2 — Ausgangsrechnung</li>
                <li>3 — Lieferschein</li>
                <li>4 — Bestellbestätigung</li>
                <li>5 — Angebot</li>
                <li>6 — Gutschrift / Storno</li>
                <li>7 — Mahnung</li>
                <li>8 — Kontoauszug</li>
                <li>9 — Vertrag</li>
                <li>10 — Lohnabrechnung</li>
                <li>11 — Steuer- / Behördendokument</li>
                <li>12 — Reisekostenabrechnung</li>
                <li>13 — Kassenbon / Quittung</li>
                <li>14 — Sonstiges kaufmännisches Dokument</li>
                <li>15 — Unbekannt</li>
              </ul>
            </div>
          )}

          {/* Inhalt */}
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700">Prompt-Inhalt</label>
            <textarea
              className="input font-mono text-xs"
              rows={10}
              placeholder="Du bist ein Experte für die Analyse von Rechnungen..."
              value={formContent}
              onChange={(e) => setFormContent(e.target.value)}
            />
          </div>

          <div className="flex gap-2">
            <button
              onClick={save}
              disabled={saving || !formName.trim() || !formContent.trim()}
              className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {saving ? "Speichern..." : "Speichern"}
            </button>
            <button
              onClick={() => setEditingId(null)}
              className="rounded border px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
            >
              Abbrechen
            </button>
          </div>
        </div>
      )}

      {/* Liste */}
      {prompts.length === 0 && !loading ? (
        <div className="rounded-lg border bg-white p-8 text-center text-sm text-gray-400">
          Noch keine Systemprompts vorhanden.
        </div>
      ) : (
        <div className="space-y-3">
          {prompts.map((p) => (
            <div key={p.id} className="rounded-lg border bg-white p-4 shadow-sm">
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-medium text-gray-800">{p.name}</span>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${TYPE_COLOR[p.type] ?? "bg-gray-100 text-gray-600"}`}>
                      {TYPE_LABEL[p.type] ?? `Typ ${p.type}`}
                    </span>
                  </div>
                  <pre className="mt-2 max-h-28 overflow-y-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-xs text-gray-600 font-mono">
                    {p.content}
                  </pre>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    onClick={() => openEdit(p)}
                    disabled={editingId !== null}
                    className="rounded px-2 py-1 text-xs text-blue-600 hover:bg-blue-50 disabled:opacity-40"
                  >
                    Bearbeiten
                  </button>
                  <button
                    onClick={() => copyPrompt(p)}
                    disabled={editingId !== null}
                    className="rounded px-2 py-1 text-xs text-gray-600 hover:bg-gray-100 disabled:opacity-40"
                  >
                    Kopieren
                  </button>
                  <button
                    onClick={() => remove(p.id)}
                    className="rounded px-2 py-1 text-xs text-red-500 hover:bg-red-50"
                  >
                    Löschen
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
