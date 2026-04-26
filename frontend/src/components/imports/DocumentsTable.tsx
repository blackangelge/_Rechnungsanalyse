/**
 * Dokumententabelle für einen Import-Batch.
 *
 * Zeigt alle Dokumente des Batches paginiert (50 pro Seite) an.
 * Kein automatisches Polling — das Laden/Aktualisieren der Dokumente
 * wird vom übergeordneten Import-Kontext gesteuert.
 */

"use client";

import { useState } from "react";
import { DocumentItem, documentsApi } from "@/lib/api";

const PAGE_SIZE = 50;

interface Props {
  documents: DocumentItem[];
  onRefresh: () => void;
}

/** Formatiert Bytes in lesbare Einheit (KB, MB) */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS_CLASSES: Record<string, string> = {
  pending:    "bg-gray-100 text-gray-500",
  processing: "bg-blue-100 text-blue-600",
  done:       "bg-green-100 text-green-700",
  error:      "bg-red-100 text-red-700",
};

const STATUS_LABELS: Record<string, string> = {
  pending:    "Ausstehend",
  processing: "Verarbeitung",
  done:       "Fertig",
  error:      "Fehler",
};

export default function DocumentsTable({ documents, onRefresh }: Props) {
  const [previewDocId, setPreviewDocId] = useState<number | null>(null);
  const [editingCommentId, setEditingCommentId] = useState<number | null>(null);
  const [commentValue, setCommentValue] = useState("");
  const [page, setPage] = useState(0);

  const totalPages = Math.ceil(documents.length / PAGE_SIZE);
  const pageDocs = documents.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  async function saveComment(docId: number) {
    try {
      await documentsApi.updateComment(docId, commentValue || null);
      setEditingCommentId(null);
      onRefresh();
    } catch {
      alert("Fehler beim Speichern des Kommentars");
    }
  }

  if (documents.length === 0) {
    return <p className="text-sm text-gray-500">Noch keine Dokumente vorhanden.</p>;
  }

  const errorCount = documents.filter((d) => d.status === "error").length;

  return (
    <div>
      {/* Kopfzeile: Anzahl + Fehler-Hinweis + Seitennavigation */}
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-600">
            {documents.length.toLocaleString("de-DE")} Dokumente
          </span>
          {errorCount > 0 && (
            <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-medium text-red-700">
              {errorCount} Fehler
            </span>
          )}
        </div>
        {totalPages > 1 && (
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(0)}
              disabled={page === 0}
              className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
            >
              «
            </button>
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
            >
              ‹
            </button>
            <span className="px-2 text-xs text-gray-500">
              Seite {page + 1} / {totalPages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
            >
              ›
            </button>
            <button
              onClick={() => setPage(totalPages - 1)}
              disabled={page >= totalPages - 1}
              className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-40"
            >
              »
            </button>
          </div>
        )}
      </div>

      <div className={previewDocId !== null ? "flex gap-4" : ""}>
        {/* Tabelle */}
        <div
          className={`overflow-x-auto rounded-lg border bg-white shadow-sm ${
            previewDocId !== null ? "w-1/2 shrink-0" : "w-full"
          }`}
        >
          <table className="w-full text-sm">
            <thead className="border-b bg-gray-50 text-left text-xs font-medium text-gray-500">
              <tr>
                <th className="px-4 py-3">#ID</th>
                <th className="px-4 py-3">Dateiname</th>
                <th className="px-4 py-3">Größe</th>
                <th className="px-4 py-3 text-center">Seiten</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Kommentar</th>
                <th className="px-4 py-3">Aktionen</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {pageDocs.map((doc) => (
                <tr
                  key={doc.id}
                  className={[
                    "hover:bg-gray-50",
                    previewDocId === doc.id ? "bg-blue-50" : "",
                    doc.status === "error" ? "bg-red-50" : "",
                  ].join(" ")}
                >
                  <td className="px-4 py-2.5 font-mono text-xs text-gray-400">{doc.id}</td>

                  <td className="px-4 py-2.5">
                    <p className="font-medium text-gray-800">{doc.original_filename}</p>
                    {doc.stored_filename && (
                      <p className="text-xs text-gray-400">→ {doc.stored_filename}</p>
                    )}
                    {doc.error_message && (
                      <p className="text-xs text-red-500 mt-0.5">{doc.error_message}</p>
                    )}
                  </td>

                  <td className="px-4 py-2.5 text-gray-500">{formatBytes(doc.file_size_bytes)}</td>

                  <td className="px-4 py-2.5 text-center text-gray-500">
                    {doc.page_count || "–"}
                  </td>

                  <td className="px-4 py-2.5">
                    <span
                      className={[
                        "rounded-full px-2 py-0.5 text-xs font-medium",
                        STATUS_CLASSES[doc.status] ?? "bg-gray-100 text-gray-500",
                      ].join(" ")}
                    >
                      {STATUS_LABELS[doc.status] ?? doc.status}
                    </span>
                  </td>

                  <td className="px-4 py-2.5">
                    {editingCommentId === doc.id ? (
                      <div className="flex gap-1">
                        <input
                          className="rounded border px-2 py-1 text-xs"
                          value={commentValue}
                          onChange={(e) => setCommentValue(e.target.value)}
                          autoFocus
                          onKeyDown={(e) => {
                            if (e.key === "Enter") saveComment(doc.id);
                            if (e.key === "Escape") setEditingCommentId(null);
                          }}
                        />
                        <button
                          onClick={() => saveComment(doc.id)}
                          className="rounded bg-blue-600 px-2 py-1 text-xs text-white"
                        >
                          OK
                        </button>
                        <button
                          onClick={() => setEditingCommentId(null)}
                          className="rounded border px-2 py-1 text-xs text-gray-500"
                        >
                          ✕
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => {
                          setEditingCommentId(doc.id);
                          setCommentValue(doc.comment ?? "");
                        }}
                        className="text-left text-xs text-gray-400 hover:text-gray-700"
                      >
                        {doc.comment || "+ Kommentar"}
                      </button>
                    )}
                  </td>

                  <td className="px-4 py-2.5">
                    <button
                      onClick={() => setPreviewDocId(previewDocId === doc.id ? null : doc.id)}
                      disabled={!doc.stored_filename}
                      className={[
                        "rounded px-2 py-1 text-xs font-medium transition-colors",
                        previewDocId === doc.id
                          ? "bg-blue-600 text-white hover:bg-blue-700"
                          : "text-blue-600 hover:bg-blue-50",
                        !doc.stored_filename ? "opacity-30 cursor-not-allowed" : "",
                      ].join(" ")}
                    >
                      {previewDocId === doc.id ? "Schließen" : "PDF"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {/* Seitennavigation unten */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t px-4 py-2 text-xs text-gray-500">
              <span>
                {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, documents.length)} von {documents.length.toLocaleString("de-DE")}
              </span>
              <div className="flex items-center gap-1">
                <button onClick={() => setPage(0)} disabled={page === 0}
                  className="rounded border px-2 py-1 hover:bg-gray-50 disabled:opacity-40">«</button>
                <button onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}
                  className="rounded border px-2 py-1 hover:bg-gray-50 disabled:opacity-40">‹</button>
                <span className="px-2">Seite {page + 1} / {totalPages}</span>
                <button onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}
                  className="rounded border px-2 py-1 hover:bg-gray-50 disabled:opacity-40">›</button>
                <button onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1}
                  className="rounded border px-2 py-1 hover:bg-gray-50 disabled:opacity-40">»</button>
              </div>
            </div>
          )}
        </div>

        {/* PDF-Vorschau rechts */}
        {previewDocId !== null && (
          <div className="flex w-1/2 shrink-0 flex-col rounded-lg border bg-white shadow-sm">
            <div className="flex items-center justify-between border-b px-4 py-2">
              <h3 className="text-sm font-medium">PDF-Vorschau #{previewDocId}</h3>
              <button
                onClick={() => setPreviewDocId(null)}
                className="text-xs text-gray-400 hover:text-gray-700"
              >
                ✕ Schließen
              </button>
            </div>
            <iframe
              src={documentsApi.previewUrl(previewDocId)}
              className="h-[calc(100vh-16rem)] min-h-[400px] w-full rounded-b-lg"
              title={`PDF-Vorschau Dokument #${previewDocId}`}
            />
          </div>
        )}
      </div>
    </div>
  );
}
