"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { KiStats, WorkerStats, logsApi, workerApi } from "@/lib/api";

export default function LogsPage() {
  const [kiStats, setKiStats]               = useState<KiStats | null>(null);
  const [kiStatsLoading, setKiStatsLoading] = useState(false);
  const [kiStatsError, setKiStatsError]     = useState<string | null>(null);

  const [workerStats, setWorkerStats]               = useState<WorkerStats | null>(null);
  const [workerStatsLoading, setWorkerStatsLoading] = useState(false);
  const [workerStatsError, setWorkerStatsError]     = useState<string | null>(null);

  const workerIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── KI-Statistiken laden ──────────────────────────────────────────────────
  const loadKiStats = useCallback(async () => {
    setKiStatsLoading(true);
    setKiStatsError(null);
    try {
      setKiStats(await logsApi.kiStats());
    } catch {
      setKiStatsError("KI-Statistiken konnten nicht geladen werden.");
    } finally {
      setKiStatsLoading(false);
    }
  }, []);

  // ── Worker-Status laden ───────────────────────────────────────────────────
  const loadWorkerStats = useCallback(async () => {
    setWorkerStatsLoading(true);
    setWorkerStatsError(null);
    try {
      setWorkerStats(await workerApi.getStats());
    } catch {
      setWorkerStatsError("Worker-Status konnte nicht geladen werden.");
    } finally {
      setWorkerStatsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadKiStats();
    loadWorkerStats();
    // Worker-Status alle 30 s automatisch aktualisieren
    workerIntervalRef.current = setInterval(loadWorkerStats, 30_000);
    return () => {
      if (workerIntervalRef.current) clearInterval(workerIntervalRef.current);
    };
  }, [loadKiStats, loadWorkerStats]);

  // ── Hilfsfunktionen ───────────────────────────────────────────────────────
  function StatCard({ label, value, sub }: {
    label: string;
    value: React.ReactNode;
    sub?: React.ReactNode;
  }) {
    return (
      <div className="rounded-lg border bg-white p-4 shadow-sm">
        <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</p>
        <p className="mt-1 text-2xl font-bold text-gray-900 tabular-nums">{value}</p>
        {sub && <p className="mt-0.5 text-xs text-gray-400">{sub}</p>}
      </div>
    );
  }

  const fmt = (n: number | null | undefined) =>
    n != null ? n.toLocaleString("de-DE") : "–";

  const fmtDuration = (seconds: number | null | undefined): string => {
    if (seconds == null || seconds <= 0) return "–";
    if (seconds < 60) return `${seconds.toFixed(1).replace(".", ",")} s`;
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return `${m}:${String(s).padStart(2, "0")} min`;
  };

  const fmtTime = (iso: string | null): string => {
    if (!iso) return "–";
    try {
      return new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    } catch {
      return iso;
    }
  };

  // ── Worker-Status: Hilfswerte ─────────────────────────────────────────────
  const noAI      = workerStats?.no_ai_available ?? false;
  const hasQueue  = (workerStats?.queue_length ?? 0) > 0;

  return (
    <div className="mx-auto max-w-5xl px-6 py-8 space-y-10">

      {/* ── Kopfzeile ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">KI-Statistiken</h1>
        <div className="flex gap-2">
          <button
            onClick={loadWorkerStats}
            disabled={workerStatsLoading}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 disabled:opacity-50 transition-colors"
          >
            {workerStatsLoading ? "Lade..." : "↻ Worker"}
          </button>
          <button
            onClick={loadKiStats}
            disabled={kiStatsLoading}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 disabled:opacity-50 transition-colors"
          >
            {kiStatsLoading ? "Lade..." : "↻ Statistiken"}
          </button>
        </div>
      </div>

      {/* ── Worker-Status ─────────────────────────────────────────────── */}
      <section>
        <h2 className="mb-3 text-lg font-semibold text-gray-800">
          Worker-Status
          <span className="ml-2 text-xs font-normal text-gray-400">
            (wird alle 30 s aktualisiert)
          </span>
        </h2>

        {workerStatsError && (
          <div className="mb-3 rounded bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-600">
            {workerStatsError}
          </div>
        )}

        {/* Warnung: keine KI verfügbar */}
        {noAI && (
          <div className="mb-4 rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
            <span className="font-semibold">⚠ Keine KI verfügbar!</span>{" "}
            Alle aktiven KI-Konfigurationen sind deaktiviert oder temporär gesperrt.
            {hasQueue && (
              <span> Es warten <strong>{workerStats!.queue_length}</strong> Dokument{workerStats!.queue_length !== 1 ? "e" : ""} in der Warteschlange.</span>
            )}
            {" "}Bitte eine KI unter{" "}
            <a href="/settings/ai" className="underline font-medium">KI-Einstellungen</a>
            {" "}aktivieren.
          </div>
        )}

        {workerStats && (
          <>
            {/* Übersichtskarten */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
              <StatCard
                label="Aktive Worker"
                value={workerStats.worker_count}
                sub={`Max: ${workerStats.max_capacity}`}
              />
              <StatCard
                label="Kapazität (aktuell)"
                value={workerStats.current_capacity}
                sub="Summe parallel_request"
              />
              <StatCard
                label="In Warteschlange"
                value={workerStats.queue_length}
                sub={workerStats.in_progress > 0 ? `${workerStats.in_progress} in Bearbeitung` : undefined}
              />
              <StatCard
                label="Fehlgeschlagen"
                value={workerStats.failed_tasks}
                sub="Dauerhafte Fehler"
              />
            </div>

            {/* KI-Konfigurationen Tabelle */}
            <div className="rounded-lg border bg-white shadow-sm overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium text-gray-600">KI-Konfiguration</th>
                    <th className="px-4 py-3 text-center font-medium text-gray-600">Status</th>
                    <th className="px-4 py-3 text-right font-medium text-gray-600">Parallel</th>
                    <th className="px-4 py-3 text-right font-medium text-gray-600">Gesperrt bis</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {workerStats.ai_configs.map((c) => (
                    <tr key={c.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 font-medium text-gray-800">{c.name}</td>
                      <td className="px-4 py-3 text-center">
                        {c.temp_disabled ? (
                          <span className="rounded-full bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-700">
                            ⏱ Temp. gesperrt
                          </span>
                        ) : c.active ? (
                          <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                            ● Aktiv
                          </span>
                        ) : (
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-400">
                            Inaktiv
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-gray-700">
                        {c.parallel_request}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-gray-500">
                        {c.temp_disabled ? fmtTime(c.timeout_at) : "–"}
                      </td>
                    </tr>
                  ))}
                  {workerStats.ai_configs.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-4 py-6 text-center text-sm text-gray-400">
                        Keine KI-Konfigurationen vorhanden.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {/* ── KI-Statistiken ────────────────────────────────────────────── */}
      <section>
        <h2 className="mb-3 text-lg font-semibold text-gray-800">Analyse-Statistiken</h2>

        {kiStatsError && (
          <div className="mb-3 rounded bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-600">
            {kiStatsError}
          </div>
        )}

        {kiStatsLoading && !kiStats && (
          <p className="text-sm text-gray-400">Wird geladen...</p>
        )}

        {kiStats && (
          <>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 mb-6">
              <StatCard label="KI-Analysen" value={fmt(kiStats.total_entries)} />
              <StatCard
                label="Input-Tokens (Σ)"
                value={fmt(kiStats.sum_input_tokens)}
                sub={
                  kiStats.avg_input_tokens != null
                    ? `Ø ${Math.round(kiStats.avg_input_tokens).toLocaleString("de-DE")} pro Anfrage`
                    : undefined
                }
              />
              <StatCard label="Output-Tokens (Σ)" value={fmt(kiStats.sum_output_tokens)} />
              <StatCard label="Reasoning-Tokens (Σ)" value={fmt(kiStats.sum_reasoning)} />
              <StatCard
                label="Gesamtdauer (Σ)"
                value={fmtDuration(kiStats.sum_duration_seconds)}
                sub={
                  kiStats.avg_duration_seconds != null
                    ? `Ø ${fmtDuration(kiStats.avg_duration_seconds)} pro Anfrage`
                    : undefined
                }
              />
            </div>

            <div className="rounded-lg border bg-white shadow-sm overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left font-medium text-gray-600">Kennzahl</th>
                    <th className="px-4 py-3 text-right font-medium text-gray-600">Summe</th>
                    <th className="px-4 py-3 text-right font-medium text-gray-600">Durchschnitt</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  <tr className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-800">Analysen gesamt</td>
                    <td className="px-4 py-3 text-right tabular-nums text-gray-700">{fmt(kiStats.total_entries)}</td>
                    <td className="px-4 py-3 text-right text-gray-400">—</td>
                  </tr>
                  <tr className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-800">Input-Tokens</td>
                    <td className="px-4 py-3 text-right tabular-nums text-gray-700">{fmt(kiStats.sum_input_tokens)}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-gray-500">
                      {kiStats.avg_input_tokens != null
                        ? Math.round(kiStats.avg_input_tokens).toLocaleString("de-DE")
                        : "–"}
                    </td>
                  </tr>
                  <tr className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-800">Output-Tokens</td>
                    <td className="px-4 py-3 text-right tabular-nums text-gray-700">{fmt(kiStats.sum_output_tokens)}</td>
                    <td className="px-4 py-3 text-right text-gray-400">—</td>
                  </tr>
                  <tr className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-800">Reasoning-Tokens</td>
                    <td className="px-4 py-3 text-right tabular-nums text-gray-700">{fmt(kiStats.sum_reasoning)}</td>
                    <td className="px-4 py-3 text-right text-gray-400">—</td>
                  </tr>
                  <tr className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-800">Gesamtdauer</td>
                    <td className="px-4 py-3 text-right tabular-nums text-gray-700">{fmtDuration(kiStats.sum_duration_seconds)}</td>
                    <td className="px-4 py-3 text-right tabular-nums text-gray-500">{fmtDuration(kiStats.avg_duration_seconds)}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            {kiStats.total_entries === 0 && (
              <p className="mt-4 text-center text-sm text-gray-400">
                Noch keine KI-Analysen durchgeführt.
              </p>
            )}
          </>
        )}
      </section>
    </div>
  );
}
