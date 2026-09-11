from logging.config import fileConfig

from sqlalchemy import pool, create_engine, text
from alembic import context

from app.core.config import settings
from app.core.database import Base
import app.models  # noqa: F401  # Ensure all SQLAlchemy models are registered for autogenerate.

# Import all models here so Alembic can detect them for autogenerate
# from app.models import user, wallet, transaction  # uncomment as you add models

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    is_postgres = settings.DATABASE_URL.lower().startswith("postgres")
    context_kwargs = {
        "url": settings.DATABASE_URL,
        "target_metadata": target_metadata,
        "literal_binds": True,
        "dialect_opts": {"paramstyle": "named"},
    }
    if is_postgres:
        context_kwargs["version_table_schema"] = "public"

    context.configure(**context_kwargs)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    is_postgres = settings.DATABASE_URL.lower().startswith("postgres")
    connect_args = {}
    if is_postgres:
        connect_args["options"] = "-csearch_path=public"

    connectable = create_engine(
        settings.DATABASE_URL,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    with connectable.connect() as connection:
        context_kwargs = {
            "connection": connection,
            "target_metadata": target_metadata,
        }
        if is_postgres or connection.dialect.name == "postgresql":
            connection.execute(text("SET search_path TO public"))
            connection.commit()
            context_kwargs["version_table_schema"] = "public"

        context.configure(**context_kwargs)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
