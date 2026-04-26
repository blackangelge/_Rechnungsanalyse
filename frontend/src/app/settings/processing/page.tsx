"use client";

import { useEffect, useRef, useState } from "react";
import { ProcessingSettings, processingSettingsApi, backupApi, extractApiError } from "@/lib/api";

export default function ProcessingSettingsPage() {
  const [settings, setSettings] = useState<ProcessingSettings | null>(null);
  const [importConcurrency, setImportConcurrency] = useState(10);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Backup / Restore
  const [downloading, setDownloading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [restoreResult, setRestoreResult] = useState<string | null>(null);
  const [backupError, setBackupError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleDownload() {
    setDownloading(true);
    setBackupError(null);
    setRestoreResult(null);
    try {
      const blob = await backupApi.download();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `rechnungsanalyse-backup-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      setBackupError(extractApiError(err, "Fehler beim Erstellen des Backups"));
    } finally {
      setDownloading(false);
    }
  }

  async function handleRestore(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!confirm("Alle bestehenden KI-Konfigurationen und Systemprompts werden durch die Backup-Daten ersetzt. Fortfahren?")) {
      e.target.value = "";
      return;
    }
    setRestoring(true);
    setBackupError(null);
    setRestoreResult(null);
    try {
      const result = await backupApi.restore(file);
      const counts = Object.entries(result.restored).map(([k, v]) => `${k}: ${v}`).join(", ");
      setRestoreResult(`✓ ${result.message} (${counts})`);
    } catch (err) {
      setBackupError(extractApiError(err, "Fehler beim Wiederherstellen des Backups"));
    } finally {
      setRestoring(false);
      e.target.value = "";
    }
  }

  useEffect(() => {
    (async () => {
      try {
        const data = await processingSettingsApi.get();
        setSettings(data);
        setImportConcurrency(data.import_concurrency);
      } catch {
        // Standardwerte behalten, Seite bleibt nutzbar
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSuccessMsg(null);
    setErrorMsg(null);
    try {
      const updated = await processingSettingsApi.update({
        import_concurrency: importConcurrency,
        ai_concurrency: settings?.ai_concurrency ?? 1,  // Wert beibehalten (KI-Analyse ist sequenziell)
      });
      setSettings(updated);
      setSuccessMsg("Einstellungen gespeichert.");
    } catch {
      setErrorMsg("Fehler beim Speichern der Einstellungen.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h1 className="mb-1 text-2xl font-bold text-gray-900">Verarbeitungseinstellungen</h1>
      <p className="mb-6 text-sm text-gray-500">
        Steuert, wie viele Vorgänge gleichzeitig ausgeführt werden.
      </p>

      {successMsg && (
        <div className="mb-4 rounded-md border border-green-200 bg-green-50 p-3 text-sm text-green-700">
          {successMsg}
        </div>
      )}
      {errorMsg && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {errorMsg}
        </div>
      )}

      {loading ? (
        <div className="py-8 text-center text-sm text-gray-400">Lade Einstellungen…</div>
      ) : (
        <form onSubmit={handleSave} className="space-y-6">

          {/* ── PDF-Import ─────────────────────────────────────────────────── */}
          <div className="rounded-lg border bg-white p-6 shadow-sm">
            <h2 className="mb-1 text-base font-semibold text-gray-800">PDF-Import</h2>
            <p className="mb-5 text-sm text-gray-500">
              Maximale Anzahl gleichzeitig verarbeiteter PDFs beim Import.
              Höhere Werte verkürzen die Importzeit, erhöhen aber die CPU- und
              Festplattenlast auf dem NAS.
            </p>

            <div className="flex items-end gap-6">
              <div className="flex flex-col gap-1.5">
                <label className="text-sm font-medium text-gray-700">
                  Parallele PDF-Verarbeitung
                </label>
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={1}
                    max={32}
                    value={importConcurrency}
                    onChange={(e) => setImportConcurrency(Number(e.target.value))}
                    className="w-56 accent-blue-600"
                  />
                  <input
                    type="number"
                    min={1}
                    max={32}
                    value={importConcurrency}
                    onChange={(e) =>
                      setImportConcurrency(
                        Math.max(1, Math.min(32, Number(e.target.value)))
                      )
                    }
                    className="w-20 rounded border border-gray-300 px-3 py-1.5 text-center text-sm font-semibold focus:border-blue-500 focus:outline-none"
                  />
                  <span className="text-sm text-gray-500">von max. 32</span>
                </div>
              </div>
            </div>

            {/* Hinweis-Badges */}
            <div className="mt-4 flex gap-2 flex-wrap">
              {importConcurrency <= 3 && (
                <span className="inline-flex items-center rounded-full bg-yellow-100 px-3 py-1 text-xs font-medium text-yellow-800">
                  Niedrig — langsamer Import, wenig Last
                </span>
              )}
              {importConcurrency >= 4 && importConcurrency <= 10 && (
                <span className="inline-flex items-center rounded-full bg-green-100 px-3 py-1 text-xs font-medium text-green-800">
                  ✓ Empfohlen für NAS
                </span>
              )}
              {importConcurrency > 10 && (
                <span className="inline-flex items-center rounded-full bg-orange-100 px-3 py-1 text-xs font-medium text-orange-800">
                  Hoch — schnell, aber hohe I/O-Last
                </span>
              )}
            </div>
          </div>

          {/* ── Aktuelle Werte (Info) ───────────────────────────────────────── */}
          {settings && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 px-5 py-3 text-sm text-gray-600">
              <span className="font-medium">Gespeicherter Wert: </span>
              Import {settings.import_concurrency}× parallel
            </div>
          )}

          {/* ── Speichern ──────────────────────────────────────────────────── */}
          <div className="flex gap-3">
            <button
              type="submit"
              disabled={saving}
              className="rounded bg-blue-600 px-5 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {saving ? "Speichere…" : "Einstellungen speichern"}
            </button>
            <button
              type="button"
              onClick={() => setImportConcurrency(10)}
              className="rounded border border-gray-300 px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors"
            >
              Auf Standard zurücksetzen (10)
            </button>
          </div>
        </form>
      )}

      {/* ── Backup / Restore ───────────────────────────────────────────────── */}
      <div className="mt-10 border-t pt-8">
        <h2 className="mb-1 text-xl font-bold text-gray-900">Backup / Restore</h2>
        <p className="mb-6 text-sm text-gray-500">
          Einstellungen sichern und wiederherstellen: KI-Konfigurationen, Systemprompts,
          Bild- und Verarbeitungseinstellungen.
        </p>

        {backupError && (
          <div className="mb-4 rounded bg-red-50 px-4 py-3 text-sm text-red-600">{backupError}</div>
        )}
        {restoreResult && (
          <div className="mb-4 rounded bg-green-50 px-4 py-3 text-sm text-green-700">{restoreResult}</div>
        )}

        <div className="space-y-4">
          {/* Backup herunterladen */}
          <div className="rounded-lg border bg-white p-6 shadow-sm">
            <h3 className="mb-1 text-base font-semibold text-gray-900">Backup herunterladen</h3>
            <p className="mb-4 text-sm text-gray-500">
              Exportiert alle KI-Konfigurationen, Systemprompts, Bildeinstellungen und
              Verarbeitungseinstellungen als JSON-Datei.
            </p>
            <button
              onClick={handleDownload}
              disabled={downloading}
              className="rounded bg-blue-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {downloading ? "Wird erstellt..." : "⬇ Backup herunterladen"}
            </button>
          </div>

          {/* Backup wiederherstellen */}
          <div className="rounded-lg border bg-white p-6 shadow-sm">
            <h3 className="mb-1 text-base font-semibold text-gray-900">Backup wiederherstellen</h3>
            <p className="mb-1 text-sm text-gray-500">
              Stellt Einstellungen aus einer Backup-Datei wieder her.
            </p>
            <p className="mb-4 rounded bg-amber-50 px-3 py-2 text-xs text-amber-700">
              ⚠ Achtung: Alle bestehenden KI-Konfigurationen und Systemprompts werden durch
              die Backup-Daten ersetzt.
            </p>
            <input ref={fileRef} type="file" accept=".json" onChange={handleRestore} className="hidden" />
            <button
              onClick={() => fileRef.current?.click()}
              disabled={restoring}
              className="rounded bg-amber-500 px-5 py-2.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50 transition-colors"
            >
              {restoring ? "Wird wiederhergestellt..." : "⬆ Backup hochladen & wiederherstellen"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
