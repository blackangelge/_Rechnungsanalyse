"""
ORM-Modell für Rechnungspositionen (Tabelle: order_positions).

Pro Dokument können beliebig viele Positionen angelegt werden. position_index
entspricht der Reihenfolge im Original-Rechnungsdokument (0-basiert) und wird
im Excel-Export und in der Infos-Ansicht verwendet, um die Positionen korrekt
der KI-Rohausgabe zuzuordnen (z.B. für den Steuersatz).
"""

from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OrderPosition(Base):
    """
    Eine einzelne Rechnungsposition (Zeile) aus einer Eingangsrechnung.

    position_index (0-basiert) gibt die Reihenfolge im Originaldokument an.
    Er wird auch genutzt, um im raw_response-JSON der KI den passenden Eintrag
    aus der 'positionen'-Liste zu finden (z.B. für Steuersatz-Auslese im Export).
    """

    __tablename__ = "order_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    position_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)  # 0-basierte Reihenfolge
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)    # Artikelbezeichnung
    product_description: Mapped[str | None] = mapped_column(Text, nullable=True)    # Detailbeschreibung
    article_number: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Artikelnummer des Lieferanten
    unit_price_netto: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)   # Einzelpreis netto
    unit_price_brutto: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True)  # Einzelpreis brutto
    tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)      # Steuerbetrag dieser Position
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4), nullable=True) # Menge
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)             # Mengeneinheit (z.B. "Stück", "kg")
    discount: Mapped[str | None] = mapped_column(String(100), nullable=True)        # Nachlass-Angabe (Freitext)

    document: Mapped["Document"] = relationship("Document", back_populates="order_positions")  # noqa: F821
