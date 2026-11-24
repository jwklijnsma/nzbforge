import os
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

URL = os.getenv("DATABASE_URL", "sqlite:///./nzbforge.db")
engine = create_engine(
    URL,
    # timeout: web requests and the scanner thread share the SQLite file
    connect_args={"check_same_thread": False, "timeout": 30} if URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

if URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        # WAL: the scanner writes while the web UI keeps reading; NORMAL sync is safe with WAL and much faster
        cur = dbapi_conn.cursor()
        for pragma in ("journal_mode=WAL", "synchronous=NORMAL", "cache_size=-65536", "temp_store=MEMORY"):
            cur.execute(f"PRAGMA {pragma}")
        cur.close()


class Base(DeclarativeBase):
    pass


def init_db():
    from . import models  # noqa: F401  (registers tables)
    Base.metadata.create_all(engine)
    _add_missing_columns()


def _add_missing_columns():
    """Tiny migration: new nullable columns are added to tables that already exist."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"))
        for c in ("imdbid", "tvdbid", "tmdbid", "resolution", "source"):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_releases_{c} ON releases ({c})"))
        # batch lookup of collections by name (scanner.ingest)
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_collections_group_base ON collections (group_id, base)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_releases_title ON releases (title)"))
