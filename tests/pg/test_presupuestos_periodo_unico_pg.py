from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
import threading
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import (
    EstadoPresupuesto,
    PeriodoPresupuestoTipo,
    Presupuesto,
    RenovacionPresupuesto,
)
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import presupuesto_service


def _cargar_modulo_migracion():
    ruta = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "a6b7c8d9e0f2_periodo_presupuesto_unico.py"
    )
    spec = importlib.util.spec_from_file_location("mig_a6b7c8d9e0f2", ruta)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _crear_presupuesto_pg(db) -> tuple[Usuario, Presupuesto]:
    u = Usuario(
        id=uuid4(),
        email=f"pg_pres_{uuid4().hex[:8]}@test.com",
        nombre="Usuario PG Presupuesto",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    cat = Categoria(
        id=uuid4(),
        nombre=f"CatPG_{uuid4().hex[:6]}",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    pres = Presupuesto(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Presupuesto Único PG",
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
    )
    pc = PresupuestoCategoria(
        id=uuid4(),
        presupuesto_id=pres.id,
        categoria_id=cat.id,
    )
    db.add_all([u, cat, pres, pc])
    db.commit()
    return u, pres


def test_te4_concurrencia_asegurar_periodo_y_restriccion_unica(db, SessionTesting):
    """TE.4: Dos hilos llaman a asegurar_periodo a la vez (Barrier): 0 errores y 1 fila; insert duplicado da IntegrityError."""
    u, pres = _crear_presupuesto_pg(db)
    pres_id = pres.id
    hoy = date(2026, 10, 15)

    barrier = threading.Barrier(2)
    errores: list[BaseException] = []
    resultados: list[str] = []

    def worker() -> None:
        s = SessionTesting()
        try:
            p = s.get(Presupuesto, pres_id)
            assert p is not None
            # Cargamos p.periodos vacío antes del barrier para que ambos hilos intenten insertar
            _ = list(p.periodos)
            barrier.wait(timeout=10)
            per = presupuesto_service.asegurar_periodo(s, p, hoy)
            s.commit()
            assert per is not None
            resultados.append(str(per.id))
        except BaseException as exc:
            s.rollback()
            errores.append(exc)
        finally:
            s.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert errores == [], f"Se esperaban 0 errores y hubo: {errores}"
    assert len(resultados) == 2
    assert resultados[0] == resultados[1]

    db.expire_all()
    filas = (
        db.query(PeriodoPresupuesto)
        .filter(PeriodoPresupuesto.presupuesto_id == pres_id)
        .all()
    )
    assert len(filas) == 1

    # Insertar a mano una fila con el mismo presupuesto_id y fecha_inicio da IntegrityError
    duplicado = PeriodoPresupuesto(
        id=uuid4(),
        presupuesto_id=pres_id,
        fecha_inicio=filas[0].fecha_inicio,
        fecha_fin=filas[0].fecha_fin,
        monto_limite=Decimal("100000.00"),
        monto_usado=Decimal("0.00"),
        superado=False,
    )
    db.add(duplicado)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_te4_migracion_datos_idempotencia_y_reversion(db, pg_engine):
    """TE.4: _migrar_datos deja la más vieja de 3 y 2 en respaldo; segunda corrida no cambia nada; _revertir_datos deja volcado idéntico."""
    _, pres = _crear_presupuesto_pg(db)
    pres_id = pres.id
    mod_mig = _cargar_modulo_migracion()

    id_vieja = uuid4()
    id_media = uuid4()
    id_nueva = uuid4()

    sql_volcado = text(
        """
        SELECT
            id::text,
            presupuesto_id::text,
            fecha_inicio::text,
            fecha_fin::text,
            monto_limite::text,
            monto_usado::text,
            superado::text,
            to_char(fecha_creacion AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US')
        FROM periodos_presupuesto
        WHERE presupuesto_id = :pid
        ORDER BY id ASC
        """
    )

    with pg_engine.begin() as conn:
        # Estado previo a la migración: sin restricción única ni tabla de respaldo
        conn.execute(
            text(
                "ALTER TABLE periodos_presupuesto "
                "DROP CONSTRAINT IF EXISTS uq_periodos_presupuesto_presupuesto_inicio"
            )
        )
        conn.execute(text("DROP TABLE IF EXISTS respaldo_periodos_a6b7"))

        filas_insertar = [
            (
                str(id_media),
                str(pres_id),
                date(2026, 10, 1),
                date(2026, 10, 31),
                Decimal("100000.00"),
                Decimal("20000.00"),
                False,
                datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc),
            ),
            (
                str(id_vieja),
                str(pres_id),
                date(2026, 10, 1),
                date(2026, 10, 31),
                Decimal("100000.00"),
                Decimal("10000.00"),
                False,
                datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc),
            ),
            (
                str(id_nueva),
                str(pres_id),
                date(2026, 10, 1),
                date(2026, 10, 31),
                Decimal("100000.00"),
                Decimal("30000.00"),
                False,
                datetime(2026, 10, 1, 14, 0, 0, tzinfo=timezone.utc),
            ),
        ]
        for fid, pid, fini, ffin, mlim, musado, sup, fcrea in filas_insertar:
            conn.execute(
                text(
                    """
                    INSERT INTO periodos_presupuesto
                        (id, presupuesto_id, fecha_inicio, fecha_fin, monto_limite, monto_usado, superado, fecha_creacion)
                    VALUES
                        (:id, :pid, :fini, :ffin, :mlim, :musado, :sup, :fcrea)
                    """
                ),
                {
                    "id": fid,
                    "pid": pid,
                    "fini": fini,
                    "ffin": ffin,
                    "mlim": mlim,
                    "musado": musado,
                    "sup": sup,
                    "fcrea": fcrea,
                },
            )

        volcado_antes = [tuple(r) for r in conn.execute(sql_volcado, {"pid": str(pres_id)}).fetchall()]
        assert len(volcado_antes) == 3

        # 1ra corrida de _migrar_datos
        mod_mig._migrar_datos(conn)

        quedan_1 = [
            r[0]
            for r in conn.execute(
                text("SELECT id::text FROM periodos_presupuesto WHERE presupuesto_id = :pid ORDER BY id ASC"),
                {"pid": str(pres_id)},
            ).fetchall()
        ]
        respaldo_1 = [
            r[0]
            for r in conn.execute(
                text("SELECT id::text FROM respaldo_periodos_a6b7 WHERE presupuesto_id = :pid ORDER BY id ASC"),
                {"pid": str(pres_id)},
            ).fetchall()
        ]
        assert quedan_1 == [str(id_vieja)]
        assert respaldo_1 == sorted([str(id_media), str(id_nueva)])

        # 2da corrida de _migrar_datos (idempotencia)
        mod_mig._migrar_datos(conn)

        quedan_2 = [
            r[0]
            for r in conn.execute(
                text("SELECT id::text FROM periodos_presupuesto WHERE presupuesto_id = :pid ORDER BY id ASC"),
                {"pid": str(pres_id)},
            ).fetchall()
        ]
        respaldo_2 = [
            r[0]
            for r in conn.execute(
                text("SELECT id::text FROM respaldo_periodos_a6b7 WHERE presupuesto_id = :pid ORDER BY id ASC"),
                {"pid": str(pres_id)},
            ).fetchall()
        ]
        assert quedan_2 == quedan_1
        assert respaldo_2 == respaldo_1

        # Reversión con _revertir_datos: el volcado queda idéntico al de antes
        mod_mig._revertir_datos(conn)
        volcado_despues = [tuple(r) for r in conn.execute(sql_volcado, {"pid": str(pres_id)}).fetchall()]
        assert volcado_despues == volcado_antes

        # Dejamos la restricción activa nuevamente para el resto de la suite
        mod_mig._migrar_datos(conn)
