"""Controles de la suite de regresión: conteos, saldos de referencia y reconciliación."""
from __future__ import annotations

import uuid
from decimal import Decimal
from sqlalchemy import select, text, func
from sqlalchemy.orm import Session, sessionmaker
from app.core.database import engine

from app.models.usuario import Usuario
from app.models.billetera import Billetera
from app.models.transaccion import Transaccion
from app.models.conversacion_wpp import ConversacionWpp
from app.models.transferencia_interna import TransferenciaInterna
from app.models.movimiento_meta import MovimientoMeta
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado

from scripts.regresion.suite.comun import USUARIO_PRUEBAS_EMAIL


def obtener_conteos_base(db: Session, usuario_id: uuid.UUID | None = None):
    from app.models.meta import Meta
    from app.models.movimiento_meta import MovimientoMeta
    from app.models.transferencia_interna import TransferenciaInterna
    if usuario_id is None:
        usuario_id = db.execute(select(Usuario.id).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)).scalar()
    tx_cnt = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == usuario_id)).scalar()
    conv_cnt = db.execute(select(func.count(ConversacionWpp.id)).where(ConversacionWpp.usuario_id == usuario_id)).scalar()
    tr_cnt = db.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == usuario_id)).scalar()
    mm_cnt = db.execute(select(func.count(MovimientoMeta.id)).join(Meta, MovimientoMeta.meta_id == Meta.id).where(Meta.usuario_id == usuario_id)).scalar()
    msg_cnt = db.execute(select(text("count(*)")).select_from(text("mensajes_whatsapp_procesados")).where(text("wamid LIKE 'wamid_reg_%'"))).scalar()
    saldos = {
        str(b.id): b.saldo_actual
        for b in db.execute(select(Billetera).where(Billetera.usuario_id == usuario_id).order_by(Billetera.id)).scalars().all()
    }
    return {"tx": tx_cnt, "conv": conv_cnt, "tr": tr_cnt, "mm": mm_cnt, "msg": msg_cnt, "saldos": saldos}


def obtener_saldos_21(db: Session):
    return {
        (email, b.nombre, b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)): b.saldo_actual
        for b, email in db.execute(
            select(Billetera, Usuario.email)
            .join(Usuario, Billetera.usuario_id == Usuario.id)
            .order_by(Usuario.email, Billetera.nombre)
        ).all()
    }


DIFERENCIAS_RECONCILIACION_BASELINE = {
    ("mrm291201@gmail.com", "Galicia"): Decimal("-941.00"),
}


def verificar_reconciliacion_billeteras(db: Session = None):
    """
    Compara el saldo_actual guardado de cada billetera contra el saldo teórico calculado
    mediante la función oficial calcular_saldo_teorico (app.services.conciliacion_service).
    Compara contra el baseline conocido de diferencias históricas (-$941 en Galicia de mrm291201@gmail.com)
    y reporta discrepancias solo si surge una diferencia NUEVA o cambia una existente.
    """
    cerrar_db = False
    if db is None:
        SessionRR = sessionmaker(
            bind=engine.execution_options(isolation_level="REPEATABLE READ"),
            autocommit=False,
            autoflush=False,
        )
        db = SessionRR()
        cerrar_db = True
    try:
        from app.models.billetera import Billetera
        from app.models.usuario import Usuario
        from app.services.conciliacion_service import calcular_saldo_teorico
        from app.utils.fecha import hoy_argentina
        
        hoy = hoy_argentina()
        billeteras = db.execute(
            select(Billetera, Usuario.email)
            .join(Usuario, Billetera.usuario_id == Usuario.id)
            .order_by(Usuario.email, Billetera.nombre)
        ).all()
        
        discrepancias_no_esperadas = []
        detalles = []
        
        for b, email in billeteras:
            s_guardado = b.saldo_actual
            s_calc = calcular_saldo_teorico(db, b.id, hasta=hoy)
            diff = s_guardado - s_calc
            esperado_diff = DIFERENCIAS_RECONCILIACION_BASELINE.get((email, b.nombre), Decimal("0.00"))
            coincide_con_baseline = (diff == esperado_diff)
            
            item = {
                "email": email,
                "billetera": b.nombre,
                "guardado": s_guardado,
                "calculado": s_calc,
                "diferencia": diff,
                "esperado_diff": esperado_diff,
                "ok": coincide_con_baseline
            }
            detalles.append(item)
            if not coincide_con_baseline:
                discrepancias_no_esperadas.append(item)
                
        return len(discrepancias_no_esperadas) == 0, discrepancias_no_esperadas, detalles
    finally:
        if cerrar_db:
            db.close()


SALDOS_REFERENCIA_21 = {
    ("testingadmin@argentum.com", "Ahorro con rendimiento", "ARS"): Decimal("2082358.73"),
    ("testingadmin@argentum.com", "Efectivo Pesos", "ARS"): Decimal("566340.50"),
    ("testingadmin@argentum.com", "Efectivo Dólares", "USD"): Decimal("388.00"),
    ("testingadmin@argentum.com", "Galicia", "ARS"): Decimal("3554871.05"),
    ("testingadmin@argentum.com", "Santander", "ARS"): Decimal("404460.37"),
}


def verificar_saldos_contra_referencia(db: Session, saldos_inicio_21: dict):
    """
    Compara testingadmin contra referencias fijas y recopila variaciones
    en las otras cuentas respecto a la foto tomada al inicio de la suite.
    """
    from app.models.billetera import Billetera
    from app.models.usuario import Usuario

    billeteras = db.execute(
        select(Billetera, Usuario.email)
        .join(Usuario, Billetera.usuario_id == Usuario.id)
        .order_by(Usuario.email, Billetera.nombre)
    ).all()

    desvios = []
    detalles = []
    for b, email in billeteras:
        actual = b.saldo_actual
        moneda = b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)
        clave = (email, b.nombre, moneda)
        ref = SALDOS_REFERENCIA_21.get(clave) or SALDOS_REFERENCIA_21.get((email, b.nombre))
        saldo_inicial = saldos_inicio_21.get(clave)
        es_admin = (email == USUARIO_PRUEBAS_EMAIL)
        esperado = ref if es_admin else saldo_inicial
        diff = actual - esperado if esperado is not None else None
        item = {
            "email": email,
            "billetera": b.nombre,
            "moneda": moneda,
            "actual": actual,
            "referencia": ref,
            "saldo_inicial": saldo_inicial,
            "diff": diff
        }
        detalles.append(item)
        if es_admin and (esperado is None or actual != esperado):
            desvios.append(item)
    return len(desvios) == 0, desvios, detalles
