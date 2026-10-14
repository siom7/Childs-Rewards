from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def upgrade_schema() -> None:
    """Crée les tables manquantes et applique les petites évolutions de schéma des versions précédentes."""
    from . import models  # noqa: F401  (enregistre les tables)

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        columns = {c["name"] for c in inspect(conn).get_columns("children")}
        if "active" not in columns:  # v1 -> v2 : enfants masquables
            conn.execute(text("ALTER TABLE children ADD COLUMN active BOOLEAN NOT NULL DEFAULT 1"))
        if "archived_points" not in columns:  # rétention de l'historique
            conn.execute(text("ALTER TABLE children ADD COLUMN archived_points INTEGER NOT NULL DEFAULT 0"))
        installed = conn.execute(text("SELECT 1 FROM users LIMIT 1")).first() is not None
        has_language = conn.execute(text("SELECT 1 FROM app_settings WHERE key = 'language'")).first()
        if installed and not has_language:  # v1 installée avant le choix de la langue
            conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('language', 'fr')"))
