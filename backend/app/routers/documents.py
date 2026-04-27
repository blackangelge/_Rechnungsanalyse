"""
Router für Dokument-Verwaltung und KI-Analyse.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ÜBERSICHT DER ENDPUNKTE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  GET    /api/documents               — Dokumentenliste mit optionalen Filtern
  POST   /api/documents/enqueue       — Dokumente in Worker-Warteschlange einreihen
  POST   /api/documents/analyze       — KI-Analyse direkt starten (Legacy-Pfad)
  GET    /api/documents/{id}          — Dokument-Details (inkl. Extraktion + Token-Stats)
  GET    /api/documents/{id}/preview  — PDF-Datei direkt im Browser anzeigen
  PATCH  /api/documents/{id}/comment  — Freitext-Kommentar speichern
  DELETE /api/documents/{id}          — Dokument logisch löschen (Soft-Delete)
  POST   /api/documents/{id}/restore  — Soft-Delete rückgängig machen

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KI-ANALYSE-PIPELINE — SCHRITT FÜR SCHRITT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Jede Analyse durchläuft diese Schritte (alle in _analyze_single_inner):

  SCHRITT 1 — DB-Lesephase (_db_analyze_read, läuft in Thread)
    • Dokument aus DB laden (inkl. Batch für Pfad-Auflösung)
    • KI-Konfiguration laden (API-URL, Modell, Token-Limits)
    • Bildkonvertierungseinstellungen laden (DPI, Format, Qualität)
    • Dokumententyp-Prompt laden (type=0 — falls kein Prompt → einstufig)
    • Statische Dokumententypen-Liste bereitstellen
    → Gibt Dict mit allen benötigten Daten zurück oder None bei Fehler

  SCHRITT 2 — PDF-Rendering (_run_ki_io → pdf_service.pdf_to_base64_images)
    • PDF-Seiten werden mit pypdfium2 gerendert (DPI aus Schritt 1)
    • Jede Seite wird als Base64-kodiertes Bild gespeichert
    • Ergebnis: Liste von data:image/...;base64,...-Strings
    → Wird direkt an die KI-API weitergereicht (Vision API)

  SCHRITT 3 — Typ-Entscheidung (drei Pfade je nach Situation)
    ┌─ Pfad A: Typ bereits bekannt und ≠ Eingangsrechnung
    │   → _db_type_only_finish: Status "done", keine Extraktion, return
    │
    ├─ Pfad B: Typ unbekannt (0) + Dokumententyp-Prompt vorhanden
    │   → detect_document_type (KI-Aufruf 1): KI erkennt den Dokumenttyp
    │   → _db_save_document_type: Typ sofort in DB sichern (auch bei späterer Fehler)
    │   → Falls NICHT Eingangsrechnung: _db_type_only_finish, return
    │   → Falls Eingangsrechnung: weiter mit extract_invoice_data (KI-Aufruf 2)
    │
    └─ Pfad C: Typ bekannt = Eingangsrechnung ODER kein Typ-Prompt
        → extract_invoice_data (KI-Aufruf 1): Direkt Rechnungsdaten extrahieren

  SCHRITT 4 — Ergebnisse speichern (_db_analyze_write, läuft in Thread)
    • Phase 1: InvoiceExtraction + OrderPositions + DocumentTokenCount speichern
    • Phase 2: page_count, document_type, status="done" im Document speichern
    • ZWEI getrennte Sessions: Phase-1-Fehler beeinflusst nicht den Status-Commit

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
THREADING-MODELL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Der asyncio Event-Loop darf NIEMALS blockiert werden. Alle blockierenden
Operationen laufen deshalb in Thread-Pools:

  asyncio.to_thread(fn)  — Standard-Thread-Pool für DB-Operationen
  _run_ki_io(fn)         — Dedizierter _KI_IO_EXECUTOR für PDF-Rendering
                           und große Base64-Payloads (verhindert Blockierung
                           des normalen Thread-Pools)

  Synchrone KI-Funktionen (extract_invoice_data, detect_document_type) müssen
  ebenfalls via asyncio.to_thread() aufgerufen werden, da httpx.Client (sync)
  den Event-Loop sonst blockieren würde.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCHUTZ VOR DOPPELVERARBEITUNG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Dasselbe Dokument darf nicht gleichzeitig von mehreren Tasks analysiert werden,
da das zu fehlerhaften DB-Einträgen (doppelte InvoiceExtractions) führt:

  1. _analyzing_docs: set[int]  — In-Memory-Guard, blockiert parallele Aufrufe
                                  für dieselbe doc_id innerhalb dieses Prozesses
  2. Worker (runner.py)         — Prüft doc.status == "processing" → skip
  3. enqueue-Endpunkt           — Prüft auf vorhandene pending/in_progress Tasks

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FEHLERBEHANDLUNG
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KI-Verbindungsfehler (Timeout, 429, 503, Netzwerkfehler):
  → _is_ai_conn_error() erkennt diese am raw_response-Präfix
  → Status wird NICHT auf "error" gesetzt
  → Rückgabe "ai_unavailable" → Worker re-queued den Task (erneuter Versuch)

Sonstige Fehler (PDF-Rendering, DB-Commit, Extraktion):
  → _set_error() setzt status="error" in einer eigenen, frischen Session
  → Dokument bleibt in der DB (kein Verlust), kann manuell erneut analysiert werden
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import crud
from app.database import SessionLocal, get_db
from app.schemas.document import DocumentCommentUpdate, DocumentDetail, DocumentListRead
from app.services import ai_service, pdf_service

logger = logging.getLogger(__name__)

# ─── Dedizierter Thread-Pool für KI-I/O-Operationen ──────────────────────────
#
# Warum ein separater Pool statt des Standard-asyncio-Thread-Pools?
#
# PDF-Rendering und KI-HTTP-Requests erzeugen sehr große Datenmengen (Base64-Bilder
# können 10–50 MB pro Dokument erreichen). Würden diese im Standard-Thread-Pool
# laufen, blockierten sie normale DB-Operationen und verlangsamten das gesamte Backend.
#
# max_workers-Berechnung: 2 × CPU-Kerne, mindestens 4, maximal 16.
# Auf einem NAS mit 4 Kernen = 8 Worker → genug für parallele PDF-Renderings,
# ohne den RAM durch zu viele gleichzeitige große Base64-Puffer zu überlasten.
_KI_IO_EXECUTOR = ThreadPoolExecutor(
    max_workers=min(16, (os.cpu_count() or 4) * 2),
    thread_name_prefix="ki_pdf",  # Sichtbar im Stack-Trace bei Debugging
)

# Alle Endpunkte dieses Routers beginnen mit /api/documents
router = APIRouter(prefix="/api/documents", tags=["Dokumente"])


# ─── Interne Hilfsfunktionen ─────────────────────────────────────────────────


async def _run_ki_io(func, *args):
    """
    Führt eine blockierende I/O-Funktion im dedizierten _KI_IO_EXECUTOR aus
    und wartet asynchron auf das Ergebnis.

    Im Gegensatz zu asyncio.to_thread() verwendet diese Funktion den
    spezialisierten _KI_IO_EXECUTOR, der ausschließlich für große
    Datei-/Netzwerk-Operationen reserviert ist.

    Typische Aufrufer:
      - pdf_service.pdf_to_base64_images() (PDF-Rendering → große Bilder)

    Args:
        func: Die synchrone (blockierende) Funktion, die ausgeführt werden soll.
        *args: Alle Argumente, die an func übergeben werden.

    Returns:
        Was auch immer func zurückgibt.
    """
    # Den aktuell laufenden Event-Loop holen (gibt es immer, da wir in async-Kontext sind)
    loop = asyncio.get_running_loop()
    # func(*args) in einem Thread des _KI_IO_EXECUTOR ausführen.
    # run_in_executor gibt ein Future zurück → await pausiert den Event-Loop
    # bis der Thread fertig ist, blockiert ihn aber NICHT.
    return await loop.run_in_executor(_KI_IO_EXECUTOR, func, *args)


def _set_error(doc_id: int, message: str) -> None:
    """
    Setzt den Status eines Dokuments auf 'error' — öffnet dafür eine EIGENE Session.

    Diese Funktion wird als Notfall-Fallback verwendet, wenn die Haupt-Session
    der aktuellen Analyse-Phase bereits geschlossen oder in einem fehlerhaften
    Zustand ist (z.B. nach einem fehlgeschlagenen Commit).

    Durch die eigene Session ist diese Operation vollständig unabhängig vom
    Rest der Analyse und kann nicht selbst durch Session-Probleme fehlschlagen.

    Args:
        doc_id:  ID des Dokuments, dessen Status geändert werden soll.
        message: Fehlermeldung für das Log (wird nicht in der DB gespeichert).
    """
    try:
        _db = SessionLocal()  # Frische, unbelastete Session öffnen
        try:
            crud.document.update_status(_db, doc_id, "error")
            # Session wird von update_status selbst committed
        finally:
            _db.close()  # Session immer schließen, auch bei Fehler
    except Exception as exc:
        # Selbst dieser letzte Ausweg ist fehlgeschlagen — nur noch loggen
        logger.error("Konnte Fehlerstatus für #%d nicht setzen: %s", doc_id, exc)


# ─── Pydantic-Request/Response-Schemas für diesen Router ─────────────────────


class AnalyzeRequest(BaseModel):
    """
    Request-Body für POST /api/documents/analyze (Legacy-Pfad).

    document_ids:     Liste der Dokument-IDs, die analysiert werden sollen.
    ai_config_id:     Optional — spezifische KI-Konfiguration. None = Standard wählen.
    system_prompt_id: Optional — spezifischer Extraktionsprompt. None = Standard wählen.
    """

    document_ids: list[int]
    ai_config_id: Optional[int] = None
    system_prompt_id: Optional[int] = None


class AnalyzeResponse(BaseModel):
    """Antwort auf POST /api/documents/analyze — gibt an wie viele Analysen gestartet wurden."""

    started: int   # Anzahl der Dokumente, für die die Analyse gestartet wurde
    message: str   # Lesbare Bestätigung (z.B. "KI-Analyse für 3 Dokument(e) gestartet")


class EnqueueRequest(BaseModel):
    """Request-Body für POST /api/documents/enqueue."""

    document_ids: list[int]  # Liste der Dokument-IDs, die in die Warteschlange eingereiht werden sollen


class EnqueueResponse(BaseModel):
    """Antwort auf POST /api/documents/enqueue."""

    enqueued: int   # Anzahl neu eingereihter Tasks
    message: str    # Lesbare Bestätigung inkl. ggf. "N bereits in Warteschlange"


# ─── ENDPUNKTE ────────────────────────────────────────────────────────────────


@router.post("/enqueue", response_model=EnqueueResponse)
def enqueue_documents(payload: EnqueueRequest, db: Session = Depends(get_db)):
    """
    Reiht Dokumente zur KI-Analyse in die Worker-Warteschlange (workflow_tasks) ein.

    Das ist der empfohlene Weg, um eine KI-Analyse zu starten — im Gegensatz
    zum Legacy-Endpunkt POST /analyze läuft die eigentliche Verarbeitung nicht
    im Request-Thread, sondern wird vom Worker-Pool (runner.py) übernommen.

    Vorteile gegenüber /analyze:
      • Worker-Queue überlebt Container-Neustarts
      • Automatische Retry-Logik bei KI-Ausfällen (bis zu max_attempts Versuche)
      • Keine Überlastung bei großen Batches (Worker verarbeitet sequenziell)
      • Doppel-Einreihung wird geprüft (kein doppelter Task für dieselbe doc_id)

    Ablauf für jede Dokument-ID:
      1. Prüfen ob bereits ein aktiver Task vorhanden ist (status = pending/in_progress)
         → Falls ja: überspringen (verhindert doppelte Analyse)
      2. Neuen WorkflowTask anlegen (status=pending, payload={kind, document_id})
      3. Am Ende alle Tasks in einem einzigen Commit in die DB schreiben

    Args:
        payload: EnqueueRequest mit Liste von Dokument-IDs.
        db:      Datenbank-Session (via FastAPI-Dependency injiziert).

    Returns:
        EnqueueResponse mit Anzahl eingereihter Tasks und Statusmeldung.

    Raises:
        HTTPException 400: Wenn keine Dokument-IDs angegeben wurden.
    """
    import uuid
    from sqlalchemy import text as _text
    from app.models.workflow_task import WorkflowTask

    # Mindestens eine ID muss angegeben sein
    if not payload.document_ids:
        raise HTTPException(status_code=400, detail="Keine Dokument-IDs angegeben.")

    count = 0    # Anzahl neu angelegter Tasks
    skipped = 0  # Anzahl übersprungener Dokumente (bereits in Warteschlange)

    for doc_id in payload.document_ids:
        # ── Schritt 1: Prüfen ob bereits ein aktiver Task für dieses Dokument existiert ──
        # Raw SQL für maximale Effizienz — wir prüfen nur auf Existenz (LIMIT 1),
        # ein vollständiger ORM-Load wäre hier unnötig teuer.
        # payload->>'document_id' ist eine JSONB-Abfrage (PostgreSQL-Syntax):
        # Der JSONB-Operator ->> gibt den Wert als Text zurück.
        existing = db.execute(
            _text(
                "SELECT id FROM workflow_tasks "
                "WHERE payload->>'document_id' = :doc_id "
                "AND status IN ('pending', 'in_progress') LIMIT 1"
            ),
            {"doc_id": str(doc_id)},  # doc_id als String, da JSONB Text-Vergleich
        ).first()

        if existing:
            # Task bereits vorhanden → nicht erneut einreihen
            skipped += 1
            logger.debug("Dokument #%d: bereits in Warteschlange — übersprungen", doc_id)
            continue

        # ── Schritt 2: Neuen WorkflowTask anlegen ────────────────────────────
        # workflow_id: UUID zur Gruppierung zusammengehöriger Tasks
        # (hier jeder Task einzeln, da direkt vom Nutzer ausgelöst)
        task = WorkflowTask(
            workflow_id=str(uuid.uuid4()),
            payload={"kind": "process_document", "document_id": doc_id},
            status="pending",   # Worker holt sich pending-Tasks via SKIP LOCKED
        )
        db.add(task)
        count += 1

    # ── Schritt 3: Alle neuen Tasks in EINEM Commit in die DB schreiben ──────
    # Einzel-Commit statt N Commits → deutlich schneller bei großen Listen
    db.commit()

    logger.info("%d Dokument(e) in Worker-Warteschlange gestellt (%d übersprungen)", count, skipped)

    # Lesbare Antwort zusammenbauen
    msg = f"{count} Dokument{'e' if count != 1 else ''} zur KI-Analyse eingereiht"
    if skipped:
        msg += f" ({skipped} bereits in Warteschlange)"
    return EnqueueResponse(enqueued=count, message=msg + ".")


@router.get("", response_model=list[DocumentListRead])
def list_documents(
    # ── Filter-Parameter ────────────────────────────────────────────────────
    company: Optional[str] = None,
    year: Optional[int] = None,
    status: Optional[str] = None,
    total_min: Optional[float] = None,
    total_max: Optional[float] = None,
    page_min: Optional[int] = None,
    page_max: Optional[int] = None,
    batch_ids: Optional[list[int]] = Query(default=None),
    include_deleted: bool = False,
    has_extraction: Optional[bool] = None,
    supplier_name: Optional[str] = None,
    doc_id: Optional[int] = None,
    document_type_ids: Optional[list[int]] = Query(default=None),
    # ── Datenbank-Session ────────────────────────────────────────────────────
    db: Session = Depends(get_db),
):
    """
    Gibt alle Dokumente zurück — optional durch beliebige Kombination von Filtern eingeschränkt.

    Alle Filter sind optional. Werden mehrere angegeben, werden sie mit AND verknüpft.
    Die Abfrage verwendet joinedload für InvoiceExtraction und DocumentType, damit
    keine N+1-Queries entstehen (eine DB-Abfrage für alle Dokumente statt eine pro Dokument).

    Filter-Parameter:
        company:           Firmenname (Teilstring-Suche, aus ImportBatch.company_name)
        year:              Importjahr (exakt, aus ImportBatch.year)
        status:            Dokumentenstatus: "pending" | "processing" | "done" | "error"
        total_min:         Mindest-Bruttobetrag (€) der Rechnung
        total_max:         Maximal-Bruttobetrag (€) der Rechnung
        page_min:          Minimale Seitenanzahl des PDFs
        page_max:          Maximale Seitenanzahl des PDFs
        batch_ids:         Liste von Import-Batch-IDs — nur Dokumente aus diesen Batches
                           (Query-Array: ?batch_ids=1&batch_ids=2)
        include_deleted:   Soft-gelöschte Dokumente einschließen (Standard: false)
        has_extraction:    true = nur Dokumente mit KI-Extraktion,
                           false = nur Dokumente ohne KI-Extraktion
        supplier_name:     Lieferantenname-Suche (Teilstring, auf InvoiceExtraction.vendor_id)
        doc_id:            Exakte Dokument-ID (für Direktsuche)
        document_type_ids: Liste von Dokumententyp-IDs
                           (Query-Array: ?document_type_ids=1&document_type_ids=3)
        db:                Datenbank-Session (via FastAPI-Dependency injiziert).

    Returns:
        Liste von DocumentListRead-Objekten (enthält Extraktion-Summary-Felder).
    """
    # Alle Filter werden an die CRUD-Funktion durchgereicht,
    # die daraus eine optimierte SQL-Abfrage baut.
    return crud.document.get_all_filtered(
        db,
        company=company,
        year=year,
        status=status,
        total_min=total_min,
        total_max=total_max,
        page_min=page_min,
        page_max=page_max,
        batch_ids=batch_ids,
        include_deleted=include_deleted,
        has_extraction=has_extraction,
        supplier_name_filter=supplier_name,
        doc_id=doc_id,
        document_type_ids=document_type_ids,
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze_documents(
    payload: AnalyzeRequest,
    db: Session = Depends(get_db),
):
    """
    Startet die KI-Analyse direkt als Hintergrund-Task (Legacy-Pfad, ohne Worker-Queue).

    ⚠ Bevorzuge POST /enqueue für neue Implementierungen:
      Die Worker-Queue überlebt Container-Neustarts und handhabt KI-Ausfälle robuster.
      Dieser Endpunkt eignet sich für interaktive Einzel-Analysen aus dem Frontend.

    Ablauf:
      1. Sicherheitschecks synchron erledigen (KI-Konfiguration, Dokumente vorhanden)
      2. Alle gültigen Dokumente sofort auf status="processing" setzen
         (zeigt dem Nutzer sofort "wird verarbeitet")
      3. _run_analysis als asyncio.create_task() starten → Request kehrt sofort zurück
         (die eigentliche Analyse läuft im Hintergrund weiter)

    Args:
        payload: AnalyzeRequest mit Dokument-IDs und optionaler KI-Konfiguration.
        db:      Datenbank-Session (via FastAPI-Dependency injiziert).

    Returns:
        AnalyzeResponse mit Anzahl gestarteter Analysen.

    Raises:
        HTTPException 400: Keine Dokument-IDs, keine aktive KI-Konfiguration,
                           oder keine gültigen Dokumente gefunden.
        HTTPException 404: Angegebene KI-Konfiguration existiert nicht.
    """
    # ── Schritt 1: Eingabe prüfen ────────────────────────────────────────────
    if not payload.document_ids:
        raise HTTPException(status_code=400, detail="Keine Dokument-IDs angegeben")

    # ── Schritt 2: KI-Konfiguration bestimmen ────────────────────────────────
    ai_config = None
    if payload.ai_config_id:
        # Explizite KI-ID angegeben → genau diese verwenden
        ai_config = crud.ai_config.get_by_id(db, payload.ai_config_id)
        if ai_config is None:
            raise HTTPException(
                status_code=404,
                detail=f"KI-Konfiguration #{payload.ai_config_id} nicht gefunden"
            )
    else:
        # Keine explizite ID → Standard-KI wählen (zufällig aus aktiven Configs)
        ai_config = crud.ai_config.get_default(db)
        if ai_config is None:
            raise HTTPException(
                status_code=400,
                detail="Keine aktive KI-Konfiguration vorhanden."
            )

    # ── Schritt 3: Extraktions-Systemprompt bestimmen ────────────────────────
    # Der Systemprompt steuert, wie die KI die Rechnungsdaten extrahiert.
    # Er wird hier als reiner Text in den Background-Task übergeben,
    # damit kein Session-Zugriff mehr im Thread nötig ist.
    system_prompt_text: str | None = None
    if payload.system_prompt_id:
        # Expliziter Prompt angegeben → dessen Inhalt laden
        sp = crud.system_prompt.get_by_id(db, payload.system_prompt_id)
        if sp:
            system_prompt_text = sp.content
    else:
        # Kein expliziter Prompt → Standard-Extraktionsprompt (type=1) verwenden
        default_sp = crud.system_prompt.get_default(db)
        if default_sp:
            system_prompt_text = default_sp.content

    # ── Schritt 4: Dokumente validieren und auf "processing" setzen ──────────
    from app.models.document import Document as DocModel
    valid_ids: list[int] = []

    for doc_id in payload.document_ids:
        doc = db.get(DocModel, doc_id)

        if doc is None:
            # Dokument existiert nicht in der DB → überspringen
            logger.warning("Dokument #%d nicht gefunden — übersprungen", doc_id)
            continue

        if not doc.stored_filename:
            # Dokument wurde importiert, aber die PDF-Datei fehlt
            # (kann passieren wenn der Import abgebrochen wurde)
            logger.warning("Dokument #%d hat keine gespeicherte Datei — übersprungen", doc_id)
            continue

        # Dokument ist gültig → zur Analyse-Liste hinzufügen
        valid_ids.append(doc_id)

        # Status sofort auf "processing" setzen, damit das Frontend
        # den Fortschritt anzeigen kann (Spinner statt "ausstehend")
        crud.document.update_status(db, doc_id, "processing")

    if not valid_ids:
        raise HTTPException(status_code=400, detail="Keine gültigen Dokumente gefunden")

    # ── Schritt 5: Analyse als Background-Task starten ───────────────────────
    # ai_config.id hier sichern, BEVOR die Session (db) geschlossen wird.
    # Die Session wird nach dem Request-Ende von FastAPI geschlossen —
    # das SQLAlchemy-Objekt (ai_config) wäre danach "detached" und nicht mehr lesbar.
    ai_config_id = ai_config.id

    # create_task() registriert die Coroutine im laufenden Event-Loop
    # und startet sie beim nächsten Loop-Zyklus.
    # Der HTTP-Request kehrt sofort zurück — die Analyse läuft im Hintergrund.
    asyncio.create_task(_run_analysis(
        document_ids=valid_ids,
        ai_config_id=ai_config_id,
        system_prompt_text=system_prompt_text,
    ))

    return AnalyzeResponse(
        started=len(valid_ids),
        message=f"KI-Analyse für {len(valid_ids)} Dokument(e) gestartet"
    )


# ─── Statische Dokumententypen-Liste ─────────────────────────────────────────
#
# Diese Liste wird bei jeder Analyse an detect_document_type() übergeben,
# damit die KI weiß, welche Typen sie erkennen soll.
# ID=1 (Eingangsrechnung) hat Sonderstatus: nur für diesen Typ läuft extract_invoice_data().
# Alle anderen Typen werden durch _db_type_only_finish() ohne weitere Extraktion abgeschlossen.
_DOCUMENT_TYPE_LIST = [
    {"id": 0,  "name": "Unbekannt"},
    {"id": 1,  "name": "Eingangsrechnung"},
    {"id": 2,  "name": "Ausgangsrechnung"},
    {"id": 3,  "name": "Lieferschein"},
    {"id": 4,  "name": "Bestellbestätigung"},
    {"id": 5,  "name": "Angebot"},
    {"id": 6,  "name": "Gutschrift / Storno"},
    {"id": 7,  "name": "Mahnung"},
    {"id": 8,  "name": "Kontoauszug"},
    {"id": 9,  "name": "Vertrag"},
    {"id": 10, "name": "Lohnabrechnung"},
    {"id": 11, "name": "Steuer- / Behördendokument"},
    {"id": 12, "name": "Reisekostenabrechnung"},
    {"id": 13, "name": "Kassenbon / Quittung"},
    {"id": 14, "name": "Sonstiges kaufmännisches Dokument"},
]


# ─── Analyse-Pipeline: DB-Phasen und Hilfsfunktionen ─────────────────────────


def _db_analyze_read(doc_id: int, ai_config_id: int) -> dict | None:
    """
    ANALYSE-SCHRITT 1: Alle benötigten Daten aus der DB lesen.

    Diese Funktion läuft SYNCHRON in einem Thread (via asyncio.to_thread),
    da SQLAlchemy-Sessions nicht thread-sicher über Koroutinen hinweg verwendet
    werden können. Sie öffnet eine eigene Session und schließt sie selbst.

    Was wird geladen:
      • Dokument-Datensatz (inkl. Batch via joinedload für Pfad-Auflösung)
      • KI-Konfiguration (Verbindungsdaten, Modell, Einstellungen)
      • Bildkonvertierungseinstellungen (DPI, Format, JPEG-Qualität)
      • Dokumententyp-Prompt (type=0) — dessen Existenz entscheidet,
        ob zweistufige Analyse (Typ-Erkennung + Extraktion) oder einstufig
      • Bekannte Dokumententypen (statische Liste für KI-Prompt)

    Warum joinedload für batch?
      Das Dokument hat eine Fremdschlüsselbeziehung zu ImportBatch.
      Ohne joinedload würde SQLAlchemy beim Zugriff auf doc.batch eine
      zweite SELECT-Anfrage auslösen (lazy loading) — joinedload kombiniert
      beide Abfragen in einem JOIN, spart einen DB-Roundtrip.

    Args:
        doc_id:       ID des zu analysierenden Dokuments.
        ai_config_id: ID der KI-Konfiguration, die verwendet werden soll.

    Returns:
        Dict mit allen Analyse-Parametern ODER None bei einem Fehler:
          pdf_path:              Absoluter Pfad zur PDF-Datei (Path-Objekt)
          original_filename:     Ursprünglicher Dateiname (für Logs)
          batch_id:              ID des Import-Batches (für _db_analyze_write)
          img_dpi:               Render-Auflösung in DPI
          img_format:            Bildformat ("PNG" oder "JPEG")
          img_quality:           JPEG-Kompressionsqualität (1–100)
          ai_api_url:            Vollständige API-URL (http://ip:port)
          ai_api_key:            API-Schlüssel (kann None sein)
          ai_model_name:         Modellbezeichnung (z.B. "llava-v1.6")
          ai_max_tokens:         Maximale Ausgabe-Token
          ai_temperature:        Kreativitätswert (0.0 = deterministisch)
          ai_reasoning:          Reasoning-Stufe ("off"|"low"|"medium"|"high"|"on")
          ai_endpoint_type:      API-Protokoll ("openai" oder "lmstudio")
          doc_type_prompt_text:  Inhalt des Typ-Erkennungs-Prompts (None = einstufig)
          document_types:        Liste aller bekannten Dokumententypen
          existing_document_type: Bereits gespeicherter Typ (0 = noch nicht erkannt)
    """
    from app.models.document import Document as DocModel
    db = SessionLocal()  # Eigene Session für diesen Thread
    try:
        from sqlalchemy.orm import joinedload

        # Dokument laden — joinedload(batch) für Pfad-Auflösung in einem einzigen JOIN
        doc = (
            db.query(DocModel)
            .options(joinedload(DocModel.batch))
            .filter(DocModel.id == doc_id)
            .first()
        )

        if doc is None:
            # Sollte nicht vorkommen (Validierung in analyze_documents),
            # aber defensiv prüfen — Race Condition möglich (Dokument gelöscht)
            logger.error("Dokument #%d nicht in DB gefunden", doc_id)
            return None

        # KI-Konfiguration laden
        ai_config = crud.ai_config.get_by_id(db, ai_config_id)
        if ai_config is None:
            # KI-Config wurde zwischen Anfrage und Analyse-Start gelöscht
            logger.error("KI-Konfiguration #%d nicht in DB gefunden", ai_config_id)
            crud.document.update_status(db, doc_id, "error")
            return None

        # PDF-Pfad aus Batch-Metadaten ableiten:
        # storage_folder_path = STORAGE_PATH/Firma_Jahr/
        # stored_filename = {id}.pdf (die kopierte Datei)
        storage_path = doc.batch.storage_folder_path if doc.batch else ""
        pdf_path = Path(storage_path) / doc.stored_filename

        if not pdf_path.exists():
            # PDF-Datei fehlt auf dem Dateisystem (z.B. manuell gelöscht)
            logger.error("PDF nicht gefunden: %s", pdf_path)
            crud.document.update_status(db, doc_id, "error")
            return None

        # Bildkonvertierungseinstellungen aus DB laden (Singleton get_or_create)
        img_settings = crud.image_settings.get_or_create(db)

        # Dokumententyp-Erkennungs-Prompt laden (type=0 in system_prompts).
        # Wenn None → kein zweistufiger Modus → direkt Extraktion ohne Typ-Erkennung.
        doc_type_prompt = crud.system_prompt.get_doc_type_prompt(db)

        # API-URL aus IP + Port zusammensetzen.
        # Das Modell speichert IP und Port getrennt, damit beides einzeln änderbar ist.
        ip = ai_config.ip_address
        port = ai_config.port
        api_url = f"http://{ip}:{port}" if port else ip

        return {
            "pdf_path":              pdf_path,
            "original_filename":     doc.original_filename,
            "batch_id":              doc.batch_id,
            "img_dpi":               img_settings.dpi,
            "img_format":            img_settings.image_format,
            "img_quality":           img_settings.jpeg_quality,
            "ai_api_url":            api_url,
            "ai_api_key":            ai_config.api_key,
            "ai_model_name":         ai_config.model_name,
            "ai_max_tokens":         ai_config.max_tokens,
            "ai_temperature":        ai_config.temperature,
            "ai_reasoning":          ai_config.reasoning or "off",
            "ai_endpoint_type":      ai_config.endpoint_type or "openai",
            "doc_type_prompt_text":  doc_type_prompt.content if doc_type_prompt else None,
            "document_types":        _DOCUMENT_TYPE_LIST,
            # Bereits gespeicherter Typ: 0 = unbekannt/noch nicht erkannt,
            # 1 = Eingangsrechnung, 2+ = anderer Typ
            "existing_document_type": doc.document_type or 0,
        }

    except Exception as exc:
        # Unerwarteter Fehler (z.B. DB-Verbindungsabbruch) → loggen, zurückrollen, abbrechen
        logger.exception("Phase 1 DB-Fehler bei Dokument #%d: %s", doc_id, exc)
        try:
            db.rollback()
            crud.document.update_status(db, doc_id, "error")
        except Exception:
            pass  # Auch das Setzen des Fehler-Status ist fehlgeschlagen → ignorieren
        return None
    finally:
        db.close()  # Session IMMER schließen — auch bei Exceptions (kein Connection-Leak)


def _db_save_document_type(doc_id: int, type_id: int | None) -> None:
    """
    ANALYSE-SCHRITT 3b (Zwischenspeicherung): Erkannten Dokumententyp sofort in der DB sichern.

    Diese Funktion wird UNMITTELBAR nach detect_document_type() aufgerufen,
    BEVOR extract_invoice_data() startet. Das hat einen wichtigen Grund:

    Wenn die Extraktion später fehlschlägt (z.B. KI-Timeout), ist der erkannte
    Dokumententyp trotzdem bereits in der DB gespeichert. Ohne diesen Zwischenspeicher
    würde der Typ bei einem Fehler verloren gehen.

    Diese Funktion öffnet ihre eigene Session, da sie via asyncio.to_thread()
    aus dem async-Kontext aufgerufen wird und die Session nicht thread-sicher
    über Koroutinen hinweg geteilt werden kann.

    Args:
        doc_id:  ID des Dokuments, dessen Typ gespeichert werden soll.
        type_id: Erkannter Dokumententyp-ID (None wenn KI keinen Typ erkennen konnte).
    """
    db = SessionLocal()  # Eigene Session für atomaren Commit
    try:
        crud.document.update_document_type(db, doc_id, type_id)
        # update_document_type committed intern selbst
    except Exception as exc:
        logger.error("Fehler beim Speichern des Dokumententyps für #%d: %s", doc_id, exc)
    finally:
        db.close()


def _db_type_only_finish(
    doc_id: int,
    type_id: int | None,
    type_name: str | None,
    page_count: int,
    batch_id: int | None,
    original_filename: str,
    ki_stats: dict | None = None,
) -> None:
    """
    ANALYSE-ABSCHLUSS für Nicht-Eingangsrechnungen (kein InvoiceExtraction-Eintrag).

    Wird aufgerufen wenn:
      a) Der erkannte Dokumententyp ≠ Eingangsrechnung (type_id ≠ 1)
      b) Der Dokumententyp bereits bekannt war und keine Extraktion nötig ist

    Aufgaben dieser Funktion:
      1. document.document_type auf den erkannten Typ setzen
      2. document.page_count auf die tatsächliche Seitenanzahl setzen
      3. document.status auf "done" setzen
      4. KI-Token-Statistiken der Typ-Erkennung als DocumentTokenCount speichern
         (falls ki_stats übergeben wurde — kann None sein wenn Typ bereits bekannt war)

    Warum keine InvoiceExtraction?
      Eine vollständige Rechnungsextraktion macht nur für Eingangsrechnungen Sinn.
      Für Lieferscheine, Mahnungen etc. werden keine Rechnungsfelder extrahiert,
      aber die KI-Kosten (Token-Verbrauch) werden trotzdem gespeichert.

    Args:
        doc_id:            ID des abzuschließenden Dokuments.
        type_id:           Erkannter Dokumententyp-ID (None = unbekannt).
        type_name:         Name des Typs für den Log (z.B. "Lieferschein").
        page_count:        Seitenanzahl des PDFs (aus len(images_b64)).
        batch_id:          ID des Import-Batches (aktuell nur für künftige Nutzung).
        original_filename: Ursprünglicher Dateiname (aktuell nur für künftige Nutzung).
        ki_stats:          Token-Statistiken aus detect_document_type()
                           (None wenn Typ bereits bekannt → kein KI-Aufruf nötig).
    """
    from app.models.document import Document as _DocModel
    from app.models.document_token_count import DocumentTokenCount

    db = SessionLocal()
    try:
        # ── Schritt 1: Dokument-Metadaten aktualisieren ──────────────────────
        doc = db.get(_DocModel, doc_id)
        if doc is not None:
            doc.document_type = type_id or 0   # 0 = Unbekannt als Fallback
            if page_count > 0:
                # Seitenanzahl nur setzen wenn bekannt (>0 bedeutet PDF wurde gerendert)
                doc.page_count = page_count
            doc.status = "done"   # Analyse abgeschlossen — kein Fehler
            db.commit()

        # ── Schritt 2: KI-Token-Statistiken speichern (falls vorhanden) ──────
        if ki_stats:
            # DocumentTokenCount ist 1:N zu Document — jeder KI-Aufruf wird
            # als eigener Eintrag gespeichert (niemals überschreiben, immer anfügen).
            # Das ermöglicht vollständige Nachvollziehbarkeit aller KI-Kosten.
            tc = DocumentTokenCount(
                document_id=doc_id,
                input_token_count=(ki_stats.get("input_tokens") or 0),
                output_token_count=(ki_stats.get("output_tokens") or 0),
                reasoning_count=ki_stats.get("reasoning_tokens") or 0,
                time_spent_seconds=ki_stats.get("total_duration") or 0.0,
            )
            db.add(tc)
            try:
                db.commit()
            except Exception:
                # Token-Statistiken sind nicht kritisch — Commit-Fehler ignorieren
                db.rollback()

        logger.info(
            "Dokument #%d: Typ erkannt als '%s' — keine Rechnungsextraktion",
            doc_id, type_name
        )

    except Exception as exc:
        logger.error("Fehler beim Abschluss ohne Extraktion für #%d: %s", doc_id, exc)
        try:
            db.rollback()
            # Letzter Ausweg: _set_error öffnet eine eigene Session
            _set_error(doc_id, f"Abschlussfehler: {exc}")
        except Exception:
            pass
    finally:
        db.close()


def _merge_ki_stats(stats1: dict, stats2: dict) -> dict:
    """
    Summiert die Token-Statistiken aus zwei KI-Aufrufen zu einer kombinierten Statistik.

    Wird verwendet um die Kosten aus Typ-Erkennung (detect_document_type) und
    Rechnungsextraktion (extract_invoice_data) zu einer Gesamtstatistik zusammenzufassen,
    die in DocumentTokenCount gespeichert wird.

    Summen-Felder (werden addiert):
      input_tokens:     Eingabe-Token beider Aufrufe zusammen
      output_tokens:    Ausgabe-Token beider Aufrufe zusammen
      reasoning_tokens: Reasoning-Token beider Aufrufe (nur bestimmte Modelle)
      total_duration:   Gesamtdauer beider KI-Aufrufe in Sekunden

    Einzelwert-Felder (nicht summierbar, Priorisierung):
      tokens_per_second:   Wert aus stats2 (Extraktion) bevorzugt, da aussagekräftiger.
                           Die Typ-Erkennung ist zu kurz für eine sinnvolle Messung.
      time_to_first_token: Wert aus stats1 (Typ-Erkennung) — zeitlich der erste Aufruf.

    None-Semantik:
      Wenn BEIDE Felder None sind → Ergebnis ist None (Modell liefert keine Stats).
      Wenn EIN Feld None ist → wird als 0 behandelt (partieller Support).

    Args:
        stats1: Statistiken des ersten KI-Aufrufs (Typ-Erkennung).
        stats2: Statistiken des zweiten KI-Aufrufs (Rechnungsextraktion).

    Returns:
        Neues dict mit den kombinierten Statistiken.
    """
    # Hilfsfunktion zum sicheren Addieren: None + None = None, sonst Summe
    def _add(a, b):
        if a is None and b is None:
            return None          # Beide nicht vorhanden → kein Modell-Support
        return (a or 0) + (b or 0)  # None als 0 behandeln wenn nur einer fehlt

    return {
        # Addierbare Zähler
        "input_tokens":        _add(stats1.get("input_tokens"),     stats2.get("input_tokens")),
        "output_tokens":       _add(stats1.get("output_tokens"),    stats2.get("output_tokens")),
        "reasoning_tokens":    _add(stats1.get("reasoning_tokens"), stats2.get("reasoning_tokens")),
        # Geschwindigkeit: stats2 bevorzugen (Extraktion ist länger → representativer)
        "tokens_per_second":   stats2.get("tokens_per_second") or stats1.get("tokens_per_second"),
        # Time-to-First-Token: stats1 bevorzugen (zeitlich erster Aufruf)
        "time_to_first_token": stats1.get("time_to_first_token"),
        # Gesamtdauer: beide addieren
        "total_duration":      _add(stats1.get("total_duration"),   stats2.get("total_duration")),
    }


def _db_analyze_write(
    doc_id: int,
    original_filename: str,
    batch_id: int | None,
    ai_model_name: str,
    page_count: int,
    extracted_fields: dict,
    order_positions: list,
    raw_response: str,
    ki_stats: dict | None = None,
    document_type_id: int | None = None,
) -> None:
    """
    ANALYSE-SCHRITT 4: KI-Ergebnisse in der DB speichern und Dokument abschließen.

    Diese Funktion verwendet ZWEI getrennte Datenbank-Sessions für zwei unabhängige
    Schreibphasen. Das ist bewusst so entworfen:

    Warum zwei Sessions?
      save_extraction() (Phase 1) kann intern mehrere Commits und Rollbacks
      durchführen (z.B. Retry ohne KI-Stats-Felder bei fehlender Migration).
      Wenn Phase 1 fehlschlägt und die Session in einem rollback-Zustand ist,
      würde Phase 2 (Status-Update) ebenfalls fehlschlagen — das Dokument bliebe
      für immer im Status "processing".

      Durch zwei getrennte Sessions ist Phase 2 vollständig unabhängig und
      setzt den Status in jedem Fall (done oder error), selbst wenn Phase 1 fehltschlug.

    Phase 1 — Extraktionsdaten speichern (db1):
      • InvoiceExtraction-Datensatz anlegen oder aktualisieren
      • OrderPosition-Einträge anlegen (alle alten für dieses Dokument werden zuerst gelöscht)
      • DocumentTokenCount-Eintrag anfügen (Token-Statistiken dieses KI-Durchlaufs)

    Phase 2 — Dokument-Metadaten und Status (db2):
      • page_count aktualisieren (endgültige Seitenanzahl aus dem Rendering)
      • document_type setzen (falls durch Typ-Erkennung ermittelt)
      • status auf "done" oder "error" setzen
        → "error" wenn raw_response mit einem KI-Fehler-Präfix beginnt
           ODER wenn Phase 1 fehlschlug

    Args:
        doc_id:            ID des zu aktualisierenden Dokuments.
        original_filename: Ursprünglicher Dateiname (für Logs).
        batch_id:          ID des Import-Batches (für künftige Nutzung).
        ai_model_name:     Verwendetes KI-Modell (für Logs).
        page_count:        Anzahl gerenderter PDF-Seiten.
        extracted_fields:  Dict mit extrahierten Rechnungsfeldern von der KI.
        order_positions:   Liste von Position-Dicts (Artikelpositionen der Rechnung).
        raw_response:      Vollständige KI-Antwort als String (JSON oder Fehlertext).
        ki_stats:          Token-Statistiken aus dem KI-Aufruf (kann None sein).
        document_type_id:  Erkannter Dokumententyp (None wenn nicht ermittelt).
    """
    # ── Phase 1: Extraktion speichern (eigene Session) ────────────────────────
    db1 = SessionLocal()
    extraction_ok = False   # Merker: War Phase 1 erfolgreich?
    try:
        crud.document.save_extraction(
            db=db1,
            doc_id=doc_id,
            extracted_data=extracted_fields,
            positions=order_positions,
            raw_response=raw_response,
            ki_stats=ki_stats,
        )
        extraction_ok = True   # Nur True wenn save_extraction ohne Exception durchläuft

    except Exception as exc:
        logger.exception("Phase 4 (Extraktion) DB-Fehler bei Dokument #%d: %s", doc_id, exc)
        try:
            db1.rollback()   # Session-Zustand aufräumen bevor Session geschlossen wird
        except Exception:
            pass
    finally:
        db1.close()   # Session IMMER schließen (verhindert Connection-Leak)

    # ── Phase 2: Dokument-Metadaten + Status (frische Session) ───────────────
    # Herausfinden ob raw_response auf einen KI-Verbindungsfehler hindeutet.
    # Diese Prefixe werden in ai_service.py gesetzt wenn die KI nicht erreichbar ist.
    _raw = raw_response or ""   # None-sicher für startswith-Check
    is_ki_error = any(_raw.startswith(p) for p in (
        "KI überlastet:",        # HTTP 429 (Rate Limit)
        "KI-Fehler:",            # HTTP 4xx/5xx
        "KI-Timeout",            # Verbindungs-Timeout
        "KI-Verbindungsfehler",  # Netzwerk-Fehler
        "Unerwarteter KI-Fehler", # Sonstige Ausnahmen in ai_service
    ))

    # Status bestimmen: "done" nur wenn BEIDE Phasen erfolgreich waren
    # und die KI keine Fehlermeldung zurückgegeben hat.
    final_status = "error" if (is_ki_error or not extraction_ok) else "done"

    from app.models.document import Document as _DocModel
    db2 = SessionLocal()   # Frische, unbelastete Session
    try:
        _doc = db2.get(_DocModel, doc_id)
        if _doc is not None:
            if page_count > 0:
                # Seitenanzahl war beim Import noch 0 (kein Rendering beim Kopieren)
                # — jetzt wird der echte Wert aus dem PDF gesetzt
                _doc.page_count = page_count

            if document_type_id is not None:
                # Falls durch zweistufige Analyse ein Typ erkannt wurde
                _doc.document_type = document_type_id

            _doc.status = final_status   # "done" oder "error"
            db2.commit()   # Alles in einem Commit → atomare Aktualisierung

        # Abschluss-Log je nach Status
        if final_status == "done":
            logger.info(
                "Dokument #%d erfolgreich analysiert (%d Seiten)",
                doc_id, page_count
            )
        else:
            logger.warning(
                "Dokument #%d: Status=%s — %s",
                doc_id, final_status, raw_response[:120]
            )

    except Exception as exc:
        logger.exception("Phase 4 (Status) DB-Fehler bei Dokument #%d: %s", doc_id, exc)
        try:
            db2.rollback()
        except Exception:
            pass
        # Absoluter letzter Ausweg: _set_error mit eigener 3. Session
        _set_error(doc_id, f"Speicherfehler: {exc}")
    finally:
        db2.close()


# ─── Analyse-Orchestrierung ───────────────────────────────────────────────────


async def _run_analysis(
    document_ids: list[int],
    ai_config_id: int,
    system_prompt_text: str | None,
) -> None:
    """
    Analysiert eine Liste von Dokumenten SEQUENZIELL (eines nach dem anderen).

    Warum sequenziell statt parallel (asyncio.gather)?

      Lokale LLMs (LM Studio, Ollama) verarbeiten ohnehin nur EINE Anfrage
      gleichzeitig. Würden wir parallel senden, würden alle Anfragen außer der
      ersten queued warten — der Thread-Pool würde aber trotzdem belastet.

      Auf einem NAS mit begrenztem RAM führt paralleles Rendering mehrerer
      PDFs (jedes 10–50 MB Base64) schnell zu Speicherengpässen.

      Sequenziell: ein PDF zur Zeit → kontrollierter RAM-Verbrauch,
      vorhersehbarer Ablauf, einfacheres Debugging.

    Fehlerbehandlung:
      Jedes Dokument wird unabhängig verarbeitet. Schlägt ein Dokument fehl,
      wird sein Status auf "error" gesetzt und die nächsten Dokumente werden
      trotzdem analysiert (kein Abbruch der gesamten Liste).

    Args:
        document_ids:       Liste der zu analysierenden Dokument-IDs (in Reihenfolge).
        ai_config_id:       ID der KI-Konfiguration für alle Dokumente dieser Liste.
        system_prompt_text: Inhalt des Extraktionsprompts (oder None für Standard).
    """
    logger.info("KI-Analyse gestartet: %d Dokument(e)", len(document_ids))

    for doc_id in document_ids:
        try:
            # Ein Dokument vollständig analysieren bevor das nächste startet
            await _analyze_single(doc_id, ai_config_id, system_prompt_text)
        except Exception as exc:
            # Unerwarteter Fehler außerhalb der normalen Fehlerbehandlung
            # (sollte nicht vorkommen, aber sicherheitshalber abgefangen)
            logger.exception(
                "Unbehandelter Fehler bei Dokument #%d: %s", doc_id, exc
            )
            # asyncio.to_thread statt direktem _set_error-Aufruf, da wir
            # im async-Kontext sind und keine sync DB-Operation direkt aufrufen sollten
            await asyncio.to_thread(_set_error, doc_id, f"Unerwarteter Fehler: {exc}")

    logger.info("KI-Analyse abgeschlossen (%d Dokumente)", len(document_ids))


# ─── KI-Fehler-Erkennung und Doppelverarbeitungs-Schutz ──────────────────────

# Präfixe in raw_response, die auf einen nicht erreichbaren KI-Endpunkt hinweisen.
# Diese Präfixe werden in ai_service.py gesetzt (extract_invoice_data / detect_document_type)
# wenn der HTTP-Request zur KI-API fehlschlägt.
# Bei diesen Fehlern soll das Dokument NICHT auf "error" gesetzt werden,
# damit der Worker-Task erneut versucht werden kann (re-queue statt fail).
_AI_CONN_ERROR_PREFIXES = (
    "KI-Verbindungsfehler",   # Connection refused, DNS-Fehler
    "KI-Timeout",              # Request-Timeout überschritten
    "KI überlastet:",          # HTTP 429 Too Many Requests
)

# In-Memory-Set aller Dokument-IDs, die gerade aktiv in _analyze_single_inner
# verarbeitet werden. Verhindert, dass dasselbe Dokument durch zwei gleichzeitige
# Aufrufe (z.B. Worker-Queue + direkter Analyze-Endpunkt) doppelt verarbeitet wird.
# Wird in _analyze_single via add() und discard() verwaltet.
# ACHTUNG: Dieser Schutz gilt nur innerhalb EINES Prozesses.
# Bei mehreren Backend-Instanzen ist ein zusätzlicher DB-Level-Lock nötig.
_analyzing_docs: set[int] = set()


def _is_ai_conn_error(raw: str | None) -> bool:
    """
    Prüft ob die KI-Antwort auf einen Verbindungsfehler zur KI-API hindeutet.

    Wird nach jedem KI-Aufruf geprüft. Gibt True zurück wenn der raw_response-String
    mit einem der bekannten Fehler-Präfixe beginnt (z.B. "KI-Verbindungsfehler: ...").

    Wichtig für die Fehlerbehandlung:
      True  → Dokument NICHT auf "error" setzen → Worker kann erneut versuchen
      False → Normales Verhalten (done oder error je nach Extraktion)

    Args:
        raw: Die rohe KI-Antwort (oder Fehlermeldung bei Verbindungsproblem).
             Kann None sein wenn der Aufruf gar keine Antwort zurückgegeben hat.

    Returns:
        True wenn ein Verbindungsfehler erkannt wurde, sonst False.
    """
    return bool(raw and any(raw.startswith(p) for p in _AI_CONN_ERROR_PREFIXES))


async def _analyze_single(
    doc_id: int,
    ai_config_id: int,
    system_prompt_text: str | None,
) -> str:
    """
    Schutzschicht um _analyze_single_inner: verhindert parallele Doppelverarbeitung.

    Bevor die eigentliche Analyse startet, wird doc_id in _analyzing_docs eingetragen.
    Falls die ID bereits vorhanden ist → sofort abbrechen (anderer Task verarbeitet
    gerade dieses Dokument).

    Das finally-Block stellt sicher, dass die ID aus dem Set entfernt wird,
    selbst wenn _analyze_single_inner eine unbehandelte Exception wirft.

    Args:
        doc_id:             ID des zu analysierenden Dokuments.
        ai_config_id:       ID der zu verwendenden KI-Konfiguration.
        system_prompt_text: Inhalt des Extraktionsprompts (None = Standard-Prompt).

    Returns:
        "ok"             — Analyse abgeschlossen (auch wenn das Dokument einen Fehler hat)
        "ai_unavailable" — KI-Endpunkt nicht erreichbar (Task soll re-queued werden)
    """
    if doc_id in _analyzing_docs:
        # Schutz: Dasselbe Dokument wird bereits verarbeitet
        # Dieser Fall tritt auf wenn Worker + direkter Analyze-Endpunkt gleichzeitig
        # dasselbe Dokument verarbeiten wollen.
        logger.warning(
            "Dokument #%d wird bereits analysiert (paralleler Aufruf) — übersprungen",
            doc_id
        )
        return "ok"

    # Dokument als "in Verarbeitung" markieren
    _analyzing_docs.add(doc_id)
    try:
        # Eigentliche Analyse ausführen
        return await _analyze_single_inner(doc_id, ai_config_id, system_prompt_text)
    finally:
        # Auch bei Exceptions: Dokument aus dem Schutz-Set entfernen,
        # damit es später erneut analysiert werden kann
        _analyzing_docs.discard(doc_id)


async def _analyze_single_inner(
    doc_id: int,
    ai_config_id: int,
    system_prompt_text: str | None,
) -> str:
    """
    Eigentliche KI-Analyse-Logik für ein einzelnes Dokument (nach dem Duplikat-Check).

    Diese Funktion durchläuft die vollständige Analyse-Pipeline:
      Schritt 1 → Schritt 2 → Schritt 3 (Pfad A, B oder C) → Schritt 4

    Die Entscheidung zwischen den Pfaden (A/B/C) basiert auf zwei Kriterien:
      • Ist der Dokumententyp bereits bekannt (existing_document_type > 0)?
      • Gibt es einen Dokumententyp-Prompt in der DB?

    Args:
        doc_id:             ID des zu analysierenden Dokuments.
        ai_config_id:       ID der zu verwendenden KI-Konfiguration.
        system_prompt_text: Inhalt des Extraktionsprompts (None = kein Prompt).

    Returns:
        "ok"             — Analyse abgeschlossen (Dokument ist jetzt done oder error)
        "ai_unavailable" — KI-Endpunkt nicht erreichbar (Task wird re-queued)
    """
    # ═══════════════════════════════════════════════════════════════════════════
    # ANALYSE-SCHRITT 1: DB-Daten lesen
    # ═══════════════════════════════════════════════════════════════════════════
    # asyncio.to_thread führt die synchrone _db_analyze_read-Funktion in einem
    # separaten Thread aus, ohne den Event-Loop zu blockieren.
    data = await asyncio.to_thread(_db_analyze_read, doc_id, ai_config_id)

    if data is None:
        # _db_analyze_read hat bereits den Status auf "error" gesetzt und geloggt
        return "ok"

    # Ergebnisse aus dem DB-Read-Dict auspacken
    pdf_path: Path = data["pdf_path"]
    original_filename: str = data["original_filename"]
    batch_id: int | None = data["batch_id"]
    doc_type_prompt_text: str | None = data["doc_type_prompt_text"]
    document_types: list[dict] = data["document_types"]

    # ═══════════════════════════════════════════════════════════════════════════
    # ANALYSE-SCHRITT 2: PDF in Bilder umwandeln (Rendering)
    # ═══════════════════════════════════════════════════════════════════════════
    logger.info("Rendere PDF für Dokument #%d: %s", doc_id, pdf_path.name)
    try:
        # _run_ki_io verwendet den _KI_IO_EXECUTOR (nicht den Standard-Thread-Pool),
        # um große Base64-Puffer nicht im normalen DB-Thread-Pool zu erzeugen.
        # pdf_to_base64_images: synchron, blockierend, gibt Liste von Base64-Strings zurück.
        images_b64: list = await _run_ki_io(
            pdf_service.pdf_to_base64_images,
            pdf_path,             # Absoluter Pfad zur PDF-Datei
            data["img_dpi"],      # Render-Auflösung (Standard: 150 DPI)
            data["img_format"],   # Bildformat ("PNG" oder "JPEG")
            data["img_quality"],  # JPEG-Qualität (Standard: 85, nur bei JPEG relevant)
        )
    except Exception as exc:
        logger.error("Fehler beim Rendern von #%d: %s", doc_id, exc)
        _set_error(doc_id, f"PDF-Rendering-Fehler: {exc}")
        return "ok"

    if not images_b64:
        # Leere Liste = pypdfium2 konnte das PDF nicht lesen
        # (z.B. verschlüsselt, korrupt oder kein gültiges PDF)
        _set_error(doc_id, "PDF konnte nicht gerendert werden")
        return "ok"

    # Seitenanzahl aus den gerenderten Bildern ableiten (ein Bild = eine Seite).
    # Dies ist der finale, korrekte Wert — beim Import wurde page_count=0 gesetzt.
    page_count = len(images_b64)

    # ─── Config-Proxy ────────────────────────────────────────────────────────
    # ai_service.extract_invoice_data() und detect_document_type() erwarten ein
    # Objekt mit bestimmten Attributen (statt eines Dicts).
    # Dieser Mini-Proxy bildet das DB-Modell AIClients nach, ohne SQLAlchemy
    # oder eine DB-Session zu benötigen (alles bereits aus Phase 1 geladen).
    class _ConfigProxy:
        def __init__(self):
            self.api_url       = data["ai_api_url"]
            self.api_key       = data["ai_api_key"]
            self.model_name    = data["ai_model_name"]
            self.max_tokens    = data["ai_max_tokens"]
            self.temperature   = data["ai_temperature"]
            self.reasoning     = data["ai_reasoning"]
            self.endpoint_type = data["ai_endpoint_type"]

    config_proxy = _ConfigProxy()

    # Bereits gespeicherter Typ aus der DB (0 = unbekannt/noch nie erkannt)
    existing_document_type: int = data.get("existing_document_type", 0)
    document_type_id: int | None = None  # Wird im Verlauf gesetzt

    # ═══════════════════════════════════════════════════════════════════════════
    # ANALYSE-SCHRITT 3: Typ-Entscheidung und KI-Aufruf(e)
    # ═══════════════════════════════════════════════════════════════════════════
    #
    # Drei mögliche Pfade je nach Situation:
    #
    #   Pfad A: Typ bereits bekannt UND kein Eingangsrechnungs-Typ
    #           → Direkt abschließen, kein weiterer KI-Aufruf nötig
    #
    #   Pfad B: Typ unbekannt (0) UND Typ-Erkennungs-Prompt vorhanden
    #           → Zweistufig: erst Typ erkennen, dann ggf. Rechnungsdaten extrahieren
    #
    #   Pfad C: Typ bereits als Eingangsrechnung (1) bekannt ODER kein Typ-Prompt
    #           → Direkt Rechnungsdaten extrahieren (einstufig)

    # ─── PFAD A: Typ bereits bekannt, kein weiterer KI-Aufruf nötig ──────────
    if existing_document_type > 1:
        # existing_document_type > 1 bedeutet: ein spezifischer Nicht-Eingangsrechnungs-Typ
        # wurde bereits in einem früheren Durchlauf gespeichert (z.B. "Lieferschein")
        # → Keine erneute Typ-Erkennung, keine Rechnungsextraktion nötig.
        # Typischer Fall: Dokument wird manuell ein zweites Mal analysiert.
        type_name = next(
            (t["name"] for t in document_types if t["id"] == existing_document_type),
            "Unbekannt",  # Fallback wenn ID nicht in der Liste
        )
        logger.info(
            "Dokument #%d: Typ bereits bekannt (%d – %s) — überspringe KI-Erkennung",
            doc_id, existing_document_type, type_name,
        )
        del images_b64   # Base64-Bilder freigeben (kein KI-Aufruf → nicht mehr gebraucht)
        await asyncio.to_thread(
            _db_type_only_finish,
            doc_id, existing_document_type, type_name, page_count,
            batch_id, original_filename,
            None,   # Keine ki_stats (kein KI-Aufruf)
        )
        return "ok"

    # ─── PFAD B: Typ unbekannt, Typ-Erkennungs-Prompt vorhanden → zweistufig ──
    if existing_document_type == 0 and doc_type_prompt_text and document_types:
        # ── KI-Aufruf 1: Dokumententyp erkennen ─────────────────────────────
        # detect_document_type ist SYNCHRON → läuft in asyncio.to_thread
        logger.info(
            "Starte Dokumententyp-Erkennung für #%d (%d Seite(n))",
            doc_id, page_count
        )
        type_id, type_name, _type_raw, type_stats = await asyncio.to_thread(
            ai_service.detect_document_type,
            images_b64,           # Gerenderte PDF-Seiten als Base64-Liste
            config_proxy,         # KI-Verbindungsparameter
            document_types,       # Liste aller möglichen Typen (KI wählt daraus)
            doc_type_prompt_text, # Inhalt des Typ-Erkennungs-Prompts
        )
        # detect_document_type gibt zurück:
        #   type_id:    Erkannte Typ-ID (None wenn KI keinen gültigen Typ lieferte)
        #   type_name:  Erkannter Typ-Name (None wenn type_id None)
        #   _type_raw:  Vollständige KI-Antwort (für Fehleranalyse)
        #   type_stats: Token-Statistiken des ersten KI-Aufrufs

        # ── Sofortiger Abbruch bei KI-Verbindungsfehler ──────────────────────
        # Verbindungsfehler (Timeout, 429, Netzwerk-Down) sind keine Dokument-Fehler.
        # Das Dokument bleibt im Status "processing", damit es beim nächsten
        # Worker-Versuch erneut analysiert wird (kein verlorenes Dokument).
        if _is_ai_conn_error(_type_raw):
            logger.warning(
                "Dokument #%d: KI-Verbindungsfehler bei Typ-Erkennung", doc_id
            )
            _set_error(doc_id, f"KI nicht erreichbar: {_type_raw[:200]}")
            return "ai_unavailable"  # Signalisiert dem Worker: erneut versuchen

        # Erkannten Typ merken für späteren _db_analyze_write-Aufruf
        document_type_id = type_id

        # ── Sofortspeicherung des erkannten Typs ─────────────────────────────
        # Wichtig: Typ in DB sichern BEVOR extract_invoice_data startet.
        # Falls die Extraktion fehlschlägt (Timeout, 429, Absturz),
        # ist der Typ trotzdem bereits persistiert.
        await asyncio.to_thread(_db_save_document_type, doc_id, type_id)

        # ── Entscheidung: Eingangsrechnung oder nicht? ────────────────────────
        if type_id != 1:
            # Kein Eingangsrechnungs-Typ → Analyse beendet, keine Extraktion
            # (z.B. Lieferschein, Mahnung, Kontoauszug)
            del images_b64   # Bilder freigeben — nicht mehr benötigt
            await asyncio.to_thread(
                _db_type_only_finish,
                doc_id, type_id, type_name, page_count,
                batch_id, original_filename,
                type_stats,   # Token-Stats aus Typ-Erkennung speichern
            )
            return "ok"

        # ── type_id == 1: Eingangsrechnung erkannt → Extraktion starten ──────
        logger.info(
            "Dokument #%d: Eingangsrechnung erkannt — starte Extraktion", doc_id
        )

        # ── KI-Aufruf 2: Rechnungsdaten extrahieren ──────────────────────────
        # extract_invoice_data ist SYNCHRON → läuft in asyncio.to_thread
        extracted_fields, order_positions, raw_response, inv_stats = await asyncio.to_thread(
            ai_service.extract_invoice_data,
            images_b64,         # Dieselben Base64-Bilder wie in KI-Aufruf 1
            config_proxy,       # Dieselbe KI-Konfiguration
            system_prompt_text, # Inhalt des Extraktionsprompts (type=1)
        )
        del images_b64   # Base64-Bilder freigeben — nach KI-Aufruf nicht mehr gebraucht

        # ── Verbindungsfehler bei der Extraktion prüfen ──────────────────────
        if _is_ai_conn_error(raw_response):
            logger.warning(
                "Dokument #%d: KI-Verbindungsfehler bei Extraktion", doc_id
            )
            _set_error(doc_id, f"KI nicht erreichbar: {raw_response[:200]}")
            return "ai_unavailable"

        # Statistiken beider KI-Aufrufe zu einer Gesamtstatistik zusammenführen
        # (Typ-Erkennung + Extraktion = Gesamtkosten für dieses Dokument)
        ki_stats = _merge_ki_stats(type_stats, inv_stats)

    else:
        # ─── PFAD C: Direkte Extraktion (einstufig) ──────────────────────────
        # Dieser Pfad wird gewählt wenn:
        #   a) existing_document_type == 1 (Eingangsrechnung bereits bekannt)
        #      → kein Typ-Erkennungs-Schritt nötig, direkt extrahieren
        #   b) doc_type_prompt_text ist None (kein Typ-Prompt konfiguriert)
        #      → zweistufige Analyse nicht möglich, direkt extrahieren

        if existing_document_type == 1:
            # Typ war bereits als Eingangsrechnung bekannt
            logger.info(
                "Dokument #%d: Typ bereits bekannt (Eingangsrechnung) — Extraktion direkt",
                doc_id,
            )
            document_type_id = 1   # Eingangsrechnung für _db_analyze_write merken
        else:
            # Kein Typ-Prompt → einstufige Analyse (Legacy-Modus)
            logger.info(
                "Starte KI-Extraktion für Dokument #%d (%d Seite(n))",
                doc_id, page_count
            )

        # ── KI-Aufruf: Rechnungsdaten extrahieren ────────────────────────────
        extracted_fields, order_positions, raw_response, ki_stats = await asyncio.to_thread(
            ai_service.extract_invoice_data,
            images_b64,         # Gerenderte PDF-Seiten als Base64-Liste
            config_proxy,       # KI-Verbindungsparameter
            system_prompt_text, # Extraktionsprompt (oder None für KI-eigenen Prompt)
        )
        del images_b64   # Bilder nach KI-Aufruf freigeben

        # ── Verbindungsfehler prüfen ──────────────────────────────────────────
        if _is_ai_conn_error(raw_response):
            logger.warning(
                "Dokument #%d: KI-Verbindungsfehler bei Extraktion", doc_id
            )
            _set_error(doc_id, f"KI nicht erreichbar: {raw_response[:200]}")
            return "ai_unavailable"

    # ═══════════════════════════════════════════════════════════════════════════
    # ANALYSE-SCHRITT 4: Ergebnisse in DB schreiben
    # ═══════════════════════════════════════════════════════════════════════════
    # Dieser Schritt wird nur erreicht wenn die Extraktion erfolgreich war
    # (oder mit KI-Fehlerantwort, aber ohne Verbindungsfehler).
    # _db_analyze_write setzt das Dokument auf "done" oder "error".
    await asyncio.to_thread(
        _db_analyze_write,
        doc_id, original_filename, batch_id, data["ai_model_name"],
        page_count, extracted_fields, order_positions, raw_response,
        ki_stats, document_type_id,
    )
    return "ok"


# ─── API-Endpunkte (Dokument-Detail, Preview, Kommentar, Soft-Delete) ─────────


@router.delete("/{doc_id}", response_model=DocumentDetail)
def soft_delete_document(doc_id: int, db: Session = Depends(get_db)):
    """
    Markiert ein Dokument als gelöscht (Soft-Delete).

    Das Dokument und alle zugehörigen Daten (InvoiceExtraction, OrderPositions,
    TokenCounts) bleiben vollständig in der Datenbank erhalten — es wird nur
    soft_deleted=True gesetzt. Die PDF-Datei auf dem Dateisystem wird NICHT gelöscht.

    Soft-gelöschte Dokumente:
      • Sind in der Standardliste (GET /api/documents) nicht mehr sichtbar
      • Können mit POST /{id}/restore wiederhergestellt werden
      • Werden im Import-Export (Excel) nicht berücksichtigt

    Args:
        doc_id: ID des zu löschenden Dokuments.
        db:     Datenbank-Session (via FastAPI-Dependency injiziert).

    Returns:
        DocumentDetail des (nun soft-gelöschten) Dokuments.

    Raises:
        HTTPException 404: Dokument nicht gefunden.
    """
    doc = crud.document.soft_delete(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    # Detail-Ansicht laden und zurückgeben (enthält alle Felder inkl. soft_deleted=True)
    return crud.document.get_by_id_with_details(db, doc_id)


@router.post("/{doc_id}/restore", response_model=DocumentDetail)
def restore_document(doc_id: int, db: Session = Depends(get_db)):
    """
    Macht einen Soft-Delete rückgängig (soft_deleted → False).

    Das Dokument ist danach wieder in der normalen Dokumentenliste sichtbar
    und kann erneut analysiert werden.

    Args:
        doc_id: ID des wiederherzustellenden Dokuments.
        db:     Datenbank-Session (via FastAPI-Dependency injiziert).

    Returns:
        DocumentDetail des wiederhergestellten Dokuments.

    Raises:
        HTTPException 404: Dokument nicht gefunden.
    """
    doc = crud.document.restore(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return crud.document.get_by_id_with_details(db, doc_id)


@router.get("/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: int, db: Session = Depends(get_db)):
    """
    Gibt ein Dokument mit vollständigen Details zurück.

    Im Gegensatz zu GET /api/documents (Liste) lädt dieser Endpunkt alle
    verknüpften Daten via joinedload in einer optimierten Abfrage:

      • InvoiceExtraction   — extrahierte Rechnungsfelder (oder None)
      • OrderPosition-Liste — alle Rechnungspositionen (oder leer)
      • DocumentTokenCount-Liste — alle KI-Durchläufe mit Token-Statistiken
      • Aggregierte ki_*-Felder — Summen über alle TokenCount-Einträge
        (ki_input_tokens, ki_output_tokens, ki_total_duration)

    Wird vom Frontend für:
      • Die Infos-Ansicht (Detail-Tabelle mit Rechnungsfeldern)
      • Das KI-Modal (raw_response + Token-Statistiken)
      • Die PDF-Vorschau (um stored_filename für die Preview-URL zu kennen)

    Args:
        doc_id: ID des abzurufenden Dokuments.
        db:     Datenbank-Session (via FastAPI-Dependency injiziert).

    Returns:
        DocumentDetail-Schema mit allen Feldern und verknüpften Objekten.

    Raises:
        HTTPException 404: Dokument nicht gefunden.
    """
    doc = crud.document.get_by_id_with_details(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    return doc


@router.get("/{doc_id}/preview")
def preview_document(doc_id: int, db: Session = Depends(get_db)):
    """
    Liefert die Original-PDF-Datei direkt zum Anzeigen im Browser.

    Unterschied zu einem normalen Download:
      content_disposition_type="inline" → Browser zeigt die Datei im PDF-Viewer an
                                          statt einen Download-Dialog zu öffnen.

    Pfad-Auflösung:
      Die Datei liegt unter:
        {batch.storage_folder_path}/{doc.stored_filename}
      Beispiel:
        /volume1/docker/_rechnungsanalyse/storage/Lieferant_GmbH_2025/42.pdf

    Das Dokument wird inklusive seiner Batch-Beziehung geladen (joinedload),
    damit storage_folder_path ohne zweite DB-Abfrage verfügbar ist.

    Args:
        doc_id: ID des Dokuments, dessen PDF angezeigt werden soll.
        db:     Datenbank-Session (via FastAPI-Dependency injiziert).

    Returns:
        FileResponse mit Content-Type "application/pdf" (inline).

    Raises:
        HTTPException 404: Dokument nicht gefunden, PDF-Datei noch nicht importiert,
                           oder PDF-Datei auf dem Dateisystem nicht vorhanden.
    """
    from sqlalchemy.orm import joinedload as _jl
    from app.models.document import Document as _DocModel

    # Dokument + Batch in einem JOIN laden (verhindert lazy-load-Abfrage auf doc.batch)
    doc = (
        db.query(_DocModel)
        .options(_jl(_DocModel.batch))
        .filter(_DocModel.id == doc_id)
        .first()
    )

    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")

    if not doc.stored_filename:
        # stored_filename ist None wenn der Import abgebrochen wurde
        # (z.B. Speicherproblem beim Kopieren)
        raise HTTPException(status_code=404, detail="PDF-Datei noch nicht verfügbar.")

    # Absoluten Pfad aus Batch-Ordner + Dateiname zusammensetzen
    storage_path = doc.batch.storage_folder_path if doc.batch else ""
    pdf_path = Path(storage_path) / doc.stored_filename

    if not pdf_path.exists():
        # Datei wurde nach dem Import manuell vom Dateisystem entfernt
        raise HTTPException(status_code=404, detail="PDF-Datei nicht auf dem Server gefunden.")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=doc.original_filename,          # Originaler Dateiname im Browser-Tab
        content_disposition_type="inline",       # Inline anzeigen statt herunterladen
    )


@router.patch("/{doc_id}/comment", response_model=DocumentDetail)
def update_document_comment(
    doc_id: int,
    payload: DocumentCommentUpdate,
    db: Session = Depends(get_db),
):
    """
    Speichert einen Freitext-Kommentar zum Dokument oder löscht ihn.

    Der Kommentar ist ein optionales Freitext-Feld auf dem Document-Datensatz.
    Er wird im Frontend in der Dokumententabelle angezeigt und ist im Excel-Export
    als eigene Spalte enthalten.

    Kommentar löschen: payload.comment = null → setzt document.comment = None

    Args:
        doc_id:   ID des Dokuments, dessen Kommentar geändert werden soll.
        payload:  DocumentCommentUpdate mit dem neuen Kommentar (oder null).
        db:       Datenbank-Session (via FastAPI-Dependency injiziert).

    Returns:
        Aktualisiertes DocumentDetail mit dem neuen Kommentar.

    Raises:
        HTTPException 404: Dokument nicht gefunden.
    """
    doc = crud.document.update_comment(db, doc_id, payload.comment)
    if doc is None:
        raise HTTPException(status_code=404, detail="Dokument nicht gefunden")
    # Detail-Ansicht laden (enthält alle Felder inkl. neuem Kommentar)
    return crud.document.get_by_id_with_details(db, doc_id)
