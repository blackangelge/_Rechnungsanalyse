"use client";

import { useCallback, useEffect, useState } from "react";
import { DocumentVersion, documentsApi, extractApiError } from "@/lib/api";

/** "2-5, 8" → [2,3,4,5,8] (nur Seiten 1..max; null bei ungültiger Eingabe) */
export function parsePageSpec(spec: string, max: number): number[] | null {
  const pages = new Set<number>();
  for (const part of spec.split(/[,;\s]+/).filter(Boolean)) {
    const m = part.match(/^(\d+)(?:\s*[-–]\s*(\d+))?$/);
    if (!m) return null;
    const from = parseInt(m[1], 10);
    const to = m[2] ? parseInt(m[2], 10) : from;
    if (from < 1 || to < from || to > max) return null;
    for (let p = from; p <= to; p++) pages.add(p);
  }
  return Array.from(pages).sort((a, b) => a - b);
}

const SOURCE_LABELS: Record<DocumentVersion["source"], { label: string; cls: string }> = {
  original: { label: "Original", cls: "bg-gray-100 text-gray-700" },
  manual: { label: "Manuell", cls: "bg-blue-100 text-blue-700" },
  auto: { label: "Automatisch", cls: "bg-amber-100 text-amber-800" },
  restore: { label: "Wiederhergestellt", cls: "bg-violet-100 text-violet-700" },
};

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;
}

/**
 * PDF bearbeiten: Seiten per Vorschaubild auswählen und entfernen. Jede Änderung
 * erzeugt eine neue Version — das Original und alle früheren Stände bleiben erhalten.
 */
export default function PdfEditModal({
  docId,
  filename,
  onClose,
  onChanged,
}: {
  docId: number;
  filename: string;
  onClose: () => void;
  /** Wird nach jeder gespeicherten Änderung aufgerufen (Liste neu laden) */
  onChanged: () => void;
}) {
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [lastClicked, setLastClicked] = useState<number | null>(null);
  const [specInput, setSpecInput] = useState("");
  const [keepFirst, setKeepFirst] = useState("1");
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const current = versions.find((v) => v.is_current) ?? null;
  const pageCount = current?.page_count ?? 0;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setVersions(await documentsApi.versions(docId));
      setError(null);
    } catch (err) {
      setError(extractApiError(err, "Versionen konnten nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  }, [docId]);

  useEffect(() => { load(); }, [load]);

  function togglePage(page: number, shift: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (shift && lastClicked !== null) {
        const [a, b] = [Math.min(lastClicked, page), Math.max(lastClicked, page)];
        const add = !prev.has(page);
        for (let p = a; p <= b; p++) add ? next.add(p) : next.delete(p);
      } else if (next.has(page)) {
        next.delete(page);
      } else {
        next.add(page);
      }
      return next;
    });
    setLastClicked(page);
  }

  function applySpec() {
    const pages = parsePageSpec(specInput, pageCount);
    if (!pages) {
      setError(`Ungültige Seitenangabe. Beispiel: 2-5, 8 (erlaubt: 1–${pageCount})`);
      return;
    }
    setError(null);
    setSelected(new Set(pages));
  }

  async function run(action: () => Promise<DocumentVersion>, okText: (v: DocumentVersion) => string) {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const v = await action();
      setInfo(okText(v));
      setSelected(new Set());
      setLastClicked(null);
      await load();
      onChanged();
    } catch (err) {
      setError(extractApiError(err, "Aktion fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  }

  function removeSelected() {
    const pages = Array.from(selected).sort((a, b) => a - b);
    if (pages.length === 0) return;
    if (pages.length >= pageCount) {
      setError("Es muss mindestens eine Seite übrig bleiben.");
      return;
    }
    if (!confirm(`${pages.length} Seite(n) entfernen? Das Original bleibt als frühere Version erhalten.`)) return;
    run(
      () => documentsApi.editPages(docId, { remove_pages: pages, note: note.trim() || undefined }),
      (v) => `Neue Version ${v.version_number} gespeichert (${v.page_count} Seite(n)).`,
    );
  }

  function keepSelected() {
    const pages = Array.from(selected).sort((a, b) => a - b);
    if (pages.length === 0 || pages.length === pageCount) return;
    if (!confirm(`Nur ${pages.length} Seite(n) behalten und alle anderen entfernen?`)) return;
    run(
      () => documentsApi.editPages(docId, { keep_pages: pages, note: note.trim() || undefined }),
      (v) => `Neue Version ${v.version_number} gespeichert (${v.page_count} Seite(n)).`,
    );
  }

  function keepFirstN() {
    const n = parseInt(keepFirst, 10);
    if (!Number.isFinite(n) || n < 1 || n >= pageCount) {
      setError(`Bitte eine Zahl zwischen 1 und ${pageCount - 1} angeben.`);
      return;
    }
    if (!confirm(`Nur die ersten ${n} Seite(n) behalten?`)) return;
    run(
      () => documentsApi.editPages(docId, {
        keep_pages: Array.from({ length: n }, (_, i) => i + 1),
        note: note.trim() || undefined,
      }),
      (v) => `Neue Version ${v.version_number} gespeichert (${v.page_count} Seite(n)).`,
    );
  }

  function restore(v: DocumentVersion) {
    if (!confirm(`Version ${v.version_number} (${v.page_count} Seite(n)) wiederherstellen? Es entsteht eine neue Version.`)) return;
    run(
      () => documentsApi.restoreVersion(docId, v.version_number),
      (nv) => `Version ${v.version_number} als neue Version ${nv.version_number} wiederhergestellt.`,
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="flex max-h-[92vh] w-full max-w-6xl flex-col rounded-xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">PDF bearbeiten</h2>
            <p className="text-sm text-gray-500">
              {filename} · #{docId}
              {current && <> · aktuell Version {current.version_number} ({current.page_count} Seite(n))</>}
            </p>
          </div>
          <button onClick={onClose} className="text-xl leading-none text-gray-400 hover:text-gray-700">✕</button>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-0 overflow-hidden lg:grid-cols-[1fr_20rem]">
          {/* ── Seiten ───────────────────────────────────────────────── */}
          <div className="flex min-h-0 flex-col border-b lg:border-b-0 lg:border-r">
            <div className="space-y-3 border-b bg-gray-50 px-5 py-3">
              {error && <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
              {info && <p className="rounded bg-green-50 px-3 py-2 text-sm text-green-700">{info}</p>}

              <div className="flex flex-wrap items-end gap-3">
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-gray-600">Seiten wählen (z.B. 2-5, 8)</label>
                  <div className="flex gap-1">
                    <input value={specInput} onChange={(e) => setSpecInput(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && applySpec()}
                      placeholder="2-100"
                      className="w-36 input py-1.5" />
                    <button onClick={applySpec} className="btn btn-secondary btn-sm">
                      Auswählen
                    </button>
                  </div>
                </div>
                <div className="flex flex-col gap-1">
                  <label className="text-xs font-medium text-gray-600">Schnell: nur die ersten N behalten</label>
                  <div className="flex gap-1">
                    <input type="number" min={1} value={keepFirst} onChange={(e) => setKeepFirst(e.target.value)}
                      className="w-20 input py-1.5" />
                    <button onClick={keepFirstN} disabled={busy || pageCount < 2}
                      className="rounded bg-blue-600 px-2 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50">
                      Kürzen
                    </button>
                  </div>
                </div>
                <div className="flex flex-1 flex-col gap-1 min-w-[10rem]">
                  <label className="text-xs font-medium text-gray-600">Notiz zur Version (optional)</label>
                  <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
                    placeholder="z.B. nur Rechnungsseite"
                    className="input py-1.5" />
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-gray-600">{selected.size} von {pageCount} Seite(n) ausgewählt</span>
                <button onClick={() => setSelected(new Set(Array.from({ length: pageCount }, (_, i) => i + 1)))}
                  className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-100">Alle</button>
                <button onClick={() => setSelected(new Set())}
                  className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-700 hover:bg-gray-100">Keine</button>
                <div className="ml-auto flex gap-2">
                  <button onClick={keepSelected} disabled={busy || selected.size === 0 || selected.size === pageCount}
                    className="btn btn-secondary btn-sm">
                    Nur ausgewählte behalten
                  </button>
                  <button onClick={removeSelected} disabled={busy || selected.size === 0}
                    className="btn btn-danger btn-sm">
                    Ausgewählte entfernen
                  </button>
                </div>
              </div>
              <p className="text-xs text-gray-400">Tipp: Mit gedrückter Umschalttaste (Shift) lässt sich ein Bereich markieren.</p>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {loading && !current && <p className="py-8 text-center text-sm text-gray-400">Lade…</p>}
              {current && (
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5">
                  {Array.from({ length: pageCount }, (_, i) => i + 1).map((page) => {
                    const isSel = selected.has(page);
                    return (
                      <button
                        key={`${current.version_number}-${page}`}
                        type="button"
                        onClick={(e) => togglePage(page, e.shiftKey)}
                        className={`relative overflow-hidden rounded-lg border-2 bg-white text-left transition-colors ${
                          isSel ? "border-red-500 ring-2 ring-red-200" : "border-gray-200 hover:border-blue-300"
                        }`}
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={documentsApi.pageThumbnailUrl(docId, page, current.version_number)}
                          alt={`Seite ${page}`}
                          loading="lazy"
                          className={`block w-full bg-gray-100 ${isSel ? "opacity-60" : ""}`}
                          style={{ aspectRatio: "1 / 1.41" }}
                        />
                        <span className={`absolute left-1.5 top-1.5 rounded px-1.5 py-0.5 text-xs font-semibold ${
                          isSel ? "bg-red-600 text-white" : "bg-gray-800/70 text-white"
                        }`}>
                          {page}
                        </span>
                        {isSel && <span className="absolute right-1.5 top-1.5 text-lg leading-none text-red-600">✕</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* ── Versionen ────────────────────────────────────────────── */}
          <div className="min-h-0 overflow-y-auto bg-gray-50 p-4">
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-gray-700">Versionen</h3>
            <ol className="space-y-2">
              {[...versions].reverse().map((v) => {
                const src = SOURCE_LABELS[v.source] ?? SOURCE_LABELS.manual;
                return (
                  <li key={v.version_number}
                    className={`rounded-lg border p-3 text-sm ${v.is_current ? "border-blue-300 bg-blue-50" : "border-gray-200 bg-white"}`}>
                    <div className="mb-1 flex flex-wrap items-center gap-1.5">
                      <span className="font-semibold text-gray-900">Version {v.version_number}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${src.cls}`}>{src.label}</span>
                      {v.is_current && <span className="rounded-full bg-blue-600 px-2 py-0.5 text-xs font-medium text-white">aktuell</span>}
                    </div>
                    <p className="text-xs text-gray-600">
                      {v.page_count} Seite(n) · {formatBytes(v.file_size_bytes)}
                      {v.created_at && <> · {new Date(v.created_at).toLocaleString("de-DE")}</>}
                    </p>
                    {v.description && <p className="mt-1 text-xs text-gray-700">{v.description}</p>}
                    <div className="mt-2 flex gap-2">
                      <a href={documentsApi.versionPreviewUrl(docId, v.version_number)} target="_blank" rel="noreferrer"
                        className="rounded border border-gray-300 bg-white px-2 py-0.5 text-xs text-gray-700 hover:bg-gray-100">
                        Ansehen
                      </a>
                      {!v.is_current && (
                        <button onClick={() => restore(v)} disabled={busy}
                          className="rounded border border-violet-300 bg-white px-2 py-0.5 text-xs text-violet-700 hover:bg-violet-50 disabled:opacity-50">
                          Wiederherstellen
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
            <p className="mt-4 text-xs text-gray-500">
              Nach einer Änderung bleibt die bisherige KI-Extraktion bestehen. Bitte den Beleg danach
              erneut analysieren, damit die Daten zur neuen PDF passen.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
