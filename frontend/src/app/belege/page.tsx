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
import Pager from "@/components/common/Pager";
import AnalysisHistoryPanel from "@/components/belege/AnalysisHistoryPanel";
import PdfEditModal from "@/components/belege/PdfEditModal";
import {
  DocumentDetail,
  DocumentFilter,
  DocumentItem,
  DocumentType,
  DOCUMENT_TYPES,
  ImportBatch,
  ValidationStatus,
  WorkflowListItem,
  WorkflowTraceEntry,
  workflowsApi,
  documentsApi,
  extractApiError,
  getDocTypeName,
  importsApi,
} from "@/lib/api";

// ─── Konstante ───────────────────────────────────────────────────────────────

/** Standard-Seitengröße der serverseitig paginierten Tabelle (wählbar 25–200). */
const DEFAULT_PAGE_SIZE = 50;

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
    waiting_capacity: "bg-amber-100 text-amber-700",
  };
  const labels: Record<string, string> = {
    pending: "Ausstehend",
    processing: "Wird verarbeitet",
    done: "Fertig",
    error: "Fehler",
    // Kein Endzustand: wird automatisch neu versucht, sobald eine ausreichend
    // große KI-Konfiguration verfügbar ist (siehe worker.py, Fallstricke in CLAUDE.md)
    waiting_capacity: "Wartet auf KI-Kapazität",
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

const VALIDATION_STYLES: Record<ValidationStatus, { label: string; cls: string; hint: string }> = {
  ok: { label: "OK", cls: "bg-green-100 text-green-700", hint: "Alle Prüfungen bestanden" },
  // Automatisch korrigierte Belege sind ERLEDIGT (grün) — sie verlangen keine Bestätigung; die Änderungen
  // bleiben im Prüfpanel nachlesbar. Nur "Prüfen!" braucht eine Entscheidung des Nutzers.
  corrected: { label: "Korrigiert", cls: "bg-emerald-100 text-emerald-800", hint: "Automatisch (rechnerisch) korrigiert — keine Aktion nötig" },
  ai_corrected: { label: "KI-korrigiert", cls: "bg-emerald-100 text-emerald-800", hint: "Durch zweite KI-Abfrage korrigiert — keine Aktion nötig" },
  needs_review: { label: "Prüfen!", cls: "bg-red-100 text-red-700", hint: "Nicht plausibel — bitte manuell prüfen" },
  manual_ok: { label: "Geprüft", cls: "bg-blue-100 text-blue-700", hint: "Manuell als geprüft bestätigt" },
  unchecked: { label: "–", cls: "bg-gray-100 text-gray-500", hint: "Nicht prüfbar (keine Beträge/Positionen)" },
};

function ValidationBadge({ status }: { status: ValidationStatus | null | undefined }) {
  if (!status) return <span className="text-gray-400">–</span>;
  const v = VALIDATION_STYLES[status];
  if (!v) return <span className="text-gray-400">{status}</span>;
  return (
    <span title={v.hint} className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${v.cls}`}>
      {v.label}
    </span>
  );
}

/** Filter-Presets für die Plausibilitätsprüfung → Backend-Statuswerte */
const VALIDATION_FILTERS: Record<string, { label: string; statuses: ValidationStatus[] }> = {
  needs_review: { label: "Manuell prüfen", statuses: ["needs_review"] },
  corrected: { label: "Automatisch korrigiert", statuses: ["corrected", "ai_corrected"] },
  ok: { label: "In Ordnung", statuses: ["ok", "manual_ok"] },
};

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
              <span className="flex-1 truncate">{b.company_name} {b.year}{b.hidden_at ? " (ausgeblendet)" : ""}</span>
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
  const [includeHidden, setIncludeHidden] = useState(false);
  const [filterKi, setFilterKi] = useState<"" | "ja" | "nein">("");
  const [filterSupplierName, setFilterSupplierName] = useState("");
  const [filterDocId, setFilterDocId] = useState("");
  const [filterValidation, setFilterValidation] = useState("");
  const [activeFilters, setActiveFilters] = useState<DocumentFilter>({});

  // Daten
  // Serverseitig paginiert: nur die aktuelle Seite liegt im Browser
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [yearOptions, setYearOptions] = useState<number[]>([]);
  const [supplierOptions, setSupplierOptions] = useState<string[]>([]);
  // Aktuelle Seite/Größe/Filter für Timer und Callbacks (Refs, damit der Auto-Refresh die Seite NICHT zurücksetzt)
  const pageRef = useRef(0);
  const pageSizeRef = useRef(DEFAULT_PAGE_SIZE);
  const activeFiltersRef = useRef<DocumentFilter>({});
  const [batches, setBatches] = useState<ImportBatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Auswahl
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());

  // PDF-Vorschau (Split-View)
  const [previewDocId, setPreviewDocId] = useState<number | null>(null);


  // KI-Modal / Infos-Ansicht
  const [viewMode, setViewMode] = useState<"ki" | "infos" | null>(null);
  const [viewedDoc, setViewedDoc] = useState<DocumentDetail | null>(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [infosDocId, setInfosDocId] = useState<number | null>(null);
  const infosContainerRef = useRef<HTMLDivElement>(null);
  const tableContainerRef = useRef<HTMLDivElement>(null);

  // Analyse-Workflow (leer = automatisch: Lieferant → Standard → klassisch)
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string>("");

  // PDF-Bearbeitung (Modal „Seiten") + Sammel-Kürzen
  const [editDoc, setEditDoc] = useState<{ id: number; filename: string } | null>(null);
  const [trimMaxPages, setTrimMaxPages] = useState("1");
  const [trimming, setTrimming] = useState(false);

  // Analyse
  const [analyzing, setAnalyzing] = useState(false);

  // ─── Laden ──────────────────────────────────────────────────────────────

  /**
   * Lädt eine Seite der Belegliste. Ohne `pageIdx` bleibt die AKTUELLE Seite (pageRef) erhalten —
   * so setzt der Auto-Refresh eine geblätterte Liste nicht auf Seite 1 zurück.
   */
  const loadDocuments = useCallback(async (filters: DocumentFilter, pageIdx?: number): Promise<DocumentItem[]> => {
    setLoading(true);
    setError(null);
    try {
      let target = pageIdx ?? pageRef.current;
      let res = await documentsApi.listPage(filters, target, pageSizeRef.current);
      const lastPage = Math.max(0, Math.ceil(res.total / pageSizeRef.current) - 1);
      if (target > lastPage) {          // z.B. nach dem Löschen des letzten Belegs der letzten Seite
        target = lastPage;
        res = await documentsApi.listPage(filters, target, pageSizeRef.current);
      }
      pageRef.current = target;
      setPage(target);
      setDocuments(res.items);
      setTotal(res.total);
      return res.items;
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Laden der Belege"));
      console.error(err);
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  /** Import-/Jahr-/Lieferanten-Auswahl: ausgeblendete Imports nur, wenn „Ausgeblendete Imports einbeziehen" aktiv ist. */
  const loadImportOptions = useCallback(async (withHidden: boolean) => {
    try {
      setBatches(await importsApi.list({ include_hidden: withHidden }));
    } catch (err) {
      console.error("Fehler beim Laden der Imports:", err);
    }
    try {
      const options = await documentsApi.filterOptions(withHidden);
      setYearOptions(options.years);
      setSupplierOptions(options.suppliers);
    } catch (err) {
      console.error("Fehler beim Laden der Filter-Optionen:", err);
    }
  }, []);

  const loadOptions = useCallback(async () => {
    // Workflows sind optional — ohne Workflows bleibt die Auswahl leer ("automatisch")
    try {
      setWorkflows(await workflowsApi.list());
    } catch {
      setWorkflows([]);
    }
  }, []);

  useEffect(() => {
    // Direktlink, z.B. /belege?doc_id=42 (aus den Tasks/Logs): ID ins Suchfeld "Beleg-ID" eintragen und
    // sofort danach filtern. Auch der ältere Parameter ?id= wird verstanden. Optional include_deleted=1.
    // (Aus window.location statt useSearchParams gelesen: kein Suspense nötig, und der erste Render
    // bleibt identisch zum Server-Render → keine Hydration-Abweichung.)
    const params = new URLSearchParams(window.location.search);
    const idParam = (params.get("doc_id") ?? params.get("id") ?? "").trim();
    const initial: DocumentFilter = {};
    if (/^\d+$/.test(idParam)) {
      initial.doc_id = parseInt(idParam, 10);
      setFilterDocId(idParam);
      if (params.get("include_deleted") === "1") {
        initial.include_deleted = true;
        setIncludeDeleted(true);
      }
      setActiveFilters(initial);
    }
    activeFiltersRef.current = initial;
    loadDocuments(initial, 0);      // nur die erste Seite — schnell, auch bei sehr vielen Belegen
    loadOptions();
  }, [loadDocuments, loadOptions]);

  // Auswahllisten folgen dem Schalter „Ausgeblendete Imports einbeziehen"
  useEffect(() => { loadImportOptions(includeHidden); }, [loadImportOptions, includeHidden]);

  // Auto-Refresh, solange Dokumente verarbeitet werden. Lädt die AKTUELLE Seite neu (pageRef) —
  // ein Seitenwechsel wird dadurch nicht zurückgesetzt. Geprüft wird auch, ob irgendwo (nicht nur
  // auf dieser Seite) noch Dokumente verarbeitet werden (z.B. nach "KI-Analyse starten").
  const [watchProcessing, setWatchProcessing] = useState(false);
  const hasProcessingOnPage = documents.some((d) => d.status === "processing");
  useEffect(() => {
    if (!hasProcessingOnPage && !watchProcessing) return;
    const timer = setInterval(async () => {
      await loadDocuments(activeFiltersRef.current);
      try {
        const busy = await documentsApi.listPage({ status: "processing" }, 0, 1);
        if (busy.total === 0) setWatchProcessing(false);
      } catch { /* nächster Tick */ }
    }, 5000);
    return () => clearInterval(timer);
  }, [hasProcessingOnPage, watchProcessing, loadDocuments]);

  // ─── Filter-Logik ─────────────────────────────────────────────────────────

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") applyFilters();
  }

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
    if (includeHidden) f.include_hidden = true;
    if (filterKi === "ja") f.has_extraction = true;
    if (filterKi === "nein") f.has_extraction = false;
    if (filterSupplierName.trim()) f.supplier_name = filterSupplierName.trim();
    if (filterDocId.trim()) f.doc_id = parseInt(filterDocId.trim(), 10);
    if (filterValidation && VALIDATION_FILTERS[filterValidation]) {
      f.validation_status = VALIDATION_FILTERS[filterValidation].statuses;
    }
    return f;
  }

  /** Hält doc_id/include_deleted in der Adresszeile synchron (teilbar, Neuladen-sicher) */
  function syncUrl(f: DocumentFilter) {
    const url = new URL(window.location.href);
    url.searchParams.delete("id");
    if (f.doc_id !== undefined && !Number.isNaN(f.doc_id)) url.searchParams.set("doc_id", String(f.doc_id));
    else url.searchParams.delete("doc_id");
    if (f.doc_id !== undefined && f.include_deleted) url.searchParams.set("include_deleted", "1");
    else url.searchParams.delete("include_deleted");
    window.history.replaceState(null, "", url.pathname + url.search);
  }

  function applyFilters() {
    const f = buildFilters();
    syncUrl(f);
    setActiveFilters(f);
    activeFiltersRef.current = f;
    setSelectedIds(new Set());
    setPreviewDocId(null);
    loadDocuments(f, 0);
  }

  function resetFilters() {
    setFilterYear(""); setFilterStatus("");
    setFilterTotalMin(""); setFilterTotalMax(""); setFilterPageMin(""); setFilterPageMax("");
    setSelectedBatchIds(new Set()); setSelectedDocTypeIds(new Set()); setIncludeDeleted(false); setIncludeHidden(false);
    setFilterKi(""); setFilterSupplierName(""); setFilterDocId(""); setFilterValidation("");
    const f: DocumentFilter = {};
    syncUrl(f);
    setActiveFilters(f);
    activeFiltersRef.current = f;
    setSelectedIds(new Set());
    setPreviewDocId(null);
    loadDocuments(f, 0);
  }

  function goToPage(p: number) {
    const last = Math.max(0, Math.ceil(total / pageSize) - 1);
    const target = Math.min(Math.max(0, p), last);
    setPreviewDocId(null);
    loadDocuments(activeFilters, target);
  }

  async function changePageSize(size: number) {
    pageSizeRef.current = size;                      // für das Laden sofort wirksam
    await loadDocuments(activeFilters, 0);
    setPageSize(size);                               // Anzeige erst umstellen, wenn die Daten dazu da sind
  }

  /** Alle Treffer (über alle Seiten) auswählen */
  const [selectingAll, setSelectingAll] = useState(false);
  async function selectAllHits() {
    setSelectingAll(true);
    try {
      setSelectedIds(new Set(await documentsApi.ids(activeFilters)));
    } catch (err) {
      setError(extractApiError(err, "Treffer konnten nicht ausgewählt werden"));
    } finally {
      setSelectingAll(false);
    }
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
      const result = await documentsApi.enqueue(
        Array.from(selectedIds),
        selectedWorkflowId ? parseInt(selectedWorkflowId, 10) : undefined,
      );
      setSuccessMsg(result.message);
      setSelectedIds(new Set());
      setWatchProcessing(true);
      await loadDocuments(activeFilters);
    } catch (err: unknown) {
      setError(extractApiError(err, "Fehler beim Einreihen der KI-Analyse"));
    } finally {
      setAnalyzing(false);
    }
  }

  /** Ausgewählte Dokumente auf die ersten N Seiten kürzen (jeweils als neue Version) */
  async function trimSelected() {
    const n = parseInt(trimMaxPages, 10);
    if (selectedIds.size === 0 || !Number.isFinite(n) || n < 1) return;
    if (!confirm(
      `${selectedIds.size} Dokument(e) auf die ersten ${n} Seite(n) kürzen? ` +
      `Dokumente mit höchstens ${n} Seite(n) bleiben unverändert. Die Originale bleiben als Version 1 erhalten.`
    )) return;
    setTrimming(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const r = await documentsApi.autoTrim(Array.from(selectedIds), n);
      const errs = Object.entries(r.errors);
      setSuccessMsg(
        `${r.trimmed.length} gekürzt, ${r.unchanged.length} unverändert` +
        (errs.length ? `, ${errs.length} Fehler (${errs.map(([id, m]) => `#${id}: ${m}`).join("; ")})` : "") +
        (r.trimmed.length ? " — bitte danach neu analysieren." : "")
      );
      await loadDocuments(activeFilters);
    } catch (err: unknown) {
      setError(extractApiError(err, "Fehler beim Kürzen"));
    } finally {
      setTrimming(false);
    }
  }

  /** Nach einer PDF-Änderung: Liste und ggf. geöffnete Infos-Ansicht aktualisieren */
  async function handlePdfChanged() {
    await loadDocuments(activeFilters);
    if (editDoc && viewedDoc && viewedDoc.id === editDoc.id) {
      try { setViewedDoc(await documentsApi.get(editDoc.id)); } catch { /* ignore */ }
    }
  }

  // ─── Soft-Delete / Restore ────────────────────────────────────────────────

  async function handleDelete(docId: number) {
    try {
      // Wenn gerade die PDF dieses Dokuments angezeigt wird → zur nächsten/vorherigen wechseln
      if (previewDocId === docId) {
        const idx = documents.findIndex((d) => d.id === docId);
        const nextPreview =
          documents.slice(idx + 1).find((d) => !d.soft_deleted) ??
          documents.slice(0, idx).reverse().find((d) => !d.soft_deleted) ??
          null;
        setPreviewDocId(nextPreview?.id ?? null);
      }
      await documentsApi.softDelete(docId);
      setSelectedIds((prev) => { const next = new Set(prev); next.delete(docId); return next; });
      await loadDocuments(activeFilters);
    } catch (err) {
      setError(extractApiError(err, "Fehler beim Löschen des Belegs"));
    }
  }

  /** Löscht das aktuell angezeigte Dokument in der Infos-Ansicht und navigiert zum nächsten/vorherigen. */
  async function handleDeleteInInfos(docId: number) {
    const idx = documents.findIndex((d) => d.id === docId);
    const nextDoc = documents[idx + 1] ?? documents[idx - 1] ?? null;
    try {
      await documentsApi.softDelete(docId);
      setSelectedIds((prev) => { const next = new Set(prev); next.delete(docId); return next; });
      await loadDocuments(activeFilters);
      if (nextDoc) {
        await openView(nextDoc.id, "infos");
      } else {
        closeView();
      }
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
      if (mode === "infos") {
        setTimeout(() => window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" }), 80);
      }
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
    if (idx < 0) return;
    let target: DocumentItem | undefined = documents[idx + delta];
    if (!target) {
      // Seitengrenze: angrenzende Seite laden und am Rand weitermachen
      const newPage = page + (delta > 0 ? 1 : -1);
      if (newPage < 0 || newPage * pageSize >= total) return;
      const docs = await loadDocuments(activeFilters, newPage);
      target = delta > 0 ? docs[0] : docs[docs.length - 1];
    }
    if (!target) return;
    await openView(target.id, "infos");
  }

  // ─── Ableitungen ──────────────────────────────────────────────────────────

  const activeDocs = documents.filter((d) => !d.soft_deleted);
  const allSelected = activeDocs.length > 0 && selectedIds.size === activeDocs.length;
  const someSelected = selectedIds.size > 0 && selectedIds.size < activeDocs.length;
  const availableYears = yearOptions;
  const availableSuppliers = supplierOptions;
  const infosIdx = infosDocId !== null ? documents.findIndex((d) => d.id === infosDocId) : -1;

  // Pagination (serverseitig)
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const rangeFrom = total === 0 ? 0 : page * pageSize + 1;
  const rangeTo = Math.min((page + 1) * pageSize, total);

  // ─── Render ───────────────────────────────────────────────────────────────

  const showPreview = previewDocId !== null;
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
                <option value="waiting_capacity">Wartet auf KI-Kapazität</option>
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
                onKeyDown={handleKeyDown}
                placeholder="z.B. 42" min="1"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-28" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Plausibilität</label>
              <select value={filterValidation} onChange={(e) => setFilterValidation(e.target.value)}
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-44">
                <option value="">Alle</option>
                {Object.entries(VALIDATION_FILTERS).map(([key, v]) => (
                  <option key={key} value={key}>{v.label}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Betrag von (€)</label>
              <input type="number" value={filterTotalMin} onChange={(e) => setFilterTotalMin(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="0" min="0" step="0.01"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-28" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Betrag bis (€)</label>
              <input type="number" value={filterTotalMax} onChange={(e) => setFilterTotalMax(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="∞" min="0" step="0.01"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-28" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Seiten von</label>
              <input type="number" value={filterPageMin} onChange={(e) => setFilterPageMin(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="1" min="1"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-24" />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-gray-600">Seiten bis</label>
              <input type="number" value={filterPageMax} onChange={(e) => setFilterPageMax(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="∞" min="1"
                className="rounded border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none w-24" />
            </div>
            <label className="flex cursor-pointer items-center gap-2 mt-auto pb-1.5">
              <input type="checkbox" checked={includeDeleted} onChange={(e) => setIncludeDeleted(e.target.checked)}
                className="rounded border-gray-300 text-blue-600" />
              <span className="text-sm text-gray-600">Gelöschte anzeigen</span>
            </label>
            {/* Belege ausgeblendeter Imports */}
            <label className="flex cursor-pointer items-center gap-2 mt-auto pb-1.5"
              title="Belege von Imports, die auf dem Dashboard ausgeblendet wurden. Ein ausdrücklich gewählter Import wird immer angezeigt.">
              <input type="checkbox" checked={includeHidden} onChange={(e) => setIncludeHidden(e.target.checked)}
                className="rounded border-gray-300 text-blue-600" />
              <span className="text-sm text-gray-600">Ausgeblendete Imports einbeziehen</span>
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
            <div className="ml-auto flex items-center gap-2">
              <label className={`text-xs font-medium ${selectedIds.size > 0 ? "text-blue-700" : "text-gray-500"}`}>Workflow</label>
              <select value={selectedWorkflowId} onChange={(e) => setSelectedWorkflowId(e.target.value)}
                title="Automatisch: Workflow des Lieferanten, sonst Standard-Workflow, sonst klassischer Ablauf"
                className="rounded border border-gray-300 bg-white px-2 py-1.5 text-sm focus:border-blue-500 focus:outline-none">
                <option value="">Automatisch</option>
                {workflows.map((w) => (
                  <option key={w.id} value={w.id}>{w.name}{w.is_default ? " (Standard)" : ""}</option>
                ))}
              </select>
            </div>
            <button onClick={startAnalysis} disabled={analyzing || selectedIds.size === 0}
              className="rounded bg-blue-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
              {analyzing ? "Wird eingereiht…" : "KI-Analyse starten"}
            </button>
          </div>

          {/* PDFs kürzen (für die Auswahl) */}
          <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-gray-200 pt-3">
            <span className={`text-sm font-medium ${selectedIds.size > 0 ? "text-blue-800" : "text-gray-500"}`}>PDFs kürzen:</span>
            <span className="text-sm text-gray-600">nur die ersten</span>
            <input type="number" min={1} value={trimMaxPages} onChange={(e) => setTrimMaxPages(e.target.value)}
              className="w-16 rounded border border-gray-300 bg-white px-2 py-1 text-center text-sm focus:border-blue-500 focus:outline-none" />
            <span className="text-sm text-gray-600">Seite(n) behalten</span>
            <button onClick={trimSelected} disabled={trimming || selectedIds.size === 0}
              className="rounded border border-blue-300 bg-white px-3 py-1 text-sm font-medium text-blue-700 hover:bg-blue-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
              {trimming ? "Kürze..." : "Auswahl kürzen"}
            </button>
            <span className="text-xs text-gray-400">Original bleibt als Version 1 erhalten. Automatisch vor jeder Analyse: Einstellungen → Automatisierung.</span>
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
                  Beleg <span className="font-semibold text-gray-800">{(page * pageSize + infosIdx + 1).toLocaleString("de-DE")}</span>
                  <span className="text-gray-400"> / {total.toLocaleString("de-DE")}</span>
                </span>
              )}
              <div className="ml-auto flex items-center gap-2">
                <button onClick={() => navigateInfos(-1)} disabled={page * pageSize + infosIdx <= 0 || viewLoading}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40 transition-colors">
                  ← Vorherige
                </button>
                <button onClick={() => navigateInfos(1)} disabled={page * pageSize + infosIdx >= total - 1 || viewLoading}
                  className="rounded border border-gray-300 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40 transition-colors">
                  Nächste →
                </button>
                {infosDocId !== null && (
                  <button
                    onClick={() => handleDeleteInInfos(infosDocId)}
                    disabled={viewLoading}
                    className="rounded border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-40 transition-colors"
                  >
                    🗑 Löschen
                  </button>
                )}
              </div>
            </div>

            {viewLoading ? (
              <div className="flex items-center justify-center py-16 text-gray-400 text-sm rounded-xl border bg-white shadow-sm">
                <span className="animate-spin mr-2 inline-block">⟳</span> Lade…
              </div>
            ) : viewedDoc ? (
              <div className="flex gap-4">
                <div className="w-1/2 shrink-0 overflow-y-auto rounded-xl border bg-white shadow-sm" style={{ maxHeight: "calc(113vh - 14rem)" }}>
                  <div className="sticky top-0 z-10 border-b bg-white px-5 py-3">
                    <h2 className="text-sm font-semibold text-gray-900 truncate">{viewedDoc.original_filename}</h2>
                    <p className="text-xs text-gray-500">{viewedDoc.company} {viewedDoc.year} · #{viewedDoc.id}</p>
                    <button
                      onClick={() => setEditDoc({ id: viewedDoc.id, filename: viewedDoc.original_filename })}
                      className="mt-1.5 rounded border border-gray-300 px-2 py-0.5 text-xs font-medium text-gray-600 hover:bg-gray-100"
                    >
                      PDF bearbeiten (Seiten / Versionen)
                    </button>
                  </div>
                  <div className="p-5">
                    <InfosView doc={viewedDoc} onDocChange={setViewedDoc} />
                  </div>
                </div>
                <div className="w-1/2 shrink-0 rounded-xl border bg-white shadow-sm overflow-hidden">
                  <iframe
                    src={documentsApi.previewUrl(viewedDoc.id)}
                    className="w-full rounded-xl"
                    style={{ height: "calc(113vh - 14rem)" }}
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
            {/* Anzahl + Blätterleiste (oben) */}
            <Pager testId="pager-top" loading={loading} total={total} page={page} pageSize={pageSize}
              totalPages={totalPages} rangeFrom={rangeFrom} rangeTo={rangeTo}
              selectedCount={selectedIds.size} selectingAll={selectingAll}
              onSelectAll={selectAllHits} onClearSelection={() => setSelectedIds(new Set())}
              onPage={goToPage} onPageSize={changePageSize} />

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
                    {!showPreview && <th className="px-3 py-3 text-left font-medium text-gray-600">Firma</th>}
                    {!showPreview && <th className="px-3 py-3 text-left font-medium text-gray-600">Jahr</th>}
                    <th className="px-3 py-3 text-left font-medium text-gray-600">Dateiname</th>
                    {!showPreview && <th className="px-3 py-3 text-right font-medium text-gray-600">Seiten</th>}
                    {!showPreview && <th className="px-3 py-3 text-right font-medium text-gray-600">Betrag</th>}
                    <th className="px-3 py-3 text-left font-medium text-gray-600">Status</th>
                    {!showPreview && <th className="px-3 py-3 text-left font-medium text-gray-600">KI</th>}
                    <th className="px-3 py-3 text-left font-medium text-gray-600 whitespace-nowrap">Prüfung</th>
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
                    <tr><td colSpan={13} className="px-4 py-8 text-center text-gray-400">Wird geladen...</td></tr>
                  )}
                  {!loading && documents.length === 0 && (
                    <tr><td colSpan={13} className="px-4 py-8 text-center text-gray-400">Keine Belege gefunden</td></tr>
                  )}
                  {documents.map((doc) => {
                    const isDeleted = doc.soft_deleted;
                    const isActivePreview = previewDocId === doc.id;
                    const isActiveInfos = infosDocId === doc.id;
                    const docTypeName = getDocTypeName(doc.document_type);

                    return (
                      <tr key={doc.id} data-testid="row"
                        className={[
                          isDeleted ? "bg-red-50 opacity-60"
                            : isActiveInfos ? "bg-blue-100 ring-1 ring-inset ring-blue-200"
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
                        {!showPreview && <td className="px-3 py-2.5 font-medium text-gray-900">{doc.company ?? "–"}</td>}
                        {!showPreview && <td className="px-3 py-2.5 text-gray-700">{doc.year ?? "–"}</td>}

                        <td className={`px-3 py-2.5 text-gray-700 ${showPreview ? "max-w-[9rem]" : "max-w-xs"}`}>
                          <div className="flex items-center gap-2">
                            <span className="truncate" title={doc.original_filename}>{doc.original_filename}</span>
                            {isDeleted && (
                              <span className="shrink-0 inline-flex items-center rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                                Gelöscht
                              </span>
                            )}
                          </div>
                        </td>

                        {!showPreview && <td className="px-3 py-2.5 text-right text-gray-700 tabular-nums">
                          {doc.page_count > 0 ? doc.page_count : "–"}
                          {(doc.pdf_version ?? 1) > 1 && (
                            <span
                              title={`PDF bearbeitet (Version ${doc.pdf_version})${
                                doc.has_extraction && doc.analyzed_pdf_version !== doc.pdf_version ? " — seit der Analyse geändert, bitte neu analysieren" : ""
                              }`}
                              className={`ml-1 rounded px-1 text-[10px] font-semibold ${
                                doc.has_extraction && doc.analyzed_pdf_version !== doc.pdf_version
                                  ? "bg-amber-100 text-amber-800" : "bg-gray-100 text-gray-500"
                              }`}
                            >
                              v{doc.pdf_version}
                            </span>
                          )}
                        </td>}
                        {!showPreview && <td className="px-3 py-2.5 text-right text-gray-700 tabular-nums">
                          {formatCurrency(doc.total_amount)}
                        </td>}
                        <td className="px-3 py-2.5 whitespace-nowrap"><StatusBadge status={doc.status} /></td>
                        {!showPreview && <td className="px-3 py-2.5"><KiBadge hasExtraction={doc.has_extraction ?? false} /></td>}
                        <td className="px-3 py-2.5 whitespace-nowrap"><ValidationBadge status={doc.validation_status} /></td>

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
                          <div className="flex items-center gap-1.5 flex-nowrap whitespace-nowrap">
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
                                {isActivePreview ? "✕ PDF" : "PDF"}
                              </button>
                            )}

                            {/* Seiten bearbeiten / Versionen */}
                            {!isDeleted && doc.stored_filename && doc.status !== "processing" && (
                              <button
                                onClick={() => setEditDoc({ id: doc.id, filename: doc.original_filename })}
                                title="Seiten entfernen, Versionen ansehen"
                                className="rounded border border-gray-300 px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-100 transition-colors"
                              >
                                Seiten
                              </button>
                            )}

                            {(doc.status === "done" || doc.status === "error" || doc.status === "waiting_capacity") && (
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
                            ) : (
                              <button onClick={() => handleDelete(doc.id)}
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

            {/* Blätterleiste (unten) */}
            <div className="mt-3">
              <Pager testId="pager-bottom" loading={loading} total={total} page={page} pageSize={pageSize}
                totalPages={totalPages} rangeFrom={rangeFrom} rangeTo={rangeTo} compact
                onPage={goToPage} onPageSize={changePageSize} />
            </div>

            {/* PDF-Vorschau */}
            {showPreview && createPortal(
              <div className="fixed right-0 bottom-0 z-40 flex w-1/2 flex-col border-l border-gray-200 bg-white shadow-2xl"
                   style={{ top: 0 }}>
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

                  {/* Fallback auf viewedDoc.raw_response (Document-Ebene) für Fälle ohne
                      InvoiceExtraction, z.B. waiting_capacity — dort steht der Grund als
                      Klartext, nicht als KI-JSON-Antwort. */}
                  <KiRawView rawResponse={viewedDoc.extraction?.raw_response ?? viewedDoc.raw_response ?? null} />
                  <UsedWorkflow doc={viewedDoc} />
                  <UsedPrompts doc={viewedDoc} />
                </>
              )}
              {!viewLoading && !viewedDoc && (
                <p className="text-sm text-red-500">Dokument konnte nicht geladen werden.</p>
              )}
            </div>
          </div>
        </div>
      )}

      {editDoc && (
        <PdfEditModal
          docId={editDoc.id}
          filename={editDoc.filename}
          onClose={() => setEditDoc(null)}
          onChanged={handlePdfChanged}
        />
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

function InfosView({
  doc,
  onDocChange,
}: {
  doc: DocumentDetail;
  onDocChange: (doc: DocumentDetail) => void;
}) {
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
      {doc.extraction && doc.analyzed_pdf_version != null && doc.analyzed_pdf_version !== (doc.pdf_version ?? 1) && (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          Die PDF wurde nach der letzten KI-Analyse bearbeitet (analysiert: Version {doc.analyzed_pdf_version},
          aktuell: Version {doc.pdf_version}). Bitte neu analysieren, damit die Daten zur aktuellen PDF passen.
        </div>
      )}
      <ValidationPanel doc={doc} onChange={onDocChange} />
      <AnalysisHistoryPanel doc={doc} onChange={onDocChange} />

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
        <Row label="Bestellnummer" value={rechnung?.bestellnummer} />
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
                  <th className="px-3 py-2 text-right">Gesamtpreis</th>
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
                          <td className="px-3 py-2 text-right tabular-nums">
                            {(() => {
                              // Gespeicherter (ggf. durch die Plausibilitätsprüfung korrigierter) Gesamtpreis
                              const stored = doc.order_positions.find((p) => p.position_index === i)?.total_price;
                              const original = pos.gesamtpreis;
                              const shown = stored ?? original;
                              const changed = stored != null && original != null
                                && Math.abs(Number(stored) - Number(String(original).replace(",", "."))) > 0.004;
                              return (
                                <span
                                  className={changed ? "font-medium text-emerald-700" : ""}
                                  title={changed ? `Korrigiert (laut KI-Auslesung: ${fmt(original)})` : undefined}
                                >
                                  {shown != null ? fmt(shown) : "–"}
                                </span>
                              );
                            })()}
                          </td>
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
                        <td className="px-3 py-2 text-right tabular-nums">{pos.total_price != null ? fmt(pos.total_price) : "–"}</td>
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

// ─── Plausibilitätsprüfung (Infos-Ansicht) ────────────────────────────────────

function ValidationPanel({
  doc,
  onChange,
}: {
  doc: DocumentDetail;
  onChange: (doc: DocumentDetail) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const status = doc.validation_status;
  const v = doc.validation;
  if (!doc.extraction || !status) return null;

  async function run(action: "revalidate" | "confirm") {
    setBusy(true);
    setErr(null);
    try {
      const updated = action === "revalidate"
        ? await documentsApi.revalidate(doc.id)
        : await documentsApi.confirmValidation(doc.id);
      onChange(updated);
    } catch (e) {
      setErr(extractApiError(e, "Aktion fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  }

  const fmtNum = (n: number) =>
    new Intl.NumberFormat("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(n);
  const info = VALIDATION_STYLES[status];
  const box =
    status === "needs_review" ? "border-red-200 bg-red-50"
    : status === "corrected" || status === "ai_corrected" ? "border-emerald-200 bg-emerald-50"
    : status === "manual_ok" ? "border-blue-200 bg-blue-50"
    : "border-green-200 bg-green-50";
  const sevCls: Record<string, string> = {
    error: "text-red-700", warning: "text-red-700", info: "text-gray-600",
  };

  return (
    <div className={`mb-6 rounded-lg border p-4 ${box}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold text-gray-700 uppercase tracking-wide">Plausibilitätsprüfung</h3>
        <ValidationBadge status={status} />
        <span className="text-xs text-gray-500">{info?.hint}</span>
      </div>

      {(v?.corrections ?? []).length > 0 && (
        <ul className="mb-2 space-y-1 text-sm text-amber-900">
          {v!.corrections.map((c, i) => (
            <li key={i}>
              {c.field === "total_amount" ? (
                <>
                  ↻ Gesamtbetrag (Brutto){" "}
                  {c.from == null ? "fehlte und wurde berechnet:" : <>von <b>{fmtNum(c.from)} €</b> auf</>}{" "}
                  <b>{fmtNum(c.to)} €</b>{c.from == null ? "" : " korrigiert"} (Netto + Steuer)
                </>
              ) : c.field === "unit" ? (
                <>
                  ↻ Position {(c.position ?? 0) + 1}: Mengeneinheit von <b>{String(c.from ?? "–")}</b> auf{" "}
                  <b>{String(c.to)}</b> korrigiert (passend zur Preiseinheit)
                </>
              ) : (
                <>
                  ↻ Position {(c.position ?? 0) + 1}: Positionspreis von <b>{fmtNum(c.from ?? 0)} €</b> auf{" "}
                  <b>{fmtNum(c.to)} €</b> korrigiert (Faktor {String(c.factor ?? "").replace(".", ",")})
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      {(v?.issues ?? []).length > 0 && (
        <ul className="mb-2 space-y-1 text-sm">
          {v!.issues.map((i, idx) => (
            <li key={idx} className={sevCls[i.severity] ?? "text-gray-600"}>
              {i.severity === "info" ? "ℹ" : "⚠"} {i.message}
            </li>
          ))}
        </ul>
      )}

      {(v?.calc ?? []).length > 0 && (
        <details className="mb-2 rounded border border-gray-200 bg-white/70" data-testid="calc-details">
          <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium text-gray-700">
            Durchrechnung ({v!.calc!.length} Prüfungen
            {v!.calc!.some((r) => r.status === "warn") ? `, ${v!.calc!.filter((r) => r.status === "warn").length} auffällig` : ", alle stimmig"})
          </summary>
          <table className="w-full text-xs">
            <thead className="border-t border-gray-200 bg-gray-50 text-left text-gray-500">
              <tr>
                <th className="px-3 py-1 font-medium">Prüfung</th>
                <th className="px-3 py-1 text-right font-medium">Berechnet</th>
                <th className="px-3 py-1 text-right font-medium">Laut Rechnung</th>
                <th className="w-8 px-2 py-1"></th>
              </tr>
            </thead>
            <tbody>
              {v!.calc!.map((r, i) => (
                <tr key={i} className={`border-t border-gray-100 ${r.status === "warn" ? "bg-red-50" : ""}`}>
                  <td className="px-3 py-1 text-gray-700">
                    {r.label}
                    {r.note && <span className="block text-[11px] text-gray-400">{r.note}</span>}
                  </td>
                  <td className="px-3 py-1 text-right tabular-nums">{r.expected == null ? "–" : `${fmtNum(r.expected)} €`}</td>
                  <td className="px-3 py-1 text-right tabular-nums">{r.actual == null ? "–" : `${fmtNum(r.actual)} €`}</td>
                  <td className={`px-2 py-1 text-center font-bold ${r.status === "ok" ? "text-green-600" : "text-red-600"}`}>
                    {r.status === "ok" ? "✓" : "✕"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {v?.recheck?.performed && (
        <p className="mb-2 text-xs text-gray-600">
          Zweite KI-Abfrage:{" "}
          {v.recheck.outcome === "corrected" && "Auslesung korrigiert — jetzt plausibel."}
          {v.recheck.outcome === "still_inconsistent" && "weiterhin nicht plausibel — ursprüngliche Auslesung behalten."}
          {v.recheck.outcome === "failed" && `fehlgeschlagen${v.recheck.error ? ` (${v.recheck.error})` : ""}.`}
        </p>
      )}

      {err && <p className="mb-2 text-xs text-red-600">{err}</p>}

      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => run("revalidate")}
          disabled={busy}
          className="rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
        >
          {busy ? "…" : "Prüfung erneut ausführen"}
        </button>
        {/* Nur Belege mit ungelöster Auffälligkeit brauchen eine Bestätigung — korrigierte nicht */}
        {status === "needs_review" && (
          <button
            onClick={() => run("confirm")}
            disabled={busy}
            className="rounded bg-blue-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Als geprüft bestätigen
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Verwendete Systemprompts (KI-Modal) ──────────────────────────────────────

function UsedPrompts({ doc }: { doc: DocumentDetail }) {
  const items: { title: string; text: string | null | undefined }[] = [
    { title: "Systemprompt (Extraktion)", text: doc.used_system_prompt },
    { title: "Systemprompt (Dokumententyp-Erkennung)", text: doc.used_doc_type_prompt },
    { title: "Systemprompt (zweite KI-Abfrage / Plausibilität)", text: doc.used_recheck_prompt },
  ];
  const present = items.filter((i) => i.text);
  if (present.length === 0) return null;
  return (
    <div className="mt-4 space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
        Bei dieser Analyse verwendete Prompts
      </h3>
      {present.map((i) => (
        <details key={i.title} className="rounded-lg border border-gray-200 bg-gray-50">
          <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-gray-700">{i.title}</summary>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words border-t border-gray-200 p-3 text-xs text-gray-700">
            {i.text}
          </pre>
        </details>
      ))}
    </div>
  );
}

// ─── Verwendeter Workflow + Ablaufprotokoll (KI-Modal) ────────────────────────

function UsedWorkflow({ doc }: { doc: DocumentDetail }) {
  const run: WorkflowTraceEntry[] = doc.workflow_run ?? [];
  if (!doc.used_workflow_name) return null;
  return (
    <div className="mt-4">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        Verwendeter Workflow: {doc.used_workflow_name}
      </h3>
      <ol className="space-y-1 rounded-lg border border-gray-200 bg-gray-50 p-3 text-sm">
        {run.map((t, i) => (
          <li key={`${t.node_id}-${i}`} className="flex flex-wrap items-baseline gap-x-2">
            <span className={t.status === "error" ? "text-red-600" : "text-green-600"}>{t.status === "error" ? "✕" : "✓"}</span>
            <span className="font-medium text-gray-800">{t.label}</span>
            {t.branch && (
              <span className={`rounded px-1.5 text-xs font-semibold ${t.branch === "true" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                {t.branch === "true" ? "Ja" : "Nein"}
              </span>
            )}
            <span className="text-xs text-gray-400">{t.duration_s.toLocaleString("de-DE")} s</span>
            {t.detail && <span className={`text-xs ${t.status === "error" ? "text-red-600" : "text-gray-500"}`}>— {t.detail}</span>}
          </li>
        ))}
      </ol>
      {doc.used_workflow && (
        <details className="mt-2 rounded-lg border border-gray-200 bg-gray-50">
          <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-gray-700">Workflow-Definition (Snapshot der Analyse)</summary>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words border-t border-gray-200 p-3 text-xs text-gray-700">
            {JSON.stringify(doc.used_workflow, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
