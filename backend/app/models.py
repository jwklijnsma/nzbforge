from datetime import datetime
from sqlalchemy import String, BigInteger, DateTime, JSON, Boolean, ForeignKey, UniqueConstraint, Text, Float
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    api_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    last_article: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    server_first: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # from the last GROUP reply
    server_last: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Collection(Base):
    """Headers that have been seen but not yet turned into a release."""
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("group_id", "poster", "base"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"), index=True)
    poster: Mapped[str] = mapped_column(String(255))
    base: Mapped[str] = mapped_column(String(512))
    set_total: Mapped[int | None] = mapped_column(nullable=True)
    posted: Mapped[datetime] = mapped_column(DateTime)
    updated: Mapped[datetime] = mapped_column(DateTime, index=True)  # newest article date
    # {filename: {"total": N, "parts": {"1": [msgid, bytes], ...}}}
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Release(Base):
    __tablename__ = "releases"
    id: Mapped[int] = mapped_column(primary_key=True)
    guid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512))
    searchname: Mapped[str] = mapped_column(String(512), index=True)
    group_name: Mapped[str] = mapped_column(String(255))
    poster: Mapped[str] = mapped_column(String(255))
    posted: Mapped[datetime] = mapped_column(DateTime, index=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    category: Mapped[int] = mapped_column(index=True, default=7000)
    season: Mapped[int | None] = mapped_column(nullable=True)
    episode: Mapped[int | None] = mapped_column(nullable=True)
    files: Mapped[int] = mapped_column(default=1)
    grabs: Mapped[int] = mapped_column(default=0)
    # [{"name": str, "total": int, "parts": [[n, msgid, bytes], ...]}]
    files_data: Mapped[list] = mapped_column(JSON)

    # metadata (filled by enrich.py / NFO fetch); all nullable so old databases upgrade in place
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)       # parsed from the release name
    meta_title: Mapped[str | None] = mapped_column(String(512), nullable=True)  # canonical name from TVmaze/TMDB
    year: Mapped[int | None] = mapped_column(nullable=True)
    imdbid: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    tvdbid: Mapped[int | None] = mapped_column(nullable=True, index=True)
    tmdbid: Mapped[int | None] = mapped_column(nullable=True, index=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    nfo: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    nfo_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    genres: Mapped[str | None] = mapped_column(String(255), nullable=True)      # "Drama, Crime" (TVmaze/TMDB)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)     # TVmaze/TMDB page

    # Spotweb-style details parsed from the name (parse.release_info)
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    source: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    video_codec: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hdr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio: Mapped[str | None] = mapped_column(String(32), nullable=True)
    languages: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subtitles: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edition: Mapped[str | None] = mapped_column(String(128), nullable=True)
    release_group: Mapped[str | None] = mapped_column(String(32), nullable=True)
    info_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
