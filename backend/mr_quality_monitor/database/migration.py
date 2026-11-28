"""Serialized transactional migration of empty, legacy-v1 and Alembic databases."""

import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import UniqueConstraint, inspect
from sqlalchemy.engine import Engine

from .models import Base

HEAD = "0003_pacs_analytics"
LEGACY_TABLES = {
    "schema_version",
    "intake_records",
    "dicom_instances",
    "reception_events",
    "analysis_jobs",
    "system_events",
}


def backup_database(path: Path) -> Path:
    """SQLite online backup includes committed WAL contents and never overwrites a backup."""
    destination = path.with_name(f"{path.name}.pre-history-{uuid4().hex}.sqlite3")
    try:
        with (
            closing(sqlite3.connect(str(path))) as source,
            closing(sqlite3.connect(str(destination))) as target,
        ):
            source.backup(target)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def migrate(engine: Engine, path: Path) -> None:
    """Hold SQLite's writer reservation across compatibility checks, DDL and version stamp."""
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            tables = set(inspect(connection).get_table_names())
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
            config.attributes["connection"] = connection
            if not tables:
                command.upgrade(config, HEAD)
            else:
                if "schema_version" not in tables:
                    raise RuntimeError("Unrecognized database: no schema version")
                versions = connection.exec_driver_sql("SELECT id,version FROM schema_version").all()
                if "alembic_version" not in tables:
                    if versions != [(1, 1)] or tables != LEGACY_TABLES:
                        raise RuntimeError("Unsupported legacy database schema")
                    inspector = inspect(connection)
                    for name in LEGACY_TABLES:
                        table = Base.metadata.tables[name]
                        actual = {column["name"] for column in inspector.get_columns(name)}
                        primary = inspector.get_pk_constraint(name)["constrained_columns"]
                        expected_unique = {
                            tuple(column.name for column in constraint.columns)
                            for constraint in table.constraints
                            if isinstance(constraint, UniqueConstraint)
                        }
                        actual_unique = {
                            tuple(item["column_names"])
                            for item in inspector.get_unique_constraints(name)
                        }
                        expected_fk = {
                            (fk.parent.name, fk.column.table.name, fk.column.name)
                            for fk in table.foreign_keys
                        }
                        actual_fk = {
                            (
                                item["constrained_columns"][0],
                                item["referred_table"],
                                item["referred_columns"][0],
                            )
                            for item in inspector.get_foreign_keys(name)
                        }
                        if (
                            actual != set(table.columns.keys())
                            or tuple(primary) != tuple(column.name for column in table.primary_key)
                            or actual_unique != expected_unique
                            or actual_fk != expected_fk
                        ):
                            raise RuntimeError("Malformed legacy intake schema")
                    if connection.exec_driver_sql("PRAGMA foreign_key_check").all():
                        raise RuntimeError("Legacy intake contains broken foreign-key references")
                    backup_database(path)
                    command.stamp(config, "0001_intake")
                    command.upgrade(config, HEAD)
                else:
                    revisions = connection.exec_driver_sql(
                        "SELECT version_num FROM alembic_version"
                    ).all()
                    if revisions == [("0001_intake",)] and versions == [(1, 1)]:
                        backup_database(path)
                        command.upgrade(config, HEAD)
                    elif revisions == [("0002_history",)] and versions == [(1, 2)]:
                        backup_database(path)
                        command.upgrade(config, HEAD)
                    elif revisions != [(HEAD,)] or versions not in ([(1, 2)], [(1, 3)]):
                        raise RuntimeError("Unsupported database schema revision")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
