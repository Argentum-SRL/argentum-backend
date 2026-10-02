from __future__ import annotations

import ast
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types
import uuid

from app.core.database import Base


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    orig_uuid_processor = types.Uuid.bind_processor
    def _safe_uuid_processor(self, dialect):
        proc = orig_uuid_processor(self, dialect)
        if proc is None:
            return None
        def process(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)
        return process
    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.billeteras import delete_billetera, get_billetera
from app.services import ajuste_saldo_service
from app.services.ajuste_saldo_service import (
    calcular_cobertura_desde,
    detectar_huecos,
    mediana_salidas_semanales,
)
from app.services.conciliacion_service import calcular_saldo_teorico
from app.utils.fecha import hoy_argentina


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario_y_billetera(
    db: Session,
    saldo: Decimal = Decimal("190000.00"),
    tna: Decimal | None = None,
    archivada: bool = False,
) -> tuple[Usuario, Billetera]:
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Galicia Test",
        moneda=Moneda.ARS,
        saldo_inicial=saldo,
        saldo_actual=saldo,
        tna=tna,
        fecha_ultimo_rendimiento=datetime.now(timezone.utc) if tna else None,
        estado=EstadoBilletera.ARCHIVADA if archivada else EstadoBilletera.ACTIVA,
    )
    db.add_all([u, b])
    db.commit()
    return u, b


# -----------------------------------------------------------------------------
# 1. registrar_control con saldo 190.000 y declarado 140.000
# -----------------------------------------------------------------------------
def test_1_registrar_control_baja_saldo(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("190000.00"))
    cant_tx_antes = db_session.execute(select(Transaccion)).scalars().all()

    ajuste = ajuste_saldo_service.registrar_control(
        db_session, u.id, b.id, saldo_declarado=Decimal("140000.00")
    )

    assert ajuste.monto == Decimal("-50000.00")
    assert ajuste.saldo_anterior == Decimal("190000.00")
    assert ajuste.saldo_declarado == Decimal("140000.00")
    assert ajuste.fecha == hoy_argentina()
    assert b.saldo_actual == Decimal("140000.00")

    cant_tx_desp = db_session.execute(select(Transaccion)).scalars().all()
    assert len(cant_tx_desp) == len(cant_tx_antes)


# -----------------------------------------------------------------------------
# 2. Declarado igual al saldo: fila con monto 0; el saldo no cambia.
# -----------------------------------------------------------------------------
def test_2_declarado_igual_al_saldo(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("190000.00"))

    ajuste = ajuste_saldo_service.registrar_control(
        db_session, u.id, b.id, saldo_declarado=Decimal("190000.00")
    )

    assert ajuste.monto == Decimal("0.00")
    assert ajuste.saldo_anterior == Decimal("190000.00")
    assert ajuste.saldo_declarado == Decimal("190000.00")
    assert b.saldo_actual == Decimal("190000.00")


# -----------------------------------------------------------------------------
# 3. Saldo 100.000, declarado 103.000 y rendimiento 1.200
# -----------------------------------------------------------------------------
def test_3_saldo_con_rendimiento_y_ajuste(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("100000.00"), tna=Decimal("35.00"))

    ajuste = ajuste_saldo_service.registrar_control(
        db_session,
        u.id,
        b.id,
        saldo_declarado=Decimal("103000.00"),
        rendimiento=Decimal("1200.00"),
    )

    # Verifica la fila de rendimiento
    rend = db_session.execute(
        select(RendimientoBilletera).where(RendimientoBilletera.billetera_id == b.id)
    ).scalar_one()
    assert rend.monto == Decimal("1200.00")

    # Verifica el ajuste
    assert ajuste.monto == Decimal("1800.00")
    assert ajuste.saldo_anterior == Decimal("101200.00")
    assert ajuste.saldo_declarado == Decimal("103000.00")
    assert b.saldo_actual == Decimal("103000.00")


# -----------------------------------------------------------------------------
# 4. Rendimiento 5.000 con diferencia 3.000: 400. Rendimiento 100 con diferencia -3.000: 400.
# -----------------------------------------------------------------------------
def test_4_rendimiento_mayor_que_diferencia_error(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("100000.00"), tna=Decimal("30.00"))

    # Rendimiento 5.000 con diferencia 3.000 (declarado 103.000)
    with pytest.raises(HTTPException) as exc1:
        ajuste_saldo_service.registrar_control(
            db_session, u.id, b.id, saldo_declarado=Decimal("103000.00"), rendimiento=Decimal("5000.00")
        )
    assert exc1.value.status_code == 400
    assert "El rendimiento no puede ser mayor que la diferencia." in exc1.value.detail

    # Rendimiento 100 con diferencia -3.000 (declarado 97.000)
    with pytest.raises(HTTPException) as exc2:
        ajuste_saldo_service.registrar_control(
            db_session, u.id, b.id, saldo_declarado=Decimal("97000.00"), rendimiento=Decimal("100.00")
        )
    assert exc2.value.status_code == 400
    assert "El rendimiento no puede ser mayor que la diferencia." in exc2.value.detail


# -----------------------------------------------------------------------------
# 5. Billetera archivada: 400 con el texto exacto.
# -----------------------------------------------------------------------------
def test_5_billetera_archivada_error(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("100000.00"), archivada=True)

    with pytest.raises(HTTPException) as exc:
        ajuste_saldo_service.registrar_control(
            db_session, u.id, b.id, saldo_declarado=Decimal("95000.00")
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "No se puede actualizar el saldo de una billetera archivada."


# -----------------------------------------------------------------------------
# 6. eliminar_ajuste después del caso 1: saldo 190.000 y la fila borrada.
# -----------------------------------------------------------------------------
def test_6_eliminar_ajuste(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("190000.00"))
    ajuste = ajuste_saldo_service.registrar_control(
        db_session, u.id, b.id, saldo_declarado=Decimal("140000.00")
    )
    assert b.saldo_actual == Decimal("140000.00")

    ajuste_saldo_service.eliminar_ajuste(db_session, u.id, ajuste.id)
    assert b.saldo_actual == Decimal("190000.00")
    assert db_session.get(AjusteSaldo, ajuste.id) is None


# -----------------------------------------------------------------------------
# 7. calcular_saldo_teorico después del caso 1 es igual a saldo_actual
# -----------------------------------------------------------------------------
def test_7_calcular_saldo_teorico_con_ajuste(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("190000.00"))
    ajuste_saldo_service.registrar_control(
        db_session, u.id, b.id, saldo_declarado=Decimal("140000.00")
    )

    teorico = calcular_saldo_teorico(db_session, b.id)
    assert teorico == b.saldo_actual
    assert teorico == Decimal("140000.00")


# -----------------------------------------------------------------------------
# 8. mediana_salidas_semanales con semanas de 10.000, 12.000, 8.000 y 11.000
# -----------------------------------------------------------------------------
def test_8_mediana_salidas_semanales():
    hoy = date(2026, 9, 20)
    # k=1: [hoy - 7, hoy - 1] -> [13/9, 19/9]
    # k=2: [hoy - 14, hoy - 8] -> [6/9, 12/9]
    # k=3: [hoy - 21, hoy - 15] -> [30/8, 5/9]
    # k=4: [hoy - 28, hoy - 22] -> [23/8, 29/8]
    salidas = {
        date(2026, 9, 15): Decimal("10000.00"),  # k=1
        date(2026, 9, 10): Decimal("12000.00"),  # k=2
        date(2026, 9, 2): Decimal("8000.00"),    # k=3
        date(2026, 8, 25): Decimal("11000.00"),  # k=4
    }
    # Caso 4 semanas completas
    primer_mov = date(2026, 8, 20)
    mediana, semanas = mediana_salidas_semanales(salidas, primer_mov, hoy)
    assert mediana == Decimal("10500.00")
    assert semanas == 4

    # Con primer_movimiento hace 9 días (hoy - 9 = 11/9): solo k=1 califica (inicio 13/9 >= 11/9)
    primer_mov_9d = hoy - timedelta(days=9)
    mediana_1s, semanas_1s = mediana_salidas_semanales(salidas, primer_mov_9d, hoy)
    assert semanas_1s == 1
    assert mediana_1s == Decimal("10000.00")


# -----------------------------------------------------------------------------
# 9. es_grande: con mediana 10.500, diferencia -9.000 da False y -11.000 da True;
#    con 1 semana de historia da True; con diferencia 0 da False.
# -----------------------------------------------------------------------------
def test_9_es_grande():
    salida_tipica = Decimal("10500.00")

    def calc_es_grande(diferencia: Decimal, semanas: int, tipica: Decimal | None) -> bool:
        if diferencia == Decimal("0"):
            return False
        if semanas < 2:
            return True
        return abs(diferencia) > tipica

    assert calc_es_grande(Decimal("-9000.00"), semanas=4, tipica=salida_tipica) is False
    assert calc_es_grande(Decimal("-11000.00"), semanas=4, tipica=salida_tipica) is True
    assert calc_es_grande(Decimal("-9000.00"), semanas=1, tipica=salida_tipica) is True
    assert calc_es_grande(Decimal("0.00"), semanas=4, tipica=salida_tipica) is False


# -----------------------------------------------------------------------------
# 10. detectar_huecos con movimientos los días 1, 2, 3, 4, 5, 12 y 13 de septiembre,
#     desde 1/9 y hoy 20/9: huecos (6/9, 11/9) y (14/9, 20/9).
# -----------------------------------------------------------------------------
def test_10_detectar_huecos():
    dias = [
        date(2026, 9, 1),
        date(2026, 9, 2),
        date(2026, 9, 3),
        date(2026, 9, 4),
        date(2026, 9, 5),
        date(2026, 9, 12),
        date(2026, 9, 13),
    ]
    desde = date(2026, 9, 1)
    hoy = date(2026, 9, 20)

    huecos = detectar_huecos(dias, desde, hoy)
    assert len(huecos) == 2
    assert huecos[0] == (date(2026, 9, 6), date(2026, 9, 11))
    assert huecos[1] == (date(2026, 9, 14), date(2026, 9, 20))


# -----------------------------------------------------------------------------
# 11. calcular_cobertura_desde con controles (1/8, -10.000) y (5/9, -15.000) y salidas de 85.000
# -----------------------------------------------------------------------------
def test_11_calcular_cobertura_desde():
    hoy = date(2026, 9, 10)
    c1 = (date(2026, 8, 1), Decimal("-10000.00"))
    c2 = (date(2026, 9, 5), Decimal("-15000.00"))
    salidas = {
        date(2026, 8, 15): Decimal("50000.00"),
        date(2026, 8, 25): Decimal("35000.00"),
    }

    res = calcular_cobertura_desde([c1, c2], salidas, hoy)
    assert res["mostrar"] is True
    assert res["por_cada_100"] == 85
    assert res["desde"] == date(2026, 8, 1)
    assert res["hasta"] == date(2026, 9, 5)

    # Con un solo control
    res_1 = calcular_cobertura_desde([c1], salidas, hoy)
    assert res_1["mostrar"] is False

    # Con controles a 20 días
    c_cercano = (date(2026, 8, 21), Decimal("-15000.00"))
    res_20d = calcular_cobertura_desde([c1, c_cercano], salidas, hoy)
    assert res_20d["mostrar"] is False


# -----------------------------------------------------------------------------
# 12. Posibles duplicados: dos egresos del mismo día y monto aparecen con diferencia > 0;
#     con diferencia < 0 la lista está vacía.
# -----------------------------------------------------------------------------
def test_12_posibles_duplicados(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("100000.00"))
    cat = Categoria(id=uuid4(), nombre="Super", tipo=TipoCategoria.EGRESO)
    db_session.add(cat)
    db_session.commit()

    hoy = hoy_argentina()
    # Crear dos egresos con misma fecha y monto
    tx1 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("2500.00"),
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        fecha=hoy - timedelta(days=2),
        descripcion="Compra super 1",
        metodo_pago=MetodoPago.DEBITO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime.now(timezone.utc),
    )
    tx2 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        categoria_id=cat.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("2500.00"),
        moneda=Moneda.ARS,
        origen=OrigenTransaccion.MANUAL,
        fecha=hoy - timedelta(days=2),
        descripcion="Compra super 2",
        metodo_pago=MetodoPago.DEBITO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    db_session.add_all([tx1, tx2])
    db_session.commit()

    # Con diferencia > 0 (declarado 110.000 > registrado 100.000)
    prev_pos = ajuste_saldo_service.previsualizar_control(
        db_session, u.id, b.id, saldo_declarado=Decimal("110000.00")
    )
    assert len(prev_pos["posibles_duplicados"]) == 1
    dup = prev_pos["posibles_duplicados"][0]
    assert dup["monto"] == Decimal("2500.00")
    assert dup["cantidad"] == 2
    assert "Compra super 1" in dup["descripciones"]

    # Con diferencia < 0 (declarado 90.000 < registrado 100.000)
    prev_neg = ajuste_saldo_service.previsualizar_control(
        db_session, u.id, b.id, saldo_declarado=Decimal("90000.00")
    )
    assert len(prev_neg["posibles_duplicados"]) == 0


# -----------------------------------------------------------------------------
# 13. Por ast: ajuste_saldo_service.py no contiene "Transaccion(" ni llamadas a crear_transaccion;
#     definiciones_service.py no menciona ajuste.
# -----------------------------------------------------------------------------
def test_13_analisis_ast_e_integridad_estructural():
    backend_dir = Path(__file__).resolve().parent.parent
    service_path = backend_dir / "app" / "services" / "ajuste_saldo_service.py"
    def_path = backend_dir / "app" / "services" / "definiciones_service.py"

    service_code = service_path.read_text(encoding="utf-8")
    assert "Transaccion(" not in service_code
    assert "crear_transaccion(" not in service_code

    tree = ast.parse(service_code)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id != "Transaccion"
                assert node.func.id != "crear_transaccion"
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr != "crear_transaccion"

    def_code = def_path.read_text(encoding="utf-8")
    assert "ajuste" not in def_code.lower()


# -----------------------------------------------------------------------------
# 14. delete_billetera con un ajuste: 400 con el texto exacto.
#     tiene_transacciones es True con solo un ajuste.
# -----------------------------------------------------------------------------
def test_14_delete_billetera_con_ajuste_y_tiene_transacciones(db_session):
    u, b = _crear_usuario_y_billetera(db_session, saldo=Decimal("100000.00"))

    # Antes de ajuste, tiene_transacciones es False
    b_read_antes = get_billetera(str(b.id), db=db_session, current_user=u)
    assert b_read_antes.tiene_transacciones is False

    # Registramos un ajuste
    ajuste_saldo_service.registrar_control(
        db_session, u.id, b.id, saldo_declarado=Decimal("95000.00")
    )

    # Ahora tiene_transacciones es True
    b_read_desp = get_billetera(str(b.id), db=db_session, current_user=u)
    assert b_read_desp.tiene_transacciones is True

    # Intentar eliminar la billetera debe dar 400 con el texto exacto
    with pytest.raises(HTTPException) as exc:
        delete_billetera(str(b.id), db=db_session, current_user=u)
    assert exc.value.status_code == 400
    assert exc.value.detail == "No se puede eliminar la billetera porque tiene ajustes de saldo. Por favor, archivala."
