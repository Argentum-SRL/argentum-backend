import contextvars
import time
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
import structlog
from app.core.config import settings

db_query_duration_var: contextvars.ContextVar[list[float] | None] = contextvars.ContextVar("db_query_duration_var", default=None)
db_logger = structlog.get_logger("database")

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_recycle=3600,
    pool_pre_ping=True
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("query_start_times", []).append(time.perf_counter())


@event.listens_for(engine, "after_cursor_execute")
def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    start_times = conn.info.get("query_start_times")
    if start_times:
        start_time = start_times.pop()
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    else:
        elapsed_ms = 0.0

    # Acumular tiempo de base de datos en el ContextVar (contenedor mutable compartido)
    container = db_query_duration_var.get()
    if container is not None:
        container[0] += elapsed_ms

    # Logging con structlog respetando seguridad de datos
    stmt_type = statement.strip().split()[0].upper() if statement else "UNKNOWN"
    if settings.ENVIRONMENT == "development":
        stmt_preview = statement.strip().replace("\n", " ")[:200]
        db_logger.debug(
            "db_query",
            statement_type=stmt_type,
            statement=stmt_preview,
            duration_ms=round(elapsed_ms, 2),
        )
    else:
        db_logger.debug(
            "db_query",
            statement_type=stmt_type,
            duration_ms=round(elapsed_ms, 2),
        )


@event.listens_for(engine, "handle_error")
def handle_error(exception_context):
    conn = exception_context.connection
    if conn:
        start_times = conn.info.get("query_start_times")
        if start_times:
            start_time = start_times.pop()
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            container = db_query_duration_var.get()
            if container is not None:
                container[0] += elapsed_ms


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
