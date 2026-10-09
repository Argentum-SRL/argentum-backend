"""
tests/test_patrones_service.py — Tests del servicio y endpoints de 'Lo que se repite' (Fase 4d3 / 4d4b).

Cubre los 15 casos de prueba requeridos:
1. Usuario sin movimientos: listas vacías.
2. Datos base usuario A: fijos, costumbre, día a día e ingresos en sugerido.
3. Patrón débil y cuenta_en_numeros (False hasta que se confirma).
4. Descarte de fijo: se excluye de fijos y pasa a clasificador de frecuentes.
5. Mover costumbre a día a día.
6. Mover día a día a costumbre.
7. Mover a su caja detectada: borra la decisión y vuelve a sugerido.
8. Deshacer descarte: vuelve a la caja original como sugerido.
9. Validaciones de error HTTP 400 y 404.
10. Aislamiento estricto entre usuarios A y B.
11. Eliminación de usuario en cascada (ON DELETE CASCADE y usuario_service).
12. Invariancia total de la tabla de transacciones.
13. Decisiones sobre ingresos habituales (confirmar, descartar, deshacer).
14. Endpoints HTTP vía FastAPI TestClient (GET, POST decisiones, POST deshacer, errores).
15. Endpoints HTTP para usuario no admin (403 Forbidden y cero filas en decisiones_patrones).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types
import uuid

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera
from app.models.categoria import Categoria, TipoCategoria
from app.models.decision_patron import DecisionPatron
from app.models.subcategoria import Subcategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import (
    AuthProvider,
    CicloTipo,
    EstadoUsuario,
    Moneda,
    RolUsuario,
    Usuario,
)
from app.services import patrones_service
from app.services.usuario_service import eliminar_usuario


@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"


@compiles(PGUUID, "sqlite")
def compile_pguuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
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
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=engine)


def _crear_usuario(
    db: Session,
    email: str = "usuario_a@argentum.com",
    ciclo_tipo: CicloTipo = CicloTipo.DIA_FIJO,
    ciclo_valor: str = "1",
    is_admin: bool = False,
) -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=email,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.ADMIN if is_admin else RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Test",
        apellido="User",
        ciclo_tipo=ciclo_tipo,
        ciclo_valor=ciclo_valor,
        is_admin=is_admin,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _crear_billetera(db: Session, usuario_id: UUID) -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre="Efectivo",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("5000000.00"),
        saldo_actual=Decimal("5000000.00"),
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _crear_categorias(db: Session) -> dict[str, Any]:
    # Si las categorías ya existen en la base de datos de prueba, reutilizarlas
    cat_viv_exist = db.execute(select(Categoria).where(Categoria.nombre == "Vivienda")).scalar_one_or_none()
    if cat_viv_exist:
        cat_viv = cat_viv_exist
        sub_alq = db.execute(select(Subcategoria).where(Subcategoria.nombre == "Alquiler")).scalar_one()
        cat_gast = db.execute(select(Categoria).where(Categoria.nombre == "Gastronomía")).scalar_one()
        sub_del = db.execute(select(Subcategoria).where(Subcategoria.nombre == "Delivery")).scalar_one()
        cat_alim = db.execute(select(Categoria).where(Categoria.nombre == "Alimentación")).scalar_one()
        sub_super = db.execute(select(Subcategoria).where(Subcategoria.nombre == "Supermercado")).scalar_one()
        cat_ing = db.execute(select(Categoria).where(Categoria.nombre == "Ingresos")).scalar_one()
        sub_sueldo = db.execute(select(Subcategoria).where(Subcategoria.nombre == "Sueldo")).scalar_one()
        return {
            "cat_viv": cat_viv,
            "sub_alq": sub_alq,
            "cat_gast": cat_gast,
            "sub_del": sub_del,
            "cat_alim": cat_alim,
            "sub_super": sub_super,
            "cat_ing": cat_ing,
            "sub_sueldo": sub_sueldo,
        }

    cat_viv = Categoria(
        id=uuid4(),
        nombre="Vivienda",
        tipo=TipoCategoria.EGRESO,
        icono="home",
        color="#336699",
    )
    db.add(cat_viv)
    db.flush()
    sub_alq = Subcategoria(
        id=uuid4(),
        categoria_id=cat_viv.id,
        nombre="Alquiler",
    )
    db.add(sub_alq)

    cat_gast = Categoria(
        id=uuid4(),
        nombre="Gastronomía",
        tipo=TipoCategoria.EGRESO,
        icono="utensils",
        color="#993333",
    )
    db.add(cat_gast)
    db.flush()
    sub_del = Subcategoria(
        id=uuid4(),
        categoria_id=cat_gast.id,
        nombre="Delivery",
    )
    db.add(sub_del)

    cat_alim = Categoria(
        id=uuid4(),
        nombre="Alimentación",
        tipo=TipoCategoria.EGRESO,
        icono="shopping-cart",
        color="#339933",
    )
    db.add(cat_alim)
    db.flush()
    sub_super = Subcategoria(
        id=uuid4(),
        categoria_id=cat_alim.id,
        nombre="Supermercado",
    )
    db.add(sub_super)

    cat_ing = Categoria(
        id=uuid4(),
        nombre="Ingresos",
        tipo=TipoCategoria.INGRESO,
        icono="wallet",
        color="#00aa00",
    )
    db.add(cat_ing)
    db.flush()
    sub_sueldo = Subcategoria(
        id=uuid4(),
        categoria_id=cat_ing.id,
        nombre="Sueldo",
    )
    db.add(sub_sueldo)

    db.commit()
    return {
        "cat_viv": cat_viv,
        "sub_alq": sub_alq,
        "cat_gast": cat_gast,
        "sub_del": sub_del,
        "cat_alim": cat_alim,
        "sub_super": sub_super,
        "cat_ing": cat_ing,
        "sub_sueldo": sub_sueldo,
    }


def _crear_tx(
    db: Session,
    usuario_id: UUID,
    billetera_id: UUID,
    fecha: date,
    monto: Decimal,
    tipo: TipoTransaccion,
    categoria_id: UUID,
    subcategoria_id: UUID,
    descripcion: str,
    moneda: Moneda = Moneda.ARS,
) -> Transaccion:
    tx = Transaccion(
        id=uuid4(),
        usuario_id=usuario_id,
        billetera_id=billetera_id,
        fecha=fecha,
        monto=monto,
        tipo=tipo,
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id,
        descripcion=descripcion,
        moneda=moneda,
        origen=OrigenTransaccion.MANUAL,
        metodo_pago=MetodoPago.EFECTIVO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
    )
    db.add(tx)
    return tx


def _poblar_usuario_base(db: Session, usuario: Usuario) -> dict[str, Any]:
    b = _crear_billetera(db, usuario.id)
    cats = _crear_categorias(db)

    # 1. "Alquiler depto": $400.000 el día 5 de mayo, junio, julio y agosto de 2026, en Vivienda > Alquiler
    for f in [date(2026, 5, 5), date(2026, 6, 5), date(2026, 7, 5), date(2026, 8, 5)]:
        _crear_tx(
            db,
            usuario.id,
            b.id,
            f,
            Decimal("400000.00"),
            TipoTransaccion.EGRESO,
            cats["cat_viv"].id,
            cats["sub_alq"].id,
            "Alquiler depto",
        )

    # 2. "PedidosYa": $10.000 los días 3, 13 y 23 de junio, julio y agosto, en Delivery
    fechas_del = [
        date(2026, 6, 3), date(2026, 6, 13), date(2026, 6, 23),
        date(2026, 7, 3), date(2026, 7, 13), date(2026, 7, 23),
        date(2026, 8, 3), date(2026, 8, 13), date(2026, 8, 23),
    ]
    for f in fechas_del:
        _crear_tx(
            db,
            usuario.id,
            b.id,
            f,
            Decimal("10000.00"),
            TipoTransaccion.EGRESO,
            cats["cat_gast"].id,
            cats["sub_del"].id,
            "PedidosYa",
        )

    # 3. "Coto": $25.000 los días 2, 9, 16 y 23 de junio, julio y agosto, en Supermercado
    fechas_sup = [
        date(2026, 6, 2), date(2026, 6, 9), date(2026, 6, 16), date(2026, 6, 23),
        date(2026, 7, 2), date(2026, 7, 9), date(2026, 7, 16), date(2026, 7, 23),
        date(2026, 8, 2), date(2026, 8, 9), date(2026, 8, 16), date(2026, 8, 23),
    ]
    for f in fechas_sup:
        _crear_tx(
            db,
            usuario.id,
            b.id,
            f,
            Decimal("25000.00"),
            TipoTransaccion.EGRESO,
            cats["cat_alim"].id,
            cats["sub_super"].id,
            "Coto",
        )

    # 4. "Sueldo": $1.000.000 (ingreso) el día 1 de marzo a agosto, en Sueldo
    for f in [
        date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1),
        date(2026, 6, 1), date(2026, 7, 1), date(2026, 8, 1)
    ]:
        _crear_tx(
            db,
            usuario.id,
            b.id,
            f,
            Decimal("1000000.00"),
            TipoTransaccion.INGRESO,
            cats["cat_ing"].id,
            cats["sub_sueldo"].id,
            "Sueldo",
        )

    db.commit()
    return {"billetera": b, "categorias": cats}


# ==============================================================================
# CASOS DE PRUEBA (1 a 14)
# ==============================================================================

def test_caso_01_usuario_sin_movimientos(db_session: Session):
    """Caso 1: Usuario sin movimientos retorna todas las cajas vacías."""
    u = _crear_usuario(db_session, "vacio@argentum.com")
    resumen = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))

    assert len(resumen.fijos) == 0
    assert len(resumen.costumbre) == 0
    assert len(resumen.dia_a_dia) == 0
    assert len(resumen.ingresos) == 0
    assert resumen.fecha_calculo == date(2026, 9, 5)


def test_caso_02_datos_base_usuario_a(db_session: Session):
    """Caso 2: Usuario con datos base clasifica correctamente en fijos, costumbre, día a día e ingresos en sugerido."""
    u = _crear_usuario(db_session, "user_a@argentum.com")
    data_base = _poblar_usuario_base(db_session, u)
    cats = data_base["categorias"]

    resumen = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    resumen2 = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))

    # Fijos con un solo ítem
    assert len(resumen.fijos) == 1
    fijo = resumen.fijos[0]
    assert fijo.clave_item.startswith("fijo|ARS|")
    assert fijo.clave_item == resumen2.fijos[0].clave_item
    assert fijo.frecuencia == "mensual"
    assert fijo.fuerza == "fuerte"
    assert fijo.dia_tipico == 5
    assert fijo.estado == "sugerido"
    assert fijo.cuenta_en_numeros is True
    assert fijo.monto_mensual == Decimal("400000.00")
    assert fijo.rubro == "Alquiler"

    # Costumbre con un solo ítem: "Delivery", clave_item f"rubro|ARS|sub:{id de Delivery}", 9 ocurrencias y monto_mensual 30000.00
    assert len(resumen.costumbre) == 1
    cost = resumen.costumbre[0]
    assert cost.nombre == "Delivery"
    assert cost.clave_item == f"rubro|ARS|sub:{cats['sub_del'].id}"
    assert cost.ocurrencias == 9
    assert cost.monto_mensual == Decimal("30000.00")

    # Día a día con un solo ítem: "Supermercado", clave_item f"rubro|ARS|sub:{id de Supermercado}", 12 ocurrencias y monto_mensual 100000.00
    assert len(resumen.dia_a_dia) == 1
    dia = resumen.dia_a_dia[0]
    assert dia.nombre == "Supermercado"
    assert dia.clave_item == f"rubro|ARS|sub:{cats['sub_super'].id}"
    assert dia.ocurrencias == 12
    assert dia.monto_mensual == Decimal("100000.00")

    # Ingresos
    assert len(resumen.ingresos) >= 1
    ing = resumen.ingresos[0]
    assert "Sueldo" in ing.nombre or "Ingreso" in ing.nombre
    assert ing.estado == "sugerido"
    assert ing.cuenta_en_numeros is True


def test_caso_03_patron_debil_cuenta_en_numeros(db_session: Session):
    """Caso 3: otro usuario con solo el alquiler de julio y agosto: fuerza 'debil' y cuenta_en_numeros False; después de confirmar, 'confirmado' y True."""
    u = _crear_usuario(db_session, "debil@argentum.com")
    b = _crear_billetera(db_session, u.id)
    cats = _crear_categorias(db_session)

    # Solo el alquiler de julio y agosto
    for f in [date(2026, 7, 5), date(2026, 8, 5)]:
        _crear_tx(
            db_session,
            u.id,
            b.id,
            f,
            Decimal("400000.00"),
            TipoTransaccion.EGRESO,
            cats["cat_viv"].id,
            cats["sub_alq"].id,
            "Alquiler depto",
        )
    db_session.commit()

    resumen = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    assert len(resumen.fijos) == 1
    fijo = resumen.fijos[0]
    assert fijo.fuerza == "debil"
    assert fijo.estado == "sugerido"
    assert fijo.cuenta_en_numeros is False

    # Confirmar
    resumen_post = patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=fijo.clave_item,
        decision="confirmar",
        hoy=date(2026, 9, 5),
    )
    assert len(resumen_post.fijos) == 1
    fijo_conf = resumen_post.fijos[0]
    assert fijo_conf.estado == "confirmado"
    assert fijo_conf.cuenta_en_numeros is True


def test_caso_04_descarte_fijo_pasa_a_frecuentes(db_session: Session):
    """Caso 4: después de descartar el alquiler:
    - fijos queda vacío;
    - día a día tiene 'Alquiler' con 3 ocurrencias;
    - una segunda llamada da las mismas cuatro listas (clave_item, caja, estado, ocurrencias y monto_mensual)."""
    u = _crear_usuario(db_session, "descarte@argentum.com")
    _poblar_usuario_base(db_session, u)

    res_inicial = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    assert len(res_inicial.fijos) == 1
    clave_fijo = res_inicial.fijos[0].clave_item

    # Descartar fijo
    res_post = patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=clave_fijo,
        decision="descartar",
        hoy=date(2026, 9, 5),
    )

    # fijos queda vacío
    assert len(res_post.fijos) == 0

    # día a día tiene "Alquiler" con 3 ocurrencias
    alquiler_items = [it for it in res_post.dia_a_dia if "Alquiler" in it.nombre]
    assert len(alquiler_items) == 1
    assert alquiler_items[0].ocurrencias == 3

    # una segunda llamada da las mismas cuatro listas (clave_item, caja, estado, ocurrencias y monto_mensual)
    res_segunda = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    for lista_a, lista_b in [
        (res_post.fijos, res_segunda.fijos),
        (res_post.costumbre, res_segunda.costumbre),
        (res_post.dia_a_dia, res_segunda.dia_a_dia),
        (res_post.ingresos, res_segunda.ingresos),
    ]:
        assert len(lista_a) == len(lista_b)
        for it_a, it_b in zip(lista_a, lista_b):
            assert it_a.clave_item == it_b.clave_item
            assert getattr(it_a, "caja", None) == getattr(it_b, "caja", None)
            assert it_a.estado == it_b.estado
            assert getattr(it_a, "ocurrencias", None) == getattr(it_b, "ocurrencias", None)
            assert it_a.monto_mensual == it_b.monto_mensual


def test_caso_05_mover_costumbre_a_dia_a_dia(db_session: Session):
    """Caso 5: Mover un ítem de costumbre a día a día actualiza estado a 'movido'."""
    u = _crear_usuario(db_session, "mover_dad@argentum.com")
    _poblar_usuario_base(db_session, u)

    res_inicial = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    item_deliv = res_inicial.costumbre[0]
    assert item_deliv.caja == "costumbre"

    res_post = patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=item_deliv.clave_item,
        decision="mover",
        caja_destino="dia_a_dia",
        hoy=date(2026, 9, 5),
    )

    assert len(res_post.costumbre) == 0
    item_movido = [it for it in res_post.dia_a_dia if it.clave_item == item_deliv.clave_item][0]
    assert item_movido.caja == "dia_a_dia"
    assert item_movido.caja_detectada == "costumbre"
    assert item_movido.estado == "movido"


def test_caso_06_mover_alquiler_fijo_a_costumbre(db_session: Session):
    """Caso 6 nuevo: mover el alquiler (fijo) a costumbre.
    - fijos queda vacío;
    - en costumbre está el alquiler con estado 'movido', caja_detectada 'fijo', frecuencia 'mensual' y dia_tipico 5;
    - día a día sigue con un solo ítem (Supermercado)."""
    u = _crear_usuario(db_session, "mover_alq_cost@argentum.com")
    _poblar_usuario_base(db_session, u)

    res_inicial = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    item_fijo = res_inicial.fijos[0]
    assert item_fijo.caja == "fijo"

    res_post = patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=item_fijo.clave_item,
        decision="mover",
        caja_destino="costumbre",
        hoy=date(2026, 9, 5),
    )

    # fijos queda vacío
    assert len(res_post.fijos) == 0

    # en costumbre está el alquiler con estado "movido", caja_detectada "fijo", frecuencia "mensual" y dia_tipico 5
    alquiler_cost = [it for it in res_post.costumbre if it.clave_item == item_fijo.clave_item]
    assert len(alquiler_cost) == 1
    assert alquiler_cost[0].estado == "movido"
    assert alquiler_cost[0].caja_detectada == "fijo"
    assert alquiler_cost[0].frecuencia == "mensual"
    assert alquiler_cost[0].dia_tipico == 5

    # día a día sigue con un solo ítem (Supermercado)
    assert len(res_post.dia_a_dia) == 1
    assert res_post.dia_a_dia[0].nombre == "Supermercado"


def test_caso_06b_mover_dia_a_dia_a_costumbre(db_session: Session):
    """Caso 6b: Mover un ítem de día a día a costumbre actualiza estado a 'movido'."""
    u = _crear_usuario(db_session, "mover_cost@argentum.com")
    _poblar_usuario_base(db_session, u)

    res_inicial = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    item_super = res_inicial.dia_a_dia[0]
    assert item_super.caja == "dia_a_dia"

    res_post = patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=item_super.clave_item,
        decision="mover",
        caja_destino="costumbre",
        hoy=date(2026, 9, 5),
    )

    assert len(res_post.dia_a_dia) == 0
    item_movido = [it for it in res_post.costumbre if it.clave_item == item_super.clave_item][0]
    assert item_movido.caja == "costumbre"
    assert item_movido.caja_detectada == "dia_a_dia"
    assert item_movido.estado == "movido"


def test_caso_07_mover_a_caja_detectada_restaura_sugerido(db_session: Session):
    """Caso 7: Mover un ítem movido a su caja original detectada borra la decisión y vuelve a sugerido."""
    u = _crear_usuario(db_session, "restaurar_sug@argentum.com")
    _poblar_usuario_base(db_session, u)

    res_ini = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    clave_deliv = res_ini.costumbre[0].clave_item

    # 1. Mover a dia_a_dia
    patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=clave_deliv,
        decision="mover",
        caja_destino="dia_a_dia",
        hoy=date(2026, 9, 5),
    )

    # 2. Mover de nuevo a costumbre (su caja detectada)
    res_restaurado = patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=clave_deliv,
        decision="mover",
        caja_destino="costumbre",
        hoy=date(2026, 9, 5),
    )

    # Debe estar en costumbre como sugerido
    item = [it for it in res_restaurado.costumbre if it.clave_item == clave_deliv][0]
    assert item.caja == "costumbre"
    assert item.caja_detectada == "costumbre"
    assert item.estado == "sugerido"

    # La fila en decisiones_patrones debe haber sido eliminada
    dec = db_session.execute(
        select(DecisionPatron).where(DecisionPatron.usuario_id == u.id, DecisionPatron.clave_item == clave_deliv)
    ).scalar_one_or_none()
    assert dec is None


def test_caso_08_deshacer_descarte(db_session: Session):
    """Caso 8: Deshacer un descarte restaura el ítem en su caja original como sugerido."""
    u = _crear_usuario(db_session, "deshacer_desc@argentum.com")
    _poblar_usuario_base(db_session, u)

    res_ini = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    clave_fijo = res_ini.fijos[0].clave_item

    # Descartar
    patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=clave_fijo,
        decision="descartar",
        hoy=date(2026, 9, 5),
    )

    # Deshacer
    res_post = patrones_service.deshacer_decision(
        db_session,
        u,
        clave_item=clave_fijo,
        hoy=date(2026, 9, 5),
    )

    assert len(res_post.fijos) == 1
    fijo = res_post.fijos[0]
    assert fijo.clave_item == clave_fijo
    assert fijo.estado == "sugerido"

    dec = db_session.execute(
        select(DecisionPatron).where(DecisionPatron.usuario_id == u.id, DecisionPatron.clave_item == clave_fijo)
    ).scalar_one_or_none()
    assert dec is None


def test_caso_09_validaciones_error(db_session: Session):
    """Caso 9: código y detail exactos:
    - mover sin caja_destino: 400 'Elegí a qué caja moverlo.';
    - mover a la caja en la que ya está: 400 'Ya está en esa caja.';
    - clave_item inexistente: 404 'No encontré ese ítem.';
    - deshacer sin decisión: 404 'No hay nada para deshacer en ese ítem.';
    - mover el ingreso: 400 'Los ingresos no se pueden mover.'."""
    u = _crear_usuario(db_session, "errores@argentum.com")
    _poblar_usuario_base(db_session, u)

    res = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    clave_deliv = res.costumbre[0].clave_item
    clave_ingreso = res.ingresos[0].clave_item

    # 1. mover sin caja_destino: 400 "Elegí a qué caja moverlo."
    with pytest.raises(HTTPException) as exc1:
        patrones_service.registrar_decision(
            db_session, u, clave_item=clave_deliv, decision="mover", caja_destino=None, hoy=date(2026, 9, 5)
        )
    assert exc1.value.status_code == 400
    assert exc1.value.detail == "Elegí a qué caja moverlo."

    # 2. mover a la caja en la que ya está: 400 "Ya está en esa caja."
    with pytest.raises(HTTPException) as exc2:
        patrones_service.registrar_decision(
            db_session, u, clave_item=clave_deliv, decision="mover", caja_destino="costumbre", hoy=date(2026, 9, 5)
        )
    assert exc2.value.status_code == 400
    assert exc2.value.detail == "Ya está en esa caja."

    # 3. clave_item inexistente: 404 "No encontré ese ítem."
    with pytest.raises(HTTPException) as exc3:
        patrones_service.registrar_decision(
            db_session, u, clave_item="inexistente|ARS|foo", decision="confirmar", hoy=date(2026, 9, 5)
        )
    assert exc3.value.status_code == 404
    assert exc3.value.detail == "No encontré ese ítem."

    # 4. deshacer sin decisión: 404 "No hay nada para deshacer en ese ítem."
    with pytest.raises(HTTPException) as exc4:
        patrones_service.deshacer_decision(db_session, u, clave_item=clave_deliv, hoy=date(2026, 9, 5))
    assert exc4.value.status_code == 404
    assert exc4.value.detail == "No hay nada para deshacer en ese ítem."

    # 5. mover el ingreso: 400 "Los ingresos no se pueden mover."
    with pytest.raises(HTTPException) as exc5:
        patrones_service.registrar_decision(
            db_session, u, clave_item=clave_ingreso, decision="mover", caja_destino="dia_a_dia", hoy=date(2026, 9, 5)
        )
    assert exc5.value.status_code == 400
    assert exc5.value.detail == "Los ingresos no se pueden mover."



def test_caso_10_aislamiento_entre_usuarios(db_session: Session):
    """Caso 10: Las decisiones de Usuario A no impactan ni son accesibles por Usuario B."""
    u_a = _crear_usuario(db_session, "user_iso_a@argentum.com")
    u_b = _crear_usuario(db_session, "user_iso_b@argentum.com")
    _poblar_usuario_base(db_session, u_a)
    _poblar_usuario_base(db_session, u_b)

    res_a = patrones_service.armar_lo_que_se_repite(db_session, u_a, hoy=date(2026, 9, 5))
    clave_deliv_a = res_a.costumbre[0].clave_item

    # Usuario A mueve Delivery a dia_a_dia
    patrones_service.registrar_decision(
        db_session,
        u_a,
        clave_item=clave_deliv_a,
        decision="mover",
        caja_destino="dia_a_dia",
        hoy=date(2026, 9, 5),
    )

    # Usuario B consulta su resumen: Delivery sigue en costumbre como sugerido
    res_b = patrones_service.armar_lo_que_se_repite(db_session, u_b, hoy=date(2026, 9, 5))
    assert len(res_b.costumbre) == 1
    assert res_b.costumbre[0].caja == "costumbre"
    assert res_b.costumbre[0].estado == "sugerido"

    # Usuario B no tiene filas en decisiones_patrones
    dec_b = db_session.execute(
        select(DecisionPatron).where(DecisionPatron.usuario_id == u_b.id)
    ).scalars().all()
    assert len(dec_b) == 0

    # Usuario B no puede deshacer la decisión de Usuario A
    with pytest.raises(HTTPException) as exc:
        patrones_service.deshacer_decision(db_session, u_b, clave_item=clave_deliv_a, hoy=date(2026, 9, 5))
    assert exc.value.status_code == 404


def test_caso_11_eliminacion_usuario_cascada(db_session: Session):
    """Caso 11: La eliminación del usuario borra en cascada sus decisiones en decisiones_patrones."""
    u = _crear_usuario(db_session, "eliminar@argentum.com")
    _poblar_usuario_base(db_session, u)

    res = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    clave_deliv = res.costumbre[0].clave_item

    patrones_service.registrar_decision(
        db_session,
        u,
        clave_item=clave_deliv,
        decision="mover",
        caja_destino="dia_a_dia",
        hoy=date(2026, 9, 5),
    )

    # Verificar que existe la decisión
    cant_antes = db_session.execute(
        select(func.count()).select_from(DecisionPatron).where(DecisionPatron.usuario_id == u.id)
    ).scalar_one()
    assert cant_antes == 1

    # Eliminar usuario
    eliminar_usuario(db_session, u)

    # Verificar que no quedaron decisiones huérfanas
    cant_despues = db_session.execute(
        select(func.count()).select_from(DecisionPatron).where(DecisionPatron.usuario_id == u.id)
    ).scalar_one()
    assert cant_despues == 0


def test_caso_12_invariancia_transacciones(db_session: Session):
    """Caso 12: Las operaciones de patrones no modifican, eliminan ni agregan transacciones."""
    u = _crear_usuario(db_session, "invariancia@argentum.com")
    _poblar_usuario_base(db_session, u)

    txs_antes = db_session.execute(
        select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.id)
    ).scalars().all()
    snapshot_antes = [(tx.id, tx.monto, tx.fecha, tx.tipo, tx.descripcion) for tx in txs_antes]

    # Ejecutar consultas y decisiones
    res = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))
    clave_fijo = res.fijos[0].clave_item
    clave_deliv = res.costumbre[0].clave_item

    patrones_service.registrar_decision(db_session, u, clave_fijo, "confirmar", hoy=date(2026, 9, 5))
    patrones_service.registrar_decision(db_session, u, clave_deliv, "mover", caja_destino="dia_a_dia", hoy=date(2026, 9, 5))
    patrones_service.deshacer_decision(db_session, u, clave_fijo, hoy=date(2026, 9, 5))

    txs_despues = db_session.execute(
        select(Transaccion).where(Transaccion.usuario_id == u.id).order_by(Transaccion.id)
    ).scalars().all()
    snapshot_despues = [(tx.id, tx.monto, tx.fecha, tx.tipo, tx.descripcion) for tx in txs_despues]

    assert len(snapshot_antes) == len(snapshot_despues)
    assert snapshot_antes == snapshot_despues


def test_caso_13_decisiones_ingresos_habituales(db_session: Session):
    """Caso 13: Decisiones sobre ingresos habituales (confirmar, descartar y deshacer)."""
    u = _crear_usuario(db_session, "ingresos_dec@argentum.com")
    _poblar_usuario_base(db_session, u)

    res_ini = patrones_service.armar_lo_que_se_repite(db_session, u, hoy=date(2026, 9, 5))

    # Antes del flujo actual, ingresos tiene un solo ítem con moneda "ARS", tipo "regular",
    # monto_mensual 1000000.00, estado "sugerido", cuenta_en_numeros True y editable True
    assert len(res_ini.ingresos) == 1
    ing0 = res_ini.ingresos[0]
    assert ing0.moneda == "ARS"
    assert ing0.tipo == "regular"
    assert ing0.monto_mensual == Decimal("1000000.00") or ing0.monto_mensual == 1000000.00
    assert ing0.estado == "sugerido"
    assert ing0.cuenta_en_numeros is True
    assert ing0.editable is True

    clave_ing = ing0.clave_item

    # Confirmar
    res_conf = patrones_service.registrar_decision(db_session, u, clave_ing, "confirmar", hoy=date(2026, 9, 5))
    ing_conf = [it for it in res_conf.ingresos if it.clave_item == clave_ing][0]
    assert ing_conf.estado == "confirmado"

    # Descartar
    res_desc = patrones_service.registrar_decision(db_session, u, clave_ing, "descartar", hoy=date(2026, 9, 5))
    assert not any(it.clave_item == clave_ing for it in res_desc.ingresos)

    # Deshacer
    res_desh = patrones_service.deshacer_decision(db_session, u, clave_ing, hoy=date(2026, 9, 5))
    ing_desh = [it for it in res_desh.ingresos if it.clave_item == clave_ing][0]
    assert ing_desh.estado == "sugerido"


def test_caso_14_endpoints_http(db_session: Session):
    """Caso 14: Endpoints HTTP de patrones vía TestClient (GET, POST decisiones, POST deshacer, errores)."""
    u = _crear_usuario(db_session, "test_http@argentum.com", is_admin=True)
    _poblar_usuario_base(db_session, u)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u

    client = TestClient(app)
    try:
        # 1. GET /patrones
        resp_get = client.get("/patrones")
        assert resp_get.status_code == 200
        data_get = resp_get.json()
        assert "fijos" in data_get
        assert "costumbre" in data_get
        assert "dia_a_dia" in data_get
        assert "ingresos" in data_get
        assert len(data_get["costumbre"]) >= 1

        clave_deliv = data_get["costumbre"][0]["clave_item"]

        # 2. POST /patrones/decisiones (mover Delivery a día a día)
        resp_post = client.post(
            "/patrones/decisiones",
            json={
                "clave_item": clave_deliv,
                "decision": "mover",
                "caja_destino": "dia_a_dia",
            },
        )
        assert resp_post.status_code == 200
        data_post = resp_post.json()
        assert len(data_post["costumbre"]) == 0
        assert any(it["clave_item"] == clave_deliv and it["caja"] == "dia_a_dia" for it in data_post["dia_a_dia"])

        # 3. POST /patrones/decisiones/deshacer
        resp_desh = client.post(
            "/patrones/decisiones/deshacer",
            json={"clave_item": clave_deliv},
        )
        assert resp_desh.status_code == 200
        data_desh = resp_desh.json()
        assert any(it["clave_item"] == clave_deliv and it["caja"] == "costumbre" for it in data_desh["costumbre"])

        # 4. Error 400: mover sin caja_destino
        resp_err400 = client.post(
            "/patrones/decisiones",
            json={"clave_item": clave_deliv, "decision": "mover"},
        )
        assert resp_err400.status_code == 400

        # 5. Error 404: ítem inexistente
        resp_err404 = client.post(
            "/patrones/decisiones",
            json={"clave_item": "inexistente|ARS|foo", "decision": "confirmar"},
        )
        assert resp_err404.status_code == 404

    finally:
        app.dependency_overrides.clear()


def test_caso_15_endpoints_usuario_no_admin(db_session: Session):
    """Caso 15: Un usuario no admin recibe 403 en GET /patrones, POST /patrones/decisiones y POST /patrones/decisiones/deshacer, y no se crea ninguna fila en decisiones_patrones."""
    u_no_admin = _crear_usuario(db_session, "no_admin@argentum.com", is_admin=False)
    _poblar_usuario_base(db_session, u_no_admin)

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: u_no_admin

    client = TestClient(app)
    try:
        # 1. GET /patrones -> 403 con detail estándar de admin
        resp_get = client.get("/patrones")
        assert resp_get.status_code == 403
        data_get = resp_get.json()
        assert (
            data_get.get("detail") == "No tenés permiso para hacer eso."
            or data_get.get("error", {}).get("message") == "No tenés permiso para hacer eso."
        )

        # 2. POST /patrones/decisiones -> 403 con detail estándar de admin
        resp_post = client.post(
            "/patrones/decisiones",
            json={
                "clave_item": "fijo|ARS|test",
                "decision": "confirmar",
            },
        )
        assert resp_post.status_code == 403
        data_post = resp_post.json()
        assert (
            data_post.get("detail") == "No tenés permiso para hacer eso."
            or data_post.get("error", {}).get("message") == "No tenés permiso para hacer eso."
        )

        # 3. POST /patrones/decisiones/deshacer -> 403 con detail estándar de admin
        resp_desh = client.post(
            "/patrones/decisiones/deshacer",
            json={"clave_item": "fijo|ARS|test"},
        )
        assert resp_desh.status_code == 403
        data_desh = resp_desh.json()
        assert (
            data_desh.get("detail") == "No tenés permiso para hacer eso."
            or data_desh.get("error", {}).get("message") == "No tenés permiso para hacer eso."
        )

        # 4. No se crea ninguna fila en decisiones_patrones
        cant_decisiones = db_session.execute(select(func.count(DecisionPatron.id))).scalar()
        assert cant_decisiones == 0

    finally:
        app.dependency_overrides.clear()
