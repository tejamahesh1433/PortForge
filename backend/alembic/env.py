from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.models import Base  # noqa: F401 -- imports every model so Base.metadata is complete

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Precedence: an explicitly-configured Alembic sqlalchemy.url wins if one
# is already present (e.g. a caller did
# `cfg.set_main_option("sqlalchemy.url", TEST_URL)` before invoking
# `alembic.command.upgrade(cfg, ...)`, exactly what test_migrations.py
# does to run migrations against an isolated test database) -- only when
# Alembic has NOT already been given a meaningful URL do we fall back to
# the application's own Settings (env vars). alembic.ini deliberately
# leaves sqlalchemy.url unset (see that file's comment), so the normal
# `alembic upgrade head` CLI path still falls through to Settings exactly
# as before; only a caller that explicitly set a URL first now has that
# override respected instead of silently clobbered.
_explicit_url = config.get_main_option("sqlalchemy.url")
if not _explicit_url:
    config.set_main_option("sqlalchemy.url", get_settings().sqlalchemy_database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
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
