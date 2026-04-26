# Rechnungsanalyse — CLAUDE.md

Selbst gehostetes System zur automatischen KI-Extraktion von Rechnungsdaten.
Läuft auf einem Synology NAS via Docker Compose (Container Manager GUI — kein SSH).

## Architektur

```
Frontend  (Next.js 16, React 19, TypeScript, Tailwind 4)  → Port 3100
Backend   (FastAPI, Python 3.12, SQLAlchemy 2, Alembic)   → Port 8100
Datenbank (PostgreSQL 16)                                  → intern
```

### Datenpfade (auf dem NAS-Host)

| Pfad | Inhalt |
|---|---|
| `/volume1/docker/_rechnungsanalyse/db/` | PostgreSQL-Datenbankdateien |
| `/volume1/docker/_rechnungsanalyse/storage/` | Kopierte Rechnungs-PDFs (`{Firma_Jahr}/{id}.pdf`) |
| `/volume1/docker/_rechnungsanalyse/import/` | Quell-PDFs zum Import (`IMPORT_BASE_PATH`) |
| `/volume1/docker/_rechnungsanalyse/python_env/` | Python venv (persistent) |
| `/volume1/docker/_rechnungsanalyse/node_modules/` | npm-Pakete (persistent) |

### Wichtige Konventionen

- **`redirect_slashes=False`** in `main.py` → alle Router-Routen **ohne** abschließenden `/` registrieren (`@router.get("")` statt `@router.get("/")`), sonst 404
- **Kein Docker CLI** — der Nutzer verwendet ausschließlich den Synology Container Manager (GUI)

---

## Backend (`backend/`)

### Struktur

```
app/
├── main.py                 # FastAPI-Instanz, Router-Registrierung, CORS
│                           # redirect_slashes=False!
├── config.py               # Settings via pydantic-settings (aus .env)
├── database.py             # SQLAlchemy Session-Factory
├── models/                 # SQLAlchemy-ORM-Modelle
│   ├── document.py         # + Properties: total_amount, invoice_number, supplier_name,
│   │                       #   document_type_name, ki_input_tokens, ki_output_tokens,
│   │                       #   ki_total_duration (Fallback auf doc_ki_* für Non-Eingangsrechnungen)
│   ├── document_type.py    # DocumentType (id, name) — 15 vordefinierte Typen
│   ├── import_batch.py
│   ├── ai_config.py
│   ├── image_settings.py
│   ├── invoice_extraction.py  # + supplier_id FK → suppliers
│   │                          # + ki_input_tokens, ki_output_tokens, ki_reasoning_tokens,
│   │                          #   ki_tokens_per_second, ki_time_to_first_token (nullable)
│   ├── order_position.py
│   ├── supplier.py         # Lieferanten-Stammdaten (Deduplication)
│   └── system_prompt.py    # Systemprompts für KI-Extraktion
│                           # + is_document_type_prompt: bool (Dokumententyp-Erkennung)
├── schemas/                # Pydantic-Schemas (Request/Response)
│   ├── document.py         # DocumentRead, DocumentListRead (+Extraktion-Summary,
│   │                       #   document_type_id/name), DocumentDetail (+ki_*-Felder
│   │                       #   explizit — nicht von DocumentRead geerbt!),
│   │                       #   DocumentCommentUpdate
│   ├── document_type.py    # DocumentTypeRead (id, name)
│   └── import_batch.py     # ImportBatchCreate (inkl. analyze_after_import,
│                           #   system_prompt_id, delete_source_files)
├── crud/                   # Datenbankoperationen (je Modell eine Datei)
│   ├── document.py         # get_all_filtered mit joinedload(extraction, document_type)
│   │                       # save_extraction: speichert ki_stats; Fallback ohne Stats
│   │                       #   falls Migration noch nicht angewendet
│   │                       # update_document_type(db, doc_id, type_id)
│   ├── document_type.py    # get_all(), get_by_id()
│   ├── import_batch.py
│   ├── supplier.py         # find_or_create (IBAN → VAT-ID → Name)
│   └── system_prompt.py    # + get_doc_type_prompt(db) → Prompt mit is_document_type_prompt=True
│                           # + _clear_doc_type_prompt(db) — stellt Eindeutigkeit sicher
├── routers/                # API-Endpunkte
│   ├── imports.py          # GET/POST /api/imports, DELETE löscht auch Dateien
│   │                       # GET /api/imports/{id}/export → Excel-Download
│   │                       # _import_then_analyze, _delete_source_files
│   │                       # _build_export_excel (openpyxl, 2 Sheets)
│   ├── documents.py        # GET /api/documents, POST /analyze, GET/{id}, preview, comment
│   │                       # _KI_IO_EXECUTOR, _analyze_single (zweistufig, sequenziell)
│   │                       # _db_type_only_finish: Abschluss ohne InvoiceExtraction
│   │                       # _merge_ki_stats: summiert Token-Stats aus Stufe 1+2
│   ├── ai_configs.py       # CRUD /api/ai-configs, POST set-default
│   ├── logs.py             # GET /api/logs (System-Log), GET /api/logs/ki-stats
│   ├── settings.py         # GET/PUT /api/settings/image-conversion
│   │                       # GET /api/settings/paths
│   │                       # CRUD /api/settings/system-prompts
│   │                       # GET /api/document-types (doc_types_router)
│   ├── sse.py              # GET /api/imports/{id}/progress (Server-Sent Events)
│   └── items.py            # CRUD /api/items (Platzhalter)
└── services/
    ├── import_service.py   # Import-Orchestrierung, parallel (Semaphore), kein KI
    │                       # Keine Seitenanzahl-Lesung mehr — wird bei KI-Analyse gesetzt
    ├── ai_service.py       # KI-Extraktion via OpenAI-kompatibler Vision-API
    │                       # Verschachteltes JSON-Format + Normalisierung
    │                       # extract_invoice_data ist SYNCHRON (httpx.Client)
    │                       # detect_document_type ist SYNCHRON (httpx.Client)
    │                       # DEFAULT_DOC_TYPE_SYSTEM_PROMPT — Fallback-Systemprompt
    └── pdf_service.py      # PDF → Bilder (pypdfium2), Seitenanzahl (pypdf)
alembic/
└── versions/
    ├── 0001_initial.py     # Alle Basistabellen
    ├── 0002_system_prompts.py
    ├── 0003_supplier.py    # suppliers-Tabelle + supplier_id FK auf invoice_extractions
    ├── 0009_ki_stats_on_invoice_extractions.py
    │                       # 5 ki_*-Spalten auf invoice_extractions (nullable)
    ├── 0011_document_types.py
    │                       # document_types-Tabelle (15 Typen), document_type_id FK
    │                       # auf documents, is_document_type_prompt auf system_prompts
    │                       # ACHTUNG: down_revision = "0010" (nicht "0010_ki_total_duration")
    └── 0012_doc_ki_stats.py
                            # doc_ki_input_tokens, doc_ki_output_tokens, doc_ki_total_duration
                            # auf documents (Fallback-Stats für Nicht-Eingangsrechnungen)
```

### Import-Ablauf

1. User gibt Firmenname + Jahr an (kein Ordnerpfad — wird aus `IMPORT_BASE_PATH` genommen)
2. Sicherheitscheck: Pfad muss unter `IMPORT_BASE_PATH` liegen
3. Alle `.pdf`/`.PDF` im Import-Ordner werden gefunden
4. Speicherziel: `STORAGE_PATH/{Firma}_{Jahr}/{id}.pdf`
5. Pro PDF parallel: DB-Datensatz anlegen → kopieren → Status `done` setzen
   - **Keine Seitenanzahl** beim Import — `page_count` bleibt `0` bis zur KI-Analyse
6. Fortschritt wird via SSE an das Frontend gestreamt
7. Optional nach Import: Quelldateien löschen und/oder KI-Analyse starten

### Import-Optionen (`ImportBatchCreate`)

| Feld | Typ | Beschreibung |
|---|---|---|
| `analyze_after_import` | `bool` | KI-Analyse direkt nach Import starten |
| `ai_config_id` | `int\|None` | Spezifische KI-Konfiguration (None = Standard) |
| `system_prompt_id` | `int\|None` | Spezifischer Systemprompt (None = Standard) |
| `delete_source_files` | `bool` | Original-PDFs aus Import-Ordner löschen nach erfolgreichem Kopieren |

**Task-Pfade in `imports.py`:**
- `analyze_after_import=True` → `_import_then_analyze(batch_id, import_folder, ai_config_id, system_prompt_id, delete_source_files)`
- `analyze_after_import=False, delete_source_files=True` → `_import_and_delete(batch_id, import_folder)`
- Sonst → `run_import(batch_id)`

`_delete_source_files()` löscht nur Dateien, für die ein DB-Eintrag mit `stored_filename` existiert (kein Blind-Delete).

### Import löschen

`DELETE /api/imports/{id}` löscht:
- PDF-Dateien aus `STORAGE_PATH/{Firma}_{Jahr}/`
- Leere Unterordner werden ebenfalls entfernt
- DB-Einträge (Batch → Dokumente → Extraktionen → Positionen via CASCADE)

### Dokumententypen (`models/document_type.py`, `crud/document_type.py`)

15 vordefinierte Dokumententypen werden beim ersten Start (Migration 0011) in die DB eingetragen:

| ID | Name |
|---|---|
| 1 | **Eingangsrechnung** ← löst vollständige KI-Extraktion aus |
| 2 | Ausgangsrechnung |
| 3 | Lieferschein |
| 4 | Bestellbestätigung |
| 5 | Angebot |
| 6 | Gutschrift / Storno |
| 7 | Mahnung |
| 8 | Kontoauszug |
| 9 | Vertrag |
| 10 | Lohnabrechnung |
| 11 | Steuer- / Behördendokument |
| 12 | Reisekostenabrechnung |
| 13 | Kassenbon / Quittung |
| 14 | Sonstiges kaufmännisches Dokument |
| 15 | Unbekannt |

`GET /api/document-types` — gibt alle Typen zurück (registriert als `doc_types_router` in `settings.py`, eingebunden in `main.py`).

### KI-Extraktion (`services/ai_service.py`)

- Unterstützt jede OpenAI-kompatible Vision-API (LM Studio, Ollama, OpenAI, etc.)
- Endpunkt je nach `endpoint_type`:
  - `openai` → `{api_url}/v1/chat/completions`
  - `lmstudio` → `{api_url}/api/v1/chat`
- Alle PDF-Seiten werden in **einer** Anfrage gesendet
- **Kein `"detail": "high"`** in image_url → LM-Studio-Kompatibilität
- **`"stream": False`** explizit gesetzt → verhindert channelId-Warnungen in LM Studio
- System-Prompt: aus DB (Standard-Prompt) oder explizit per `system_prompt_id`
- **Niemals `raise_for_status()`** — alle HTTP-Fehler (429/503/500/Timeout/Netzwerk)
  werden als `({}, [], "KI-Fehler: ...", {})` zurückgegeben, nie als Exception
- **`extract_invoice_data` ist SYNCHRON** (`def`, nicht `async def`) und verwendet
  `httpx.Client` (sync). Muss immer via `asyncio.to_thread()` aufgerufen werden.
  Grund: verhindert, dass JSON-Serialisierung großer Base64-Payloads und HTTP-I/O
  den asyncio Event-Loop blockieren.
- **`detect_document_type` ist SYNCHRON** — gleiche Konvention, muss via `asyncio.to_thread()` aufgerufen werden.

#### Rückgabe von `extract_invoice_data`

```python
def extract_invoice_data(
    images_b64: list[str],
    config: AIConfig,
    system_prompt_text: str | None = None,
) -> tuple[dict, list[dict], str, dict]:
    # Returns: (extracted_fields, order_positions, raw_response, ki_stats)
```

`ki_stats` enthält: `input_tokens`, `output_tokens`, `reasoning_tokens`,
`tokens_per_second`, `time_to_first_token` (alle können `None` sein).

#### Rückgabe von `detect_document_type`

```python
def detect_document_type(
    images_b64: list[str],
    config: AIConfig,
    document_types: list[dict],          # [{"id": 1, "name": "Eingangsrechnung"}, ...]
    system_prompt_text: str | None = None,
) -> tuple[int | None, str | None, str, dict]:
    # Returns: (type_id, type_name, raw_response, ki_stats)
```

Die KI antwortet mit:
```json
{"dokumententyp_id": 1, "dokumententyp_name": "Eingangsrechnung"}
```

`type_id` wird gegen die bekannten IDs validiert. Bei ungültiger oder fehlender Antwort → `(None, None, raw, {})`.

`DEFAULT_DOC_TYPE_SYSTEM_PROMPT` — interner Fallback-Prompt für die Typenerkennung (wird verwendet, falls kein Dokumententyp-Prompt in der DB gesetzt ist, aber `detect_document_type` direkt aufgerufen wird).

#### Verschachteltes KI-JSON-Format

Die KI soll Daten in diesem verschachtelten Format zurückgeben:

```json
{
  "lieferant": {
    "name": "...",
    "anschrift": { "strasse": "...", "plz": "...", "ort": "...", "land": "..." },
    "hrb_nummer": "...",
    "steuernummer": "...",
    "ust_id_nr": "...",
    "bankverbindung": { "bank_name": "...", "iban": "...", "bic": "..." }
  },
  "rechnungsdaten": {
    "rechnungsnummer": "...",
    "rechnungsdatum": "YYYY-MM-DD",
    "faelligkeit": "YYYY-MM-DD",
    "kundennummer": "..."
  },
  "positionen": [
    {
      "position_nr": 1,
      "artikelbezeichnung": "...",
      "artikelnummer_lieferant": "...",
      "menge": 1,
      "mengeneinheit": "Stück",
      "einzelpreis": 0.0,
      "gesamtpreis": 0.0,
      "waehrung": "EUR",
      "steuersatz": 19.0,
      "preisnachlass": { "betrag": null, "prozent": null, "bezeichnung": null }
    }
  ],
  "zahlungsinformationen": {
    "gesamtbetrag_netto": 0.0,
    "umsatzsteuer_zusammenfassung": [{ "steuersatz": 19.0, "nettobetrag": 0.0, "steuerbetrag": 0.0 }],
    "gesamtbetrag_brutto": 0.0,
    "waehrung": "EUR",
    "skonto": { "prozent": null, "betrag": null, "frist_tage": null },
    "zahlungsbedingungen": "..."
  }
}
```

**Auto-Detection:** Enthält das geparste JSON `lieferant`, `rechnungsdaten` oder `zahlungsinformationen` → neues Format (`_map_new_format()`). Sonst → altes flaches Format (`_clean_flat_fields()`).

**`_map_new_format()` gibt zurück:** `extracted_fields` enthält neben den DB-Spalten auch
`supplier_street`, `supplier_zip`, `supplier_city` (nur für Supplier-Lookup, keine DB-Spalten).
Diese werden in `_db_analyze_write` vor `save_extraction` herausgefiltert (`_SUPPLIER_ONLY_KEYS`).

#### Normalisierung-Hilfsfunktionen

| Funktion | Beschreibung |
|---|---|
| `_normalize_decimal_commas(obj)` | Rekursiv: `"1.234,56 €"` → `1234.56`, `"719,99"` → `719.99` — strips `€$£¥` vorher |
| `_date(val)` | `"25.03.2025"` / `"2025-03-25"` / `"03/25/2025"` → `"2025-03-25"` (ISO); unbekanntes Format → `None` (verhindert DB-Fehler) |
| `_num(val)` | String oder Zahl → `float\|None` |
| `_str(val)` | Beliebig → `str\|None` |

### KI-Analyse: Zweistufiger Ansatz (`routers/documents.py`)

**Kernprinzip:** Alle blockierenden Operationen laufen in Threads — der Event-Loop wird nie blockiert.

**Sequenzielle Verarbeitung:** Dokumente werden **nacheinander** analysiert (kein `asyncio.gather`, kein Semaphore). Grund: Lokale Modelle (LM Studio, Ollama) verarbeiten ohnehin nur eine Anfrage gleichzeitig. Parallele Verarbeitung erschöpft auf einem NAS den Thread-Pool und den Arbeitsspeicher.

#### Zweistufige Analyse (wenn Dokumententyp-Prompt konfiguriert)

| Phase | Inhalt | Ausführung |
|---|---|---|
| 1 | Alle Daten aus DB lesen (inkl. `doc_type_prompt_text`, `document_types`) | `asyncio.to_thread(_db_analyze_read)` |
| 2 | PDF → Bilder | `_run_ki_io()` (dedizierter `_KI_IO_EXECUTOR`) |
| 3a | Dokumententyp erkennen (`detect_document_type`) | `asyncio.to_thread(ai_service.detect_document_type)` |
| 3b | Typ in DB speichern (`_db_save_document_type`) | `asyncio.to_thread(...)` |
| 3c | **Falls NICHT Eingangsrechnung (ID 1):** Abschluss ohne Extraktion | `asyncio.to_thread(_db_type_only_finish)` → return |
| 3d | **Falls Eingangsrechnung:** Rechnungsextraktion | `asyncio.to_thread(ai_service.extract_invoice_data)` |
| 4 | Ergebnisse + Seitenanzahl in DB schreiben | `asyncio.to_thread(_db_analyze_write)` |

**Einstufige Analyse (kein Dokumententyp-Prompt):** Direkt Phase 2 → 3d → 4 (altes Verhalten, rückwärtskompatibel).

**`_merge_ki_stats(stats1, stats2)`** summiert `input_tokens`, `output_tokens`, `reasoning_tokens` und `total_duration` über beide KI-Aufrufe (Typenerkennung + Extraktion). Die kombinierten Stats werden in `InvoiceExtraction` gespeichert.

**`_db_type_only_finish(doc_id, type_id, type_name, page_count, batch_id, original_filename, ki_stats=None)`**
Schließt Nicht-Eingangsrechnungen ab:
- Setzt `document.status = "done"`, `document.page_count`
- Speichert KI-Stats direkt in `doc_ki_input_tokens`, `doc_ki_output_tokens`, `doc_ki_total_duration` auf `Document`

**Seitenanzahl:** `page_count = len(images_b64)` nach Phase 2 → wird in Phase 4 (oder `_db_type_only_finish`) in `Document.page_count` geschrieben.

`_set_error(doc_id, message)` — Hilfsfunktion, öffnet eigene Session nur zum Setzen des Fehlerstatus.

**Fehlerbehandlung in `save_extraction`:** Bei fehlgeschlagenem DB-Commit (z.B. Migration noch nicht angewendet) → `db.rollback()` + Retry ohne KI-Stats-Felder.

### KI-Token-Statistiken

#### Für Eingangsrechnungen (in `invoice_extractions`)

| Spalte | Typ | Beschreibung |
|---|---|---|
| `ki_input_tokens` | `int\|None` | Eingabe-Token (Summe aus Typ-Erkennung + Extraktion) |
| `ki_output_tokens` | `int\|None` | Ausgabe-Token (Summe) |
| `ki_reasoning_tokens` | `int\|None` | Reasoning-Token (nur manche Modelle) |
| `ki_tokens_per_second` | `float\|None` | Generierungsgeschwindigkeit |
| `ki_time_to_first_token` | `float\|None` | Zeit bis erstes Token (Sekunden) |

#### Für Nicht-Eingangsrechnungen (direkt in `documents`)

| Spalte | Typ | Beschreibung |
|---|---|---|
| `doc_ki_input_tokens` | `int\|None` | Eingabe-Token der Typenerkennung |
| `doc_ki_output_tokens` | `int\|None` | Ausgabe-Token der Typenerkennung |
| `doc_ki_total_duration` | `float\|None` | Gesamtdauer der Typenerkennung |

Die Properties `ki_input_tokens`, `ki_output_tokens`, `ki_total_duration` auf `Document` liefern zuerst den Wert aus `InvoiceExtraction` (falls vorhanden), andernfalls den Fallback aus `doc_ki_*`.

**Wichtig für Schemas:** `DocumentDetail` erbt von `DocumentRead` (nicht `DocumentListRead`). Die `ki_*`-Felder müssen daher **explizit** in `DocumentDetail` deklariert werden — sie werden nicht automatisch geerbt.

Aggregierte Statistiken: `GET /api/logs/ki-stats` — gibt Summen und Durchschnitte über alle Extraktionen zurück.

### Lieferanten-Deduplication (`crud/supplier.py`)

`find_or_create()` sucht in dieser Priorität:
1. IBAN (stärkster Identifier)
2. VAT-ID (USt-IdNr.)
3. Name (Fallback)

Vorhandene Felder werden nur überschrieben, wenn der neue Wert besser (nicht leer) ist.
`supplier_id` wird in `invoice_extractions` gespeichert.

### Systemprompts (`models/system_prompt.py`, `crud/system_prompt.py`)

Zusätzlich zum normalen Extraktions-Prompt gibt es einen **Dokumententyp-Prompt**:

- `is_document_type_prompt: bool` — markiert einen Prompt als Dokumententyp-Erkennung
- Nur **ein** Prompt kann gleichzeitig `is_document_type_prompt=True` haben
- `_clear_doc_type_prompt(db)` setzt alle anderen auf `False`, bevor ein neuer gesetzt wird
- `get_doc_type_prompt(db)` — gibt den aktiven Dokumententyp-Prompt zurück (oder `None`)
- Bei `_analyze_single`: `doc_type_prompt_text` aus DB gelesen → zweistufige Analyse aktiv

**Erwartetes KI-Antwortformat für Dokumententyp-Prompt:**
```json
{"dokumententyp_id": 1, "dokumententyp_name": "Eingangsrechnung"}
```

### Umgebungsvariablen (`.env`)

```
POSTGRES_USER=appuser
POSTGRES_PASSWORD=...
POSTGRES_DB=rechnungsanalyse
DATABASE_URL=postgresql://appuser:...@db:5432/rechnungsanalyse
IMPORT_BASE_PATH=/volume1/docker/_rechnungsanalyse/import
STORAGE_PATH=/volume1/docker/_rechnungsanalyse/storage
```

### Excel-Export (`routers/imports.py`)

`GET /api/imports/{id}/export` — gibt eine `.xlsx`-Datei zurück (StreamingResponse).
Lädt alle **nicht-soft-gelöschten** Dokumente des Batches via `joinedload` für
`extraction`, `extraction.supplier` und `order_positions`.

**Sheet „Rechnungen"** (26 Spalten):

| Spalten | Quelle |
|---|---|
| Beleg-Nr., Dateiname, Status, Seiten | `Document` |
| Rechnungsnr., Rechnungsdatum, Fälligkeit | `InvoiceExtraction` |
| Lieferant, Straße, PLZ, Ort | `InvoiceExtraction.supplier_name` + `Supplier.street/zip_code/city` |
| USt-IdNr., Steuernr., HRB-Nr., Kundennr. | `InvoiceExtraction` |
| Bank, IBAN, BIC | `InvoiceExtraction` |
| Gesamtbetrag (€), Rabatt (€) | `InvoiceExtraction` |
| Skonto (€), Skonto (%), Skonto Frist (Tage) | `InvoiceExtraction.cash_discount_amount` + `raw_response → zahlungsinformationen.skonto` |
| Zahlungsbedingungen, Kommentar, Importiert am | `InvoiceExtraction` / `Document` |

**Sheet „Positionen"** (12 Spalten):

| Spalten | Quelle |
|---|---|
| Beleg-Nr., Rechnungsnr., Lieferant, Pos. | `Document` / `InvoiceExtraction` |
| Artikelbezeichnung, Artikelnummer, Menge, Einheit | `OrderPosition` |
| Einzelpreis (€), Gesamtpreis (€) | `OrderPosition` |
| Steuersatz (%) | `raw_response → positionen[i].steuersatz` (Index = `position_index`) |
| Nachlass | `OrderPosition` |

**Wichtig:** Straße/PLZ/Ort sind nur befüllt wenn ein `Supplier`-Datensatz existiert
(erfordert KI-Analyse). Steuersatz und Skonto-Details nur im neuen verschachtelten
KI-JSON-Format vorhanden. `_parse_raw()` parst `raw_response` sicher (keine Exception
bei leerem oder ungültigem JSON).

**Frontend:** `<a href="/api/imports/{id}/export/" download>` auf der Import-Detailseite
(grüner Button „↓ Excel exportieren" neben „Aktualisieren", nur sichtbar wenn Import
abgeschlossen).

### Wichtige Abhängigkeiten

```
fastapi, uvicorn, sqlalchemy, alembic, psycopg2-binary
httpx          # KI-API-Aufrufe (sync httpx.Client in extract_invoice_data)
pypdfium2      # PDF → Bilder (kein Poppler nötig)
pypdf          # Seitenanzahl auslesen (nur in pdf_service, nicht mehr im Import)
Pillow         # Bildbearbeitung / Base64
sse-starlette  # Server-Sent Events
openpyxl       # Excel-Export (GET /api/imports/{id}/export)
pydantic-settings
```

---

## Frontend (`frontend/`)

### Struktur

```
src/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                        # Redirect zu /dashboard
│   ├── dashboard/page.tsx              # Übersicht aller Import-Batches
│   ├── belege/page.tsx                 # Alle Dokumente, Filter, KI-Analyse starten
│   │                                   # KI-Rohdaten-Ansicht + Infos-Ansicht (50/50)
│   │                                   # Dokumententyp-Filter (DocTypeMultiSelect)
│   │                                   # Dokumententyp-Spalte in Tabelle
│   ├── imports/
│   │   ├── new/page.tsx                # Neuen Import starten
│   │   └── [id]/page.tsx               # Import-Detail: ProgressPanel + Dokumentenliste
│   │                                   # SSE + Polling-Fallback alle 4 s
│   │                                   # Dokumentenliste lädt automatisch nach Abschluss
│   └── settings/
│       ├── ai/page.tsx                 # KI-Konfigurationen verwalten
│       ├── prompts/page.tsx            # Systemprompts verwalten
│       │                               # Checkbox: Als Dokumententyp-Prompt verwenden
│       │                               # Hilfetext: erwartetes KI-JSON + Typenliste
│       └── image/page.tsx             # Bildkonvertierungseinstellungen
├── components/
│   ├── Nav.tsx                         # Links: Dashboard, Belege, Neuer Import,
│   │                                   #   KI-Einstellungen, Systemprompts, Bildeinstellungen
│   ├── dashboard/BatchTable.tsx
│   ├── dashboard/FilterBar.tsx
│   ├── imports/ImportForm.tsx          # Firma + Jahr + Pfad-Vorschau (aus API)
│   │                                   # Optionen: Quelldateien löschen,
│   │                                   #   KI-Analyse nach Import (mit KI-Config + Prompt)
│   ├── imports/DocumentsTable.tsx
│   ├── imports/ProgressPanel.tsx       # SSE + initialTotal/initialProcessed Fallback
│   └── settings/AIConfigForm.tsx
└── lib/
    ├── api.ts      # axios-Client, alle API-Typen und -Funktionen
    └── sse.ts      # SSE-Client für Fortschritts-Updates
```

### Navigation (`Nav.tsx`)

```
/dashboard        Dashboard
/belege           Belege
/imports/new      Neuer Import
/settings/ai      KI-Einstellungen
/settings/prompts Systemprompts
/settings/image   Bildeinstellungen
```

### API-Client (`src/lib/api.ts`)

- Server-seitig: vollständige URL via `NEXT_PUBLIC_API_URL`
- Client-seitig: leere Basis → Next.js Rewrite-Proxy
- Wichtig: Axios-Calls **mit** trailing Slash (`/api/documents/`) — Next.js Rewrite
  entfernt den Slash, Backend empfängt ohne Slash → passt zu `redirect_slashes=False`

Exports:
- `itemsApi` — Platzhalter
- `aiConfigsApi` — KI-Konfigurationen CRUD
- `importsApi` — Import-Batches CRUD + `getStatus(id)` (ohne Dokumentliste)
- `documentsApi` — Dokumente, Analyse, Vorschau, Kommentar
- `imageSettingsApi` — Bildkonvertierungseinstellungen
- `systemPromptsApi` — Systemprompts CRUD
- `importSettingsApi` — Pfade abrufen (`/api/settings/paths`)
- `logsApi` — System-Logs + `kiStats()` → `GET /api/logs/ki-stats`
- `documentTypesApi` — Dokumententypen abrufen (`GET /api/document-types`)

Typen:
- `DocumentType` — `{ id: number; name: string }`
- `DocumentItem` — enthält `document_type_id?`, `document_type_name?`, `ki_input_tokens?`, `ki_output_tokens?`, `ki_total_duration?`
- `DocumentFilter` — enthält `document_type_ids?: number[]`
- `SystemPrompt` — enthält `is_document_type_prompt: boolean`
- `SystemPromptCreate` — enthält `is_document_type_prompt?: boolean`

### Belege-Seite (`belege/page.tsx`)

- Filter: Firma, Jahr, Status, Dokumententyp, Import, Betrag von/bis, Seiten von/bis
- Tabelle: Checkbox, ID, Firma, Jahr, Dateiname, Seiten, Betrag, Status, Dokumententyp, Rechnungsnr., Lieferant, PDF-Link
- Betrag/Rechnungsnr./Lieferant/Dokumententyp kommen direkt aus `DocumentItem` (Backend liefert sie mit)
- Aktionsleiste bei Auswahl: KI-Konfiguration + Systemprompt wählen → „KI-Analyse starten"
- Auto-Refresh alle 5 s solange Dokumente mit Status `processing` vorhanden

#### Filter: `DocTypeMultiSelect`

Gleiche Dropdown-Checkbox-Komponente wie `BatchMultiSelect` (Import-Filter):
- State: `selectedDocTypeIds: Set<number>`
- `loadOptions()` lädt `documentTypesApi.list()` parallel zu Batches
- `buildFilters()` fügt `document_type_ids` als Array hinzu, wenn mindestens ein Typ gewählt
- `resetFilters()` setzt `selectedDocTypeIds` zurück
- Backend: `GET /api/documents?document_type_ids=1&document_type_ids=3` — filtert mit `.in_()`

#### Aktions-Buttons pro Dokument

| Button | Bedingung | Funktion |
|---|---|---|
| **KI** (violett) | Status `done` oder `error` | Zeigt KI-Rohantwort als JSON im Modal-Overlay inkl. Token-Statistik |
| **Infos** (smaragd) | Status `done` | Wechselt in Infos-Ansicht (50/50 Split) |

**KI-Modal Token-Statistik:** Liest `viewedDoc.ki_input_tokens`, `viewedDoc.ki_output_tokens`, `viewedDoc.ki_total_duration` — also direkt aus `DocumentDetail`, nicht aus `viewedDoc.extraction`. Dies stellt sicher, dass auch Nicht-Eingangsrechnungen (ohne `InvoiceExtraction`) ihre Token-Stats anzeigen.

#### Infos-Ansicht

- Tabelle verschwindet, wird durch 50/50-Split ersetzt: **Infos links, PDF-iframe rechts**
- Navigationsleiste oben: `← Zur Liste` | `Beleg N / M` | `← Vorherige` | `Nächste →`
- Navigation scrollt automatisch zum Inhalt
- Abschnitte: Lieferant, Bankverbindung, Rechnungsdaten, Zahlungsinformationen, USt-Zusammenfassung, Positionen
- Liest verschachteltes KI-JSON aus `raw_response`; fällt auf flache Extraktionsfelder zurück

#### `fmt()` Währungsformatierung

Behandelt sowohl `number` als auch Strings wie `"719,99 €"`:
- Strips `€$£¥` und Leerzeichen
- Normalisiert `"1.234,56"` → `1234.56`
- Gibt `null` zurück für leere Werte, Original-String bei Parse-Fehler

### Import-Detailseite (`imports/[id]/page.tsx`)

- `ProgressPanel` zeigt Fortschritt via SSE
- **Polling-Fallback** alle 4 s wenn SSE-Verbindung ausfällt
- Nach Abschluss des Imports: Dokumentenliste wird automatisch geladen
- `docsLoadedRef` verhindert doppeltes Laden der Dokumentenliste
- `batchLoadedRef` verhindert Infinite-Loop in `useCallback`
- **„↓ Excel exportieren"-Button** (grün) neben „Aktualisieren" — direkter Download-Link
  auf `GET /api/imports/{id}/export/`, nur sichtbar wenn Import nicht mehr aktiv

### Logs-Seite (`logs/page.tsx`)

- System-Log-Tabelle (Import- und KI-Ereignisse)
- **KI-Stats-Panel** oben: aggregierte Token-Zahlen über alle Extraktionen
  - Anzahl KI-Anfragen, Summen und Durchschnitte für Input/Output/Reasoning-Tokens
  - Ø Tokens/Sek., Ø Time-to-First-Token

### Neuer Import (`components/imports/ImportForm.tsx`)

**Import-Optionen:**

| Option | Typ | Beschreibung |
|---|---|---|
| Quelldateien löschen | Checkbox (orange) | Original-PDFs aus Import-Ordner löschen nach erfolgreichem Kopieren |
| Dokumente an KI senden | Checkbox (blau) | KI-Analyse nach Import automatisch starten |
| ↳ KI-Konfiguration | Dropdown | Nur sichtbar wenn KI aktiv; Standard vorgewählt |
| ↳ Systemprompt | Dropdown | Nur sichtbar wenn KI aktiv; Standard-Prompt vorgewählt |

### Systemprompts-Seite (`settings/prompts/page.tsx`)

- CRUD für Systemprompts
- **Checkbox „Als Dokumententyp-Prompt verwenden"** (`is_document_type_prompt`)
  - Violet Info-Box klappt auf: zeigt erwartetes KI-Antwortformat + vollständige Typenliste (15 Typen, ID 1 fett als Eingangsrechnung mit Hinweis auf vollständige Extraktion)
  - Hinweis: Nur ein Prompt kann gleichzeitig als Dokumententyp-Prompt markiert sein
- Liste: violet Badge „Dokumententyp-Prompt" bei `is_document_type_prompt=true`

### Bekannte Fallstricke

- **Infinite render loop in `useCallback`**: Nie State-Variablen in Dependency-Array aufnehmen, die innerhalb des Callbacks gesetzt werden. Stattdessen `useRef` verwenden (z.B. `batchLoadedRef` in `/imports/[id]/page.tsx`).
- **LM Studio `channelId`-Warnung**: Entsteht durch `"detail": "high"` in image_url oder fehlendes `"stream": false`. Beides in `ai_service.py` korrekt gesetzt.
- **Dokument bleibt auf „Wird verarbeitet"**: Kann durch fehlgeschlagenen DB-Commit entstehen (z.B. Datum im falschen Format von KI). `_date()` in `ai_service.py` normalisiert alle bekannten Formate → `None` bei unbekanntem Format, verhindert Commit-Fehler.
- **Backend friert ein bei mehreren KI-Anfragen**: Entsteht durch parallele `asyncio.to_thread`-Aufrufe mit großen Payloads, die den Thread-Pool erschöpfen. Lösung: KI-Analyse ist sequenziell — `_run_analysis` verwendet eine `for`-Schleife statt `asyncio.gather`.
- **`NameError: cannot access local variable 'images_b64'`**: Entsteht wenn `del images_b64` vor `len(images_b64)` steht. Seitenanzahl immer zuerst in `page_count` sichern, dann `del`.
- **`TypeError: Unrecognized arguments` bei `InvoiceExtraction`**: `_map_new_format()` gibt `supplier_street/zip/city` zurück, die keine DB-Spalten sind. Vor `save_extraction` mit `_SUPPLIER_ONLY_KEYS` herausfiltern.
- **`ki_*`-Spalten fehlen (Migration 0009 nicht angewendet)**: `save_extraction` fängt den Commit-Fehler ab und wiederholt den Schreibvorgang ohne KI-Stats. Migration nachholen: `alembic upgrade head`.
- **`KeyError` beim Container-Neustart nach Migration**: `down_revision` in einer neuen Migration muss die `revision`-ID der Vorgänger-Migration verwenden (nicht den Dateinamen). Beispiel: `0011_document_types.py` hat `down_revision = "0010"` (nicht `"0010_ki_total_duration"`).
- **KI-Stats nicht angezeigt für Nicht-Eingangsrechnungen**: `DocumentDetail` erbt von `DocumentRead`, nicht von `DocumentListRead`. Deshalb müssen `ki_input_tokens`, `ki_output_tokens`, `ki_total_duration` **explizit** in `DocumentDetail` deklariert werden. Das KI-Modal liest `viewedDoc.ki_input_tokens` (nicht `viewedDoc.extraction?.ki_input_tokens`), damit auch Dokumente ohne `InvoiceExtraction` Stats haben.
- **Dokumententyp-Prompt: Nur einer gleichzeitig aktiv**: `_clear_doc_type_prompt(db)` muss vor dem Setzen von `is_document_type_prompt=True` aufgerufen werden. Wird in `crud/system_prompt.py` in `create()` und `update()` gehandhabt.
- **React 19 Hydration-Mismatch / Buttons reagieren nicht**: Entsteht wenn SSR-Output und initialer Client-Render abweichen. Häufigste Ursache: `useState(true)` als initialer Ladezustand. Die Seite rendert auf dem Server ohne Ladeindikator (nach dem Fix), aber der alte Client-JS-Bundle (aus `.next/`-Cache) erwartet noch `true`. React 19 bricht die Hydration komplett ab → keine Event-Handler → Buttons tot. **Regel:** Ladestate immer mit `useState(false)` initialisieren; `setLoading(true)` nur innerhalb von `load()` setzen, nicht auf Top-Level.
- **`.next/`-Cache überlebt Container-Neustart (NAS-spezifisch)**: Da `frontend:/app` als Docker-Volume gemountet ist, liegt der Turbopack-Cache unter `frontend/.next/` auf dem NAS-Host und überlebt Neustarts. Inotify funktioniert auf SMB/NFS-Mounts nicht → Turbopack erkennt keine Dateiänderungen → alte Client-Bundles werden weiterhin ausgeliefert. Lösung: `rm -rf /app/.next` am Anfang des Start-Kommandos in `docker-compose.yml` (bereits eingebaut).
- **HMR WebSocket blockiert (NAS-Hostname)**: Next.js 16 blockiert cross-origin Anfragen zu `/_next/webpack-hmr` wenn der Zugriff über einen Hostnamen erfolgt (z.B. `http://nas:3100`). Symptom im Container-Log: `⚠ Blocked cross-origin request to Next.js dev resource /_next/webpack-hmr from "nas"`. Lösung: `allowedDevOrigins: ["nas", "NAS-IP"]` in `next.config.ts` eintragen (bereits konfiguriert).
- **Frontend startet nicht nach `&&`-Kette in docker-compose**: Wenn ein Schritt in `cmd1 && cmd2 && cmd3` fehlschlägt, werden alle nachfolgenden Schritte übersprungen. Startup-Kommando verwendet daher `;` als Trenner — jeder Schritt läuft unabhängig. `npm install` Fehler blockieren nicht mehr `npm run dev`.

### Wichtige Abhängigkeiten

```
next 16.2.4, react 19, typescript 6, tailwindcss 4, axios
```

---

## Entwicklungs-Workflow

### Code-Änderungen übernehmen

```
Backend  (Python): uvicorn --reload aktiv → Dateiänderung genügt
Frontend (Next.js): next dev aktiv → Dateiänderung genügt

Container-Neustart nur nötig bei:
  - neuen Python-Paketen (requirements.txt)
  - neuen npm-Paketen (package.json)
  - Änderungen an next.config.ts (wird nicht hot-reloaded)
```

> **Hinweis NAS/Turbopack:** Inotify funktioniert nicht auf SMB/Docker-Mounts. HMR (Hot Module Replacement) ist daher nicht verfügbar — Dateiänderungen wirken erst nach einem Browser-Reload (F5), nicht automatisch. Der `.next/`-Cache wird bei jedem Container-Start automatisch gelöscht (`rm -rf /app/.next` im Startup-Kommando), damit keine veralteten Client-Bundles ausgeliefert werden.

### Frontend-Cache manuell löschen

Falls das Frontend nach einem Update seltsam reagiert (Buttons tot, „JS ✗" im Nav-Badge, Seiten frieren ein):

1. Container Manager → Frontend-Container → **Stoppen**
2. Den Ordner `\\nas\docker\_rechnungsanalyse\frontend\.next\` auf dem NAS löschen
3. Container wieder **Starten**
4. Browser-Cache leeren (`Strg+Shift+R`) und neu laden

### DB-Migration erstellen

Nur über Container Manager → Backend-Container → Terminal:
```bash
/venv/bin/alembic revision --autogenerate -m "beschreibung"
/venv/bin/alembic upgrade head
```

### Logs einsehen

Container Manager → jeweiliger Container → Protokoll

### Swagger UI

`http://NAS-IP:8100/docs`

---

## Datenmodell (Überblick)

```
ImportBatch  1──n  Document  1──1  InvoiceExtraction  n──1  Supplier
                   │          1──n  OrderPosition
                   n──1  DocumentType
AIConfig        (referenziert von ImportBatch.ai_config_id)
ImageSettings   (Singleton, globale Bildkonvertierungseinstellungen)
SystemPrompt    (Standard-Prompt + optionaler Dokumententyp-Prompt)
ProcessingSettings (Singleton, import_concurrency + ai_concurrency)
```

### Migrationen

| Datei | Inhalt |
|---|---|
| `0001_initial.py` | Alle Basistabellen (ai_configs, image_settings, import_batches, documents, invoice_extractions, order_positions) |
| `0002_system_prompts.py` | `system_prompts`-Tabelle |
| `0003_supplier.py` | `suppliers`-Tabelle + `supplier_id` FK auf `invoice_extractions` |
| `0009_ki_stats_on_invoice_extractions.py` | 5 `ki_*`-Spalten auf `invoice_extractions` (nullable) |
| `0011_document_types.py` | `document_types`-Tabelle (15 vordefinierte Typen), `document_type_id` FK auf `documents`, `is_document_type_prompt` auf `system_prompts` |
| `0012_doc_ki_stats.py` | `doc_ki_input_tokens`, `doc_ki_output_tokens`, `doc_ki_total_duration` auf `documents` (Fallback-Stats für Nicht-Eingangsrechnungen ohne InvoiceExtraction) |

### Import-Status-Flow

```
pending → running → done
                 → error
```

### Dokument-Status-Flow

```
pending → processing → done
                    → error
```
