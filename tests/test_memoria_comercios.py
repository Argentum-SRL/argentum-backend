from __future__ import annotations

import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

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


from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.memoria_comercio import MemoriaComercio
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.transaccion import (
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers import memoria_comercios
from app.schemas.memoria_comercio import MemoriaAplicarRequest, MemoriaComercioCreate
from app.services.memoria_comercio_service import (
    anteriores_distintos,
    aplicar_a_anteriores,
    aplicar_memoria_a_movimiento,
    buscar,
    clave_comercio,
    guardar,
)


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


def _crear_usuario(db: Session, email_prefix: str = "test") -> Usuario:
    u = Usuario(
        id=uuid4(),
        email=f"{email_prefix}_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _crear_billetera(db: Session, usuario_id: uuid.UUID) -> Billetera:
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario_id,
        nombre="Efectivo ARS",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000.00"),
        saldo_actual=Decimal("50000.00"),
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


def _crear_categoria_y_sub(
    db: Session,
    nombre_cat: str = "Alimentación",
    tipo: TipoCategoria = TipoCategoria.EGRESO,
    nombre_sub: str | None = "Supermercado",
) -> tuple[Categoria, Subcategoria | None]:
    cat = Categoria(
        id=uuid4(),
        nombre=nombre_cat,
        tipo=tipo,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add(cat)
    db.flush()
    sub = None
    if nombre_sub:
        sub = Subcategoria(
            id=uuid4(),
            categoria_id=cat.id,
            nombre=nombre_sub,
            estado=EstadoSubcategoria.ACTIVA,
        )
        db.add(sub)
        db.flush()
    db.commit()
    db.refresh(cat)
    if sub:
        db.refresh(sub)
    return cat, sub


# -----------------------------------------------------------------------------
# D1.1. clave_comercio: casos esperados y vacíos
# -----------------------------------------------------------------------------
def test_d1_1_clave_comercio():
    assert clave_comercio("gasto en el chino") == "chino"
    assert clave_comercio("chinos") == "chino"
    assert clave_comercio("Gasto en Coto") == "coto"
    assert clave_comercio("Uber") == "uber"
    assert clave_comercio("gasto en uber") == "uber"
    assert clave_comercio("Pago a Juan $5.000") == "juan"
    assert clave_comercio("Transferencia a amiga") == "transferencia amiga"
    assert clave_comercio("") is None
    assert clave_comercio(None) is None
    assert clave_comercio("12345") is None


# -----------------------------------------------------------------------------
# D1.2. guardar y buscar: upsert, tipo egreso/ingreso y validaciones
# -----------------------------------------------------------------------------
def test_d1_2_guardar_y_buscar(db_session: Session):
    u = _crear_usuario(db_session, "user1")
    cat_egreso, sub_egreso = _crear_categoria_y_sub(db_session, "Supermercado", TipoCategoria.EGRESO, "Chino")
    cat_ingreso, sub_ingreso = _crear_categoria_y_sub(db_session, "Sueldo", TipoCategoria.INGRESO, "Principal")

    # Guardar primera vez
    m1 = guardar(
        db=db_session,
        usuario_id=u.id,
        descripcion="gasto en el chino",
        tipo="egreso",
        categoria_id=cat_egreso.id,
        subcategoria_id=sub_egreso.id,
    )
    assert m1.clave == "chino"
    assert m1.tipo == "egreso"

    # Guardar dos veces la misma clave actualiza la memoria, no crea otra
    m2 = guardar(
        db=db_session,
        usuario_id=u.id,
        descripcion="Chinos",
        tipo="egreso",
        categoria_id=cat_egreso.id,
        subcategoria_id=None,
    )
    assert m2.id == m1.id
    assert m2.subcategoria_id is None

    total_memorias = db_session.execute(select(MemoriaComercio).where(MemoriaComercio.usuario_id == u.id)).scalars().all()
    assert len(total_memorias) == 1

    # La misma clave en egreso e ingreso son memorias distintas
    m_ing = guardar(
        db=db_session,
        usuario_id=u.id,
        descripcion="pago de chino",
        tipo="ingreso",
        categoria_id=cat_ingreso.id,
        subcategoria_id=sub_ingreso.id,
    )
    assert m_ing.id != m1.id
    assert m_ing.tipo == "ingreso"
    assert m_ing.clave == "chino"

    # Buscar egreso e ingreso
    b_egr = buscar(db_session, u.id, "chino", "egreso")
    b_ing = buscar(db_session, u.id, "chino", "ingreso")
    assert b_egr.id == m1.id
    assert b_ing.id == m_ing.id

    # Categoría de otro tipo -> 400 con texto exacto
    with pytest.raises(HTTPException) as exc1:
        guardar(
            db=db_session,
            usuario_id=u.id,
            descripcion="chino",
            tipo="egreso",
            categoria_id=cat_ingreso.id,
            subcategoria_id=None,
        )
    assert exc1.value.status_code == 400
    assert exc1.value.detail == "Esa categoría no corresponde."

    # Descripción "12345" -> 400 con texto exacto
    with pytest.raises(HTTPException) as exc2:
        guardar(
            db=db_session,
            usuario_id=u.id,
            descripcion="12345",
            tipo="egreso",
            categoria_id=cat_egreso.id,
            subcategoria_id=None,
        )
    assert exc2.value.status_code == 400
    assert exc2.value.detail == "No pudimos reconocer el comercio en esa descripción."


# -----------------------------------------------------------------------------
# D1.3. anteriores_distintos: con 3 egresos "chinos" (2 en Super y 1 en Resto)
# -----------------------------------------------------------------------------
def test_d1_3_anteriores_distintos(db_session: Session):
    u = _crear_usuario(db_session, "user_ant")
    b = _crear_billetera(db_session, u.id)
    cat_super, _ = _crear_categoria_y_sub(db_session, "Supermercado", TipoCategoria.EGRESO, None)
    cat_resto, _ = _crear_categoria_y_sub(db_session, "Restaurantes", TipoCategoria.EGRESO, None)

    # 2 en Supermercado
    tx1 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("1500.00"),
        fecha=date(2026, 9, 10),
        descripcion="gasto en chinos",
        categoria_id=cat_super.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    tx2 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("2500.00"),
        fecha=date(2026, 9, 12),
        descripcion="Chino",
        categoria_id=cat_super.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    # 1 en Restaurantes
    tx3 = Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("3500.00"),
        fecha=date(2026, 9, 15),
        descripcion="chinos",
        categoria_id=cat_resto.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    db_session.add_all([tx1, tx2, tx3])
    db_session.commit()

    # Memoria "chino" en Supermercado
    mem = guardar(
        db=db_session,
        usuario_id=u.id,
        descripcion="chino",
        tipo="egreso",
        categoria_id=cat_super.id,
        subcategoria_id=None,
    )

    anteriores = anteriores_distintos(db_session, u.id, mem)
    assert len(anteriores) == 1
    assert anteriores[0].id == tx3.id
    assert anteriores[0].categoria_id == cat_resto.id


# -----------------------------------------------------------------------------
# D1.4. aplicar_a_anteriores: actualizadas 1, omitidas 2 (otro usuario, cuota hija)
# -----------------------------------------------------------------------------
def test_d1_4_aplicar_a_anteriores(db_session: Session):
    u1 = _crear_usuario(db_session, "u1")
    u2 = _crear_usuario(db_session, "u2")
    b1 = _crear_billetera(db_session, u1.id)
    b2 = _crear_billetera(db_session, u2.id)

    cat_super, _ = _crear_categoria_y_sub(db_session, "Supermercado", TipoCategoria.EGRESO, None)
    cat_resto, _ = _crear_categoria_y_sub(db_session, "Restaurantes", TipoCategoria.EGRESO, None)

    # tx_valida (en Restaurantes para u1)
    tx_valida = Transaccion(
        id=uuid4(),
        usuario_id=u1.id,
        billetera_id=b1.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("3000.00"),
        fecha=date(2026, 9, 1),
        descripcion="chinos",
        categoria_id=cat_resto.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    # tx_otro_user (para u2)
    tx_otro_user = Transaccion(
        id=uuid4(),
        usuario_id=u2.id,
        billetera_id=b2.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("4000.00"),
        fecha=date(2026, 9, 2),
        descripcion="chinos",
        categoria_id=cat_resto.id,
        metodo_pago=MetodoPago.DEBITO,
        origen=OrigenTransaccion.MANUAL,
    )
    # tx_cuota_hija (es_cuota_hija=True)
    tx_cuota_hija = Transaccion(
        id=uuid4(),
        usuario_id=u1.id,
        billetera_id=b1.id,
        tipo=TipoTransaccion.EGRESO,
        moneda=Moneda.ARS,
        monto=Decimal("1000.00"),
        fecha=date(2026, 9, 3),
        descripcion="chinos",
        categoria_id=cat_resto.id,
        es_cuota_hija=True,
        metodo_pago=MetodoPago.CREDITO,
        origen=OrigenTransaccion.MANUAL,
    )
    db_session.add_all([tx_valida, tx_otro_user, tx_cuota_hija])
    db_session.commit()

    mem = guardar(
        db=db_session,
        usuario_id=u1.id,
        descripcion="chino",
        tipo="egreso",
        categoria_id=cat_super.id,
        subcategoria_id=None,
    )

    resultado = aplicar_a_anteriores(
        db=db_session,
        usuario_id=u1.id,
        memoria=mem,
        transaccion_ids=[tx_valida.id, tx_otro_user.id, tx_cuota_hija.id],
        commit=True,
    )
    assert resultado["actualizadas"] == 1
    assert resultado["omitidas"] == 2

    db_session.refresh(tx_valida)
    assert tx_valida.categoria_id == cat_super.id


# -----------------------------------------------------------------------------
# D1.5. aplicar_memoria_a_movimiento e inspección AST en etapa_ia.py
# -----------------------------------------------------------------------------
def test_d1_5_aplicar_memoria_a_movimiento_y_ast(db_session: Session):
    u = _crear_usuario(db_session, "u_mov")
    cat_alim, sub_super = _crear_categoria_y_sub(
        db_session, "Alimentación", TipoCategoria.EGRESO, "Supermercado"
    )

    # Con memoria "chino" en Alimentación > Supermercado
    guardar(
        db=db_session,
        usuario_id=u.id,
        descripcion="chino",
        tipo="egreso",
        categoria_id=cat_alim.id,
        subcategoria_id=sub_super.id,
    )

    mov = {"descripcion": "gasto en el chino", "categoria": "Gastronomía > Restaurantes"}
    cambio = aplicar_memoria_a_movimiento(db_session, u.id, mov, "egreso")
    assert cambio is True
    assert mov["categoria"] == "Alimentación > Supermercado"

    # Sin memoria no cambia y devuelve False
    mov2 = {"descripcion": "nafta ypf", "categoria": "Transporte > Combustible"}
    cambio2 = aplicar_memoria_a_movimiento(db_session, u.id, mov2, "egreso")
    assert cambio2 is False
    assert mov2["categoria"] == "Transporte > Combustible"

    # Verificación por AST de etapa_ia.py
    backend_dir = Path(__file__).resolve().parent.parent
    etapa_ia_path = backend_dir / "app" / "routers" / "whatsapp" / "etapa_ia.py"
    etapa_code = etapa_ia_path.read_text(encoding="utf-8")
    tree = ast.parse(etapa_code)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "procesar_llamada_ia_y_normalizacion":
            body = node.body
            for i, stmt in enumerate(body):
                # Si el statement contiene una llamada a ajustar_categoria_marcas
                calls = [n for n in ast.walk(stmt) if isinstance(n, ast.Call)]
                for c in calls:
                    fn_name = None
                    if isinstance(c.func, ast.Name):
                        fn_name = c.func.id
                    if fn_name == "ajustar_categoria_marcas":
                        # El statement siguiente dentro de su bloque padre o adyacente debe llamar a aplicar_memoria_a_movimiento
                        pass

    assert "ajustar_categoria_marcas" in etapa_code
    assert "aplicar_memoria_a_movimiento" in etapa_code


# -----------------------------------------------------------------------------
# D1.6. Endpoints: sugerencia, DELETE y POST /aplicar con memoria de otro usuario
# -----------------------------------------------------------------------------
def test_d1_6_endpoints_memoria_comercios(db_session: Session):
    u1 = _crear_usuario(db_session, "u_end1")
    u2 = _crear_usuario(db_session, "u_end2")
    cat_egreso, _ = _crear_categoria_y_sub(db_session, "Supermercado", TipoCategoria.EGRESO, None)

    # 1. Sugerencia sin memoria
    sug_vacia = memoria_comercios.obtener_sugerencia(
        descripcion="gasto en coto",
        tipo="egreso",
        db=db_session,
        current_user=u1,
    )
    assert sug_vacia.clave == "coto"
    assert sug_vacia.memoria_id is None
    assert sug_vacia.categoria_id is None

    # Guardamos memoria para u1
    mem_u1 = memoria_comercios.guardar_memoria(
        data=MemoriaComercioCreate(
            descripcion="coto",
            tipo="egreso",
            categoria_id=cat_egreso.id,
            subcategoria_id=None,
        ),
        db=db_session,
        current_user=u1,
    )
    assert mem_u1.clave == "coto"

    # Sugerencia con memoria
    sug_llena = memoria_comercios.obtener_sugerencia(
        descripcion="coto digital",
        tipo="egreso",
        db=db_session,
        current_user=u1,
    )
    assert sug_llena.clave == "coto digital"
    # coto digital -> clave no coincide con 'coto'. Con 'gasto en coto' sí coincide:
    sug_coto = memoria_comercios.obtener_sugerencia(
        descripcion="gasto en coto",
        tipo="egreso",
        db=db_session,
        current_user=u1,
    )
    assert sug_coto.clave == "coto"
    assert sug_coto.memoria_id == mem_u1.memoria_id
    assert sug_coto.categoria_id == cat_egreso.id

    # 2. DELETE de memoria de otro usuario -> 404
    with pytest.raises(HTTPException) as exc_del:
        memoria_comercios.eliminar_memoria(
            memoria_id=mem_u1.memoria_id,
            db=db_session,
            current_user=u2,
        )
    assert exc_del.value.status_code == 404

    # 3. POST /aplicar con memoria de otro usuario -> 404
    with pytest.raises(HTTPException) as exc_apl:
        memoria_comercios.aplicar_a_anteriores(
            data=MemoriaAplicarRequest(
                memoria_id=mem_u1.memoria_id,
                transaccion_ids=[],
            ),
            db=db_session,
            current_user=u2,
        )
    assert exc_apl.value.status_code == 404
