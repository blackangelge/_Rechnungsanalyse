"use client";

import { useState } from "react";
import { AIConfig, AIConfigCreate, aiConfigsApi, extractApiError, EndpointType, ReasoningLevel, PrimaryType } from "@/lib/api";

interface Props {
  initialData?: AIConfig;
  onSaved: () => void;
  onCancel: () => void;
}

export default function AIConfigForm({ initialData, onSaved, onCancel }: Props) {
  const [name, setName] = useState(initialData?.name ?? "");
  const [ipAddress, setIpAddress] = useState(initialData?.ip_address ?? "");
  const [port, setPort] = useState(initialData?.port ?? "1234");
  const [apiKey, setApiKey] = useState(initialData?.api_key ?? "");
  const [modelName, setModelName] = useState(initialData?.model_name ?? "");
  const [endpointType, setEndpointType] = useState<EndpointType>(initialData?.endpoint_type ?? "openai");
  const [primaryType, setPrimaryType] = useState<PrimaryType>(initialData?.primary_type ?? 0);
  const [maxTokens, setMaxTokens] = useState(initialData?.max_tokens ?? 32000);
  const [temperature, setTemperature] = useState(initialData?.temperature ?? 0.1);
  const [reasoning, setReasoning] = useState<ReasoningLevel>(initialData?.reasoning ?? "off");
  const [chatResponse, setChatResponse] = useState(initialData?.chat_response ?? false);
  const [active, setActive] = useState(initialData?.active ?? false);
  const [parallelRequest, setParallelRequest] = useState(initialData?.parallel_request ?? 1);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);

    const data: AIConfigCreate = {
      name,
      ip_address: ipAddress,
      port,
      api_key: apiKey || null,
      model_name: modelName,
      endpoint_type: endpointType,
      primary_type: primaryType,
      max_tokens: maxTokens,
      temperature,
      reasoning,
      chat_response: chatResponse,
      active,
      parallel_request: parallelRequest,
    };

    try {
      if (initialData) {
        await aiConfigsApi.update(initialData.id, data);
      } else {
        await aiConfigsApi.create(data);
      }
      onSaved();
    } catch (err: unknown) {
      setError(extractApiError(err, "Fehler beim Speichern der KI-Konfiguration"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border bg-white p-6 shadow-sm">
      <h2 className="text-base font-semibold">
        {initialData ? "KI-Client bearbeiten" : "Neuer KI-Client"}
      </h2>

      {error && (
        <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
      )}

      {/* Name */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Name <span className="text-red-500">*</span>
        </label>
        <input
          className="input"
          placeholder="z.B. LM Studio Lokal"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
      </div>

      {/* IP + Port */}
      <div className="grid grid-cols-3 gap-4">
        <div className="col-span-2">
          <label className="mb-1 block text-sm font-medium text-gray-700">
            IP-Adresse / Hostname <span className="text-red-500">*</span>
          </label>
          <input
            className="input"
            placeholder="192.168.1.100"
            value={ipAddress}
            onChange={(e) => setIpAddress(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">
            Port <span className="text-red-500">*</span>
          </label>
          <input
            className="input"
            placeholder="1234"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            required
          />
        </div>
      </div>

      {/* Endpunkt-Typ */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Endpunkt-Typ
        </label>
        <select
          value={endpointType}
          onChange={(e) => setEndpointType(e.target.value as EndpointType)}
          className="rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none w-full"
        >
          <option value="openai">openai — POST /v1/chat/completions</option>
          <option value="lmstudio">lmstudio — POST /api/v1/chat</option>
        </select>
      </div>

      {/* Modell-ID */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Modell-ID <span className="text-red-500">*</span>
        </label>
        <input
          className="input"
          placeholder="z.B. llava-1.5-7b-hf"
          value={modelName}
          onChange={(e) => setModelName(e.target.value)}
          required
        />
      </div>

      {/* API-Key */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          API-Schlüssel{" "}
          <span className="text-xs font-normal text-gray-400">(optional)</span>
        </label>
        <input
          className="input"
          type="password"
          placeholder="Für lokale APIs meist nicht benötigt"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </div>

      {/* Primärtyp */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          Primärtyp
        </label>
        <select
          value={primaryType}
          onChange={(e) => setPrimaryType(parseInt(e.target.value) as PrimaryType)}
          className="rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none w-full"
        >
          <option value={0}>0 — Dokumententyp-Erkennung</option>
          <option value={1}>1 — Eingangsrechnungsanalyse</option>
        </select>
        <p className="mt-1 text-xs text-gray-400">
          Bestimmt, für welche Aufgabe dieser Client bevorzugt eingesetzt wird.
        </p>
      </div>

      {/* Max. Tokens + Temperatur */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">
            max_tokens
          </label>
          <input
            className="input"
            type="number"
            min={256}
            max={128000}
            value={maxTokens}
            onChange={(e) => setMaxTokens(parseInt(e.target.value))}
          />
        </div>
        <div>
          <label className="mb-1 block text-sm font-medium text-gray-700">
            temperature (0–1)
          </label>
          <input
            className="input"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={temperature}
            onChange={(e) => setTemperature(parseFloat(e.target.value))}
          />
        </div>
      </div>

      {/* Reasoning */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          reasoning
        </label>
        <select
          value={reasoning}
          onChange={(e) => setReasoning(e.target.value as ReasoningLevel)}
          className="rounded border border-gray-300 bg-white px-3 py-2 text-sm focus:border-blue-500 focus:outline-none w-full"
        >
          <option value="off">off — deaktiviert</option>
          <option value="low">low — gering</option>
          <option value="medium">medium — mittel</option>
          <option value="high">high — hoch</option>
          <option value="on">on — maximal</option>
        </select>
      </div>

      {/* parallel_request */}
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          parallel_request
        </label>
        <input
          className="input"
          type="number"
          min={1}
          max={32}
          value={parallelRequest}
          onChange={(e) => setParallelRequest(parseInt(e.target.value))}
        />
        <p className="mt-1 text-xs text-gray-400">
          Maximale Anzahl gleichzeitiger Anfragen an diesen KI-Client.
        </p>
      </div>

      {/* Checkboxen: chat_response + active */}
      <div className="space-y-2">
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={chatResponse}
            onChange={(e) => setChatResponse(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300"
          />
          <span>chat_response — Antwort im Chat-Format erwarten</span>
        </label>

        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={active}
            onChange={(e) => setActive(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-green-600"
          />
          <span className="font-medium">active — Client ist aktiv (Workers verwenden diesen Client)</span>
        </label>
      </div>

      {/* Aktionsbuttons */}
      <div className="flex gap-3 pt-2">
        <button
          type="submit"
          disabled={loading}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {loading ? "Speichern..." : "Speichern"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded border px-4 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50"
        >
          Abbrechen
        </button>
      </div>
    </form>
  );
}
