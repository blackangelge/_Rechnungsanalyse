/**
 * Seite: KI-Konfiguration testen (/settings/ai-test)
 *
 * Sendet ein hochgeladenes Test-PDF an eine ausgewählte KI-Konfiguration —
 * unabhängig davon, ob sie aktiv ist. Zeigt die vollständige gesendete Anfrage
 * (Bilddaten gekürzt) und die vollständige Antwort zum Debuggen.
 *
 * Schreibt nichts in die Datenbank — rein zum Testen/Debuggen von Verbindungs-
 * oder Konfigurationsproblemen, ohne einen echten Import/Beleg anzulegen.
 */

"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AIConfig,
  AIConfigTestResult,
  SystemPrompt,
  aiConfigsApi,
  extractApiError,
  systemPromptsApi,
} from "@/lib/api";

export default function AiTestPage() {
  const [configs, setConfigs] = useState<AIConfig[]>([]);
  const [prompts, setPrompts] = useState<SystemPrompt[]>([]);
  const [configId, setConfigId] = useState<number | "">("");
  const [promptId, setPromptId] = useState<number | "">("");
  const [file, setFile] = useState<File | null>(null);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AIConfigTestResult | null>(null);

  const loadOptions = useCallback(async () => {
    try {
      const [c, p] = await Promise.all([aiConfigsApi.list(), systemPromptsApi.list()]);
      setConfigs(c);
      setPrompts(p);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Laden der KI-Konfigurationen"));
    }
  }, []);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!configId || !file) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await aiConfigsApi.test(configId, file, promptId === "" ? null : promptId);
      setResult(data);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Senden der Testanfrage"));
    } finally {
      setLoading(false);
    }
  }

  const selectedConfig = configs.find((c) => c.id === configId);

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">KI-Konfiguration testen</h1>
        <p className="mt-1 text-sm text-gray-500">
          Sendet ein Testdokument direkt an eine KI-Konfiguration — auch wenn sie nicht
          aktiviert ist. Es wird nichts gespeichert (kein Import, kein Beleg); die
          vollständige Anfrage und Antwort werden zum Debuggen angezeigt.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border bg-white p-6 shadow-sm">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">
            KI-Konfiguration <span className="text-red-500">*</span>
          </label>
          <select
            className="rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none w-full"
            value={configId}
            onChange={(e) => setConfigId(e.target.value === "" ? "" : parseInt(e.target.value))}
            required
          >
            <option value="">Bitte wählen…</option>
            {configs.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} {c.active ? "(aktiv)" : "(inaktiv)"} — {c.ip_address}{c.port ? `:${c.port}` : ""}
              </option>
            ))}
          </select>
          {selectedConfig && (
            <p className="mt-1 text-xs text-gray-400">
              endpoint_type={selectedConfig.endpoint_type} · model={selectedConfig.model_name} ·
              max_tokens={selectedConfig.max_tokens}
            </p>
          )}
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Systemprompt <span className="text-xs font-normal text-gray-400">(leer = Standard-Extraktionsprompt)</span>
          </label>
          <select
            className="rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none w-full"
            value={promptId}
            onChange={(e) => setPromptId(e.target.value === "" ? "" : parseInt(e.target.value))}
          >
            <option value="">Standard-Extraktionsprompt</option>
            {prompts.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.type === 0 ? "Typ-Erkennung" : "Extraktion"})
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Test-PDF <span className="text-red-500">*</span>
          </label>
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-gray-600 file:mr-3 file:rounded file:border-0 file:bg-blue-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-blue-700 hover:file:bg-blue-100"
            required
          />
        </div>

        {error && (
          <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
        )}

        <button
          type="submit"
          disabled={loading || !configId || !file}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "Sende Testanfrage… (kann bei lokalen Modellen mehrere Minuten dauern)" : "Test senden"}
        </button>
      </form>

      {result && (
        <div className="space-y-4">
          <div
            className={`rounded-lg border px-4 py-3 text-sm font-medium ${
              result.success
                ? "border-green-200 bg-green-50 text-green-700"
                : "border-red-200 bg-red-50 text-red-700"
            }`}
          >
            {result.success ? "✓ Erfolgreich" : `✕ Fehlgeschlagen${result.error ? `: ${result.error}` : ""}`}
            {result.response && (
              <span className="ml-2 font-normal text-gray-500">
                (HTTP {result.response.status_code}, {result.response.duration_seconds.toFixed(2)}s)
              </span>
            )}
          </div>

          <JsonBlock title={`Anfrage — ${result.request.method} ${result.request.url}`} data={result.request} />

          {result.response && (
            <>
              <JsonBlock title="Antwort-Header" data={result.response.headers} />
              <RawBlock title={`Antwort-Body (HTTP ${result.response.status_code})`} text={result.response.body} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function JsonBlock({ title, data }: { title: string; data: unknown }) {
  return (
    <div className="rounded-lg border bg-white shadow-sm">
      <div className="border-b bg-gray-50 px-4 py-2 text-sm font-semibold text-gray-700">{title}</div>
      <pre className="overflow-x-auto p-4 text-xs leading-relaxed text-gray-800 whitespace-pre-wrap break-words">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}

function RawBlock({ title, text }: { title: string; text: string }) {
  let display = text;
  try {
    display = JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    // kein valides JSON — Rohtext anzeigen
  }
  return (
    <div className="rounded-lg border bg-white shadow-sm">
      <div className="border-b bg-gray-50 px-4 py-2 text-sm font-semibold text-gray-700">{title}</div>
      <pre className="overflow-x-auto p-4 text-xs leading-relaxed text-gray-800 whitespace-pre-wrap break-words max-h-[600px] overflow-y-auto">
        {display}
      </pre>
    </div>
  );
}
