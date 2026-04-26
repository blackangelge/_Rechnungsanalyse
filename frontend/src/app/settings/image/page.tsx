/**
 * Seite: Bildkonvertierungseinstellungen (/settings/image)
 *
 * Steuert, wie PDF-Seiten vor der KI-Extraktion in Bilder umgewandelt werden.
 *
 * Konfigurierbare Parameter:
 * - DPI: Renderauflösung (höher = besser lesbar, aber mehr Tokens und langsamer)
 * - Bildformat: PNG (verlustfrei) oder JPEG (kleiner/schneller)
 * - JPEG-Qualität: Kompressionsgrad 1–100 (nur bei JPEG)
 */

"use client";

import { useEffect, useState } from "react";
import { ImageSettings, imageSettingsApi } from "@/lib/api";

/** Empfohlene DPI-Voreinstellungen */
const DPI_PRESETS = [
  { label: "72 DPI — Schnell / Kleinstes Bild", value: 72 },
  { label: "150 DPI — Standard (empfohlen)", value: 150 },
  { label: "200 DPI — Gut für gedruckten Text", value: 200 },
  { label: "300 DPI — Hochwertig / Mehr Tokens", value: 300 },
];

export default function ImageSettingsPage() {
  const [settings, setSettings] = useState<ImageSettings | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Formularwerte
  const [dpi, setDpi] = useState(150);
  const [imageFormat, setImageFormat] = useState<"PNG" | "JPEG">("PNG");
  const [jpegQuality, setJpegQuality] = useState(85);

  /** Einstellungen vom Server laden */
  async function load() {
    try {
      setError(null);
      const data = await imageSettingsApi.get();
      setSettings(data);
      // Formulare mit geladenen Werten befüllen
      setDpi(data.dpi);
      setImageFormat(data.image_format);
      setJpegQuality(data.jpeg_quality);
    } catch {
      // Standardwerte werden verwendet (bereits als useState-Defaults gesetzt).
      // Kein Fehler anzeigen — das Formular ist trotzdem speicherbar (PUT legt
      // den Datensatz beim ersten Speichern automatisch an).
      console.warn("Bildeinstellungen konnten nicht geladen werden, Standardwerte werden verwendet.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  /** Einstellungen speichern */
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(false);

    try {
      await imageSettingsApi.update({
        dpi,
        image_format: imageFormat,
        jpeg_quality: jpegQuality,
      });
      setSuccess(true);
      // Erfolgsmeldung nach 3 Sekunden ausblenden
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
        <h1 className="text-xl font-semibold">Bildkonvertierungseinstellungen</h1>
        <p className="mt-1 text-sm text-gray-500">
          Steuert, wie PDF-Seiten in Bilder umgewandelt werden, bevor sie an die KI gesendet werden.
          Höhere Qualität verbessert die Texterkennung, erhöht aber die Verarbeitungszeit.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 rounded-lg border bg-white p-6 shadow-sm">

        {/* Fehler- und Erfolgsmeldungen */}
        {error && (
          <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-600">{error}</p>
        )}
        {success && (
          <p className="rounded bg-green-50 px-3 py-2 text-sm text-green-700">
            Einstellungen gespeichert.
          </p>
        )}

        {/* ── DPI-Einstellung ─────────────────────────────────────────────── */}
        <div>
          <label className="mb-2 block text-sm font-medium text-gray-700">
            Renderauflösung (DPI)
          </label>

          {/* Schnellauswahl-Buttons */}
          <div className="mb-3 flex flex-wrap gap-2">
            {DPI_PRESETS.map((preset) => (
              <button
                key={preset.value}
                type="button"
                onClick={() => setDpi(preset.value)}
                className={[
                  "rounded px-3 py-1.5 text-xs font-medium transition-colors",
                  dpi === preset.value
                    ? "bg-blue-600 text-white"
                    : "border text-gray-600 hover:bg-gray-50",
                ].join(" ")}
              >
                {preset.label}
              </button>
            ))}
          </div>

          {/* Eigener Wert */}
          <div className="flex items-center gap-3">
            <input
              type="number"
              min={72}
              max={600}
              step={1}
              value={dpi}
              onChange={(e) => setDpi(parseInt(e.target.value))}
              className="input w-28"
            />
            <span className="text-sm text-gray-500">DPI (72–600)</span>
          </div>

          <p className="mt-1 text-xs text-gray-400">
            Höhere DPI = bessere Lesbarkeit für die KI, aber größere Bilddateien und mehr API-Tokens.
          </p>
        </div>

        {/* ── Bildformat ──────────────────────────────────────────────────── */}
        <div>
          <label className="mb-2 block text-sm font-medium text-gray-700">
            Bildformat
          </label>
          <div className="flex gap-4">
            {(["PNG", "JPEG"] as const).map((fmt) => (
              <label key={fmt} className="flex cursor-pointer items-center gap-2">
                <input
                  type="radio"
                  name="imageFormat"
                  value={fmt}
                  checked={imageFormat === fmt}
                  onChange={() => setImageFormat(fmt)}
                  className="h-4 w-4"
                />
                <div>
                  <span className="text-sm font-medium">{fmt}</span>
                  <p className="text-xs text-gray-400">
                    {fmt === "PNG"
                      ? "Verlustfrei, größere Dateien, beste Qualität"
                      : "Komprimiert, kleinere Dateien, etwas weniger Schärfe"}
                  </p>
                </div>
              </label>
            ))}
          </div>
        </div>

        {/* ── JPEG-Qualität (nur bei JPEG sichtbar) ───────────────────────── */}
        {imageFormat === "JPEG" && (
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700">
              JPEG-Qualität: <span className="text-blue-600 font-bold">{jpegQuality}</span>
            </label>
            <input
              type="range"
              min={1}
              max={100}
              step={1}
              value={jpegQuality}
              onChange={(e) => setJpegQuality(parseInt(e.target.value))}
              className="w-full accent-blue-600"
            />
            <div className="mt-1 flex justify-between text-xs text-gray-400">
              <span>1 — Kleinste Datei</span>
              <span>85 — Empfohlen</span>
              <span>100 — Beste Qualität</span>
            </div>
            <p className="mt-1 text-xs text-gray-400">
              Werte unter 70 können die Texterkennung der KI beeinträchtigen.
            </p>
          </div>
        )}

        {/* ── Vorschau der Einstellungsauswirkung ─────────────────────────── */}
        <div className="rounded bg-gray-50 p-3 text-xs text-gray-500">
          <p className="font-medium text-gray-600">Aktuelle Konfiguration:</p>
          <p>
            {dpi} DPI · {imageFormat}
            {imageFormat === "JPEG" ? ` · Qualität ${jpegQuality}` : " · Verlustfrei"}
          </p>
          <p className="mt-1">
            {dpi <= 100
              ? "Schnelle Verarbeitung, möglicherweise schlechtere Texterkennung."
              : dpi <= 200
              ? "Gutes Gleichgewicht zwischen Qualität und Geschwindigkeit."
              : "Hohe Qualität — erhöhter Token-Verbrauch und längere Verarbeitungszeit."}
          </p>
        </div>

        <button
          type="submit"
          disabled={saving}
          className="rounded bg-blue-600 px-6 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? "Speichern..." : "Einstellungen speichern"}
        </button>
      </form>
    </div>
  );
}
