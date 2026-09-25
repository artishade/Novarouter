"""SQLAlchemy engine + session management (sync sessions; FastAPI endpoints
run sync DB work in threadpool via `Depends(get_db)` or direct `with Session`)."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import DATABASE_URL, IS_POSTGRES

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,          # survives Neon idle disconnects
    pool_recycle=280,
    future=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — always closes the session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_sqlite_dir() -> None:
    if DATABASE_URL.startswith("sqlite"):
        from pathlib import Path

        db_path = DATABASE_URL.replace("sqlite:///", "", 1)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def ping() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


__all__ = ["engine", "SessionLocal", "get_db", "ensure_sqlite_dir", "ping", "IS_POSTGRES"]
