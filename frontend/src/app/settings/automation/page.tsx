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
import ChangeRuns from "@/components/settings/ChangeRuns";
import {
  ValidationBackfillState, automationSettingsApi, pdfSettingsApi, validationBackfillApi, extractApiError,
} from "@/lib/api";

const WEEKDAYS = [
  { value: 0, label: "Montag" },
  { value: 1, label: "Dienstag" },
  { value: 2, label: "Mittwoch" },
  { value: 3, label: "Donnerstag" },
  { value: 4, label: "Freitag" },
  { value: 5, label: "Samstag" },
  { value: 6, label: "Sonntag" },
];

/** Automatisches Kürzen langer PDFs vor der KI-Analyse (eigene Einstellung, eigener Speichern-Button) */
function PdfTrimSettings() {
  const [enabled, setEnabled] = useState(false);
  const [maxPages, setMaxPages] = useState(3);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    pdfSettingsApi.get()
      .then((d) => { setEnabled(d.auto_trim_enabled); setMaxPages(d.auto_trim_max_pages); })
      .catch(() => { /* Standardwerte behalten */ });
  }, []);

  async function save() {
    setSaving(true);
    setMsg(null);
    try {
      await pdfSettingsApi.update({ auto_trim_enabled: enabled, auto_trim_max_pages: maxPages });
      setMsg({ ok: true, text: "Gespeichert." });
    } catch (err) {
      setMsg({ ok: false, text: extractApiError(err, "Fehler beim Speichern") });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card p-6">
      <h2 className="mb-1 text-base font-semibold text-gray-800">Lange PDFs automatisch kürzen</h2>
      <p className="mb-5 text-sm text-gray-500">
        Hat eine PDF mehr Seiten als eingestellt, werden vor der KI-Analyse nur die ersten Seiten behalten.
        Das spart Analysezeit, wenn z.B. nur die erste Seite die Rechnung enthält. Das Original bleibt
        immer als Version 1 erhalten und kann jederzeit wiederhergestellt werden.
      </p>
      <label className="mb-4 flex items-center gap-2 text-sm font-medium text-gray-700">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)}
          className="rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
        Vor der KI-Analyse automatisch kürzen
      </label>
      <div className="mb-5 flex items-center gap-3">
        <label className="text-sm text-gray-700">Nur die ersten</label>
        <input type="number" min={1} max={500} value={maxPages} disabled={!enabled}
          onChange={(e) => setMaxPages(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
          className="w-20 rounded border border-gray-300 px-3 py-1.5 text-center text-sm font-semibold focus:border-blue-500 focus:outline-none disabled:opacity-50" />
        <span className="text-sm text-gray-700">Seite(n) behalten</span>
      </div>
      {msg && (
        <p className={`mb-3 text-sm ${msg.ok ? "text-green-700" : "text-red-600"}`}>{msg.text}</p>
      )}
      <button type="button" onClick={save} disabled={saving}
        className="btn btn-primary">
        {saving ? "Speichere…" : "Speichern"}
      </button>
    </div>
  );
}

const OUTCOME_LABELS: Record<string, string> = {
  ok: "In Ordnung", corrected: "Automatisch korrigiert", needs_review: "Prüfen!",
  filled: "Gesamtpreis ergänzt", basis_set: "Preisbasis/Verpackung/Typ ergänzt", normalized: "Auf Tonnen umgerechnet", unchanged: "Unverändert (keine Gewichtseinheit / nicht umrechenbar)",
  unchecked: "Nicht prüfbar", skipped_no_data: "Übersprungen (keine verwertbare KI-Antwort)",
};

/** Plausibilitätsprüfung für Belege nachrüsten, die vor deren Einführung analysiert wurden — ohne KI */
function ValidationBackfill() {
  const [state, setState] = useState<ValidationBackfillState | null>(null);
  const [autoCorrect, setAutoCorrect] = useState(true);
  const [normalizeUnits, setNormalizeUnits] = useState(false);   // Modus „Einheiten auf Tonnen vereinheitlichen"
  const [restoreOriginals, setRestoreOriginals] = useState(false); // Modus „Originalwerte wiederherstellen"
  const [fillPrices, setFillPrices] = useState(false);       // Modus „Gesamtpreise ergänzen"
  const [priceBasis, setPriceBasis] = useState(false);       // Modus „Preisbasis & Verpackung erkennen"
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try { setState(await validationBackfillApi.status(normalizeUnits, restoreOriginals, fillPrices, priceBasis)); } catch { /* beim nächsten Takt erneut */ }
  }

  useEffect(() => { refresh(); }, [normalizeUnits, restoreOriginals, fillPrices, priceBasis]);
  useEffect(() => {
    if (!state?.running) return;
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [state?.running]);

  async function start(dryRun: boolean) {
    if (!dryRun && priceBasis && !confirm(
      `Preisbasis (Preis je 100 …), Verpackungsinhalt und Einheitentyp bei ${state?.pending?.toLocaleString("de-DE") ?? "allen betroffenen"} Belegen ermitteln?\n\n` +
      "Nur aus bereits gespeicherten Daten (KI-Antwort, Beträge, Artikeltext) — keine KI, nichts wird überschrieben, nichts geraten. Menge, Einheit und Preise bleiben unverändert. Rückgängig im Änderungsprotokoll.",
    )) return;
    if (!dryRun && fillPrices && !confirm(
      `Fehlende Gesamtpreise bei ${state?.pending?.toLocaleString("de-DE") ?? "allen betroffenen"} Belegen aus der gespeicherten KI-Antwort ergänzen?\n\n` +
      "Es wird nichts berechnet und die KI nicht aufgerufen. Nur leere Gesamtpreise (und fehlendes Netto/Steuer) werden gefüllt; vorhandene Werte und Prüfstatus bleiben unverändert. Der Lauf ist im Änderungsprotokoll rückgängig zu machen.",
    )) return;
    if (!dryRun && normalizeUnits && !confirm(
      `Gewichtseinheiten bei ${state?.pending?.toLocaleString("de-DE") ?? "allen"} Belegen auf Tonnen vereinheitlichen?\n\n` +
      "Nur Positionen, die je Tonne bepreist sind (Preiseinheit t), werden umgerechnet (kg → t, Tonnen/TO/to → t); Preise je kg/g bleiben unverändert. " +
      "Die ursprünglichen Werte bleiben in der gespeicherten KI-Antwort erhalten. Prüfstatus bleiben unverändert.",
    )) return;
    if (!dryRun && restoreOriginals && !confirm(
      `Mengen, Einheiten und Einzelpreise bei bis zu ${state?.pending?.toLocaleString("de-DE") ?? "allen"} Belegen wieder auf den Wortlaut der Rechnung zurücksetzen?\n\n` +
      "Zurückgesetzt werden nur Positionen, die früher auf Tonnen umgerechnet wurden (z. B. 25,5 t → 25.500 kg). Gesamtpreise, Prüfstatus und echte Korrekturen bleiben unverändert.",
    )) return;
    if (!dryRun && !normalizeUnits && !restoreOriginals && !fillPrices && !priceBasis && !confirm(
      `Prüfung für ${state?.pending?.toLocaleString("de-DE") ?? "alle ungeprüften"} Belege nachrüsten?\n\n` +
      (autoCorrect ? "Eindeutige Fehler (z.B. t/kg) werden dabei automatisch korrigiert. " : "") +
      "Fehlende Positions-Gesamtpreise werden aus der gespeicherten KI-Antwort ergänzt.",
    )) return;
    setBusy(true);
    setError(null);
    try { setState(await validationBackfillApi.start({ dry_run: dryRun, auto_correct: autoCorrect, normalize_units: normalizeUnits, restore_originals: restoreOriginals, fill_prices: fillPrices, price_basis: priceBasis })); }
    catch (err) { setError(extractApiError(err, "Start fehlgeschlagen")); }
    finally { setBusy(false); }
  }

  async function cancel() {
    try { await validationBackfillApi.cancel(); await refresh(); }
    catch (err) { setError(extractApiError(err, "Abbrechen fehlgeschlagen")); }
  }

  const total = state?.total ?? 0;
  const processed = state?.processed ?? 0;
  const percent = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
  const hasRun = !!state?.started_at;

  return (
    <div className="card p-6" data-testid="backfill-card">
      <h2 className="mb-1 text-base font-semibold text-gray-800">Prüfung für den Altbestand nachrüsten</h2>
      <p className="mb-4 text-sm text-gray-500">
        Belege, die vor der Plausibilitätsprüfung analysiert wurden, haben keinen Prüfstatus. Hier wird er <b>ohne KI</b> nachgeholt:
        fehlende Positions-Gesamtpreise werden aus der gespeicherten KI-Antwort ergänzt und alle Werte durchgerechnet.
        Die „Vorschau" rechnet alles durch, speichert aber nichts. Belege mit „Prüfen!" kannst du danach gezielt neu analysieren lassen.
      </p>
      <div className="mb-3 flex gap-4 text-sm text-gray-700" data-testid="backfill-mode">
        <label className="flex items-center gap-1.5">
          <input type="radio" checked={!normalizeUnits && !restoreOriginals && !fillPrices && !priceBasis} disabled={state?.running} onChange={() => { setNormalizeUnits(false); setRestoreOriginals(false); setFillPrices(false); setPriceBasis(false); }} />
          Prüfung nachrüsten
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" checked={normalizeUnits} disabled={state?.running} onChange={() => { setNormalizeUnits(true); setRestoreOriginals(false); setFillPrices(false); setPriceBasis(false); }} />
          Einheiten auf Tonnen vereinheitlichen (nur Positionen mit Preis je Tonne; Tonnen/TO/to → t)
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" checked={restoreOriginals} disabled={state?.running} onChange={() => { setRestoreOriginals(true); setNormalizeUnits(false); setFillPrices(false); setPriceBasis(false); }} />
          Originalwerte wiederherstellen (Menge/Einheit wie auf der Rechnung)
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" data-testid="backfill-mode-fill" checked={fillPrices} disabled={state?.running} onChange={() => { setFillPrices(true); setNormalizeUnits(false); setRestoreOriginals(false); setPriceBasis(false); }} />
          Gesamtpreise ergänzen (fehlende Werte aus der KI-Antwort, auch bei bereits geprüften Belegen)
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" data-testid="backfill-mode-basis" checked={priceBasis} disabled={state?.running}
            onChange={() => { setPriceBasis(true); setFillPrices(false); setNormalizeUnits(false); setRestoreOriginals(false); }} />
          Preisbasis &amp; Verpackung erkennen (Preis je 100 …, Inhalt je VPE, Einheitentyp)
        </label>
      </div>
      <p className="mb-4 text-sm text-gray-700" data-testid="backfill-pending">
        {priceBasis ? "Belege mit Positionen ohne Preisbasis-/Typangabe" : fillPrices ? "Belege mit fehlendem Gesamtpreis" : normalizeUnits || restoreOriginals ? "Zu prüfen" : "Noch ungeprüft"}: <b>{state ? state.pending.toLocaleString("de-DE") : "…"}</b> Beleg(e)
      </p>
      <label className="mb-4 flex items-center gap-2 text-sm text-gray-700">
        <input type="checkbox" checked={autoCorrect} onChange={(e) => setAutoCorrect(e.target.checked)}
          disabled={state?.running} className="rounded border-gray-300 text-blue-600" />
        Eindeutige Fehler automatisch korrigieren (sonst nur markieren)
      </label>

      {state?.running ? (
        <div className="mb-4" data-testid="backfill-progress">
          <div className="mb-1 flex justify-between text-xs text-gray-500">
            <span>{state.dry_run ? "Vorschau läuft…" : "Nachrüstung läuft…"}</span>
            <span>{processed.toLocaleString("de-DE")} / {total.toLocaleString("de-DE")} ({percent} %)</span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-gray-100">
            <div className="h-2 rounded-full bg-blue-500 transition-all" style={{ width: `${percent}%` }} />
          </div>
          <button type="button" onClick={cancel}
            className="btn btn-danger-outline btn-sm mt-3">Abbrechen</button>
        </div>
      ) : (
        <div className="mb-4 flex gap-3">
          <button type="button" onClick={() => start(true)} disabled={busy} data-testid="backfill-preview"
            className="rounded border border-blue-300 px-4 py-2 text-sm font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-50">
            Vorschau (nichts speichern)
          </button>
          <button type="button" onClick={() => start(false)} disabled={busy || !state?.pending} data-testid="backfill-start"
            className="btn btn-primary">
            Nachrüstung starten
          </button>
        </div>
      )}

      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}

      {hasRun && !state?.running && (
        <div className="rounded border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700" data-testid="backfill-result">
          <p className="mb-1 font-medium">
            {state?.dry_run ? "Vorschau (nichts gespeichert)" : state?.cancelled ? "Abgebrochen" : "Fertig"}:{" "}
            {processed.toLocaleString("de-DE")} Beleg(e) geprüft
          </p>
          <ul className="list-inside list-disc">
            {Object.entries(state?.outcomes ?? {}).map(([k, n]) => (
              <li key={k}>{OUTCOME_LABELS[k] ?? k}: {n.toLocaleString("de-DE")}</li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-gray-500">
            {(state?.filled_prices ?? 0).toLocaleString("de-DE")} Gesamtpreise ergänzt, {(state?.corrections ?? 0).toLocaleString("de-DE")} Korrekturen
            {(state?.error_count ?? 0) > 0 && ` · ${state?.error_count} Fehler`}
          </p>
          {state?.errors?.slice(0, 5).map((e, i) => <p key={i} className="text-xs text-red-600">{e}</p>)}
          {!state?.dry_run && (state?.changed_fields ?? 0) > 0 && (
            <p className="mt-1 text-xs text-emerald-700">
              {(state?.changed_fields ?? 0).toLocaleString("de-DE")} Feld(er) in {(state?.changed_rows ?? 0).toLocaleString("de-DE")} Zeile(n) geändert — im Änderungsprotokoll unten festgehalten und dort rückgängig zu machen.
            </p>
          )}
        </div>
      )}
      <ChangeRuns key={state?.finished_at ?? "init"} />
    </div>
  );
}

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
  const [historyKeep, setHistoryKeep] = useState(3);

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
      setHistoryKeep(data.analysis_history_keep ?? 3);
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
        analysis_history_keep: historyKeep,
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

      <form onSubmit={handleSubmit} className="space-y-6 card p-6">
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

        {/* Analyse-Historie */}
        <div className="border-t pt-6">
          <h2 className="mb-1 text-sm font-semibold text-gray-800">Analyse-Historie</h2>
          <p className="mb-4 text-xs text-gray-500">
            Wird ein Beleg erneut analysiert, bleibt der bisherige Stand (Daten, Positionen, verwendete Prompts)
            erhalten und lässt sich in der Infos-Ansicht vergleichen und wiederherstellen. Ältere Stände werden
            automatisch gelöscht. 0 schaltet die Historie aus.
          </p>
          <label htmlFor="history-keep" className="mb-2 block text-sm font-medium text-gray-700">
            Aufbewahrte frühere Stände je Beleg
          </label>
          <div className="flex items-center gap-3">
            <input
              id="history-keep"
              type="number"
              min={0}
              max={20}
              step={1}
              value={historyKeep}
              onChange={(e) => setHistoryKeep(Math.max(0, Math.min(20, parseInt(e.target.value) || 0)))}
              className="input w-28"
            />
            <span className="text-sm text-gray-500">Stände</span>
          </div>
          <p className="mt-1 text-xs text-gray-400">
            Jeder Stand enthält u. a. die KI-Rohantwort (ca. 10 KB). Bei sehr vielen Belegen den Wert niedrig halten.
          </p>
        </div>

        <button
          type="submit"
          disabled={saving || loading}
          className="btn btn-primary"
        >
          {saving ? "Speichern..." : "Einstellungen speichern"}
        </button>
      </form>

      <PdfTrimSettings />

      <ValidationBackfill />
    </div>
  );
}
