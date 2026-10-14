import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase


def _normalize(url: str) -> str:
    # Cloud providers hand out "postgres://" or "postgresql://"; SQLAlchemy needs the driver named.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


# Local: SQLite file. Cloud: set DATABASE_URL to your PostgreSQL connection string.
DATABASE_URL = _normalize(os.getenv("DATABASE_URL", "sqlite:///./stockroom.db"))
IS_SQLITE = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if IS_SQLITE else {},
    pool_pre_ping=True,  # survives cloud DBs that close idle connections
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def migrate():
    """Adds the new record columns to tables created by the first version."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "stock_movements" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("stock_movements")}
    with engine.begin() as c:
        if "record_date" not in cols:
            c.execute(text("ALTER TABLE stock_movements ADD COLUMN record_date DATE"))
            c.execute(text("UPDATE stock_movements SET record_date = DATE(created_at)"))
        if "initials" not in cols:
            c.execute(text("ALTER TABLE stock_movements ADD COLUMN initials VARCHAR(10) DEFAULT ''"))
        if "note" not in cols:
            c.execute(text("ALTER TABLE stock_movements ADD COLUMN note VARCHAR(300) DEFAULT ''"))
