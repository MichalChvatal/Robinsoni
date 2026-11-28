"""Programmatic migrations use the caller's explicit SQLite transaction."""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations through open_database, not an unbound Alembic command")

context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
