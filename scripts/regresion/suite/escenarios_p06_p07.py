from __future__ import annotations

import time
import uuid
import re
import logging
import threading
from decimal import Decimal
from datetime import datetime, date, timezone, timedelta
from unittest.mock import patch

from sqlalchemy import select, text, func
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transferencia_interna import TransferenciaInterna
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
    MetodoPago,
)
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.schemas.suscripcion import SuscripcionCreate
from app.services import suscripcion_service, ai_service
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from app.routers.whatsapp.propuestas import _construir_propuesta_transaccion
from app.routers.whatsapp.registro import _confirmar_propuesta_transaccion
from app.routers.whatsapp.db_lookups import _resolver_categoria_y_subcategoria
from app.utils.fecha import hoy_argentina

from scripts.regresion.suite.comun import (
    USUARIO_PRUEBAS_EMAIL,
    TELEFONO_TEST,
    make_payload,
    run_isolated,
    _normalizar_accion_ejecutada,
)


def p6_ejecutar_caso(datos, nombre_caso, ent, forzar_cero: bool = False):
    def test(conn, Session, respuestas):
        db = Session()
        u = db.execute(select(Usuario).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)).scalar_one()

        b_nom = ent.get("billetera_destino") if ent.get("tipo") == "ingreso" else ent.get("billetera_origen")
        if forzar_cero and b_nom:
            conn.execute(text("UPDATE billeteras SET saldo_actual = 0 WHERE usuario_id = :uid AND nombre = :bnom"), {"uid": u.id, "bnom": b_nom})
            db.expire_all()

        b_obj = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == b_nom)).scalars().first()
        b_mon = b_obj.moneda if b_obj else (Moneda.USD if "USD" in (b_nom or "") else Moneda.ARS)

        entidades_caso = dict(ent)
        if "transacciones_adicionales" in ent:
            entidades_caso["transacciones_adicionales"] = [dict(x) for x in ent["transacciones_adicionales"]]

        propuesta_texto = _construir_propuesta_transaccion(entidades_caso, b_nom, se_asumio_principal=False, billetera_moneda=b_mon)

        if "No se puede registrar ningún movimiento." in propuesta_texto:
            return f"Propuesta:\n{propuesta_texto}\nConfirmación:\nNO_APLICA"

        pid = uuid.uuid4()
        conv = ConversacionWpp(
            id=pid,
            usuario_id=u.id,
            wamid=f"sim_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="simulacion",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot=propuesta_texto,
            intent_detectado="registrar_transaccion",
            entidades=entidades_caso,
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc),
        )
        db.add(conv)
        db.commit()

        tx, msg_confirm, ya_conf = _confirmar_propuesta_transaccion(u, db, propuesta_id=pid)
        return f"Propuesta:\n{propuesta_texto}\nConfirmación:\n{msg_confirm}"

    return run_isolated(test)


def p7_ejecutar_caso(datos, mensaje, cat_esperada, desc_esperada):
    def test(conn, Session, respuestas):
        db = Session()
        u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
        res = ai_service.procesar_mensaje(mensaje, u, db)
        cat_obtenida = res.get("entidades", {}).get("categoria")
        desc_obtenida = res.get("entidades", {}).get("descripcion")

        cat_id, sub_id = _resolver_categoria_y_subcategoria(cat_obtenida, u.id, db, tipo="egreso")
        if not cat_id:
            return f"Error: no se pudo resolver categoría {cat_obtenida}"

        return f"Cat: {cat_obtenida} | Desc: {desc_obtenida}"
    return run_isolated(test)
