"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  WorkflowTask,
  WorkflowTaskListResponse,
  extractApiError,
  tasksApi,
} from "@/lib/api";

const STATUS_LABELS: Record<WorkflowTask["status"], string> = {
  pending:     "Wartend",
  in_progress: "In Bearbeitung",
  completed:   "Abgeschlossen",
  failed:      "Fehlgeschlagen",
};

const STATUS_COLORS: Record<WorkflowTask["status"], string> = {
  pending:     "bg-yellow-100 text-yellow-700",
  in_progress: "bg-blue-100 text-blue-700",
  completed:   "bg-green-100 text-green-700",
  failed:      "bg-red-100 text-red-700",
};

const PAGE_SIZE = 50;

export default function TasksPage() {
  const [data, setData]           = useState<WorkflowTaskListResponse | null>(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [offset, setOffset]       = useState(0);
  const [deleting, setDeleting]   = useState<number | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (currentOffset = offset, currentStatus = statusFilter) => {
    setLoading(true);
    setError(null);
    try {
      const result = await tasksApi.list({
        status: currentStatus || undefined,
        limit: PAGE_SIZE,
        offset: currentOffset,
      });
      setData(result);
    } catch (err) {
      setError(extractApiError(err, "Tasks konnten nicht geladen werden."));
    } finally {
      setLoading(false);
    }
  }, [offset, statusFilter]);

  useEffect(() => {
    load();
    intervalRef.current = setInterval(() => load(), 10_000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [load]);

  const handleStatusChange = (s: string) => {
    setStatusFilter(s);
    setOffset(0);
    load(0, s);
  };

  const handlePageChange = (newOffset: number) => {
    setOffset(newOffset);
    load(newOffset, statusFilter);
  };

  const handleDeleteOne = async (taskId: number) => {
    setDeleting(taskId);
    try {
      await tasksApi.deleteOne(taskId);
      setActionMsg(`Task #${taskId} gelöscht.`);
      load();
    } catch (err) {
      setActionMsg(`Fehler: ${extractApiError(err)}`);
    } finally {
      setDeleting(null);
      setTimeout(() => setActionMsg(null), 3000);
    }
  };

  const handleBulkDelete = async (status: "completed" | "failed") => {
    if (!confirm(`Alle ${STATUS_LABELS[status]}-Tasks löschen?`)) return;
    setBulkDeleting(true);
    try {
      const result = await tasksApi.deleteByStatus(status);
      setActionMsg(`${result.deleted} Task${result.deleted !== 1 ? "s" : ""} gelöscht.`);
      setOffset(0);
      load(0, statusFilter);
    } catch (err) {
      setActionMsg(`Fehler: ${extractApiError(err)}`);
    } finally {
      setBulkDeleting(false);
      setTimeout(() => setActionMsg(null), 3000);
    }
  };

  const fmtDate = (iso: string | null) => {
    if (!iso) return "–";
    try {
      return new Date(iso).toLocaleString("de-DE", {
        day: "2-digit", month: "2-digit", year: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
    } catch { return iso; }
  };

  const total   = data?.total ?? 0;
  const items   = data?.items ?? [];
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <div className="mx-auto max-w-6xl px-6 py-8 space-y-6">

      {/* Kopfzeile */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-gray-900">Workflow-Tasks</h1>
        <div className="flex items-center gap-2 flex-wrap">
          {/* Bulk-Delete */}
          <button
            onClick={() => handleBulkDelete("completed")}
            disabled={bulkDeleting}
            className="rounded border border-green-300 px-3 py-1.5 text-sm font-medium text-green-700 hover:bg-green-50 disabled:opacity-50 transition-colors"
          >
            Abgeschlossene löschen
          </button>
          <button
            onClick={() => handleBulkDelete("failed")}
            disabled={bulkDeleting}
            className="rounded border border-red-300 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50 transition-colors"
          >
            Fehlgeschlagene löschen
          </button>
          <button
            onClick={() => load()}
            disabled={loading}
            className="rounded border border-gray-300 px-3 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 disabled:opacity-50 transition-colors"
          >
            {loading ? "Lade..." : "↻ Aktualisieren"}
          </button>
        </div>
      </div>

      {/* Status-Meldung */}
      {actionMsg && (
        <div className="rounded bg-blue-50 border border-blue-200 px-4 py-2 text-sm text-blue-700">
          {actionMsg}
        </div>
      )}

      {/* Fehler */}
      {error && (
        <div className="rounded bg-red-50 border border-red-200 px-4 py-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {/* Filter + Zähler */}
      <div className="flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <label className="text-sm font-medium text-gray-600">Status:</label>
          <select
            value={statusFilter}
            onChange={(e) => handleStatusChange(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="">Alle</option>
            <option value="pending">Wartend</option>
            <option value="in_progress">In Bearbeitung</option>
            <option value="completed">Abgeschlossen</option>
            <option value="failed">Fehlgeschlagen</option>
          </select>
        </div>
        <span className="text-sm text-gray-500">
          {total.toLocaleString("de-DE")} Task{total !== 1 ? "s" : ""}
          {statusFilter ? ` (${STATUS_LABELS[statusFilter as WorkflowTask["status"]]})` : " gesamt"}
        </span>
        <span className="text-xs text-gray-400">(wird alle 10 s aktualisiert)</span>
      </div>

      {/* Tabelle */}
      <div className="rounded-lg border bg-white shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-4 py-3 text-left font-medium text-gray-600 w-14">ID</th>
              <th className="px-4 py-3 text-left font-medium text-gray-600">Typ</th>
              <th className="px-4 py-3 text-left font-medium text-gray-600">Dokument</th>
              <th className="px-4 py-3 text-center font-medium text-gray-600">Status</th>
              <th className="px-4 py-3 text-center font-medium text-gray-600 w-20">Versuche</th>
              <th className="px-4 py-3 text-left font-medium text-gray-600">Worker</th>
              <th className="px-4 py-3 text-left font-medium text-gray-600">Fehler</th>
              <th className="px-4 py-3 text-left font-medium text-gray-600">Erstellt</th>
              <th className="px-4 py-3 text-left font-medium text-gray-600">Aktualisiert</th>
              <th className="px-4 py-3 w-10"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {items.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-8 text-center text-sm text-gray-400">
                  {loading ? "Wird geladen..." : "Keine Tasks vorhanden."}
                </td>
              </tr>
            )}
            {items.map((task) => {
              const kind = (task.payload?.kind as string) ?? "–";
              const docId = task.payload?.document_id as number | undefined;
              return (
                <tr key={task.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-xs text-gray-500">#{task.id}</td>
                  <td className="px-4 py-3 text-gray-700">{kind}</td>
                  <td className="px-4 py-3 tabular-nums text-gray-700">
                    {docId != null ? (
                      <a
                        href={`/belege?id=${docId}`}
                        className="text-blue-600 hover:underline"
                      >
                        Beleg #{docId}
                      </a>
                    ) : "–"}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLORS[task.status]}`}>
                      {STATUS_LABELS[task.status]}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center tabular-nums text-gray-600">
                    {task.attempts}/{task.max_attempts}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-gray-500">
                    {task.worker_id ?? "–"}
                  </td>
                  <td className="px-4 py-3 max-w-xs">
                    {task.error ? (
                      <span
                        className="text-xs text-red-600 break-words"
                        title={task.error}
                      >
                        {task.error.length > 80 ? task.error.slice(0, 80) + "…" : task.error}
                      </span>
                    ) : "–"}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                    {fmtDate(task.created_at)}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                    {fmtDate(task.updated_at)}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => handleDeleteOne(task.id)}
                      disabled={deleting === task.id}
                      title="Task löschen"
                      className="rounded p-1 text-gray-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-40 transition-colors"
                    >
                      {deleting === task.id ? "..." : "✕"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm text-gray-600">
          <span>
            {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} von {total.toLocaleString("de-DE")}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => handlePageChange(offset - PAGE_SIZE)}
              disabled={!hasPrev}
              className="rounded border border-gray-300 px-3 py-1.5 hover:bg-gray-100 disabled:opacity-40 transition-colors"
            >
              ← Zurück
            </button>
            <button
              onClick={() => handlePageChange(offset + PAGE_SIZE)}
              disabled={!hasNext}
              className="rounded border border-gray-300 px-3 py-1.5 hover:bg-gray-100 disabled:opacity-40 transition-colors"
            >
              Weiter →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
