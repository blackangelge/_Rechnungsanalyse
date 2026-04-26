"use client";

import { useRef, useState } from "react";
import { backupApi, extractApiError } from "@/lib/api";

export default function BackupPage() {
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
    if (!confirm(
      "Alle bestehenden KI-Konfigurationen und Systemprompts werden durch die Backup-Daten ersetzt. Fortfahren?"
    )) {
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
      setBackupError(extractApiError(err, "Fehler beim Wiederherstellen"));
    } finally {
      setRestoring(false);
      e.target.value = "";
    }
  }

  return (
    <div className="max-w-2xl space-y-8">
      <div>
        <h1 className="text-xl font-semibold">Backups / Wiederherstellung</h1>
        <p className="mt-1 text-sm text-gray-500">
          KI-Konfigurationen, Systemprompts und Bildeinstellungen sichern und wiederherstellen.
        </p>
      </div>

      {backupError && (
        <div className="rounded bg-red-50 px-4 py-3 text-sm text-red-600 border border-red-200">
          {backupError}
        </div>
      )}
      {restoreResult && (
        <div className="rounded bg-green-50 px-4 py-3 text-sm text-green-700 border border-green-200">
          {restoreResult}
        </div>
      )}

      {/* Backup herunterladen */}
      <div className="rounded-lg border bg-white p-6 shadow-sm">
        <h2 className="mb-1 text-base font-semibold text-gray-900">Backup herunterladen</h2>
        <p className="mb-4 text-sm text-gray-500">
          Exportiert alle KI-Konfigurationen, Systemprompts und Bildeinstellungen als JSON-Datei.
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
        <h2 className="mb-1 text-base font-semibold text-gray-900">Backup wiederherstellen</h2>
        <p className="mb-1 text-sm text-gray-500">
          Stellt Einstellungen aus einer zuvor erstellten Backup-Datei wieder her.
        </p>
        <p className="mb-4 rounded bg-amber-50 px-3 py-2 text-xs text-amber-700 border border-amber-200">
          ⚠ Achtung: Alle bestehenden KI-Konfigurationen und Systemprompts werden durch die
          Backup-Daten ersetzt.
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
  );
}
