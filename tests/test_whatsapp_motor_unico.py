import ast
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import uuid
from sqlalchemy import types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.notificacion import TipoNotificacion
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate
from app.routers.whatsapp.registro import (
    _confirmar_propuesta_transaccion,
    _registrar_item_batch,
    _registrar_movimiento_directo,
)
from app.services import presupuesto_service


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

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario_y_billetera(db, saldo=Decimal("20000.00"), moneda=Moneda.ARS):
    u = Usuario(
        id=uuid4(),
        email=f"wpp_{uuid4().hex[:6]}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=moneda,
    )
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Efectivo",
        moneda=moneda,
        saldo_actual=saldo,
        estado=EstadoBilletera.ACTIVA,
        es_principal=True,
    )
    cat_kiosco = Categoria(
        id=uuid4(),
        nombre="Kiosco",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_otros = Categoria(
        id=uuid4(),
        nombre="Otros",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_otros_ing = Categoria(
        id=uuid4(),
        nombre="Otros",
        tipo=TipoCategoria.INGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([u, b, cat_kiosco, cat_otros, cat_otros_ing])
    db.commit()
    return u, b, cat_kiosco, cat_otros


# 1. Por AST: en app/routers/whatsapp/ no hay llamadas a Transaccion(...),
# ni asignaciones a .saldo_actual, ni llamadas a registrar_impacto_presupuesto.
def test_01_ast_sin_llamadas_prohibidas():
    backend_dir = Path(__file__).resolve().parent.parent
    whatsapp_dir = backend_dir / "app" / "routers" / "whatsapp"

    transaccion_calls = []
    saldo_actual_assigns = []
    impacto_presupuesto_calls = []

    class Inspector(ast.NodeVisitor):
        def __init__(self, filepath):
            self.filepath = filepath

        def visit_Call(self, node):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr

            if name == "Transaccion":
                transaccion_calls.append(f"{self.filepath}:{node.lineno}")
            elif name == "registrar_impacto_presupuesto":
                impacto_presupuesto_calls.append(f"{self.filepath}:{node.lineno}")

            self.generic_visit(node)

        def visit_Assign(self, node):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr == "saldo_actual":
                    saldo_actual_assigns.append(f"{self.filepath}:{node.lineno}")
            self.generic_visit(node)

        def visit_AugAssign(self, node):
            if isinstance(node.target, ast.Attribute) and node.target.attr == "saldo_actual":
                saldo_actual_assigns.append(f"{self.filepath}:{node.lineno}")
            self.generic_visit(node)

    for py_file in whatsapp_dir.rglob("*.py"):
        code = py_file.read_text(encoding="utf-8")
        tree = ast.parse(code, filename=str(py_file))
        rel = py_file.relative_to(backend_dir).as_posix()
        Inspector(rel).visit(tree)

    assert not transaccion_calls, f"Se encontraron llamadas a Transaccion(...): {transaccion_calls}"
    assert not saldo_actual_assigns, f"Se encontraron asignaciones a .saldo_actual: {saldo_actual_assigns}"
    assert not impacto_presupuesto_calls, f"Se encontraron llamadas a registrar_impacto_presupuesto: {impacto_presupuesto_calls}"


# 2. Un ítem de lote por billetera (_registrar_item_batch): egreso de 5000, Kiosco,
# billetera con saldo 20000 y fecha de hoy.
# - Crea una transacción CONFIRMADA de origen IA_WPP con monto 5000, esa billetera y esa categoría.
# - El saldo queda en 15000.
# - registrar_impacto_presupuesto se llama una sola vez.
def test_02_item_batch_billetera_monto_saldo_impacto(db):
    u, b, cat_kiosco, _ = _crear_usuario_y_billetera(db, saldo=Decimal("20000.00"))

    datos = {
        "tipo": "egreso",
        "monto": Decimal("5000.00"),
        "categoria": "Kiosco",
        "fecha": str(date.today()),
        "descripcion": "Kiosco golosinas",
        "billetera": b.nombre,
    }

    with patch.object(presupuesto_service, "registrar_impacto_presupuesto", wraps=presupuesto_service.registrar_impacto_presupuesto) as spy_impacto:
        tx, motivo = _registrar_item_batch(datos, u.id, [b], [], db)

    assert tx is not None
    assert motivo is None
    assert tx.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
    assert tx.origen == OrigenTransaccion.IA_WPP
    assert tx.monto == Decimal("5000.00")
    assert tx.billetera_id == b.id
    assert tx.categoria_id == cat_kiosco.id
    assert b.saldo_actual == Decimal("15000.00")
    assert spy_impacto.call_count == 1


# 3. _registrar_item_batch usa la fecha devuelta por _resolver_y_validar_fecha.
def test_03_item_batch_usa_fecha_de_resolver_y_validar(db):
    u, b, cat_kiosco, _ = _crear_usuario_y_billetera(db, saldo=Decimal("20000.00"))

    fecha_conocida = date(2026, 5, 10)
    datos = {
        "tipo": "egreso",
        "monto": Decimal("5000.00"),
        "categoria": "Kiosco",
        "fecha": "ayer",
        "descripcion": "Kiosco conocido",
        "billetera": b.nombre,
    }

    with patch("app.routers.whatsapp.registro._resolver_y_validar_fecha", return_value=(fecha_conocida, None)):
        tx, motivo = _registrar_item_batch(datos, u.id, [b], [], db)

    assert tx is not None
    assert motivo is None
    assert tx.fecha == fecha_conocida


# 4. Un egreso que deja la billetera exactamente en 0 crea el aviso SALDO_CERO con canal_whatsapp False.
def test_04_egreso_saldo_cero_aviso_sin_whatsapp(db):
    u, b, cat_kiosco, _ = _crear_usuario_y_billetera(db, saldo=Decimal("5000.00"))

    datos = {
        "tipo": "egreso",
        "monto": Decimal("5000.00"),
        "categoria": "Kiosco",
        "fecha": str(date.today()),
        "descripcion": "Gasto total",
        "billetera": b.nombre,
    }

    from app.services import notificacion_service
    with patch.object(notificacion_service, "crear_notificacion", wraps=notificacion_service.crear_notificacion) as spy_notif:
        tx, motivo = _registrar_item_batch(datos, u.id, [b], [], db)

    assert tx is not None
    assert motivo is None
    assert b.saldo_actual == Decimal("0.00")

    # Verificar que se llamó a crear_notificacion para SALDO_CERO con canal_whatsapp=False
    called = False
    for call in spy_notif.call_args_list:
        kwargs = call.kwargs
        if kwargs.get("tipo") == TipoNotificacion.SALDO_CERO:
            assert kwargs.get("canal_whatsapp") is False
            called = True
            break
    assert called, "No se invocó crear_notificacion para SALDO_CERO"


# 5. Una categoría inválida descarta el ítem con el mensaje "No se pudo registrar <desc>: Debés seleccionar una categoría.",
# y no escribe ni la transacción ni el saldo.
def test_05_categoria_invalida_descarta_item(db):
    u, b, _, _ = _crear_usuario_y_billetera(db, saldo=Decimal("20000.00"))

    datos = {
        "tipo": "egreso",
        "monto": Decimal("5000.00"),
        "categoria": "CategoriaInexistenteTotal",
        "fecha": str(date.today()),
        "descripcion": "Kiosco",
        "billetera": b.nombre,
    }

    tx_count_before = db.query(Transaccion).count()
    with patch("app.routers.whatsapp.registro._resolver_categoria_y_subcategoria", return_value=(None, None)), \
         patch("app.routers.whatsapp.registro.TransaccionCreate", side_effect=lambda **kw: TransaccionCreate.model_construct(**kw)):
        tx, motivo = _registrar_item_batch(datos, u.id, [b], [], db)

    assert tx is None
    assert motivo == "No se pudo registrar Kiosco: Debés seleccionar una categoría."
    assert b.saldo_actual == Decimal("20000.00")
    assert db.query(Transaccion).count() == tx_count_before


# 6. Un caso de monto y saldo para _registrar_movimiento_directo,
# y otro para la rama de billetera única de _confirmar_propuesta_transaccion.
def test_06_movimiento_directo_y_confirmar_propuesta_billetera_unica(db):
    u, b, cat_kiosco, _ = _crear_usuario_y_billetera(db, saldo=Decimal("10000.00"))

    # Parte A: _registrar_movimiento_directo
    entidades_directo = {
        "tipo": "egreso",
        "monto": Decimal("3000.00"),
        "categoria": "Kiosco",
        "fecha": str(date.today()),
        "billetera_resuelta_nombre": b.nombre,
        "descripcion": "Gasto directo",
    }
    tx_dir, msg_dir = _registrar_movimiento_directo(u, entidades_directo, db)
    assert tx_dir is not None
    assert tx_dir.monto == Decimal("3000.00")
    assert b.saldo_actual == Decimal("7000.00")

    # Parte B: _confirmar_propuesta_transaccion (billetera única)
    entidades_propuesta = {
        "tipo": "egreso",
        "monto": Decimal("2000.00"),
        "categoria": "Kiosco",
        "fecha": str(date.today()),
        "billetera_resuelta_id": b.id,
        "billetera": b.nombre,
        "descripcion": "Gasto confirmado",
    }
    tx_conf, msg_conf, ya_conf = _confirmar_propuesta_transaccion(
        u, db, entidades_actuales=entidades_propuesta
    )
    assert tx_conf is not None
    assert ya_conf is False
    assert tx_conf.monto == Decimal("2000.00")
    assert b.saldo_actual == Decimal("5000.00")
