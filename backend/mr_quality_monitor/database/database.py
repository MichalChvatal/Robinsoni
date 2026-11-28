"""Local SQLite setup and schema compatibility guard."""

from pathlib import Path

from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .migration import migrate


def open_database(path: Path) -> tuple[Engine, sessionmaker[Session]]:
    """Migrate recognized schemas, refusing unsupported or unidentified databases.

    Sessions are per operation. SQLite serializes writers with a bounded busy wait.
    Existing intake data is retained and backed up before the history migration.
    """
    engine = create_engine(URL.create("sqlite", database=str(path)), connect_args={"timeout": 10})

    @event.listens_for(engine, "connect")
    def configure_connection(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA synchronous=FULL")
        cursor.close()

    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        migrate(engine, path)
    except Exception:
        engine.dispose()
        raise
    sessions = sessionmaker(engine, expire_on_commit=False)
    return engine, sessions
