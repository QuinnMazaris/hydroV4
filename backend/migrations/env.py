import os
import sys

# Ensure the project root is on sys.path before importing application modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from alembic import context
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool

from backend.config import settings
from backend.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_database_url() -> str:
    url = settings.database_url
    if url.startswith('sqlite+aiosqlite'):
        return url.replace('+aiosqlite', '')
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_database_url(),
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
        url=_sync_database_url(),
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
