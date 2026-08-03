/**
 * Seite: Automatisierungseinstellungen (/settings/automation)
 *
 * Steuert mehrere unabhängige Hintergrund-Loops im Worker-Container:
 * - Ordner-Sync-Intervall: wie oft (in Minuten) Import-Batches mit aktiviertem
 *   "Ordner-Sync" auf neue PDFs geprüft werden.
 * - Export-Zeitplan: fester Wochentag + Uhrzeit, zu dem der automatische
 *   Excel-Export für Batches mit "Automatischer Export" geschrieben wird.
 * - KI-Task-Dispatcher: maximale gleichzeitige Worker, Poll-Intervall, Lock-Timeout.
 *   Anfangswerte kommen aus den ENV-Variablen WORKER_CONCURRENCY/WORKER_POLL_INTERVAL/
 *   WORKER_LOCK_TIMEOUT_MINUTES, danach ist ausschließlich diese Seite maßgeblich.
 *
 * Alle Werte wirken live ohne Container-Neustart (Worker liest sie bei jedem
 * Poll-Zyklus frisch aus der Datenbank).
 */

"use client";

import { useEffect, useState } from "react";
import { automationSettingsApi } from "@/lib/api";

const WEEKDAYS = [
  { value: 0, label: "Montag" },
  { value: 1, label: "Dienstag" },
  { value: 2, label: "Mittwoch" },
  { value: 3, label: "Donnerstag" },
  { value: 4, label: "Freitag" },
  { value: 5, label: "Samstag" },
  { value: 6, label: "Sonntag" },
];

export default function AutomationSettingsPage() {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const [intervalMinutes, setIntervalMinutes] = useState(15);
  const [weekday, setWeekday] = useState(0);
  const [hour, setHour] = useState(6);
  const [minute, setMinute] = useState(0);
  const [workerConcurrency, setWorkerConcurrency] = useState(5);
  const [workerPollInterval, setWorkerPollInterval] = useState(20);
  const [workerLockTimeout, setWorkerLockTimeout] = useState(30);

  async function load() {
    try {
      setError(null);
      const data = await automationSettingsApi.get();
      setIntervalMinutes(data.folder_sync_interval_minutes);
      setWeekday(data.export_weekday);
      setHour(data.export_hour);
      setMinute(data.export_minute);
      setWorkerConcurrency(data.worker_concurrency);
      setWorkerPollInterval(data.worker_poll_interval_seconds);
      setWorkerLockTimeout(data.worker_lock_timeout_minutes);
    } catch {
      console.warn("Automatisierungseinstellungen konnten nicht geladen werden, Standardwerte werden verwendet.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      await automationSettingsApi.update({
        folder_sync_interval_minutes: intervalMinutes,
        export_weekday: weekday,
        export_hour: hour,
        export_minute: minute,
        worker_concurrency: workerConcurrency,
        worker_poll_interval_seconds: workerPollInterval,
        worker_lock_timeout_minutes: workerLockTimeout,
      });
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch {
      setError("Fehler beim Speichern der Einstellungen");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="max-w-xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Automatisierung</h1>
        <p className="mt-1 text-sm text-gray-500">
          Steuert den Ordner-Sync (automatischer Import neuer PDFs) und den automatischen
          wöchentlichen Excel-Export. Änderungen wirken beim nächsten Prüfzyklus des
          Worker-Containers — ohne Neustart.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 rounded-lg border bg-white p-6 shadow-sm">
        {error && (
          <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
        )}
        {success && (
          <p className="rounded bg-green-50 px-3 py-2 text-sm text-green-700">
            Einstellungen gespeichert.
          </p>
        )}

        {/* Ordner-Sync-Intervall */}
        <div>
          <label className="mb-2 block text-sm font-medium text-gray-700">
            Ordner-Sync-Intervall
          </label>
          <div className="flex items-center gap-3">
            <input
              type="number"
              min={1}
              step={1}
              value={intervalMinutes}
              onChange={(e) => setIntervalMinutes(parseInt(e.target.value) || 1)}
              className="input w-28"
            />
            <span className="text-sm text-gray-500">Minuten</span>
          </div>
          <p className="mt-1 text-xs text-gray-400">
            Wie oft Import-Batches mit aktiviertem &quot;Ordner-Sync&quot; auf neue PDFs geprüft
            werden. Gilt global für alle Ordner-Sync-Imports.
          </p>
        </div>

        {/* Export-Zeitplan */}
        <div>
          <label className="mb-2 block text-sm font-medium text-gray-700">
            Automatischer Export — Wochentermin
          </label>
          <div className="flex items-center gap-3">
            <select
              value={weekday}
              onChange={(e) => setWeekday(parseInt(e.target.value))}
              className="input w-40"
            >
              {WEEKDAYS.map((wd) => (
                <option key={wd.value} value={wd.value}>{wd.label}</option>
              ))}
            </select>
            <input
              type="number"
              min={0}
              max={23}
              value={hour}
              onChange={(e) => setHour(Math.min(23, Math.max(0, parseInt(e.target.value) || 0)))}
              className="input w-20"
            />
            <span className="text-sm text-gray-500">:</span>
            <input
              type="number"
              min={0}
              max={59}
              value={minute}
              onChange={(e) => setMinute(Math.min(59, Math.max(0, parseInt(e.target.value) || 0)))}
              className="input w-20"
            />
            <span className="text-sm text-gray-500">Uhr</span>
          </div>
          <p className="mt-1 text-xs text-gray-400">
            Zeitpunkt in der lokalen Serverzeit, zu dem für Imports mit aktiviertem
            &quot;Automatischer Export&quot; die inkrementelle Excel-Datei geschrieben wird
            (nur Belege seit dem letzten Export). Der Worker prüft alle 15 Minuten, ob der
            Termin erreicht ist.
          </p>
        </div>

        {/* Worker-Einstellungen */}
        <div className="border-t pt-6">
          <h2 className="mb-4 text-sm font-semibold text-gray-800">
            KI-Task-Dispatcher (Worker-Container)
          </h2>

          <div className="space-y-4">
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700">
                Maximale gleichzeitige Worker
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={workerConcurrency}
                  onChange={(e) => setWorkerConcurrency(parseInt(e.target.value) || 1)}
                  className="input w-28"
                />
              </div>
              <p className="mt-1 text-xs text-gray-400">
                Obergrenze unabhängig von der Summe aus parallel_request über alle
                aktiven KI-Configs — verhindert Thread-Pool-Erschöpfung auf Systemen mit
                wenigen CPUs.
              </p>
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700">
                Poll-Intervall
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={workerPollInterval}
                  onChange={(e) => setWorkerPollInterval(parseInt(e.target.value) || 1)}
                  className="input w-28"
                />
                <span className="text-sm text-gray-500">Sekunden</span>
              </div>
              <p className="mt-1 text-xs text-gray-400">
                Wie oft der Dispatcher die Datenbank auf neue Tasks und geänderte
                KI-Configs prüft.
              </p>
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700">
                Lock-Timeout
              </label>
              <div className="flex items-center gap-3">
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={workerLockTimeout}
                  onChange={(e) => setWorkerLockTimeout(parseInt(e.target.value) || 1)}
                  className="input w-28"
                />
                <span className="text-sm text-gray-500">Minuten</span>
              </div>
              <p className="mt-1 text-xs text-gray-400">
                Nach dieser Zeit ohne Fortschritt gilt ein Beleg als &quot;hängengeblieben&quot;
                (z.B. Worker abgestürzt) und wird automatisch erneut versucht. Sollte
                deutlich über der längsten erwarteten KI-Antwortzeit liegen — zu niedrig
                eingestellt können noch laufende Analysen fälschlich unterbrochen werden.
              </p>
            </div>
          </div>
        </div>

        <button
          type="submit"
          disabled={saving || loading}
          className="rounded bg-blue-600 px-6 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? "Speichern..." : "Einstellungen speichern"}
        </button>
      </form>
    </div>
  );
}
