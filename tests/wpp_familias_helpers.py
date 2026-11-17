# -*- coding: utf-8 -*-
"""Fixtures y helpers compartidos para pruebas de familias de textos WhatsApp."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid
from uuid import uuid4

import sqlalchemy.types as types
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

if (
    not hasattr(PGUUID, "_compiler_dispatcher")
    or "sqlite" not in getattr(PGUUID._compiler_dispatcher, "specs", {})
):
    @compiles(PGUUID, "sqlite")
    def _compile_pguuid_sqlite(type_, compiler, **kw):
        return "CHAR(32)"

if (
    not hasattr(JSONB, "_compiler_dispatcher")
    or "sqlite" not in getattr(JSONB._compiler_dispatcher, "specs", {})
):
    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(type_, compiler, **kw):
        return "TEXT"

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import whatsapp_service


def aplicar_compat_sqlite(monkeypatch):
    """Compatibilidad UUID y DateTime con zona horaria en SQLite."""
    orig_uuid = types.Uuid.bind_processor

    def _safe_uuid(self, dialect):
        proc = orig_uuid(self, dialect)
        if proc is None:
            return None

        def _proc(val):
            if isinstance(val, str):
                try:
                    val = uuid.UUID(val)
                except Exception:
                    pass
            return proc(val)

        return _proc

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid)
    from sqlalchemy.dialects.sqlite.base import DATETIME

    orig_dt = DATETIME.result_processor

    def _safe_dt(self, dialect, coltype):
        proc = orig_dt(self, dialect, coltype)

        def _proc(val):
            res = proc(val) if proc else val
            if res is not None and isinstance(res, datetime) and res.tzinfo is None:
                return res.replace(tzinfo=timezone.utc)
            return res

        return _proc

    monkeypatch.setattr(DATETIME, "result_processor", _safe_dt)


def crear_db_sqlite(monkeypatch):
    """Base SQLite en memoria aislada por test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models as _modelos
    _ = _modelos
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("app.core.database.SessionLocal", TestingSession)
    monkeypatch.setattr("app.services.whatsapp_service.SessionLocal", TestingSession)
    return TestingSession()


def instalar_enviados(monkeypatch):
    """Captura los mensajes enviados vía whatsapp_service.enviar_whatsapp."""
    lista: list[str] = []

    def _fake_enviar(numero: str, mensaje: str, tipo: str = "respuesta", exento_tope: bool = False):
        lista.append(mensaje)
        return True

    monkeypatch.setattr(whatsapp_service, "enviar_whatsapp", _fake_enviar)
    return lista


def _fijar_hoy(monkeypatch, hoy: date) -> None:
    """Fija hoy_argentina en los módulos de WhatsApp para textos determinísticos."""
    for mod in (
        "app.utils.fecha",
        "app.routers.whatsapp.registro",
        "app.routers.whatsapp.propuestas",
        "app.routers.whatsapp.parsers",
        "app.routers.whatsapp.deshacer_corregir",
        "app.routers.whatsapp.factura_wpp",
        "app.routers.whatsapp.lote_documento",
        "app.routers.whatsapp.reintegro_wpp",
        "app.routers.whatsapp.que_es_plata_wpp",
        "app.routers.whatsapp.dolares_desde_pesos",
        "app.routers.whatsapp.transferencias",
        "app.routers.whatsapp.transferencias_flujo",
        "app.routers.whatsapp.transferencias_flujo_b",
        "app.routers.whatsapp.handlers_menus",
        "app.routers.whatsapp.handlers_suscripciones",
        "app.routers.whatsapp.confirmaciones",
        "app.routers.whatsapp.metas",
        "app.routers.whatsapp.enriquecedores",
    ):
        try:
            monkeypatch.setattr(f"{mod}.hoy_argentina", lambda h=hoy: h)
        except AttributeError:
            pass


def _crear_escenario_base(db: Session, hoy: date):
    """Crea usuario, cuentas, tarjeta y categorías estándar para las pruebas."""
    u = Usuario(
        id=uuid4(),
        email=f"fam_{uuid4().hex[:6]}@argentum.com",
        telefono="+5491112345678",
        telefono_normalizado="+5491112345678",
        telefono_verificado=True,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        nombre="Sofía",
        apellido="Test",
    )
    db.add(u)
    db.flush()

    b_galicia = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000"),
        saldo_actual=Decimal("100000"),
        tiene_punto_partida=True,
        fecha_saldo=date(2026, 10, 1),
        es_principal=True,
        estado=EstadoBilletera.ACTIVA,
    )
    b_mp = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Mercado Pago",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("50000"),
        saldo_actual=Decimal("50000"),
        tiene_punto_partida=True,
        fecha_saldo=date(2026, 10, 1),
        es_principal=False,
        estado=EstadoBilletera.ACTIVA,
    )
    b_usd = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Caja USD",
        moneda=Moneda.USD,
        saldo_inicial=Decimal("1000"),
        saldo_actual=Decimal("1000"),
        tiene_punto_partida=True,
        fecha_saldo=date(2026, 10, 1),
        es_principal=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add_all([b_galicia, b_mp, b_usd])
    db.flush()

    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_galicia.id,
        nombre="•••• 1506",
        red=RedTarjeta.VISA,
        estado=EstadoTarjeta.ACTIVA,
        dia_cierre=20,
        dia_vencimiento=5,
    )
    db.add(tarjeta)

    cat_alim = Categoria(
        id=uuid4(),
        nombre="Alimentación",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_serv = Categoria(
        id=uuid4(),
        nombre="Servicios",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_otros = Categoria(
        id=uuid4(),
        nombre="Otros",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_ing = Categoria(
        id=uuid4(),
        nombre="Ingresos",
        tipo=TipoCategoria.INGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    cat_banco = Categoria(
        id=uuid4(),
        nombre="Banco",
        tipo=TipoCategoria.EGRESO,
        estado=EstadoCategoria.ACTIVA,
    )
    db.add_all([cat_alim, cat_serv, cat_otros, cat_ing, cat_banco])
    db.flush()

    sub_kiosco = Subcategoria(
        id=uuid4(),
        categoria_id=cat_alim.id,
        nombre="Kiosco",
        estado=EstadoSubcategoria.ACTIVA,
    )
    sub_super = Subcategoria(
        id=uuid4(),
        categoria_id=cat_alim.id,
        nombre="Supermercado",
        estado=EstadoSubcategoria.ACTIVA,
    )
    sub_verd = Subcategoria(
        id=uuid4(),
        categoria_id=cat_alim.id,
        nombre="Verdulería",
        estado=EstadoSubcategoria.ACTIVA,
    )
    sub_deliv = Subcategoria(
        id=uuid4(),
        categoria_id=cat_alim.id,
        nombre="Delivery",
        estado=EstadoSubcategoria.ACTIVA,
    )
    sub_elec = Subcategoria(
        id=uuid4(),
        categoria_id=cat_serv.id,
        nombre="Electricidad",
        estado=EstadoSubcategoria.ACTIVA,
    )
    sub_sueldo = Subcategoria(
        id=uuid4(),
        categoria_id=cat_ing.id,
        nombre="Sueldo",
        estado=EstadoSubcategoria.ACTIVA,
    )
    sub_otros = Subcategoria(
        id=uuid4(),
        categoria_id=cat_otros.id,
        nombre="Otros",
        estado=EstadoSubcategoria.ACTIVA,
    )
    sub_comision = Subcategoria(
        id=uuid4(),
        categoria_id=cat_banco.id,
        nombre="Comisiones y gastos bancarios",
        estado=EstadoSubcategoria.ACTIVA,
    )
    db.add_all([
        sub_kiosco, sub_super, sub_verd, sub_deliv,
        sub_elec, sub_sueldo, sub_otros, sub_comision,
    ])
    db.commit()

    return {
        "usuario": u,
        "galicia": b_galicia,
        "mp": b_mp,
        "usd": b_usd,
        "tarjeta": tarjeta,
        "cat_alim": cat_alim,
        "cat_serv": cat_serv,
        "cat_otros": cat_otros,
        "cat_ing": cat_ing,
        "sub_kiosco": sub_kiosco,
        "sub_super": sub_super,
        "sub_verd": sub_verd,
        "sub_deliv": sub_deliv,
        "sub_elec": sub_elec,
        "sub_sueldo": sub_sueldo,
        "sub_otros": sub_otros,
    }


def _crear_conv_propuesta(
    db: Session,
    usuario: Usuario,
    intent: str,
    entidades: dict,
    slot_activo: bool = False,
    slot_estado: dict | None = None,
) -> ConversacionWpp:
    conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=f"wamid_{uuid4().hex[:8]}",
        mensaje_usuario="msg",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot="prop",
        intent_detectado=intent,
        entidades=entidades,
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        slot_filling_activo=slot_activo,
        slot_filling_estado=slot_estado,
        fecha=datetime.now(timezone.utc),
    )
    db.add(conv)
    db.commit()
    return conv
