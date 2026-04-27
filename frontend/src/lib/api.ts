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
  port: string;
  parallel_request: number;
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
  port?: string;
  parallel_request?: number;
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
};

// ─── Typen: Import-Batches ────────────────────────────────────────────────────

export interface ImportBatch {
  id: number;
  import_folder_path: string;
  storage_folder_path: string;
  company_name: string;
  year: number;
  comment: string | null;
  status: "pending" | "running" | "done" | "error";
  folder_sync: boolean | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
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
  analyze_after_import?: boolean;
  delete_source_files?: boolean;
  folder_sync?: boolean;
}

export interface BatchKiStats {
  total_tokens: number;
  total_duration_seconds: number;
}

export const importsApi = {
  list: (params?: { company_name?: string; year?: number }) =>
    apiClient.get<ImportBatch[]>("/api/imports/", { params }).then((r) => r.data),
  get: (id: number) =>
    apiClient.get<ImportBatchWithDocuments>(`/api/imports/${id}`).then((r) => r.data),
  create: (data: ImportBatchCreate) =>
    apiClient.post<ImportBatch>("/api/imports/", data).then((r) => r.data),
  getStatus: (id: number) =>
    apiClient.get<ImportBatch>(`/api/imports/${id}/status`).then((r) => r.data),
  kiStats: (id: number) =>
    apiClient.get<BatchKiStats>(`/api/imports/${id}/ki-stats`).then((r) => r.data),
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
  company: string | null;
  year: number | null;
  /** Integer: 0=Unbekannt, 1=Eingangsrechnung, 2=Ausgangsrechnung, … */
  document_type: number;
  comment: string | null;
  status: "pending" | "processing" | "done" | "error";
  soft_deleted: boolean;
  created_at: string;
  /** Kurzfelder aus der Extraktion */
  total_amount?: number | null;
  invoice_number?: string | null;
  supplier_name?: string | null;
  has_extraction?: boolean;
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
  tax: number | null;
  quantity: number | null;
  unit: string | null;
  discount: string | null;
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
  has_extraction?: boolean;
  supplier_name?: string;
  doc_id?: number;
  document_type_ids?: number[];
}

export interface AnalyzeRequest {
  document_ids: number[];
  ai_config_id?: number;
  system_prompt_id?: number;
}

export const documentsApi = {
  list: (filters?: DocumentFilter) =>
    apiClient.get<DocumentItem[]>("/api/documents/", { params: filters }).then((r) => r.data),
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
  enqueue: (documentIds: number[]) =>
    apiClient.post<{ enqueued: number; message: string }>("/api/documents/enqueue", { document_ids: documentIds }).then((r) => r.data),
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

export const importSettingsApi = {
  getPaths: () =>
    apiClient.get<{ import_base_path: string; storage_path: string }>("/api/settings/paths/").then((r) => r.data),
};

// ─── Typen: Systemprompts ─────────────────────────────────────────────────────

export interface SystemPrompt {
  id: number;
  name: string;
  content: string;
  /** 0 = Dokumententyp-Erkennung, 1 = Standard-Extraktion (Eingangsrechnung) */
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
    return apiClient
      .post<{ restored: Record<string, number>; message: string }>(
        "/api/settings/restore/",
        form,
        { headers: { "Content-Type": "multipart/form-data" } }
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
}

export const logsApi = {
  kiStats: () => apiClient.get<KiStats>("/api/logs/ki-stats/").then((r) => r.data),
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
  bank_accounts: VendorBankAccount[];
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

export const vendorsApi = {
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
  ai_configs: WorkerAIConfig[];
}

export const workerApi = {
  getStats: () =>
    apiClient.get<WorkerStats>("/api/logs/worker-stats").then((r) => r.data),
};
