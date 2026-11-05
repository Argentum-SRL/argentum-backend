# -*- coding: utf-8 -*-
"""
tests/test_consejos_ruta.py
Pruebas de endpoints para consejos y prioridades de usuario (fase_i1_consejos).
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.consejo import ConsejoEmitido, ConsejoSilenciado
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
)
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, CicloTipo, Moneda
from app.services import consejos_service
from app.services import fin_de_mes_service
from app.services.consejos_service import emitir_si_corresponde
from app.services.frescura_service import Frescura
from app.services.inflacion_esperada_service import InflacionEsperada
from app.services.usuario_service import eliminar_usuario


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
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db")
def db_fixture():
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


@pytest.fixture(name="usuario_p")
def usuario_p_fixture(db: Session) -> Usuario:
    p = Usuario(
        id=uuid4(),
        email="p_ruta@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        onboarding_completo=True,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_p: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_p

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _preparar_p_con_consejo(db: Session, p: Usuario, monkeypatch) -> ConsejoEmitido:
    """Configura P con sus transacciones y emite su consejo inicial."""
    monkeypatch.setattr(fin_de_mes_service, "cobertura_usuario", lambda *a, **kw: None)
    monkeypatch.setattr(
        fin_de_mes_service,
        "calcular_frescura",
        lambda *a, **kw: Frescura(
            ultima_carga=date(2026, 11, 2),
            dias_sin_cargar=0,
            umbral_dias=7,
            intervalos=5,
            datos_viejos=False,
        ),
    )
    monkeypatch.setattr(consejos_service, "hoy_argentina", lambda: date(2026, 11, 2))

    cuenta = Billetera(
        id=uuid4(),
        usuario_id=p.id,
        nombre="Cuenta",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("200000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(cuenta)

    # Categorías mínimas para base(1)
    cats_data = [
        ("Transporte", TipoCategoria.EGRESO, ["Taxi y apps"]),
        ("Alimentación", TipoCategoria.EGRESO, ["Kiosco", "Supermercado", "Verdulería", "Carnicería"]),
        ("Gastronomía", TipoCategoria.EGRESO, ["Cafetería", "Restaurantes", "Delivery"]),
        ("Vivienda", TipoCategoria.EGRESO, ["Luz", "Alquiler"]),
        ("Trabajo", TipoCategoria.INGRESO, ["Sueldo"]),
    ]
    subcats: dict[tuple[str, str], Subcategoria] = {}
    for cat_nom, tipo, sub_noms in cats_data:
        c = Categoria(id=uuid4(), nombre=cat_nom, tipo=tipo, estado=EstadoCategoria.ACTIVA)
        db.add(c)
        for sn in sub_noms:
            s = Subcategoria(id=uuid4(), categoria_id=c.id, nombre=sn, estado=EstadoSubcategoria.ACTIVA)
            db.add(s)
            subcats[(cat_nom, sn)] = s
    db.commit()

    # Sueldo
    sub_sueldo = subcats[("Trabajo", "Sueldo")]
    db.add(
        Transaccion(
            id=uuid4(),
            usuario_id=p.id,
            billetera_id=cuenta.id,
            categoria_id=sub_sueldo.categoria_id,
            subcategoria_id=sub_sueldo.id,
            monto=Decimal("1000000"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.INGRESO,
            fecha=date(2026, 10, 1),
            metodo_pago=MetodoPago.TRANSFERENCIA,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            origen=OrigenTransaccion.MANUAL,
        )
    )

    # base(1)
    filas = [
        ("Transporte", "Taxi y apps", 9, 2500),
        ("Alimentación", "Kiosco", 3, 1500),
        ("Gastronomía", "Cafetería", 5, 3000),
        ("Alimentación", "Supermercado", 10, 20000),
        ("Alimentación", "Verdulería", 4, 8000),
        ("Alimentación", "Carnicería", 4, 15000),
        ("Gastronomía", "Restaurantes", 2, 20000),
        ("Gastronomía", "Delivery", 3, 12000),
        ("Vivienda", "Luz", 1, 25000),
        ("Vivienda", "Alquiler", 1, 300000),
    ]
    for cat_nom, sub_nom, cant, monto in filas:
        sub = subcats[(cat_nom, sub_nom)]
        for _ in range(cant):
            db.add(
                Transaccion(
                    id=uuid4(),
                    usuario_id=p.id,
                    billetera_id=cuenta.id,
                    categoria_id=sub.categoria_id,
                    subcategoria_id=sub.id,
                    monto=Decimal(str(monto)),
                    moneda=Moneda.ARS,
                    tipo=TipoTransaccion.EGRESO,
                    fecha=date(2026, 10, 10),
                    metodo_pago=MetodoPago.DEBITO,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    origen=OrigenTransaccion.MANUAL,
                )
            )
    db.commit()

    inflacion = InflacionEsperada(tasas=[Decimal("2.1")], fuente_texto="REM de octubre", es_respaldo=False)
    emitido = emitir_si_corresponde(db, p, hoy=date(2026, 11, 2), inflacion=inflacion)
    assert emitido is not None
    return emitido


def test_rutas_consejos_completo(db: Session, usuario_p: Usuario, client: TestClient, monkeypatch):
    """Prueba completa del ciclo de vida del endpoint /consejos."""
    emitido = _preparar_p_con_consejo(db, usuario_p, monkeypatch)

    # 1. GET /consejos/actual → {"consejo": {"id": ..., "tipo": "colchon", "texto": <el de P>, "accion": "crear_meta", "accion_texto": "Crear meta", "accion_datos": {"nombre": "Imprevistos", "monto": "368000", "moneda": "ARS"}}}
    resp = client.get("/consejos/actual")
    assert resp.status_code == 200
    data = resp.json()
    assert data["consejo"] is not None
    c_data = data["consejo"]
    assert c_data["id"] == str(emitido.id)
    assert c_data["tipo"] == "colchon"
    assert c_data["texto"] == emitido.texto
    assert c_data["accion"] == "crear_meta"
    assert c_data["accion_texto"] == "Crear meta"
    assert c_data["accion_datos"] == {"nombre": "Imprevistos", "monto": "368000", "moneda": "ARS"}

    # 2. POST "otro" → 422
    resp_422 = client.post(f"/consejos/{emitido.id}/respuesta", json={"respuesta": "otro"})
    assert resp_422.status_code == 422

    # 3. con el id de otro usuario → 404
    id_otro = uuid4()
    resp_404 = client.post(f"/consejos/{id_otro}/respuesta", json={"respuesta": "sirvio"})
    assert resp_404.status_code == 404

    # 4. POST "sirvio" → 200 {"ok": true} y después GET → {"consejo": null}
    resp_post = client.post(f"/consejos/{emitido.id}/respuesta", json={"respuesta": "sirvio"})
    assert resp_post.status_code == 200
    assert resp_post.json() == {"ok": True}

    resp_despues = client.get("/consejos/actual")
    assert resp_despues.status_code == 200
    assert resp_despues.json() == {"consejo": None}

    # 5. segundo POST "no_sirve" → 200 y queda "sirvio"
    resp_segundo = client.post(f"/consejos/{emitido.id}/respuesta", json={"respuesta": "no_sirve"})
    assert resp_segundo.status_code == 200
    db.refresh(emitido)
    assert emitido.respuesta == "sirvio"

    # 6. "no_avisar" → fila en consejos_silenciados (P, "colchon")
    # Emitimos otro consejo para probar silenciar
    c2 = ConsejoEmitido(
        id=uuid4(),
        usuario_id=usuario_p.id,
        tipo="hormiga",
        clave="hormiga/ARS/test",
        moneda="ARS",
        texto="Texto hormiga",
        accion=None,
        accion_datos=None,
        valores={},
        fecha_emision=date(2026, 11, 2),
        fecha_vence=date(2026, 11, 9),
    )
    db.add(c2)
    db.commit()

    resp_silenciar = client.post(f"/consejos/{c2.id}/respuesta", json={"respuesta": "no_avisar"})
    assert resp_silenciar.status_code == 200
    silenciado = (
        db.query(ConsejoSilenciado)
        .filter(ConsejoSilenciado.usuario_id == usuario_p.id, ConsejoSilenciado.tipo == "hormiga")
        .first()
    )
    assert silenciado is not None

    # 7. Con hoy parcheada al 09/11/2026 → {"consejo": null}
    # Creamos un consejo activo que vence el 09/11/2026
    c_activo = ConsejoEmitido(
        id=uuid4(),
        usuario_id=usuario_p.id,
        tipo="comisiones",
        clave="comisiones/ARS/test",
        moneda="ARS",
        texto="Texto comisiones",
        accion=None,
        accion_datos=None,
        valores={},
        fecha_emision=date(2026, 11, 2),
        fecha_vence=date(2026, 11, 9),
    )
    db.add(c_activo)
    db.commit()

    monkeypatch.setattr(consejos_service, "hoy_argentina", lambda: date(2026, 11, 9))
    resp_vencido = client.get("/consejos/actual")
    assert resp_vencido.status_code == 200
    assert resp_vencido.json() == {"consejo": None}


def test_rutas_prioridades_y_eliminacion_cuenta(db: Session, usuario_p: Usuario, client: TestClient):
    """PUT /usuarios/me/prioridades-consejos y eliminación de cuenta."""
    # PUT /usuarios/me/prioridades-consejos: [1, 3, 7] → 200 con prioridades_consejos [1, 3, 7]
    resp = client.put("/usuarios/me/prioridades-consejos", json={"prioridades": [1, 3, 7]})
    assert resp.status_code == 200
    assert resp.json()["prioridades_consejos"] == [1, 3, 7]

    # [1, 1], [0], [9] y [1, 2, 3, 4] → 422
    for payload in [[1, 1], [0], [9], [1, 2, 3, 4]]:
        resp_err = client.put("/usuarios/me/prioridades-consejos", json={"prioridades": payload})
        assert resp_err.status_code == 422, f"Falló validación para {payload}"

    # [] → 200 con []
    resp_vacio = client.put("/usuarios/me/prioridades-consejos", json={"prioridades": []})
    assert resp_vacio.status_code == 200
    assert resp_vacio.json()["prioridades_consejos"] == []

    # GET /usuarios/me de un usuario nuevo → prioridades_consejos []
    u_nuevo = Usuario(
        id=uuid4(),
        email="nuevo@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
    )
    db.add(u_nuevo)
    db.commit()

    app.dependency_overrides[get_current_user] = lambda: u_nuevo
    resp_me = client.get("/usuarios/me")
    assert resp_me.status_code == 200
    assert resp_me.json()["prioridades_consejos"] == []

    # Eliminar la cuenta de P → no quedan filas suyas en las dos tablas
    c_emit = ConsejoEmitido(
        id=uuid4(),
        usuario_id=usuario_p.id,
        tipo="hormiga",
        clave="h",
        moneda="ARS",
        texto="t",
        valores={},
        fecha_emision=date(2026, 11, 2),
        fecha_vence=date(2026, 11, 9),
    )
    c_sil = ConsejoSilenciado(usuario_id=usuario_p.id, tipo="hormiga")
    db.add_all([c_emit, c_sil])
    db.commit()

    eliminar_usuario(db, usuario_p)

    assert db.query(ConsejoEmitido).filter(ConsejoEmitido.usuario_id == usuario_p.id).count() == 0
    assert db.query(ConsejoSilenciado).filter(ConsejoSilenciado.usuario_id == usuario_p.id).count() == 0
