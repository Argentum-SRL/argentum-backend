"""
Servicio oficial de gestión de reintegros de Argentum.
Ubicación: app/services/reintegro_service.py

Reglas de negocio (Fase 5C):
D1. Un reintegro es una compra al revés. Al llegar, se crea una transacción espejo
    de tipo EGRESO con monto NEGATIVO (-reintegro), misma categoría, subcategoría,
    billetera, metodo_pago y tarjeta.
D4. Sin crédito: mueve el saldo reutilizando _afecta_saldo y aplicar_impacto_saldo.
D5. Con crédito: padre + GrupoCuotas de 1 cuota + cuota hija NEGATIVA al resumen correspondiente.
D6. Validaciones estrictas y borrado de espejos al revertir o eliminar.
D10. Aviso a los 30 días para compras pendientes de reintegro.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.notificacion import NivelNotificacion, Notificacion, TipoNotificacion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.services import calendario_tarjeta_service
from app.services.notificacion_service import crear_notificacion
from app.services.saldo_billetera_service import aplicar_impacto_saldo
from app.services.transaccion_service import (
    _afecta_saldo,
    _queda_pendiente_por_fecha,
    _validar_moneda_coincide,
    eliminar_transaccion,
    obtener_transaccion,
)
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto


def tiene_pago_resumen_confirmado(db: Session, tarjeta_id: UUID, vencimiento: date) -> bool:
    """Verifica si un resumen ya cuenta con un pago confirmado registrado."""
    stmt = select(Transaccion.id).where(
        Transaccion.tarjeta_id == tarjeta_id,
        Transaccion.pago_resumen_vencimiento == vencimiento,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        or_(
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.estado_verificacion.is_(None),
        ),
    )
    return db.execute(stmt).first() is not None


def _archivar_notificacion_reintegro(db: Session, usuario_id: UUID, transaccion_id: UUID) -> None:
    """Archiva cualquier notificación pendiente vinculada a esta compra."""
    grupo = f"REINTEGRO_PENDIENTE_{transaccion_id}"
    notifs = db.execute(
        select(Notificacion).where(
            Notificacion.usuario_id == usuario_id,
            Notificacion.grupo_agrupacion == grupo,
        )
    ).scalars().all()
    for n in notifs:
        n.archivada = True


def _validar_compra_para_reintegro(transaccion: Transaccion, monto: Decimal) -> None:
    """Valida los requisitos previos para registrar o modificar un reintegro."""
    if monto <= Decimal("0"):
        raise HTTPException(status_code=400, detail="El reintegro tiene que ser mayor a cero.")

    # Solo un gasto puede tener reintegro (excluye ingresos, pagos de resumen, metas, espejos, cuotas hijas)
    if (
        transaccion.tipo != TipoTransaccion.EGRESO
        or transaccion.pago_resumen_vencimiento is not None
        or transaccion.movimiento_meta_id is not None
        or transaccion.reintegro_de_id is not None
        or transaccion.es_cuota_hija
    ):
        raise HTTPException(status_code=400, detail="Solo un gasto puede tener reintegro.")

    # Contra el monto total de la compra menos partes vinculadas; en cuotas, el del padre
    suma_partes = Decimal("0")
    if getattr(transaccion, "espejos_reintegro", None):
        for e in transaccion.espejos_reintegro:
            if getattr(e, "tipo_espejo", None) == "parte" and not getattr(e, "es_cuota_hija", False):
                suma_partes += abs(Decimal(str(e.monto)))
    monto_disponible = transaccion.monto - suma_partes
    if monto > monto_disponible:
        raise HTTPException(status_code=400, detail="El reintegro no puede ser mayor que la compra.")


def registrar_o_actualizar_reintegro(
    db: Session,
    usuario_id: UUID,
    transaccion_id: UUID,
    monto: Decimal,
    recibido: bool,
    fecha_recibido: Optional[date] = None,
    commit: bool = True,
) -> Transaccion:
    """
    Registra o actualiza el reintegro de una transacción de gasto.
    Si recibido es True, crea el espejo correspondiente; si es False, borra espejos existentes.
    """
    compra = obtener_transaccion(db, usuario_id, transaccion_id)
    _validar_compra_para_reintegro(compra, monto)

    # 1. Borrar espejo anterior si ya existía (solo espejos tipo 'reintegro')
    espejos_existentes = db.execute(
        select(Transaccion.id).where(
            Transaccion.reintegro_de_id == compra.id,
            or_(Transaccion.tipo_espejo == "reintegro", Transaccion.tipo_espejo.is_(None)),
        )
    ).scalars().all()
    for esp_id in espejos_existentes:
        eliminar_transaccion(db, usuario_id, esp_id, commit=False)

    # 2. Guardar reintegro esperado
    compra.reintegro_esperado = monto

    # 3. Si recibido, crear el espejo nuevo
    if recibido:
        f_llegada = fecha_recibido or hoy_argentina()

        if compra.metodo_pago == MetodoPago.CREDITO and compra.tarjeta_id:
            # D5. Con crédito: padre + GrupoCuotas de 1 cuota + cuota hija NEGATIVA
            tarjeta = db.get(TarjetaCredito, compra.tarjeta_id)
            cierre, vencimiento = calendario_tarjeta_service.resumen_de_compra(tarjeta, f_llegada)
            while tiene_pago_resumen_confirmado(db, tarjeta.id, vencimiento):
                cierre, vencimiento = calendario_tarjeta_service.resumen_de_compra(
                    tarjeta, cierre + timedelta(days=1)
                )

            espejo_padre = Transaccion(
                usuario_id=usuario_id,
                tipo=TipoTransaccion.EGRESO,
                monto=-monto,
                moneda=compra.moneda,
                fecha=f_llegada,
                descripcion=f"Reintegro: {compra.descripcion}".strip(),
                categoria_id=compra.categoria_id,
                subcategoria_id=compra.subcategoria_id,
                metodo_pago=MetodoPago.CREDITO,
                billetera_id=compra.billetera_id,
                tarjeta_id=compra.tarjeta_id,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                es_padre_cuotas=True,
                es_cuota_hija=False,
                reintegro_de_id=compra.id,
                tipo_espejo="reintegro",
            )
            espejo_padre.saldo_aplicado = not _queda_pendiente_por_fecha(espejo_padre)
            db.add(espejo_padre)
            db.flush()

            grupo = GrupoCuotas(
                usuario_id=usuario_id,
                transaccion_padre_id=espejo_padre.id,
                tarjeta_id=compra.tarjeta_id,
                descripcion=espejo_padre.descripcion,
                monto_total=-monto,
                cantidad_cuotas=1,
                tiene_interes=False,
                tasa_interes=None,
                total_financiado=-monto,
                moneda=compra.moneda,
                primer_vencimiento=vencimiento,
            )
            db.add(grupo)
            db.flush()

            espejo_padre.grupo_cuotas_id = grupo.id

            hija = Transaccion(
                usuario_id=usuario_id,
                tipo=TipoTransaccion.EGRESO,
                monto=-monto,
                moneda=compra.moneda,
                fecha=vencimiento,
                descripcion=espejo_padre.descripcion,
                categoria_id=compra.categoria_id,
                subcategoria_id=compra.subcategoria_id,
                metodo_pago=MetodoPago.CREDITO,
                billetera_id=compra.billetera_id,
                tarjeta_id=compra.tarjeta_id,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.PENDIENTE,
                es_cuota_hija=True,
                es_padre_cuotas=False,
                grupo_cuotas_id=grupo.id,
                reintegro_de_id=compra.id,
                tipo_espejo="reintegro",
            )
            hija.saldo_aplicado = not _queda_pendiente_por_fecha(hija)
            db.add(hija)
            db.flush()

            cuota_reg = Cuota(
                grupo_id=grupo.id,
                transaccion_id=hija.id,
                numero_cuota=1,
                monto_proyectado=-monto,
                fecha_vencimiento=vencimiento,
                pagada=False,
            )
            db.add(cuota_reg)
            db.flush()

        else:
            # D4. Sin crédito: débito, efectivo, transferencia, etc.
            espejo = Transaccion(
                usuario_id=usuario_id,
                tipo=TipoTransaccion.EGRESO,
                monto=-monto,
                moneda=compra.moneda,
                fecha=f_llegada,
                descripcion=f"Reintegro: {compra.descripcion}".strip(),
                categoria_id=compra.categoria_id,
                subcategoria_id=compra.subcategoria_id,
                metodo_pago=compra.metodo_pago,
                billetera_id=compra.billetera_id,
                tarjeta_id=compra.tarjeta_id,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                es_padre_cuotas=False,
                es_cuota_hija=False,
                reintegro_de_id=compra.id,
                tipo_espejo="reintegro",
            )
            espejo.saldo_aplicado = not _queda_pendiente_por_fecha(espejo)
            db.add(espejo)
            db.flush()

            if _afecta_saldo(espejo):
                billetera = db.get(Billetera, espejo.billetera_id)
                if billetera:
                    _validar_moneda_coincide(espejo.moneda, billetera)
                    impacto = espejo.monto if espejo.tipo == TipoTransaccion.INGRESO else -espejo.monto
                    aplicar_impacto_saldo(billetera, impacto, espejo.fecha)

            from app.services import presupuesto_service
            presupuesto_service.registrar_impacto_presupuesto(
                db, espejo, revertir=False, commit=False
            )

        # Si pasa a recibido, archivar notificación previa si existía
        _archivar_notificacion_reintegro(db, usuario_id, compra.id)

    if commit:
        db.commit()
        db.refresh(compra)
    else:
        db.flush()

    return compra


def eliminar_reintegro(
    db: Session,
    usuario_id: UUID,
    transaccion_id: UUID,
    commit: bool = True,
) -> Transaccion:
    """
    Quita el reintegro de una compra: elimina los espejos generados y limpia reintegro_esperado.
    """
    compra = obtener_transaccion(db, usuario_id, transaccion_id)
    if (
        compra.tipo != TipoTransaccion.EGRESO
        or compra.pago_resumen_vencimiento is not None
        or compra.movimiento_meta_id is not None
        or compra.reintegro_de_id is not None
        or compra.es_cuota_hija
    ):
        raise HTTPException(status_code=400, detail="Solo un gasto puede tener reintegro.")

    espejos_existentes = db.execute(
        select(Transaccion.id).where(
            Transaccion.reintegro_de_id == compra.id,
            or_(Transaccion.tipo_espejo == "reintegro", Transaccion.tipo_espejo.is_(None)),
        )
    ).scalars().all()
    for esp_id in espejos_existentes:
        eliminar_transaccion(db, usuario_id, esp_id, commit=False)

    compra.reintegro_esperado = None
    _archivar_notificacion_reintegro(db, usuario_id, compra.id)

    if commit:
        db.commit()
        db.refresh(compra)
    else:
        db.flush()

    return compra


def notificar_reintegros_pendientes(db: Session, hoy: date) -> int:
    """
    Aviso a los 30 días (D10):
    Para cada compra PENDIENTE con fecha <= hoy - 30 y sin notificación previa,
    crea una notificación web informativa.
    """
    fecha_limite = hoy - timedelta(days=30)
    compras = db.execute(
        select(Transaccion).where(
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.reintegro_esperado.isnot(None),
            Transaccion.reintegro_de_id.is_(None),
            Transaccion.fecha <= fecha_limite,
        )
    ).scalars().all()

    creadas = 0
    for c in compras:
        # Si ya tiene un espejo recibido (reintegro), no está pendiente
        tiene_espejo = db.execute(
            select(Transaccion.id).where(
                Transaccion.reintegro_de_id == c.id,
                or_(Transaccion.tipo_espejo == "reintegro", Transaccion.tipo_espejo.is_(None)),
            )
        ).first() is not None
        if tiene_espejo:
            continue

        grupo = f"REINTEGRO_PENDIENTE_{c.id}"
        notif_existente = db.execute(
            select(Notificacion.id).where(
                Notificacion.usuario_id == c.usuario_id,
                Notificacion.grupo_agrupacion == grupo,
            )
        ).first()
        if notif_existente:
            continue

        monto_str = formatear_monto(c.reintegro_esperado, c.moneda)
        mensaje = f"¿Te llegó el reintegro de {monto_str} de {c.descripcion}?"
        deep_link = f"/app/transacciones?transaccion={c.id}"

        crear_notificacion(
            db=db,
            usuario_id=c.usuario_id,
            tipo=TipoNotificacion.REINTEGRO_PENDIENTE,
            nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
            mensaje=mensaje,
            entidad_tipo="transaccion",
            entidad_id=c.id,
            deep_link=deep_link,
            canal_web=True,
            canal_whatsapp=False,
            canal_email=False,
            grupo_agrupacion_override=grupo,
            commit=False,
        )
        creadas += 1

    if creadas > 0:
        db.commit()

    return creadas
