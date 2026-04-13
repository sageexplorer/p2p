"""SQLAlchemy engine, session factory, and Base class.

SQLite is fine for the demo — single file, zero infra. `check_same_thread=False`
is required because FastAPI handles requests on multiple threads but a single
SQLite connection is otherwise pinned to its creating thread.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DATABASE_URL = "sqlite:///./p2p.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


def get_db():
    """FastAPI dependency that yields a DB session and ensures it's closed."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
