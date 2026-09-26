"""
Módulo de Seguridad, Canario, Interceptación de Envíos y Borrado Limpio.

Este módulo implementa:
1. Validación canaria obligatoria de usuarios existentes.
2. Interceptación estricta (mock/monkeypatch) de cualquier servicio de notificación
   (WhatsApp, Email, Notificaciones Push/In-App) para garantizar que NINGÚN
   mensaje llegue a casillas o teléfonos reales durante la regeneración.
3. Snapshot de seguridad de las cuentas de otros usuarios.
4. Borrado limpio, consistente y ordenado de los registros de testingadmin.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.calibracion_usuario import CalibracionUsuario
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta
from app.models.perfil_financiero import PerfilFinanciero
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import Presupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.saldo_arrastrado import PagoSaldoArrastrado, SaldoArrastradoTarjeta
from app.models.suscripcion import Suscripcion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import Usuario

logger = logging.getLogger("testingadmin.seguridad")

TESTINGADMIN_EMAIL = "testingadmin@argentum.com"
TESTINGADMIN_ID = "4c2ed62e-c22e-4d21-9bf9-ec6705f2c6fa"

CANARIO_MINIMO = [
    "albanopavia@gmail.com",
    "angieperiolo@hotmail.com",
    "benitezsantiago2001@gmail.com",
    "giordaninosebas@gmail.com",
    "mrm291201@gmail.com",
    "orlandodjsegovia@gmail.com",
    "testingadmin@argentum.com",
]

# Registro global de intercepciones de mensajería
_INTERCEPTACIONES: List[Dict[str, Any]] = []


def verificar_canario_y_usuario(db: Session) -> Usuario:
    """
    Verifica que todos los usuarios del canario estén presentes en la base de datos
    y que testingadmin@argentum.com posea el ID canónico establecido.
    Si algo no coincide, aborta la ejecución con excepción.
    """
    emails_db = {u.email for u in db.query(Usuario.email).all()}
    faltantes = [e for e in CANARIO_MINIMO if e not in emails_db]
    if faltantes:
        raise RuntimeError(
            f"ABORT CRÍTICO DE SEGURIDAD: Faltan usuarios del canario en la base de datos: {faltantes}"
        )

    user = db.query(Usuario).filter(Usuario.email == TESTINGADMIN_EMAIL).first()
    if not user:
        raise RuntimeError(f"ABORT CRÍTICO: Usuario {TESTINGADMIN_EMAIL} no encontrado.")
    if str(user.id) != TESTINGADMIN_ID:
        raise RuntimeError(
            f"ABORT CRÍTICO: ID de testingadmin no coincide: {user.id} vs {TESTINGADMIN_ID}"
        )

    return user


def instalar_arnes_interceptores():
    """
    Reemplaza todas las funciones que envían WhatsApp, email o crean notificaciones
    por funciones seguras en memoria que registran cada llamada y no emiten
    ningún tráfico de red externo.
    """
    global _INTERCEPTACIONES
    _INTERCEPTACIONES = []

    # 1. WhatsApp Service
    from app.services import whatsapp_service

    def _mock_enviar_whatsapp(telefono: str, mensaje: str) -> bool:
        _INTERCEPTACIONES.append({
            "canal": "whatsapp",
            "destino": telefono,
            "mensaje": mensaje,
        })
        return True

    whatsapp_service.enviar_mensaje_whatsapp = _mock_enviar_whatsapp

    # 2. Notificacion WhatsApp Service
    from app.services import notificacion_whatsapp_service
    notificacion_whatsapp_service.enviar_whatsapp_notificacion = _mock_enviar_whatsapp

    # 3. Email Service
    from app.services import email_service

    def _mock_enviar_email(destinatario: str, asunto: str, html_contenido: str, tag: str = "general") -> bool:
        _INTERCEPTACIONES.append({
            "canal": "email",
            "destino": destinatario,
            "asunto": asunto,
            "tag": tag,
        })
        return True

    email_service._enviar_email = _mock_enviar_email

    # 4. Notificacion Email Service
    from app.services import notificacion_email_service

    def _mock_enviar_email_notif(destinatario: str, asunto: str, mensaje: str, template: str = "notificacion_general") -> bool:
        _INTERCEPTACIONES.append({
            "canal": "email_notificacion",
            "destino": destinatario,
            "asunto": asunto,
            "mensaje": mensaje,
            "template": template,
        })
        return True

    notificacion_email_service.enviar_email_notificacion = _mock_enviar_email_notif

    # 5. Notificacion Service (crear_notificacion in-app / push)
    from app.services import notificacion_service

    def _mock_crear_notificacion(db: Session, usuario_id: UUID, tipo: Any, nivel: Any, mensaje: str, **kwargs) -> Any:
        _INTERCEPTACIONES.append({
            "canal": "notificacion_inapp",
            "usuario_id": str(usuario_id),
            "tipo": str(getattr(tipo, "value", tipo)),
            "nivel": str(getattr(nivel, "value", nivel)),
            "mensaje": mensaje,
        })
        return None

    notificacion_service.crear_notificacion = _mock_crear_notificacion


def obtener_interceptaciones() -> List[Dict[str, Any]]:
    """Devuelve la lista completa de mensajes y notificaciones interceptadas."""
    return list(_INTERCEPTACIONES)


def snapshot_cuentas_ajenas(db: Session) -> Dict[str, int]:
    """
    Toma un inventario de filas de todas las tablas para todos los usuarios
    distintos de testingadmin, para verificar que no sufran ninguna alteración.
    """
    otros_uids = [
        u.id for u in db.query(Usuario.id).filter(Usuario.id != TESTINGADMIN_ID).all()
    ]
    if not otros_uids:
        return {}

    snap = {}
    snap["transacciones"] = db.query(Transaccion).filter(Transaccion.usuario_id.in_(otros_uids)).count()
    snap["transferencias"] = db.query(TransferenciaInterna).filter(TransferenciaInterna.usuario_id.in_(otros_uids)).count()
    snap["grupos_cuotas"] = db.query(GrupoCuotas).filter(GrupoCuotas.usuario_id.in_(otros_uids)).count()
    snap["cuotas"] = (
        db.query(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .filter(GrupoCuotas.usuario_id.in_(otros_uids))
        .count()
    )
    snap["metas"] = db.query(Meta).filter(Meta.usuario_id.in_(otros_uids)).count()
    snap["movimientos_meta"] = (
        db.query(MovimientoMeta)
        .join(Meta, MovimientoMeta.meta_id == Meta.id)
        .filter(Meta.usuario_id.in_(otros_uids))
        .count()
    )
    snap["presupuestos"] = db.query(Presupuesto).filter(Presupuesto.usuario_id.in_(otros_uids)).count()
    snap["presupuestos_categorias"] = (
        db.query(PresupuestoCategoria)
        .join(Presupuesto, PresupuestoCategoria.presupuesto_id == Presupuesto.id)
        .filter(Presupuesto.usuario_id.in_(otros_uids))
        .count()
    )
    snap["periodos_presupuesto"] = (
        db.query(PeriodoPresupuesto)
        .join(Presupuesto, PeriodoPresupuesto.presupuesto_id == Presupuesto.id)
        .filter(Presupuesto.usuario_id.in_(otros_uids))
        .count()
    )
    snap["suscripciones"] = db.query(Suscripcion).filter(Suscripcion.usuario_id.in_(otros_uids)).count()
    snap["historial_suscripciones"] = (
        db.query(HistorialSuscripcion)
        .join(Suscripcion, HistorialSuscripcion.suscripcion_id == Suscripcion.id)
        .filter(Suscripcion.usuario_id.in_(otros_uids))
        .count()
    )
    snap["billeteras"] = db.query(Billetera).filter(Billetera.usuario_id.in_(otros_uids)).count()
    snap["calibraciones"] = db.query(CalibracionUsuario).filter(CalibracionUsuario.usuario_id.in_(otros_uids)).count()
    snap["perfiles"] = db.query(PerfilFinanciero).filter(PerfilFinanciero.usuario_id.in_(otros_uids)).count()

    tarjetas_ajenas = [t.id for t in db.query(TarjetaCredito.id).filter(TarjetaCredito.usuario_id.in_(otros_uids)).all()]
    if tarjetas_ajenas:
        snap["saldos_arrastrados"] = db.query(SaldoArrastradoTarjeta).filter(SaldoArrastradoTarjeta.tarjeta_id.in_(tarjetas_ajenas)).count()
        snap["pagos_saldo_arrastrado"] = (
            db.query(PagoSaldoArrastrado)
            .join(SaldoArrastradoTarjeta, PagoSaldoArrastrado.saldo_arrastrado_id == SaldoArrastradoTarjeta.id)
            .filter(SaldoArrastradoTarjeta.tarjeta_id.in_(tarjetas_ajenas))
            .count()
        )
    else:
        snap["saldos_arrastrados"] = 0
        snap["pagos_saldo_arrastrado"] = 0

    return snap


def ejecutar_borrado_testingadmin(db: Session, user: Usuario) -> Dict[str, int]:
    """
    Elimina ordenadamente todos los datos dependientes de testingadmin,
    respetando restricciones de integridad referencial y reseteando saldos.
    """
    conteos: Dict[str, int] = {}
    uid = user.id

    # 1. Desvincular llaves foráneas circulares en transacciones
    db.execute(
        text("""
            UPDATE transacciones 
            SET grupo_cuotas_id = NULL, 
                movimiento_meta_id = NULL, 
                suscripcion_id = NULL,
                pago_origen_id = NULL
            WHERE usuario_id = :uid
        """),
        {"uid": uid},
    )
    db.flush()

    # 2. Pagos de saldo arrastrado y saldos arrastrados de tarjetas
    tarjetas_user = [t.id for t in db.query(TarjetaCredito.id).filter(TarjetaCredito.usuario_id == uid).all()]
    cant_psa = 0
    cant_sa = 0
    if tarjetas_user:
        sa_ids = [s.id for s in db.query(SaldoArrastradoTarjeta.id).filter(SaldoArrastradoTarjeta.tarjeta_id.in_(tarjetas_user)).all()]
        if sa_ids:
            cant_psa = db.query(PagoSaldoArrastrado).filter(PagoSaldoArrastrado.saldo_arrastrado_id.in_(sa_ids)).delete(synchronize_session=False)
            db.flush()
        cant_sa = db.query(SaldoArrastradoTarjeta).filter(SaldoArrastradoTarjeta.tarjeta_id.in_(tarjetas_user)).delete(synchronize_session=False)
        db.flush()
    conteos["pagos_saldo_arrastrado"] = cant_psa
    conteos["saldos_arrastrados_tarjeta"] = cant_sa

    # 3. Cuotas y Grupos de cuotas
    gc_ids = [g.id for g in db.query(GrupoCuotas.id).filter(GrupoCuotas.usuario_id == uid).all()]
    cant_c = 0
    if gc_ids:
        cant_c = db.query(Cuota).filter(Cuota.grupo_id.in_(gc_ids)).delete(synchronize_session=False)
        db.flush()
    cant_gc = db.query(GrupoCuotas).filter(GrupoCuotas.usuario_id == uid).delete(synchronize_session=False)
    db.flush()
    conteos["cuotas"] = cant_c
    conteos["grupos_cuotas"] = cant_gc

    # 4. Transacciones
    cant_tx = db.query(Transaccion).filter(Transaccion.usuario_id == uid).delete(synchronize_session=False)
    db.flush()
    conteos["transacciones"] = cant_tx

    # 5. Movimientos de Meta y Metas
    meta_ids = [m.id for m in db.query(Meta.id).filter(Meta.usuario_id == uid).all()]
    cant_mm = 0
    if meta_ids:
        cant_mm = db.query(MovimientoMeta).filter(MovimientoMeta.meta_id.in_(meta_ids)).delete(synchronize_session=False)
        db.flush()
    cant_m = db.query(Meta).filter(Meta.usuario_id == uid).delete(synchronize_session=False)
    db.flush()
    conteos["movimientos_meta"] = cant_mm
    conteos["metas"] = cant_m

    # 6. Presupuestos, Periodos y Categorias
    p_ids = [p.id for p in db.query(Presupuesto.id).filter(Presupuesto.usuario_id == uid).all()]
    cant_pp = 0
    cant_pc = 0
    if p_ids:
        cant_pp = db.query(PeriodoPresupuesto).filter(PeriodoPresupuesto.presupuesto_id.in_(p_ids)).delete(synchronize_session=False)
        cant_pc = db.query(PresupuestoCategoria).filter(PresupuestoCategoria.presupuesto_id.in_(p_ids)).delete(synchronize_session=False)
        db.flush()
    cant_p = db.query(Presupuesto).filter(Presupuesto.usuario_id == uid).delete(synchronize_session=False)
    db.flush()
    conteos["periodos_presupuesto"] = cant_pp
    conteos["presupuestos_categorias"] = cant_pc
    conteos["presupuestos"] = cant_p

    # 7. Suscripciones e Historial
    sub_ids = [s.id for s in db.query(Suscripcion.id).filter(Suscripcion.usuario_id == uid).all()]
    cant_hs = 0
    if sub_ids:
        cant_hs = db.query(HistorialSuscripcion).filter(HistorialSuscripcion.suscripcion_id.in_(sub_ids)).delete(synchronize_session=False)
        db.flush()
    cant_s = db.query(Suscripcion).filter(Suscripcion.usuario_id == uid).delete(synchronize_session=False)
    db.flush()
    conteos["historial_suscripciones"] = cant_hs
    conteos["suscripciones"] = cant_s

    # 8. Transferencias internas
    cant_tr = db.query(TransferenciaInterna).filter(TransferenciaInterna.usuario_id == uid).delete(synchronize_session=False)
    db.flush()
    conteos["transferencias_internas"] = cant_tr

    # 9. Rendimientos de billetera
    billeteras_user = [b.id for b in db.query(Billetera.id).filter(Billetera.usuario_id == uid).all()]
    cant_rend = 0
    if billeteras_user:
        cant_rend = db.query(RendimientoBilletera).filter(RendimientoBilletera.billetera_id.in_(billeteras_user)).delete(synchronize_session=False)
        db.flush()
    conteos["rendimientos_billetera"] = cant_rend

    # 10. Calibraciones y Perfil Financiero
    cant_cal = db.query(CalibracionUsuario).filter(CalibracionUsuario.usuario_id == uid).delete(synchronize_session=False)
    cant_perf = db.query(PerfilFinanciero).filter(PerfilFinanciero.usuario_id == uid).delete(synchronize_session=False)
    db.flush()
    conteos["calibraciones_usuario"] = cant_cal
    conteos["perfiles_financieros"] = cant_perf

    # 11. Resetear saldos de billeteras a 0.00
    billeteras = db.query(Billetera).filter(Billetera.usuario_id == uid).all()
    for b in billeteras:
        b.saldo_actual = Decimal("0.00")
        b.saldo_inicial = Decimal("0.00")
    db.commit()

    return conteos
