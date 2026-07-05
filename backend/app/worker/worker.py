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
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Protocol

import asyncpg

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
def build_server_url(ip_address: str, port: str) -> str:
    """Setzt die Basis-URL aus IP + Port zusammen.

    Falls ``ip_address`` bereits ein Schema enthält (z. B.
    ``https://api.openai.com``), wird der Port nicht angehängt,
    sofern er nicht schon Teil der Adresse ist.

    Freistehende Funktion (statt Methode), damit sie auch ohne
    ``AIServer``-Instanz genutzt werden kann (z. B. von den
    Status-Routen des Worker-Containers).
    """
    if ip_address.startswith(("http://", "https://")):
        base = ip_address.rstrip("/")
        if port and ":" not in base.split("//", 1)[1]:
            base = f"{base}:{port}"
        return base
    return f"http://{ip_address}:{port}"


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
    port: str
    parallel_request: int    # Slots (1-4)
    active: bool
    timeout_at: Optional[datetime]

    #: Komplette DB-Zeile als dict – für die externe AIClient-Klasse.
    raw: dict = field(default_factory=dict)

    #: Slot-Verwaltung; init=False, wird in __post_init__ gesetzt.
    semaphore: asyncio.Semaphore = field(init=False)

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

    @asynccontextmanager
    async def acquire_slot(self, timeout: float = 30.0):
        """Reserviert einen Slot auf einem aktiven, erreichbaren Server.

        "Geeignet" bedeutet:
          * ``active = True``
          * ``timeout_at IS NULL`` (Server gilt als erreichbar)

        Verwendung::

            async with pool.acquire_slot() as server:
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
                if s.active and not s.is_down
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

    async def _release_for_retry(self, task: Task) -> None:
        """Setzt die Task auf 'pending' zurück, damit sie beim nächsten
        Polling-Zyklus neu eingesammelt wird.

        Vorteil gegenüber direktem Requeue: Versuchszähler landet sofort
        in der DB und überlebt einen Crash.
        """
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE workflow_tasks
                   SET status='pending',
                       worker_id=NULL,
                       locked_at=NULL,
                       attempts=attempts+1,
                       updated_at=NOW()
                   WHERE id=$1""",
                task.id,
            )
        log.info(
            f"[{self.worker_id}] Task {task.id} zurück auf 'pending' "
            f"(Versuch {task.attempts + 1}/{task.max_attempts})"
        )

    async def _set_document_error(self, task: Task, reason: str) -> None:
        """Setzt das verknüpfte Dokument auf 'error', falls der Payload eine document_id enthält.

        Wird aufgerufen wenn ein Task endgültig fehlschlägt (max_attempts erreicht),
        damit das Dokument nicht dauerhaft im Status 'processing' hängt.
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

    async def _check_attempts_and_fail(self, task: Task) -> bool:
        """Prüft, ob ein weiterer Versuch das Limit überschreiten würde.

        Returns
        -------
        bool
            ``True``, wenn die Task als 'failed' markiert wurde
            (Aufrufer muss nichts mehr tun).
        """
        if task.attempts + 1 >= task.max_attempts:
            log.warning(
                f"[{self.worker_id}] Task {task.id} hat "
                f"max_attempts ({task.max_attempts}) erreicht"
            )
            await self._mark_failed(task, "max attempts exceeded")
            await self._set_document_error(task, "max attempts exceeded — KI nicht erreichbar")
            return True
        return False

    async def _claim_task(self, task: Task) -> None:
        """Setzt worker_id auf diese Worker-Instanz (Tracking)."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE workflow_tasks SET worker_id=$2, updated_at=NOW() WHERE id=$1",
                task.id, self.worker_id,
            )

    # ──────────────────────────────────────────────────────────────────
    async def _process(self, task: Task) -> None:
        await self._claim_task(task)

        try:
            async with self.pool.acquire_slot(
                timeout=SLOT_ACQUIRE_TIMEOUT,
            ) as server:
                log.info(
                    f"[{self.worker_id}] Task {task.id} auf {server.name}"
                )

                try:
                    result = await self.ai_client.send(server, task.payload)
                    await self._mark_completed(task, result)
                    log.info(f"[{self.worker_id}] Task {task.id} fertig")

                except ConnectionError as e:
                    # Server-Problem (timeout_at wurde von der AIClient-
                    # Klasse selbst gesetzt). Task zurück in den Pool.
                    log.error(
                        f"[{self.worker_id}] Verbindung zu {server.name} "
                        f"verloren: {e}"
                    )
                    if not await self._check_attempts_and_fail(task):
                        await self._release_for_retry(task)

                except Exception as e:
                    # Inhaltlicher Fehler – nicht erneut versuchen.
                    log.error(
                        f"[{self.worker_id}] Task {task.id} fehlgeschlagen: "
                        f"{type(e).__name__}: {e}",
                        exc_info=True,
                    )
                    await self._mark_failed(task, f"{type(e).__name__}: {e}")

        except TimeoutError:
            log.warning(
                f"[{self.worker_id}] Kein Server frei für Task {task.id}"
            )
            if not await self._check_attempts_and_fail(task):
                await self._release_for_retry(task)

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
    ) -> None:
        self.db_pool = db_pool
        self.ai_client = ai_client
        self.poll_interval = poll_interval
        self.max_workers_cap = max_workers_cap  # Globale Obergrenze für Worker-Anzahl

        self.queue: asyncio.Queue = asyncio.Queue()
        self.pool = ServerPool()
        self.workers: list[Worker] = []
        self._counter = 0

        #: Wenn True, werden keine neuen Tasks aus der DB geladen (siehe ``run()``).
        #: Bereits geladene/in Bearbeitung befindliche Tasks laufen weiter — Pause
        #: heißt "keine neue Arbeit annehmen", kein hartes Stoppen laufender Worker.
        self.paused: bool = False

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
                          parallel_request, timeout_at
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
        """Holt neue Tasks atomar aus der DB.

        Nutzt ``FOR UPDATE SKIP LOCKED`` für Parallelsicherheit. Setzt
        Status, locked_at und worker_id (Platzhalter) in einem Schritt.
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """UPDATE workflow_tasks
                   SET status='in_progress',
                       locked_at=NOW(),
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
    async def _cleanup_stale_tasks(self) -> None:
        """Setzt beim Start alle 'in_progress'-Tasks auf 'pending' zurück.

        Nach einem Absturz oder Container-Neustart sind Tasks, die sich noch im
        Status 'in_progress' befinden, verwaist — die Worker, die sie bearbeitet
        haben, existieren nicht mehr. Ohne diesen Reset würden sie nie wieder
        abgeholt, da _load_tasks nur 'pending'-Tasks sucht.
        """
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.execute(
                    """UPDATE workflow_tasks
                       SET status='pending',
                           worker_id=NULL,
                           locked_at=NULL,
                           updated_at=NOW()
                       WHERE status='in_progress'"""
                )
            # asyncpg gibt "UPDATE N" als String zurück
            count = int(result.split()[-1]) if result else 0
            if count:
                log.warning(
                    "Startup-Cleanup: %d verwaiste 'in_progress'-Tasks auf 'pending' zurückgesetzt",
                    count,
                )
        except Exception as exc:
            log.error("Startup-Cleanup fehlgeschlagen (nicht kritisch): %s", exc)

    # ──────────────────────────────────────────────────────────────────
    async def run(self) -> None:
        log.info("Dispatcher gestartet")

        # Verwaiste Tasks aus vorherigen Sitzungen zurücksetzen
        await self._cleanup_stale_tasks()

        while True:
            try:
                # 1. Server-Stand synchronisieren
                servers = await self._load_servers()
                await self.pool.update(servers)

                # 2. Worker-Anzahl an Kapazität anpassen
                await self._scale_workers(self.pool.total_capacity())

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


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
async def main() -> None:
    # Hier deine konkrete AIClient-Implementierung importieren/erstellen:
    # from my_ai_client import MyAIClient
    # ai_client = MyAIClient(db_pool)

    # Dummy für lokale Tests:
    class DummyAIClient:
        async def send(self, server: AIServer, payload: dict) -> dict:
            await asyncio.sleep(0.5)
            return {"echo": payload}

    ai_client = DummyAIClient()

    db_pool = await asyncpg.create_pool(
        "postgresql://user:pass@localhost/dbname",
        min_size=2,
        max_size=10,
    )

    dispatcher = Dispatcher(
        db_pool=db_pool,
        ai_client=ai_client,
        poll_interval=POLL_INTERVAL_SEC,
    )

    try:
        await dispatcher.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await dispatcher.shutdown()
        await db_pool.close()


#if __name__ == "__main__":
#    asyncio.run(main())