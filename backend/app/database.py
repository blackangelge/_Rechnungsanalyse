"""
Datenbankverbindung und Session-Management.

Stellt zwei Engines bereit:
- Synchrone Engine (engine / SessionLocal): für alle normalen API-Router via get_db()
- Asynchrone Engine (async_engine / AsyncSessionLocal): für Hintergrund-Tasks
  (Import-Service, SSE-Generator), um den asyncio Event-Loop nicht zu blockieren.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# ─── Synchrone Engine ────────────────────────────────────────────────────────
# Wird von allen normalen FastAPI-Routen über die get_db()-Dependency verwendet.
# pool_pre_ping=True prüft die Verbindung vor jeder Abfrage und reconnectet bei Bedarf.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    # pool_size: Permanente Verbindungen im Pool.
    # Mit dem neuen Session-Design (kurze Verbindungen pro DB-Phase) reichen 10.
    # Jeder Task hält eine Verbindung nur für ~50 ms (Commit), nicht für die
    # gesamte Datei-Kopier-Dauer.
    pool_size=10,
    # max_overflow: Zusätzliche Verbindungen bei kurzzeitigen Spitzen.
    max_overflow=20,
    # pool_timeout: Wie lange auf eine freie Verbindung gewartet wird (Sekunden).
    # Standard ist 30 — erhöht auf 60 für sehr hohe Parallelität.
    pool_timeout=60,
    # pool_recycle: Verbindungen nach 1 Stunde neu aufbauen (verhindert stale connections).
    pool_recycle=3600,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ─── Asynchrone Engine ───────────────────────────────────────────────────────
# asyncpg-Treiber benötigt postgresql+asyncpg:// statt postgresql://
# Wir ersetzen das Schema automatisch, damit .env nur eine URL braucht.
_async_url = settings.database_url.replace(
    "postgresql://", "postgresql+asyncpg://", 1
).replace(
    "postgresql+psycopg2://", "postgresql+asyncpg://", 1
)
async_engine = create_async_engine(_async_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


# ─── Basis-Klasse für alle ORM-Modelle ───────────────────────────────────────
class Base(DeclarativeBase):
    """Gemeinsame Basisklasse aller SQLAlchemy-Modelle."""
    pass


# ─── Dependency für FastAPI-Router ───────────────────────────────────────────
def get_db():
    """
    FastAPI-Dependency, die eine synchrone Datenbank-Session erzeugt und nach
    dem Request automatisch schließt.

    Verwendung in Routen:
        def my_endpoint(db: Session = Depends(get_db)): ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """
    Async-Dependency für FastAPI-Router, die eine asynchrone Session benötigen.
    Primär für den SSE-Endpunkt gedacht.
    """
    async with AsyncSessionLocal() as session:
        yield session
