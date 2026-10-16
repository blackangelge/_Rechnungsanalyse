# Rechnungsanalyse — CLAUDE.md

Selbst gehostetes System zur automatischen KI-Extraktion von Rechnungsdaten.
Läuft auf einem Synology NAS via Docker Compose (Container Manager GUI — kein SSH).

## Architektur

```
Frontend  (Next.js 16, React 19, TypeScript, Tailwind 4)  → Port 3100
Backend   (FastAPI, Python 3.12, SQLAlchemy 2, Alembic)   → Port 8100
Worker    (FastAPI, Python 3.12 — KI-Dispatcher)          → intern (Port 8000, kein Host-Zugriff)
Datenbank (PostgreSQL 16)                                  → intern
```

Backend und Worker sind **zwei separate Container** (Docker-Compose-Services `backend` und
`worker`), teilen sich aber dieselbe Codebasis (`backend/`-Ordner) und dieselbe Python-venv.
Kommunikation ausschließlich über die gemeinsame Postgres-DB (`workflow_tasks`, `ai_clients`)
sowie über die Status-/Steuerungs-API des Workers, die das Backend intern per HTTP abfragt
(`http://worker:8000/...`). Details siehe Abschnitt „Worker-Container" weiter unten.

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
- **KI-Konfigurationen heißen `ai_clients`** (Modell `AIClients`, Router `routers/ai_clients.py`, CRUD `crud/ai_config.py`) — nicht `ai_configs`/`AIConfig`, trotz älterer Doku-Referenzen anderswo
- **Lokale Entwicklung parallel zum NAS:** Es existiert ein lokaler Docker-Compose-Stack (Windows, Docker Desktop) unter `D:\Python\_Rechnungsanalyse` für Tests — getrennt vom NAS-Produktivbetrieb. Frontend-Dev-Server auf Windows-Bind-Mounts erkennt Dateiänderungen unzuverlässig (Turbopack-Watcher), siehe „Bekannte Fallstricke"

---

## Backend (`backend/`)

### Struktur

> **Hinweis zur Vollständigkeit:** Der folgende Baum deckt die Bereiche ab, die aktiv
> gepflegt/erweitert wurden (Import/KI-Analyse/Worker/Automatisierung). Es existieren
> daneben weitere Dateien (`models/customer.py`, `models/vendor.py`,
> `models/vendor_bank_account.py`, `routers/batch.py`, `routers/suppliers.py`,
> `routers/vendors.py`, `models/export_config.py`, `models/document_token_count.py`,
> `schemas/processing_settings.py`, `crud/item.py`, `crud/system_log.py` u.a.), die hier
> (noch) nicht im Detail dokumentiert sind — bei Arbeiten in diesen Bereichen den
> jeweiligen Code direkt prüfen, nicht auf diese Doku verlassen.

```
app/
├── main.py                 # FastAPI-Instanz, Router-Registrierung, CORS
│                           # redirect_slashes=False!
├── config.py               # Settings via pydantic-settings (aus .env) — u.a. import_base_path,
│                           # storage_path, export_base_path, worker_api_url, timezone,
│                           # worker_concurrency/worker_poll_interval/worker_lock_timeout_minutes
│                           # (Anfangswerte für automation_settings, siehe dort)
├── database.py             # SQLAlchemy Session-Factory
├── db_wait.py               # wait_for_db() — gemeinsame "warte auf Postgres"-Schleife
│                           # (Backend-Migrationen + Worker-Start)
├── models/                 # SQLAlchemy-ORM-Modelle
│   ├── document.py         # + Properties: total_amount, invoice_number, supplier_name,
│   │                       #   document_type_name, ki_input_tokens, ki_output_tokens,
│   │                       #   ki_total_duration (Fallback auf doc_ki_* für Non-Eingangsrechnungen)
│   │                       # page_count wird bereits BEIM IMPORT ermittelt (pypdf, kein
│   │                       #   Rendering) — nicht erst bei der KI-Analyse, siehe unten
│   │                       # extraction-Relationship: cascade="all, delete-orphan" (WICHTIG,
│   │                       #   siehe Bekannte Fallstricke — ohne das IntegrityError beim Löschen)
│   ├── document_type.py    # DocumentType (id, name) — 15 vordefinierte Typen
│   ├── import_batch.py     # + folder_sync/last_synced_at, auto_export/last_exported_at
│   ├── ai_clients.py       # AIClients — NICHT ai_config.py/AIConfig (siehe Konventionen oben)
│   │                       # ip_address: String(100), akzeptiert IP/Hostname ODER volle URL
│   │                       #   inkl. Protokoll (z.B. "https://openrouter.ai/api")
│   │                       # port: optional (nullable) — leer = kein :port in der gebauten URL
│   │                       # max_context_tokens: int|None — Kapazitätsgrenze in Token für die
│   │                       #   Seitenzahl-Prüfung im Worker (siehe TOKENS_PER_PAGE_ESTIMATE)
│   ├── automation_settings.py  # Singleton (id=1): folder_sync_interval_minutes,
│   │                       #   export_weekday/hour/minute, worker_concurrency,
│   │                       #   worker_poll_interval_seconds, worker_lock_timeout_minutes
│   ├── workflow_task.py    # WorkflowTask — die Worker-Queue-Tabelle (workflow_tasks)
│   │                       # TaskKind.PROCESS_DOCUMENT — payload: {document_id, system_prompt_id}
│   │                       # KEINE FK auf documents — nur JSONB-Payload (siehe Fallstricke:
│   │                       #   Cascade-Delete erreicht diese Tabelle NICHT automatisch)
│   ├── document_token_count.py  # 1:n zu Document — ein Eintrag pro KI-Analyse-Durchlauf
│   │                       #   (append-only, nie überschrieben) — input/output/reasoning-Token,
│   │                       #   time_spent_seconds
│   ├── image_settings.py
│   ├── invoice_extraction.py  # document_id FK (CASCADE, NOT NULL) → siehe cascade-Hinweis oben
│   ├── order_position.py
│   └── system_prompt.py    # Systemprompts für KI-Extraktion
│                           # + is_document_type_prompt: bool (Dokumententyp-Erkennung)
├── schemas/                # Pydantic-Schemas (Request/Response)
│   ├── ai_clients.py       # AIClientsCreate/Update/Read — port: str|None, max_context_tokens: int|None
│   ├── automation_settings.py  # inkl. worker_concurrency/worker_poll_interval_seconds/
│   │                       #   worker_lock_timeout_minutes
│   ├── import_batch.py     # ImportBatchCreate (inkl. analyze_after_import,
│   │                       #   system_prompt_id, delete_source_files, folder_sync, auto_export)
│   │                       # ImportBatchAutomationUpdate (PATCH .../automation)
│   └── document.py         # DocumentRead, DocumentListRead, DocumentDetail (ki_*-Felder
│                           #   explizit — nicht von DocumentRead geerbt!)
├── crud/                   # Datenbankoperationen (je Modell eine Datei)
│   ├── ai_config.py        # CRUD-Dateiname weicht vom Modellnamen ab (historisch) —
│   │                       #   operiert auf app.models.ai_clients.AIClients
│   ├── automation_settings.py  # get_or_create(): seeded worker_*-Felder NUR beim allerersten
│   │                       #   Anlegen der Singleton-Zeile aus app.config.settings (ENV) —
│   │                       #   danach ist ausschließlich die DB (Website) maßgeblich
│   ├── document.py         # get_all_filtered mit joinedload(extraction, document_type)
│   └── import_batch.py
├── routers/                # API-Endpunkte
│   ├── imports.py          # GET/POST /api/imports, DELETE löscht auch Dateien + Worker-Tasks
│   │                       # PATCH /{id}/automation — folder_sync/auto_export nachträglich umschalten
│   │                       # GET /{id}/queue-status, POST /{id}/requeue, DELETE /{id}/queue
│   │                       #   — Worker-Queue-Verwaltung pro Import (siehe eigener Abschnitt)
│   │                       # GET /{id}/export → Excel-Download (date_from/date_to/date_field-Filter)
│   │                       # GET /{id}/export/new → inkrementeller Export (nur neue Belege)
│   ├── documents.py        # GET /api/documents, POST /analyze, GET/{id}, preview, comment
│   │                       # _KI_IO_EXECUTOR, _analyze_single (zweistufig, sequenziell)
│   ├── ai_clients.py       # CRUD /api/ai-clients, POST toggle-active/clear-timeout
│   │                       # POST /{id}/test — KI-Konfiguration testen (siehe eigener Abschnitt)
│   ├── logs.py             # GET /api/logs/ki-stats, GET /api/logs/worker-stats
│   │                       #   (inkl. documents_processed_total — Dashboard-Kennzahl)
│   ├── tasks.py            # GET /api/tasks, POST /{id}/restart, DELETE /{id}, DELETE (bulk)
│   │                       # GET /workers, POST /pause, POST /resume (Proxy zum Worker)
│   ├── settings.py         # GET/PUT /api/settings/image-conversion, /automation, /paths
│   │                       # CRUD /api/settings/system-prompts
│   │                       # GET/POST /api/settings/backup, /restore
│   ├── export_settings.py  # GET/PUT /api/settings/export (Spaltenauswahl Excel-Export)
│   ├── sse.py              # GET /api/imports/{id}/progress (Server-Sent Events)
│   └── items.py            # CRUD /api/items (Platzhalter)
└── services/
    ├── import_service.py   # Import-Orchestrierung, parallel (Semaphore), kein KI
    │                       # page_count wird DIREKT NACH DEM KOPIEREN ermittelt (pypdf,
    │                       #   kein Rendering) — nicht mehr erst bei der KI-Analyse
    ├── ai_service.py       # KI-Extraktion via OpenAI-kompatibler Vision-API
    │                       # Verschachteltes JSON-Format + Normalisierung
    │                       # extract_invoice_data/detect_document_type sind SYNCHRON (httpx.Client)
    │                       # reasoning-Parameter: nutzt build_reasoning_param() (siehe ai_reasoning.py)
    │                       # api_url wird über app.services.ai_url.build_ai_url() gebaut
    ├── plausibility_service.py  # Plausibilitätsprüfung (reine Rechenlogik, keine DB/Netzwerk) — siehe
    │                       #   Abschnitt „Plausibilitätsprüfung"
    ├── workflow_service.py  # Workflow-Engine (reine Logik): Graph-Validierung, Bedingungen, Ablaufschleife, Vorlagen
    ├── workflow_runner.py   # Knoten-Implementierungen + analyze_with_workflow (nutzt die Helfer aus routers/documents.py)
    ├── pdf_edit_service.py  # PDF-Bearbeitung mit Versionierung (Seiten entfernen, Wiederherstellen, Auto-Kürzen)
    ├── ai_url.py            # build_ai_url(ip_address, port) — EINZIGE korrekte Stelle für die
    │                       #   KI-Endpunkt-URL-Bildung, genutzt von ai_service.py, ai_test_service.py
    │                       #   UND worker/worker.py (dort als build_server_url re-exportiert)
    │                       # Respektiert http://* / https://* in ip_address; Port optional
    ├── ai_reasoning.py      # build_reasoning_param(reasoning) — "off" → Feld weglassen,
    │                       #   sonst {"effort": "low"|"medium"|"high"} (OpenRouter/OpenAI-Format)
    │                       #   NUR für den openai-kompatiblen Pfad, NICHT für LM Studios
    │                       #   eigenen /api/v1/chat-Endpunkt (der nutzt reasoning als String)
    ├── ai_test_service.py   # run_ai_test() / build_test_request() — für POST /api/ai-clients/{id}/test
    │                       # Bewusst UNABHÄNGIG von ai_service.py (Produktions-Pipeline), damit
    │                       #   Test-Anpassungen die echte Analyse nicht beeinflussen — MUSS aber
    │                       #   ai_url.py/ai_reasoning.py mitnutzen, sonst laufen Test-Tool und
    │                       #   Produktion wieder auseinander (siehe Bekannte Fallstricke)
    │                       # REQUEST_TIMEOUT_SECONDS = 900 (= ai_service.py) — war früher 120s,
    │                       #   zu kurz für langsamere/lokale Modelle, siehe Bekannte Fallstricke
    │                       # build_test_request() läuft INNERHALB von run_ai_test()s try-Block
    │                       #   (nicht davor) — sonst kann eine Exception daraus als nackter
    │                       #   HTTP 500 (ohne JSON-"detail") beim Router durchschlagen
    └── pdf_service.py      # PDF → Bilder (pypdfium2), Seitenanzahl (pypdf, get_page_count())
                            # _PDFIUM_LOCK (threading.Lock): serialisiert JEDEN PDFium-Zugriff
                            #   global — PDFium ist nicht thread-sicher, siehe Bekannte Fallstricke.
                            #   Nachbearbeitung (Resize/Encoding) läuft bewusst AUSSERHALB der
                            #   Sperre; bitmap.to_pil().copy() ist Pflicht (sonst zeigt das
                            #   PIL-Image nach pdf.close() auf freigegebenen Speicher)
alembic/
└── versions/
    ├── 0001_initial.py     # Alle Basistabellen (aktuelle Baseline — ältere Migrationshistorie
    │                       #   (0002_system_prompts, 0003_supplier, 0009/0011/0012 ...) wurde
    │                       #   irgendwann zu dieser neuen 0001-Baseline zusammengefasst)
    ├── 0002_automation.py  # automation_settings-Tabelle, import_batches.folder_sync/auto_export,
    │                       #   documents.updated_at
    ├── 0003_ip_address_length.py   # ai_clients.ip_address: String(20) → String(100)
    ├── 0004_ai_max_context_tokens.py  # ai_clients.max_context_tokens (nullable)
    ├── 0005_ai_port_optional.py    # ai_clients.port: NOT NULL → nullable
    ├── 0006_worker_settings.py     # automation_settings.worker_concurrency/
    │                               #   worker_poll_interval_seconds/worker_lock_timeout_minutes
    ├── 0007_export_config_table.py # LEERER Platzhalter: Revision der Produktiv-DB (NAS), nie im Repo — die DB ist darauf gestempelt
    ├── 0008_token_count_ai_client.py # LEERER Platzhalter (wie oben); danach folgt 0007_plausibility (down_revision = diese Revision).
    │                               #   NICHT löschen/umbenennen: sonst „Can't locate revision" beim Upgrade der Produktiv-DB
    ├── 0007_plausibility.py        # order_positions.total_price, invoice_extractions.validation_status/
    │                               #   validation_details, documents.used_system_prompt/_doc_type_prompt/
    │                               #   _recheck_prompt
    ├── 0008_analysis_history.py    # analysis_history-Tabelle, automation_settings.analysis_history_keep (Standard 3)
    ├── 0009_pdf_versions.py        # document_versions, pdf_settings (Singleton), documents.pdf_version/analyzed_pdf_version
    └── 0010_workflows.py           # workflows, vendor.workflow_id, documents.used_workflow_*/workflow_trace
tests/                      # pytest (SQLite statt PostgreSQL, siehe tests/conftest.py — JSONB wird dort
                            #   als JSON kompiliert): `cd backend && pip install -r requirements-dev.txt && pytest`
```

### Worker-Container (`app/worker/`)

Der KI-Dispatcher läuft **nicht** im Backend-Prozess, sondern in einem eigenen
Docker-Compose-Service `worker` (siehe `docker-compose.yml`). Beide Container teilen sich
dieselbe Codebasis (`backend/`-Ordner + venv) — nur der Startbefehl unterscheidet sich.

```
app/worker/
├── worker.py            # Dispatcher/Worker/ServerPool/AIServer (Poll-Loop, asyncio.Queue,
│                        # FOR UPDATE SKIP LOCKED auf workflow_tasks)
│                        # Dispatcher.paused: bool — wenn True, werden in run() keine neuen
│                        # Tasks mehr geladen (bereits laufende Tasks werden fertig verarbeitet)
│                        # build_server_url = re-exportiertes app.services.ai_url.build_ai_url
│                        #   (NICHT mehr eigene Implementierung — siehe Bekannte Fallstricke)
│                        #
│                        # Dispatcher._load_worker_settings(): liest worker_concurrency/
│                        #   worker_poll_interval_seconds/worker_lock_timeout_minutes bei JEDEM
│                        #   Poll-Zyklus frisch aus automation_settings — Änderungen über
│                        #   Einstellungen → Automatisierung wirken ohne Container-Neustart.
│                        #   Konstruktor-Parameter (poll_interval/max_workers_cap/
│                        #   lock_timeout_minutes) sind nur die ANFANGSWERTE vor dem ersten
│                        #   erfolgreichen DB-Zyklus.
│                        #
│                        # AIServer.max_pages / can_handle_pages(page_count): Kapazitätsprüfung
│                        #   aus max_context_tokens (÷ TOKENS_PER_PAGE_ESTIMATE=4500). Worker
│                        #   wählt beim acquire_slot() nur Server, die die Seitenzahl des
│                        #   Dokuments schaffen. Passt KEIN Server (aber mind. einer ist aktiv),
│                        #   schlägt der Task SOFORT fehl (kein Timeout-Warten, kein Versuch
│                        #   verschwendet) — Fehlertext nennt Seitenzahl + max. verfügbare Seiten.
│                        #
│                        # _cleanup_stale_tasks(min_age_minutes): setzt 'in_progress'-Tasks
│                        #   zurück, deren locked_at älter als min_age_minutes ist — läuft
│                        #   PERIODISCH (jeden Poll-Zyklus, min_age=lock_timeout_minutes) UND
│                        #   einmalig beim Start mit min_age_minutes=None (= alle, garantiert
│                        #   verwaist nach Container-Neustart).
│                        #
│                        # locked_at wird NICHT in _load_tasks() gesetzt (nur status=
│                        #   'in_progress', damit dieselbe Zeile nicht doppelt geladen wird),
│                        #   sondern erst in Worker._claim_task() — wenn ein Worker den Task
│                        #   WIRKLICH aus der internen Queue holt. Tasks, die nur lange in der
│                        #   Queue warten (tiefe Warteschlange, viele Dokumente), haben also
│                        #   locked_at=NULL und werden vom periodischen Stale-Cleanup NICHT
│                        #   angefasst (NULL-Vergleich ist nie TRUE) — sonst würden sie
│                        #   fälschlich als "verwaist" zurückgesetzt und beim nächsten Zyklus
│                        #   ALS DUPLIKAT erneut geladen (Bug, siehe Bekannte Fallstricke).
│                        #
│                        # KEIN dauerhaftes "failed" mehr bei KI-Erreichbarkeitsproblemen —
│                        #   _release_for_retry_no_penalty() statt _mark_failed()/max_attempts,
│                        #   für DREI Fälle: (1) kein passender Server verfügbar (TimeoutError
│                        #   aus acquire_slot), (2) Dokument zu groß für alle konfigurierten
│                        #   KI-Server (Kapazitätsprüfung → Status 'waiting_capacity' am
│                        #   Dokument, siehe unten), (3) ConnectionError beim tatsächlichen
│                        #   KI-Aufruf (KI-Rechner nicht erreichbar). attempts wird in KEINEM
│                        #   dieser drei Fälle erhöht — der Task bleibt unbegrenzt in der
│                        #   Warteschlange, bis sich die Ursache von selbst behebt (KI wieder
│                        #   erreichbar / größere Konfiguration verfügbar). NUR inhaltliche
│                        #   Fehler (Bug beim Verarbeiten der KI-Antwort, generisches
│                        #   Exception) landen weiterhin sofort und ohne Retry auf 'failed'
│                        #   (_mark_failed) — ein dauerhaft kaputtes Dokument soll nicht
│                        #   endlos KI-Ressourcen verbrauchen. Die alten Methoden
│                        #   _check_attempts_and_fail()/_release_for_retry() (MIT Penalty)
│                        #   wurden entfernt, da sie dadurch keinen Aufrufer mehr hatten.
├── main.py              # Eigene FastAPI-Instanz für den Worker-Container (uvicorn-Entrypoint:
│                        # `uvicorn app.worker.main:app --host 0.0.0.0 --port 8000`)
│                        # lifespan: Thread-Pool-Vergrößerung, wait_for_db(), asyncpg-Pool,
│                        # AIDispatcherClient, Dispatcher + folder_sync + export_schedule als
│                        # Hintergrund-Tasks (je eigener stop_event für sauberes Beenden)
│                        # KEINE Alembic-Migrationen — bleibt Aufgabe des Backend-Containers
├── folder_sync.py       # Eigener Loop (unabhängig vom Dispatcher): prüft Import-Batches mit
│                        #   folder_sync=True im konfigurierten Intervall (automation_settings.
│                        #   folder_sync_interval_minutes) auf neue PDFs, importiert + reiht sie
│                        #   zur KI-Analyse ein. Pollt intern alle 30s und vergleicht verstrichene
│                        #   Zeit gegen das AKTUELL konfigurierte Intervall — Intervalländerungen
│                        #   greifen so innerhalb von ~30s statt erst nach dem alten,
│                        #   möglicherweise viel längeren Intervall (siehe Fallstricke).
├── export_schedule.py   # Eigener Loop: prüft alle 15 Min., ob der konfigurierte Wochentermin
│                        #   (automation_settings.export_weekday/hour/minute) erreicht ist, und
│                        #   schreibt für Batches mit auto_export=True eine inkrementelle
│                        #   Excel-Datei nach EXPORT_BASE_PATH/{Firma}_{Jahr}/. Teilt sich
│                        #   last_exported_at mit dem manuellen "/export/new"-Endpunkt.
│                        #   Zeitstempel (Dateiname + Wochentermin-Vergleich) nutzen
│                        #   settings.timezone (Europe/Berlin), NICHT Container-UTC.
└── routers/
    └── status.py        # GET /health, GET /status (worker_count, queue_size, servers[],
                         # paused), POST /pause, POST /resume — intern vom Backend abgefragt
```

`app/db_wait.py::wait_for_db(database_url)` — gemeinsamer Helfer für die "Warte auf
PostgreSQL"-Schleife, genutzt von `main.py::_run_migrations` (Backend) und `worker/main.py`
(Worker), damit beide Container dieselbe Logik verwenden.

**Backend als Proxy:** Das Frontend fragt weiterhin nur das Backend ab
(`GET /api/logs/worker-stats`, `GET /api/tasks/workers`, `POST /api/tasks/pause`,
`POST /api/tasks/resume`). Das Backend ruft dafür intern per `httpx` den Worker unter
`settings.worker_api_url` (Default `http://worker:8000`, Docker-interner Service-Name) auf.
Ist der Worker-Container gerade nicht erreichbar (Neustart, Deploy), liefern diese Endpunkte
einen DB-Fallback (`worker_count: 0`, Queue-Länge weiterhin aus `workflow_tasks`) statt eines
Fehlers, plus `worker_online: false` zur Unterscheidung im Frontend.

**`.ready`-Marker (docker-compose.yml):** Backend und Worker nutzen dieselbe gemountete venv
(`PYTHON_ENV_PATH`). Damit nicht beide Container beim allerersten Start gleichzeitig
`python -m venv` / `pip install` gegen denselben Ordner ausführen (Race Condition), erstellt
das Backend nach erfolgreichem `pip install` die Datei `/venv/.ready`. Der Worker-Container
wartet in seinem Startbefehl auf diese Datei, bevor er `uvicorn` startet, und installiert
selbst keine Pakete.

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

`_import_then_analyze` ruft **NICHT** mehr `_run_analysis` direkt auf.
Stattdessen werden die importierten Dokumente via `_db_enqueue_for_analysis()` in die **Worker-Queue** eingereiht.
Die Worker übernehmen die KI-Analyse. Das verhindert Race-Conditions zwischen Import-Pfad und Worker.

`_delete_source_files()` löscht nur Dateien, für die ein DB-Eintrag mit `stored_filename` existiert (kein Blind-Delete).
Ist der Import-Unterordner danach leer, wird er ebenfalls entfernt (`_sync_delete_source_files`) —
außer es handelt sich um `IMPORT_BASE_PATH` selbst (kein Unterordner angegeben), das bleibt immer erhalten.

### Import löschen

`DELETE /api/imports/{id}` löscht:
- PDF-Dateien aus `STORAGE_PATH/{Firma}_{Jahr}/`
- Leere Unterordner werden ebenfalls entfernt
- **Alle Worker-Tasks** dieses Batches aus `workflow_tasks` (pending/in_progress/completed/failed,
  unabhängig vom Status) — nötig, weil `workflow_tasks` `document_id` nur als JSONB-Payload
  referenziert (keine FK), die CASCADE über Documents erreicht diese Tabelle deshalb NICHT
  automatisch. Ohne diesen expliziten Schritt blieben verwaiste Tasks in der Queue zurück.
- DB-Einträge (Batch → Dokumente → Extraktionen → Positionen via CASCADE)

### Worker-Queue pro Import (`routers/imports.py`)

Drei Endpunkte zur gezielten Steuerung der KI-Analyse-Queue für einen einzelnen Import-Batch
(Frontend: Panel auf der Import-Detailseite, nur sichtbar wenn `queueStatus` geladen ist):

| Endpunkt | Zweck |
|---|---|
| `GET /{batch_id}/queue-status` | Zählt Tasks dieses Batches nach Status (pending/in_progress/completed/failed) — JOIN über `(payload->>'document_id')::int = documents.id` |
| `POST /{batch_id}/requeue` | Reiht ALLE Dokumente des Batches erneut zur KI-Analyse ein (auch bereits fertige/fehlgeschlagene) — nutzt dieselbe Dedup-Logik wie `_db_enqueue_for_analysis` (überspringt Dokumente mit bereits laufendem/wartendem Task) |
| `DELETE /{batch_id}/queue` | Entfernt **pending UND in_progress**-Tasks dieses Batches aus der Queue |

**Wichtig zu `DELETE .../queue`:** Der Dispatcher markiert Tasks bereits als `in_progress`,
sobald er sie aus der DB in seine interne `asyncio.Queue` übernimmt (`_load_tasks`) —
unabhängig davon, ob gerade tatsächlich ein KI-Server verfügbar ist. Tasks bleiben deshalb oft
nur Sekundenbruchteile im Status `pending`. Ein Filter nur auf `pending` würde "alle entfernen"
in der Praxis kaum je etwas löschen — deshalb werden beide Status entfernt. Läuft für einen Task
bereits ein echter HTTP-Request an die KI, wird dieser NICHT abgebrochen (kein sauberer
Cancel-Mechanismus); das Ergebnis wird dann noch ins Dokument geschrieben, der Task-Datensatz
selbst ist aber bereits gelöscht.

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

- Unterstützt jede OpenAI-kompatible Vision-API (LM Studio, Ollama, OpenAI, OpenRouter, etc.)
- Endpunkt je nach `endpoint_type`:
  - `openai` → `{api_url}/v1/chat/completions`
  - `lmstudio` → `{api_url}/api/v1/chat`
- `api_url` wird über `app.services.ai_url.build_ai_url(ip_address, port)` gebaut — **einzige**
  korrekte Implementierung, auch von `worker/worker.py` (als `build_server_url`) und
  `ai_test_service.py` genutzt. `ip_address` darf ein reiner Hostname/IP ODER eine vollständige
  URL inkl. Protokoll sein (z.B. `https://openrouter.ai/api`); `port` ist optional (leer = kein
  `:port` in der URL). Für reine HTTPS-Cloud-Anbieter MUSS `https://` explizit in `ip_address`
  stehen, sonst wird fälschlich Klartext-HTTP verwendet (siehe Bekannte Fallstricke).
- Alle PDF-Seiten werden in **einer** Anfrage gesendet
- **Kein `"detail": "high"`** in image_url → LM-Studio-Kompatibilität
- **`"stream": False`** explizit gesetzt → verhindert channelId-Warnungen in LM Studio
- **`reasoning`-Parameter:** über `app.services.ai_reasoning.build_reasoning_param(reasoning)` —
  bei `"off"` wird das Feld im Request-Body komplett WEGGELASSEN; sonst als Objekt
  `{"effort": "low"|"medium"|"high"}` gesendet (OpenRouter/OpenAI-Format). NUR für den
  openai-kompatiblen Pfad — LM Studios eigener `/api/v1/chat`-Endpunkt bekommt weiterhin einen
  reinen String (sein eigenes, anderes Payload-Format). Siehe Bekannte Fallstricke.
- System-Prompt: aus DB (Standard-Prompt) oder explizit per `system_prompt_id`
- **Niemals `raise_for_status()`** — alle HTTP-Fehler (429/503/500/Timeout/Netzwerk)
  werden als `({}, [], "KI-Fehler: ...", {})` zurückgegeben, nie als Exception
- **`extract_invoice_data` ist SYNCHRON** (`def`, nicht `async def`) und verwendet
  `httpx.Client` (sync). Muss immer via `asyncio.to_thread()` aufgerufen werden.
  Grund: verhindert, dass JSON-Serialisierung großer Base64-Payloads und HTTP-I/O
  den asyncio Event-Loop blockieren.
- **`detect_document_type` ist SYNCHRON** — gleiche Konvention, muss via `asyncio.to_thread()` aufgerufen werden.

### KI-Konfigurationen (`ai_clients`, `routers/ai_clients.py`)

- **`ip_address`** (String(100)): IP/Hostname ODER vollständige URL mit Protokoll — siehe oben.
- **`port`** (nullable): optional, leer = kein Port in der URL. Formular (`AIConfigForm.tsx`)
  zeigt bei bestehenden Configs den echten (ggf. leeren) Wert; bei neuen Configs "1234" als
  Vorschlag (LM-Studio-Standardport), aber nicht erzwungen.
- **`max_context_tokens`** (nullable int): Kontext-Kapazität dieser KI in Token. `None` =
  unbeschränkt (Standard, rückwärtskompatibel). Wird mit `TOKENS_PER_PAGE_ESTIMATE` (4500,
  in `worker/worker.py`) verrechnet, um `AIServer.max_pages` zu bestimmen — der Worker schickt
  Dokumente nur an KIs, deren Kapazität für die (bereits beim Import ermittelte) Seitenzahl
  ausreicht (siehe `AIServer.can_handle_pages()` oben unter Worker-Container).
- **`POST /{id}/test`** — KI-Konfiguration testen, OHNE sie zu aktivieren. Nimmt ein
  hochgeladenes Test-PDF per multipart/form-data + optionale `system_prompt_id` entgegen,
  rendert es zu Bildern (`pdf_service.pdf_to_base64_images`, Einstellungen aus
  `image_settings`), baut die Anfrage über `ai_test_service.run_ai_test()` (nutzt `ai_url.py` +
  `ai_reasoning.py` — MUSS mit der Produktions-Pipeline synchron bleiben, siehe Fallstricke) und
  gibt die **vollständige** gesendete Anfrage (Bild-Base64 in der Response auf
  `<Bilddaten, N Zeichen> <erste 40 Zeichen>…` gekürzt, alles andere vollständig) sowie die
  **vollständige** Antwort (Status, Header, Body, Dauer) zurück. Schreibt NICHTS in die
  Datenbank — kein Dokument, kein Import, keine Statusänderung.
  Frontend: `/settings/ai-test` (eigener Menüpunkt "KI testen" unter Einstellungen).

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

Aggregierte Statistiken: `GET /api/logs/ki-stats` — gibt Summen und Durchschnitte über alle Extraktionen zurück, plus zwei rollierende Durchsatz-Kennzahlen (bei jedem Aufruf neu berechnet, kein Cache):

| Feld | Bedeutung | Quelle |
|---|---|---|
| `processed_last_60min` | Belege mit `status='done'`, deren letzter Status-Übergang ≤ 60 Min. her ist | `documents.updated_at` |
| `analyzed_last_24h` | Belege mit `status IN ('done','error')`, deren letzter Status-Übergang ≤ 24 Std. her ist (`waiting_capacity` zählt NICHT — noch nicht abgeschlossen) | `documents.updated_at` |

Frontend: zwei zusätzliche `StatCard`-Kacheln im Analyse-Statistiken-Bereich von `/logs` ("Verarbeitet (letzte 60 Min.)", "Analysiert (letzte 24 Std.)").

### Plausibilitätsprüfung (`services/plausibility_service.py`)

Läuft in `_analyze_single_inner` als **Phase 3c** nach der Extraktion (`_plausibility_pass` in `documents.py`),
nur bei Eingangsrechnungen mit verwertbarer KI-Antwort — **vor** `_db_analyze_write`. Dreistufig:

1. **Regeln + automatische Korrektur** (`validate_invoice`, ohne KI) — alle Werte werden gegeneinander durchgerechnet:
   - Position: Menge × Einzelpreis ≙ Positionspreis (Positionsrabatt und abweichende Preiseinheit wie „Preis je t bei Menge in kg"
     werden erkannt und nur als `info` gemeldet)
   - Summe: Σ Positionspreise ≙ Netto, **oder** Brutto − Steuer, **oder** Brutto (Positionen brutto, z.B. Kassenbon); ein
     ausgewiesener Gesamtrabatt wird berücksichtigt
   - Netto ↔ Σ Positionen; Steuerbetrag ≙ Netto × Satz je Zeile der USt-Zusammenfassung; je Steuersatz: Σ Positionen mit diesem Satz
     ≙ Nettobetrag der Zusammenfassung (in beide Richtungen); Netto + Steuer ≙ Brutto
   - Passt die Summe nicht, wird nach der **kleinsten eindeutigen** Korrektur gesucht (1, dann 2 Positionen, Faktoren aus
     Einheiten-Verhältnissen t/kg/g, l/ml/hl + 10/100/1000 und Kehrwerte). Mehrdeutig → es wird **nicht geraten**. Korrigiert wird
     `total_price`; Originalwert steht in `validation_details.corrections`.
   - **Brutto wird korrigiert** (`total_amount_brutto`), wenn Positionen ≙ Netto und Steuer ≙ Netto × Satz einander bestätigen, das
     Brutto aber nicht zu Netto + Steuer passt; **fehlt** das Brutto, wird es berechnet. Netto/Steuer werden nie überschrieben.
   - **Mengen- vs. Preiseinheit** (`_check_unit_vs_price_unit`): geht Menge × Einzelpreis = Positionspreis auf, obwohl `preiseinheit` (nur `raw_response`) eine andere Einheit
     derselben Dimension nennt als `unit` (z.B. „25,88 kg“ bei „421,10 je t“ — die KI hatte 25.880 kg zu 25,88 t umgerechnet), steht die Menge zahlenmäßig in der Preiseinheit →
     `unit` wird korrigiert (`corrections: {position, field: "unit", from, to}`; `update_validation` schreibt `order_positions.unit`), ohne Auto-Korrektur Warnung `unit_mismatch`.
   - **Gewicht in Tonnen (`_normalize_mass_to_tonnen`→`_normalize_mass_to_tonnes`)**: am Ende von `validate_invoice` (nur bei `auto_correct`) werden Gewichtspositionen **in der Datenbank** auf t umgerechnet — **nur solche, die je Tonne bepreist sind** (`preiseinheit` t, oder Menge schon in t; Schreibweisen Tonnen/TO/to/tn → „t“). Preise je kg/g bleiben unverändert (Kleinmengen), im Workflow-Knoten per `mode: "all"` erzwingbar:
     Menge in t, Einheit „t“, Einzelpreis € je t (aus `preiseinheit`, sonst Annahme Preis je Mengeneinheit bzw. schon je t — es gilt der Kandidat, bei dem Menge × Preis zum Positionspreis passt, sonst bleibt die
     Position unverändert). Excel-Export und Auswertungen lesen damit direkt Tonnen. Originalwerte: `validation_details.normalizations` + `raw_response`. Zählt nicht als Korrektur (kein Statuswechsel).
     `crud.document.update_validation` schreibt `{field: "normalize"}`-Einträge; Altbestand: `validation_backfill` im Modus `normalize_units` (Einstellungen → Automatisierung, „Einheiten auf Tonnen vereinheitlichen“) — alle Belege, Prüfstatus bleiben unberührt.
     Basiseinheit der Artikelsuche (`unit_conversion.BASE_UNITS`): Gewicht = **t**.
   - `ai_service.with_unit_rules` hängt an jeden Extraktionsprompt Zusatzregeln (Menge/Einheit wie gedruckt, deutsche Zahlen, `preiseinheit` je Position), sofern der Prompt
     `preiseinheit` nicht schon behandelt. Fehlt `preiseinheit`, kann der Widerspruch nicht erkannt werden.
   - `validation_details.position_calcs` = **Einzelrechnung je Artikel** (`_build_position_calcs`): Schritte „Menge × Einzelpreis = …“, Nachlass, Einheiten-Faktor, Vergleich mit dem Positionspreis,
     Korrekturen/Vereinheitlichungen und der gespeicherte Stand (✓/✕/nicht prüfbar); Ansicht „Einzelrechnung je Artikel“ im Prüfpanel der Infos-Ansicht.
   - `validation_details.calc` = Durchrechnung als Zeilen `{label, expected, actual, status, note}` (Tabelle im Prüfpanel).
2. **Zweite KI-Abfrage** (`ai_service.recheck_invoice_data`): nur wenn Stufe 1 `needs_review` ergibt. Systemprompt = normaler
   Extraktionsprompt + Prüfhinweis + gefundene Auffälligkeiten + erste Auslesung. Ist die neue Auslesung plausibel → `ai_corrected`
   (ersetzt die erste), sonst bleibt das Original.
3. **Markierung** `needs_review` („Prüfen!") → manuelle Prüfung; Nutzer bestätigt mit `manual_ok`.

**Keine Bestätigung für automatische Korrekturen:** `corrected` und `ai_corrected` gelten als **erledigt** (grün, kein Button); die
Änderungen bleiben im Prüfpanel nachlesbar. Nur `needs_review` verlangt eine Entscheidung (bei zehntausenden Belegen ist eine
Einzelbestätigung nicht leistbar). Neue Statuswerte, die Nutzerhandlung verlangen, sparsam einführen.

`validation_status`: `ok` · `corrected` · `ai_corrected` · `needs_review` · `manual_ok` · `unchecked`; gespeichert in
`invoice_extractions.validation_status` (+ `validation_details` als JSON-Text). Die KI darf pro Position optional `preiseinheit` liefern
(steht nur im `raw_response`, **keine** DB-Spalte — daher nicht in `_map_new_format` mappen, sonst `TypeError` bei `OrderPosition(**pos)`).

**Datenmodell:** Der Positions-Gesamtpreis (`order_positions.total_price`) wurde früher nicht gespeichert (nur im `raw_response`) — Migration
0007 ergänzt ihn; `_map_new_format` füllt jetzt auch `total_amount_netto`/`total_tax_value` (Spalten gab es, wurden aber nie befüllt).

Endpunkte: `POST /api/documents/{id}/validate` (Regeln erneut auf den gespeicherten Daten, frühere Korrekturen bleiben dokumentiert),
`POST /api/documents/{id}/validation/confirm`, Filter `GET /api/documents?validation_status=needs_review` (mehrere möglich).

**Gespeicherte Prompts:** Zu Beginn jeder Analyse (`_db_save_used_prompts`) wird der Wortlaut des verwendeten Extraktions- und
Dokumententyp-Prompts in `documents.used_system_prompt` / `used_doc_type_prompt` gespeichert, der Nachprüfungs-Prompt in
`used_recheck_prompt`. Sichtbar im KI-Modal der Belege-Seite (`UsedPrompts`).

**Migration 0007 ist Pflicht** (läuft beim Backend-Start automatisch): ohne die neuen Spalten schlägt jede Dokument-Abfrage fehl.

Frontend: Spalte „Prüfung" (`ValidationBadge`), Filter „Plausibilität", `ValidationPanel` oben in der Infos-Ansicht (Korrekturen,
Auffälligkeiten, Durchrechnung, Ergebnis der zweiten KI-Abfrage, Buttons „Prüfung erneut ausführen" / „Als geprüft bestätigen" nur bei
„Prüfen!"), Positionstabelle mit Gesamtpreis (korrigierte Werte grün).

### Nachrüstung der Prüfung für den Altbestand (`services/validation_backfill.py`, `routers/validation_backfill.py`)

Belege aus der Zeit vor der Plausibilitätsprüfung haben keinen `validation_status` und keinen `order_positions.total_price`. Die Nachrüstung holt beides **ohne KI**
nach (125.000 Belege à 300/h KI-Analyse wären > 2 Wochen; die Nachrüstung schafft ≈ 600 Belege/s):

- Fehlender Positions-Gesamtpreis ← `raw_response → positionen[i].gesamtpreis` (i = `position_index`); fehlendes Netto/Steuerbetrag ← `zahlungsinformationen`
  (nur `NULL`-Felder werden ergänzt). Danach `validate_invoice` (inkl. automatischer Korrektur eindeutiger Fehler, abschaltbar) und Speichern über
  `crud.document.update_validation(..., commit=False)`; `validation_details.backfill = true`, `method = "rules"`. **Keine zweite KI-Abfrage** — „Prüfen!"-Belege gezielt neu analysieren.
- Es werden nur Belege mit Extraktion, Status `done`, nicht gelöscht und **ohne Prüfstatus** bearbeitet; KI-Fehlerantworten/ungültiges JSON bleiben unberührt (`skipped_no_data`).
  Ein unterbrochener oder abgebrochener Lauf wird deshalb einfach neu gestartet.
- Hintergrund-Thread in Blöcken à 200 (Keyset über die Extraktions-ID), Fortschritt im Speicher (ein Lauf gleichzeitig, sonst HTTP 409), Commit je Block,
  ein defekter Beleg stoppt den Lauf nicht (Fehlerzähler + erste 20 Meldungen). `dry_run` rechnet alles, verwirft aber die Änderungen.
- Endpunkte: `GET /api/validation-backfill` (Stand + Anzahl offener Belege), `POST` `{dry_run, auto_correct, batch_ids?}`, `POST /cancel`.
- Frontend: Karte „Prüfung für den Altbestand nachrüsten" in Einstellungen → Automatisierung (Vorschau, Start, Fortschrittsbalken, Ergebnis je Status).

### Analyse-Historie (`crud/analysis_history.py`, `routers/document_history.py`)

Wird ein Beleg erneut analysiert, ersetzt `save_extraction` Extraktion und Positionen (frühere Fehlanalysen haben so schon gute Daten
überschrieben). Damit nichts verloren geht, hebt die Historie den **bisherigen Stand** vorher auf.

- **Zeitpunkt:** `crud.document.save_used_prompts` (läuft zu Beginn jeder Analyse — Worker und Backend) ruft
  `analysis_history.archive_current` auf, *bevor* die `used_*`-Felder überschrieben werden, damit der Snapshot den Prompt der **alten**
  Analyse enthält. Nicht in `save_extraction` einbauen (dort sind die Prompts schon ersetzt).
- **Inhalt** (`analysis_history.snapshot`, JSON): `extraction` (alle Spalten inkl. `raw_response`, `validation_*`), `positions`,
  `meta` (verwendete Prompts, Dokumententyp, Seitenzahl; weitere Felder über `register_meta_fields`). Kurzfelder (Datum, Prüfstatus,
  Rechnungsnr., Lieferant, Brutto, Positionszahl) liegen als Spalten daneben; die Liste lädt das Snapshot-JSON nie.
- **Nicht aufgehoben** wird: kein Stand vorhanden, Historie aus (`analysis_history_keep = 0`), KI-Fehlerantwort/leere `raw_response`,
  und ein Stand, dessen Prüfsumme (`fingerprint`, ohne KI-Laufzeitstatistik) dem zuletzt aufgehobenen gleicht.
- **Begrenzung:** `automation_settings.analysis_history_keep` (Standard 3, 0–20; Einstellungen → Automatisierung). Jeder Stand ≈ 10 KB.
- **Wiederherstellen** (`restore`): eine Transaktion — aktuellen Stand aufheben, Extraktion/Positionen/Prompts aus dem Snapshot setzen,
  wiederhergestellten Eintrag aus der Historie entfernen; Status `done`; gesperrt bei `processing` (HTTP 409).
- **Robust:** `archive_current` fängt jede Ausnahme ab (z.B. Migration 0008 fehlt) — eine Analyse bricht nie wegen der Historie ab.
- Endpunkte (`/api/documents/{id}/analysis-history`): `GET` (Liste), `GET /{hid}`, `POST /{hid}/restore`, `DELETE /{hid}`.
- Frontend: `components/belege/AnalysisHistoryPanel.tsx` in der Infos-Ansicht (Liste, Feld-für-Feld-Vergleich, Wiederherstellen, Löschen).
- Hinweis: `DocumentTokenCount` ist davon unabhängig (append-only je KI-Durchlauf) und nicht Teil des Snapshots.

### PDF-Bearbeitung & Versionen (`services/pdf_edit_service.py`, `routers/document_pdf.py`)

Seiten einer PDF können entfernt werden — **manuell** (Modal „Seiten" auf der Belege-Seite mit Vorschaubildern) und **automatisch** (nur die
ersten N Seiten behalten). Jede Änderung erzeugt eine **neue, unveränderliche Version**; das Original bleibt immer erhalten.

- Dateien: `{batch.storage_folder_path}/versions/{doc_id}/v{n}.pdf`. Version 1 (Original) wird beim **ersten Bearbeiten** angelegt (vorher
  gibt `GET /versions` ein virtuelles Original mit `id: null` zurück).
- Die **aktuelle** Version liegt zusätzlich unverändert als `{id}.pdf` — Vorschau, KI-Analyse, Worker und Export brauchen keine Anpassung.
  `documents.pdf_version` = aktuelle Version, `documents.page_count` wird mitgeführt.
- **Wiederherstellen** legt eine NEUE Version an (Kopie der alten) — der Verlauf wird nie überschrieben.
- Schreiben ist atomar (tmp-Datei → `os.replace`); schlägt der DB-Commit fehl, wird `{id}.pdf` auf die Elternversion zurückgesetzt.
  Bearbeiten ist bei `status == "processing"` gesperrt (HTTP 409). Import löschen entfernt auch `versions/{id}/` (`delete_version_files`).
- Eine bestehende KI-Extraktion wird durch eine PDF-Änderung **nicht** gelöscht. `documents.analyzed_pdf_version` (gesetzt in `_db_prepare_pdf`
  vor jeder Analyse) zeigt, ob die PDF seitdem geändert wurde → `v2` (gelb) in der Belege-Liste und Hinweis „bitte neu analysieren".
- Vorschaubilder (`pdf_service.render_page_thumbnail`) laufen wie das übrige Rendering unter `_PDFIUM_LOCK` (PDFium ist nicht thread-sicher).

**Automatisches Kürzen:** Einstellungen → Automatisierung → „Lange PDFs automatisch kürzen" (`pdf_settings`, Singleton: `auto_trim_enabled`,
`auto_trim_max_pages`). Es greift an **zwei Stellen**: (1) im Worker **vor der Kapazitätsprüfung** (`worker.prepare_pdf_for_task` in
`Worker._process`) — sonst bliebe eine 100-seitige PDF als „zu groß für alle KI-Server" (`waiting_capacity`) hängen, obwohl sie gleich auf
3 Seiten gekürzt würde; (2) in `_analyze_single_inner` als Phase 1b (`_db_prepare_pdf`, idempotent). Fehler dabei brechen die Analyse nie ab.
Für die Auswahl in der Belege-Liste: `POST /api/documents/auto-trim`.

Endpunkte (`/api/documents`): `GET /{id}/versions`, `GET /{id}/versions/{n}/preview`, `GET /{id}/pages/{page}/thumbnail` (PNG, `?version=`,
1-basiert), `POST /{id}/pages/remove` (`remove_pages` ODER `keep_pages`, optional `note`), `POST /{id}/versions/{n}/restore`,
`POST /auto-trim`; `GET|PUT /api/settings/pdf` (auch im Einstellungs-Backup).

### Workflows (`services/workflow_service.py`, `services/workflow_runner.py`, `routers/workflows.py`)

Ein **Workflow** legt als Graph fest, welche Schritte die KI-Analyse eines Belegs durchläuft und welche Systemprompts dabei gelten (Editor wie
bei n8n, siehe Frontend). Er ist optional: **ohne Standard-Workflow läuft der klassische Ablauf** (`_analyze_single_inner`, Phasen 1–4).

**Auswahl** (`_db_resolve_workflow` in `documents.py`, pro Dokument, in `_analyze_single_inner` ganz am Anfang): 1. explizit gewählter
Workflow (`workflow_id` im Task-Payload bzw. in `EnqueueRequest`/`AnalyzeRequest`) → 2. Workflow des Lieferanten (`vendor.workflow_id`; der
Lieferant wird über den **extrahierten Namen** `invoice_extractions.vendor_id` — ohne Groß/Klein — in der Tabelle `vendor` gesucht, greift also nur,
wenn der Beleg schon einmal analysiert wurde, z.B. bei erneuter Analyse; es gibt in diesem Projekt keine FK von der Extraktion zum Lieferanten) →
3. Standard-Workflow (`workflows.is_default`, höchstens einer) → 4. klassischer Ablauf.

**Worker-Integration:** Der Task-Payload (`workflow_tasks.payload`) transportiert nur die ID (`workflow_id`, optional) — `AIDispatcherClient._handle_process_document`
reicht sie an `_analyze_single(doc_id, server.id, system_prompt_id, workflow_id)` weiter. Der Workflow-Lauf teilt die KI-Konfiguration (`server.id`) des
Workers; ein Knoten kann eine andere KI-Konfiguration erzwingen (`ai_config_id`). **KI-Verbindungsfehler** (Timeout/429/Netz) in einem Knoten brechen den Lauf mit
`ai_unavailable` ab (Worker reiht neu ein, Beleg wird **nicht** auf `error` gesetzt) — wie im klassischen Ablauf.

**Kürzen vor der Kapazitätsprüfung:** `worker.prepare_pdf_for_task` → `documents._db_prepare_for_capacity`: im Workflow-Modus zählen nur die **führenden**
„PDF kürzen"-Knoten (`workflow_service.leading_trim_pages`, direkt hinter Start), sonst die globale Auto-Kürzen-Einstellung. Die globale Einstellung gilt im Workflow-Modus nicht.

**Graph** (JSON in `workflows.graph`, React-Flow-kompatibel): `{"nodes": [{id, type, position, data: {label, config}}], "edges": [{id, source, target, sourceHandle}]}`.

| Typ | Konfiguration | Wirkung |
|---|---|---|
| `start` | – | Einstieg (genau einer) |
| `trim_pages` | `max_pages` | neue PDF-Version mit den ersten N Seiten (`pdf_edit_service.auto_trim`) |
| `detect_type` | `system_prompt_id?` | Dokumententyp erkennen (leer = Dokumententyp-Prompt aus den Einstellungen; **ohne beides Fehler** — es gibt keinen Code-Fallback) |
| `branch` | `field`, `op`, `value` | Ja/Nein-Weiche (`true`/`false`); Felder `document_type`, `supplier_name`, `page_count`, `validation_status` |
| `extract` | `system_prompt_id?`, `ai_config_id?` | Rechnungsdaten extrahieren (leer = gewählter/Standard-Prompt bzw. -KI) |
| `validate` | `auto_correct`, `ai_recheck` | Plausibilitätsprüfung (`_plausibility_pass`), Stufen einzeln abschaltbar |
| `normalize_units` | `target` (`t`\|`kg`) | Gewichte (kg/g/t) in Menge, Einheit und Einzelpreis (€ je Einheit) auf eine Einheit umrechnen (`plausibility_service._normalize_mass_to_tonnes`); wirkt nur nach einer Extraktion |
| `ai_refine` | `system_prompt_id` (Pflicht), `ai_config_id?`, `include_images` | KI-Nachbearbeitung: eigener Prompt + bisheriges Ergebnis (JSON) → ersetzt Felder/Positionen (Prüfstatus wird zurückgesetzt, danach erneut „Plausibilität prüfen“); liefert die KI nichts Verwertbares, bleiben die Daten unverändert |
| `end` | – | Ende |

Regeln (`validate_graph`, beim Speichern erzwungen → HTTP 422): genau ein Start ohne Eingang; jeder Ausgang höchstens **eine** Kante (sequenzieller Ablauf);
keine Zyklen; Verzweigung nur mit `true`/`false`; Operatoren passend zum Feld. Nur Warnungen: nicht erreichbare Knoten, offene Verzweigungsausgänge, keine
Extraktion, Prüfung vor Extraktion, fehlende Prompt-/KI-IDs. Fehlt eine Kante, endet der Ablauf dort.

**Datenfluss je Knoten** (`workflow_trace`, Ansicht `components/belege/WorkflowTraceView.tsx` im KI-Modal der Belege): Jeder Ablaufeintrag enthält zusätzlich `config`, `input`/`output` (Datenstand vor/nach dem Knoten:
Felder, Positionen — max. 100 —, Prüfstatus/-meldungen), `changes` (`diff_snapshots`: Pfad/vorher/nachher — leer = Knoten hat nichts verändert) und `io` (`prompt`, `response`, `model`, `notes`; Prompt/Antwort auf 20.000 Zeichen gekürzt).
Knoten legen Prompt/Antwort/Hinweise in `ctx.node_io` ab (Engine setzt es je Knoten zurück); `normalize_units` und `branch` erklären in `notes`, warum etwas (nicht) geschah. Der letzte Eintrag `type: "saved"`
(„In Datenbank gespeichert“) liest die Positionen nach dem Speichern aus der Datenbank (Gegenprobe). Das Protokoll wächst dadurch auf einige 10 KB je Beleg (nur bei Workflow-Analysen).

**Nachvollziehbarkeit** (am Dokument): `used_workflow_id/_name`, `used_workflow_graph` (Snapshot, bleibt nach Ändern/Löschen des Workflows erhalten),
`workflow_trace` (pro Knoten: Status, Dauer, Detail, bei Weiche der Ausgang), dazu `used_*_prompt`. Beim Start jeder Analyse werden diese Felder zurückgesetzt
(`save_used_prompts`); sie sind Teil des Snapshots der **Analyse-Historie**. Fehler in einem Knoten → Status `error` mit Knotenname in `documents.raw_response`.

Task-Liste (`routers/tasks.py`, Seite `/tasks`): `POST /api/tasks/restart-all[?include_failed=true]` sendet alle abgeschlossenen (optional auch fehlgeschlagenen) Tasks samt Dokumenten erneut an die KI;
`DELETE /api/tasks?status=all` leert die gesamte Liste (auch wartende/laufende; laufende KI-Aufrufe werden nicht abgebrochen).

Endpunkte: `GET|POST /api/workflows`, `GET /api/workflows/templates` (Standard, Nur Extraktion, Erst kürzen), `POST /api/workflows/validate`,
`GET|PUT|DELETE /api/workflows/{id}`, `POST /{id}/set-default|clear-default|duplicate`, `PUT /api/vendors/{id}/workflow`. Workflow löschen setzt Lieferanten-Zuordnungen auf `NULL`.

**Neuer Knotentyp:** Konfigurationsmodell in `CONFIG_MODELS` + Eintrag in `NODE_TYPES`/`output_handles` (`workflow_service.py`), Handler in `HANDLERS`
(`workflow_runner.py`), Darstellung in `components/workflows/nodeMeta.ts` und Formular im `Inspector` (`WorkflowEditor.tsx`), Typ in `WorkflowNodeType` (`api.ts`).

Frontend: `/settings/workflows` (Liste, Standard setzen, duplizieren, aus Vorlage) und `/settings/workflows/[id]` (React-Flow-Editor `@xyflow/react`, exakt
gepinnt: Palette, Zeichenfläche, Eigenschaften je Knoten, Live-Prüfung); Belege: Dropdown „Workflow" in der Analyse-Leiste, KI-Modal zeigt Workflow + Ablaufprotokoll
(`UsedWorkflow`); Lieferanten: Spalte „Workflow". **Neues npm-Paket → nach dem Update den Frontend-Container neu starten** (`npm install` läuft im Startkommando).

### Große Bestände: paginierte Belegliste (`crud/document.py`, `routers/documents.py`)

Die Belege-Seite lädt **nur die aktuelle Seite** (Standard 50, wählbar 25–200); getestet mit 15.000 Belegen (Liste ≈ 27 ms statt 1,7 s).

- `GET /api/documents?limit=&offset=` + Filter → Gesamtzahl im Header **`X-Total-Count`** (per `Access-Control-Expose-Headers` freigegeben).
  Ohne `limit` werden weiterhin alle Treffer geliefert (alte Schnittstelle).
- `GET /api/documents/ids` (+ Filter) → alle Treffer-IDs für „Alle N Treffer auswählen"; `GET /api/documents/filter-options` → Jahre/Firmen/Lieferanten.
  Beide Routen stehen **vor** `/{doc_id}`.
- Schwere Spalten (`raw_response`, `validation_details`, `used_*`, Workflow-Snapshots) werden in der Liste per `defer` nicht geladen —
  dafür gibt es `GET /{id}`. `tests/test_document_list.py` prüft das per SQL-Mitschnitt.
- Filter laufen über `_filtered_query` (Liste, Zähler, ID-Liste); neue Filter nur dort ergänzen und in `_FILTER_KEYS` eintragen.
- Migration `0011_search_indexes`: B-Tree-Indizes + `pg_trgm`-GIN-Indizes für Teilstring-Suche (übersprungen, falls die Erweiterung nicht anlegbar ist).
- Frontend: `Pager` oben/unten; Auto-Refresh lädt die **aktuelle** Seite neu (`pageRef`) statt auf Seite 1 zu springen. Direktlink
  `/belege?doc_id=42` (auch `?id=42`, optional `&include_deleted=1`) füllt das Feld „Beleg-ID"; gelesen aus `window.location` im Mount-Effekt.
- Neue Listen mit potenziell tausenden Zeilen immer serverseitig paginieren und schwere Spalten deferieren.

### Artikelsuche (`crud/position_search.py`, `routers/positions.py`, Seite `/artikel`)

Durchsucht **alle Positionen (`order_positions`) aller Belege** — paginiert, sortierbar, mit Kennzahlen (getestet mit 75.000 Positionen, alle Abfragen < 300 ms).

- `GET /api/positions` (Header `X-Total-Count`), `/summary` (Anzahl, Belege, Σ Gesamtpreis, Einzelpreis min/Ø/max, je Einheit), `/filter-options` (Einheiten).
  Dieselben Filter für alle drei (`_filters` im Router, `_filtered` in der CRUD-Datei).
- Filter: `q` (alle Wörter müssen in Bezeichnung, Beschreibung **oder** Artikelnummer vorkommen; `%`/`_` sind keine Platzhalter), `article_number`, `supplier_name`
  (Teilstring auf `invoice_extractions.vendor_id` = Lieferantenname), `company`, `year` (aus dem Import), `batch_ids`, `document_id`, `invoice_number`, `unit`,
  `unit_price_`/`total_price_`/`quantity_` `min|max`, `date_from|date_to`, `validation_status`, `include_deleted`.
- **Einheiten-Umrechnung** (`services/unit_conversion.py`, nutzt dieselbe Tabelle `_UNITS` wie die Plausibilitätsprüfung): Gewicht → t, Volumen → l, Länge → m, Fläche → m².
  Zur Abfragezeit in SQL (nichts gespeichert, gilt sofort für den Altbestand): Trefferfelder `base_quantity`, `base_unit`, `base_unit_price` (= Positionspreis ÷ Basismenge;
  leer bei Stück/Verpackungen/unbekannten Einheiten); `/summary` fasst `by_unit` in der Basiseinheit zusammen (t + kg + g → t); Filter `unit_group=mass|volume|length|area`;
  `GET /api/positions/unit-table` liefert die Umrechnungstabelle (Oberfläche: „ⓘ Einheiten-Umrechnung"). Neue Einheit → in `_register(...)` ergänzen, nicht hier.
- Sortierung: leere Werte immer am Ende, `id` als Tie-Breaker → stabiles Blättern. Standard: neueste Belege zuerst.
- Es werden nur Listenspalten gelesen (kein `raw_response`); Teilstring-Suche nutzt die `pg_trgm`-Indizes aus Migration 0011.
- Frontend `app/artikel/page.tsx`: alle Filter stehen in der Adresszeile (`?q=…&unit=t`) → teilbar; Beleg-Link auf `/belege?doc_id=…`.

### Große Bestände: Zeitlimits, Zähl-Obergrenze, Zwischenspeicher (`services/query_guard.py`)

- Artikelsuche: `statement_timeout` (PostgreSQL, `SET LOCAL`) — Suche 30 s, Kennzahlen 45 s; bei Überschreitung HTTP 504 mit verständlicher Meldung
  statt Browser-Timeout. Die Gesamtzahl ist auf `COUNT_CAP = 10 000` begrenzt (`X-Total-Count`, `X-Total-Capped: true` → „mehr als …"-Hinweis).
- Lieferant aus dem Dropdown filtert **exakt** (`supplier_exact=true`, nutzt Index) statt `ILIKE '%…%'`.
- `TTLCache` für `/api/positions/summary` (60 s), `/filter-options` (300 s) und `/api/documents/filter-options` (120 s);
  `clear_all_caches()` wird nach Ein-/Ausblenden und Löschen von Imports aufgerufen. Tests leeren die Caches automatisch (conftest).
- Frontend `/artikel`: Tabelle, Kennzahlen und Filter-Optionen laden **unabhängig** (Ladebalken, Skeleton-Zeilen, „Suche läuft …").
  Dropdowns (Import/Jahr/Lieferant) in Belege und Artikel folgen „Ausgeblendete Imports einbeziehen".
- Belege mit geöffneter PDF-Vorschau: kompakte Tabelle (Firma/Jahr/Seiten/Betrag/KI ausgeblendet, Aktionen ohne Umbruch).
- Nach großen Datenänderungen (z.B. Nachrüstung) `VACUUM ANALYZE;` ausführen, damit der Planer die Indizes nutzt.

### Imports ausblenden (`import_batches.hidden_at`, Migration `0012_import_hidden`)

Ganze Imports lassen sich ausblenden, wenn sie gerade nicht wichtig sind — **nichts wird gelöscht**, alles ist umkehrbar.

- `POST /api/imports/visibility` `{ids, hidden}` (mehrere auf einmal, idempotent: bereits passende Imports behalten ihren Zeitstempel;
  unbekannte ID → 404 und es ändert sich nichts). `GET /api/imports` liefert Ausgeblendete nur mit `include_hidden=true`.
- `GET /api/documents` (+ `/ids`, `/filter-options`) und `GET /api/positions` schließen Belege ausgeblendeter Imports standardmäßig aus;
  `include_hidden=true` nimmt sie hinein. **Ausnahmen:** ein ausdrücklich gewählter Import (`batch_ids`) und die Suche per `doc_id`/`document_id`
  zeigen immer an. Neue Auswertungen/Listen über Belege müssen diese Regel (`_filtered_query`, `position_search._filtered`) mitbedenken.
- Frontend: Dashboard mit Mehrfachauswahl (Ausblenden/Einblenden), Schalter „Ausgeblendete anzeigen (N)", Zeilen-Buttons und Markierung;
  Import-Detailseite mit Button; Belege-/Artikel-Filter „Ausgeblendete Imports einbeziehen".

### Gemeinsamer Excel-Export mehrerer Imports (`routers/imports.py`)

`GET /api/imports/export?ids=1&ids=2` — mehrere Imports in **eine** Datei (Dashboard: Imports ankreuzen → „↓ Excel exportieren"; zusätzlich Excel-Link je Zeile).
Gleiche Spaltenkonfiguration wie der Einzelexport (`ExportConfig`); bei ≥ 2 Imports kommt am Ende beider Sheets die Spalte **„Import"** (`Firma Jahr (#id)`) dazu.
Die Route muss **vor** `/{batch_id}` stehen. Ausgeblendete Imports sind exportierbar. Laufzeit: ≈ 5–8 ms je Beleg (10.000 Belege ≈ 50–80 s) —
der Next.js-Rewrite-Proxy hat bereits `proxyTimeout` 920 s.

### Lieferanten: Dublettensuche und Zusammenführen (`services/supplier_dedup.py`, `crud/vendor.py::merge`)

- `GET /api/vendors/duplicate-groups`: Gruppen von Dublettenkandidaten (Union-Find über alle Merkmale; ein Lieferant mit mehreren Bankkonten
  wird über **alle** seine IBANs verglichen). Konfidenz: gleiche IBAN, USt-IdNr. oder Steuernummer (Schreibweise egal) = **hoch**; gleicher
  Name nach Normalisierung (ohne Rechtsform wie GmbH/KG/e.K., Umlaute/Satzzeichen egal) = **mittel**; Namensähnlichkeit ≥ 88 % = **niedrig**.
  Es wird **nie automatisch** zusammengeführt.
- `POST /api/vendors/merge` `{target_id, source_ids, fields?, update_document_names?}` — eine Transaktion: leere Stammdatenfelder des Ziels aus
  den Quellen auffüllen (`fields` legt Endwerte ausdrücklich fest), Bankverbindungen der Quellen übernehmen (bereits vorhandene IBANs entfallen),
  Workflow des Ziels bleibt (sonst erster der Quellen), Quellen werden gelöscht. Fehler → HTTP 400/404, nichts wird verändert.
- **Belege hängen nur über den Namen am Lieferanten** (`invoice_extractions.vendor_id` ist Text, kein Fremdschlüssel). Deshalb ist
  `update_document_names` hier standardmäßig **an**: Belege der Quellen (und des Ziels bei geändertem Namen) erhalten den endgültigen Namen,
  sonst verlieren sie die Zuordnung (Belegzähler, Workflow-Auswahl). Der Originalwortlaut bleibt in `raw_response`.
- `VendorRead.document_count` = nicht gelöschte Belege mit diesem Namen (`crud.vendor.get_document_counts`, nur in Liste und Dublettensuche befüllt).
- **Suchoptionen** (`GET /api/vendors/duplicate-groups?criteria=…&name_check=&min_name_similarity=&similarity=&max_key_share=`): gesuchte Merkmale
  einzeln wählbar; Standard `name_check=true` — IBAN/USt-IdNr./Steuernr.-Treffer verbinden nur Lieferanten mit Namensähnlichkeit ≥ 50 %
  (sonst wirft eine falsch gelesene Nummer, z.B. die des Rechnungsempfängers, fremde Lieferanten in eine Gruppe — Union-Find verkettet
  transitiv); `max_key_share` (Standard 10) ignoriert Nummern, die bei zu vielen Lieferanten vorkommen. Frontend: „⚙ Suchoptionen".
- **Vergleichen** (`components/suppliers/CompareModal.tsx`): Button je Dublettengruppe und für die Mehrfachauswahl — Lieferanten nebeneinander, je Feld
  „identisch / abweichend / nur teilweise / überall leer" (Schreibweise egal), abweichende Einzelwerte rot, Filter „Nur Unterschiede", Weiter zu „Zusammenführen…". Rein im Frontend (Daten aus der Lieferantenliste).
- Frontend `/lieferanten`: „Dubletten suchen" (Gruppen mit Konfidenz und Gründen), Mehrfachauswahl → `components/suppliers/MergeModal.tsx`.
- `find_or_create` (Analyse) erkennt weiterhin nur exakte IBAN/USt-IdNr./Namen — Schreibvarianten legen neue Lieferanten an, die später
  per Dublettensuche zusammengeführt werden können.

### Benutzeranmeldung (`app/auth.py`, `services/auth_service.py`, `routers/auth.py`, Migration `0013_users`)

Alle Daten-Endpunkte verlangen eine Anmeldung (`dependencies=[Depends(require_user)]` an jedem `include_router` in `main.py`);
offen sind nur `/health`, `/docs` und `/api/auth/*`. **Neue Router in `main.py` immer mit `dependencies=_protected` registrieren.**
Der Worker-Container (`app/worker/main.py`) ist nur im Docker-Netz erreichbar (`expose`) und ruft das Backend nicht auf — er braucht keine Anmeldung.

- Benutzer kommen initial aus der `.env`: `INITIAL_ADMIN_USER`/`INITIAL_ADMIN_PASSWORD`, weitere in `INITIAL_USERS="name:pw;name2:pw2"`.
  `seed_initial_users` läuft nach den Migrationen beim Backend-Start, legt **nur fehlende** Benutzer an und überschreibt vorhandene nicht
  (in der Oberfläche geändertes Passwort bleibt). `FORCE_PASSWORD_RESET=true` = einmaliger Notfall-Reset (beendet auch die Sitzungen).
  `AUTH_ENABLED=false` = Notausgang (Backend-Log warnt). Ohne jeden Benutzer loggt das Backend einen Fehler.
- Passwörter: `hashlib.scrypt` (nur Standardbibliothek, kein neues Paket). Sitzung = serverseitiger Eintrag in `user_sessions` (SHA-256 des Tokens),
  Cookie `ra_session` (httponly, samesite=lax, `COOKIE_SECURE` für HTTPS), `SESSION_DAYS` Tage gültig. Fehlversuche: 5 → 60 s Sperre je Benutzername.
- Endpunkte: `POST /api/auth/login|logout|change-password`, `GET /api/auth/me` (`{auth_enabled, username}`).
- Frontend: `components/auth/AppShell.tsx` (im Root-Layout) prüft `/api/auth/me`, leitet auf `/login?next=…` und zeigt die Navigation erst danach;
  der axios-Interceptor in `lib/api.ts` leitet bei jedem 401 dorthin. Seiten: `/login`, `/settings/account` (Passwort ändern; Klick auf den Benutzernamen in der Navigation).
- Tests: `tests/conftest.py` setzt `AUTH_ENABLED=false`; `tests/test_auth.py` schaltet es gezielt ein.
- `alembic/env.py` ruft `fileConfig` nur noch auf, wenn kein Logging eingerichtet ist (sonst verstummen die Anwendungs-Logs nach der Migration im Backend-Prozess).

### Lieferanten-Deduplication (`crud/supplier.py`)

`find_or_create()` sucht in dieser Priorität:
1. IBAN (stärkster Identifier)
2. VAT-ID (USt-IdNr.)
3. Name (Fallback)

Vorhandene Felder werden nur überschrieben, wenn der neue Wert besser (nicht leer) ist.
`supplier_id` wird in `invoice_extractions` gespeichert.

### Systemprompts (`models/system_prompt.py`, `crud/system_prompt.py`)

**Prompt-Typen:** `0` Dokumententyp-Erkennung · `1` Standard-Extraktion · `2` **Workflow-Baustein** (wird nie automatisch gewählt; beliebig viele, nur ausdrücklich z.B. im Workflow-Knoten).
Typ 0 und 1 gibt es höchstens einmal: Anlegen, Typ-Wechsel und „Als Standard setzen“ stufen andere Prompts desselben Typs zu Typ 2 herab; `get_default()`/`get_doc_type_prompt()` sind
deterministisch (kleinste ID), falls ein älterer Stand mehrere Typ-1-Prompts enthält (Prompts-Seite zeigt dann eine Warnung). Kopien sind immer Typ 2.

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

### Automatisierungseinstellungen (`automation_settings`, `routers/settings.py`)

Singleton-Tabelle (id=1), CRUD über `GET/PUT /api/settings/automation`. Steuert drei
unabhängige Hintergrund-Loops im Worker-Container:

| Feld | Loop | Beschreibung |
|---|---|---|
| `folder_sync_interval_minutes` | `folder_sync.py` | Wie oft Batches mit `folder_sync=True` auf neue PDFs geprüft werden |
| `export_weekday`/`export_hour`/`export_minute` | `export_schedule.py` | Wochentermin für den automatischen inkrementellen Excel-Export (`auto_export=True`-Batches) |
| `worker_concurrency` | `worker.py::Dispatcher` | Max. gleichzeitige Worker (Obergrenze unabhängig von `parallel_request` je KI-Config) |
| `worker_poll_interval_seconds` | `worker.py::Dispatcher` | Wie oft die DB auf neue Tasks/geänderte Configs geprüft wird |
| `worker_lock_timeout_minutes` | `worker.py::Dispatcher` | Ab wann ein `in_progress`-Task als "hängengeblieben" gilt und zurückgesetzt wird |

**ENV-seed-dann-Website-Pattern:** Die drei `worker_*`-Felder werden NUR beim allerersten
Anlegen der Singleton-Zeile aus `app.config.settings` (also aus der `.env`) übernommen
(`crud/automation_settings.py::get_or_create`). Existiert die Zeile bereits (z.B. weil eine
neue Migration nur Spalten hinzufügt, die Zeile aber schon existiert), greift stattdessen der
`server_default` der Migration — die `.env`-Werte werden dann NICHT automatisch übernommen und
müssen einmalig manuell über die Website nachgetragen werden. Nach dem ersten Anlegen ist die
Website (DB) die alleinige Quelle der Wahrheit; die Worker lesen die Werte bei **jedem**
Poll-Zyklus frisch aus der DB (`Dispatcher._load_worker_settings()`), Änderungen wirken also
ohne Container-Neustart, meist innerhalb eines Poll-Intervalls (bzw. innerhalb von ~30s für
den Ordner-Sync-Loop, der intern kürzer pollt als sein eigenes Intervall).

Frontend: `/settings/automation` (Formular mit allen sechs Feldern).

### Umgebungsvariablen (`.env`)

```
POSTGRES_USER=appuser
POSTGRES_PASSWORD=...
POSTGRES_DB=rechnungsanalyse
DATABASE_URL=postgresql://appuser:...@db:5432/rechnungsanalyse
IMPORT_BASE_PATH=/volume1/docker/_rechnungsanalyse/import
STORAGE_PATH=/volume1/docker/_rechnungsanalyse/storage
EXPORT_BASE_PATH=/volume1/docker/_rechnungsanalyse/export
TIMEZONE=Europe/Berlin
WORKER_CONCURRENCY=5
WORKER_POLL_INTERVAL=20
WORKER_LOCK_TIMEOUT_MINUTES=30
```

**Wichtig:** `WORKER_CONCURRENCY`/`WORKER_POLL_INTERVAL`/`WORKER_LOCK_TIMEOUT_MINUTES` sind nur
die **Anfangswerte** für die `automation_settings`-Singleton-Zeile — sie werden nur beim
allerersten Anlegen dieser Zeile übernommen (`crud/automation_settings.py::get_or_create`).
Danach ist ausschließlich **Einstellungen → Automatisierung** im Frontend maßgeblich; ein
Ändern der `.env` hat auf einem bereits laufenden System KEINE Wirkung mehr. Siehe Abschnitt
„Automatisierungseinstellungen" unten.

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
│   │                                   # Zeigt documents_processed_total (aus worker-stats)
│   ├── belege/page.tsx                 # Alle Dokumente, Filter, KI-Analyse starten
│   │                                   # KI-Rohdaten-Ansicht + Infos-Ansicht (50/50)
│   │                                   # Dokumententyp-Filter (DocTypeMultiSelect)
│   │                                   # Dokumententyp-Spalte in Tabelle
│   │                                   # Lazy-Load: KEIN automatisches Laden beim Mount,
│   │                                   #   erst nach "Filter anwenden" (hasLoaded-Gate) —
│   │                                   #   verhindert Hänger bei sehr vielen Belegen (z.B. 40000)
│   ├── imports/
│   │   ├── new/page.tsx                # Neuen Import starten
│   │   └── [id]/page.tsx               # Import-Detail: ProgressPanel + Dokumentenliste
│   │                                   # SSE + Polling-Fallback alle 4 s
│   │                                   # Dokumentenliste lädt automatisch nach Abschluss
│   │                                   # Queue-Status-Panel (pending/in_progress/completed/
│   │                                   #   failed), unabhängig alle 5s gepollt, mit
│   │                                   #   "↻ Erneut senden" / "✕ Aus Queue entfernen"
│   └── settings/
│       ├── ai/page.tsx                 # KI-Konfigurationen verwalten
│       ├── ai-test/page.tsx            # KI-Konfiguration testen (ohne Aktivierung) —
│       │                               #   PDF-Upload, Prompt-Auswahl, volle Request/Response
│       ├── automation/page.tsx         # Ordner-Sync-Intervall, Export-Zeitplan,
│       │                               #   Worker-Konkurrenz/Poll-Intervall/Lock-Timeout
│       ├── prompts/page.tsx            # Systemprompts verwalten
│       │                               # Checkbox: Als Dokumententyp-Prompt verwenden
│       │                               # Hilfetext: erwartetes KI-JSON + Typenliste
│       └── image/page.tsx             # Bildkonvertierungseinstellungen
├── components/
│   ├── Nav.tsx                         # Links: Dashboard, Belege, Neuer Import,
│   │                                   #   KI-Einstellungen, KI testen, Systemprompts,
│   │                                   #   Bildeinstellungen, Automatisierung
│   ├── dashboard/BatchTable.tsx
│   ├── dashboard/FilterBar.tsx
│   ├── imports/ImportForm.tsx          # Firma + Jahr + Pfad-Vorschau (aus API)
│   │                                   # Optionen: Quelldateien löschen,
│   │                                   #   KI-Analyse nach Import (mit KI-Config + Prompt)
│   ├── imports/DocumentsTable.tsx
│   ├── imports/ProgressPanel.tsx       # SSE + initialTotal/initialProcessed Fallback
│   └── settings/AIConfigForm.tsx       # port-Feld NICHT required (leer erlaubt);
│                                       #   max_context_tokens-Feld mit Seiten-Kapazitäts-
│                                       #   Hilfetext; IP-Feld-Label erwähnt volle-URL-Support
└── lib/
    ├── api.ts      # axios-Client, alle API-Typen und -Funktionen
    └── sse.ts      # SSE-Client für Fortschritts-Updates
```

### Navigation (`Nav.tsx`)

```
/dashboard          Dashboard
/belege             Belege
/imports/new        Neuer Import
/settings/ai        KI-Einstellungen
/settings/ai-test   KI testen
/settings/prompts   Systemprompts
/settings/image     Bildeinstellungen
/settings/automation Automatisierung
```

### API-Client (`src/lib/api.ts`)

- Server-seitig: vollständige URL via `NEXT_PUBLIC_API_URL`
- Client-seitig: leere Basis → Next.js Rewrite-Proxy
- Wichtig: Axios-Calls **mit** trailing Slash (`/api/documents/`) — Next.js Rewrite
  entfernt den Slash, Backend empfängt ohne Slash → passt zu `redirect_slashes=False`

Exports:
- `itemsApi` — Platzhalter
- `aiConfigsApi` — KI-Konfigurationen CRUD + `test(id, file, systemPromptId)` (multipart, 180s Timeout)
- `importsApi` — Import-Batches CRUD + `getStatus(id)` + `queueStatus(id)`, `requeue(id, systemPromptId)`, `dequeue(id)`
- `documentsApi` — Dokumente, Analyse, Vorschau, Kommentar
- `imageSettingsApi` — Bildkonvertierungseinstellungen
- `systemPromptsApi` — Systemprompts CRUD
- `importSettingsApi` — Pfade abrufen (`/api/settings/paths`)
- `logsApi` — System-Logs + `kiStats()` → `GET /api/logs/ki-stats`, `workerStats()` → `GET /api/logs/worker-stats`
- `documentTypesApi` — Dokumententypen abrufen (`GET /api/document-types`)
- `automationSettingsApi` — `get()`/`update()` → `GET/PUT /api/settings/automation`

Typen:
- `DocumentType` — `{ id: number; name: string }`
- `DocumentItem` — enthält `document_type_id?`, `document_type_name?`, `ki_input_tokens?`, `ki_output_tokens?`, `ki_total_duration?`
- `DocumentFilter` — enthält `document_type_ids?: number[]`
- `SystemPrompt` — enthält `is_document_type_prompt: boolean`
- `SystemPromptCreate` — enthält `is_document_type_prompt?: boolean`
- `AIConfig`/`AIConfigCreate` — `port: string | null` (optional)
- `AIConfigTestResult` — Ergebnis von `aiConfigsApi.test()` (Request + Response vollständig)
- `AutomationSettings`/`AutomationSettingsUpdate` — inkl. `worker_concurrency`, `worker_poll_interval_seconds`, `worker_lock_timeout_minutes`
- `BatchQueueStatus` — Ergebnis von `importsApi.queueStatus()` (Zählung nach Task-Status)

### Belege-Seite (`belege/page.tsx`)

- Filter: Firma, Jahr, Status, Dokumententyp, Import, Betrag von/bis, Seiten von/bis
- Tabelle: Checkbox, ID, Firma, Jahr, Dateiname, Seiten, Betrag, Status, Dokumententyp, Rechnungsnr., Lieferant, PDF-Link
- Betrag/Rechnungsnr./Lieferant/Dokumententyp kommen direkt aus `DocumentItem` (Backend liefert sie mit)
- Aktionsleiste bei Auswahl: KI-Konfiguration + Systemprompt wählen → „KI-Analyse starten"
- Auto-Refresh alle 5 s solange Dokumente mit Status `processing` vorhanden
- **Lazy-Load:** Beim Mount wird NUR `loadOptions()` (Firmen/Jahre/Batches/Dokumententypen für
  die Filter-Dropdowns) geladen — **keine** automatische Beleg-Abfrage. Erst nach Klick auf
  „Filter anwenden" (`applyFilters()`) wird `loadDocuments()` aufgerufen und `hasLoaded=true`
  gesetzt; solange `!hasLoaded`, zeigt die Seite einen Platzhalter-Hinweis statt der Tabelle.
  `resetFilters()` leert die Dokumentliste wieder und setzt `hasLoaded=false`. Grund: bei
  großen Datenmengen (z.B. 40000 Belege) blockierte/hing die Seite beim automatischen Laden
  ohne Filter.

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
| **KI** (violett) | Status `done`, `error` oder `waiting_capacity` | Zeigt KI-Rohantwort (oder bei `waiting_capacity` den Klartext-Grund) im Modal-Overlay inkl. Token-Statistik |
| **Infos** (smaragd) | Status `done` | Wechselt in Infos-Ansicht (50/50 Split) |

**KI-Modal Token-Statistik:** Liest `viewedDoc.ki_input_tokens`, `viewedDoc.ki_output_tokens`, `viewedDoc.ki_total_duration` — also direkt aus `DocumentDetail`, nicht aus `viewedDoc.extraction`. Dies stellt sicher, dass auch Nicht-Eingangsrechnungen (ohne `InvoiceExtraction`) ihre Token-Stats anzeigen.

**KI-Modal Rohantwort:** `viewedDoc.extraction?.raw_response ?? viewedDoc.raw_response` — Fallback auf die Document-Ebene für Fälle ohne `InvoiceExtraction` (z.B. `waiting_capacity`, wo der Kapazitäts-Grund als Klartext in `documents.raw_response` steht, nicht als KI-JSON). `KiRawView` versucht `JSON.parse` und zeigt bei Fehlschlag den Rohtext — funktioniert deshalb sowohl für echte KI-JSON-Antworten als auch für einfache Klartext-Gründe.

**Status-Badge/Filter:** `waiting_capacity` (amber, "Wartet auf KI-Kapazität") — kein Endzustand, siehe „Dokument-Status-Flow" im Backend-Abschnitt.

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
- **Queue-Status-Panel:** ruft `importsApi.queueStatus(id)` unabhängig vom Import-Status alle
  5s ab (auch nach Abschluss, solange noch Worker-Tasks existieren). Zeigt Zählung
  pending/in_progress/completed/failed. Buttons „↻ Import erneut senden"
  (`importsApi.requeue`) und „✕ Aus Queue entfernen" (`importsApi.dequeue`) — siehe
  Backend-Abschnitt „Worker-Queue pro Import" für die genaue Semantik (insbesondere: `dequeue`
  entfernt auch `in_progress`-Tasks, da `pending` in der Praxis kaum je erreicht wird).

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
- **Doppelte KI-Anfragen für dasselbe Dokument (3× hintereinander → Fehler)**:
  Entsteht wenn mehrere Worker dieselbe Dokument-ID bearbeiten ODER wenn der Import-Pfad
  (`_import_then_analyze`) und die Worker-Queue gleichzeitig dasselbe Dokument verarbeiten.
  Drei Schutzebenen:
  1. `_analyzing_docs: set[int]` in `documents.py` — In-Memory-Guard, der parallele
     `_analyze_single`-Aufrufe für dieselbe ID sofort abblockt.
  2. `_prepare_document` in `runner.py` prüft `doc.status == "processing"` → skip.
  3. `_set_document_error_if_not_done` statt `_set_document_error` im Worker-Exception-Handler —
     verhindert, dass ein COMPLETE_SQL-Fehler nach erfolgreicher Analyse das `done`-Status
     mit `error` überschreibt (was 3 Retry-Zyklen auslösen würde).
  4. `_import_then_analyze` ruft `_run_analysis` NICHT mehr direkt auf, sondern reiht
     Dokumente via `_db_enqueue_for_analysis` in die Worker-Queue ein → kein Konflikt.
- **`NameError: cannot access local variable 'images_b64'`**: Entsteht wenn `del images_b64` vor `len(images_b64)` steht. Seitenanzahl immer zuerst in `page_count` sichern, dann `del`.
- **`TypeError: Unrecognized arguments` bei `InvoiceExtraction`**: `_map_new_format()` gibt `supplier_street/zip/city` zurück, die keine DB-Spalten sind. Vor `save_extraction` mit `_SUPPLIER_ONLY_KEYS` herausfiltern.
- **`ki_*`-Spalten fehlen (Migration 0009 nicht angewendet)**: `save_extraction` fängt den Commit-Fehler ab und wiederholt den Schreibvorgang ohne KI-Stats. Migration nachholen: `alembic upgrade head`.
- **`KeyError` beim Container-Neustart nach Migration**: `down_revision` in einer neuen Migration muss die `revision`-ID der Vorgänger-Migration verwenden (nicht den Dateinamen). Beispiel: `0011_document_types.py` hat `down_revision = "0010"` (nicht `"0010_ki_total_duration"`).
- **KI-Stats nicht angezeigt für Nicht-Eingangsrechnungen**: `DocumentDetail` erbt von `DocumentRead`, nicht von `DocumentListRead`. Deshalb müssen `ki_input_tokens`, `ki_output_tokens`, `ki_total_duration` **explizit** in `DocumentDetail` deklariert werden. Das KI-Modal liest `viewedDoc.ki_input_tokens` (nicht `viewedDoc.extraction?.ki_input_tokens`), damit auch Dokumente ohne `InvoiceExtraction` Stats haben.
- **Dokumententyp-Prompt: Nur einer gleichzeitig aktiv**: `_clear_doc_type_prompt(db)` muss vor dem Setzen von `is_document_type_prompt=True` aufgerufen werden. Wird in `crud/system_prompt.py` in `create()` und `update()` gehandhabt.
- **Beleg mit bereits bekanntem Typ wird bei "erneut an KI senden" NICHT neu klassifiziert (auch bereits als Eingangsrechnung erkannte Belege bleiben bei falscher Erkennung für immer falsch)**: `_analyze_single_inner` in `routers/documents.py` hatte zwei Kurzschluss-Pfade, die einen bereits gespeicherten `document_type` blind übernahmen, statt bei explizitem Analyse-Aufruf erneut zu klassifizieren: (1) ein früherer "Pfad A" für bereits bekannte Nicht-Eingangsrechnungs-Typen (>1) — SOFORT abgeschlossen, OHNE jeden weiteren KI-Aufruf; (2) eine Sonderbehandlung in Pfad C für `existing_document_type == 1` — übersprang bei bereits bekannter Eingangsrechnung die Typ-Erkennung IMMER und extrahierte direkt. Symptom (1): "KI-Analyse starten"/"Import erneut senden" hatte für Nicht-Eingangsrechnungen sichtbar keine Wirkung (kein neuer Request, Status sofort wieder "done"). Symptom (2): Wurde der Typ-Erkennungs-Prompt korrigiert (z.B. weil er fälschlich zu viele Dokumente als Eingangsrechnung erkannte), blieb ein einmal falsch als Eingangsrechnung klassifiziertes Dokument bei jeder erneuten Analyse trotzdem eine "Eingangsrechnung" — der neue, korrigierte Prompt kam nie zum Einsatz, weil die Typ-Erkennung komplett übersprungen wurde. **Fix:** Beide Kurzschlüsse entfernt. Pfad B (Typ-Erkennung) greift jetzt IMMER, sobald ein Typ-Erkennungs-Prompt konfiguriert ist — unabhängig vom gespeicherten `existing_document_type`. Pfad C (direkte Extraktion ohne Klassifizierung) ist nur noch der reine Legacy-Fall ohne Typ-Prompt. Jeder explizite Analyse-Aufruf prüft den Typ jetzt also immer frisch gegen den aktuellen Prompt.
- **KI-Test-Tool (`/settings/ai-test`) und Backup-Restore-Upload: `HTTP 500` bzw. `422 "loc":["body","file"],"msg":"Field required"` beim Datei-Upload, obwohl derselbe Request z.B. per curl einwandfrei funktioniert**: `aiConfigsApi.test()` und `backupApi.restore()` in `frontend/src/lib/api.ts` senden `FormData`. `apiClient` (dieselbe Datei, oben) wird aber via `axios.create({ headers: { "Content-Type": "application/json" } })` mit einem INSTANZ-WEITEN Default-Header erzeugt. Dieser Default gilt für JEDEN Request der Instanz, auch FormData-Uploads — axios sieht dadurch "Content-Type ist bereits explizit gesetzt" und deaktiviert seine automatische FormData-Erkennung (die sonst selbst den korrekten `multipart/form-data; boundary=...`-Header berechnen würde). Der Request-Body wird dann per `JSON.stringify(formData)` zu `"{}"` degeneriert und mit `Content-Type: application/json` gesendet — die Datei kommt beim Backend nie an. **Zwei Fehlversuche, bevor der eigentliche Fix gefunden wurde:** (1) den Header einfach nicht mehr explizit zu setzen reicht NICHT, weil der Instanz-Default trotzdem greift; (2) den Header manuell auf `"multipart/form-data"` zu setzen war der ursprüngliche Bug (fehlende `boundary`). **Der tatsächliche Fix:** `headers: { "Content-Type": undefined }` im Request-Config — nur das explizite Überschreiben mit `undefined` lässt axios den geerbten Instanz-Default-Header wirklich ENTFERNEN, wonach die automatische FormData-Erkennung wieder greift und der Browser die korrekte Boundary berechnet. Live im Browser verifiziert (XHR-Hook zeigte vorher `bodyType: "String"` + `Content-Type: application/json`, danach `bodyType: "FormData"` ohne Content-Type-Header — Request danach `HTTP 200`).
- **React 19 Hydration-Mismatch / Buttons reagieren nicht**: Entsteht wenn SSR-Output und initialer Client-Render abweichen. Häufigste Ursache: `useState(true)` als initialer Ladezustand. Die Seite rendert auf dem Server ohne Ladeindikator (nach dem Fix), aber der alte Client-JS-Bundle (aus `.next/`-Cache) erwartet noch `true`. React 19 bricht die Hydration komplett ab → keine Event-Handler → Buttons tot. **Regel:** Ladestate immer mit `useState(false)` initialisieren; `setLoading(true)` nur innerhalb von `load()` setzen, nicht auf Top-Level.
- **`.next/`-Cache überlebt Container-Neustart (NAS-spezifisch)**: Da `frontend:/app` als Docker-Volume gemountet ist, liegt der Turbopack-Cache unter `frontend/.next/` auf dem NAS-Host und überlebt Neustarts. Inotify funktioniert auf SMB/NFS-Mounts nicht → Turbopack erkennt keine Dateiänderungen → alte Client-Bundles werden weiterhin ausgeliefert. Lösung: `rm -rf /app/.next` am Anfang des Start-Kommandos in `docker-compose.yml` (bereits eingebaut).
- **HMR WebSocket blockiert (NAS-Hostname)**: Next.js 16 blockiert cross-origin Anfragen zu `/_next/webpack-hmr` wenn der Zugriff über einen Hostnamen erfolgt (z.B. `http://nas:3100`). Symptom im Container-Log: `⚠ Blocked cross-origin request to Next.js dev resource /_next/webpack-hmr from "nas"`. Lösung: `allowedDevOrigins: ["nas", "NAS-IP"]` in `next.config.ts` eintragen (bereits konfiguriert).
- **Frontend startet nicht nach `&&`-Kette in docker-compose**: Wenn ein Schritt in `cmd1 && cmd2 && cmd3` fehlschlägt, werden alle nachfolgenden Schritte übersprungen. Startup-Kommando verwendet daher `;` als Trenner — jeder Schritt läuft unabhängig. `npm install` Fehler blockieren nicht mehr `npm run dev`.
- **`Can't locate revision identified by '0008_token_count_ai_client'`**: Die Produktiv-DB wurde lokal mit eigenen Migrationen (`0007_export_config_table`, `0008_token_count_ai_client`) weiterentwickelt, die nie im Repo waren — `alembic_version` steht auf der zweiten. Entfernt man die Dateien (oder spielt einen Stand ohne sie ein), kennt Alembic die Revision nicht mehr. Im Repo liegen sie deshalb als leere Platzhalter-Migrationen (`pass`), die Kette läuft `0006 → 0007_export_config_table → 0008_token_count_ai_client → 0007_plausibility → …`. Getestet für DB auf 0008-Stand, auf 0006 und frisch. **Eigene Migrationen immer ins Repo einchecken**, bevor man eine neue Version einspielt.
- **Backend startet mit „Multiple head revisions" / `column ... does not exist` (z.B. `automation_settings.analysis_history_keep`)**: Auf dem NAS wird der Code per Kopieren eingespielt — **gelöschte Dateien bleiben dort liegen**. Eine veraltete Migrationsdatei in `alembic/versions/` (z.B. das früher gelöschte `0002_swap_prompt_types.py` mit `down_revision = "0001_initial"`, gleich wie `0002_automation`) verzweigt die Kette, `alembic upgrade head` bricht ab, es läuft keine Migration. `main.py::_assert_single_head` erkennt das beim Start, nennt die Dateien und **bricht den Start ab** (statt mit altem Schema weiterzulaufen); `tests/test_migration_chain.py` sichert genau einen Head im Repo. **Lösung:** die genannte Altdatei aus `alembic/versions/` löschen (Ordner mit dem Repo abgleichen), Backend neu starten. Nicht `alembic upgrade heads` verwenden — die verwaiste Migration würde Daten verändern (die Swap-Migration vertauscht Prompt-Typen). Jeder andere Migrationsfehler bricht den Start ebenfalls ab (`MigrationError`); nur „DB nicht erreichbar" bleibt nicht fatal.
- **Worker-Container zeigt kurzzeitig `worker_online: false`**: Normal beim ersten Start oder nach einem Neustart des `worker`-Containers, solange dieser noch hochfährt (asyncpg-Pool aufbauen, `wait_for_db`). Backend liefert in der Zwischenzeit den DB-Fallback (Queue-Länge weiterhin korrekt, `worker_count: 0`). Kein Fehler, kein Eingreifen nötig.
- **Dispatcher loggt SQL-Fehler direkt nach dem allerersten Start**: Backend und Worker starten parallel; der Worker wartet zwar auf `/venv/.ready` (Pakete installiert), aber nicht auf `alembic upgrade head` (Migrationen). Falls der Dispatcher pollt bevor die Migration durchgelaufen ist, schlägt `_load_servers`/`_load_tasks` kurzzeitig fehl — wird von `Dispatcher.run()` abgefangen und geloggt, kein Crash. Nach der ersten erfolgreichen Migration verschwindet die Meldung von selbst.
- **Worker-Container "hängt" komplett bei hoher KI-Konkurrenz (z.B. `parallel_request`/`worker_concurrency` ≥ 4) — keine neuen Anfragen, keine Fehlermeldung, `/health` antwortet nicht mehr**: Wirkt wie ein Event-Loop-Deadlock, ist aber tatsächlich ein **Segfault des Worker-Prozesses** (Diagnose: `docker exec <worker> sh -c 'cat /proc/<server-pid>/status'` zeigt `State: Z (zombie)`; `/proc/<pid>/stat`-Exitfeld endet auf 139 = 128+11 = SIGSEGV). Ursache: `pypdfium2` (PDFium) ist **nicht thread-sicher** — `pdf_service.pdf_to_base64_images()` wurde ohne Sperre aus vielen Threads gleichzeitig aufgerufen (`_run_ki_io` → `_KI_IO_EXECUTOR`, ein Aufruf pro gleichzeitig verarbeitetem Dokument). Vorbote/Frühwarnzeichen in sich widersprüchliche Log-Fehler wie `Failed to load document (PDFium: Success)` — typisches Symptom interner PDFium-Zustandskorruption durch gleichzeitigen Zugriff, BEVOR es zum Absturz kommt. Der `uvicorn --reload`-Supervisor (Reloader-Prozess) bemerkt den Absturz des Server-Kindprozesses nicht automatisch und startet ihn nicht neu — nur ein manueller `docker restart` hilft kurzfristig, das Problem tritt bei der nächsten Charge mit genug Parallelität sofort wieder auf. **Fix:** `_PDFIUM_LOCK` (globales `threading.Lock()`) in `pdf_service.py` — serialisiert NUR den PDFium-Rendering-Schritt (Millisekunden pro Seite), alles andere (KI-HTTP-Anfragen, JSON-Encoding, DB) bleibt voll parallel; dadurch sind auch hohe Werte wie `parallel_request=24`/`worker_concurrency=24` stabil. Beim Refactor unbedingt `bitmap.to_pil().copy()` verwenden (nicht nur `to_pil()`) — pypdfium2 teilt sich für RGBA/RGBX/L-Puffer den Speicher mit dem PDFium-Bitmap-Objekt, ohne `.copy()` zeigt das PIL-Image nach `pdf.close()` auf bereits freigegebenen Speicher.
- **Signal-basierte Prozess-Diagnose (`kill -SIGUSR1`) trifft unter `uvicorn --reload` oft den falschen Prozess**: Der Reloader (PID des `uvicorn`-Hauptprozesses, meist niedrige PID wie 7) ist NICHT derselbe Prozess wie der eigentliche Server (`multiprocessing`-Kindprozess, PID wird im Log als `Started server process [N]` angezeigt — meist eine höhere/andere PID). Ein Signal an die falsche PID beendet je nach OS-Default-Handler ggf. nur den Reloader (→ ganzer Container restartet) oder verpufft wirkungslos. Für Diagnosezwecke immer die im Startup-Log genannte Server-PID verwenden, oder besser: `faulthandler.dump_traceback_later(N, repeat=True)` beim Start registrieren (dumpt automatisch über einen eigenen Watchdog-Thread, unabhängig von Signal-Routing) — sofern nach der Diagnose wieder entfernt.
- **KI-Aufruf schlägt bei HTTPS-Cloud-Anbietern fehl (z.B. OpenRouter, HTTP 400/Verbindungsfehler)**: `documents.py` baute die `api_url` früher selbst über eine naive String-Zusammensetzung (`f"http://{ip}:{port}"`), die IMMER `http://` erzwang, auch wenn `ip_address` bereits `https://...` enthielt — abweichend von der (korrekten) URL-Bildung im Worker. Root-Cause eines echten Datenverlust-Vorfalls (fehlgeschlagene Re-Analyse überschrieb erfolgreiche `invoice_extractions`). Fix: einzige URL-Bildung jetzt zentral in `app/services/ai_url.py::build_ai_url()`, von ALLEN drei Aufrufern (`ai_service.py`, `ai_test_service.py`, `worker.py`) genutzt. Bei neuen Cloud-Providern: `ip_address` MUSS `https://` explizit enthalten (z.B. `https://openrouter.ai/api`), sonst wird stillschweigend Klartext-HTTP versucht.
- **OpenRouter/andere OpenAI-kompatible APIs lehnen `reasoning`-Parameter ab (`"expected object, received string"`)**: Wurde als reiner String (`"off"`/`"high"`) gesendet, OpenRouter erwartet ein Objekt (`{"effort": "high"}`) oder das Feld ganz weggelassen bei "aus". Fix: `app/services/ai_reasoning.py::build_reasoning_param()`, NUR im openai-kompatiblen Anfragepfad genutzt — LM Studios eigener `/api/v1/chat`-Endpunkt behält das String-Format.
- **KI-Config: Port-Feld zeigt trotz Code-Fix noch `:` oder Pflichtfeld "1234" in der Vorschau**: Fast immer ein veralteter Frontend-Bundle-Cache, kein Code-Bug — vor weiterem Debuggen erst `docker restart rechnungsanalyse-frontend-1` (lokal) bzw. `.next/`-Cache löschen (NAS) und per Chunk-Diff verifizieren, dass der neue Code wirklich ausgeliefert wird, siehe „Frontend-Cache manuell löschen" oben.
- **Nur ein Bruchteil der aktivierten `parallel_request`-Kapazität wird genutzt (z.B. 4 aktiviert, nur 2 aktive Worker)**: Nicht zwangsläufig ein Bug im Sinne von "kaputt" — der Dispatcher deckelt zusätzlich über `worker_concurrency`/`max_workers_cap` (Einstellungen → Automatisierung), unabhängig von der Summe der `parallel_request`-Werte aller aktiven KI-Configs. Beide Grenzen greifen gleichzeitig (`min()`); die kleinere gewinnt.
- **Import löschen hinterlässt verwaiste Worker-Tasks**: `workflow_tasks` referenziert `document_id` nur als JSONB-Payload (keine FK) — eine CASCADE über `Document`/`ImportBatch` erreicht diese Tabelle NICHT. `DELETE /api/imports/{id}` muss die zugehörigen Tasks daher explizit per Payload-Lookup löschen (`(payload->>'document_id')::int = ANY(:doc_ids)`), sonst bleiben sie in der Queue und werden dem Worker weiterhin zur Bearbeitung angeboten (obwohl das Dokument längst gelöscht ist).
- **Dokument wird nach 3 "kein Server verfügbar"-Versuchen fälschlich permanent als `error` markiert**: `acquire_slot()` wirft `TimeoutError`, wenn (noch) kein passender KI-Server frei ist — das ist kein fehlgeschlagener KI-Aufruf, sondern schlicht "später nochmal versuchen". Wurde früher trotzdem über `_check_attempts_and_fail`/`_release_for_retry` behandelt und zählte gegen `max_attempts`. Fix: `_release_for_retry_no_penalty()` — setzt den Task ohne Erhöhung von `attempts` zurück auf `pending`; er bleibt dadurch unbegrenzt in der Queue, bis wieder Kapazität frei wird. `attempts` zählt jetzt ausschließlich echte KI-Aufrufversuche (z.B. `ConnectionError` während des HTTP-Requests).
- **Bei großen Batches (z.B. 1000+ Belege) verschwinden Worker-Tasks aus der Queue, während der zugehörige Beleg noch "wird verarbeitet" anzeigt UND tatsächlich noch an die KI gesendet wird — Warteschlangen-Status zeigt "leer", obwohl der KI-Rechner sichtbar noch arbeitet**: Ursache war ein Duplikat-Bug in `worker.py`. `_load_tasks()` setzte `locked_at=NOW()` schon beim bloßen Laden eines Tasks in die interne `asyncio.Queue` — nicht erst, wenn ein Worker ihn tatsächlich übernimmt. Bei einer tiefen Warteschlange (z.B. 1000 Belege, 24 gleichzeitige Worker) kann ein Task locker länger als `worker_lock_timeout_minutes` (Standard 10) unangetastet in der Queue warten, BEVOR ein Worker überhaupt an der Reihe ist. Die periodische `_cleanup_stale_tasks(min_age_minutes=N)` hielt solche Tasks fälschlich für "verwaist" (Worker abgestürzt) und setzte sie auf `pending` zurück — während die ursprüngliche Kopie unverändert weiter in der internen Queue lag. Beim nächsten `_load_tasks()`-Zyklus wurde dieselbe Zeile dadurch ERNEUT geladen → zwei In-Memory-Kopien desselben Tasks. Beide werden irgendwann verarbeitet: läuft die erste durch und wird ihr Task-Datensatz danach z.B. über "Abgeschlossene Tasks löschen" entfernt, verarbeitet die zweite Kopie das (oft schon fertige) Dokument als Geisterprozess erneut — ohne zugehörigen Task-Datensatz, daher unsichtbar im Warteschlangen-Status, aber real noch mit laufender KI-Anfrage. Bei einem sehr langen Testlauf (1007 Belege, mehrere Stunden durch Unterbrechungen) kumulierten sich so hunderte Duplikate (`queue_size` laut `/status` weit über der tatsächlichen Beleganzahl). **Fix:** `locked_at` wird jetzt erst in `Worker._claim_task()` gesetzt (wenn ein Worker den Task WIRKLICH aus der Queue holt), nicht mehr in `_load_tasks()`. Solange ein Task nur in der Queue wartet, bleibt `locked_at` NULL — die SQL-Bedingung `locked_at < NOW() - interval` der Cleanup-Abfrage lässt NULL-Werte automatisch unangetastet, der Timeout greift dadurch nur noch bei Tasks, die ein Worker wirklich übernommen hat und seitdem hängen. **Manuelle Wiederherstellung nach dem Vorfall:** verwaiste "processing"-Belege ohne zugehörigen Task-Datensatz per `POST /api/documents/enqueue` mit den betroffenen `document_ids` neu einreihen (setzt sie zurück auf "pending" und reiht sie sauber neu ein).
- **KI-Test-Tool: `HTTP 500: Fehler beim Senden der Testanfrage` bei "Rechnung (Extraktion)"-Tests, besonders bei langsameren/lokalen Modellen**: DREI zusammenhängende Ursachen, in dieser Reihenfolge gefunden (erste zwei Fixes allein reichten NICHT). (1) `ai_test_service.REQUEST_TIMEOUT_SECONDS` stand auf 120s — mehr als 7× kürzer als die Produktions-Pipeline (`ai_service.py`, 900s). (2) `build_test_request()` lief AUSSERHALB von `run_ai_test()`s try/except — jede Exception daraus lief ungefangen bis zum Router durch, FastAPI/Starlette antwortet auf unbehandelte Exceptions mit reinem Text ohne JSON-`detail`-Feld, das Frontend zeigt dann nur die generische Fallback-Meldung. Beide gefixt (Timeout auf 900s, `build_test_request()` innerhalb try-Block), Fehler trat beim Nutzer aber weiterhin auf. **Der eigentliche Übeltäter (3):** Next.js' EIGENER `rewrites()`-Proxy (der `/api/:path*` intern zu `${NEXT_PUBLIC_API_URL}/api/:path*` weiterleitet) hat ein Standard-`experimental.proxyTimeout` von **exakt 30000ms** — komplett UNABHÄNGIG von jedem axios-Timeout im Frontend-Code oder httpx-Timeout im Backend! Erkennbar an `Failed to proxy http://backend:8000/api/ai-clients/1/test Error: socket hang up (code: 'ECONNRESET')` in den **Frontend**-Container-Logs (nicht Backend!) — und daran, dass im Backend-Log für die betroffene Anfrage GAR KEIN Abschluss-Eintrag (weder Erfolg noch Fehler/Traceback) erscheint, weil die Anfrage vom Next.js-Proxy gekappt wird, BEVOR die Backend-Antwort zurückkommt; das Backend verarbeitet oft trotzdem im Hintergrund weiter, aber die Antwort geht ins Leere. Betraf jede Anfrage über 30 Sekunden, unabhängig vom Endpunkt — bei lokalen Modellen und der längeren Extraktions-Systemprompt deutlich wahrscheinlicher als bei der kürzeren Typ-Erkennung, aber grundsätzlich JEDEN `/api/*`-Aufruf über 30s betreffend. **Fix:** `experimental.proxyTimeout: 920000` in `next.config.ts` — **Achtung:** `next.config.ts`-Änderungen werden NICHT hot-reloaded, Frontend-Container-Neustart zwingend nötig, sonst bleibt die alte 30s-Grenze aktiv obwohl der Code schon im Repo steht. Live im Browser verifiziert (derselbe Testfall, der vorher nach 30s mit ECONNRESET/HTTP 500 abbrach, lief danach 22,5s durch und lieferte ein vollständiges Ergebnis).

### Wichtige Abhängigkeiten

```
next 16.2.4, react 19, typescript 6, tailwindcss 4, axios, @xyflow/react 12.12.0
```

---

## Entwicklungs-Workflow

### Code-Änderungen übernehmen

```
Backend  (Python): uvicorn --reload aktiv → Dateiänderung genügt
Worker   (Python): uvicorn --reload aktiv → Dateiänderung genügt
Frontend (Next.js): next dev aktiv → Dateiänderung genügt

Container-Neustart nur nötig bei:
  - neuen Python-Paketen (requirements.txt)
  - neuen npm-Paketen (package.json)
  - Änderungen an next.config.ts (wird nicht hot-reloaded)
```

> **Hinweis Worker-Container:** Bis zu einem bestimmten Punkt lief der `worker`-Service
> **ohne** `--reload` — Code-Änderungen wurden dort erst nach einem manuellen Neustart
> (`docker restart <worker-container>`) wirksam, obwohl der Backend-Container dieselben
> Änderungen sofort übernahm. Das führte zu sehr verwirrenden Symptomen (z.B. ein bereits
> behobener Prompt-Bug wirkte scheinbar weiterhin, weil der Worker-Prozess seit Stunden
> lief und die alte Code-Version im Speicher hatte). Seitdem hat auch der Worker
> `--reload` in `docker-compose.yml` — betrifft nur den lokalen Dev-Betrieb, auf dem NAS
> läuft ohnehin kein `--reload` (dort wird bei jedem Deploy neu gestartet).

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
                   │          1──n  DocumentTokenCount
                   n──1  DocumentType
AIClients       (referenziert von ImportBatch.ai_config_id, WorkflowTask-Payload)
WorkflowTask    (Worker-Queue, JSONB-Payload {document_id, system_prompt_id}, keine FK)
ImageSettings   (Singleton, globale Bildkonvertierungseinstellungen)
SystemPrompt    (Standard-Prompt + optionaler Dokumententyp-Prompt)
AutomationSettings (Singleton, folder_sync/export-Zeitplan + worker_concurrency/poll_interval/lock_timeout)
```

### Migrationen

Siehe `alembic/versions/` — vollständige, aktuelle Liste im Backend-Dateibaum weiter oben
(Abschnitt „Backend → Struktur"). Die ältere Historie (`0002_system_prompts`,
`0003_supplier`, `0009_ki_stats_on_invoice_extractions`, `0011_document_types`,
`0012_doc_ki_stats` u.a.) wurde zu einer neuen `0001_initial.py`-Baseline zusammengefasst;
Referenzen auf diese alten Dateinamen in älteren Notizen/Kommentaren sind überholt.

### Import-Status-Flow

```
pending → running → done
                 → error
```

### Dokument-Status-Flow

```
pending → processing → done
                    → error
                    → waiting_capacity → (automatisch neu versucht) → processing → done
```

`waiting_capacity` ist KEIN Endzustand: wird gesetzt, wenn ein Dokument für ALLE aktuell
konfigurierten KI-Server zu groß ist (siehe Worker._process() Kapazitätsprüfung). Der Grund
(z.B. "23 Seiten, max. verfügbar: 1 Seiten") steht als Klartext in `documents.raw_response`
(NICHT `extraction.raw_response`!) und ist über den "KI"-Button in der Belege-Übersicht
sichtbar — der zugehörige Task wird unbegrenzt neu versucht, das Dokument wird also
automatisch fertig verarbeitet, sobald eine ausreichend große KI-Konfiguration verfügbar ist
(z.B. nachträglich hinzugefügt oder `max_context_tokens` erhöht), ohne manuelles Eingreifen.
Live verifiziert: Dokument mit 23 Seiten gegen eine auf 1 Seite limitierte Konfiguration
getestet → `waiting_capacity` gesetzt, Task alle 20s (Poll-Intervall) neu versucht ohne
`attempts` zu erhöhen; nach Anheben der Kapazität automatisch fertig verarbeitet.
