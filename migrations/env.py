import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

# Import each persistence module so its tables are registered on shared metadata.
import control_tower.canonical.repository
import control_tower.close_control.repository
import control_tower.exceptions.repository
import control_tower.reconciliation.repository  # noqa: F401
from control_tower.ingestion.registry import Base

config = context.config
if database_url := os.getenv("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", database_url)
configured_url = make_url(config.get_main_option("sqlalchemy.url"))
if configured_url.drivername == "sqlite" and configured_url.database != ":memory:":
    Path(configured_url.database).parent.mkdir(parents=True, exist_ok=True)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
