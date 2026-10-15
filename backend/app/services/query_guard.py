"""
Schutz für teure Abfragen bei sehr großen Beständen (hunderttausende Belege, Millionen Positionen).

  • ``statement_timeout(db, seconds)`` — begrenzt die Laufzeit einer Abfrage in PostgreSQL (``SET LOCAL``, gilt nur
    für die aktuelle Transaktion); überschritten → ``QueryTooSlow`` (Router: HTTP 504 mit verständlicher Meldung).
    Auf anderen Datenbanken (Tests mit SQLite) ohne Wirkung.
  • ``TTLCache`` — winziger Zwischenspeicher im Prozess für Werte, die sich selten ändern und teuer zu berechnen
    sind (Filter-Auswahllisten, Kennzahlen). Bewusst ohne Invalidierung: kurze Lebensdauer genügt.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


class QueryTooSlow(Exception):
    """Die Abfrage hat das Zeitlimit überschritten."""


@contextmanager
def statement_timeout(db: Session, seconds: float):
    if db.get_bind().dialect.name != "postgresql":
        yield
        return
    db.execute(text(f"SET LOCAL statement_timeout = {int(seconds * 1000)}"))
    try:
        yield
    except DBAPIError as exc:
        # SQLSTATE 57014 = query_canceled (statement timeout)
        if getattr(getattr(exc, "orig", None), "pgcode", None) == "57014":
            db.rollback()
            raise QueryTooSlow(f"Abfrage nach {seconds:g} s abgebrochen") from exc
        raise


_ALL_CACHES: list["TTLCache"] = []


def clear_all_caches() -> None:
    """Alle Zwischenspeicher leeren — nach Änderungen, die Auswahllisten/Kennzahlen sofort ändern müssen
    (Imports aus-/einblenden oder löschen)."""
    for cache in _ALL_CACHES:
        cache.clear()


class TTLCache:
    def __init__(self, ttl_seconds: float, max_items: int = 64):
        self.ttl, self.max_items = ttl_seconds, max_items
        _ALL_CACHES.append(self)
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: Any, compute: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = compute()                         # außerhalb der Sperre: parallele Anfragen blockieren sich nicht
        with self._lock:
            if len(self._data) >= self.max_items:
                self._data.clear()
            self._data[key] = (time.monotonic(), value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
