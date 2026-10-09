"""
Pruebas de adquisición y liberación de locks distribuidos para jobs en PostgreSQL.
tests/test_job_lock.py
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.database import engine
from app.core.job_lock import intentar_tomar_lock_job, liberar_lock_job


def test_job_lock_postgresql_e1():
    url_str = str(engine.url)
    if "postgresql" not in engine.dialect.name or ":5433" not in url_str:
        pytest.skip("test_job_lock solo corre contra PostgreSQL local en el puerto 5433")

    # Dejar 3 conexiones ociosas en el pool
    conns_ociosas = [engine.connect() for _ in range(3)]
    for c in conns_ociosas:
        c.execute(text("SELECT 1"))
        c.close()

    SessionTest = sessionmaker(bind=engine)

    # 6 corridas seguidas de: tomar lock, SELECT 1 y commit en la Session, liberar
    for i in range(6):
        session = SessionTest()
        try:
            lock_ok = intentar_tomar_lock_job(session, "test_job_lock_e1")
            assert lock_ok is True, f"Fallo al tomar lock en la corrida {i + 1}"
            res = session.execute(text("SELECT 1")).scalar()
            assert res == 1
            session.commit()
        finally:
            liberar_lock_job(session, "test_job_lock_e1")
            session.close()

    # Al final, una conexión nueva toma pg_try_advisory_lock(hashtext('test_job_lock_e1')) -> True (y lo libera)
    with engine.connect() as conn:
        adquirido = conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext('test_job_lock_e1'))")
        ).scalar()
        assert bool(adquirido) is True, "El lock no quedó disponible tras las liberaciones"

        conn.execute(
            text("SELECT pg_advisory_unlock(hashtext('test_job_lock_e1'))")
        )
        conn.commit()
