import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from mr_quality_monitor.database import migration
from mr_quality_monitor.database.database import open_database
from mr_quality_monitor.database.models import Base
from sqlalchemy import URL, create_engine, inspect


def create_intake_v1(path, *, legacy=True):
    engine = create_engine(URL.create("sqlite", database=str(path)))
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            config = Config()
            config.set_main_option(
                "script_location", str(Path(migration.__file__).parent / "migrations")
            )
            config.attributes["connection"] = connection
            command.upgrade(config, "0001_intake")
            connection.exec_driver_sql(
                "INSERT INTO system_events (occurred_at,code,correlation_id) VALUES (?,?,?)",
                ("2026-11-27T12:00:00+00:00", "synthetic_legacy_event", "test-only"),
            )
            if legacy:
                connection.exec_driver_sql("DROP TABLE alembic_version")
            connection.commit()
    finally:
        engine.dispose()


def read_rows(path, sql):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


@pytest.mark.parametrize("legacy", [True, False])
def test_upgrade_preserves_intake_rows_and_creates_backup(tmp_path, legacy):
    path = tmp_path / "intake.sqlite3"
    create_intake_v1(path, legacy=legacy)
    original = read_rows(path, "SELECT * FROM system_events")
    source_file = tmp_path / "original.dcm"
    source_file.write_bytes(b"synthetic source bytes untouched by DB migration")
    engine, _sessions = open_database(path)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT version FROM schema_version").scalar() == 3
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
                == migration.HEAD
            )
            assert set(Base.metadata.tables) <= set(inspect(connection).get_table_names())
        assert read_rows(path, "SELECT * FROM system_events") == original
        backups = list(tmp_path.glob("*.pre-history-*.sqlite3"))
        assert len(backups) == 1
        assert read_rows(backups[0], "SELECT version FROM schema_version") == [(1,)]
        assert read_rows(backups[0], "SELECT * FROM system_events") == original
        assert source_file.read_bytes() == b"synthetic source bytes untouched by DB migration"
    finally:
        engine.dispose()
    reopened, _ = open_database(path)
    reopened.dispose()
    assert len(list(tmp_path.glob("*.pre-history-*.sqlite3"))) == 1


def test_failed_migration_rolls_back_ddl_and_legacy_adoption(tmp_path, monkeypatch):
    path = tmp_path / "intake.sqlite3"
    create_intake_v1(path)
    original = migration.command.upgrade

    def fail(config, revision):
        config.attributes["connection"].exec_driver_sql("CREATE TABLE should_rollback (id INTEGER)")
        raise RuntimeError("synthetic migration crash")

    monkeypatch.setattr(migration.command, "upgrade", fail)
    with pytest.raises(RuntimeError, match="synthetic migration"):
        open_database(path)
    assert read_rows(path, "SELECT version FROM schema_version") == [(1,)]
    tables = {
        row[0] for row in read_rows(path, "SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert tables == migration.LEGACY_TABLES
    monkeypatch.setattr(migration.command, "upgrade", original)
    engine, _ = open_database(path)
    engine.dispose()
    assert read_rows(path, "SELECT version FROM schema_version") == [(3,)]


@pytest.mark.parametrize("kind", ["future", "unknown", "missing_column"])
def test_unrecognized_schema_is_rejected_without_new_tables(tmp_path, kind):
    path = tmp_path / "unknown.sqlite3"
    if kind == "missing_column":
        create_intake_v1(path)
        connection = sqlite3.connect(path)
        connection.execute("ALTER TABLE intake_records DROP COLUMN checksum")
    else:
        connection = sqlite3.connect(path)
        if kind == "future":
            connection.execute(
                "CREATE TABLE schema_version (id INTEGER PRIMARY KEY,version INTEGER)"
            )
            connection.execute("INSERT INTO schema_version VALUES (1,999)")
        else:
            connection.execute("CREATE TABLE unknown_table (id INTEGER)")
    connection.commit()
    connection.close()
    before = read_rows(path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    with pytest.raises(RuntimeError):
        open_database(path)
    assert (
        read_rows(path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name") == before
    )


def test_fresh_schema_matches_model_columns_and_foreign_keys(tmp_path):
    engine, _ = open_database(tmp_path / "new.sqlite3")
    try:
        inspector = inspect(engine)
        for name, table in Base.metadata.tables.items():
            assert {column["name"] for column in inspector.get_columns(name)} == set(
                table.columns.keys()
            )
            expected = {
                (fk.parent.name, fk.column.table.name, fk.column.name) for fk in table.foreign_keys
            }
            actual = {
                (
                    item["constrained_columns"][0],
                    item["referred_table"],
                    item["referred_columns"][0],
                )
                for item in inspector.get_foreign_keys(name)
            }
            assert actual == expected
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()


def test_legacy_with_matching_columns_but_missing_primary_key_is_rejected(tmp_path):
    path = tmp_path / "malformed.sqlite3"
    create_intake_v1(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE system_events RENAME TO saved_events")
        connection.execute(
            "CREATE TABLE system_events (id INTEGER, occurred_at VARCHAR NOT NULL, "
            "code VARCHAR NOT NULL, correlation_id VARCHAR)"
        )
        connection.execute("INSERT INTO system_events SELECT * FROM saved_events")
        connection.execute("DROP TABLE saved_events")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(RuntimeError, match="Malformed legacy"):
        open_database(path)
    assert read_rows(path, "SELECT version FROM schema_version") == [(1,)]
