from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_upgrade_head_builds_operational_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    tables = set(inspect(create_engine(database_url)).get_table_names())
    assert {
        "alembic_version",
        "canonical_records",
        "close_decisions",
        "delivery_controls",
        "exception_actions",
        "exceptions",
        "ingestion_runs",
        "reconciliation_runs",
        "source_versions",
    } <= tables
