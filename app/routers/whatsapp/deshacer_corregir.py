"""
app/routers/whatsapp/deshacer_corregir.py — Funciones para deshacer y corregir transacciones y movimientos por WhatsApp.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import structlog
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.conversacion_wpp import ConversacionWpp
from app.models.grupo_cuotas import GrupoCuotas
from app.models.movimiento_meta import MovimientoMeta
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import MetodoPago, TipoTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.constantes import PREFIJOS_CORRECCION, logger
from app.routers.whatsapp.db_lookups import (
    PLAZO_DESHACER_CORREGIR_MINUTOS,
    _obtener_billeteras_activas,
    _resolver_categoria_y_subcategoria,
)
from app.routers.whatsapp.detectors import (
    _es_cancelacion,
    _es_confirmacion,
    _es_pedido_deshacer,
    _es_saludo,
)
from app.core.reglas_texto_wpp import fecha_corta_wpp, nombre_tarjeta_wpp
from app.routers.whatsapp.parsers import (
    _extraer_frecuencia_mencionada,
    _extraer_nombre_servicio,
    _nombre_corto_categoria,
    _parsear_fecha_texto,
    _parsear_monto_argentino,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.resolvers_cascada import (
    ALIAS_BILLETERAS,
    _generar_menu_billeteras,
    resolver_billetera_cascada,
)
from app.schemas.transaccion import TransaccionUpdate
from app.services import (
    meta_service,
    transaccion_service,
    transferencia_service,
    whatsapp_service,
)
from app.services.evento_service import emitir_evento_actualizacion
from app.services.transaccion_service import (
    actualizar_transaccion,
    eliminar_transaccion,
)
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto



def _evaluar_correccion_billetera(
    mensaje: str,
    usuario_id: UUID,
    propuesta: ConversacionWpp,
    db: Session,
) -> tuple[bool, Billetera | None, list[Billetera], str | None]:
    """
    Detecta determinísticamente si el mensaje del usuario busca corregir la billetera
    de una propuesta pendiente (Tarea 7). No llama a IA (7.3).
    Retorna: (es_correccion, billetera_resuelta, candidatas_ambiguas, mensaje_error_moneda).
    """
    if not mensaje or not propuesta or not propuesta.entidades:
        return False, None, [], None

    norm_msg = normalizar_texto(mensaje)
    if not norm_msg:
        return False, None, [], None

    # Excluir confirmaciones o cancelaciones directas
    if norm_msg in ("si", "dale", "ok", "confirmo", "confirmar", "va", "listo", "de una", "correcto", "perfecto", "seh", "sip", "yes"):
        return False, None, [], None
    if norm_msg in ("cancelar", "cancela", "cancelalo", "no", "no importa", "deja", "olvidalo", "borrar"):
        return False, None, [], None

    entidades = propuesta.entidades
    tipo = entidades.get("tipo", "egreso")
    moneda_prop = Moneda.USD if entidades.get("moneda") == "USD" else Moneda.ARS
    todas_billeteras = _obtener_billeteras_activas(usuario_id, db)

    # 1. Probar limpiando prefijos de conversación común ("no, fue en...", "con...", etc.)
    texto_limpio = mensaje.strip()
    for p in PREFIJOS_CORRECCION:
        texto_limpio = re.sub(p, "", texto_limpio, flags=re.IGNORECASE).strip()

    b_match, cands = resolver_billetera_cascada(texto_limpio, todas_billeteras)

    # 2. Si no hubo match por prefijo, buscar si alguna billetera o alias aparece en el texto
    if not b_match and not cands:
        for b in todas_billeteras:
            b_norm = normalizar_texto(b.nombre)
            if b_norm in norm_msg:
                cands.append(b)
            else:
                for alias_k, alias_v in ALIAS_BILLETERAS.items():
                    if alias_k in norm_msg.split() and (alias_v in b_norm or b_norm in alias_v):
                        if b not in cands:
                            cands.append(b)

    if b_match:
        cands = [b_match]

    if not cands:
        return False, None, [], None

    # Verificar monedas
    cands_moneda = [b for b in cands if b.moneda == moneda_prop]
    cands_otra_moneda = [b for b in cands if b.moneda != moneda_prop]

    if not cands_moneda and cands_otra_moneda:
        b_otra = cands_otra_moneda[0]
        nom_otra = "dólares" if b_otra.moneda == Moneda.USD else "pesos"
        nom_prop = "pesos" if moneda_prop == Moneda.ARS else "dólares"
        billeteras_validas = _obtener_billeteras_activas(usuario_id, db, moneda=moneda_prop)
        menu_validas = _generar_menu_billeteras(billeteras_validas, tipo=tipo)
        sust = "el ingreso" if tipo == "ingreso" else "el gasto"
        msg_error = (
            f"{b_otra.nombre} es en {nom_otra} y {sust} es en {nom_prop}. "
            f"Elegí otra:\n{menu_validas}"
        )
        return True, None, [], msg_error

    if len(cands_moneda) == 1:
        return True, cands_moneda[0], [], None
    elif len(cands_moneda) > 1:
        return True, None, cands_moneda, None

    return False, None, [], None


def _confirmar_propuesta_deshacer(
    usuario: Usuario,
    db: Session,
) -> tuple[Transaccion | None, str, bool]:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_DESHACER_CORREGIR_MINUTOS)
    conv_undo = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "deshacer",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .with_for_update()
    ).scalars().first()

    if not conv_undo:
        conv_ya = db.execute(
            select(ConversacionWpp)
            .where(
                ConversacionWpp.usuario_id == usuario.id,
                ConversacionWpp.intent_detectado == "deshacer",
                ConversacionWpp.accion_ejecutada.is_not(None),
                ConversacionWpp.fecha >= limite,
            )
            .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().first()
        if conv_ya and conv_ya.accion_ejecutada and str(conv_ya.accion_ejecutada).startswith("deshecho:"):
            return None, "Ya estaba borrado.", True
        return None, "No hay nada para borrar.", False

    entidades = conv_undo.entidades or {}
    lote_ids_raw = entidades.get("lote_ids")
    if lote_ids_raw and isinstance(lote_ids_raw, list):
        lote_uuids = [UUID(str(x)) for x in lote_ids_raw]
        txs = db.execute(
            select(Transaccion).where(
                Transaccion.id.in_(lote_uuids),
                Transaccion.usuario_id == usuario.id,
            ).with_for_update()
        ).scalars().all()
        if not txs:
            conv_undo.accion_ejecutada = f"deshecho:lote:{','.join(str(x) for x in lote_uuids)}"
            db.commit()
            return None, "No hay nada para borrar.", False
        cant_eliminados = len(txs)
        tipos_lote = {t.tipo for t in txs}
        if tipos_lote == {TipoTransaccion.EGRESO}:
            sust_l = "gasto" if cant_eliminados == 1 else "gastos"
        elif tipos_lote == {TipoTransaccion.INGRESO}:
            sust_l = "ingreso" if cant_eliminados == 1 else "ingresos"
        else:
            sust_l = "movimientos"
        try:
            for t in txs:
                eliminar_transaccion(db, usuario.id, t.id, commit=False)
            conv_undo.accion_ejecutada = f"deshecho:lote:{','.join(str(x) for x in lote_uuids)}"
            emitir_evento_actualizacion(db, usuario.id, "transacciones")
            emitir_evento_actualizacion(db, usuario.id, "billeteras")
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Error al borrar lote de transacciones: {e}")
            return None, "Hubo un problema y no borré nada. Probá de nuevo en un rato.", False
        return txs[0], f"Listo, borré los {cant_eliminados} {sust_l}.", False

    tr_ids = entidades.get("transferencia_ids")
    if tr_ids:
        uuids = [UUID(str(i)) for i in tr_ids]
        trs = db.execute(
            select(TransferenciaInterna)
            .where(
                TransferenciaInterna.id.in_(uuids),
                TransferenciaInterna.usuario_id == usuario.id,
            )
            .with_for_update()
        ).scalars().all()
        ids_str = ",".join(str(i) for i in uuids)
        if not trs:
            conv_undo.accion_ejecutada = f"deshecho:transferencias:{ids_str}"
            db.commit()
            return None, "No hay nada para borrar.", False
        try:
            for tr in trs:
                transferencia_service.eliminar_transferencia(
                    db, usuario.id, tr.id, commit=False
                )
            conv_undo.accion_ejecutada = f"deshecho:transferencias:{ids_str}"
            emitir_evento_actualizacion(db, usuario.id, "transferencias")
            emitir_evento_actualizacion(db, usuario.id, "billeteras")
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Error al borrar transferencias {uuids}: {e}")
            return (
                None,
                "Hubo un problema y no borré nada. Probá de nuevo en un rato.",
                False,
            )
        return trs[0], f"Listo, borré los {len(trs)} pases.", False

    tr_id_str = entidades.get("transferencia_id")
    if tr_id_str:
        tr_id = UUID(str(tr_id_str))
        tr = db.execute(
            select(TransferenciaInterna)
            .where(TransferenciaInterna.id == tr_id, TransferenciaInterna.usuario_id == usuario.id)
            .with_for_update()
        ).scalars().first()

        if not tr:
            conv_undo.accion_ejecutada = f"deshecho:{tr_id}"
            db.commit()
            return None, "No hay nada para borrar.", False

        try:
            transferencia_service.eliminar_transferencia(db, usuario.id, tr.id, commit=False)
            conv_undo.accion_ejecutada = f"deshecho:{tr_id}"
            emitir_evento_actualizacion(db, usuario.id, "transferencias")
            emitir_evento_actualizacion(db, usuario.id, "billeteras")
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Error al borrar transferencia {tr_id}: {e}")
            return None, "Hubo un problema y no borré nada. Probá de nuevo en un rato.", False

        return tr, "Listo, lo borré.", False

    reintegro_compra_id_str = entidades.get("reintegro_compra_id")
    if reintegro_compra_id_str:
        compra_id = UUID(str(reintegro_compra_id_str))
        tx_id_str = entidades.get("transaccion_id")
        compra = db.execute(
            select(Transaccion)
            .where(Transaccion.id == compra_id, Transaccion.usuario_id == usuario.id)
            .with_for_update()
        ).scalar_one_or_none()
        espejo = None
        if compra:
            espejo = db.execute(
                select(Transaccion).where(
                    Transaccion.reintegro_de_id == compra.id,
                    Transaccion.tipo_espejo == "reintegro",
                    Transaccion.es_cuota_hija.is_(False),
                )
            ).scalars().first()

        if not compra or not espejo:
            conv_undo.accion_ejecutada = f"deshecho:{tx_id_str}"
            db.commit()
            return None, "No hay nada para borrar.", False

        previo = entidades.get("reintegro_esperado_previo")
        try:
            from app.services import reintegro_service
            if previo is None:
                reintegro_service.eliminar_reintegro(db, usuario.id, compra.id, commit=False)
            else:
                reintegro_service.registrar_o_actualizar_reintegro(
                    db, usuario.id, compra.id, Decimal(str(previo)), recibido=False, commit=False
                )
            conv_undo.accion_ejecutada = f"deshecho:{tx_id_str}"
            emitir_evento_actualizacion(db, usuario.id, "transacciones")
            emitir_evento_actualizacion(db, usuario.id, "billeteras")
            db.commit()
            return compra, "Listo, borré el reintegro.", False
        except Exception as e:
            db.rollback()
            logger.error(f"Error al borrar reintegro {compra_id}: {e}")
            return None, "Hubo un problema y no borré nada. Probá de nuevo en un rato.", False

    tx_id_str = entidades.get("transaccion_id")
    if not tx_id_str:
        return None, "No hay nada para borrar.", False

    tx_id = UUID(str(tx_id_str))
    tx = db.execute(
        select(Transaccion)
        .where(Transaccion.id == tx_id, Transaccion.usuario_id == usuario.id)
        .with_for_update()
    ).scalars().first()

    if not tx:
        conv_undo.accion_ejecutada = f"deshecho:{tx_id}"
        db.commit()
        return None, "No hay nada para borrar.", False

    if tx.movimiento_meta_id is not None:
        mov = db.get(MovimientoMeta, tx.movimiento_meta_id)
        if mov:
            try:
                meta_service.eliminar_movimiento(db, usuario.id, mov.meta_id, mov.id)
                conv_undo.accion_ejecutada = f"deshecho:{tx_id}"
                emitir_evento_actualizacion(db, usuario.id, "metas")
                emitir_evento_actualizacion(db, usuario.id, "transacciones")
                emitir_evento_actualizacion(db, usuario.id, "billeteras")
                db.commit()
                return tx, "Listo, lo borré.", False
            except Exception as e:
                db.rollback()
                logger.error(f"Error al borrar aporte a meta {tx_id}: {e}")
                return None, "Hubo un problema y no borré nada. Probá de nuevo en un rato.", False

    try:
        eliminar_transaccion(db, usuario.id, tx.id, commit=False)
        conv_undo.accion_ejecutada = f"deshecho:{tx_id}"
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error al borrar transacción {tx_id}: {e}")
        return None, "Hubo un problema y no borré nada. Probá de nuevo en un rato.", False

    return tx, "Listo, lo borré.", False


def _confirmar_propuesta_corregir(
    usuario: Usuario,
    db: Session,
) -> tuple[Transaccion | None, str, bool]:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_DESHACER_CORREGIR_MINUTOS)
    conv_corr = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "corregir",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .with_for_update()
    ).scalars().first()

    if not conv_corr:
        conv_ya = db.execute(
            select(ConversacionWpp)
            .where(
                ConversacionWpp.usuario_id == usuario.id,
                ConversacionWpp.intent_detectado == "corregir",
                ConversacionWpp.accion_ejecutada.is_not(None),
                ConversacionWpp.fecha >= limite,
            )
            .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().first()
        if conv_ya and conv_ya.accion_ejecutada and str(conv_ya.accion_ejecutada).startswith("corregido:"):
            return None, "Ya estaba cambiado.", True
        return None, "No tengo nada pendiente para confirmar.", False

    entidades = conv_corr.entidades or {}
    tx_id_str = entidades.get("transaccion_id")
    cambios = entidades.get("cambios") or {}
    if not tx_id_str or not cambios:
        return None, "No pude cambiar eso.", False

    tx_id = UUID(str(tx_id_str))
    tx = db.execute(
        select(Transaccion)
        .where(Transaccion.id == tx_id, Transaccion.usuario_id == usuario.id)
        .with_for_update()
    ).scalars().first()

    if not tx:
        conv_corr.accion_ejecutada = f"corregido:{tx_id}"
        db.commit()
        return None, "No hay nada para cambiar.", False

    kwargs_update = {}
    if "monto" in cambios and cambios["monto"] is not None:
        kwargs_update["monto"] = Decimal(str(cambios["monto"]))
    if "categoria_id" in cambios and cambios["categoria_id"] is not None:
        kwargs_update["categoria_id"] = UUID(str(cambios["categoria_id"]))
    if "subcategoria_id" in cambios and cambios["subcategoria_id"] is not None:
        kwargs_update["subcategoria_id"] = UUID(str(cambios["subcategoria_id"]))
    if "billetera_id" in cambios and cambios["billetera_id"] is not None:
        kwargs_update["billetera_id"] = UUID(str(cambios["billetera_id"]))
    if "fecha" in cambios and cambios["fecha"] is not None:
        kwargs_update["fecha"] = date.fromisoformat(str(cambios["fecha"]))

    update_payload = TransaccionUpdate(**kwargs_update)

    try:
        actualizar_transaccion(db, usuario.id, tx.id, update_payload)
    except HTTPException as e:
        db.rollback()
        conv_corr.accion_ejecutada = "error"
        db.commit()
        det = str(e.detail or "")
        if "reintegro" in det.lower():
            return (
                None,
                "Primero sacá el reintegro en miargentum.com y después cambiá la cuenta.",
                False,
            )
        return None, det, False

    conv_corr.accion_ejecutada = f"corregido:{tx_id}"
    emitir_evento_actualizacion(db, usuario.id, "transacciones")
    emitir_evento_actualizacion(db, usuario.id, "billeteras")
    db.commit()

    db.refresh(tx)
    b_res = db.get(Billetera, tx.billetera_id) if tx.billetera_id else None
    b_nom = b_res.nombre if b_res else "tu cuenta"
    c_res = db.get(Categoria, tx.categoria_id) if tx.categoria_id else None
    s_res = db.get(Subcategoria, tx.subcategoria_id) if tx.subcategoria_id else None
    cat_disp = s_res.nombre if s_res else (c_res.nombre if c_res else "Otros")
    m_fmt = formatear_monto(float(tx.monto), tx.moneda, con_decimales=False)
    f_corta = fecha_corta_wpp(tx.fecha, hoy_argentina())
    f_disp = f" ({f_corta})" if f_corta else ""
    prep = "en" if tx.tipo == TipoTransaccion.INGRESO else "desde"
    return tx, f"Listo, quedó {m_fmt} en {cat_disp} {prep} {b_nom}{f_disp}.", False



def _detectar_correccion_ultimo_movimiento(
    mensaje: str,
    usuario_id: UUID,
    db: Session,
    tx_actual: Transaccion,
) -> tuple[bool, dict, str | None]:
    norm = normalizar_texto(mensaje)
    if not norm:
        return False, {}, None

    if _es_saludo(mensaje) or _es_confirmacion(mensaje) or _es_cancelacion(mensaje) or _es_pedido_deshacer(mensaje):
        return False, {}, None

    # Regla A: Verbo de operación en CUALQUIER parte del mensaje implica movimiento nuevo
    verbos_op_nueva = (
        r"\b(?:gaste|pague|compre|cargue|cobre|ingresaron|ingrese|me\s+paso|me\s+pasaron|"
        r"me\s+transfirio|me\s+mando|le\s+pase|le\s+transferi|transferi|le\s+envie|le\s+pague|"
        r"meti|puse|pase|saque|extraje|retire|vendi|dolarice|mande|movi)\b"
    )
    if re.search(verbos_op_nueva, norm):
        return False, {}, None

    # Regla A: Señales explícitas de corrección requeridas
    # 1. Empieza con "no" (seguido de coma, espacio o fin de mensaje)
    # 2. Contiene "me equivoqué", "corregí", "corregilo", "cambialo", "cambiá", "en realidad" o "eso era"
    # 3. "era/eran <monto o categoría>", "fue con/en <billetera>", "fue ayer/anteayer/hoy"
    # 4. "(ese|el) (gasto|ingreso|movimiento|último) (es|era|fue) (de|del|el) <fecha>"
    tiene_no_inicial = bool(re.search(r"^no(?:[,\s]|$)", norm))
    tiene_frase_explicita = bool(
        re.search(
            r"\b(?:me\s+equivoque|corregi|corregilo|cambialo|cambia|en\s+realidad|eso\s+era)\b",
            norm,
        )
    )
    tiene_senial_era = bool(re.search(r"\b(?:eran?)\s+", norm))
    tiene_senial_billetera = bool(re.search(r"\b(?:fue\s+con|era\s+con|fue\s+en|era\s+en)\s+", norm))
    tiene_senial_fecha_rel = bool(re.search(r"\b(?:fue|era)\s+(?:ayer|anteayer|hoy)\b", norm))
    tiene_senial_fecha_op = bool(
        re.search(
            r"\b(?:ese|el)\s+(?:gasto|ingreso|movimiento|ultimo|último)\s+(?:es|era|fue)\s+(?:de|del|el)\b",
            norm,
        )
        or re.search(r"^(?:es|era|fue)\s+(?:de|del|el)\s+", norm)
    )

    es_senial_explicita = (
        tiene_no_inicial
        or tiene_frase_explicita
        or tiene_senial_era
        or tiene_senial_billetera
        or tiene_senial_fecha_rel
        or tiene_senial_fecha_op
    )

    if not es_senial_explicita:
        # Una fecha sola o mensaje sin señal explícita nunca es corrección
        return False, {}, None

    # Descartar si tiene dos o más montos salvo que sea la estructura "X no Y"
    es_correccion_dos_montos = bool(re.search(r"(?:eran?|fue)?\s*\$?[\d\.,]+k?\s+no\s+\$?[\d\.,]+k?", norm))
    norm_sin_fechas = re.sub(r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b", "", norm)
    montos_detectados = re.findall(r"\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?\b", norm_sin_fechas)
    # Filtrar solo montos numéricos significativos
    montos_reales = [m for m in montos_detectados if _parsear_monto_argentino(m) is not None]
    if len(montos_reales) >= 2 and not es_correccion_dos_montos:
        return False, {}, None

    cambios: dict = {}
    texto_restante = mensaje.strip()

    # 1. Detectar monto: "eran 3.000 no 30.000", "no, 5000", "eran 3000", etc.
    m_no = re.search(r"(?:eran?|fue)?\s*\$?([\d\.,]+k?)\s+no\s+\$?([\d\.,]+k?)", norm)
    if m_no:
        m_val = _parsear_monto_argentino(m_no.group(1))
        if m_val:
            cambios["monto"] = float(m_val)
            texto_restante = re.sub(r"(?:eran?|fue)?\s*\$?[\d\.,]+k?\s+no\s+\$?[\d\.,]+k?", "", texto_restante, flags=re.IGNORECASE).strip()

    if "monto" not in cambios:
        m_num = re.search(r"^no,?\s+(?:eran?\s+)?\$?([\d\.,]+k?)$", norm)
        if m_num:
            m_val = _parsear_monto_argentino(m_num.group(1))
            if m_val:
                cambios["monto"] = float(m_val)
                texto_restante = ""

    if "monto" not in cambios:
        m_era = re.search(r"(?:eran?|era)\s+\$?([\d\.,]+k?)", norm)
        if m_era:
            m_val = _parsear_monto_argentino(m_era.group(1))
            if m_val:
                cambios["monto"] = float(m_val)
                texto_restante = re.sub(r"(?:eran?|era)\s+\$?[\d\.,]+k?", "", texto_restante, flags=re.IGNORECASE).strip()

    # 2. Detectar billetera: "fue con Santander", "con Santander", "era Santander", etc.
    billeteras_activas = _obtener_billeteras_activas(usuario_id, db)
    b_encontrada = None
    m_bill = re.search(r"(?:fue\s+con|era\s+con|fue\s+en|era\s+en|con|en)\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+)", texto_restante, flags=re.IGNORECASE)
    if m_bill:
        candidato_bill = m_bill.group(1).strip()
        b_match, _ = resolver_billetera_cascada(candidato_bill, billeteras_activas)
        if b_match:
            b_encontrada = b_match
            texto_restante = texto_restante.replace(m_bill.group(0), "").strip()

    if not b_encontrada:
        for b in billeteras_activas:
            b_n = normalizar_texto(b.nombre)
            if b_n in norm.split() or b_n in norm:
                b_encontrada = b
                break
            for ak, av in ALIAS_BILLETERAS.items():
                if ak in norm.split() and (av in b_n or b_n in av):
                    b_encontrada = b
                    break
            if b_encontrada:
                break

    if b_encontrada:
        if b_encontrada.moneda != tx_actual.moneda:
            nom_otra = "dólares" if b_encontrada.moneda == Moneda.USD else "pesos"
            nom_act = "pesos" if tx_actual.moneda == Moneda.ARS else "dólares"
            tipo_lbl = "ingreso" if tx_actual.tipo == TipoTransaccion.INGRESO else "gasto"
            return True, {}, (
                f"{b_encontrada.nombre} es en {nom_otra} "
                f"y el {tipo_lbl} es en {nom_act}."
            )
        if b_encontrada.id != tx_actual.billetera_id:
            cambios["billetera_id"] = str(b_encontrada.id)
            cambios["billetera_nombre"] = b_encontrada.nombre

    # 3. Detectar fecha con resolvedor completo: "ese ingreso es del 27/09", "el 18 de septiembre", "fue ayer", etc.
    candidato_fecha_str = None
    m_op_fecha = re.search(
        r"(?:(?:ese|el)\s+(?:gasto|ingreso|movimiento|ultimo|último)\s+)?(?:es|era|fue)\s+(?:de|del|el)\s+([^\.,;]+)",
        norm,
    )
    if m_op_fecha:
        candidato_fecha_str = m_op_fecha.group(1).strip()
    elif tiene_senial_fecha_rel:
        m_rel = re.search(r"\b(?:fue|era)\s+(ayer|anteayer|hoy)\b", norm)
        if m_rel:
            candidato_fecha_str = m_rel.group(1).strip()
    else:
        m_fecha_gen = re.search(r"\b(ayer|anteayer|hoy|(?:el\s+|del\s+)?\d+\s+de\s+[a-z]+(?:\s+de\s+\d+)?|(?:el\s+|del\s+)?\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?)\b", norm)
        if m_fecha_gen and (tiene_no_inicial or tiene_frase_explicita):
            candidato_fecha_str = m_fecha_gen.group(1).strip()

    if candidato_fecha_str:
        f_obj = _parsear_fecha_texto(candidato_fecha_str)
        if f_obj:
            f_val, _ = _resolver_y_validar_fecha(f_obj)
            if f_val != tx_actual.fecha:
                cambios["fecha"] = f_val.isoformat()

    # 4. Detectar categoría: "eso era supermercado", "era supermercado", "era en supermercado"
    m_cat = re.search(r"(?:eso\s+era|era|en\s+realidad\s+era)\s+(?:en\s+)?([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+)", texto_restante, flags=re.IGNORECASE)
    cat_texto = None
    if m_cat:
        cat_texto = m_cat.group(1).strip()
    elif not cambios and norm.startswith("eso era "):
        cat_texto = norm.replace("eso era ", "").strip()
    elif not cambios and norm.startswith("era "):
        cat_texto = norm.replace("era ", "").strip()

    if cat_texto:
        c_id, s_id = _resolver_categoria_y_subcategoria(cat_texto, usuario_id, db, tipo=tx_actual.tipo.value)
        if c_id:
            if normalizar_texto(cat_texto) != "otros":
                cat_db = db.get(Categoria, c_id)
                if cat_db and normalizar_texto(cat_db.nombre) == "otros":
                    c_id = None
            if c_id:
                cambios["categoria_id"] = str(c_id)
                cambios["subcategoria_id"] = str(s_id) if s_id else None
                cat_db = db.get(Categoria, c_id)
                sub_db = db.get(Subcategoria, s_id) if s_id else None
                cambios["categoria_nombre"] = sub_db.nombre if sub_db else (cat_db.nombre if cat_db else "Otros")

    if cambios:
        return True, cambios, None

    return False, {}, None


def _construir_propuesta_deshacer(
    tx_actual: Transaccion | TransferenciaInterna | list[Transaccion], db: Session
) -> str:
    hoy = hoy_argentina()
    if isinstance(tx_actual, list):
        if tx_actual and isinstance(tx_actual[0], TransferenciaInterna):
            return f"¿Borro los {len(tx_actual)} pases que anotaste recién?"
        cant = len(tx_actual)
        tipos_lote = {t.tipo for t in tx_actual}
        if tipos_lote == {TipoTransaccion.EGRESO}:
            sust = "gasto" if cant == 1 else "gastos"
        elif tipos_lote == {TipoTransaccion.INGRESO}:
            sust = "ingreso" if cant == 1 else "ingresos"
        else:
            sust = "movimientos"
        ids = [t.id for t in tx_actual]
        espejos_lote = db.query(Transaccion).filter(
            Transaccion.reintegro_de_id.in_(ids),
            Transaccion.es_cuota_hija == False,
        ).all()
        reintegros = [
            e for e in espejos_lote if getattr(e, "tipo_espejo", None) in ("reintegro", None)
        ]
        partes = [e for e in espejos_lote if getattr(e, "tipo_espejo", None) == "parte"]
        lineas_extra: list[str] = []
        if reintegros:
            suma_r = sum((abs(e.monto) for e in reintegros), Decimal("0"))
            m_r = formatear_monto(float(suma_r), reintegros[0].moneda, con_decimales=False)
            lineas_extra.append(f"Si los borro, también se borra su reintegro de {m_r}.")
        if partes:
            suma_p = sum((abs(e.monto) for e in partes), Decimal("0"))
            m_p = formatear_monto(float(suma_p), partes[0].moneda, con_decimales=False)
            lineas_extra.append(
                f"Si los borro, los {m_p} que te devolvieron vuelven a ser un cobro."
            )
        preg = f"¿Borro los {cant} {sust} que anotaste recién?"
        return "\n".join(lineas_extra + [preg])

    if isinstance(tx_actual, TransferenciaInterna):
        billetera_orig = (
            db.get(Billetera, tx_actual.billetera_origen_id)
            if tx_actual.billetera_origen_id
            else None
        )
        billetera_dest = (
            db.get(Billetera, tx_actual.billetera_destino_id)
            if tx_actual.billetera_destino_id
            else None
        )
        bill_orig_nom = billetera_orig.nombre if billetera_orig else "origen"
        bill_dest_nom = billetera_dest.nombre if billetera_dest else "destino"
        monto_fmt = formatear_monto(
            float(tx_actual.monto_origen), tx_actual.moneda_origen, con_decimales=False
        )
        return f"¿Borro el pase de {monto_fmt} de {bill_orig_nom} a {bill_dest_nom}?"

    if hasattr(tx_actual, "movimiento_meta_id") and (
        tx_actual.movimiento_meta_id is not None
        or (tx_actual.descripcion and tx_actual.descripcion.startswith("Aporte a la meta:"))
    ):
        meta_nom = (
            tx_actual.descripcion.replace("Aporte a la meta:", "").strip()
            if tx_actual.descripcion
            else "tu meta"
        )
        monto_fmt = formatear_monto(float(tx_actual.monto), tx_actual.moneda, con_decimales=False)
        return f"¿Borro el aporte de {monto_fmt} a tu meta {meta_nom}?"

    if getattr(tx_actual, "reintegro_de_id", None) is not None:
        compra_padre = db.get(Transaccion, tx_actual.reintegro_de_id)
        desc_padre = compra_padre.descripcion if compra_padre else "ese gasto"
        monto_fmt = formatear_monto(
            abs(float(tx_actual.monto)), tx_actual.moneda, con_decimales=False
        )
        return f"¿Borro el reintegro de {monto_fmt} de {desc_padre}?"

    monto_fmt = formatear_monto(float(tx_actual.monto), tx_actual.moneda, con_decimales=False)
    billetera = db.get(Billetera, tx_actual.billetera_id) if tx_actual.billetera_id else None
    bill_nom = billetera.nombre if billetera else "tu cuenta"
    cat_nom = None
    if tx_actual.categoria_id:
        c = db.get(Categoria, tx_actual.categoria_id)
        cat_nom = c.nombre if c else None
    if tx_actual.subcategoria_id:
        s = db.get(Subcategoria, tx_actual.subcategoria_id)
        cat_nom = s.nombre if s else cat_nom
    cat_disp = cat_nom or "Otros"
    f_corta = fecha_corta_wpp(tx_actual.fecha, hoy)
    fecha_disp = f" ({f_corta})" if f_corta else ""

    espejos_compra = db.query(Transaccion).filter(
        Transaccion.reintegro_de_id == tx_actual.id,
        Transaccion.es_cuota_hija == False,
    ).all()
    reintegros = [
        e for e in espejos_compra if getattr(e, "tipo_espejo", None) in ("reintegro", None)
    ]
    partes = [e for e in espejos_compra if getattr(e, "tipo_espejo", None) == "parte"]
    lineas_espejos: list[str] = []
    if reintegros:
        suma_r = sum((abs(e.monto) for e in reintegros), Decimal("0"))
        m_r = formatear_monto(float(suma_r), reintegros[0].moneda, con_decimales=False)
        lineas_espejos.append(f"Si lo borro, también se borra su reintegro de {m_r}.")
    if partes:
        suma_p = sum((abs(e.monto) for e in partes), Decimal("0"))
        m_p = formatear_monto(float(suma_p), partes[0].moneda, con_decimales=False)
        lineas_espejos.append(
            f"Si lo borro, los {m_p} que te devolvieron vuelven a ser un cobro."
        )

    if tx_actual.metodo_pago == MetodoPago.CREDITO or tx_actual.tarjeta_id:
        tarjeta = db.get(TarjetaCredito, tx_actual.tarjeta_id) if tx_actual.tarjeta_id else None
        tarjeta_nom = f"la {nombre_tarjeta_wpp(tarjeta)}" if tarjeta else "la tarjeta"
        grupo = db.execute(
            select(GrupoCuotas).where(GrupoCuotas.transaccion_padre_id == tx_actual.id)
        ).scalar_one_or_none()
        if grupo and grupo.cantidad_cuotas > 1:
            preg = (
                f"¿Borro la compra de {monto_fmt} en {grupo.cantidad_cuotas} cuotas "
                f"con {tarjeta_nom}?"
            )
        else:
            preg = f"¿Borro {monto_fmt} en {cat_disp} con {tarjeta_nom}{fecha_disp}?"
    elif tx_actual.tipo == TipoTransaccion.INGRESO:
        preg = f"¿Borro el ingreso de {monto_fmt} en {bill_nom}{fecha_disp}?"
    else:
        preg = f"¿Borro {monto_fmt} en {cat_disp} desde {bill_nom}{fecha_disp}?"
    return "\n".join(lineas_espejos + [preg])


def _construir_propuesta_corregir(
    tx_actual: Transaccion,
    cambios: dict,
    db: Session,
) -> str:
    hoy = hoy_argentina()
    b_vieja = db.get(Billetera, tx_actual.billetera_id)
    cat_vieja = db.get(Categoria, tx_actual.categoria_id) if tx_actual.categoria_id else None
    sub_vieja = db.get(Subcategoria, tx_actual.subcategoria_id) if tx_actual.subcategoria_id else None
    cat_vieja_disp = sub_vieja.nombre if sub_vieja else (cat_vieja.nombre if cat_vieja else "Otros")
    b_vieja_nom = b_vieja.nombre if b_vieja else "tu cuenta"
    f_vieja_corta = fecha_corta_wpp(tx_actual.fecha, hoy)
    m_viejo_fmt = formatear_monto(float(tx_actual.monto), tx_actual.moneda, con_decimales=False)

    m_nuevo_fmt = m_viejo_fmt
    if "monto" in cambios:
        m_nuevo_fmt = formatear_monto(
            float(cambios["monto"]), tx_actual.moneda, con_decimales=False
        )

    cat_nueva_disp = cat_vieja_disp
    if "categoria_nombre" in cambios:
        cat_nueva_disp = cambios["categoria_nombre"]
    elif "categoria_id" in cambios:
        c_n = db.get(Categoria, UUID(cambios["categoria_id"]))
        s_n = db.get(Subcategoria, UUID(cambios["subcategoria_id"])) if cambios.get("subcategoria_id") else None
        cat_nueva_disp = s_n.nombre if s_n else (c_n.nombre if c_n else cat_vieja_disp)

    b_nueva_nom = b_vieja_nom
    if "billetera_nombre" in cambios:
        b_nueva_nom = cambios["billetera_nombre"]
    elif "billetera_id" in cambios:
        b_n = db.get(Billetera, UUID(cambios["billetera_id"]))
        b_nueva_nom = b_n.nombre if b_n else b_vieja_nom

    f_nueva_corta = f_vieja_corta
    if "fecha" in cambios and cambios["fecha"]:
        f_nueva_corta = fecha_corta_wpp(date.fromisoformat(str(cambios["fecha"])), hoy)
    f_nueva_disp = f" ({f_nueva_corta})" if f_nueva_corta else ""

    pares_cambio: list[tuple[str, str]] = []
    if "monto" in cambios and Decimal(str(cambios["monto"])) != tx_actual.monto:
        pares_cambio.append((m_viejo_fmt, m_nuevo_fmt))
    cat_cambio = "categoria_id" in cambios or "categoria_nombre" in cambios
    if cat_cambio and cat_nueva_disp != cat_vieja_disp:
        pares_cambio.append((cat_vieja_disp, cat_nueva_disp))
    if ("billetera_id" in cambios or "billetera_nombre" in cambios) and b_nueva_nom != b_vieja_nom:
        pares_cambio.append((b_vieja_nom, b_nueva_nom))
    if cambios.get("fecha") and f_nueva_corta != f_vieja_corta:
        pares_cambio.append((f_vieja_corta or "hoy", f_nueva_corta or "hoy"))

    if len(pares_cambio) == 1:
        viejo, nuevo = pares_cambio[0]
        return f"¿Lo cambio de {viejo} a {nuevo}?"
    prep = "en" if tx_actual.tipo == TipoTransaccion.INGRESO else "desde"
    return f"¿Lo dejo en {m_nuevo_fmt} en {cat_nueva_disp} {prep} {b_nueva_nom}{f_nueva_disp}?"
