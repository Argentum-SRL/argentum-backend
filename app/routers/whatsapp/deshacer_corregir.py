"""
app/routers/whatsapp/deshacer_corregir.py — Funciones para deshacer y corregir transacciones y movimientos por WhatsApp.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import structlog
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
from app.routers.whatsapp.parsers import (
    _formatear_fecha_natural,
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
        msg_error = f"No podés usar una billetera en {nom_otra} para un movimiento en {nom_prop}.\n{menu_validas}"
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
            return None, "Esa operación ya fue deshecha.", True
        return None, "No tenés ninguna anulación pendiente para confirmar.", False

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
            return None, "El movimiento ya fue eliminado.", False
        cant_eliminados = len(txs)
        try:
            for t in txs:
                eliminar_transaccion(db, usuario.id, t.id, commit=False)
            conv_undo.accion_ejecutada = f"deshecho:lote:{','.join(str(x) for x in lote_uuids)}"
            emitir_evento_actualizacion(db, usuario.id, "transacciones")
            emitir_evento_actualizacion(db, usuario.id, "billeteras")
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Error al eliminar lote de transacciones: {e}")
            return None, "Hubo un problema al eliminar el lote, no se borró nada, intentá de nuevo.", False
        return txs[0], f"Listo, {cant_eliminados} movimientos eliminados.", False

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
            return None, "El movimiento ya fue eliminado.", False

        try:
            transferencia_service.eliminar_transferencia(db, usuario.id, tr.id, commit=False)
            conv_undo.accion_ejecutada = f"deshecho:{tr_id}"
            emitir_evento_actualizacion(db, usuario.id, "transferencias")
            emitir_evento_actualizacion(db, usuario.id, "billeteras")
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"Error al anular transferencia {tr_id}: {e}")
            return None, "Hubo un problema al anular la transferencia, no se modificó nada.", False

        return tr, "Listo, movimiento eliminado.", False

    tx_id_str = entidades.get("transaccion_id")
    if not tx_id_str:
        return None, "No pude procesar la anulación.", False

    tx_id = UUID(str(tx_id_str))
    tx = db.execute(
        select(Transaccion)
        .where(Transaccion.id == tx_id, Transaccion.usuario_id == usuario.id)
        .with_for_update()
    ).scalars().first()

    if not tx:
        conv_undo.accion_ejecutada = f"deshecho:{tx_id}"
        db.commit()
        return None, "El movimiento ya fue eliminado.", False

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
                return tx, "Listo, movimiento eliminado.", False
            except Exception as e:
                db.rollback()
                logger.error(f"Error al anular aporte a meta {tx_id}: {e}")
                return None, "Hubo un problema al anular el movimiento, no se modificó nada.", False

    try:
        eliminar_transaccion(db, usuario.id, tx.id, commit=False)
        conv_undo.accion_ejecutada = f"deshecho:{tx_id}"
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Error al anular transacción {tx_id}: {e}")
        return None, "Hubo un problema al anular el movimiento, no se modificó nada.", False

    return tx, "Listo, movimiento eliminado.", False


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
            return None, "Esa operación ya fue corregida.", True
        return None, "No tenés ninguna corrección pendiente para confirmar.", False

    entidades = conv_corr.entidades or {}
    tx_id_str = entidades.get("transaccion_id")
    cambios = entidades.get("cambios") or {}
    if not tx_id_str or not cambios:
        return None, "No pude procesar la corrección.", False

    tx_id = UUID(str(tx_id_str))
    tx = db.execute(
        select(Transaccion)
        .where(Transaccion.id == tx_id, Transaccion.usuario_id == usuario.id)
        .with_for_update()
    ).scalars().first()

    if not tx:
        conv_corr.accion_ejecutada = f"corregido:{tx_id}"
        db.commit()
        return None, "El movimiento ya fue eliminado. No hay nada para corregir.", False

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

    actualizar_transaccion(db, usuario.id, tx.id, update_payload)

    conv_corr.accion_ejecutada = f"corregido:{tx_id}"
    emitir_evento_actualizacion(db, usuario.id, "transacciones")
    emitir_evento_actualizacion(db, usuario.id, "billeteras")
    db.commit()

    return tx, "Listo, movimiento corregido.", False


from app.routers.whatsapp.parsers import (
    _extraer_frecuencia_mencionada,
    _extraer_nombre_servicio,
    _formatear_fecha_natural,
    _nombre_corto_categoria,
    _parsear_fecha_texto,
    _parsear_monto_argentino,
    _resolver_y_validar_fecha,
)


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
            return True, {}, f"No podés usar una billetera en {nom_otra} para un movimiento en {nom_act}."
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


def _construir_propuesta_deshacer(tx_actual: Transaccion | TransferenciaInterna | list[Transaccion], db: Session) -> str:
    if isinstance(tx_actual, list):
        cant = len(tx_actual)
        return f"¿Querés eliminar los {cant} movimientos registrados recientemente? ¿Confirmás?"

    if isinstance(tx_actual, TransferenciaInterna):
        billetera_orig = db.get(Billetera, tx_actual.billetera_origen_id) if tx_actual.billetera_origen_id else None
        billetera_dest = db.get(Billetera, tx_actual.billetera_destino_id) if tx_actual.billetera_destino_id else None
        bill_orig_nom = billetera_orig.nombre if billetera_orig else "origen"
        bill_dest_nom = billetera_dest.nombre if billetera_dest else "destino"
        monto_fmt = formatear_monto(float(tx_actual.monto_origen), tx_actual.moneda_origen)
        return f"¿Querés anular la transferencia de {monto_fmt} de {bill_orig_nom} a {bill_dest_nom}? ¿Confirmás?"

    if hasattr(tx_actual, "movimiento_meta_id") and (tx_actual.movimiento_meta_id is not None or (tx_actual.descripcion and tx_actual.descripcion.startswith("Aporte a la meta:"))):
        meta_nom = tx_actual.descripcion.replace("Aporte a la meta:", "").strip() if tx_actual.descripcion else "tu meta"
        monto_fmt = formatear_monto(float(tx_actual.monto), tx_actual.moneda)
        return f"¿Querés eliminar el aporte de {monto_fmt} a tu meta '{meta_nom}'? ¿Confirmás?"

    monto_fmt = formatear_monto(float(tx_actual.monto), tx_actual.moneda)
    billetera = db.get(Billetera, tx_actual.billetera_id) if tx_actual.billetera_id else None
    bill_nom = billetera.nombre if billetera else "tu billetera"
    cat_nom = None
    if tx_actual.categoria_id:
        c = db.get(Categoria, tx_actual.categoria_id)
        cat_nom = c.nombre if c else None
    if tx_actual.subcategoria_id:
        s = db.get(Subcategoria, tx_actual.subcategoria_id)
        cat_nom = s.nombre if s else cat_nom
    cat_disp = cat_nom or "Otros"
    fecha_nat = _formatear_fecha_natural(tx_actual.fecha)
    fecha_disp = f" ({fecha_nat})" if fecha_nat else ""

    if tx_actual.metodo_pago == MetodoPago.CREDITO or tx_actual.tarjeta_id:
        tarjeta = db.get(TarjetaCredito, tx_actual.tarjeta_id) if tx_actual.tarjeta_id else None
        tarjeta_nom = f"tarjeta {tarjeta.nombre}" if tarjeta else "tarjeta de crédito"
        grupo = db.execute(
            select(GrupoCuotas).where(GrupoCuotas.transaccion_padre_id == tx_actual.id)
        ).scalar_one_or_none()
        if grupo and grupo.cantidad_cuotas > 1:
            return f"¿Querés eliminar la compra de {monto_fmt} en {grupo.cantidad_cuotas} cuotas con {tarjeta_nom}? ¿Confirmás?"
        else:
            return f"¿Querés eliminar el último consumo de {monto_fmt} en {cat_disp} con {tarjeta_nom}{fecha_disp}? ¿Confirmás?"

    if tx_actual.tipo == TipoTransaccion.INGRESO:
        return f"¿Querés eliminar el último ingreso de {monto_fmt} en {cat_disp} a {bill_nom}{fecha_disp}? ¿Confirmás?"
    else:
        return f"¿Querés eliminar el último movimiento de {monto_fmt} en {cat_disp} desde {bill_nom}{fecha_disp}? ¿Confirmás?"


def _construir_propuesta_corregir(
    tx_actual: Transaccion,
    cambios: dict,
    db: Session,
) -> str:
    b_vieja = db.get(Billetera, tx_actual.billetera_id)
    cat_vieja = db.get(Categoria, tx_actual.categoria_id) if tx_actual.categoria_id else None
    sub_vieja = db.get(Subcategoria, tx_actual.subcategoria_id) if tx_actual.subcategoria_id else None
    cat_vieja_disp = sub_vieja.nombre if sub_vieja else (cat_vieja.nombre if cat_vieja else "Otros")
    b_vieja_nom = b_vieja.nombre if b_vieja else "tu billetera"
    f_vieja_nat = _formatear_fecha_natural(tx_actual.fecha)
    f_vieja_disp = f" ({f_vieja_nat})" if f_vieja_nat else ""
    m_viejo_fmt = formatear_monto(float(tx_actual.monto), tx_actual.moneda)

    if "monto" in cambios:
        m_nuevo_fmt = formatear_monto(float(cambios["monto"]), tx_actual.moneda)
    else:
        m_nuevo_fmt = m_viejo_fmt

    if "categoria_nombre" in cambios:
        cat_nueva_disp = cambios["categoria_nombre"]
    elif "categoria_id" in cambios:
        c_n = db.get(Categoria, UUID(cambios["categoria_id"]))
        s_n = db.get(Subcategoria, UUID(cambios["subcategoria_id"])) if cambios.get("subcategoria_id") else None
        cat_nueva_disp = s_n.nombre if s_n else (c_n.nombre if c_n else cat_vieja_disp)
    else:
        cat_nueva_disp = cat_vieja_disp

    if "billetera_nombre" in cambios:
        b_nueva_nom = cambios["billetera_nombre"]
    elif "billetera_id" in cambios:
        b_n = db.get(Billetera, UUID(cambios["billetera_id"]))
        b_nueva_nom = b_n.nombre if b_n else b_vieja_nom
    else:
        b_nueva_nom = b_vieja_nom

    if "fecha" in cambios and cambios["fecha"]:
        f_nueva_obj = date.fromisoformat(str(cambios["fecha"]))
        if f_nueva_obj == hoy_argentina():
            f_nueva_disp = " (hoy)"
        else:
            f_nueva_nat = _formatear_fecha_natural(f_nueva_obj)
            f_nueva_disp = f" ({f_nueva_nat})" if f_nueva_nat else ""
    else:
        f_nueva_disp = f_vieja_disp

    if tx_actual.tipo == TipoTransaccion.INGRESO:
        linea_antes = f"{m_viejo_fmt} en {cat_vieja_disp} a {b_vieja_nom}{f_vieja_disp}"
        linea_ahora = f"{m_nuevo_fmt} en {cat_nueva_disp} a {b_nueva_nom}{f_nueva_disp}"
    else:
        linea_antes = f"{m_viejo_fmt} en {cat_vieja_disp} desde {b_vieja_nom}{f_vieja_disp}"
        linea_ahora = f"{m_nuevo_fmt} en {cat_nueva_disp} desde {b_nueva_nom}{f_nueva_disp}"

    return (
        f"Voy a corregir el último movimiento:\n"
        f"Antes: {linea_antes}\n"
        f"Ahora: {linea_ahora}\n"
        f"¿Confirmás?"
    )
