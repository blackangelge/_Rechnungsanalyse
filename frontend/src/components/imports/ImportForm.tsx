/**
 * Formular für das Starten eines neuen Imports.
 *
 * Felder:
 *   - Firmenname und Jahr (Pflichtfelder, bilden den Speicherordner-Namen)
 *   - Unterordner (optional, Unterordner unter IMPORT_BASE_PATH)
 *   - Kommentar (optional)
 *
 * Optionen:
 *   - Quelldateien löschen (Checkbox, orangefarben): PDFs aus Import-Ordner
 *     entfernen nach erfolgreichem Kopieren. Schließt Ordner-Sync aus.
 *   - Ordner-Sync (Checkbox, teal): Periodisch auf neue PDFs prüfen.
 *     Schließt Quelldateien-Löschen aus.
 *   - Dokumente an KI senden (Checkbox, blau): KI-Analyse nach Import starten.
 *     Verwendet immer die aktive Standard-KI-Konfiguration und den Standard-Prompt.
 *
 * Zeigt Pfad-Vorschau (Quelle und Ziel) unterhalb des Unterordner-Felds.
 * Nach erfolgreichem Start wird auf /imports/{id} weitergeleitet.
 */

"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AIConfig,
  aiConfigsApi,
  extractApiError,
  importsApi,
  importSettingsApi,
} from "@/lib/api";

export default function ImportForm() {
  const router = useRouter();

  const [company, setCompany] = useState("");
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [comment, setComment] = useState("");
  const [subfolder, setSubfolder] = useState("");

  const [importBasePath, setImportBasePath] = useState("");
  const [storagePath, setStoragePath] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Optionen
  const [deleteSourceFiles, setDeleteSourceFiles] = useState(false);
  const [folderSync, setFolderSync] = useState(false);
  const [analyzeAfterImport, setAnalyzeAfterImport] = useState(false);
  const [autoExport, setAutoExport] = useState(false);

  // KI-Konfigurationen (nur für canAnalyze-Prüfung)
  const [aiConfigs, setAiConfigs] = useState<AIConfig[]>([]);

  useEffect(() => {
    importSettingsApi.getPaths()
      .then((p) => { setImportBasePath(p.import_base_path); setStoragePath(p.storage_path); })
      .catch(() => {});

    aiConfigsApi.list()
      .then((configs) => setAiConfigs(configs))
      .catch(() => {});
  }, []);

  // "Dokumente an KI senden" ist nur verfügbar wenn mindestens eine KI-Konfiguration vorhanden ist
  const canAnalyze = aiConfigs.length > 0;

  // Pfad-Vorschau
  const importFolderPreview = subfolder.trim()
    ? `${importBasePath}/${subfolder.trim()}`
    : importBasePath;
  const storagePreview = company.trim() && year.trim()
    ? `${storagePath}/${company.trim()}_${year.trim()}/`
    : null;

  /** Aktiviert "Quelldateien löschen" und deaktiviert gleichzeitig "Ordner-Sync" (beide sind inkompatibel). */
  function handleDeleteSourceFiles(checked: boolean) {
    setDeleteSourceFiles(checked);
    if (checked) setFolderSync(false);
  }

  /** Aktiviert "Ordner-Sync" und deaktiviert gleichzeitig "Quelldateien löschen" (beide sind inkompatibel). */
  function handleFolderSync(checked: boolean) {
    setFolderSync(checked);
    if (checked) setDeleteSourceFiles(false);
  }

  /** Sendet das Formular an POST /api/imports und leitet auf die Import-Detailseite weiter. */
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const batch = await importsApi.create({
        company_name: company.trim(),
        year: parseInt(year),
        comment: comment.trim() || undefined,
        subfolder: subfolder.trim() || undefined,
        folder_sync: folderSync,
        analyze_after_import: analyzeAfterImport && canAnalyze,
        delete_source_files: deleteSourceFiles,
        auto_export: autoExport,
      });
      router.push(`/imports/${batch.id}`);
    } catch (err: unknown) {
      setError(extractApiError(err, "Fehler beim Starten des Imports"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6 rounded-lg border bg-white p-6 shadow-sm">
      <h2 className="text-base font-semibold">Neuer Import</h2>

      {error && (
        <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
      )}

      {/* Firma + Jahr */}
      <div className="flex gap-4">
        <div className="flex-1">
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Firmenname <span className="text-red-500">*</span>
          </label>
          <input
            className="input"
            placeholder="z.B. Lieferant GmbH"
            value={company}
            onChange={(e) => setCompany(e.target.value)}
            required
          />
        </div>
        <div className="w-28">
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Jahr <span className="text-red-500">*</span>
          </label>
          <input
            className="input"
            placeholder="2025"
            value={year}
            onChange={(e) => setYear(e.target.value)}
            required
            pattern="\d{4}"
            inputMode="numeric"
            maxLength={4}
          />
        </div>
      </div>

      {/* Unterordner */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Unterordner im Import-Verzeichnis{" "}
          <span className="text-xs font-normal text-gray-400">(optional)</span>
        </label>
        <input
          className="input"
          placeholder="z.B. 2025/Q1 oder Lieferant_GmbH"
          value={subfolder}
          onChange={(e) => setSubfolder(e.target.value)}
        />
        {/* Pfad-Vorschau */}
        <div className="mt-1.5 space-y-0.5 rounded bg-gray-50 px-3 py-2 text-xs text-gray-500">
          <div>
            <span className="font-medium">Quelle:</span>{" "}
            <span className="font-mono">{importFolderPreview || "…"}</span>
          </div>
          {storagePreview && (
            <div>
              <span className="font-medium">Ziel:</span>{" "}
              <span className="font-mono">{storagePreview}</span>
            </div>
          )}
        </div>
      </div>

      {/* Kommentar */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Kommentar{" "}
          <span className="text-xs font-normal text-gray-400">(optional)</span>
        </label>
        <textarea
          className="input"
          placeholder="Notizen zu diesem Import..."
          rows={2}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
      </div>

      {/* Import-Optionen */}
      <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 space-y-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">Optionen</p>

        {/* Quelldateien löschen */}
        <label className={`flex cursor-pointer items-start gap-3 ${folderSync ? "opacity-40 cursor-not-allowed" : ""}`}>
          <input
            type="checkbox"
            checked={deleteSourceFiles}
            onChange={(e) => handleDeleteSourceFiles(e.target.checked)}
            disabled={folderSync}
            className="mt-0.5 h-4 w-4 rounded border-gray-300 text-orange-600 focus:ring-orange-500"
          />
          <div>
            <span className="text-sm font-medium text-gray-800">Quelldateien nach Import löschen</span>
            <p className="text-xs text-gray-500 mt-0.5">
              Original-PDFs werden aus dem Import-Ordner entfernt, sobald sie erfolgreich kopiert wurden.
            </p>
          </div>
        </label>

        {/* Folder Sync */}
        <label className={`flex cursor-pointer items-start gap-3 ${deleteSourceFiles ? "opacity-40 cursor-not-allowed" : ""}`}>
          <input
            type="checkbox"
            checked={folderSync}
            onChange={(e) => handleFolderSync(e.target.checked)}
            disabled={deleteSourceFiles}
            className="mt-0.5 h-4 w-4 rounded border-gray-300 text-teal-600 focus:ring-teal-500"
          />
          <div>
            <span className="text-sm font-medium text-gray-800">Ordner-Sync aktivieren</span>
            <p className="text-xs text-gray-500 mt-0.5">
              Der Import-Ordner wird periodisch auf neue PDFs geprüft und automatisch importiert.
            </p>
          </div>
        </label>

        {/* Dokumente direkt an KI senden */}
        <label className={`flex cursor-pointer items-start gap-3 ${!canAnalyze ? "opacity-40 cursor-not-allowed" : ""}`}>
          <input
            type="checkbox"
            checked={analyzeAfterImport && canAnalyze}
            onChange={(e) => setAnalyzeAfterImport(e.target.checked)}
            disabled={!canAnalyze}
            className="mt-0.5 h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <div>
            <span className="text-sm font-medium text-gray-800">Dokumente direkt an KI senden</span>
            {!canAnalyze ? (
              <p className="text-xs text-amber-600 mt-0.5">
                Keine aktive KI-Konfiguration vorhanden — bitte unter Einstellungen anlegen.
              </p>
            ) : (
              <p className="text-xs text-gray-500 mt-0.5">
                Nach dem Import wird automatisch die KI-Analyse für alle Dokumente gestartet.
              </p>
            )}
          </div>
        </label>

        {/* Automatischer Export (unabhängig von Ordner-Sync) */}
        <label className="flex cursor-pointer items-start gap-3">
          <input
            type="checkbox"
            checked={autoExport}
            onChange={(e) => setAutoExport(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-gray-300 text-purple-600 focus:ring-purple-500"
          />
          <div>
            <span className="text-sm font-medium text-gray-800">Automatischer Export</span>
            <p className="text-xs text-gray-500 mt-0.5">
              Erzeugt wöchentlich (Zeitplan unter Einstellungen → Automatisierung) automatisch
              eine Excel-Datei mit den seit dem letzten Export neu verarbeiteten Belegen.
            </p>
          </div>
        </label>
      </div>

      <button
        type="submit"
        disabled={loading || !company.trim() || !year.trim()}
        className="rounded bg-blue-600 px-6 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "Starte Import..." : "Import starten"}
      </button>
    </form>
  );
}
