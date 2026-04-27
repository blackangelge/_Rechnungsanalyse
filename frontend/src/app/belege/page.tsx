"use client";

/**
 * Belege-Seite — Hauptansicht für alle importierten Dokumente.
 *
 * Funktionen:
 * - Tabellarische Übersicht aller Dokumente mit Paginierung (100 pro Seite)
 * - Mehrstufige Filter: Firma, Jahr, Status, Dokumententyp, Import-Batch,
 *   Betrag von/bis, Seiten von/bis, Lieferant, Beleg-ID
 * - Auswahl per Checkbox → Massenaktionen (KI-Analyse starten)
 * - Soft-Delete / Wiederherstellen pro Dokument
 * - PDF-Vorschau als Split-View (rechte Hälfte, fixiert)
 * - KI-Modal: zeigt alle Analyse-Durchläufe mit Token-Statistiken + Rohantwort
 * - Infos-Ansicht: 50/50-Split mit formatierter Rechnungsdetail-Ansicht + PDF
 * - Auto-Refresh alle 5 Sekunden wenn Dokumente im Status 'processing' vorhanden
 *
 * KI-Analyse-Ablauf:
 *   1. Dokumente auswählen (Checkboxen)
 *   2. „KI-Analyse starten" → POST /api/documents/enqueue
 *   3. Backend-Worker verarbeiten die Dokumente sequenziell
 *   4. Auto-Refresh zeigt den aktuellen Status
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  DocumentDetail,
  DocumentFilter,
  DocumentItem,
  DocumentType,
  DOCUMENT_TYPES,
  ImportBatch,
  documentsApi,
  extractApiError,
  getDocTypeName,
  importsApi,
} from "@/lib/api";

// ─── Konstante ───────────────────────────────────────────────────────────────

/** Maximale Anzahl Dokumente pro Seite in der Tabelle. */
const PAGE_SIZE = 100;

// ─── Hilfsfunktionen ────────────────────────────────────────────────────────

/** Formatiert einen Betrag als deutsches Währungsformat (1.234,56 €). */
function formatCurrency(amount: number | null | undefined): string {
  if (amount == null) return "–";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(amount);
}

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: "bg-gray-100 text-gray-600",
    processing: "bg-blue-100 text-blue-700",
    done: "bg-green-100 text-green-700",
    error: "bg-red-100 text-red-700",
  };
  const labels: Record<string, string> = {
    pending: "Ausstehend",
    processing: "Wird verarbeitet",
    done: "Fertig",
    error: "Fehler",
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${styles[status] ?? "bg-gray-100 text-gray-500"}`}>
      {labels[status] ?? status}
    </span>
  );
}

function KiBadge({ hasExtraction }: { hasExtraction: boolean }) {
  if (hasExtraction) return <span className="text-xs font-medium text-green-700">Ja</span>;
  return <span className="text-xs text-gray-400">Nein</span>;
}

// ─── Batch-Multiselect ───────────────────────────────────────────────────────
/**
 * Dropdown-Checkbox-Komponente zur Mehrfachauswahl von Import-Batches.
 * Schließt sich automatisch bei Klick außerhalb (mousedown-Listener).
 */

function BatchMultiSelect({
  batches,
  selectedIds,
  onChange,
}: {
  batches: ImportBatch[];
  selectedIds: Set<number>;
  onChange: (ids: Set<number>) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, []);

  function toggle(id: number) {
    const next = new Set(selectedIds);
    next.has(id) ? next.delete(id) : next.add(id);
    onChange(next);
  }

  const label =
    selectedIds.size === 0
      ? "Alle Imports"
      : selectedIds.size === 1
      ? (() => { const b = batches.find((b) => selectedIds.has(b.id)); return b ? `${b.company_name} ${b.year}` : "1 Import"; })()
      : `${selectedIds.size} Imports`;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex min-w-48 items-center justify-between gap-2 rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:outline-none"
      >
        <span className="truncate">{label}</span>
        <span className="text-gray-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="absolute z-20 mt-1 max-h-64 min-w-64 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
          <button type="button" onClick={() => onChange(new Set())}
            className="w-full px-3 py-2 text-left text-xs font-medium text-blue-600 hover:bg-blue-50 border-b">
            Alle Imports anzeigen
          </button>
          {batches.length === 0 && <p className="px-3 py-2 text-xs text-gray-400">Keine Imports vorhanden</p>}
          {batches.map((b) => (
            <label key={b.id} className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50">
              <input type="checkbox" checked={selectedIds.has(b.id)} onChange={() => toggle(b.id)}
                className="rounded border-gray-300 text-blue-600" />
              <span className="flex-1 truncate">{b.company_name} {b.year}</span>
              <span className="text-xs text-gray-400">#{b.id}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── DocType-Multiselect ─────────────────────────────────────────────────────
/**
 * Dropdown-Checkbox-Komponente zur Mehrfachauswahl von Dokumententypen.
 * Identisch in der Struktur zu BatchMultiSelect, aber für DOCUMENT_TYPES.
 * Filtert über document_type_ids-Query-Parameter (Integer-IDs, nicht Namen).
 */

function DocTypeMultiSelect({
  docTypes,
  selectedIds,
  onChange,
}: {
  docTypes: DocumentType[];
  selectedIds: Set<number>;
  onChange: (ids: Set<number>) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, []);

  function toggle(id: number) {
    const next = new Set(selectedIds);
    next.has(id) ? next.delete(id) : next.add(id);
    onChange(next);
  }

  const label =
    selectedIds.size === 0
      ? "Alle Typen"
      : selectedIds.size === 1
      ? (() => { const t = docTypes.find((t) => selectedIds.has(t.id)); return t ? t.name : "1 Typ"; })()
      : `${selectedIds.size} Typen`;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex min-w-48 items-center justify-between gap-2 rounded border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:outline-none"
      >
        <span className="truncate">{label}</span>
        <span className="text-gray-400">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div className="absolute z-20 mt-1 max-h-72 min-w-56 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
          <button type="button" onClick={() => onChange(new Set())}
            className="w-full px-3 py-2 text-left text-xs font-medium text-blue-600 hover:bg-blue-50 border-b">
            Alle Typen anzeigen
          </button>
          {docTypes.map((t) => (
            <label key={t.id} className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-gray-50">
              <input type="checkbox" checked={selectedIds.has(t.id)} onChange={() => toggle(t.id)}
                className="rounded border-gray-300 text-violet-600" />
              <span className="flex-1 truncate">{t.name}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Hauptkomponente ─────────────────────────────────────────────────────────

export default function BelegePage() {
  // Filter
  const [filterYear, setFilterYear] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState("");
  const [filterTotalMin, setFilterTotalMin] = useState("");
  const [filterTotalMax, setFilterTotalMax] = useState("");
  const [filterPageMin, setFilterPageMin] = useState("");
  const [filterPageMax, setFilterPageMax] = useState("");
  const [selectedBatchIds, setSelectedBatchIds] = useState<Set<number>>(new Set());
  const [selectedDocTypeIds, setSelectedDocTypeIds] = useState<Set<number>>(new Set());
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [filterKi, setFilterKi] = useState<"" | "ja" | "nein">("");
  const [filterSupplierName, setFilterSupplierName] = useState("");
  const [filterDocId, setFilterDocId] = useState("");
  const [activeFilters, setActiveFilters] = useState<DocumentFilter>({});

  // Daten
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Auswahl
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // Pagination
  const [currentPage, setCurrentPage] = useState(0);

  // PDF-Vorschau (Split-View)
  const [previewDocId, setPreviewDocId] = useState<number | null>(null);

  // Löschen-Bestätigung
  const [deleteConfirmId, setDeleteConfirmId] = useState<number | null>(null);

  // KI-Modal / Infos-Ansicht
  const [viewMode, setViewMode] = useState<"ki" | "infos" | null>(null);
  const [viewedDoc, setViewedDoc] = useState<DocumentDetail | null>(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [infosDocId, setInfosDocId] = useState<number | null>(null);
  const infosContainerRef = useRef<HTMLDivElement>(null);
  const tableContainerRef = useRef<HTMLDivElement>(null);
  const [tableTop, setTableTop] = useState(300);
  const NAV_HEIGHT = 60;

  // Analyse
  const [analyzing, setAnalyzing] = useState(false);

  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ─── Laden ──────────────────────────────────────────────────────────────

  const loadDocuments = useCallback(async (filters: DocumentFilter) => {
    setLoading(true);
    setError(null);
    try {
      const docs = await documentsApi.list(filters);
      setDocuments(docs);
      setCurrentPage(0);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Laden der Belege"));
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadOptions = useCallback(async () => {
    try {
      const allBatches = await importsApi.list();
      setBatches(allBatches);
    } catch (err) {
      console.error("Fehler beim Laden der Imports:", err);
    }
  }, []);

  useEffect(() => {
    loadDocuments({});
    loadOptions();
  }, [loadDocuments, loadOptions]);

  // Auto-Refresh bei processing-Dokumenten
  useEffect(() => {
    const hasProcessing = documents.some((d) => d.status === "processing");
    if (hasProcessing && !refreshTimerRef.current) {
      refreshTimerRef.current = setInterval(() => loadDocuments(activeFilters), 5000);
    } else if (!hasProcessing && refreshTimerRef.current) {
      clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    return () => { if (refreshTimerRef.current) { clearInterval(refreshTimerRef.current); refreshTimerRef.current = null; } };
  }, [documents, activeFilters, loadDocuments]);

  // Scroll-/Resize-Listener für PDF-Panel-Position
  useEffect(() => {
    if (previewDocId === null) return;
    function check() {
      if (!tableContainerRef.current) return;
      setTableTop(tableContainerRef.current.getBoundingClientRect().top);
    }
    check();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check, { passive: true });
    return () => { window.removeEventListener("scroll", check); window.removeEventListener("resize", check); };
  }, [previewDocId]);

  // ─── Filter-Logik ─────────────────────────────────────────────────────────

  function buildFilters(): DocumentFilter {
    const f: DocumentFilter = {};
    if (filterYear) f.year = parseInt(filterYear, 10);
    if (filterStatus) f.status = filterStatus;
    if (filterTotalMin) f.total_min = parseFloat(filterTotalMin);
    if (filterTotalMax) f.total_max = parseFloat(filterTotalMax);
    if (filterPageMin) f.page_min = parseInt(filterPageMin, 10);
    if (filterPageMax) f.page_max = parseInt(filterPageMax, 10);
    if (selectedBatchIds.size > 0) f.batch_ids = Array.from(selectedBatchIds);
    if (selectedDocTypeIds.size > 0) f.document_type_ids = Array.from(selectedDocTypeIds);
    if (includeDeleted) f.include_deleted = true;
    if (filterKi === "ja") f.has_extraction = true;
    if (filterKi === "nein") f.has_extraction = false;
    if (filterSupplierName.trim()) f.supplier_name = filterSupplierName.trim();
    if (filterDocId.trim()) f.doc_id = parseInt(filterDocId.trim(), 10);
    return f;
  }

  function applyFilters() {
    const f = buildFilters();
    setActiveFilters(f);
    setSelectedIds(new Set());
    setPreviewDocId(null);
    loadDocuments(f);
  }

  function resetFilters() {
    setFilterYear(""); setFilterStatus("");
    setFilterTotalMin(""); setFilterTotalMax(""); setFilterPageMin(""); setFilterPageMax("");
    setSelectedBatchIds(new Set()); setSelectedDocTypeIds(new Set()); setIncludeDeleted(false);
    setFilterKi(""); setFilterSupplierName(""); setFilterDocId("");
    const f: DocumentFilter = {};
    setActiveFilters(f);
    setSelectedIds(new Set());
    setPreviewDocId(null);
    loadDocuments(f);
  }

  // ─── Auswahl ──────────────────────────────────────────────────────────────

  function toggleSelectAll() {
    const activeDocs = documents.filter((d) => !d.soft_deleted);
    if (selectedIds.size === activeDocs.length && activeDocs.length > 0) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(activeDocs.map((d) => d.id)));
    }
  }

  function toggleSelect(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  // ─── KI-Analyse ───────────────────────────────────────────────────────────

  async function startAnalysis() {
    if (selectedIds.size === 0) return;
    setAnalyzing(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const result = await documentsApi.enqueue(Array.from(selectedIds));
      setSuccessMsg(result.message);
      setSelectedIds(new Set());
      await loadDocuments(activeFilters);
    } catch (err: unknown) {
      setError(extractApiError(err, "Fehler beim Einreihen der KI-Analyse"));
    } finally {
      setAnalyzing(false);
    }
  }

  // ─── Soft-Delete / Restore ────────────────────────────────────────────────

  async function handleDelete(docId: number) {
    try {
      await documentsApi.softDelete(docId);
      setDeleteConfirmId(null);
      setSelectedIds((prev) => { const next = new Set(prev); next.delete(docId); return next; });
      await loadDocuments(activeFilters);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Löschen des Belegs"));
    }
  }

  async function handleRestore(docId: number) {
    try {
      await documentsApi.restore(docId);
      await loadDocuments(activeFilters);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Wiederherstellen des Belegs"));
    }
  }

  // ─── KI-Modal / Infos-Inline öffnen ─────────────────────────────────────

  async function openView(docId: number, mode: "ki" | "infos") {
    setViewMode(mode);
    setViewedDoc(null);
    setViewLoading(true);
    if (mode === "infos") {
      setInfosDocId(docId);
      setPreviewDocId(null);
    }
    try {
      const detail = await documentsApi.get(docId);
      setViewedDoc(detail);
    } catch (err) {
      console.error("Fehler beim Laden des Dokuments:", err);
    } finally {
      setViewLoading(false);
    }
  }

  function closeView() {
    setViewMode(null);
    setViewedDoc(null);
    setInfosDocId(null);
  }

  async function navigateInfos(delta: number) {
    const idx = documents.findIndex((d) => d.id === infosDocId);
    const nextIdx = idx + delta;
    if (nextIdx < 0 || nextIdx >= documents.length) return;
    await openView(documents[nextIdx].id, "infos");
    infosContainerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ─── Ableitungen ──────────────────────────────────────────────────────────

  const activeDocs = documents.filter((d) => !d.soft_deleted);
  const allSelected = activeDocs.length > 0 && selectedIds.size === activeDocs.length;
  const someSelected = selectedIds.size > 0 && selectedIds.size < activeDocs.length;
  const availableYears = Array.from(
    new Set(documents.map((d) => d.year).filter((y): y is number => y !== null))
  ).sort((a, b) => b - a);
  const availableSuppliers = Array.from(
    new Set(documents.map((d) => d.supplier_name).filter((s): s is string => !!s))
  ).sort((a, b) => a.localeCompare(b, "de"));
  const infosIdx = infosDocId !== null ? documents.findIndex((d) => d.id === infosDocId) : -1;

  // Pagination
  const totalPages = Math.ceil(documents.length / PAGE_SIZE);
  const pagedDocs = documents.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  // ─── Render ───────────────────────────────────────────────────────────────

  const showPreview = previewDocId !== null;
  const pdfPanelTop = showPreview ? Math.max(NAV_HEIGHT, tableTop) + 25 : NAV_HEIGHT;
  const previewDoc = showPreview ? documents.find((d) => d.id === previewDocId) : null;

  return (
    <>
      {/* ── Kopfbereich ─────────────────────────────────────────────────── */}
      <div>
        <h1 className="mb-6 text-2xl font-bold text-gray-900">Belege</h1>

        {/* ── Meldungen ──────────────────────────────────────────────── */}
        {error && (
          <div className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700 border border-red-200">{error}</div>
        )}
        {successMsg && (
          <div className="mb-4 rounded-md bg-green-50 p-3 text-sm text-green-700 border border-green-200">{successMsg}</div>
        )}

        {/* ── Filter ────────────────────────────────────────────────── */}
        <div className="mb-4 rounded-lg border bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Import</label>
              <BatchMultiSelect batches={batches} selectedIds={selectedBatchIds} onChange={setSelectedBatchIds} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Dokumententyp</label>
              <DocTypeMultiSelect docTypes={DOCUMENT_TYPES} selectedIds={selectedDocTypeIds} onChange={setSelectedDocTypeIds} />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Jahr</label>
              <select value={filterYear} onChange={(e) => setFilterYear(e.target.value)}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-28">
                <option value="">Alle Jahre</option>
                {availableYears.map((y) => <option key={y} value={y}>{y}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Status</label>
              <select value={filterStatus} onChange={(e) => setFilterStatus(e.target.value)}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-40">
                <option value="">Alle Status</option>
                <option value="pending">Ausstehend</option>
                <option value="processing">Wird verarbeitet</option>
                <option value="done">Fertig</option>
                <option value="error">Fehler</option>
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">KI</label>
              <select value={filterKi} onChange={(e) => setFilterKi(e.target.value as "" | "ja" | "nein")}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-28">
                <option value="">Alle</option>
                <option value="ja">Ja</option>
                <option value="nein">Nein</option>
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Lieferant</label>
              <select value={filterSupplierName} onChange={(e) => setFilterSupplierName(e.target.value)}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-48">
                <option value="">Alle Lieferanten</option>
                {availableSuppliers.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Beleg-ID</label>
              <input type="number" value={filterDocId} onChange={(e) => setFilterDocId(e.target.value)}
                placeholder="z.B. 42" min="1"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-28" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Betrag von (€)</label>
              <input type="number" value={filterTotalMin} onChange={(e) => setFilterTotalMin(e.target.value)}
                placeholder="0" min="0" step="0.01"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-28" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Betrag bis (€)</label>
              <input type="number" value={filterTotalMax} onChange={(e) => setFilterTotalMax(e.target.value)}
                placeholder="∞" min="0" step="0.01"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-28" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Seiten von</label>
              <input type="number" value={filterPageMin} onChange={(e) => setFilterPageMin(e.target.value)}
                placeholder="1" min="1"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-24" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Seiten bis</label>
              <input type="number" value={filterPageMax} onChange={(e) => setFilterPageMax(e.target.value)}
                placeholder="∞" min="1"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-24" />
            </div>
            <label className="flex cursor-pointer items-center gap-2 mt-auto pb-1.5">
              <input type="checkbox" checked={includeDeleted} onChange={(e) => setIncludeDeleted(e.target.checked)}
                className="rounded border-gray-300 text-blue-600" />
              <span className="text-sm text-gray-600">Gelöschte anzeigen</span>
            </label>
            <div className="flex gap-2 mt-auto">
              <button onClick={applyFilters}
                className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 transition-colors">
                Filter anwenden
              </button>
              <button onClick={resetFilters}
                className="rounded border border-gray-300 px-4 py-1.5 text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors">
                Zurücksetzen
              </button>
            </div>
          </div>
        </div>

        {/* ── KI-Analyse ─────────────────────────────────────────────── */}
        <div className={`mb-4 rounded-lg border p-4 transition-colors ${selectedIds.size > 0 ? "border-blue-200 bg-blue-50" : "border-gray-200 bg-gray-50"}`}>
          <div className="flex flex-wrap items-center gap-4">
            <span className={`text-sm font-medium ${selectedIds.size > 0 ? "text-blue-800" : "text-gray-500"}`}>
              {selectedIds.size > 0
                ? `${selectedIds.size} Dokument${selectedIds.size !== 1 ? "e" : ""} ausgewählt`
                : "Dokumente auswählen für KI-Analyse"}
            </span>
            {selectedIds.size > 0 && (
              <p className="text-xs text-blue-600">
                Die Dokumente werden in die Worker-Warteschlange gestellt und automatisch verarbeitet.
              </p>
            )}
            <button onClick={startAnalysis} disabled={analyzing || selectedIds.size === 0}
              className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors ml-auto">
              {analyzing ? "Wird eingereiht…" : "KI-Analyse starten"}
            </button>
          </div>
        </div>

      </div>{/* Ende Kopfbereich */}

      {/* ── Tabelle / Infos-Ansicht: volle Bildschirmbreite ─────────── */}
      <div ref={tableContainerRef} className="relative left-1/2 w-screen -translate-x-1/2 px-6 mt-4">

        {viewMode === "infos" ? (
          /* ── Inline Infos-Ansicht ── */
          <div ref={infosContainerRef}>
            <div className="mb-4 flex items-center gap-3 rounded-lg border bg-white px-4 py-2.5 shadow-sm">
              <button onClick={closeView}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 transition-colors">
                ← Zur Liste
              </button>
              <div className="h-5 w-px bg-gray-200" />
              {infosIdx >= 0 && (
                <span className="text-sm text-gray-500">
                  Beleg <span className="font-semibold text-gray-800">{infosIdx + 1}</span>
                  <span className="text-gray-400"> / {documents.length}</span>
                </span>
              )}
              <div className="ml-auto flex items-center gap-2">
                <button onClick={() => navigateInfos(-1)} disabled={infosIdx <= 0 || viewLoading}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40 transition-colors">
                  ← Vorherige
                </button>
                <button onClick={() => navigateInfos(1)} disabled={infosIdx >= documents.length - 1 || viewLoading}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40 transition-colors">
                  Nächste →
                </button>
              </div>
            </div>

            {viewLoading ? (
              <div className="flex items-center justify-center py-16 text-gray-400 text-sm rounded-xl border bg-white shadow-sm">
                <span className="animate-spin mr-2 inline-block">⟳</span> Lade…
              </div>
            ) : viewedDoc ? (
              <div className="flex gap-4">
                <div className="w-1/2 shrink-0 overflow-y-auto rounded-xl border bg-white shadow-sm" style={{ maxHeight: "calc(100vh - 14rem)" }}>
                  <div className="sticky top-0 z-10 border-b bg-white px-5 py-3">
                    <h2 className="text-sm font-semibold text-gray-900 truncate">{viewedDoc.original_filename}</h2>
                    <p className="text-xs text-gray-500">{viewedDoc.company} {viewedDoc.year} · #{viewedDoc.id}</p>
                  </div>
                  <div className="p-5">
                    <InfosView doc={viewedDoc} />
                  </div>
                </div>
                <div className="w-1/2 shrink-0 rounded-xl border bg-white shadow-sm overflow-hidden">
                  <iframe
                    src={documentsApi.previewUrl(viewedDoc.id)}
                    className="w-full rounded-xl"
                    style={{ height: "calc(100vh - 14rem)" }}
                    title={`PDF ${viewedDoc.original_filename}`}
                  />
                </div>
              </div>
            ) : (
              <div className="rounded-xl border bg-white shadow-sm p-6 text-sm text-red-500">
                Dokument konnte nicht geladen werden.
              </div>
            )}
          </div>
        ) : (
          <>
            {/* Anzahl + Pagination-Info */}
            <div className="mb-2 flex items-center justify-between text-sm text-gray-500">
              <span>{loading ? "Lade..." : `${documents.length} Beleg${documents.length !== 1 ? "e" : ""} gefunden`}</span>
              {totalPages > 1 && (
                <span>Seite {currentPage + 1} von {totalPages} ({PAGE_SIZE} pro Seite)</span>
              )}
            </div>

            {/* Tabelle */}
            <div className={`overflow-x-auto rounded-lg border bg-white shadow-sm ${showPreview ? "w-1/2" : "w-full"}`}>
              <table className="min-w-full divide-y divide-gray-200 text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="w-10 px-3 py-3">
                      <input type="checkbox" checked={allSelected}
                        ref={(el) => { if (el) el.indeterminate = someSelected; }}
                        onChange={toggleSelectAll}
                        className="rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                    </th>
                    <th className="px-3 py-3 text-left font-medium text-gray-600">#</th>
                    <th className="px-3 py-3 text-left font-medium text-gray-600">Firma</th>
                    <th className="px-3 py-3 text-left font-medium text-gray-600">Jahr</th>
                    <th className="px-3 py-3 text-left font-medium text-gray-600">Dateiname</th>
                    <th className="px-3 py-3 text-right font-medium text-gray-600">Seiten</th>
                    <th className="px-3 py-3 text-right font-medium text-gray-600">Betrag</th>
                    <th className="px-3 py-3 text-left font-medium text-gray-600">Status</th>
                    <th className="px-3 py-3 text-left font-medium text-gray-600">KI</th>
                    {!showPreview && (
                      <>
                        <th className="px-3 py-3 text-left font-medium text-gray-600">Dokumententyp</th>
                        <th className="px-3 py-3 text-left font-medium text-gray-600">Rechnungsnr.</th>
                        <th className="px-3 py-3 text-left font-medium text-gray-600">Lieferant</th>
                      </>
                    )}
                    <th className="px-3 py-3 text-left font-medium text-gray-600">Aktionen</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {loading && documents.length === 0 && (
                    <tr><td colSpan={12} className="px-4 py-8 text-center text-gray-400">Wird geladen...</td></tr>
                  )}
                  {!loading && documents.length === 0 && (
                    <tr><td colSpan={12} className="px-4 py-8 text-center text-gray-400">Keine Belege gefunden</td></tr>
                  )}
                  {pagedDocs.map((doc) => {
                    const isDeleted = doc.soft_deleted;
                    const isConfirmingDelete = deleteConfirmId === doc.id;
                    const isActivePreview = previewDocId === doc.id;
                    const docTypeName = getDocTypeName(doc.document_type);

                    return (
                      <tr key={doc.id}
                        className={[
                          isDeleted ? "bg-red-50 opacity-60"
                            : isActivePreview ? "bg-blue-50"
                            : selectedIds.has(doc.id) ? "bg-blue-50"
                            : "hover:bg-gray-50",
                        ].join(" ")}>

                        <td className="w-10 px-3 py-2.5">
                          {!isDeleted && (
                            <input type="checkbox" checked={selectedIds.has(doc.id)}
                              onChange={() => toggleSelect(doc.id)}
                              className="rounded border-gray-300 text-blue-600 focus:ring-blue-500" />
                          )}
                        </td>

                        <td className="px-3 py-2.5 text-gray-500 tabular-nums">{doc.id}</td>
                        <td className="px-3 py-2.5 font-medium text-gray-900">{doc.company ?? "–"}</td>
                        <td className="px-3 py-2.5 text-gray-700">{doc.year ?? "–"}</td>

                        <td className="px-3 py-2.5 text-gray-700 max-w-xs">
                          <div className="flex items-center gap-2">
                            <span className="truncate" title={doc.original_filename}>{doc.original_filename}</span>
                            {isDeleted && (
                              <span className="shrink-0 inline-flex items-center rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                                Gelöscht
                              </span>
                            )}
                          </div>
                        </td>

                        <td className="px-3 py-2.5 text-right text-gray-700 tabular-nums">
                          {doc.page_count > 0 ? doc.page_count : "–"}
                        </td>
                        <td className="px-3 py-2.5 text-right text-gray-700 tabular-nums">
                          {formatCurrency(doc.total_amount)}
                        </td>
                        <td className="px-3 py-2.5"><StatusBadge status={doc.status} /></td>
                        <td className="px-3 py-2.5"><KiBadge hasExtraction={doc.has_extraction ?? false} /></td>

                        {!showPreview && (
                          <>
                            <td className="px-3 py-2.5 text-gray-700 max-w-[140px]">
                              {doc.document_type > 0 ? (
                                <span className="inline-flex items-center rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-700 whitespace-nowrap">
                                  {docTypeName}
                                </span>
                              ) : (
                                <span className="text-gray-400">–</span>
                              )}
                            </td>
                            <td className="px-3 py-2.5 text-gray-700">{doc.invoice_number ?? "–"}</td>
                            <td className="px-3 py-2.5 text-gray-700 max-w-[140px] truncate" title={doc.supplier_name ?? ""}>
                              {doc.supplier_name ?? "–"}
                            </td>
                          </>
                        )}

                        <td className="px-3 py-2.5">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {!isDeleted && (
                              <button
                                onClick={() => setPreviewDocId(isActivePreview ? null : doc.id)}
                                disabled={!doc.stored_filename}
                                className={[
                                  "rounded px-2 py-1 text-xs font-medium transition-colors",
                                  isActivePreview
                                    ? "bg-blue-600 text-white hover:bg-blue-700"
                                    : "border border-gray-300 text-gray-600 hover:bg-gray-100",
                                  !doc.stored_filename ? "opacity-30 cursor-not-allowed" : "",
                                ].join(" ")}
                              >
                                {isActivePreview ? "Vorschau aus" : "PDF"}
                              </button>
                            )}

                            {(doc.status === "done" || doc.status === "error") && (
                              <button
                                onClick={() => openView(doc.id, "ki")}
                                className="rounded border border-violet-300 px-2 py-1 text-xs font-medium text-violet-700 hover:bg-violet-50 transition-colors"
                              >
                                KI
                              </button>
                            )}

                            {doc.status === "done" && (
                              <button
                                onClick={() => openView(doc.id, "infos")}
                                className="rounded border border-emerald-300 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-50 transition-colors"
                              >
                                Infos
                              </button>
                            )}

                            {isDeleted ? (
                              <button onClick={() => handleRestore(doc.id)}
                                className="rounded border border-green-300 px-2 py-1 text-xs font-medium text-green-700 hover:bg-green-50 transition-colors">
                                Wiederherstellen
                              </button>
                            ) : isConfirmingDelete ? (
                              <>
                                <button onClick={() => handleDelete(doc.id)}
                                  className="rounded bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700">
                                  Ja
                                </button>
                                <button onClick={() => setDeleteConfirmId(null)}
                                  className="rounded border border-gray-300 px-2 py-1 text-xs text-gray-500 hover:bg-gray-100">
                                  Nein
                                </button>
                              </>
                            ) : (
                              <button onClick={() => setDeleteConfirmId(doc.id)}
                                className="rounded border border-red-200 px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-50 transition-colors">
                                Löschen
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="mt-4 flex items-center justify-center gap-2">
                <button
                  onClick={() => setCurrentPage(0)}
                  disabled={currentPage === 0}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                >
                  «
                </button>
                <button
                  onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
                  disabled={currentPage === 0}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                >
                  ‹
                </button>
                {Array.from({ length: totalPages }, (_, i) => i)
                  .filter((i) => Math.abs(i - currentPage) <= 2)
                  .map((i) => (
                    <button
                      key={i}
                      onClick={() => setCurrentPage(i)}
                      className={`rounded border px-3 py-1.5 text-sm transition-colors ${
                        i === currentPage
                          ? "border-blue-600 bg-blue-600 text-white"
                          : "border-gray-300 text-gray-600 hover:bg-gray-50"
                      }`}
                    >
                      {i + 1}
                    </button>
                  ))}
                <button
                  onClick={() => setCurrentPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={currentPage >= totalPages - 1}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                >
                  ›
                </button>
                <button
                  onClick={() => setCurrentPage(totalPages - 1)}
                  disabled={currentPage >= totalPages - 1}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40"
                >
                  »
                </button>
              </div>
            )}

            {/* PDF-Vorschau */}
            {showPreview && createPortal(
              <div className="fixed right-0 bottom-0 z-40 flex w-1/2 flex-col border-l border-gray-200 bg-white shadow-2xl"
                   style={{ top: `${pdfPanelTop}px` }}>
                <div className="flex shrink-0 items-center justify-between border-b bg-white px-4 py-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-medium text-gray-800">
                      {previewDoc?.original_filename ?? `Dokument #${previewDocId}`}
                    </span>
                    <span className="shrink-0 text-xs text-gray-400">#{previewDocId}</span>
                  </div>
                  <button onClick={() => setPreviewDocId(null)} className="ml-3 shrink-0 text-sm text-gray-400 hover:text-gray-700">
                    ✕
                  </button>
                </div>
                <iframe
                  src={documentsApi.previewUrl(previewDocId!)}
                  className="w-full flex-1"
                  title={`PDF-Vorschau #${previewDocId}`}
                />
              </div>,
              document.body
            )}
          </>
        )}
      </div>

      {/* ── KI-Rohantwort Modal ──────────────────────────────────────── */}
      {viewMode === "ki" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={closeView}>
          <div className="relative flex max-h-[90vh] w-full max-w-4xl flex-col rounded-xl bg-white shadow-2xl"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b px-6 py-4">
              <div>
                <h2 className="text-lg font-semibold text-gray-900">KI-Rohantwort</h2>
                {viewedDoc && (
                  <p className="text-sm text-gray-500">{viewedDoc.original_filename} · #{viewedDoc.id}</p>
                )}
              </div>
              <button onClick={closeView} className="text-gray-400 hover:text-gray-700 text-xl leading-none">✕</button>
            </div>
            <div className="overflow-y-auto p-6 space-y-4">
              {viewLoading && (
                <div className="flex items-center justify-center py-12 text-gray-400 text-sm">
                  <span className="animate-spin mr-2">⟳</span> Lade…
                </div>
              )}
              {!viewLoading && viewedDoc && (
                <>
                  {/* ── KI-Durchläufe ──────────────────────────────────── */}
                  {viewedDoc.token_counts && viewedDoc.token_counts.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                        KI-Durchläufe ({viewedDoc.token_counts.length})
                      </p>

                      {/* Ein Block pro Durchlauf */}
                      {viewedDoc.token_counts.map((run, idx) => (
                        <div key={run.id}
                             className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-xs text-gray-600">
                          <p className="mb-1.5 font-semibold text-gray-700">
                            Durchlauf {idx + 1}
                            <span className="ml-2 font-normal text-gray-400">
                              {new Date(run.created_at).toLocaleString("de-DE", {
                                day: "2-digit", month: "2-digit", year: "numeric",
                                hour: "2-digit", minute: "2-digit", second: "2-digit",
                              })}
                            </span>
                          </p>
                          <div className="flex flex-wrap gap-3">
                            {run.input_token_count > 0 && (
                              <span>
                                <span className="font-medium text-gray-500">Input:</span>{" "}
                                <span className="font-semibold text-gray-800 tabular-nums">
                                  {run.input_token_count.toLocaleString("de-DE")}
                                </span>
                              </span>
                            )}
                            {run.output_token_count > 0 && (
                              <span>
                                <span className="font-medium text-gray-500">Output:</span>{" "}
                                <span className="font-semibold text-gray-800 tabular-nums">
                                  {run.output_token_count.toLocaleString("de-DE")}
                                </span>
                              </span>
                            )}
                            {run.reasoning_count > 0 && (
                              <span>
                                <span className="font-medium text-gray-500">Reasoning:</span>{" "}
                                <span className="font-semibold text-gray-800 tabular-nums">
                                  {run.reasoning_count.toLocaleString("de-DE")}
                                </span>
                              </span>
                            )}
                            {run.time_spent_seconds > 0 && (
                              <span>
                                <span className="font-medium text-gray-500">Dauer:</span>{" "}
                                <span className="font-semibold text-gray-800 tabular-nums">
                                  {run.time_spent_seconds < 60
                                    ? `${run.time_spent_seconds.toFixed(1).replace(".", ",")} s`
                                    : `${Math.floor(run.time_spent_seconds / 60)}:${String(Math.round(run.time_spent_seconds % 60)).padStart(2, "0")} min`}
                                </span>
                              </span>
                            )}
                          </div>
                        </div>
                      ))}

                      {/* Gesamt-Zeile — nur bei mehr als einem Durchlauf */}
                      {viewedDoc.token_counts.length > 1 && (
                        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-xs text-blue-700">
                          <p className="mb-1.5 font-semibold text-blue-800">
                            Gesamt ({viewedDoc.token_counts.length} Durchläufe)
                          </p>
                          <div className="flex flex-wrap gap-3">
                            {viewedDoc.ki_input_tokens != null && (
                              <span>
                                <span className="font-medium">Input:</span>{" "}
                                <span className="font-bold tabular-nums">
                                  {viewedDoc.ki_input_tokens.toLocaleString("de-DE")}
                                </span>
                              </span>
                            )}
                            {viewedDoc.ki_output_tokens != null && (
                              <span>
                                <span className="font-medium">Output:</span>{" "}
                                <span className="font-bold tabular-nums">
                                  {viewedDoc.ki_output_tokens.toLocaleString("de-DE")}
                                </span>
                              </span>
                            )}
                            {viewedDoc.ki_reasoning_tokens != null && viewedDoc.ki_reasoning_tokens > 0 && (
                              <span>
                                <span className="font-medium">Reasoning:</span>{" "}
                                <span className="font-bold tabular-nums">
                                  {viewedDoc.ki_reasoning_tokens.toLocaleString("de-DE")}
                                </span>
                              </span>
                            )}
                            {viewedDoc.ki_total_duration != null && (
                              <span>
                                <span className="font-medium">Dauer gesamt:</span>{" "}
                                <span className="font-bold tabular-nums">
                                  {viewedDoc.ki_total_duration < 60
                                    ? `${viewedDoc.ki_total_duration.toFixed(1).replace(".", ",")} s`
                                    : `${Math.floor(viewedDoc.ki_total_duration / 60)}:${String(Math.round(viewedDoc.ki_total_duration % 60)).padStart(2, "0")} min`}
                                </span>
                              </span>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  <KiRawView rawResponse={viewedDoc.extraction?.raw_response ?? null} />
                </>
              )}
              {!viewLoading && !viewedDoc && (
                <p className="text-sm text-red-500">Dokument konnte nicht geladen werden.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ─── KI-Rohantwort-Ansicht ────────────────────────────────────────────────────
/**
 * Zeigt die JSON-Rohantwort der KI formatiert an.
 * Versucht das JSON zu parsen und mit 2-Spaces-Einrückung darzustellen.
 * Fällt bei Parse-Fehlern auf den unformatierten Original-String zurück.
 */

function KiRawView({ rawResponse }: { rawResponse: string | null }) {
  let formatted = rawResponse ?? "";
  if (rawResponse) {
    try {
      formatted = JSON.stringify(JSON.parse(rawResponse), null, 2);
    } catch {
      // Kein gültiges JSON → Rohantwort anzeigen
    }
  }

  return (
    <div>
      {rawResponse ? (
        <pre className="overflow-x-auto rounded-lg bg-gray-950 p-4 text-xs leading-relaxed text-green-300 whitespace-pre-wrap break-words">
          {formatted}
        </pre>
      ) : (
        <p className="text-sm text-gray-400">Keine KI-Antwort gespeichert.</p>
      )}
    </div>
  );
}

// ─── Formatierte Infos-Ansicht ────────────────────────────────────────────────
/**
 * Zeigt die extrahierten Rechnungsdaten strukturiert an.
 *
 * Liest bevorzugt aus dem raw_response (neuem verschachtelten JSON-Format):
 *   lieferant, rechnungsdaten, zahlungsinformationen, positionen
 *
 * Fällt auf die flachen extraction-Felder zurück falls kein raw_response vorhanden
 * oder das alte Format verwendet wird (Rückwärtskompatibilität).
 *
 * fmt(): Wandelt Zahlen und deutsche Dezimalstring-Formate in €-Beträge um.
 */

function InfosView({ doc }: { doc: DocumentDetail }) {
  const ext = doc.extraction;

  let raw: Record<string, unknown> | null = null;
  if (ext?.raw_response) {
    try { raw = JSON.parse(ext.raw_response); } catch { /* ignore */ }
  }

  const lieferant = (raw?.lieferant as Record<string, unknown>) ?? null;
  const anschrift = (lieferant?.anschrift as Record<string, unknown>) ?? null;
  const bank = (lieferant?.bankverbindung as Record<string, unknown>) ?? null;
  const rechnung = (raw?.rechnungsdaten as Record<string, unknown>) ?? null;
  const zahlung = (raw?.zahlungsinformationen as Record<string, unknown>) ?? null;
  const skonto = (zahlung?.skonto as Record<string, unknown>) ?? null;
  const positionen = (raw?.positionen as unknown[]) ?? null;
  const ustZusammenfassung = (zahlung?.umsatzsteuer_zusammenfassung as unknown[]) ?? null;

  function Row({ label, value }: { label: string; value: unknown }) {
    if (value == null || value === "") return null;
    return (
      <div className="flex gap-3 py-1.5 border-b border-gray-100 last:border-0">
        <span className="w-44 shrink-0 text-xs font-medium text-gray-500">{label}</span>
        <span className="text-sm text-gray-800 break-words">{String(value)}</span>
      </div>
    );
  }

  function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
      <div className="mb-6">
        <h3 className="mb-2 text-sm font-semibold text-gray-700 uppercase tracking-wide">{title}</h3>
        <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-1">
          {children}
        </div>
      </div>
    );
  }

  const fmt = (n: unknown): string | null => {
    if (n == null) return null;
    let num: number;
    if (typeof n === "number") {
      num = n;
    } else {
      const s = String(n).replace(/[€$£¥\s]/g, "");
      const normalized = s.includes(",") ? s.replace(/\./g, "").replace(",", ".") : s;
      num = parseFloat(normalized);
    }
    if (isNaN(num)) return String(n);
    return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" }).format(num);
  };

  return (
    <div>
      {/* Lieferant */}
      <Section title="Lieferant">
        <Row label="Name" value={lieferant?.name} />
        {anschrift ? (
          <>
            <Row label="Straße" value={anschrift.strasse} />
            <Row label="PLZ / Ort" value={[anschrift.plz, anschrift.ort].filter(Boolean).join(" ")} />
            <Row label="Land" value={anschrift.land} />
          </>
        ) : null}
        <Row label="HRB-Nummer" value={lieferant?.hrb_nummer} />
        <Row label="Steuernummer" value={lieferant?.steuernummer} />
        <Row label="USt-IdNr." value={lieferant?.ust_id_nr} />
      </Section>

      {/* Bankverbindung */}
      {bank && (
        <Section title="Bankverbindung">
          <Row label="Bank" value={bank.bank_name} />
          <Row label="IBAN" value={bank.iban} />
          <Row label="BIC" value={bank.bic} />
        </Section>
      )}

      {/* Rechnungsdaten */}
      <Section title="Rechnungsdaten">
        <Row label="Rechnungsnummer" value={rechnung?.rechnungsnummer ?? ext?.invoice_number} />
        <Row label="Rechnungsdatum" value={rechnung?.rechnungsdatum ?? ext?.invoice_date} />
        <Row label="Fälligkeit" value={rechnung?.faelligkeit ?? ext?.due_date} />
        <Row label="Kundennummer" value={rechnung?.kundennummer} />
      </Section>

      {/* Zahlungsinformationen */}
      <Section title="Zahlungsinformationen">
        <Row label="Gesamtbetrag Netto" value={fmt(zahlung?.gesamtbetrag_netto ?? ext?.total_amount_netto)} />
        <Row label="Gesamtbetrag Brutto" value={fmt(zahlung?.gesamtbetrag_brutto ?? ext?.total_amount_brutto)} />
        <Row label="Währung" value={zahlung?.waehrung} />
        {skonto && (
          <>
            <Row label="Skonto %" value={skonto.prozent != null ? `${skonto.prozent} %` : null} />
            <Row label="Skonto Betrag" value={fmt(skonto.betrag ?? ext?.cash_discount_amount)} />
            <Row label="Skonto Frist" value={skonto.frist_tage != null ? `${skonto.frist_tage} Tage` : null} />
          </>
        )}
        <Row label="Zahlungsbedingungen" value={zahlung?.zahlungsbedingungen ?? ext?.payment_terms} />
      </Section>

      {/* USt-Zusammenfassung */}
      {ustZusammenfassung && ustZusammenfassung.length > 0 && (
        <div className="mb-6">
          <h3 className="mb-2 text-sm font-semibold text-gray-700 uppercase tracking-wide">Umsatzsteuer</h3>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-xs font-medium text-gray-500">
                <tr>
                  <th className="px-4 py-2 text-right">Steuersatz</th>
                  <th className="px-4 py-2 text-right">Nettobetrag</th>
                  <th className="px-4 py-2 text-right">Steuerbetrag</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {(ustZusammenfassung as Record<string, unknown>[]).map((row, i) => (
                  <tr key={i}>
                    <td className="px-4 py-2 text-right">{row.steuersatz != null ? `${row.steuersatz} %` : "–"}</td>
                    <td className="px-4 py-2 text-right">{fmt(row.nettobetrag) ?? "–"}</td>
                    <td className="px-4 py-2 text-right">{fmt(row.steuerbetrag) ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Positionen */}
      {(positionen ?? doc.order_positions).length > 0 && (
        <div className="mb-2">
          <h3 className="mb-2 text-sm font-semibold text-gray-700 uppercase tracking-wide">
            Positionen ({(positionen ?? doc.order_positions).length})
          </h3>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-xs font-medium text-gray-500">
                <tr>
                  <th className="px-3 py-2 text-left">Nr.</th>
                  <th className="px-3 py-2 text-left">Bezeichnung</th>
                  <th className="px-3 py-2 text-left">Art.-Nr.</th>
                  <th className="px-3 py-2 text-right">Menge</th>
                  <th className="px-3 py-2 text-left">Einheit</th>
                  <th className="px-3 py-2 text-right">Einzelpreis</th>
                  <th className="px-3 py-2 text-right">Steuersatz</th>
                  <th className="px-3 py-2 text-left">Nachlass</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {positionen
                  ? (positionen as Record<string, unknown>[]).map((pos, i) => {
                      const nachlass = (pos.preisnachlass as Record<string, unknown>) ?? {};
                      const nachlassStr = [
                        nachlass.betrag != null ? fmt(nachlass.betrag) : null,
                        nachlass.prozent != null ? `${nachlass.prozent}%` : null,
                        nachlass.bezeichnung ? String(nachlass.bezeichnung) : null,
                      ].filter(Boolean).join(" / ");
                      return (
                        <tr key={i} className="hover:bg-gray-50">
                          <td className="px-3 py-2 text-gray-500">{String(pos.position_nr ?? i + 1)}</td>
                          <td className="px-3 py-2">{String(pos.artikelbezeichnung ?? "–")}</td>
                          <td className="px-3 py-2 text-gray-500 text-xs">{String(pos.artikelnummer_lieferant ?? "–")}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{pos.menge != null ? String(pos.menge) : "–"}</td>
                          <td className="px-3 py-2 text-gray-500">{String(pos.mengeneinheit ?? "–")}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{pos.einzelpreis != null ? fmt(pos.einzelpreis) : "–"}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{pos.steuersatz != null ? `${pos.steuersatz} %` : "–"}</td>
                          <td className="px-3 py-2 text-xs text-gray-500">{nachlassStr || "–"}</td>
                        </tr>
                      );
                    })
                  : doc.order_positions.map((pos, i) => (
                      <tr key={pos.id} className="hover:bg-gray-50">
                        <td className="px-3 py-2 text-gray-500">{i + 1}</td>
                        <td className="px-3 py-2">{pos.product_name ?? pos.product_description ?? "–"}</td>
                        <td className="px-3 py-2 text-gray-500 text-xs">{pos.article_number ?? "–"}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{pos.quantity != null ? String(pos.quantity) : "–"}</td>
                        <td className="px-3 py-2 text-gray-500">{pos.unit ?? "–"}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{pos.unit_price_netto != null ? fmt(pos.unit_price_netto) : "–"}</td>
                        <td className="px-3 py-2 text-right">{pos.tax != null ? `${pos.tax} %` : "–"}</td>
                        <td className="px-3 py-2 text-xs text-gray-500">{pos.discount ?? "–"}</td>
                      </tr>
                    ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {!ext && (
        <p className="text-sm text-gray-400">Keine Extraktionsdaten vorhanden.</p>
      )}
    </div>
  );
}
