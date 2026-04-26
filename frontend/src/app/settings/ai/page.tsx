"use client";

import { useEffect, useState } from "react";
import { AIConfig, aiConfigsApi, extractApiError } from "@/lib/api";
import AIConfigForm from "@/components/settings/AIConfigForm";

const PRIMARY_TYPE_LABEL: Record<number, string> = {
  0: "Typ-Erkennung",
  1: "Rechnungsanalyse",
};

/** Gibt true zurück wenn timeout_at in der Zukunft liegt (temporäre Sperre aktiv). */
function isTempDisabled(config: AIConfig): boolean {
  if (!config.active || !config.timeout_at) return false;
  return new Date(config.timeout_at) > new Date();
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export default function AISettingsPage() {
  const [configs, setConfigs] = useState<AIConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editTarget, setEditTarget] = useState<AIConfig | undefined | null>(null);

  async function load() {
    try {
      setError(null);
      const data = await aiConfigsApi.list();
      setConfigs(data);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Laden der KI-Konfigurationen"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function handleDelete(config: AIConfig) {
    if (!confirm(`"${config.name}" wirklich löschen?`)) return;
    try {
      await aiConfigsApi.delete(config.id);
      await load();
    } catch (err) {
      alert(extractApiError(err, "Fehler beim Löschen"));
    }
  }

  async function handleToggleActive(config: AIConfig) {
    try {
      await aiConfigsApi.toggleActive(config.id);
      await load();
    } catch (err) {
      alert(extractApiError(err, "Fehler beim Umschalten"));
    }
  }

  async function handleClearTimeout(config: AIConfig) {
    try {
      await aiConfigsApi.clearTimeout(config.id);
      await load();
    } catch (err) {
      alert(extractApiError(err, "Fehler beim Aufheben der Sperre"));
    }
  }

  async function handleCopy(config: AIConfig) {
    try {
      await aiConfigsApi.create({
        name: `Kopie von ${config.name}`,
        ip_address: config.ip_address,
        port: config.port,
        api_key: config.api_key,
        model_name: config.model_name,
        endpoint_type: config.endpoint_type,
        primary_type: config.primary_type,
        max_tokens: config.max_tokens,
        temperature: config.temperature,
        reasoning: config.reasoning,
        chat_response: config.chat_response,
        active: false,
        parallel_request: config.parallel_request,
      });
      await load();
    } catch (err) {
      alert(extractApiError(err, "Fehler beim Kopieren"));
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">KI-Einstellungen</h1>
          <p className="text-sm text-gray-500">Verwaltung der KI-Clients (LM Studio, Ollama, OpenAI, …)</p>
        </div>
        {editTarget === null && (
          <button
            onClick={() => setEditTarget(undefined)}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            + Neuer Client
          </button>
        )}
      </div>

      {editTarget !== null && (
        <AIConfigForm
          initialData={editTarget}
          onSaved={() => { setEditTarget(null); load(); }}
          onCancel={() => setEditTarget(null)}
        />
      )}

      {loading && <p className="text-sm text-gray-500">Lade...</p>}
      {error && <p className="text-sm text-red-500">{error}</p>}

      {!loading && configs.length === 0 && !error && (
        <p className="text-sm text-gray-500">Noch keine KI-Clients vorhanden.</p>
      )}

      {/* Hinweis: Mehrere Clients können gleichzeitig aktiv sein */}
      {configs.length > 0 && (
        <div className="rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700">
          <span className="font-medium">Cluster-Betrieb:</span> Mehrere KI-Clients können gleichzeitig aktiv sein.
          Der Worker wählt bei jeder Anfrage automatisch einen aktiven Client aus.
        </div>
      )}

      <div className="space-y-3">
        {configs.map((config) => {
          const tempDisabled = isTempDisabled(config);
          return (
              <div
                key={config.id}
                className={[
                  "flex items-start justify-between rounded-lg border p-4 shadow-sm transition-colors",
                  tempDisabled
                    ? "border-orange-200 bg-orange-50"
                    : config.active
                    ? "border-green-200 bg-green-50"
                    : "border-gray-200 bg-white",
                ].join(" ")}
              >
                {/* Info-Bereich */}
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="font-medium">{config.name}</p>

                    {tempDisabled ? (
                      <span className="rounded-full bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-700">
                        ⏱ Temp. gesperrt bis {fmtTime(config.timeout_at!)}
                      </span>
                    ) : config.active ? (
                      <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                        ● Aktiv
                      </span>
                    ) : (
                      <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-400">
                        Inaktiv
                      </span>
                    )}

                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                      {PRIMARY_TYPE_LABEL[config.primary_type] ?? `Typ ${config.primary_type}`}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-gray-500">
                    <span className="font-mono">{config.model_name}</span>
                    {" · "}
                    <span className="font-mono text-gray-400">{config.ip_address}:{config.port}</span>
                    {" · "}
                    <span className="text-gray-400">{config.endpoint_type}</span>
                  </p>
                  <p className="mt-0.5 text-xs text-gray-400">
                    max_tokens: {config.max_tokens} · temperature: {config.temperature} · reasoning: {config.reasoning} · parallel: {config.parallel_request}
                  </p>
                </div>

                {/* Aktions-Buttons */}
                <div className="flex shrink-0 gap-2 ml-4">
                  {config.active ? (
                    <button
                      onClick={() => handleToggleActive(config)}
                      title={tempDisabled ? "Sperre aufheben und dauerhaft deaktivieren" : undefined}
                      className="rounded px-2 py-1 text-xs font-medium text-orange-600 hover:bg-orange-50 border border-orange-200"
                    >
                      {tempDisabled ? "Sperre aufheben & deaktiv." : "Deaktivieren"}
                    </button>
                  ) : (
                    <button
                      onClick={() => handleToggleActive(config)}
                      className="rounded px-2 py-1 text-xs font-medium text-green-600 hover:bg-green-50 border border-green-200"
                    >
                      Aktivieren
                    </button>
                  )}
                  {tempDisabled && (
                    <button
                      onClick={() => handleClearTimeout(config)}
                      title="Temporäre Sperre aufheben — KI bleibt aktiv"
                      className="rounded px-2 py-1 text-xs font-medium text-blue-600 hover:bg-blue-50 border border-blue-200"
                    >
                      Sperre aufheben
                    </button>
                  )}
                  <button
                    onClick={() => setEditTarget(config)}
                    className="rounded px-2 py-1 text-xs text-gray-600 hover:bg-gray-100"
                  >
                    Bearbeiten
                  </button>
                  <button
                    onClick={() => handleCopy(config)}
                    className="rounded px-2 py-1 text-xs text-gray-600 hover:bg-gray-100"
                  >
                    Kopieren
                  </button>
                  <button
                    onClick={() => handleDelete(config)}
                    className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                  >
                    Löschen
                  </button>
                </div>
              </div>
          );
        })}
      </div>
    </div>
  );
}
