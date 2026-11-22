import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

URL = os.getenv("DATABASE_URL", "sqlite:///./nzbforge.db")
engine = create_engine(
    URL,
    connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


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
        for c in ("imdbid", "tvdbid", "tmdbid"):
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_releases_{c} ON releases ({c})"))
