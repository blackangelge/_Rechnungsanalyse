"""
================================================================================
 KI-Task-Dispatcher mit dynamischem Worker-Pool
================================================================================

Angepasst an die vorhandenen Tabellen ``ai_servers`` und ``tasks``.
Die HTTP-Kommunikation und die Timeout-Logik (timeout_at setzen/zurück-
setzen, Erreichbarkeit prüfen) liegen vollständig in der externen
``AIClient``-Klasse, die von außen injiziert wird.

Kernverhalten
-------------
* **Tasks** werden gleichmäßig auf alle aktiven, erreichbaren KI-Server
  verteilt.
* **timeout_at** in ai_servers wird vom Dispatcher nur GELESEN. Ist es
  gesetzt, wird der Server beim Slot-Vergabe übersprungen.
* **AIClient.send()** ist der einzige Kontaktpunkt zur KI. Die Klasse
  ist selbst dafür zuständig:
    - Erreichbarkeit zu prüfen,
    - bei Nichterreichbarkeit ``timeout_at`` in der DB zu setzen,
    - bei Erfolg ``timeout_at`` ggf. zurückzusetzen,
    - bei Verbindungsfehlern eine ``ConnectionError`` zu werfen.

Status-Lebenszyklus tasks
-------------------------
``pending`` → ``in_progress`` → ``completed`` | ``failed``
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol

import asyncpg

from app.services.ai_url import build_ai_url as build_server_url

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("dispatcher")


# ──────────────────────────────────────────────────────────────────────────────
# Konfiguration
# ──────────────────────────────────────────────────────────────────────────────
#: Polling-Intervall des Dispatchers (Sekunden).
POLL_INTERVAL_SEC = 20

#: Maximale Wartezeit eines Workers auf einen freien Slot.
SLOT_ACQUIRE_TIMEOUT = 60.0

#: Grobe Schätzung des Token-Verbrauchs pro gerenderter PDF-Seite (Bild + Anteil
#: Prompt/Ausgabe). Wird mit AIClients.max_context_tokens verrechnet, um zu
#: entscheiden, ob ein Dokument auf eine bestimmte KI-Instanz passt.
from app.services.capacity import TOKENS_PER_PAGE_ESTIMATE  # noqa: E402  (gemeinsam mit den Workflow-Knoten)



async def prepare_pdf_for_task(payload) -> None:
    """
    Wendet das automatische Kürzen (Einstellungen → Automatisierung → „Lange PDFs kürzen", bzw. bei
    einem Workflow dessen führende „PDF kürzen"-Knoten) VOR der Kapazitätsprüfung an: Eine 100-seitige PDF, die auf 3 Seiten gekürzt wird, darf nicht
    als „zu groß für alle KI-Server" (waiting_capacity) hängen bleiben. Idempotent — hat die PDF
    schon höchstens N Seiten, passiert nichts. Fehler werden nur geloggt (die Analyse selbst
    versucht das Kürzen später nochmal und bricht bei einem Fehler nie ab).
    """
    doc_id = payload.get("document_id") if isinstance(payload, dict) else None
    if not isinstance(doc_id, int):
        return
    try:
        from app.routers.documents import _db_prepare_for_capacity
        await asyncio.to_thread(_db_prepare_for_capacity, doc_id, payload.get("workflow_id"))
    except Exception as exc:  # noqa: BLE001
        log.warning("PDF-Vorbereitung für Dokument %s fehlgeschlagen: %s", doc_id, exc)


# ══════════════════════════════════════════════════════════════════════════════
#  PROTOCOL: Externe KI-Klasse
# ══════════════════════════════════════════════════════════════════════════════
class AIClient(Protocol):
    """Schnittstelle, die deine externe KI-Anfrage-Klasse erfüllen muss.

    Der Dispatcher selbst macht keine HTTP-Calls. Deine Klasse:
      * baut die Anfrage aus ``server.raw`` (api_key, model_name,
        max_tokens, temperature, reasoning, endpoint_type ...),
      * prüft Erreichbarkeit,
      * verwaltet ``timeout_at`` in der DB,
      * liefert das Ergebnis als dict zurück.

    Konvention für Fehler:
      * ``ConnectionError`` → Server ist nicht erreichbar oder geht
        während der Anfrage offline. Der Dispatcher requeued die Task.
      * Jede andere Exception → Task wird als ``failed`` markiert.
    """

    async def send(self, server: "AIServer", payload: dict) -> dict:
        ...


# ══════════════════════════════════════════════════════════════════════════════
#  DATENKLASSEN
# ══════════════════════════════════════════════════════════════════════════════
# build_server_url (importiert oben) — kanonische Implementierung in
# app/services/ai_url.py. MUSS mit der URL-Bildung in app/routers/documents.py
# identisch sein, sonst bauen Analyse-Pipeline und Worker-Status unterschiedliche
# URLs für dieselbe KI-Konfiguration.


@dataclass
class AIServer:
    """Spiegelt einen Eintrag aus der ``ai_servers``-Tabelle.

    Es werden nur die Felder geladen, die der Dispatcher selbst braucht.
    Alle anderen Felder (api_key, model_name, max_tokens, temperature,
    chat_response, reasoning, endpoint_type ...) sind über ``raw`` für
    die externe ``AIClient``-Implementierung verfügbar.
    """

    id: int
    name: str
    ip_address: str
    port: Optional[str]
    parallel_request: int    # Slots (1-4)
    active: bool
    timeout_at: Optional[datetime]

    #: Komplette DB-Zeile als dict – für die externe AIClient-Klasse.
    raw: dict = field(default_factory=dict)

    #: Slot-Verwaltung; init=False, wird in __post_init__ gesetzt.
    semaphore: asyncio.Semaphore = field(init=False)

    #: Aufgaben, die GERADE auf diesem Server laufen: task_id → {task_id, document_id, started_at (Unix-Zeit), worker_id}.
    #: Nur für die Live-Ansicht (GET /status → servers[].running); wird beim Neuladen der Server-Liste beibehalten.
    running: dict = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.semaphore = asyncio.Semaphore(self.parallel_request)

    @property
    def url(self) -> str:
        """Setzt die Basis-URL aus IP + Port zusammen (siehe ``build_server_url``)."""
        return build_server_url(self.ip_address, self.port)

    @property
    def is_down(self) -> bool:
        """True, wenn ``timeout_at`` gesetzt ist (Server gilt als nicht
        erreichbar). Wird ausschließlich von der externen AIClient-Klasse
        gesetzt/zurückgesetzt – der Dispatcher liest nur."""
        return self.timeout_at is not None

    @property
    def max_pages(self) -> int | None:
        """Maximale Seitenzahl, die diese KI laut max_context_tokens verarbeiten
        kann (grobe Schätzung über TOKENS_PER_PAGE_ESTIMATE). None = unbeschränkt."""
        limit = self.raw.get("max_context_tokens")
        if not limit:
            return None
        return max(1, limit // TOKENS_PER_PAGE_ESTIMATE)

    def can_handle_pages(self, page_count: int) -> bool:
        """True, wenn diese KI ein Dokument mit page_count Seiten verarbeiten kann."""
        if page_count <= 0:
            return True  # Seitenzahl (noch) unbekannt — nicht vorab ausschließen
        limit = self.max_pages
        return limit is None or page_count <= limit


@dataclass
class Task:
    """Spiegelt einen Eintrag aus der ``tasks``-Tabelle."""

    id: int
    workflow_id: str
    payload: dict
    attempts: int
    max_attempts: int


# ══════════════════════════════════════════════════════════════════════════════
#  SERVER-POOL
# ══════════════════════════════════════════════════════════════════════════════
class ServerPool:
    """Verwaltet alle bekannten KI-Rechner mit ihren freien Slots."""

    def __init__(self) -> None:
        self._servers: dict[int, AIServer] = {}
        self._lock = asyncio.Lock()

    async def update(self, latest: list[AIServer]) -> None:
        """Synchronisiert den Pool mit den DB-Daten.

        Drei Fälle pro Server:
          1. **Neu**       → in den Pool aufnehmen.
          2. **Geändert**  → komplett ersetzen (Kapazität/IP/Port anders).
          3. **Gleich**    → nur weiche Felder (active, timeout_at, raw)
                             übernehmen, Semaphore behalten.
        """
        async with self._lock:
            latest_ids = {s.id for s in latest}

            for srv in latest:
                existing = self._servers.get(srv.id)

                if existing is None:
                    self._servers[srv.id] = srv
                    log.info(
                        f"+ Server {srv.name} "
                        f"(parallel={srv.parallel_request}) hinzugefügt"
                    )
                elif (
                    existing.parallel_request != srv.parallel_request
                    or existing.ip_address != srv.ip_address
                    or existing.port != srv.port
                ):
                    self._servers[srv.id] = srv
                    log.info(f"~ Server {srv.name} aktualisiert")
                else:
                    # Nur weiche Felder übernehmen, Semaphore behalten.
                    existing.active = srv.active
                    existing.timeout_at = srv.timeout_at
                    existing.raw = srv.raw

            # Verschwundene Server entfernen.
            for sid in list(self._servers.keys()):
                if sid not in latest_ids:
                    log.info(f"- Server {self._servers[sid].name} entfernt")
                    del self._servers[sid]

    def eligible_servers(self, page_count: int = 0) -> list["AIServer"]:
        """Aktive, erreichbare Server, die page_count Seiten verarbeiten können
        (unabhängig von aktuell freien Slots) — für Kapazitäts-Vorabprüfungen."""
        return [
            s for s in self._servers.values()
            if s.active and not s.is_down and s.can_handle_pages(page_count)
        ]

    @asynccontextmanager
    async def acquire_slot(self, timeout: float = 30.0, page_count: int = 0):
        """Reserviert einen Slot auf einem aktiven, erreichbaren Server, der
        Dokumente mit page_count Seiten verarbeiten kann (siehe AIServer.can_handle_pages).

        "Geeignet" bedeutet:
          * ``active = True``
          * ``timeout_at IS NULL`` (Server gilt als erreichbar)
          * Kapazität (max_context_tokens) reicht für page_count Seiten

        Verwendung::

            async with pool.acquire_slot(page_count=doc_pages) as server:
                ...

        Raises
        ------
        TimeoutError
            Falls innerhalb von ``timeout`` kein Server frei wurde.
        """
        deadline = asyncio.get_event_loop().time() + timeout

        while True:
            candidates = [
                s for s in self._servers.values()
                if s.active and not s.is_down and s.can_handle_pages(page_count)
            ]
            random.shuffle(candidates)

            for srv in candidates:
                try:
                    await asyncio.wait_for(
                        srv.semaphore.acquire(), timeout=0.1
                    )
                    try:
                        yield srv
                    finally:
                        srv.semaphore.release()
                    return
                except asyncio.TimeoutError:
                    continue

            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("Kein freier Server-Slot verfügbar")
            await asyncio.sleep(0.5)

    def total_capacity(self) -> int:
        """Summe der Kapazitäten aller einsatzbereiten Server."""
        return sum(
            s.parallel_request
            for s in self._servers.values()
            if s.active and not s.is_down
        )


# ══════════════════════════════════════════════════════════════════════════════
#  WORKER
# ══════════════════════════════════════════════════════════════════════════════
class Worker:
    """Verarbeitet einzelne Tasks gegen einen vom Pool zugewiesenen Server."""

    def __init__(
        self,
        worker_id: str,
        queue: asyncio.Queue,
        pool: ServerPool,
        db_pool: asyncpg.Pool,
        ai_client: AIClient,
    ) -> None:
        self.worker_id = worker_id
        self.queue = queue
        self.pool = pool
        self.db_pool = db_pool
        self.ai_client = ai_client

        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    # ──────────────────────────────────────────────────────────────────
    # DB-Helfer
    # ──────────────────────────────────────────────────────────────────
    async def _mark_completed(self, task: Task, result: dict) -> None:
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE workflow_tasks
                   SET status='completed',
                       result=$2,
                       updated_at=NOW()
                   WHERE id=$1""",
                task.id, result,
            )

    async def _mark_failed(self, task: Task, error: str) -> None:
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE workflow_tasks
                   SET status='failed',
                       error=$2,
                       updated_at=NOW()
                   WHERE id=$1""",
                task.id, error,
            )

    async def _release_for_retry_no_penalty(self, task: Task, reason: str = "kein KI-Server verfügbar") -> None:
        """Setzt die Task auf 'pending' zurück OHNE den Versuchszähler zu erhöhen —
        der Task wird dadurch UNBEGRENZT neu versucht, es gibt kein 'failed' mehr.

        Verwendet für drei KI-bezogene Fälle, in denen ein permanentes 'failed'
        nicht sinnvoll ist, weil sich die Ursache von selbst beheben kann:
          1. Kein passender Server verfügbar (kein aktiver KI-Rechner konfiguriert,
             alle temporär deaktiviert, oder alle Slots dauerhaft belegt) — hier fand
             gar kein KI-Aufruf statt, es gibt also ohnehin keinen "Versuch".
          2. Dokument zu groß für alle konfigurierten KI-Server (Kapazitätsprüfung) —
             wird automatisch verarbeitet, sobald eine größere KI-Konfiguration
             verfügbar ist (siehe _set_document_waiting_capacity).
          3. Verbindungsfehler beim tatsächlichen KI-Aufruf (ConnectionError) — die
             KI war kurzzeitig/länger nicht erreichbar; kein inhaltliches Problem
             des Dokuments, sondern ein Infrastrukturproblem, das sich von selbst
             löst, sobald die KI wieder erreichbar ist.
        Inhaltliche Fehler (z.B. Bug beim Verarbeiten der KI-Antwort) sind davon
        NICHT betroffen und landen weiterhin sofort auf 'failed' (_mark_failed) —
        ein dauerhaft kaputtes Dokument soll nicht endlos KI-Ressourcen verbrauchen.
        """
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE workflow_tasks
                   SET status='pending',
                       worker_id=NULL,
                       locked_at=NULL,
                       updated_at=NOW()
                   WHERE id=$1""",
                task.id,
            )
        log.info(
            f"[{self.worker_id}] Task {task.id} zurück auf 'pending' "
            f"({reason} — Versuch nicht gezählt)"
        )

    async def _set_document_error(self, task: Task, reason: str) -> None:
        """Setzt das verknüpfte Dokument auf 'error', falls der Payload eine document_id enthält.

        Wird aufgerufen wenn ein Task endgültig fehlschlägt (inhaltlicher Fehler,
        kein Verbindungsproblem), damit das Dokument nicht dauerhaft im Status
        'processing' hängt.
        """
        doc_id = task.payload.get("document_id") if isinstance(task.payload, dict) else None
        if not isinstance(doc_id, int):
            return
        try:
            from app.routers.documents import _set_error as _doc_set_error
            await asyncio.to_thread(_doc_set_error, doc_id, reason)
            log.info(f"[{self.worker_id}] Dokument #{doc_id} auf 'error' gesetzt")
        except Exception as exc:
            log.error(
                f"[{self.worker_id}] Konnte Dokument #{doc_id} nicht auf 'error' setzen: {exc}"
            )

    async def _set_document_waiting_capacity(self, task: Task, reason: str) -> None:
        """Setzt das verknüpfte Dokument auf 'waiting_capacity' (KEIN Endzustand).

        Wird verwendet, wenn ein Dokument für alle aktuell konfigurierten KI-Server
        zu groß ist. Der Task selbst wird dabei NICHT auf 'failed' gesetzt, sondern
        unbegrenzt neu versucht (siehe _process()) — sobald eine ausreichend große
        KI-Konfiguration verfügbar ist, wird das Dokument automatisch fertig
        verarbeitet, ohne dass jemand manuell eingreifen muss.
        """
        doc_id = task.payload.get("document_id") if isinstance(task.payload, dict) else None
        if not isinstance(doc_id, int):
            return
        try:
            from app.routers.documents import _set_waiting_capacity as _doc_set_waiting_capacity
            await asyncio.to_thread(_doc_set_waiting_capacity, doc_id, reason)
        except Exception as exc:
            log.error(
                f"[{self.worker_id}] Konnte Dokument #{doc_id} nicht auf 'waiting_capacity' setzen: {exc}"
            )

    async def _claim_task(self, task: Task) -> None:
        """Markiert den Task als TATSÄCHLICH in Bearbeitung (worker_id + locked_at).

        WICHTIG: locked_at wird bewusst HIER gesetzt — NICHT schon in _load_tasks()
        beim bloßen Laden in die interne asyncio.Queue. Grund: _cleanup_stale_tasks()
        hält einen Task für "verwaist", sobald locked_at älter als lock_timeout_minutes
        ist. Würde locked_at schon beim Laden gesetzt, würden Tasks, die bei einer
        großen Warteschlange (z.B. 1000+ Dokumente, 24 gleichzeitige Worker) einfach
        nur lange auf ihren Platz warten — ohne dass je ein Worker sie angefasst hat —
        fälschlich als "verwaist" erkannt und zurück auf 'pending' gesetzt. Das
        erzeugt ein DUPLIKAT: die ursprüngliche Kopie hängt weiterhin unverändert in
        der internen Queue, während _load_tasks() beim nächsten Zyklus dieselbe
        (jetzt wieder 'pending') Zeile ERNEUT lädt und eine zweite Kopie in die Queue
        stellt. Beide werden irgendwann von je einem Worker verarbeitet — das führt zu
        doppelten KI-Anfragen für dasselbe Dokument, und wenn die erste, "legitime"
        Kopie fertig wird und ihr Task-Datensatz danach z.B. über "Abgeschlossene
        Tasks löschen" entfernt wird, läuft die zweite Kopie als Geisterprozess ohne
        zugehörigen Task-Datensatz weiter — sichtbar als Dokument, das laut
        Warteschlangen-Status "fertig"/"keine Tasks mehr" ist, aber laut Beleg-Status
        weiterhin "wird verarbeitet" bleibt und tatsächlich noch an die KI sendet.
        Solange locked_at NULL bleibt (Task nur geladen, aber von keinem Worker
        beansprucht), lässt die SQL-Bedingung "locked_at < NOW() - interval" der
        Cleanup-Abfrage die Zeile automatisch unangetastet (NULL-Vergleich ist nie
        TRUE) — der Timeout greift dann nur noch bei Tasks, die ein Worker wirklich
        übernommen hat und die seitdem hängen (z.B. Worker/Container abgestürzt).
        """
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE workflow_tasks
                   SET worker_id=$2, locked_at=NOW(), updated_at=NOW()
                   WHERE id=$1""",
                task.id, self.worker_id,
            )

    async def _get_document_page_count(self, task: Task) -> int:
        """Liest page_count des verknüpften Dokuments (0 falls unbekannt/kein
        process_document-Task) — Basis für die KI-Kapazitätsprüfung."""
        doc_id = task.payload.get("document_id") if isinstance(task.payload, dict) else None
        if not isinstance(doc_id, int):
            return 0
        async with self.db_pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT page_count FROM documents WHERE id=$1", doc_id
            )
        return value or 0

    # ──────────────────────────────────────────────────────────────────
    async def _process(self, task: Task) -> None:
        await self._claim_task(task)

        # Automatisches Kürzen langer PDFs vor der Kapazitätsprüfung (siehe prepare_pdf_for_task)
        await prepare_pdf_for_task(task.payload)

        doc_id = task.payload.get("document_id") if isinstance(task.payload, dict) else None
        if isinstance(doc_id, int):
            from app.routers.documents import _db_precheck_pdf, _db_workflow_reduces_pages
            problem = await asyncio.to_thread(_db_precheck_pdf, doc_id)
            if problem:                                  # verschlüsselt/defekt → Nachbearbeitung, die KI wird gar nicht belastet
                log.warning(f"[{self.worker_id}] Task {task.id}: {problem}")
                await self._mark_failed(task, f"Nachbearbeitung nötig: {problem}")
                return
            reduces_pages = await asyncio.to_thread(_db_workflow_reduces_pages, task.payload.get("workflow_id"))
        else:
            reduces_pages = False

        page_count = await self._get_document_page_count(task)
        if reduces_pages:
            # Der Workflow verringert die Seitenzahl selbst (Seiten erkennen → nur Rechnungsseiten behalten): die Prüfung der
            # verbleibenden Seiten übernimmt der Extraktions-Knoten, hier darf die Gesamtseitenzahl nicht blockieren.
            page_count = 0

        # Kapazitäts-Vorabprüfung: Ist das Dokument für ALLE aktiven KI-Server zu
        # groß, NICHT fehlschlagen (kein 'failed'!) — stattdessen unbegrenzt neu
        # versuchen, bis irgendwann eine ausreichend große KI-Konfiguration verfügbar
        # ist (z.B. weil sie nachträglich hinzugefügt/angepasst wurde). Damit das nicht
        # wie ein stilles Hängenbleiben aussieht, wird der Grund sichtbar als
        # 'waiting_capacity' am Dokument vermerkt (siehe _set_document_waiting_capacity).
        if page_count > 0 and self.pool.eligible_servers() and not self.pool.eligible_servers(page_count):
            max_known = max(
                (s.max_pages for s in self.pool.eligible_servers() if s.max_pages is not None),
                default=None,
            )
            reason = (
                f"Dokument zu groß für alle konfigurierten KI-Server "
                f"({page_count} Seiten, max. verfügbar: {max_known if max_known is not None else '?'} Seiten)"
            )
            # Im neuen System ist ein zu großer Beleg ein FEHLER ZUR NACHBEARBEITUNG (Seiten entfernen, Rechnung heraustrennen,
            # Workflow mit Seitenerkennung wählen) — kein endloses Warten.
            reason += " — bitte Seiten entfernen oder einen Workflow mit Seitenerkennung wählen"
            log.warning(f"[{self.worker_id}] Task {task.id}: {reason}")
            from app.routers.documents import _set_error as _doc_set_error
            await asyncio.to_thread(_doc_set_error, doc_id, reason, "too_many_pages")
            await self._mark_failed(task, f"Nachbearbeitung nötig: {reason}")
            return

        try:
            async with self.pool.acquire_slot(
                timeout=SLOT_ACQUIRE_TIMEOUT,
                page_count=page_count,
            ) as server:
                log.info(
                    f"[{self.worker_id}] Task {task.id} auf {server.name}"
                )
                server.running[task.id] = {
                    "task_id": task.id,
                    "document_id": doc_id if isinstance(doc_id, int) else None,
                    "started_at": time.time(),
                    "worker_id": self.worker_id,
                }

                try:
                    result = await self.ai_client.send(server, task.payload)
                    await self._mark_completed(task, result)
                    log.info(f"[{self.worker_id}] Task {task.id} fertig")

                except ConnectionError as e:
                    # Server-/Verbindungsproblem (timeout_at wurde von der AIClient-
                    # Klasse selbst gesetzt). KEIN 'failed' — Task wird unbegrenzt neu
                    # versucht, bis die KI wieder erreichbar ist (kein manuelles
                    # Eingreifen nötig, wenn ein KI-Rechner z.B. neu startet/ausfällt).
                    # attempts wird bewusst NICHT erhöht (siehe _release_for_retry_no_penalty)
                    # — ein Verbindungsfehler ist kein inhaltliches Problem des Dokuments.
                    log.error(
                        f"[{self.worker_id}] Verbindung zu {server.name} "
                        f"verloren: {e} — wird erneut versucht"
                    )
                    await self._release_for_retry_no_penalty(
                        task, reason=f"Verbindung zu {server.name} verloren"
                    )

                except Exception as e:
                    # Inhaltlicher Fehler – nicht erneut versuchen (siehe Antwort
                    # des Nutzers zur Scope-Frage: NUR Verbindungsfehler werden
                    # unbegrenzt neu versucht, echte inhaltliche Fehler weiterhin
                    # nicht — sonst würde ein dauerhaft kaputtes Dokument (z.B. Bug
                    # beim Verarbeiten der KI-Antwort) endlos KI-Ressourcen verbrauchen.
                    reason = f"{type(e).__name__}: {e}"
                    log.error(
                        f"[{self.worker_id}] Task {task.id} fehlgeschlagen: {reason}",
                        exc_info=True,
                    )
                    await self._mark_failed(task, reason)
                    # WICHTIG (Bugfix, unabhängig von obigem): _mark_failed setzt nur
                    # den TASK auf 'failed' — ohne diesen Aufruf blieb das zugehörige
                    # DOKUMENT für immer auf 'processing' hängen (keine sichtbare
                    # Fehleranzeige in der Belege-Übersicht, obwohl der Task längst
                    # fehlgeschlagen war).
                    await self._set_document_error(task, reason)
                finally:
                    server.running.pop(task.id, None)

        except TimeoutError:
            # Innerhalb von SLOT_ACQUIRE_TIMEOUT wurde kein passender Server frei —
            # es fand also gar kein tatsächlicher KI-Aufruf statt (weder Erfolg
            # noch Fehler). Zählt deshalb NICHT als Versuch, damit ein Dokument
            # nicht fälschlich auf 'error' landet, nur weil gerade kein KI-Rechner
            # aktiv/erreichbar ist. Wird unbegrenzt weiter versucht, bis ein
            # Server verfügbar wird.
            log.warning(
                f"[{self.worker_id}] Kein Server frei für Task {task.id} "
                f"(kein aktiver/passender KI-Rechner verfügbar)"
            )
            await self._release_for_retry_no_penalty(task)

    # ──────────────────────────────────────────────────────────────────
    async def run(self) -> None:
        """Hauptschleife des Workers."""
        while not self._stop.is_set():
            try:
                task = await asyncio.wait_for(
                    self.queue.get(), timeout=2.0
                )
            except asyncio.TimeoutError:
                continue

            try:
                await self._process(task)
            except Exception as e:
                # Sicherheitsnetz: verhindert dass ein unbehandelter Fehler
                # den Worker-Loop komplett beendet.
                log.error(
                    f"[{self.worker_id}] Kritischer Fehler in _process: "
                    f"{type(e).__name__}: {e}",
                    exc_info=True,
                )
            finally:
                self.queue.task_done()

        log.info(f"[{self.worker_id}] gestoppt")

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                self._task.cancel()


# ══════════════════════════════════════════════════════════════════════════════
#  DISPATCHER
# ══════════════════════════════════════════════════════════════════════════════
class Dispatcher:
    """Polling-Schleife, die DB und Worker-Pool synchron hält."""

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        ai_client: AIClient,
        poll_interval: int = POLL_INTERVAL_SEC,
        max_workers_cap: int = 5,
        lock_timeout_minutes: int = 30,
    ) -> None:
        # poll_interval/max_workers_cap/lock_timeout_minutes sind nur die
        # ANFANGSWERTE (z.B. vor dem ersten erfolgreichen DB-Zugriff) — run()
        # liest die aktuellen Werte bei jedem Zyklus frisch aus automation_settings
        # nach (siehe _load_worker_settings()), damit Änderungen über die Website
        # ohne Container-Neustart wirken.
        self.db_pool = db_pool
        self.ai_client = ai_client
        self.poll_interval = poll_interval
        self.max_workers_cap = max_workers_cap  # Globale Obergrenze für Worker-Anzahl
        self.lock_timeout_minutes = lock_timeout_minutes

        self.queue: asyncio.Queue = asyncio.Queue()
        self.pool = ServerPool()
        self.workers: list[Worker] = []
        self._counter = 0

        #: Wenn True, werden keine neuen Tasks aus der DB geladen (siehe ``run()``).
        #: Bereits geladene/in Bearbeitung befindliche Tasks laufen weiter — Pause
        #: heißt "keine neue Arbeit annehmen", kein hartes Stoppen laufender Worker.
        self.paused: bool = False

    # ──────────────────────────────────────────────────────────────────
    async def _load_worker_settings(self) -> None:
        """
        Liest worker_concurrency/worker_poll_interval_seconds/worker_lock_timeout_minutes
        frisch aus automation_settings und übernimmt sie — macht diese Werte über
        die Website live änderbar, ohne den Worker-Container neu zu starten.
        Bei Fehlern (z.B. Tabelle noch nicht migriert) bleiben die bisherigen
        Werte unverändert bestehen.
        """
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT worker_concurrency, worker_poll_interval_seconds,
                              worker_lock_timeout_minutes
                       FROM automation_settings WHERE id = 1"""
                )
            if row is not None:
                self.max_workers_cap = row["worker_concurrency"]
                self.poll_interval = row["worker_poll_interval_seconds"]
                self.lock_timeout_minutes = row["worker_lock_timeout_minutes"]
        except Exception as exc:
            log.warning("Konnte Worker-Einstellungen nicht laden (bleibt bei bisherigen Werten): %s", exc)

    # ──────────────────────────────────────────────────────────────────
    async def _load_servers(self) -> list[AIServer]:
        """Lädt alle aktiven Server aus der DB.

        ``timeout_at`` wird mit gelesen – aber NICHT als Filter im SQL,
        damit deine AIClient-Klasse das Feld weiterhin kontrollieren
        kann und der ServerPool seinen Stand aktualisiert (z. B. wenn
        die Klasse den Server wieder freigegeben hat).
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, name, api_key, model_name, primary_type,
                          max_tokens, temperature, chat_response, active,
                          reasoning, ip_address, endpoint_type, port,
                          parallel_request, timeout_at, max_context_tokens
                   FROM ai_clients
                   WHERE active = TRUE"""
            )

        return [
            AIServer(
                id=r["id"],
                name=r["name"],
                ip_address=r["ip_address"],
                port=r["port"],
                parallel_request=r["parallel_request"],
                active=r["active"],
                timeout_at=r["timeout_at"],
                raw=dict(r),
            )
            for r in rows
        ]

    async def _load_tasks(self) -> list[Task]:
        """Holt neue Tasks atomar aus der DB und übernimmt sie in die interne Queue.

        Nutzt ``FOR UPDATE SKIP LOCKED`` für Parallelsicherheit. Setzt Status auf
        'in_progress', damit dieselbe Zeile nicht in einem späteren Zyklus erneut
        als 'pending' gefunden und doppelt geladen wird — locked_at bleibt hier
        ABSICHTLICH NULL (kein SET locked_at=NOW()!). Das eigentliche "in Bearbeitung
        seit"-Zeitstempel wird erst in Worker._claim_task() gesetzt, wenn ein Worker
        den Task WIRKLICH aus der Queue holt und zu verarbeiten beginnt — siehe
        ausführliche Begründung dort (Duplikat-Bug bei langer Warteschlange).
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """UPDATE workflow_tasks
                   SET status='in_progress',
                       worker_id='dispatcher',
                       updated_at=NOW()
                   WHERE id IN (
                       SELECT id FROM workflow_tasks
                       WHERE status='pending'
                         AND attempts < max_attempts
                       ORDER BY created_at
                       FOR UPDATE SKIP LOCKED
                   )
                   RETURNING id, workflow_id, payload,
                             attempts, max_attempts"""
            )
        return [
            Task(
                id=r["id"],
                workflow_id=r["workflow_id"],
                payload=r["payload"],
                attempts=r["attempts"],
                max_attempts=r["max_attempts"],
            )
            for r in rows
        ]

    # ──────────────────────────────────────────────────────────────────
    async def _scale_workers(self, target: int) -> None:
        """Passt die Worker-Anzahl an die Gesamtkapazität an.

        Mindestens 1 Worker, auch wenn alle Server gerade down sind –
        sonst läuft die Queue leer, sobald die AIClient-Klasse einen
        Server wieder freigibt.

        max_workers_cap begrenzt die Gesamtzahl unabhängig von der
        Server-Kapazität. Verhindert Thread-Pool-Erschöpfung auf NAS-Systemen
        mit wenigen CPUs, wo zu viele parallele KI-HTTP-Calls (à 300s) den
        asyncio-Default-Thread-Pool auslasten können.
        """
        target = min(target, self.max_workers_cap)  # Globale Obergrenze
        target = max(target, 1)
        current = len(self.workers)

        if target > current:
            for _ in range(target - current):
                self._counter += 1
                w = Worker(
                    worker_id=f"W{self._counter}",
                    queue=self.queue,
                    pool=self.pool,
                    db_pool=self.db_pool,
                    ai_client=self.ai_client,
                )
                w.start()
                self.workers.append(w)
            log.info(
                f"+ {target - current} Worker gestartet "
                f"(gesamt: {target})"
            )
        elif target < current:
            to_remove = self.workers[target:]
            self.workers = self.workers[:target]
            await asyncio.gather(*(w.stop() for w in to_remove))
            log.info(
                f"- {len(to_remove)} Worker gestoppt "
                f"(gesamt: {target})"
            )

    # ──────────────────────────────────────────────────────────────────
    async def _cleanup_stale_tasks(self, min_age_minutes: int | None = None) -> None:
        """Setzt verwaiste 'in_progress'-Tasks auf 'pending' zurück.

        min_age_minutes=None (Startup-Fall): setzt ALLE 'in_progress'-Tasks zurück —
        nach einem Absturz/Container-Neustart existieren die Worker, die sie
        bearbeitet haben, garantiert nicht mehr.

        min_age_minutes=N (periodischer Fall, siehe run()): setzt nur Tasks zurück,
        deren locked_at älter als N Minuten ist — verhindert, dass ein Task, der
        gerade tatsächlich noch bei der KI in Bearbeitung ist, mitten in der
        Anfrage weggerissen wird. N sollte deutlich über der maximalen KI-
        Antwortzeit liegen (siehe ai_service.REQUEST_TIMEOUT_SECONDS).

        Ohne diesen Reset würden verwaiste Tasks nie wieder abgeholt, da
        _load_tasks nur 'pending'-Tasks sucht.
        """
        try:
            async with self.db_pool.acquire() as conn:
                if min_age_minutes is None:
                    result = await conn.execute(
                        """UPDATE workflow_tasks
                           SET status='pending',
                               worker_id=NULL,
                               locked_at=NULL,
                               updated_at=NOW()
                           WHERE status='in_progress'"""
                    )
                else:
                    result = await conn.execute(
                        """UPDATE workflow_tasks
                           SET status='pending',
                               worker_id=NULL,
                               locked_at=NULL,
                               updated_at=NOW()
                           WHERE status='in_progress'
                             AND locked_at < NOW() - make_interval(mins => $1)""",
                        min_age_minutes,
                    )
            # asyncpg gibt "UPDATE N" als String zurück
            count = int(result.split()[-1]) if result else 0
            if count:
                log.warning(
                    "%d verwaiste 'in_progress'-Task(s) auf 'pending' zurückgesetzt (min_age_minutes=%s)",
                    count, min_age_minutes,
                )
        except Exception as exc:
            log.error("Stale-Task-Cleanup fehlgeschlagen (nicht kritisch): %s", exc)

    # ──────────────────────────────────────────────────────────────────
    async def run(self) -> None:
        log.info("Dispatcher gestartet")

        # Verwaiste Tasks aus vorherigen Sitzungen zurücksetzen (Container-Neustart
        # o.ä. — hier ist jeder 'in_progress'-Task garantiert verwaist)
        await self._cleanup_stale_tasks()

        while True:
            try:
                # 0. Worker-Einstellungen (Konkurrenz/Poll-Intervall/Lock-Timeout)
                #    frisch aus der DB übernehmen — macht sie über die Website
                #    live änderbar, ohne den Container neu zu starten.
                await self._load_worker_settings()

                # 1. Server-Stand synchronisieren
                servers = await self._load_servers()
                await self.pool.update(servers)

                # 2. Worker-Anzahl an Kapazität anpassen
                await self._scale_workers(self.pool.total_capacity())

                # 2b. Tasks zurücksetzen, die länger als lock_timeout_minutes ohne
                #     Fortschritt in Bearbeitung sind (z.B. Worker mitten in der
                #     Verarbeitung abgestürzt, kein sauberer Neustart).
                await self._cleanup_stale_tasks(min_age_minutes=self.lock_timeout_minutes)

                # 3. Neue Tasks in Queue legen (übersprungen wenn pausiert)
                if not self.paused:
                    tasks = await self._load_tasks()
                    for t in tasks:
                        await self.queue.put(t)

                    if tasks:
                        log.info(
                            f"{len(tasks)} neue Tasks, "
                            f"{len(self.workers)} Worker, "
                            f"Queue: {self.queue.qsize()}"
                        )

            except Exception:
                log.exception("Fehler im Dispatcher-Loop")

            await asyncio.sleep(self.poll_interval)

    async def shutdown(self) -> None:
        log.info("Shutdown läuft …")
        await asyncio.gather(*(w.stop() for w in self.workers))