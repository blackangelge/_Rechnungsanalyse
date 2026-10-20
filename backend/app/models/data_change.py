"""
Änderungsprotokoll: jede Datenänderung durch Nachrüst-Läufe, Daten-Browser und KI-Assistent (mit Alt- und Neuwert).

`data_change_runs` = ein Vorgang (z. B. ein Nachrüst-Lauf), `data_changes` = die einzelnen Feldänderungen. Damit lässt sich ein Vorgang
zurücknehmen (siehe services/change_log.py): zurückgesetzt wird nur, wo der aktuelle Wert noch dem protokollierten Neuwert entspricht.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

_BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class DataChangeRun(Base):
    __tablename__ = "data_change_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # backfill_rules | backfill_normalize | backfill_restore | browser | assistant
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    changed_rows: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    changed_fields: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undo_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class DataChange(Base):
    __tablename__ = "data_changes"

    id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("data_change_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(60), nullable=False)
    row_id: Mapped[int] = mapped_column(Integer, nullable=False)
    document_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    field: Mapped[str] = mapped_column(String(60), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0 = offen (kann zurückgesetzt werden) · 1 = zurückgesetzt · 2 = übersprungen (Wert wurde danach erneut geändert)
    status: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
