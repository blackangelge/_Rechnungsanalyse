/**
 * Zentraler API-Client für das Rechnungsanalyse-Frontend.
 *
 * Verwendet axios mit einer konfigurierbaren Basis-URL:
 * - Server-seitig (SSR/RSC): vollständige URL über NEXT_PUBLIC_API_URL
 * - Client-seitig (Browser): leere Basis → Next.js-Rewrite-Proxy übernimmt
 *
 * WICHTIG: Alle axios-Calls verwenden einen trailing Slash (z.B. /api/documents/).
 * Der Next.js-Rewrite-Proxy entfernt diesen, das FastAPI-Backend empfängt die URL
 * ohne Slash — das passt zu redirect_slashes=False in main.py.
 *
 * Array-Parameter (batch_ids, document_type_ids) werden ohne Klammern serialisiert:
 *   batch_ids=1&batch_ids=2  (nicht: batch_ids[]=1&batch_ids[]=2)
 * Das entspricht dem FastAPI Query(default=None)-Format mit list[int].
 */

import axios from "axios";

const BASE =
  typeof window === "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : "";

export const apiClient = axios.create({
  baseURL: BASE,
  headers: { "Content-Type": "application/json" },
  timeout: 60000,
});

// Array-Parameter ohne Klammern serialisieren
apiClient.defaults.paramsSerializer = (params) => {
  const parts: string[] = [];
  for (const key of Object.keys(params)) {
    const val = params[key];
    if (Array.isArray(val)) {
      val.forEach((v) => parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(v)}`));
    } else if (val !== undefined && val !== null) {
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(val)}`);
    }
  }
  return parts.join("&");
};

// Abgelaufene/fehlende Anmeldung: zur Anmeldeseite (außer bei den Auth-Aufrufen selbst,
// die ihren 401 selbst anzeigen).
apiClient.interceptors.response.use(
  (r) => r,
  (error) => {
    const status = error?.response?.status;
    const url: string = error?.config?.url ?? "";
    if (
      status === 401 &&
      typeof window !== "undefined" &&
      !url.includes("/api/auth/") &&
      !window.location.pathname.startsWith("/login")
    ) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/login?next=${next}`;
    }
    return Promise.reject(error);
  },
);

export function extractApiError(err: unknown, fallback = "Unbekannter Fehler"): string {
  if (err && typeof err === "object") {
    const e = err as {
      response?: { data?: { detail?: unknown }; status?: number };
      message?: string;
    };
    const detail = e.response?.data?.detail;
    if (typeof detail === "string" && detail) return detail;
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    const status = e.response?.status;
    if (status === 0 || e.message === "Network Error")
      return "Backend nicht erreichbar — bitte Container-Status prüfen";
    if (status) return `HTTP ${status}: ${fallback}`;
    if (e.message) return e.message;
  }
  return fallback;
}

// ─── Typen: KI-Konfigurationen (ai_clients) ──────────────────────────────────

export type ReasoningLevel = "off" | "low" | "medium" | "high" | "on";
export type EndpointType = "openai" | "lmstudio";

/** Primärtyp: 0 = Dokumententyp-Erkennung, 1 = Eingangsrechnungsanalyse */
export type PrimaryType = 0 | 1;

export interface AIConfig {
  id: number;
  name: string;
  api_key: string | null;
  model_name: string;
  /** 0 = Dokumententyp-Erkennung, 1 = Eingangsrechnungsanalyse */
  primary_type: PrimaryType;
  max_tokens: number;
  temperature: number;
  chat_response: boolean;
  active: boolean;
  reasoning: ReasoningLevel;
  ip_address: string;
  endpoint_type: EndpointType;
  /** Optional — null/leer = kein :port in der gebauten URL. */
  port: string | null;
  parallel_request: number;
  /** Maximale Kontext-Kapazität in Token (Eingabe). null = keine Beschränkung. */
  max_context_tokens: number | null;
  /** ISO-Zeitstempel: wenn gesetzt und in der Zukunft → temporär deaktiviert */
  timeout_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AIConfigCreate {
  name: string;
  api_key?: string | null;
  model_name: string;
  primary_type?: PrimaryType;
  max_tokens?: number;
  temperature?: number;
  chat_response?: boolean;
  active?: boolean;
  reasoning?: ReasoningLevel;
  ip_address?: string;
  endpoint_type?: EndpointType;
  port?: string | null;
  parallel_request?: number;
  max_context_tokens?: number | null;
}

export const aiConfigsApi = {
  list: () => apiClient.get<AIConfig[]>("/api/ai-clients/").then((r) => r.data),
  get: (id: number) => apiClient.get<AIConfig>(`/api/ai-clients/${id}`).then((r) => r.data),
  create: (data: AIConfigCreate) =>
    apiClient.post<AIConfig>("/api/ai-clients/", data).then((r) => r.data),
  update: (id: number, data: AIConfigCreate) =>
    apiClient.put<AIConfig>(`/api/ai-clients/${id}`, data).then((r) => r.data),
  delete: (id: number) => apiClient.delete(`/api/ai-clients/${id}`),
  toggleActive: (id: number) =>
    apiClient.post<AIConfig>(`/api/ai-clients/${id}/toggle-active`).then((r) => r.data),
  /** Hebt eine temporäre Sperre auf ohne active zu ändern */
  clearTimeout: (id: number) =>
    apiClient.post<AIConfig>(`/api/ai-clients/${id}/clear-timeout`).then((r) => r.data),
  /**
   * Sendet ein Testdokument an diese KI-Konfiguration (unabhängig von active) und
   * gibt die vollständige Anfrage + Antwort zurück. Schreibt nichts in die DB.
   * Langes Timeout, da lokale Modelle für große/mehrseitige Dokumente Minuten
   * brauchen können.
   */
  test: (id: number, file: File, systemPromptId?: number | null) => {
    const form = new FormData();
    form.append("file", file);
    if (systemPromptId != null) form.append("system_prompt_id", String(systemPromptId));
    // WICHTIG: Content-Type explizit auf undefined setzen (nicht einfach weglassen!).
    // apiClient hat "Content-Type: application/json" als Instanz-Default (siehe oben,
    // axios.create({ headers: {...} })). Dieser Default gilt für JEDEN Request dieser
    // Instanz, auch FormData-Uploads — axios erkennt dann fälschlich "Content-Type ist
    // bereits explizit gesetzt" und deaktiviert seine automatische FormData-Erkennung,
    // wodurch der Body als JSON.stringify(form) statt als multipart gesendet wird (die
    // Datei kommt beim Backend nie an → 422 "file: Field required"). Nur ein WEGLASSEN
    // des Headers reicht deshalb NICHT — er muss aktiv auf undefined überschrieben werden,
    // damit axios ihn entfernt und der Browser den korrekten
    // "multipart/form-data; boundary=..."-Header selbst berechnet.
    // Timeout muss über dem Backend-seitigen ai_test_service.REQUEST_TIMEOUT_SECONDS
    // (900s) liegen — sonst bricht der Browser die Anfrage vorzeitig ab, bevor das
    // Backend selbst einen sauberen Timeout-Fehler zurückgeben konnte.
    return apiClient
      .post<AIConfigTestResult>(`/api/ai-clients/${id}/test`, form, {
        headers: { "Content-Type": undefined },
        timeout: 920000,
      })
      .then((r) => r.data);
  },
};

export interface AIConfigTestResult {
  request: {
    method: string;
    url: string;
    headers: Record<string, string>;
    body: Record<string, unknown>;
  };
  response: {
    status_code: number;
    headers: Record<string, string>;
    body: string;
    duration_seconds: number;
  } | null;
  error: string | null;
  success: boolean;
}

// ─── Typen: Import-Batches ────────────────────────────────────────────────────

export interface ImportBatch {
  id: number;
  import_folder_path: string;
  storage_folder_path: string;
  company_name: string;
  year: number;
  comment: string | null;
  /** Workflow für Analyse nach Import, Ordner-Sync und erneutes Einreihen */
  workflow_id?: number | null;
  status: "pending" | "running" | "done" | "error";
  folder_sync: boolean | null;
  last_synced_at: string | null;
  auto_export: boolean;
  last_exported_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  total_documents?: number;
  /** gesetzt = Import ist ausgeblendet */
  hidden_at?: string | null;
}

export interface ImportBatchWithDocuments extends ImportBatch {
  documents: DocumentItem[];
}

export interface ImportBatchCreate {
  folder_path?: string;
  subfolder?: string;
  comment?: string;
  company_name?: string;
  year?: number;
  ai_config_id?: number;
  system_prompt_id?: number;
  /** Pflicht bei analyze_after_import oder folder_sync — es gibt keinen Standard-Workflow */
  workflow_id?: number;
  analyze_after_import?: boolean;
  delete_source_files?: boolean;
  folder_sync?: boolean;
  auto_export?: boolean;
}

export interface BatchKiStats {
  total_tokens: number;
  total_duration_seconds: number;
}

export interface BatchQueueStatus {
  pending: number;
  in_progress: number;
  completed: number;
  failed: number;
  total: number;
}

export const importsApi = {
  list: (params?: { company_name?: string; year?: number; include_hidden?: boolean }) =>
    apiClient.get<ImportBatch[]>("/api/imports/", { params }).then((r) => r.data),

  /** Imports aus- bzw. einblenden (es wird nichts gelöscht) */
  setHidden: (ids: number[], hidden: boolean) =>
    apiClient
      .post<{ updated: number; hidden: boolean; ids: number[] }>("/api/imports/visibility", { ids, hidden })
      .then((r) => r.data),

  /** Download-URL für den Excel-Export von einem oder mehreren Imports (eine gemeinsame Datei) */
  exportUrl: (ids: number[]) => `/api/imports/export/?${ids.map((i) => `ids=${i}`).join("&")}`,
  get: (id: number) =>
    apiClient.get<ImportBatchWithDocuments>(`/api/imports/${id}`).then((r) => r.data),
  create: (data: ImportBatchCreate) =>
    apiClient.post<ImportBatch>("/api/imports/", data).then((r) => r.data),
  getStatus: (id: number) =>
    apiClient.get<ImportBatch>(`/api/imports/${id}/status`).then((r) => r.data),
  kiStats: (id: number) =>
    apiClient.get<BatchKiStats>(`/api/imports/${id}/ki-stats`).then((r) => r.data),
  updateAutomation: (id: number, data: { folder_sync?: boolean; auto_export?: boolean; workflow_id?: number | null }) =>
    apiClient.patch<ImportBatch>(`/api/imports/${id}/automation/`, data).then((r) => r.data),
  queueStatus: (id: number) =>
    apiClient.get<BatchQueueStatus>(`/api/imports/${id}/queue-status/`).then((r) => r.data),
  requeue: (id: number, systemPromptId?: number | null) =>
    apiClient
      .post<{ batch_id: number; total_documents: number; enqueued: number }>(
        `/api/imports/${id}/requeue/`,
        null,
        { params: systemPromptId != null ? { system_prompt_id: systemPromptId } : undefined }
      )
      .then((r) => r.data),
  dequeue: (id: number) =>
    apiClient
      .delete<{ batch_id: number; removed: number }>(`/api/imports/${id}/queue/`)
      .then((r) => r.data),
  delete: (id: number) => apiClient.delete(`/api/imports/${id}`),
};

// ─── Typen: Dokumente ─────────────────────────────────────────────────────────

export interface DocumentItem {
  id: number;
  batch_id: number;
  original_filename: string;
  stored_filename: string | null;
  file_size_bytes: number;
  page_count: number;
  /** Zustand der PDF (Hintergrunddienst): ok | encrypted | unreadable; null = noch nicht geprüft */
  pdf_state?: "ok" | "encrypted" | "unreadable" | null;
  /** Fehlercode, wenn der Beleg nachbearbeitet werden muss: too_many_pages | encrypted | unreadable */
  error_code?: string | null;
  company: string | null;
  year: number | null;
  /** Integer: 0=Unbekannt, 1=Eingangsrechnung, 2=Ausgangsrechnung, … */
  document_type: number;
  comment: string | null;
  /** waiting_capacity = kein Endzustand, wird automatisch neu versucht sobald eine
   *  ausreichend große KI-Konfiguration verfügbar ist (siehe worker.py) */
  status: "pending" | "processing" | "done" | "error" | "waiting_capacity";
  soft_deleted: boolean;
  /** PDF-Version (aktuelle) und Version, auf der die letzte KI-Analyse lief */
  pdf_version?: number;
  analyzed_pdf_version?: number | null;
  created_at: string;
  /** Kurzfelder aus der Extraktion */
  total_amount?: number | null;
  invoice_number?: string | null;
  supplier_name?: string | null;
  has_extraction?: boolean;
  /** Ergebnis der Plausibilitätsprüfung (nur bei Eingangsrechnungen) */
  validation_status?: ValidationStatus | null;
}

/** Eine gespeicherte PDF-Version (Original = 1, danach manuell/auto/restore) */
export interface DocumentVersion {
  /** null = Original, das noch nie bearbeitet wurde (noch nicht gespeichert) */
  id: number | null;
  version_number: number;
  page_count: number;
  file_size_bytes: number;
  source: "original" | "manual" | "auto" | "restore";
  description: string | null;
  parent_version: number | null;
  /** Entfernte Seiten (1-basiert, bezogen auf die Elternversion) */
  removed_pages: number[] | null;
  created_at: string | null;
  is_current: boolean;
}

export interface AutoTrimResult {
  trimmed: number[];
  unchanged: number[];
  errors: Record<string, string>;
}

export interface PdfSettings {
  id: number;
  auto_trim_enabled: boolean;
  auto_trim_max_pages: number;
}

/** ok = plausibel · corrected = automatisch korrigiert · ai_corrected = durch zweite
 *  KI-Abfrage korrigiert · needs_review = manuelle Prüfung nötig · manual_ok = vom
 *  Nutzer bestätigt · unchecked = nichts prüfbar */
export type ValidationStatus =
  | "ok" | "corrected" | "ai_corrected" | "needs_review" | "manual_ok" | "unchecked";

export interface ValidationIssue {
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
  /** 0-basierter Positionsindex (null = betrifft die ganze Rechnung) */
  position: number | null;
  details: Record<string, unknown>;
}

/** Automatische Korrektur: Positionspreis (`position` gesetzt) oder Gesamtbetrag (`field: "total_amount"`) */
export interface ValidationCorrection {
  position?: number;
  field: string;
  from: number | null;
  to: number;
  factor?: number;
  method: string;
}

/** Eine Zeile der „Durchrechnung": berechneter Wert (expected) ↔ Wert laut Rechnung (actual) */
export interface ValidationCalcRow {
  label: string;
  expected: number | null;
  actual: number | null;
  status: "ok" | "warn";
  note?: string | null;
}

export interface ValidationDetails {
  status: ValidationStatus;
  method?: "rules" | "ai_recheck";
  issues: ValidationIssue[];
  corrections: ValidationCorrection[];
  checks_run: number;
  /** Durchrechnung aller Werte (Positionen, Netto, Steuer je Satz, Brutto) */
  calc?: ValidationCalcRow[];
  /** Einzelschritt-Berechnung je Position (Menge × Preis, Nachlass, Einheiten, Korrekturen, gespeicherter Stand) */
  position_calcs?: { position: number; label: string; status: "ok" | "warn" | "skip"; steps: string[] }[];
  confirmed_manually?: boolean;
  recheck?: {
    performed: boolean;
    outcome?: "corrected" | "still_inconsistent" | "failed";
    issues_before?: ValidationIssue[];
    issues_after?: ValidationIssue[];
    error?: string;
  };
}

export interface OrderPosition {
  id: number;
  document_id: number;
  position_index: number;
  product_name: string | null;
  product_description: string | null;
  article_number: string | null;
  unit_price_netto: number | null;
  unit_price_brutto: number | null;
  /** Gesamtpreis der Position (ggf. durch die Plausibilitätsprüfung korrigiert) */
  total_price: number | null;
  tax: number | null;
  quantity: number | null;
  unit: string | null;
  discount: string | null;
  /** Einzelpreis gilt je N Einheiten (z. B. 100 = „Preis je 100 Stück“) */
  price_quantity?: number | string | null;
  /** Inhalt einer Verpackungs-/Verkaufseinheit (z. B. 100) und dessen Einheit */
  pack_size?: number | string | null;
  pack_unit?: string | null;
  /** stueck | verpackung | gewicht | volumen | laenge | flaeche | pauschal | dienstleistung | sonstige */
  unit_type?: string | null;
}

export interface InvoiceExtraction {
  id: number;
  document_id: number;
  vendor_id: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  due_date: string | null;
  total_amount_netto: number | null;
  total_amount_brutto: number | null;
  total_tax_value: number | null;
  total_tax: number | null;
  discount_amount: number | null;
  cash_discount_amount: number | null;
  payment_terms: string | null;
  raw_response: string | null;
  validation_status?: ValidationStatus | null;
  created_at: string;
  updated_at: string;
}

/**
 * Ein einzelner KI-Analyse-Durchlauf.
 * Jeder Aufruf von save_extraction() im Backend erzeugt einen neuen Eintrag —
 * bestehende werden nie überschrieben. Dadurch ist die komplette Historie sichtbar.
 */
export interface TokenCountEntry {
  id: number;
  input_token_count: number;
  output_token_count: number;
  /** Reasoning-Token: nur bei Reasoning-Modellen > 0, sonst immer 0 */
  reasoning_count: number;
  /** Gesamtdauer des HTTP-Requests in Sekunden */
  time_spent_seconds: number;
  /** ISO-Zeitstempel des Durchlaufs — für chronologische Sortierung im KI-Modal */
  created_at: string;
}

export interface DocumentDetail extends DocumentItem {
  /** Vollständige Rechnungsextraktion — nur bei Eingangsrechnungen (document_type=1) */
  extraction: InvoiceExtraction | null;
  /**
   * NUR für Status-Übergänge OHNE InvoiceExtraction gefüllt (z.B. waiting_capacity) —
   * der Grund als Klartext. Für Eingangsrechnungen liegt die KI-Rohantwort stattdessen
   * in extraction.raw_response, NICHT hier.
   */
  raw_response: string | null;
  /** Alle extrahierten Rechnungspositionen */
  order_positions: OrderPosition[];
  /** Alle KI-Analyse-Durchläufe, chronologisch — je Eintrag = ein Aufruf */
  token_counts: TokenCountEntry[];
  /**
   * Aggregierte Summen über alle token_counts-Einträge.
   * Werden direkt aus DocumentDetail gelesen (nicht aus extraction),
   * damit auch Nicht-Eingangsrechnungen Token-Stats haben.
   */
  ki_input_tokens: number | null;
  ki_output_tokens: number | null;
  ki_reasoning_tokens: number | null;
  ki_total_duration: number | null;
  /** Plausibilitätsprüfung: Details (Auffälligkeiten, Korrekturen, Durchrechnung) */
  validation?: ValidationDetails | null;
  /** Wortlaut der bei der letzten Analyse verwendeten Systemprompts */
  used_system_prompt?: string | null;
  used_doc_type_prompt?: string | null;
  used_recheck_prompt?: string | null;
  /** Bei der letzten Analyse verwendeter Workflow (Name, Graph-Snapshot) + Ablaufprotokoll */
  used_workflow_id?: number | null;
  used_workflow_name?: string | null;
  used_workflow?: WorkflowGraph | null;
  workflow_run?: WorkflowTraceEntry[] | null;
}

/**
 * Filter-Parameter für GET /api/documents/.
 * Fehlende Felder werden ignoriert (keine Filterung auf diesem Kriterium).
 * Arrays (batch_ids, document_type_ids) werden als mehrfache Query-Parameter gesendet.
 */
export interface DocumentFilter {
  company?: string;
  year?: number;
  status?: string;
  total_min?: number;
  total_max?: number;
  page_min?: number;
  page_max?: number;
  batch_ids?: number[];
  include_deleted?: boolean;
  include_hidden?: boolean;
  has_extraction?: boolean;
  supplier_name?: string;
  doc_id?: number;
  document_type_ids?: number[];
  validation_status?: ValidationStatus[];
  /** "any" = alle Belege, die nachbearbeitet werden müssen, oder konkrete Codes */
  error_code?: string[];
}

export interface AnalyzeRequest {
  document_ids: number[];
  ai_config_id?: number;
  system_prompt_id?: number;
  /** Workflow (Analyse-Ablauf). Leer = automatisch: Lieferant → Standard → klassisch */
  workflow_id?: number;
}

export const documentsApi = {
  list: (filters?: DocumentFilter) =>
    apiClient.get<DocumentItem[]>("/api/documents/", { params: filters }).then((r) => r.data),
  /**
   * Eine Seite der Belegliste + Gesamtzahl (Header X-Total-Count). Die Belege-Seite lädt immer nur
   * die aktuelle Seite — bei vielen tausend Belegen ist `list` (alle Treffer) zu langsam.
   */
  listPage: async (filters: DocumentFilter | undefined, page: number, pageSize: number) => {
    const r = await apiClient.get<DocumentItem[]>("/api/documents/", {
      params: { ...filters, limit: pageSize, offset: page * pageSize },
    });
    const header = Number(r.headers["x-total-count"]);
    return { items: r.data, total: Number.isFinite(header) ? header : r.data.length };
  },
  /** Alle Treffer-IDs zu den Filtern (für „alle Treffer auswählen") */
  ids: (filters?: DocumentFilter) =>
    apiClient.get<number[]>("/api/documents/ids", { params: filters }).then((r) => r.data),
  /** Werte für die Filter-Dropdowns, unabhängig von der geladenen Seite */
  filterOptions: (includeHidden = false) =>
    apiClient
      .get<{ years: number[]; companies: string[]; suppliers: string[] }>("/api/documents/filter-options", {
        params: { include_hidden: includeHidden },
      })
      .then((r) => r.data),
  get: (id: number) =>
    apiClient.get<DocumentDetail>(`/api/documents/${id}`).then((r) => r.data),
  previewUrl: (id: number) => `${BASE}/api/documents/${id}/preview`,
  updateComment: (id: number, comment: string | null) =>
    apiClient.patch<DocumentDetail>(`/api/documents/${id}/comment`, { comment }).then((r) => r.data),
  softDelete: (id: number) =>
    apiClient.delete<DocumentDetail>(`/api/documents/${id}`).then((r) => r.data),
  restore: (id: number) =>
    apiClient.post<DocumentDetail>(`/api/documents/${id}/restore`).then((r) => r.data),
  analyze: (data: AnalyzeRequest) =>
    apiClient.post<{ started: number; message: string }>("/api/documents/analyze", data).then((r) => r.data),
  /** Versionsverlauf der PDF (Original + Bearbeitungen) */
  versions: (id: number) =>
    apiClient.get<DocumentVersion[]>(`/api/documents/${id}/versions`).then((r) => r.data),

  /** URL zur PDF einer bestimmten Version */
  versionPreviewUrl: (id: number, version: number) =>
    `${BASE}/api/documents/${id}/versions/${version}/preview`,

  /** URL zum Vorschaubild einer Seite (1-basiert) einer Version */
  pageThumbnailUrl: (id: number, page: number, version: number, width = 240) =>
    `${BASE}/api/documents/${id}/pages/${page}/thumbnail?version=${version}&width=${width}`,

  /** Seiten entfernen ODER nur bestimmte behalten → neue Version */
  editPages: (id: number, data: { remove_pages?: number[]; keep_pages?: number[]; note?: string }) =>
    apiClient.post<DocumentVersion>(`/api/documents/${id}/pages/remove`, data).then((r) => r.data),

  /** Frühere Version als neue aktuelle Version wiederherstellen */
  restoreVersion: (id: number, version: number) =>
    apiClient
      .post<DocumentVersion>(`/api/documents/${id}/versions/${version}/restore`)
      .then((r) => r.data),

  /** Mehrere Dokumente auf die ersten N Seiten kürzen */
  autoTrim: (document_ids: number[], max_pages: number) =>
    apiClient
      .post<AutoTrimResult>("/api/documents/auto-trim", { document_ids, max_pages })
      .then((r) => r.data),

  /** Plausibilitätsprüfung auf den gespeicherten Daten erneut ausführen */
  revalidate: (id: number) =>
    apiClient.post<DocumentDetail>(`/api/documents/${id}/validate`).then((r) => r.data),
  /** Prüfung nach manueller Kontrolle als "geprüft" bestätigen */
  confirmValidation: (id: number) =>
    apiClient.post<DocumentDetail>(`/api/documents/${id}/validation/confirm`).then((r) => r.data),
  /** Dokumente in die Worker-Warteschlange einreihen; optional mit einem bestimmten Analyse-Workflow */
  enqueue: (documentIds: number[], workflowId: number, skipTypeDetection = false) =>
    apiClient
      .post<{ enqueued: number; message: string }>("/api/documents/enqueue", {
        document_ids: documentIds,
        workflow_id: workflowId,
        ...(skipTypeDetection ? { skip_type_detection: true } : {}),
      })
      .then((r) => r.data),
};

// ─── Typen: Bildkonvertierungseinstellungen ───────────────────────────────────

export interface ImageSettings {
  id: number;
  dpi: number;
  image_format: "PNG" | "JPEG";
  jpeg_quality: number;
  grayscale: boolean;
}

export interface ImageSettingsUpdate {
  dpi: number;
  image_format: "PNG" | "JPEG";
  jpeg_quality: number;
  grayscale: boolean;
}

export const imageSettingsApi = {
  get: () => apiClient.get<ImageSettings>("/api/settings/image-conversion/").then((r) => r.data),
  update: (data: ImageSettingsUpdate) =>
    apiClient.put<ImageSettings>("/api/settings/image-conversion/", data).then((r) => r.data),
};

// ─── Typen: Automatisierungseinstellungen (Ordner-Sync-Intervall, Export-Zeitplan) ──

export interface AutomationSettings {
  id: number;
  folder_sync_interval_minutes: number;
  /** 0=Montag … 6=Sonntag */
  export_weekday: number;
  export_hour: number;
  export_minute: number;
  /** Maximale Anzahl gleichzeitiger Worker im Worker-Container */
  worker_concurrency: number;
  /** Poll-Intervall des Dispatchers in Sekunden */
  worker_poll_interval_seconds: number;
  /** Ab wie vielen Minuten ein verwaister 'in_progress'-Task zurückgesetzt wird */
  worker_lock_timeout_minutes: number;
  /** Aufbewahrte frühere Analyse-Stände je Beleg (0 = Historie aus) */
  analysis_history_keep: number;
}

export interface AutomationSettingsUpdate {
  folder_sync_interval_minutes: number;
  export_weekday: number;
  export_hour: number;
  export_minute: number;
  worker_concurrency: number;
  worker_poll_interval_seconds: number;
  worker_lock_timeout_minutes: number;
  analysis_history_keep: number;
}

export const automationSettingsApi = {
  get: () => apiClient.get<AutomationSettings>("/api/settings/automation/").then((r) => r.data),
  update: (data: AutomationSettingsUpdate) =>
    apiClient.put<AutomationSettings>("/api/settings/automation/", data).then((r) => r.data),
};

// ─── Analyse-Historie ────────────────────────────────────────────────────────

export interface AnalysisHistoryItem {
  id: number;
  document_id: number;
  created_at: string;
  pdf_version: number | null;
  workflow_name: string | null;
  validation_status: string | null;
  invoice_number: string | null;
  supplier_name: string | null;
  total_amount: number | string | null;
  position_count: number;
}

export interface AnalysisHistoryDetail extends AnalysisHistoryItem {
  extraction: Partial<InvoiceExtraction> & Record<string, unknown>;
  order_positions: Partial<OrderPosition>[];
  used_system_prompt?: string | null;
  used_doc_type_prompt?: string | null;
  used_recheck_prompt?: string | null;
  validation?: ValidationDetails | null;
}

export const analysisHistoryApi = {
  list: (docId: number) =>
    apiClient.get<AnalysisHistoryItem[]>(`/api/documents/${docId}/analysis-history`).then((r) => r.data),
  get: (docId: number, id: number) =>
    apiClient.get<AnalysisHistoryDetail>(`/api/documents/${docId}/analysis-history/${id}`).then((r) => r.data),
  restore: (docId: number, id: number) =>
    apiClient.post(`/api/documents/${docId}/analysis-history/${id}/restore`).then((r) => r.data),
  remove: (docId: number, id: number) =>
    apiClient.delete(`/api/documents/${docId}/analysis-history/${id}`).then(() => undefined),
};

export const pdfSettingsApi = {
  get: () => apiClient.get<PdfSettings>("/api/settings/pdf").then((r) => r.data),
  update: (data: { auto_trim_enabled: boolean; auto_trim_max_pages: number }) =>
    apiClient.put<PdfSettings>("/api/settings/pdf", data).then((r) => r.data),
};

export const importSettingsApi = {
  getPaths: () =>
    apiClient.get<{ import_base_path: string; storage_path: string }>("/api/settings/paths/").then((r) => r.data),
};

// ─── Typen: Systemprompts ─────────────────────────────────────────────────────

export interface SystemPrompt {
  id: number;
  name: string;
  content: string;
  /** 0 = Dokumententyp-Erkennung, 1 = Standard-Extraktion (Eingangsrechnung), 2 = Workflow-Baustein, 3 = Antwortformat */
  type: number;
  created_at: string;
  updated_at: string;
}

export interface SystemPromptCreate {
  name: string;
  content: string;
  /** 0 = Dokumententyp-Erkennung, 1 = Standard-Extraktion (Eingangsrechnung) */
  type?: number;
}

export const systemPromptsApi = {
  list: () =>
    apiClient.get<SystemPrompt[]>("/api/settings/system-prompts/").then((r) => r.data),
  create: (data: SystemPromptCreate) =>
    apiClient.post<SystemPrompt>("/api/settings/system-prompts/", data).then((r) => r.data),
  update: (id: number, data: SystemPromptCreate) =>
    apiClient.put<SystemPrompt>(`/api/settings/system-prompts/${id}/`, data).then((r) => r.data),
  delete: (id: number) => apiClient.delete(`/api/settings/system-prompts/${id}/`),
  /** Als Standard-Extraktionsprompt setzen — bisherige Standards werden zu Workflow-Bausteinen */
  setDefault: (id: number) =>
    apiClient.post<SystemPrompt>(`/api/settings/system-prompts/${id}/set-default/`).then((r) => r.data),
  /** Das eingebaute Standard-Antwortformat (Vorlage für einen Prompt vom Typ 3) */
  defaultFormat: () =>
    apiClient.get<{ content: string }>("/api/settings/system-prompts/default-format/").then((r) => r.data.content),
};

// ─── Typen: Dokumententypen (statisch) ───────────────────────────────────────

export interface DocumentType {
  id: number;
  name: string;
}

/** Statische Dokumententypen — gespiegelt vom Backend */
export const DOCUMENT_TYPES: DocumentType[] = [
  { id: 0,  name: "Unbekannt" },
  { id: 1,  name: "Eingangsrechnung" },
  { id: 2,  name: "Ausgangsrechnung" },
  { id: 3,  name: "Lieferschein" },
  { id: 4,  name: "Bestellbestätigung" },
  { id: 5,  name: "Angebot" },
  { id: 6,  name: "Gutschrift / Storno" },
  { id: 7,  name: "Mahnung" },
  { id: 8,  name: "Kontoauszug" },
  { id: 9,  name: "Vertrag" },
  { id: 10, name: "Lohnabrechnung" },
  { id: 11, name: "Steuer- / Behördendokument" },
  { id: 12, name: "Reisekostenabrechnung" },
  { id: 13, name: "Kassenbon / Quittung" },
  { id: 14, name: "Sonstiges kaufmännisches Dokument" },
];

export function getDocTypeName(typeId: number): string {
  return DOCUMENT_TYPES.find((t) => t.id === typeId)?.name ?? `Typ ${typeId}`;
}

export const documentTypesApi = {
  list: () => apiClient.get<DocumentType[]>("/api/document-types/").then((r) => r.data),
};

// ─── Backup / Restore ─────────────────────────────────────────────────────────

export const backupApi = {
  download: () =>
    apiClient.get("/api/settings/backup/", { responseType: "blob" }).then((r) => r.data),
  restore: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    // Content-Type MUSS explizit auf undefined gesetzt werden (nicht nur weglassen!) —
    // siehe ausführlicher Kommentar bei aiConfigsApi.test() oben, gleicher Bug (Instanz-
    // Default "application/json" verhindert sonst axios' automatische FormData-Erkennung).
    return apiClient
      .post<{ restored: Record<string, number>; message: string }>(
        "/api/settings/restore/",
        form,
        { headers: { "Content-Type": undefined } }
      )
      .then((r) => r.data);
  },
};

// ─── KI-Stats ─────────────────────────────────────────────────────────────────

export interface KiStats {
  total_entries: number;
  sum_input_tokens: number | null;
  sum_output_tokens: number | null;
  sum_reasoning: number | null;
  sum_duration_seconds: number | null;
  avg_input_tokens: number | null;
  avg_duration_seconds: number | null;
  /** Belege mit status='done', deren letzter Status-Übergang <= 60 Minuten her ist */
  processed_last_60min: number;
  /** Belege mit status IN ('done','error'), deren letzter Status-Übergang <= 24 Std. her ist */
  analyzed_last_24h: number;
}

export const logsApi = {
  kiStats: () => apiClient.get<KiStats>("/api/logs/ki-stats/").then((r) => r.data),
};

// ─── Typen: Workflows ─────────────────────────────────────────────────────────

export type WorkflowNodeType =
  | "start" | "trim_pages" | "detect_type" | "branch" | "extract" | "schema_extract" | "classify_pages" | "validate" | "normalize_units" | "ai_refine" | "end";

export interface WorkflowNodeConfig {
  // trim_pages
  max_pages?: number;
  // detect_type / extract
  system_prompt_id?: number | null;
  ai_config_id?: number | null;
  // extract / ai_refine: Prompt vom Typ „Antwortformat“
  format_prompt_id?: number | null;
  // normalize_units
  target?: "t" | "kg";
  mode?: "tonne_prices" | "all";
  // ai_refine
  include_images?: boolean;
  // schema_extract
  schema_version_id?: number | null;
  // classify_pages
  dpi?: number;
  chunk_size?: number;
  action?: "classify_only" | "keep_invoice_pages";
  // validate
  auto_correct?: boolean;
  ai_recheck?: boolean;
  // branch
  field?: "document_type" | "supplier_name" | "page_count" | "invoice_page_count" | "validation_status";
  op?: "equals" | "not_equals" | "contains" | "gt" | "lt" | "in";
  value?: string;
}

export interface WorkflowGraphNode {
  id: string;
  type: WorkflowNodeType;
  position: { x: number; y: number };
  data: { label: string; config: WorkflowNodeConfig };
}

export interface WorkflowGraphEdge {
  id: string;
  source: string;
  target: string;
  /** "true" | "false" bei Verzweigungen, sonst null */
  sourceHandle?: string | null;
}

export interface WorkflowGraph {
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
}

export interface WorkflowListItem {
  id: number;
  name: string;
  description: string | null;
  is_default: boolean;
  node_count: number;
  supplier_count: number;
  created_at: string;
  updated_at: string;
}

export interface Workflow {
  id: number;
  name: string;
  description: string | null;
  graph: WorkflowGraph;
  is_default: boolean;
  supplier_count: number;
  created_at: string;
  updated_at: string;
}

export interface WorkflowTemplate {
  key: string;
  name: string;
  description: string;
  graph: WorkflowGraph;
}

export interface WorkflowValidation {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

/** Ein Eintrag im Ablaufprotokoll einer Analyse */
/** Datenstand im Ablauf (vor/nach einem Knoten bzw. aus der Datenbank) */
export interface WorkflowSnapshot {
  document_type?: string | null;
  page_count?: number | null;
  extracted?: boolean;
  validation_status?: string | null;
  fields?: Record<string, string | number | boolean | null>;
  positions?: Record<string, string | number | null>[];
  positions_total?: number;
  validation?: { issues: string[]; corrections: unknown[]; normalizations: unknown[] };
}

export interface WorkflowTraceEntry {
  node_id: string;
  /** "saved" = abschließender Eintrag „In Datenbank gespeichert" */
  type: WorkflowNodeType | "saved";
  label: string;
  status: "ok" | "error";
  duration_s: number;
  detail: string | null;
  branch: "true" | "false" | null;
  config?: Record<string, unknown>;
  input?: WorkflowSnapshot;
  output?: WorkflowSnapshot;
  /** Änderungen dieses Knotens an den Daten (leer = nichts verändert) */
  changes?: { path: string; from: unknown; to: unknown }[];
  /** Prompt/Antwort der KI und Hinweise des Knotens */
  io?: { prompt?: string | null; response?: string | null; model?: string | null; notes?: string[] };
}

export const workflowsApi = {
  list: () => apiClient.get<WorkflowListItem[]>("/api/workflows/").then((r) => r.data),
  templates: () => apiClient.get<WorkflowTemplate[]>("/api/workflows/templates").then((r) => r.data),
  get: (id: number) => apiClient.get<Workflow>(`/api/workflows/${id}`).then((r) => r.data),
  create: (data: { name: string; description?: string | null; template?: string; graph?: WorkflowGraph }) =>
    apiClient.post<Workflow>("/api/workflows/", data).then((r) => r.data),
  update: (id: number, data: { name: string; description?: string | null; graph: WorkflowGraph }) =>
    apiClient.put<Workflow>(`/api/workflows/${id}`, data).then((r) => r.data),
  delete: (id: number) => apiClient.delete(`/api/workflows/${id}`),
  validate: (graph: WorkflowGraph) =>
    apiClient.post<WorkflowValidation>("/api/workflows/validate", { graph }).then((r) => r.data),
  duplicate: (id: number) =>
    apiClient.post<Workflow>(`/api/workflows/${id}/duplicate`).then((r) => r.data),
};

// ─── Typen: Lieferanten (Vendors) ────────────────────────────────────────────

export interface VendorBankAccount {
  id: number;
  bank_name: string | null;
  iban: string | null;
  bic: string | null;
}

export interface Vendor {
  id: number;
  name: string;
  street: string | null;
  postal_code: string | null;
  city: string | null;
  country: string | null;
  hrb_number: string | null;
  tax_number: string | null;
  vat_id: string | null;
  /** Eigener Analyse-Workflow dieses Lieferanten (null = Standard) */
  workflow_id?: number | null;
  bank_accounts: VendorBankAccount[];
  /** Belege mit diesem Lieferantennamen (nicht gelöschte) */
  document_count?: number;
}

export interface VendorUpdate {
  name: string;
  street?: string | null;
  postal_code?: string | null;
  city?: string | null;
  country?: string | null;
  hrb_number?: string | null;
  tax_number?: string | null;
  vat_id?: string | null;
}

export interface DuplicateGroup {
  suppliers: Vendor[];
  reasons: string[];
  reason_labels: string[];
  confidence: "high" | "medium" | "low";
}

export interface VendorMergeRequest {
  target_id: number;
  source_ids: number[];
  /** Ausdrücklich gewählte Endwerte einzelner Felder */
  fields?: Record<string, string | null>;
  /** Belege auf den Namen des Ziels umschreiben (nur dann gehören sie danach zum Ziel) */
  update_document_names?: boolean;
}

export interface VendorMergeResult {
  supplier: Vendor;
  merged_ids: number[];
  moved_documents: number;
  moved_bank_accounts: number;
}

export interface DuplicateSearchOptions {
  criteria: string[];            // iban | vat_id | tax_number | name | name_similar
  nameCheck: boolean;            // Nummern-Treffer nur bei ähnlichem Namen werten
  minNameSimilarity: number;     // 0–1
  similarity: number;            // Schwelle „ähnlicher Name“ (0.5–1)
  maxKeyShare: number;           // Nummern, die bei mehr Lieferanten vorkommen, ignorieren
}

export interface OrphanName {
  name: string;
  document_count: number;
  suggestion: { vendor_id: number; vendor_name: string; reason: string; confidence: "high" | "medium" | "low"; ambiguous: boolean } | null;
}

export const vendorsApi = {
  /** Excel mit allen Belegen und Positionen der gewählten Lieferanten */
  exportUrl: (ids: number[], includeHidden = false) =>
    `/api/vendors/export/?${ids.map((i) => `ids=${i}`).join("&")}${includeHidden ? "&include_hidden=true" : ""}`,
  /** Belegnamen ohne Lieferant (z. B. nach früherem Zusammenführen) mit Vorschlag — verändert nichts */
  orphanNames: () =>
    apiClient.get<{ total: number; orphans: OrphanName[] }>("/api/vendors/orphan-names").then((r) => r.data),
  /** Bestätigte Zuordnungen umschreiben (protokolliert, rückgängig machbar) */
  reassignNames: (assignments: { name: string; target_id: number }[]) =>
    apiClient.post<{ moved_documents: number; run_id: number | null }>("/api/vendors/reassign-names", { assignments }).then((r) => r.data),
  /** Dublettensuche (IBAN, USt-IdNr., Steuernummer, Name) — verändert nichts */
  duplicateGroups: (opts?: DuplicateSearchOptions) =>
    apiClient
      .get<DuplicateGroup[]>("/api/vendors/duplicate-groups", {
        params: opts && {
          criteria: opts.criteria, name_check: opts.nameCheck, min_name_similarity: opts.minNameSimilarity,
          similarity: opts.similarity, max_key_share: opts.maxKeyShare,
        },
        paramsSerializer: { indexes: null },
      })
      .then((r) => r.data),
  /** Lieferanten zusammenführen */
  merge: (data: VendorMergeRequest) =>
    apiClient.post<VendorMergeResult>("/api/vendors/merge", data).then((r) => r.data),
  list: () => apiClient.get<Vendor[]>("/api/vendors/").then((r) => r.data),
  get: (id: number) => apiClient.get<Vendor>(`/api/vendors/${id}/`).then((r) => r.data),
  update: (id: number, data: VendorUpdate) =>
    apiClient.put<Vendor>(`/api/vendors/${id}/`, data).then((r) => r.data),
  delete: (id: number) => apiClient.delete(`/api/vendors/${id}/`),
};

// ─── Typen: SSE-Fortschritts-Event ───────────────────────────────────────────

export interface ProgressEvent {
  total: number;
  processed: number;
  percent: number;
  elapsed_seconds: number;
  docs_per_minute: number;
  status: string;
  message?: string;
}

// ─── Typen: Worker-Status ─────────────────────────────────────────────────────

export interface WorkerAIConfig {
  id: number;
  name: string;
  active: boolean;
  temp_disabled: boolean;
  timeout_at: string | null;
  parallel_request: number;
}

export interface WorkerStats {
  worker_count: number;
  max_capacity: number;
  current_capacity: number;
  queue_length: number;
  in_progress: number;
  failed_tasks: number;
  no_ai_available: boolean;
  worker_online: boolean;
  ai_configs: WorkerAIConfig[];
  /** Anzahl Dokumente, die insgesamt mindestens einmal von der KI verarbeitet wurden */
  documents_processed_total: number;
}

export const workerApi = {
  getStats: () =>
    apiClient.get<WorkerStats>("/api/logs/worker-stats/").then((r) => r.data),
};

// ─── Typen: Workflow-Tasks ────────────────────────────────────────────────────

export interface WorkflowTask {
  id: number;
  workflow_id: string;
  payload: Record<string, unknown>;
  status: "pending" | "in_progress" | "completed" | "failed";
  attempts: number;
  max_attempts: number;
  result: Record<string, unknown> | null;
  error: string | null;
  worker_id: string | null;
  locked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkflowTaskListResponse {
  items: WorkflowTask[];
  total: number;
  limit: number;
  offset: number;
}

export interface DispatcherServer {
  id: number;
  name: string;
  active: boolean;
  is_down: boolean;
  parallel_request: number;
  url: string;
}

export interface DispatcherStatus {
  worker_count: number;
  queue_size: number;
  servers: DispatcherServer[];
  paused: boolean | null;
  worker_online: boolean;
}

export const tasksApi = {
  list: (params?: { status?: string; limit?: number; offset?: number }) =>
    apiClient
      .get<WorkflowTaskListResponse>("/api/tasks/", { params })
      .then((r) => r.data),

  getDispatcherStatus: () =>
    apiClient.get<DispatcherStatus>("/api/tasks/workers/").then((r) => r.data),

  pause: () =>
    apiClient.post<DispatcherStatus>("/api/tasks/pause/").then((r) => r.data),

  resume: () =>
    apiClient.post<DispatcherStatus>("/api/tasks/resume/").then((r) => r.data),

  deleteOne: (taskId: number) =>
    apiClient.delete(`/api/tasks/${taskId}/`),

  /** Alle abgeschlossenen (optional auch fehlgeschlagene) Tasks erneut an die KI senden */
  restartAll: (includeFailed = false) =>
    apiClient
      .post<{ restarted: number; include_failed: boolean }>("/api/tasks/restart-all/", null, {
        params: { include_failed: includeFailed }, timeout: 300000,
      })
      .then((r) => r.data),

  deleteByStatus: (status: "completed" | "failed" | "all") =>
    apiClient
      .delete<{ deleted: number; status: string }>("/api/tasks/", {
        params: { status },
      })
      .then((r) => r.data),

  restart: (taskId: number) =>
    apiClient.post<{ task_id: number; status: string }>(`/api/tasks/${taskId}/restart/`).then((r) => r.data),
};

// ─── Excel-Export-Einstellungen ────────────────────────────────────────────────
export interface ExportConfig {
  invoice_fields: string[];
  position_fields: string[];
  invoice_field_labels: Record<string, string>;
  position_field_labels: Record<string, string>;
  invoice_fields_all: string[];
  position_fields_all: string[];
}

export const exportSettingsApi = {
  get: () => apiClient.get<ExportConfig>("/api/settings/export/").then((r) => r.data),
  update: (data: { invoice_fields: string[]; position_fields: string[] }) =>
    apiClient.put<ExportConfig>("/api/settings/export/", data).then((r) => r.data),
};

// ─── Artikelsuche (Positionen über alle Belege) ───────────────────────────────

export interface PositionHit {
  id: number;
  document_id: number;
  position_index: number;
  product_name: string | null;
  product_description: string | null;
  article_number: string | null;
  quantity: number | string | null;
  unit: string | null;
  unit_price: number | string | null;
  /** Umrechnung in die Basiseinheit (kg, l, m, m²); null bei Stück/Verpackungen/unbekannter Einheit */
  base_quantity?: number | string | null;
  base_unit?: string | null;
  base_unit_price?: number | string | null;
  /** Auswertung über den Inhalt (Menge × Verpackungsinhalt, Preis je Inhaltseinheit) — nur bei Verpackung/Preisbasis */
  content_quantity?: number | string | null;
  content_unit?: string | null;
  content_unit_price?: number | string | null;
  total_price: number | string | null;
  discount: string | null;
  /** Einzelpreis gilt je N Einheiten (z. B. 100 = „Preis je 100 Stück“) */
  price_quantity?: number | string | null;
  /** Inhalt einer Verpackungs-/Verkaufseinheit (z. B. 100) und dessen Einheit */
  pack_size?: number | string | null;
  pack_unit?: string | null;
  /** stueck | verpackung | gewicht | volumen | laenge | flaeche | pauschal | dienstleistung | sonstige */
  unit_type?: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  supplier_name: string | null;
  validation_status: ValidationStatus | null;
  company: string;
  year: number;
  batch_id: number;
  original_filename: string;
}

export interface PositionFilter {
  q?: string;
  article_number?: string;
  supplier_name?: string;
  /** Lieferantenname exakt (Auswahl aus der Liste) statt Teilstring — deutlich schneller */
  supplier_exact?: boolean;
  company?: string;
  year?: number;
  batch_ids?: number[];
  document_id?: number;
  invoice_number?: string;
  unit?: string;
  /** alle Einheiten einer Gruppe zusammen (t, kg, g … = mass) */
  unit_group?: "mass" | "volume" | "length" | "area";
  unit_price_min?: number;
  unit_price_max?: number;
  total_price_min?: number;
  total_price_max?: number;
  quantity_min?: number;
  quantity_max?: number;
  /** YYYY-MM-DD */
  date_from?: string;
  date_to?: string;
  validation_status?: ValidationStatus[];
  include_hidden?: boolean;
  include_deleted?: boolean;
}

export type PositionSort =
  | "document_id" | "product_name" | "product_description" | "article_number" | "quantity" | "unit"
  | "unit_price" | "total_price" | "supplier_name" | "invoice_number" | "invoice_date";

export interface PositionSummary {
  count: number;
  documents: number;
  /** Positionen ohne gespeicherten Gesamtpreis (Altbestand) — fehlen bei Preisfiltern */
  without_total_price?: number;
  total_price_sum: number | null;
  unit_price_min: number | null;
  unit_price_avg: number | null;
  unit_price_max: number | null;
  by_unit: { unit: string | null; count: number; quantity: number | null; total_price: number | null }[];
}

export const positionsApi = {
  /** Eine Seite Treffer (serverseitig paginiert); `total` = Gesamtzahl (Header X-Total-Count) */
  search: async (
    filters: PositionFilter,
    page: number,
    pageSize: number,
    sort?: PositionSort,
    order: "asc" | "desc" = "desc",
  ) => {
    const r = await apiClient.get<PositionHit[]>("/api/positions/", {
      params: { ...filters, limit: pageSize, offset: page * pageSize, sort, order },
    });
    const header = Number(r.headers["x-total-count"]);
    return {
      items: r.data,
      total: Number.isFinite(header) ? header : r.data.length,
      /** true = es gibt mehr Treffer als `total` (die Zählung wird bei sehr breiten Filtern gedeckelt) */
      capped: r.headers["x-total-capped"] === "true",
    };
  },

  /** Kennzahlen zu denselben Filtern (Anzahl, Summe, Einzelpreis min/Ø/max, je Einheit) */
  summary: (filters: PositionFilter) =>
    apiClient.get<PositionSummary>("/api/positions/summary", { params: filters }).then((r) => r.data),

  /** Umrechnungstabelle (1 t = 1000 kg …) */
  unitTable: () =>
    apiClient
      .get<{ group: string; label: string; base_unit: string; units: { unit: string; factor: number }[] }[]>(
        "/api/positions/unit-table")
      .then((r) => r.data),

  /** Einheiten für das Filter-Dropdown */
  filterOptions: () =>
    apiClient
      .get<{ units: { unit: string; count: number }[] }>("/api/positions/filter-options")
      .then((r) => r.data),
};

// ─── Nachrüstung der Plausibilitätsprüfung (Altbestand, ohne KI) ──────────────

export interface ValidationBackfillState {
  running: boolean;
  /** Anzahl noch ungeprüfter, fertig analysierter Belege */
  pending: number;
  dry_run?: boolean;
  auto_correct?: boolean;
  normalize_units?: boolean;
  restore_originals?: boolean;
  fill_prices?: boolean;
  price_basis?: boolean;
  run_id?: number;
  changed_rows?: number;
  changed_fields?: number;
  total?: number;
  processed?: number;
  filled_prices?: number;
  corrections?: number;
  /** Prüfstatus → Anzahl (zusätzlich `skipped_no_data` = keine verwertbare KI-Antwort) */
  outcomes?: Record<string, number>;
  error_count?: number;
  errors?: string[];
  cancelled?: boolean;
  started_at?: string;
  finished_at?: string | null;
}

export const validationBackfillApi = {
  status: (normalizeUnits = false, restoreOriginals = false, fillPrices = false, priceBasis = false) =>
    apiClient
      .get<ValidationBackfillState>("/api/validation-backfill/", { params: { normalize_units: normalizeUnits, restore_originals: restoreOriginals, fill_prices: fillPrices, price_basis: priceBasis } })
      .then((r) => r.data),
  start: (opts: { dry_run: boolean; auto_correct: boolean; normalize_units?: boolean; restore_originals?: boolean; fill_prices?: boolean; price_basis?: boolean }) =>
    apiClient.post<ValidationBackfillState>("/api/validation-backfill/", opts).then((r) => r.data),
  cancel: () =>
    apiClient.post<{ cancelled: boolean }>("/api/validation-backfill/cancel").then((r) => r.data),
};

// ─── Anmeldung ───────────────────────────────────────────────────────────────

export interface AuthInfo {
  /** false = Anmeldung ist auf dem Server abgeschaltet (AUTH_ENABLED=false) */
  auth_enabled: boolean;
  username: string | null;
}

export const authApi = {
  me: () => apiClient.get<AuthInfo>("/api/auth/me").then((r) => r.data),
  login: (username: string, password: string) =>
    apiClient.post<AuthInfo>("/api/auth/login", { username, password }).then((r) => r.data),
  logout: () => apiClient.post("/api/auth/logout").then(() => undefined),
  changePassword: (current_password: string, new_password: string) =>
    apiClient.post("/api/auth/change-password", { current_password, new_password }).then(() => undefined),
};

// ─── Daten-Browser (CRUD) und KI-Assistent ───────────────────────────────────

export interface DbColumn {
  name: string;
  type: "bool" | "int" | "zahl" | "datetime" | "date" | "json" | "text" | string;
  nullable: boolean;
  primary_key: boolean;
  references: string | null;
  secret: boolean;
}

export interface DbTable {
  name: string;
  count: number | null;
  primary_key: string | null;
  columns: DbColumn[];
}

export type DbRow = Record<string, unknown>;

export const dbApi = {
  tables: () => apiClient.get<DbTable[]>("/api/db/tables/", { timeout: 120000 }).then((r) => r.data),
  rows: async (
    table: string,
    p: { limit: number; offset: number; q?: string; sort?: string; order?: "asc" | "desc"; filters?: Record<string, string> },
  ) => {
    const params: Record<string, unknown> = { limit: p.limit, offset: p.offset, q: p.q || undefined, sort: p.sort, order: p.order };
    Object.entries(p.filters ?? {}).forEach(([k, v]) => { params[`f.${k}`] = v; });
    const r = await apiClient.get<DbRow[]>(`/api/db/tables/${table}/rows/`, { params });
    const header = Number(r.headers["x-total-count"]);
    return { items: r.data, total: Number.isFinite(header) ? header : r.data.length };
  },
  row: (table: string, pk: unknown) =>
    apiClient.get<DbRow>(`/api/db/tables/${table}/rows/${encodeURIComponent(String(pk))}`).then((r) => r.data),
  update: (table: string, pk: unknown, changes: Record<string, unknown>) =>
    apiClient
      .patch<{ row: DbRow; changed: Record<string, { alt: unknown; neu: unknown }> }>(
        `/api/db/tables/${table}/rows/${encodeURIComponent(String(pk))}`, { changes })
      .then((r) => r.data),
  insert: (table: string, values: Record<string, unknown>) =>
    apiClient.post<{ row: DbRow; pk: unknown }>(`/api/db/tables/${table}/rows/`, { values }).then((r) => r.data),
  remove: (table: string, pk: unknown) =>
    apiClient.delete(`/api/db/tables/${table}/rows/${encodeURIComponent(String(pk))}`).then((r) => r.data),
};

export interface AssistantStep {
  sql: string;
  ok: boolean;
  error?: string;
  row_count?: number;
  columns?: string[];
  rows?: unknown[][];
  truncated?: boolean;
}

export interface AssistantProposal {
  table: string;
  id: unknown;
  changes: Record<string, unknown>;
  current: Record<string, unknown>;
  reason: string;
}

export interface AssistantReanalysis {
  document_ids: number[];
  count: number;
  skipped: number;
  truncated: boolean;
  sample: { id: number; filename: string; status: string }[];
  reason: string;
  skip_type_detection?: boolean;
}

export interface AssistantReply {
  answer: string;
  steps: AssistantStep[];
  proposals: AssistantProposal[];
  reanalysis?: AssistantReanalysis[];
  usage: { input_tokens: number; output_tokens: number; seconds: number; calls: number };
}

export const dbAssistantApi = {
  models: () =>
    apiClient.get<{ id: number; name: string; model_name: string; active: boolean; default: boolean }[]>("/api/db-assistant/models/")
      .then((r) => r.data),
  /** Lokale Modelle können mehrere Minuten brauchen → kein Zeitlimit im Browser (der Proxy erlaubt 15 Minuten) */
  chat: (messages: { role: "user" | "assistant"; content: string }[], aiConfigId?: number, allowProposals = true) =>
    apiClient
      .post<AssistantReply>("/api/db-assistant/chat/", { messages, ai_config_id: aiConfigId ?? null, allow_proposals: allowProposals }, { timeout: 0 })
      .then((r) => r.data),
  /** Freigabe: Belege erneut zur KI-Analyse in die Warteschlange einreihen */
  applyReanalysis: (p: { document_ids: number[]; reason: string; skip_type_detection?: boolean }) =>
    apiClient.post<{ requested: number; eligible: number; enqueued: number; message: string }>("/api/db-assistant/apply-reanalysis/", p).then((r) => r.data),
  apply: (p: { table: string; id: unknown; changes: Record<string, unknown>; reason: string }) =>
    apiClient.post<{ row: DbRow; changed: Record<string, { alt: unknown; neu: unknown }> }>("/api/db-assistant/apply/", p).then((r) => r.data),
};

// ─── Änderungsprotokoll ──────────────────────────────────────────────────────

export interface ChangeRun {
  id: number;
  kind: string;
  label: string | null;
  started_at: string | null;
  finished_at: string | null;
  changed_rows: number;
  changed_fields: number;
  undone_at: string | null;
  undo_summary: string | null;
}

export interface ChangeEntry {
  id: number;
  table: string;
  row_id: number;
  document_id: number | null;
  field: string;
  old: string | null;
  new: string | null;
  /** 0 = offen · 1 = zurückgesetzt · 2 = übersprungen (danach erneut geändert) */
  status: number;
}

export const changeLogApi = {
  runs: (kind?: string, limit = 30) =>
    apiClient.get<ChangeRun[]>("/api/change-log/runs/", { params: { kind, limit } }).then((r) => r.data),
  changes: async (runId: number, offset = 0, limit = 100) => {
    const r = await apiClient.get<ChangeEntry[]>(`/api/change-log/runs/${runId}/changes/`, { params: { offset, limit } });
    return { items: r.data, total: Number(r.headers["x-total-count"]) || r.data.length };
  },
  undo: (runId: number) =>
    apiClient.post<{ running: boolean; reverted?: number; skipped?: number }>(`/api/change-log/runs/${runId}/undo/`).then((r) => r.data),
  undoState: () =>
    apiClient.get<{ running: boolean; run_id?: number; reverted?: number; skipped?: number; error?: string }>("/api/change-log/undo-state/").then((r) => r.data),
};

// ─── Daten-Backup und Import ─────────────────────────────────────────────────

export interface BackupManifestSummary {
  created_at: string;
  include_pdfs: boolean;
  include_vendors: boolean;
  scope: "all" | "selected";
  counts: Record<string, number>;
  files: { docs: number; versions: number; missing: number };
  batches: { id: number; company_name: string; year: number; status: string; created_at: string | null; documents: number }[];
}

export interface BackupFile {
  name: string;
  size: number;
  modified: string;
  manifest: BackupManifestSummary | null;
  error: string | null;
}

export interface BackupJobState {
  running: boolean;
  kind?: "backup" | "restore";
  phase?: string;
  done?: number;
  total?: number;
  files_done?: number;
  warnings?: string[];
  error?: string | null;
  cancelled?: boolean;
  dry_run?: boolean;
  mode?: string;
  name?: string;
  started_at?: string;
  finished_at?: string | null;
  result?: {
    name?: string;
    size?: number;
    counts?: Record<string, number>;
    tables?: Record<string, number>;
    skipped_batches?: { id: number; reason: string }[];
    files?: Record<string, number>;
  } | null;
}

export interface RestoreOptions {
  mode: "preserve" | "remap";
  batch_ids?: number[] | null;
  restore_files: boolean;
  include_vendors: boolean;
  allow_duplicates: boolean;
  dry_run: boolean;
}

export const dataBackupApi = {
  overview: () => apiClient.get<{ path: string; backups: BackupFile[]; state: BackupJobState }>("/api/data-backup/").then((r) => r.data),
  state: () => apiClient.get<BackupJobState>("/api/data-backup/state/").then((r) => r.data),
  create: (opts: { include_pdfs: boolean; include_vendors: boolean; batch_ids?: number[] | null }) =>
    apiClient.post<BackupJobState>("/api/data-backup/create/", opts).then((r) => r.data),
  restore: (name: string, opts: RestoreOptions) =>
    apiClient.post<BackupJobState>(`/api/data-backup/${encodeURIComponent(name)}/restore/`, opts).then((r) => r.data),
  cancel: () => apiClient.post<{ cancelled: boolean }>("/api/data-backup/cancel/").then((r) => r.data),
  remove: (name: string) => apiClient.delete(`/api/data-backup/${encodeURIComponent(name)}/`).then((r) => r.data),
  downloadUrl: (name: string) => `/api/data-backup/${encodeURIComponent(name)}/download/`,
  upload: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return apiClient.post("/api/data-backup/upload/", fd, { headers: { "Content-Type": undefined }, timeout: 0 }).then((r) => r.data);
  },
};

/** Anzeige-Hilfen für Einheitentyp und Preisbasis (Belege-Infos, Artikelsuche) */
export const UNIT_TYPE_LABELS: Record<string, string> = {
  stueck: "Stück", verpackung: "Verpackung", gewicht: "Gewicht", volumen: "Volumen", laenge: "Länge",
  flaeche: "Fläche", pauschal: "Pauschal", dienstleistung: "Dienstleistung", sonstige: "Sonstige",
};

// ─── Freie KI-Frage an Belege (mit Verlauf) ───────────────────────────────────

export interface AiQueryField { key: string; label: string; kind: "text" | "iban" | "date" | "number" }
export interface AiQueryHistoryItem { id: number; question: string; field_key: string | null; use_count: number; last_used_at: string | null }
export interface AiAnswer {
  id: number; document_id: number; question: string; field_key: string | null; answer: string | null; raw_answer: string | null;
  valid: boolean; applied: boolean; note: string | null; old_value: string | null; change_run_id: number | null; model: string | null; created_at: string | null;
}
export interface AiQuestionState {
  running: boolean; question?: string; field_key?: string | null; apply?: boolean; total?: number; processed?: number;
  found?: number; not_found?: number; applied?: number; errors?: number; cancelled?: boolean;
  results?: { document_id: number; answer: string | null; applied: boolean; note: string | null; change_run_id?: number | null }[];
}

export const aiQuestionsApi = {
  fields: () => apiClient.get<AiQueryField[]>("/api/ai-questions/fields/").then((r) => r.data),
  history: () => apiClient.get<AiQueryHistoryItem[]>("/api/ai-questions/").then((r) => r.data),
  deleteHistory: (id: number) => apiClient.delete(`/api/ai-questions/${id}/`),
  /** Lokale Modelle brauchen länger → kein Zeitlimit im Browser */
  ask: (p: { document_id: number; question: string; field_key: string | null; ai_config_id?: number | null; apply: boolean }) =>
    apiClient.post<AiAnswer>("/api/ai-questions/ask/", p, { timeout: 0 }).then((r) => r.data),
  run: (p: { document_ids: number[]; question: string; field_key: string | null; ai_config_id?: number | null; apply: boolean }) =>
    apiClient.post<AiQuestionState>("/api/ai-questions/run/", p).then((r) => r.data),
  state: () => apiClient.get<AiQuestionState>("/api/ai-questions/state/").then((r) => r.data),
  cancel: () => apiClient.post<{ cancelled: boolean }>("/api/ai-questions/cancel/").then((r) => r.data),
  answers: (documentId: number) =>
    apiClient.get<AiAnswer[]>("/api/ai-questions/answers/", { params: { document_id: documentId } }).then((r) => r.data),
};

// ─── Dynamisches Schema ───────────────────────────────────────────────────────

export type SchemaDataType = "text" | "integer" | "decimal" | "date" | "boolean" | "enum" | "quantity";

export interface NormalizerStep { name: string; params: Record<string, unknown> }

export interface SchemaField {
  id: number;
  name: string;
  display_name: string;
  data_type: SchemaDataType;
  required: boolean;
  output_format: string | null;
  description: string | null;
  position: number;
  source: "llm" | "system";
  is_locked: boolean;
  enum_values: string[] | null;
  unit_group_id: number | null;
  system_function: string | null;
  system_params: Record<string, unknown> | null;
  normalizers: NormalizerStep[];
}

export interface SchemaEntity {
  id: number;
  name: string;
  display_name: string;
  description: string | null;
  master_table: string | null;
  match_fields: string[] | null;
  fields: SchemaField[];
}

export interface SchemaRelation {
  id: number;
  name: string;
  kind: "parent_child" | "reference";
  source_entity_id: number;
  target_entity_id: number;
  description: string | null;
}

export interface SchemaVersionSummary {
  id: number;
  version_number: number;
  status: "draft" | "active" | "archived";
  comment: string | null;
  created_at: string;
  activated_at: string | null;
  entity_count: number;
  relation_count: number;
}

export interface SchemaVersionFull extends SchemaVersionSummary {
  entities: SchemaEntity[];
  relations: SchemaRelation[];
  problems: string[];
}

/** Beschreibung eines Bausteins (Normalisierer / Systemfunktion) samt JSON-Schema der Parameter */
export interface RegistryItem {
  name: string;
  label?: string;
  description?: string;
  params_schema?: { properties?: Record<string, { type?: string; title?: string; description?: string; default?: unknown; enum?: unknown[] }>; required?: string[] };
  [key: string]: unknown;
}

export type SchemaFieldInput = Partial<Omit<SchemaField, "id" | "position" | "is_locked">> & { name: string; display_name: string; data_type: SchemaDataType };

export const schemaApi = {
  normalizers: () => apiClient.get<RegistryItem[]>("/api/schema/normalizers").then((r) => r.data),
  functions: () => apiClient.get<RegistryItem[]>("/api/schema/functions").then((r) => r.data),
  preview: (value: unknown, chain: NormalizerStep[]) =>
    apiClient.post<{ ok: boolean; value: unknown; errors: string[]; issues: unknown[] }>("/api/schema/normalizers/preview", { value, chain }).then((r) => r.data),
  versions: () => apiClient.get<SchemaVersionSummary[]>("/api/schema/versions").then((r) => r.data),
  version: (id: number) => apiClient.get<SchemaVersionFull>(`/api/schema/versions/${id}`).then((r) => r.data),
  createDraft: (baseVersionId?: number, comment?: string) =>
    apiClient.post<SchemaVersionFull>("/api/schema/versions/draft", { base_version_id: baseVersionId ?? null, comment: comment ?? null }).then((r) => r.data),
  activate: (id: number) => apiClient.post<SchemaVersionFull>(`/api/schema/versions/${id}/activate`).then((r) => r.data),
  discard: (id: number) => apiClient.delete(`/api/schema/versions/${id}`),
  addEntity: (versionId: number, data: { name: string; display_name: string; description?: string | null; master_table?: string | null; match_fields?: string[] | null }) =>
    apiClient.post<SchemaVersionFull>(`/api/schema/versions/${versionId}/entities`, data).then((r) => r.data),
  deleteEntity: (id: number) => apiClient.delete<SchemaVersionFull>(`/api/schema/entities/${id}`).then((r) => r.data),
  addField: (entityId: number, data: SchemaFieldInput) =>
    apiClient.post<SchemaVersionFull>(`/api/schema/entities/${entityId}/fields`, data).then((r) => r.data),
  patchField: (id: number, data: Partial<SchemaFieldInput>) =>
    apiClient.patch<SchemaVersionFull>(`/api/schema/fields/${id}`, data).then((r) => r.data),
  deleteField: (id: number) => apiClient.delete<SchemaVersionFull>(`/api/schema/fields/${id}`).then((r) => r.data),
  addRelation: (versionId: number, data: { name: string; kind: "parent_child" | "reference"; source_entity_id: number; target_entity_id: number; description?: string | null }) =>
    apiClient.post<SchemaVersionFull>(`/api/schema/versions/${versionId}/relations`, data).then((r) => r.data),
  deleteRelation: (id: number) => apiClient.delete<SchemaVersionFull>(`/api/schema/relations/${id}`).then((r) => r.data),
};

// ─── Einheiten ────────────────────────────────────────────────────────────────

export interface UnitAliasItem { id: number; alias: string }
export interface UnitItem {
  id: number; name: string; symbol: string; factor: number; is_base: boolean; case_insensitive: boolean; aliases: UnitAliasItem[];
}
export interface UnitGroupItem { id: number; name: string; description: string | null; units: UnitItem[] }
export interface UnitReviewGroup {
  alias: string; count: number; documents: number;
  examples: { record_id: number; document_id: number; field: string }[];
}

export const unitsApi = {
  list: () => apiClient.get<UnitGroupItem[]>("/api/units").then((r) => r.data),
  createGroup: (name: string, description?: string) => apiClient.post("/api/units/groups", { name, description: description ?? null }).then((r) => r.data),
  deleteGroup: (id: number) => apiClient.delete(`/api/units/groups/${id}`),
  createUnit: (groupId: number, data: { name: string; symbol: string; factor: number; is_base?: boolean; case_insensitive?: boolean; aliases?: string[] }) =>
    apiClient.post<UnitItem>(`/api/units/groups/${groupId}/units`, data).then((r) => r.data),
  patchUnit: (id: number, data: Partial<{ name: string; symbol: string; factor: number; case_insensitive: boolean }>) =>
    apiClient.patch<UnitItem>(`/api/units/${id}`, data).then((r) => r.data),
  deleteUnit: (id: number) => apiClient.delete(`/api/units/${id}`),
  addAlias: (unitId: number, alias: string) => apiClient.post<UnitAliasItem>(`/api/units/${unitId}/aliases`, { alias }).then((r) => r.data),
  deleteAlias: (aliasId: number) => apiClient.delete(`/api/units/aliases/${aliasId}`),
  review: () => apiClient.get<UnitReviewGroup[]>("/api/units/review").then((r) => r.data),
  assign: (alias: string, unitId: number) => apiClient.post("/api/units/review/assign", { alias, unit_id: unitId }).then((r) => r.data),
  ignore: (alias: string) => apiClient.post("/api/units/review/ignore", { alias }).then((r) => r.data),
  convert: (value: number, unit: string | null, groupId?: number | null) =>
    apiClient.post<{ quantity: Record<string, unknown>; issues: unknown[] }>("/api/units/convert", { value, unit, group_id: groupId ?? null }).then((r) => r.data),
};

// ─── Export-Vorlagen ──────────────────────────────────────────────────────────

export interface ExportTemplateConfig {
  entities?: { entity: string; fields?: string[] | null }[];
  vendor?: string[];
  flat?: { root?: string; child?: string };
  [key: string]: unknown;
}
export interface ExportTemplate {
  id: number; name: string; description: string | null;
  layout: "sheet_per_entity" | "flat"; quantity_mode: "original" | "normalized";
  config: ExportTemplateConfig; created_at: string; updated_at: string;
}
export type ExportTemplateInput = Pick<ExportTemplate, "name" | "description" | "layout" | "quantity_mode" | "config">;
export interface ExportColumnsPreview { entity: string; display_name: string; columns: { header: string; kind: string; format: string | null }[] }

export const exportTemplatesApi = {
  list: () => apiClient.get<ExportTemplate[]>("/api/export-templates").then((r) => r.data),
  create: (data: ExportTemplateInput) => apiClient.post<ExportTemplate>("/api/export-templates", data).then((r) => r.data),
  update: (id: number, data: ExportTemplateInput) => apiClient.put<ExportTemplate>(`/api/export-templates/${id}`, data).then((r) => r.data),
  delete: (id: number) => apiClient.delete(`/api/export-templates/${id}`),
  columns: (id: number) => apiClient.get<ExportColumnsPreview[]>(`/api/export-templates/${id}/columns`).then((r) => r.data),
  /** Download-Adresse (Browser lädt die Datei direkt) */
  exportUrl: (id: number, p: { batch_ids?: number[]; vendor_ids?: number[]; include_hidden?: boolean }) => {
    const q = new URLSearchParams();
    p.batch_ids?.forEach((b) => q.append("batch_ids", String(b)));
    p.vendor_ids?.forEach((v) => q.append("vendor_ids", String(v)));
    if (p.include_hidden) q.set("include_hidden", "true");
    const qs = q.toString();
    return `/api/export-templates/${id}/export${qs ? `?${qs}` : ""}`;
  },
};
