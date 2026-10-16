"""
tests/test_recordatorio_impago_wpp.py

Casos de prueba para el recordatorio por WhatsApp de resúmenes de tarjeta impagos.
Cubre los casos a) hasta j) requeridos por la especificación.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.config import settings
from app.core.database import Base
from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.recordatorio_impago_wpp import enviar_recordatorio_impago_si_corresponde


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    """Configuración de compatibilidad con SQLite en memoria para JSONB y UUID."""
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


@pytest.fixture(name="db", scope="function")
def db_fixture():
    """Sesión de base de datos aislada en memoria."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


def _crear_usuario(db: Session) -> Usuario:
    """Crea un usuario activo para pruebas."""
    u = Usuario(
        id=uuid4(),
        email=f"user_{uuid4().hex[:6]}@argentum.com",
        telefono="+5491112345678",
        telefono_normalizado="+5491112345678",
        telefono_verificado=True,
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(u)
    db.commit()
    return u


def _crear_tarjeta(db: Session, usuario: Usuario, nombre: str = "Visa") -> TarjetaCredito:
    """Crea una tarjeta de crédito activa para el usuario."""
    b = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Cuenta Banco",
        moneda=Moneda.ARS,
        saldo_inicial=Decimal("100000.00"),
        saldo_actual=Decimal("100000.00"),
        es_inversion=False,
    )
    db.add(b)

    t = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=b.id,
        nombre=nombre,
        red=RedTarjeta.VISA,
        moneda=Moneda.ARS,
        dia_cierre=24,
        dia_vencimiento=4,
        regla_cierre="dia_fijo",
        estado=EstadoTarjeta.ACTIVA,
    )
    db.add(t)
    db.commit()
    return t


def _crear_notificacion_impago(
    db: Session,
    usuario: Usuario,
    tarjeta: TarjetaCredito,
    vencimiento: date,
    d: int = 0,
    monto_str: str = "50000",
    archivada: bool = False,
    enviada_whatsapp: bool = False,
) -> Notificacion:
    """Crea una notificación RESUMEN_IMPAGO."""
    deep_link = f"/app/billeteras/{tarjeta.billetera_id}?tab=credito&tarjeta_id={tarjeta.id}"
    notif = Notificacion(
        id=uuid4(),
        usuario_id=usuario.id,
        tipo=TipoNotificacion.RESUMEN_IMPAGO,
        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
        mensaje=f"Resumen impago de {tarjeta.nombre}",
        entidad_tipo="tarjeta",
        entidad_id=tarjeta.id,
        deep_link=deep_link,
        canal_web=True,
        canal_whatsapp=False,
        canal_email=False,
        archivada=archivada,
        enviada_whatsapp=enviada_whatsapp,
        datos_template={
            "vencimiento": vencimiento.isoformat(),
            "monto": monto_str,
            "d": d,
        },
    )
    db.add(notif)
    db.commit()
    return notif


def test_caso_a_un_resumen_impago_texto_exacto(db: Session):
    """
    a) 1 resumen impago con deuda 50000 ARS (tarjeta 'Visa', vencimiento 04/11):
       sale exactamente el texto de 1 resumen y la notificación queda enviada_whatsapp=True.
    """
    usuario = _crear_usuario(db)
    tarjeta = _crear_tarjeta(db, usuario, nombre="Visa")
    vencimiento = date(2026, 11, 4)
    notif = _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=0, monto_str="50000")

    expected_link = f"{settings.FRONTEND_URL}/app/billeteras/{tarjeta.billetera_id}?tab=credito&tarjeta_id={tarjeta.id}"
    expected_texto = (
        f"Ojo: el resumen de Visa de $50.000 que venció el 04/11 figura sin pagar. "
        f"Si ya lo pagaste, marcalo acá: {expected_link}"
    )

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp", return_value=True) as mock_wpp:
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("50000")}

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)

        assert res is True
        mock_wpp.assert_called_once_with(usuario.telefono, expected_texto)

    db.refresh(notif)
    assert notif.enviada_whatsapp is True


def test_caso_b_se_llama_de_nuevo_enseguida_no_sale_nada(db: Session):
    """
    b) Se llama de nuevo enseguida: no sale nada y devuelve False.
    """
    usuario = _crear_usuario(db)
    tarjeta = _crear_tarjeta(db, usuario, nombre="Visa")
    vencimiento = date(2026, 11, 4)
    notif = _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=0)

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp", return_value=True) as mock_wpp:
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("50000")}

        # Primera llamada exitosa
        res1 = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)
        assert res1 is True
        assert mock_wpp.call_count == 1

        # Segunda llamada enseguida
        mock_wpp.reset_mock()
        res2 = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)
        assert res2 is False
        mock_wpp.assert_not_called()

    db.refresh(notif)
    assert notif.enviada_whatsapp is True


def test_caso_c_notificacion_archivada_no_sale_nada(db: Session):
    """
    c) Notificación archivada: no sale nada.
    """
    usuario = _crear_usuario(db)
    tarjeta = _crear_tarjeta(db, usuario, nombre="Visa")
    vencimiento = date(2026, 11, 4)
    _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=0, archivada=True)

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp") as mock_wpp:
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("50000")}

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)

        assert res is False
        mock_wpp.assert_not_called()


def test_caso_d_deuda_restante_cero_no_sale_nada_y_queda_marcada(db: Session):
    """
    d) Deuda restante 0: no sale nada y la notificación queda enviada_whatsapp=True.
    """
    usuario = _crear_usuario(db)
    tarjeta = _crear_tarjeta(db, usuario, nombre="Visa")
    vencimiento = date(2026, 11, 4)
    notif = _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=0)

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp") as mock_wpp:
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("0")}

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)

        assert res is False
        mock_wpp.assert_not_called()

    db.refresh(notif)
    assert notif.enviada_whatsapp is True


def test_caso_e_propuesta_pendiente_no_sale_nada_y_sigue_sin_marcar(db: Session):
    """
    e) Hay propuesta pendiente: no sale nada y la notificación sigue en enviada_whatsapp=False.
    """
    usuario = _crear_usuario(db)
    tarjeta = _crear_tarjeta(db, usuario, nombre="Visa")
    vencimiento = date(2026, 11, 4)
    notif = _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=0)

    # Simular propuesta pendiente activa
    propuesta = ConversacionWpp(
        usuario_id=usuario.id,
        mensaje_usuario="gasto 500",
        mensaje_bot="¿Confirmás el gasto?",
        intent_detectado="registrar_transaccion",
        slot_filling_activo=False,
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(propuesta)
    db.commit()

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp") as mock_wpp:
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("50000")}

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)

        assert res is False
        mock_wpp.assert_not_called()

    db.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_f_dos_tarjetas_impagas_varios_resumenes_ordenados(db: Session):
    """
    f) 2 tarjetas impagas: un solo mensaje con el texto de varios resúmenes,
       del vencimiento más viejo al más nuevo.
    """
    usuario = _crear_usuario(db)
    tarjeta1 = _crear_tarjeta(db, usuario, nombre="Mastercard")
    tarjeta2 = _crear_tarjeta(db, usuario, nombre="Visa")

    # Vencimientos: tarjeta1 más viejo (20/10), tarjeta2 más nuevo (04/11)
    venc1 = date(2026, 10, 20)
    venc2 = date(2026, 11, 4)

    notif1 = _crear_notificacion_impago(db, usuario, tarjeta1, venc1, monto_str="20000")
    notif2 = _crear_notificacion_impago(db, usuario, tarjeta2, venc2, monto_str="50000")

    link1 = f"{settings.FRONTEND_URL}/app/billeteras/{tarjeta1.billetera_id}?tab=credito&tarjeta_id={tarjeta1.id}"
    link2 = f"{settings.FRONTEND_URL}/app/billeteras/{tarjeta2.billetera_id}?tab=credito&tarjeta_id={tarjeta2.id}"

    expected_texto = (
        "Ojo: estos resúmenes figuran sin pagar:\n"
        f"- Mastercard: $20.000, venció el 20/10. {link1}\n"
        f"- Visa: $50.000, venció el 04/11. {link2}\n"
        "Si ya los pagaste, marcalos desde esos links."
    )

    def mock_obtener_detalle(db_sess, tarjeta_id, venc):
        if tarjeta_id == tarjeta1.id:
            return {"monto_deuda_restante": Decimal("20000")}
        return {"monto_deuda_restante": Decimal("50000")}

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento", side_effect=mock_obtener_detalle), \
         patch("app.services.whatsapp_service.enviar_whatsapp", return_value=True) as mock_wpp:

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)

        assert res is True
        mock_wpp.assert_called_once_with(usuario.telefono, expected_texto)

    db.refresh(notif1)
    db.refresh(notif2)
    assert notif1.enviada_whatsapp is True
    assert notif2.enviada_whatsapp is True


def test_caso_g_mismo_resumen_notificaciones_d0_y_d3_una_sola_linea(db: Session):
    """
    g) El mismo resumen con notificaciones d=0 y d=3: una sola línea y las dos quedan marcadas.
    """
    usuario = _crear_usuario(db)
    tarjeta = _crear_tarjeta(db, usuario, nombre="Visa")
    vencimiento = date(2026, 11, 4)

    notif_d0 = _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=0, monto_str="50000")
    notif_d3 = _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=3, monto_str="50000")

    expected_link = f"{settings.FRONTEND_URL}/app/billeteras/{tarjeta.billetera_id}?tab=credito&tarjeta_id={tarjeta.id}"
    expected_texto = (
        f"Ojo: el resumen de Visa de $50.000 que venció el 04/11 figura sin pagar. "
        f"Si ya lo pagaste, marcalo acá: {expected_link}"
    )

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp", return_value=True) as mock_wpp:
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("50000")}

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)

        assert res is True
        mock_wpp.assert_called_once_with(usuario.telefono, expected_texto)

    db.refresh(notif_d0)
    db.refresh(notif_d3)
    assert notif_d0.enviada_whatsapp is True
    assert notif_d3.enviada_whatsapp is True


def test_caso_h_cuatro_resumenes_solo_tres_mas_viejos(db: Session):
    """
    h) 4 resúmenes: se nombran los 3 más viejos y el cuarto queda en enviada_whatsapp=False.
    """
    usuario = _crear_usuario(db)
    t1 = _crear_tarjeta(db, usuario, nombre="Tarjeta A")
    t2 = _crear_tarjeta(db, usuario, nombre="Tarjeta B")
    t3 = _crear_tarjeta(db, usuario, nombre="Tarjeta C")
    t4 = _crear_tarjeta(db, usuario, nombre="Tarjeta D")

    # Fechas ordenadas: t1 (01/10), t2 (10/10), t3 (20/10), t4 (05/11)
    v1 = date(2026, 10, 1)
    v2 = date(2026, 10, 10)
    v3 = date(2026, 10, 20)
    v4 = date(2026, 11, 5)

    n1 = _crear_notificacion_impago(db, usuario, t1, v1)
    n2 = _crear_notificacion_impago(db, usuario, t2, v2)
    n3 = _crear_notificacion_impago(db, usuario, t3, v3)
    n4 = _crear_notificacion_impago(db, usuario, t4, v4)

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp", return_value=True) as mock_wpp:
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("10000")}

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)

        assert res is True
        assert mock_wpp.call_count == 1
        mensaje_enviado = mock_wpp.call_args[0][1]

        # Debe mencionar las 3 más viejas (A, B, C) y no la cuarta (D)
        assert "Tarjeta A" in mensaje_enviado
        assert "Tarjeta B" in mensaje_enviado
        assert "Tarjeta C" in mensaje_enviado
        assert "Tarjeta D" not in mensaje_enviado

    db.refresh(n1)
    db.refresh(n2)
    db.refresh(n3)
    db.refresh(n4)
    assert n1.enviada_whatsapp is True
    assert n2.enviada_whatsapp is True
    assert n3.enviada_whatsapp is True
    assert n4.enviada_whatsapp is False


def test_caso_i_obtener_detalle_resumen_vencimiento_excepcion_retorna_false(db: Session):
    """
    i) obtener_detalle_resumen_vencimiento lanza una excepción: devuelve False y no propaga nada.
    """
    usuario = _crear_usuario(db)
    tarjeta = _crear_tarjeta(db, usuario, nombre="Visa")
    vencimiento = date(2026, 11, 4)
    notif = _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=0)

    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento", side_effect=RuntimeError("Falla de base")):
        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)

        assert res is False

    db.refresh(notif)
    assert notif.enviada_whatsapp is False


def test_caso_j_enviar_whatsapp_falla_notificaciones_no_se_marcan(db: Session):
    """
    j) enviar_whatsapp devuelve False o falla: las notificaciones no se marcan.
    """
    usuario = _crear_usuario(db)
    tarjeta = _crear_tarjeta(db, usuario, nombre="Visa")
    vencimiento = date(2026, 11, 4)
    notif = _crear_notificacion_impago(db, usuario, tarjeta, vencimiento, d=0)

    # Subcaso 1: enviar_whatsapp retorna False
    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp", return_value=False):
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("50000")}

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)
        assert res is False

    db.refresh(notif)
    assert notif.enviada_whatsapp is False

    # Subcaso 2: enviar_whatsapp lanza una excepción
    with patch("app.services.tarjeta_service.obtener_detalle_resumen_vencimiento") as mock_detalle, \
         patch("app.services.whatsapp_service.enviar_whatsapp", side_effect=Exception("Falla de red")):
        mock_detalle.return_value = {"monto_deuda_restante": Decimal("50000")}

        res = enviar_recordatorio_impago_si_corresponde(db, usuario, usuario.telefono)
        assert res is False

    db.refresh(notif)
    assert notif.enviada_whatsapp is False
