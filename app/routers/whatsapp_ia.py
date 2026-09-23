"""
app/routers/whatsapp_ia.py — Webhook de WhatsApp para IA conversacional de Argentum con Meta Cloud API.
Recibe webhooks JSON de Meta, los procesa con ai_service y responde vía Graph API.
"""
import hashlib
import hmac
import json
import re
import secrets
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import httpx
import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.auth import get_current_admin_user
from app.core.database import SessionLocal, get_db
from app.core.config import settings
from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.utils.fecha import hoy_argentina, TZ_ARGENTINA
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
from app.models.grupo_cuotas import GrupoCuotas
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import EstadoUsuario, Moneda, Usuario
from app.models.transferencia_interna import TransferenciaInterna
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import transferencia_service
from app.models.meta import Meta, EstadoMeta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.services import meta_service
from app.services import ai_service
from app.services import presupuesto_service
from app.services.evento_service import emitir_evento_actualizacion
from app.services.openai_client import get_openai_client
from app.services import transaccion_service
from app.services.transaccion_service import (
    deducir_metodo_pago,
    eliminar_transaccion,
    actualizar_transaccion,
)
from app.services.tarjeta_service import calcular_primer_vencimiento
from app.schemas.transaccion import InfoCuotas, TransaccionCreate, TransaccionUpdate
from app.services.whatsapp_service import (
    enviar_whatsapp,
    buscar_codigo_vinculacion,
    consumir_codigo_vinculacion,
    marcar_leido_y_escribiendo,
    get_meta_http_client,
)
from app.services.rate_limit_service import verificar_rate_limit
from app.routers.whatsapp.parsers import (
    _MESES_RIOPLATENSE,
    _extraer_frecuencia_mencionada,
    _extraer_monto_y_moneda_suscripcion,
    _extraer_nombre_servicio,
    _fmt,
    _formatear_fecha_natural,
    _interpretar_cuotas,
    _nombre_corto_categoria,
    _parsear_monto_argentino,
    _parsear_monto_texto_cuota,
    _resolver_fecha_transaccion,
    _resolver_y_validar_fecha,
)

from app.routers.whatsapp.detectors import (
    COOLDOWN_MINUTOS_NO_REGISTRADO,
    FRASES_DESHACER,
    PALABRAS_CANCELACION,
    PALABRAS_CONFIRMACION,
    SALUDOS_RIOPLATENSE,
    _debe_responder_no_registrado,
    _detectar_ambiguedad_suscripcion,
    _es_cambio_precio_suscripcion,
    _es_cancelacion,
    _es_confirmacion,
    _es_confirmacion_gasto_aparte,
    _es_confirmacion_lote_ambos,
    _es_confirmacion_lote_uno_solo,
    _es_confirmacion_nuevo_movimiento,
    _es_consulta_suscripciones,
    _es_descarte_duplicado,
    _es_intento_alta_suscripcion,
    _es_pedido_baja_suscripcion,
    _es_pedido_deshacer,
    _es_pedido_pago_resumen,
    _es_pregunta_billetera,
    _es_saludo,
    _es_senial_gasto_suelto,
    _es_senial_suscripcion,
    _parece_intento_correccion,
)
from app.routers.whatsapp.resolvers_cascada import (
    ALIAS_BANCOS_ARGENTINOS,
    ALIAS_BILLETERAS,
    ALIAS_REDES_ARGENTINAS,
    FORMAS_GENERICAS_TARJETA,
    _detectar_duplicados_en_lote,
    _entidades_completas,
    _generar_menu_billeteras,
    _generar_menu_tarjetas,
    _merge_entidades,
    _validar_item_movimiento,
    construir_alias_tarjeta,
    resolver_billetera_cascada,
    resolver_tarjeta_cascada,
)
from app.routers.whatsapp.db_lookups import (
    MAX_INTENTOS_VINCULACION_POR_VENTANA,
    MAX_MEDIOS_POR_MINUTO_REGISTRADO,
    MAX_MENSAJES_POR_MINUTO_REGISTRADO,
    PLAZO_DESHACER_CORREGIR_MINUTOS,
    PLAZO_EXPIRACION_ESTADO_MINUTOS,
    VENTANA_RATE_LIMIT_WPP_SEGUNDOS,
    VENTANA_VINCULACION_SEGUNDOS,
    _buscar_meta_activa_por_nombre,
    _buscar_propuesta_baja_suscripcion_pendiente,
    _buscar_propuesta_cambio_precio_pendiente,
    _buscar_propuesta_confirmable_mas_reciente,
    _buscar_propuesta_corregir_pendiente,
    _buscar_propuesta_deshacer_pendiente,
    _buscar_propuesta_pendiente,
    _buscar_propuesta_suscripcion_pendiente,
    _buscar_propuesta_transferencia_pendiente,
    _buscar_slot_filling_activo,
    _buscar_slot_filling_vencido,
    _buscar_suscripcion_activa_por_nombre,
    _buscar_suscripcion_cobrada_periodo_actual,
    _buscar_transaccion_duplicada_reciente,
    _buscar_ultimo_movimiento_whatsapp,
    _buscar_usuario_por_telefono,
    _obtener_billeteras_activas,
    _obtener_cotizacion_referencia_usuario,
    _obtener_fallback_otros,
    _obtener_historial_reciente,
    _obtener_tarjetas_activas,
    _resolver_billetera,
    _resolver_categoria_y_subcategoria,
    _verificar_rate_limit_registrado,
    _verificar_rate_limit_vinculacion,
)
from app.routers.whatsapp.media import (
    _descargar_medio_meta,
    _extraer_transaccion_de_imagen,
    _obtener_duracion_audio_bytes,
    _transcribir_audio,
)
from app.routers.whatsapp.handlers import (
    manejar_ambiguedad_suscripcion,
    manejar_baja_suscripcion,
    manejar_cambio_precio_suscripcion,
    manejar_cancelacion,
    manejar_confirmacion,
    manejar_consulta_suscripciones,
    manejar_corregir,
    manejar_deshacer,
    manejar_numero_aislado,
    manejar_pago_resumen,
    manejar_saludo,
    manejar_menu_billetera,
    manejar_menu_tarjeta,
    manejar_aclaracion_cuotas,
    manejar_transferencias,
    manejar_verificaciones_slot_filling,
    manejar_alta_suscripcion,
)
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.routers.whatsapp.gastos import manejar_consulta_gastos
from app.utils.telefono import normalizar_telefono_ar
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.schemas.suscripcion import SuscripcionCreate, ActualizarPrecioRequest
from app.services import suscripcion_service
from app.core.catalogo_suscripciones import buscar_servicio_por_texto, identificar_servicio_en_texto
import structlog

from app.core.constants import CATEGORIAS_SISTEMA
from app.utils.texto import normalizar_texto
from app.utils.formato import formatear_monto
from app.models.usuario import Moneda

logger = structlog.get_logger("whatsapp")






router = APIRouter(prefix="/whatsapp", tags=["whatsapp-ia"])

MAX_MOVIMIENTOS_POR_LOTE = 10
MSG_TOPE_MOVIMIENTOS_SUPERADO = (
    "El límite es de 10 movimientos por mensaje. "
    "Por favor mandalos en tandas más chicas o usá la importación desde la web de Argentum."
)
MSG_NO_MEZCLAR_TRANSFERENCIAS = (
    "Las transferencias, extracciones de cajero y compra de dólares deben registrarse "
    "en mensajes separados de los gastos o ingresos. Por favor mandalas por separado."
)














PREFIJOS_CORRECCION = [
    r"^no,?\s+fue\s+en\s+",
    r"^no,?\s+fue\s+con\s+",
    r"^no,?\s+era\s+en\s+",
    r"^no,?\s+era\s+con\s+",
    r"^no,?\s+en\s+",
    r"^no,?\s+con\s+",
    r"^no,?\s+",
    r"^fue\s+en\s+",
    r"^fue\s+con\s+",
    r"^era\s+en\s+",
    r"^era\s+con\s+",
    r"^cambia\s+a\s+",
    r"^cambiala\s+a\s+",
    r"^pasalo\s+a\s+",
    r"^ponele\s+",
    r"^pone\s+",
    r"^mejor\s+",
    r"^en\s+",
    r"^con\s+",
    r"^desde\s+",
    r"^a\s+",
]





# Rate limiting para vinculación de cuentas por WhatsApp (por número de teléfono no registrado)
# Rate limiting para usuarios verificados (protección contra ráfagas y costos de OpenAI en Postgres)


# ==============================================================================
# HELPERS Y CONSTANTES DE TARJETAS DE CRÉDITO Y CUOTAS (PUNTO 9A)
# ==============================================================================

PALABRAS_FUERZAN_CREDITO = [
    "credito", "crédito", "cuota", "cuotas", "en cuotas",
    "visa", "master", "mastercard", "amex", "american express", "american",
    "naranja", "cabal", "tarje", "la tarje", "la de credito", "la de crédito",
    "la credi", "tarjeta de credito", "tarjeta de crédito"
]

PALABRAS_FUERZAN_DEBITO = [
    "debito", "débito", "tarjeta de debito", "tarjeta de débito",
    "debito automatico", "débito automático"
]

MESES_ES_GEN = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]


def _resolver_mencion_tarjeta_en_texto(
    mensaje: str, tarjetas: list[TarjetaCredito]
) -> tuple[TarjetaCredito | None, list[TarjetaCredito]]:
    """
    Encuentra menciones de tarjetas en el texto del mensaje priorizando coincidencias
    más específicas (ej: 'visa del galicia' sobre 'visa' o 'galicia').
    """
    m_norm = normalizar_texto(mensaje)
    if not m_norm or not tarjetas:
        return None, []

    genericas_norm = {normalizar_texto(g) for g in FORMAS_GENERICAS_TARJETA}
    coincidencias = []
    for t in tarjetas:
        aliases = construir_alias_tarjeta(t)
        for a in aliases:
            if a in genericas_norm:
                continue
            if a in m_norm:
                coincidencias.append((len(a), a, t))

    if not coincidencias:
        return None, []

    coincidencias.sort(key=lambda x: x[0], reverse=True)
    mejores_alias = set()
    for l, a, t in coincidencias:
        if any(a in m_alias for m_alias in mejores_alias):
            continue
        mejores_alias.add(a)

    cands = []
    for l, a, t in coincidencias:
        if a in mejores_alias and t not in cands:
            cands.append(t)

    if len(cands) == 1:
        return cands[0], cands
    return None, cands








def _construir_propuesta_credito(
    entidades: dict,
    tarjeta: TarjetaCredito,
    cant_cuotas: int,
    monto_cuota: Decimal,
    monto_total: Decimal,
    fecha_vencimiento: date,
    se_asumio_tarjeta: bool = False,
) -> str:
    """Construye la propuesta obligatoria de consumo con tarjeta de crédito (Tareas 5.4 y 6.5)."""
    moneda_enum = tarjeta.moneda
    cuota_fmt = formatear_monto(float(monto_cuota), moneda_enum)
    total_fmt = formatear_monto(float(monto_total), moneda_enum)

    cat_nom = entidades.get("categoria")
    cat_disp = _nombre_corto_categoria(cat_nom) if cat_nom else "Otros"

    venc_str = f"{fecha_vencimiento.day} de {MESES_ES_GEN[fecha_vencimiento.month - 1]}"

    if cant_cuotas > 1:
        msg = f"Voy a anotar {cant_cuotas} cuotas de {cuota_fmt} (total {total_fmt}) en {cat_disp} con tarjeta {tarjeta.nombre} (primer vencimiento: {venc_str}). ¿Va?"
    else:
        msg = f"Voy a anotar 1 cuota de {cuota_fmt} (total {total_fmt}) en {cat_disp} con tarjeta {tarjeta.nombre} (primer vencimiento: {venc_str}). ¿Va?"

    if se_asumio_tarjeta:
        msg += "\nSi fue con otra tarjeta, decime cuál."
    return msg









def _construir_propuesta_transaccion(
    entidades: dict,
    billetera_nombre: str | None = None,
    se_asumio_principal: bool = False,
    billetera_moneda: Moneda | None = None,
    billeteras_usuario: list[Billetera] | None = None,
) -> str:
    """
    Construye el texto limpio de propuesta de confirmación siempre nombrando la billetera (8.1).
    Soporta cada movimiento con su propia billetera, tipos mezclados (ingresos y egresos),
    y consumos con tarjeta de crédito en lotes.
    Si se asumió la principal sin que el usuario la nombrara, agrega instrucción de corrección (8.2).
    Muestra la fecha natural cuando no es hoy (ayer, anteayer o fecha concreta).
    Si una fecha indicada no se puede usar (>60 días o futura), antepone aviso explicativo.
    Valida anticipadamente moneda, montos válidos y límites antes de proponer el lote,
    descartando los que no se van a poder registrar y avisando el motivo.
    Si todos los movimientos son descartados, informa que no se puede registrar nada.
    Sin emojis, rioplatense.
    """
    avisos_descarte: list[str] = []
    avisos_fechas: list[str] = []

    b_nom_0 = entidades.get("billetera") or entidades.get("billetera_origen") or entidades.get("billetera_destino") or billetera_nombre
    b_mon_0 = billetera_moneda
    if b_nom_0 and not b_mon_0:
        b_mon_0 = Moneda.USD if "usd" in b_nom_0.lower() else Moneda.ARS

    # 1. Validar ítem principal
    item_ppal = {
        "monto": entidades.get("monto"),
        "categoria": entidades.get("categoria"),
        "descripcion": entidades.get("descripcion"),
        "moneda": entidades.get("moneda"),
        "tipo": entidades.get("tipo", "egreso"),
        "fecha": entidades.get("fecha"),
        "billetera": b_nom_0,
        "tarjeta": entidades.get("tarjeta") or entidades.get("tarjeta_nombre"),
        "tarjeta_id": entidades.get("tarjeta_id"),
    }
    item_ppal_limpio, motivo_ppal = _validar_item_movimiento(item_ppal, b_nom_0, b_mon_0, billeteras_usuario)
    if motivo_ppal:
        avisos_descarte.append(motivo_ppal)

    # 2. Validar ítems adicionales si existen
    adicionales = entidades.get("transacciones_adicionales")
    adicionales_validos: list[dict] = []
    if adicionales and isinstance(adicionales, list):
        for ad in adicionales:
            if isinstance(ad, dict):
                b_nom_ad = ad.get("billetera") or ad.get("billetera_origen") or ad.get("billetera_destino") or b_nom_0
                b_mon_ad = Moneda.USD if (b_nom_ad and "usd" in b_nom_ad.lower()) else (billetera_moneda or Moneda.ARS)
                ad_item = dict(ad)
                ad_item["billetera"] = b_nom_ad
                ad_item["tarjeta"] = ad.get("tarjeta") or ad.get("tarjeta_nombre")
                ad_limpio, motivo_ad = _validar_item_movimiento(ad_item, b_nom_ad, b_mon_ad, billeteras_usuario)
                if ad_limpio:
                    adicionales_validos.append(ad_limpio)
                if motivo_ad:
                    avisos_descarte.append(motivo_ad)

    # Reestructurar entidades según los ítems válidos
    if item_ppal_limpio is None:
        if adicionales_validos:
            nuevo_ppal = adicionales_validos.pop(0)
            entidades["monto"] = nuevo_ppal["monto"]
            entidades["categoria"] = nuevo_ppal.get("categoria")
            entidades["descripcion"] = nuevo_ppal.get("descripcion")
            entidades["moneda"] = nuevo_ppal.get("moneda")
            entidades["tipo"] = nuevo_ppal.get("tipo", "egreso")
            entidades["fecha"] = nuevo_ppal.get("fecha")
            entidades["billetera"] = nuevo_ppal.get("billetera")
            entidades["billetera_origen"] = nuevo_ppal.get("billetera")
            entidades["tarjeta"] = nuevo_ppal.get("tarjeta")
            entidades["transacciones_adicionales"] = adicionales_validos
            item_ppal_limpio = nuevo_ppal
        else:
            entidades["transacciones_adicionales"] = []
            lineas_error = list(avisos_descarte)
            lineas_error.append("No se puede registrar ningún movimiento.")
            return "\n".join(lineas_error)
    else:
        entidades["monto"] = item_ppal_limpio["monto"]
        entidades["transacciones_adicionales"] = adicionales_validos

    if adicionales_validos:
        todos_items = [item_ppal_limpio] + adicionales_validos
        total_movs = len(todos_items)

        # Analizar homogeneidad de billeteras y tipos
        billeteras_items = [it.get("billetera") for it in todos_items if it.get("billetera")]
        misma_billetera = len(set(billeteras_items)) <= 1 and not any(it.get("tarjeta") for it in todos_items)
        b_comun = billeteras_items[0] if billeteras_items else billetera_nombre
        mismo_tipo = len(set(it.get("tipo", "egreso") for it in todos_items)) == 1
        tipos_mezclados = any(it.get("tipo") == "ingreso" for it in todos_items) and any(it.get("tipo", "egreso") == "egreso" for it in todos_items)

        if misma_billetera and mismo_tipo and b_comun:
            items_desc = []
            for it in todos_items:
                mon_it = Moneda.USD if it.get("moneda") == "USD" else Moneda.ARS
                m_fmt = formatear_monto(float(it["monto"]), mon_it)
                cat_d = _nombre_corto_categoria(it.get("categoria"))
                fecha_obj, av = _resolver_y_validar_fecha(it.get("fecha"))
                if av and av not in avisos_fechas:
                    avisos_fechas.append(av)
                f_nat = _formatear_fecha_natural(fecha_obj)
                f_disp = f" ({f_nat})" if f_nat else ""
                items_desc.append(f"{m_fmt} en {cat_d}{f_disp}")

            tipo_comun = todos_items[0].get("tipo", "egreso")
            origen_str = f" a {b_comun}" if tipo_comun == "ingreso" else f" desde {b_comun}"
            texto = f"Voy a anotar {total_movs} movimientos{origen_str}: {', '.join(items_desc)}. ¿Va?"
        else:
            items_desc = []
            for it in todos_items:
                mon_it = Moneda.USD if it.get("moneda") == "USD" else Moneda.ARS
                m_fmt = formatear_monto(float(it["monto"]), mon_it)
                cat_d = _nombre_corto_categoria(it.get("categoria"))
                fecha_obj, av = _resolver_y_validar_fecha(it.get("fecha"))
                if av and av not in avisos_fechas:
                    avisos_fechas.append(av)
                f_nat = _formatear_fecha_natural(fecha_obj)
                f_disp = f" ({f_nat})" if f_nat else ""

                t_nom = it.get("tarjeta")
                b_nom = it.get("billetera") or b_comun
                tipo_it = it.get("tipo", "egreso")

                if t_nom:
                    items_desc.append(f"1 cuota de {m_fmt} en {cat_d} con tarjeta {t_nom}{f_disp}")
                elif tipos_mezclados:
                    if tipo_it == "ingreso":
                        dest_s = f" a {b_nom}" if b_nom else ""
                        items_desc.append(f"+{m_fmt} en {cat_d}{dest_s}{f_disp}")
                    else:
                        orig_s = f" desde {b_nom}" if b_nom else ""
                        items_desc.append(f"-{m_fmt} en {cat_d}{orig_s}{f_disp}")
                else:
                    if tipo_it == "ingreso":
                        dest_s = f" a {b_nom}" if b_nom else ""
                        items_desc.append(f"{m_fmt} en {cat_d}{dest_s}{f_disp}")
                    else:
                        orig_s = f" desde {b_nom}" if b_nom else ""
                        items_desc.append(f"{m_fmt} en {cat_d}{orig_s}{f_disp}")
            texto = f"Voy a anotar {total_movs} movimientos: {', '.join(items_desc)}. ¿Va?"
    else:
        moneda_prop = Moneda.USD if item_ppal_limpio.get("moneda") == "USD" else Moneda.ARS
        cat_display = _nombre_corto_categoria(item_ppal_limpio.get("categoria"))
        tipo = item_ppal_limpio.get("tipo", "egreso")
        b_final_nom = item_ppal_limpio.get("billetera") or billetera_nombre or "tu billetera"
        fecha_p_obj, aviso_p = _resolver_y_validar_fecha(item_ppal_limpio.get("fecha"))
        if aviso_p and aviso_p not in avisos_fechas:
            avisos_fechas.append(aviso_p)
        fecha_p_nat = _formatear_fecha_natural(fecha_p_obj)
        fecha_p_disp = f" ({fecha_p_nat})" if fecha_p_nat else ""
        monto_str = formatear_monto(float(item_ppal_limpio["monto"]), moneda_prop)

        t_nom_ppal = item_ppal_limpio.get("tarjeta")
        if t_nom_ppal:
            texto = f"Voy a anotar 1 cuota de {monto_str} en {cat_display} con tarjeta {t_nom_ppal}{fecha_p_disp}. ¿Va?"
        elif tipo == "ingreso":
            partes = [f"Voy a registrar un ingreso de {monto_str}"]
            if cat_display:
                partes.append(f"en {cat_display}")
            partes.append(f"a {b_final_nom}{fecha_p_disp}.")
            partes.append("¿Va?")
            texto = " ".join(partes)
        else:
            partes = [f"Voy a anotar {monto_str}"]
            if cat_display:
                partes.append(f"en {cat_display}")
            partes.append(f"desde {b_final_nom}{fecha_p_disp}.")
            partes.append("¿Va?")
            texto = " ".join(partes)

    lineas = []
    if avisos_descarte:
        lineas.extend(avisos_descarte)
    if avisos_fechas:
        lineas.extend(avisos_fechas)
    lineas.append(texto)

    texto_final = "\n".join(lineas)

    if se_asumio_principal:
        texto_final += "\nSi fue con otra, decime cuál."

    return texto_final










def _registrar_item_batch(
    datos: dict,
    usuario_id: UUID,
    billeteras_usuario: list[Billetera],
    tarjetas_usuario: list[TarjetaCredito],
    db: Session,
    mensaje_original: str | None = None,
) -> tuple[Transaccion | None, str | None]:
    """
    Registra individualmente un ítem perteneciente a un lote o movimiento compuesto:
    - Si es tarjeta de crédito: crea transacción con metodo_pago=CREDITO.
    - Si es billetera: debita o acredita en la billetera resuelta determinísticamente.
    Retorna (transaccion, motivo_descarte_o_none).
    """
    desc = ai_service.sanitizar_descripcion(datos.get("descripcion"), tipo=datos.get("tipo", "egreso")) or _nombre_corto_categoria(datos.get("categoria")) or "un movimiento"
    monto_raw = datos.get("monto")
    if monto_raw is None:
        return None, f"No se pudo registrar {desc} porque no tiene un monto válido."
    try:
        monto_decimal = Decimal(str(monto_raw))
    except Exception:
        return None, f"No se pudo registrar {desc} porque el monto no es válido."

    if monto_decimal <= Decimal("0"):
        return None, f"No se pudo registrar {desc} porque el monto debe ser mayor a cero."
    if monto_decimal > MAX_MONTO_INTEGRIDAD:
        return None, f"No se pudo registrar {desc} porque el monto supera el límite permitido."

    # 1. Tarjeta de crédito si aplica
    tarjeta_id_raw = datos.get("tarjeta_id")
    tarjeta_nom = datos.get("tarjeta")
    tarjeta: TarjetaCredito | None = None
    if tarjeta_id_raw:
        tarjeta = next((t for t in tarjetas_usuario if str(t.id) == str(tarjeta_id_raw)), None)
    elif tarjeta_nom:
        tarjeta, _ = resolver_tarjeta_cascada(tarjeta_nom, tarjetas_usuario)

    if tarjeta:
        cant_cuotas = int(datos.get("cantidad_cuotas", 1))
        monto_total = Decimal(str(datos.get("monto_total", monto_decimal)))
        monto_cuota = Decimal(str(datos.get("monto_cuota", monto_total / cant_cuotas)))
        cat_id, subcat_id = _resolver_categoria_y_subcategoria(
            datos.get("categoria"), usuario_id, db, tipo="egreso"
        )
        fecha_obj, _ = _resolver_y_validar_fecha(datos.get("fecha"))
        desc_final = ai_service.sanitizar_descripcion(
            datos.get("descripcion"),
            mensaje_original=mensaje_original,
            tipo="egreso",
        )
        data_tx = TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=monto_total,
            moneda=tarjeta.moneda,
            fecha=fecha_obj,
            descripcion=desc_final or _nombre_corto_categoria(datos.get("categoria")),
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=tarjeta.billetera_id,
            tarjeta_id=tarjeta.id,
            categoria_id=cat_id,
            subcategoria_id=subcat_id,
            origen=OrigenTransaccion.IA_WPP,
            es_padre_cuotas=True,
            info_cuotas=InfoCuotas(
                cantidad_cuotas=cant_cuotas,
                cuota_inicial=1,
                tiene_interes=False,
                tasa_interes=None,
                monto_total=monto_total,
                proximo_resumen=False,
            ),
        )
        tx = transaccion_service.crear_transaccion(
            db=db,
            usuario_id=usuario_id,
            data=data_tx,
            commit=False,
        )
        return tx, None

    # 2. Billetera
    moneda_sol = Moneda.USD if datos.get("moneda") == "USD" else Moneda.ARS
    nom_b = datos.get("billetera") or datos.get("billetera_origen") or datos.get("billetera_destino")
    billetera_item: Billetera | None = None
    if datos.get("billetera_id"):
        billetera_item = next((b for b in billeteras_usuario if str(b.id) == str(datos.get("billetera_id"))), None)
    elif nom_b:
        billetera_item, _ = resolver_billetera_cascada(nom_b, [b for b in billeteras_usuario if b.moneda == moneda_sol])

    if not billetera_item:
        billetera_item = next((b for b in billeteras_usuario if b.moneda == moneda_sol and b.es_principal), None)
    if not billetera_item:
        b_mon = [b for b in billeteras_usuario if b.moneda == moneda_sol]
        if len(b_mon) == 1:
            billetera_item = b_mon[0]

    if not billetera_item:
        nom_m = "dólares" if moneda_sol == Moneda.USD else "pesos"
        return None, f"No se pudo registrar {desc} porque no tenés una billetera en {nom_m}."

    if billetera_item.moneda != moneda_sol:
        nom_m_sol = "dólares" if moneda_sol == Moneda.USD else "pesos"
        nom_m_b = "dólares" if billetera_item.moneda == Moneda.USD else "pesos"
        m_fmt = formatear_monto(monto_decimal, moneda_sol)
        return None, f"No se pudo registrar {desc} de {m_fmt} porque es en {nom_m_sol} y la billetera {billetera_item.nombre} es en {nom_m_b}."

    billetera_db = db.execute(
        select(Billetera).where(Billetera.id == billetera_item.id).with_for_update()
    ).scalars().first()

    if not billetera_db:
        return None, f"Billetera {billetera_item.nombre} no encontrada."

    tipo_item = datos.get("tipo") or "egreso"
    if tipo_item == "ingreso":
        billetera_db.saldo_actual += monto_decimal
    else:
        billetera_db.saldo_actual -= monto_decimal

    cat_id, subcat_id = _resolver_categoria_y_subcategoria(
        datos.get("categoria"), usuario_id, db, tipo=tipo_item
    )
    fecha_obj, _ = _resolver_y_validar_fecha(datos.get("fecha"))
    desc_final = ai_service.sanitizar_descripcion(
        datos.get("descripcion"),
        mensaje_original=mensaje_original,
        tipo=tipo_item,
    )
    tx = Transaccion(
        usuario_id=usuario_id,
        tipo=TipoTransaccion.INGRESO if tipo_item == "ingreso" else TipoTransaccion.EGRESO,
        monto=monto_decimal,
        moneda=billetera_db.moneda,
        fecha=fecha_obj,
        descripcion=desc_final or _nombre_corto_categoria(datos.get("categoria")),
        metodo_pago=deducir_metodo_pago(billetera_db, tarjeta_id=None),
        billetera_id=billetera_db.id,
        categoria_id=cat_id,
        subcategoria_id=subcat_id,
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_recurrente=False,
        es_cuota_hija=False,
        es_padre_cuotas=False,
    )
    db.add(tx)
    presupuesto_service.registrar_impacto_presupuesto(db, tx, revertir=False, commit=False)
    return tx, None


def _confirmar_propuesta_transaccion(
    usuario: Usuario,
    db: Session,
    propuesta_id: UUID | None = None,
    entidades_actuales: dict | None = None,
) -> tuple[Transaccion | None, str, bool]:
    """
    Ejecuta la confirmación de una propuesta pendiente con bloqueo de fila estricto (with_for_update).
    Garantiza que dos ejecuciones concurrentes NO creen transacciones duplicadas (Tarea 2).
    Retorna: (transaccion_creada_o_none, mensaje_respuesta, fue_ya_confirmada).
    """
    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)

    # 1. Primero verificar si hay una transacción pendiente de IA
    tx_pend = db.execute(
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.origen == OrigenTransaccion.IA_WPP,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE,
            Transaccion.fecha_creacion >= limite_tiempo,
        )
        .order_by(Transaccion.fecha_creacion.desc(), Transaccion.id.desc())
        .with_for_update()
    ).scalars().first()

    if tx_pend:
        tx_pend.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
        billetera = db.execute(
            select(Billetera).where(Billetera.id == tx_pend.billetera_id).with_for_update()
        ).scalars().first()
        if billetera:
            if tx_pend.tipo == TipoTransaccion.INGRESO:
                billetera.saldo_actual += tx_pend.monto
            else:
                billetera.saldo_actual -= tx_pend.monto
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.flush()

        monto_str = formatear_monto(float(tx_pend.monto), tx_pend.moneda)
        bill_nombre = billetera.nombre if billetera else None
        cat_nombre = None
        if tx_pend.categoria_id:
            cat = db.execute(select(Categoria).where(Categoria.id == tx_pend.categoria_id)).scalars().first()
            cat_nombre = cat.nombre if cat else None
        subcat_nombre = None
        if tx_pend.subcategoria_id:
            subcat = db.execute(select(Subcategoria).where(Subcategoria.id == tx_pend.subcategoria_id)).scalars().first()
            subcat_nombre = subcat.nombre if subcat else None
        nombre_cat_disp = subcat_nombre or cat_nombre or "Otros"

        fecha_nat = _formatear_fecha_natural(tx_pend.fecha)
        fecha_disp = f" ({fecha_nat})" if fecha_nat else ""

        if tx_pend.tipo == TipoTransaccion.INGRESO:
            partes = [f"Listo. Ingreso de {monto_str}"]
            if nombre_cat_disp:
                partes.append(f"en {nombre_cat_disp}")
            if bill_nombre:
                partes.append(f"a {bill_nombre}{fecha_disp}")
            partes.append("— registrado.")
        else:
            partes = [f"Listo. {monto_str}"]
            if nombre_cat_disp:
                partes.append(f"en {nombre_cat_disp}")
            if bill_nombre:
                partes.append(f"desde {bill_nombre}{fecha_disp}")
            partes.append("— registrado.")
        msg_resp = " ".join(partes)

        if billetera:
            # REGLA DE PRIVACIDAD: Los saldos no se muestran tras registrar un movimiento,
            # salvo que el usuario los pida explícitamente (privacidad de pantalla).
            if billetera.saldo_actual < 0:
                msg_resp += "\nLa billetera quedó en negativo."

        return tx_pend, msg_resp, False

    # 2. Determinar si las entidades del turno actual vienen completas
    usar_entidades_actuales = _entidades_completas(entidades_actuales)

    # 3. Buscar conversación previa con datos de transacción pendiente con BLOQUEO DE FILA ESTRICTO
    stmt_conv = (
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "registrar_transaccion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.confianza >= Decimal("0.85"),
            ConversacionWpp.fecha >= limite_tiempo,
        )
    )
    if propuesta_id:
        stmt_conv = stmt_conv.where(ConversacionWpp.id == propuesta_id)

    conv_previa = db.execute(
        stmt_conv.order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc()).with_for_update()
    ).scalars().first()

    if not conv_previa and not usar_entidades_actuales:
        # Verificar si la propuesta acaba de ser ejecutada por otra llamada concurrente
        limite_reciente = datetime.now(timezone.utc) - timedelta(minutes=10)
        candidatas = db.execute(
            select(ConversacionWpp)
            .where(
                ConversacionWpp.usuario_id == usuario.id,
                ConversacionWpp.intent_detectado == "registrar_transaccion",
                ConversacionWpp.accion_ejecutada.is_not(None),
                ConversacionWpp.fecha >= limite_reciente,
            )
            .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().all()

        recien_ejecutada = None
        for c in candidatas:
            if c.accion_ejecutada not in ("cancelada", "vencida", "descartado_por_duplicado", "descartada_por_nueva_operacion", "test", "test_setup", "test_reset"):
                try:
                    uuid_val = UUID(str(c.accion_ejecutada))
                    tx_ok = db.execute(select(Transaccion.id).where(Transaccion.id == uuid_val)).scalar()
                    if tx_ok:
                        recien_ejecutada = c
                        break
                except ValueError:
                    pass

        if recien_ejecutada:
            return None, "Esa operación ya fue confirmada.", True
        else:
            return None, "No tenés ninguna operación pendiente para confirmar.", False

    if usar_entidades_actuales:
        entidades = dict(entidades_actuales)  # type: ignore
    else:
        entidades = conv_previa.entidades or {} if conv_previa else {}

    monto = entidades.get("monto")
    if monto is None:
        return None, "No pude procesar la operación.", False

    monto_decimal = Decimal(str(monto))
    tipo_val = entidades.get("tipo") or "egreso"
    moneda_solicitada = Moneda.USD if entidades.get("moneda") == "USD" else Moneda.ARS

    adicionales = entidades.get("transacciones_adicionales")
    if adicionales and isinstance(adicionales, list) and len(adicionales) > 0:
        billeteras_todas = _obtener_billeteras_activas(usuario.id, db)
        tarjetas_todas = _obtener_tarjetas_activas(usuario.id, db)

        item_ppal = dict(entidades)
        item_ppal.pop("transacciones_adicionales", None)

        txs_registradas: list[Transaccion] = []
        items_registrados: list[dict] = []
        descartadas: list[str] = []

        tx_0, mot_0 = _registrar_item_batch(
            item_ppal,
            usuario.id,
            billeteras_todas,
            tarjetas_todas,
            db,
            mensaje_original=conv_previa.mensaje_usuario if conv_previa else None,
        )
        if tx_0:
            txs_registradas.append(tx_0)
            items_registrados.append(item_ppal)
        if mot_0:
            descartadas.append(mot_0)

        for adic in adicionales:
            if isinstance(adic, dict):
                tx_ad, mot_ad = _registrar_item_batch(
                    adic,
                    usuario.id,
                    billeteras_todas,
                    tarjetas_todas,
                    db,
                    mensaje_original=conv_previa.mensaje_usuario if conv_previa else None,
                )
                if tx_ad:
                    txs_registradas.append(tx_ad)
                    items_registrados.append(adic)
                if mot_ad:
                    descartadas.append(mot_ad)

        if not txs_registradas:
            return None, "No se pudo registrar ningún movimiento.", False

        if conv_previa:
            conv_previa.accion_ejecutada = f"lote:{','.join(str(t.id) for t in txs_registradas)}"
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        if any(t.metodo_pago == MetodoPago.CREDITO for t in txs_registradas):
            emitir_evento_actualizacion(db, usuario.id, "tarjetas")
        db.flush()

        total_registrados = len(txs_registradas)
        mov_palabra = "movimientos" if total_registrados != 1 else "movimiento"
        reg_palabra = "registrados" if total_registrados != 1 else "registrado"

        b_map = {b.id: b for b in billeteras_todas}
        t_map = {t.id: t for t in tarjetas_todas}

        b_ids = [t.billetera_id for t in txs_registradas if t.metodo_pago != MetodoPago.CREDITO]
        todas_misma_billetera = len(set(b_ids)) <= 1 and not any(t.metodo_pago == MetodoPago.CREDITO for t in txs_registradas)
        mismo_tipo = len(set(t.tipo for t in txs_registradas)) == 1
        tipos_mezclados = any(t.tipo == TipoTransaccion.INGRESO for t in txs_registradas) and any(t.tipo == TipoTransaccion.EGRESO for t in txs_registradas)

        def _cat_disp_item(item_d: dict, tx_item: Transaccion) -> str:
            cat_nom = item_d.get("categoria")
            if cat_nom:
                return _nombre_corto_categoria(cat_nom)
            if tx_item.subcategoria_id:
                s_obj = db.get(Subcategoria, tx_item.subcategoria_id)
                if s_obj:
                    return s_obj.nombre
            if tx_item.categoria_id:
                c_obj = db.get(Categoria, tx_item.categoria_id)
                if c_obj:
                    return c_obj.nombre
            return _nombre_corto_categoria(tx_item.descripcion) or "Otros"

        if todas_misma_billetera and mismo_tipo and b_ids:
            b_comun = b_map.get(b_ids[0])
            nom_b = b_comun.nombre if b_comun else "tu billetera"
            tipo_comun = txs_registradas[0].tipo
            origen_str = f" a {nom_b}" if tipo_comun == TipoTransaccion.INGRESO else f" desde {nom_b}"
            items_str = []
            for t, it_d in zip(txs_registradas, items_registrados):
                m_fmt = formatear_monto(float(t.monto), t.moneda)
                cat_d = _cat_disp_item(it_d, t)
                f_nat = _formatear_fecha_natural(t.fecha)
                f_disp = f" ({f_nat})" if f_nat else ""
                items_str.append(f"{m_fmt} en {cat_d}{f_disp}")
            msg_resp = f"Listo. {total_registrados} {mov_palabra}{origen_str}: {', '.join(items_str)} — {reg_palabra}."
        else:
            items_str = []
            for t, it_d in zip(txs_registradas, items_registrados):
                m_fmt = formatear_monto(float(t.monto), t.moneda)
                cat_d = _cat_disp_item(it_d, t)
                f_nat = _formatear_fecha_natural(t.fecha)
                f_disp = f" ({f_nat})" if f_nat else ""

                if t.metodo_pago == MetodoPago.CREDITO:
                    t_obj = t_map.get(t.tarjeta_id)
                    t_nom = t_obj.nombre if t_obj else "crédito"
                    items_str.append(f"1 cuota de {m_fmt} en {cat_d} con tarjeta {t_nom}{f_disp}")
                elif tipos_mezclados:
                    b_obj = b_map.get(t.billetera_id)
                    b_nom = b_obj.nombre if b_obj else ""
                    if t.tipo == TipoTransaccion.INGRESO:
                        dest_s = f" a {b_nom}" if b_nom else ""
                        items_str.append(f"+{m_fmt} en {cat_d}{dest_s}{f_disp}")
                    else:
                        orig_s = f" desde {b_nom}" if b_nom else ""
                        items_str.append(f"-{m_fmt} en {cat_d}{orig_s}{f_disp}")
                else:
                    b_obj = b_map.get(t.billetera_id)
                    b_nom = b_obj.nombre if b_obj else ""
                    if t.tipo == TipoTransaccion.INGRESO:
                        dest_s = f" a {b_nom}" if b_nom else ""
                        items_str.append(f"{m_fmt} en {cat_d}{dest_s}{f_disp}")
                    else:
                        orig_s = f" desde {b_nom}" if b_nom else ""
                        items_str.append(f"{m_fmt} en {cat_d}{orig_s}{f_disp}")
            msg_resp = f"Listo. {total_registrados} {mov_palabra}: {', '.join(items_str)} — {reg_palabra}."

        if descartadas:
            msg_resp += "\n" + "\n".join(descartadas)

        billeteras_tocadas = {t.billetera_id for t in txs_registradas if t.billetera_id and t.metodo_pago != MetodoPago.CREDITO}
        for bid in billeteras_tocadas:
            b_chk = b_map.get(bid)
            if b_chk and b_chk.saldo_actual < 0:
                msg_resp += f"\nLa billetera quedó en negativo."

        if conv_previa:
            conv_previa.accion_ejecutada = f"lote:{','.join(str(t.id) for t in txs_registradas)}"
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.commit()

        return txs_registradas[0], msg_resp, False

    tarjeta_id_raw = entidades.get("tarjeta_id")
    if tarjeta_id_raw:
        tarjeta_id = UUID(str(tarjeta_id_raw))
        tarjeta = db.execute(
            select(TarjetaCredito).where(TarjetaCredito.id == tarjeta_id, TarjetaCredito.usuario_id == usuario.id)
        ).scalars().first()
        if not tarjeta:
            return None, "Tarjeta no encontrada.", False

        cant_cuotas = int(entidades.get("cantidad_cuotas", 1))
        monto_total = Decimal(str(entidades.get("monto_total", monto_decimal)))
        monto_cuota = Decimal(str(entidades.get("monto_cuota", monto_total / cant_cuotas)))

        cat_id, subcat_id = _resolver_categoria_y_subcategoria(
            entidades.get("categoria"), usuario.id, db, tipo="egreso"
        )
        fecha_obj, _ = _resolver_y_validar_fecha(entidades.get("fecha"))

        desc_candidata = entidades.get("descripcion")
        desc_final = ai_service.sanitizar_descripcion(
            desc_candidata,
            mensaje_original=conv_previa.mensaje_usuario if conv_previa else None,
            tipo="egreso",
        )

        data_tx = TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=monto_total,
            moneda=tarjeta.moneda,
            fecha=fecha_obj,
            descripcion=desc_final or _nombre_corto_categoria(entidades.get("categoria")),
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=tarjeta.billetera_id,
            tarjeta_id=tarjeta.id,
            categoria_id=cat_id,
            subcategoria_id=subcat_id,
            origen=OrigenTransaccion.IA_WPP,
            es_padre_cuotas=True,
            info_cuotas=InfoCuotas(
                cantidad_cuotas=cant_cuotas,
                cuota_inicial=1,
                tiene_interes=False,
                tasa_interes=None,
                monto_total=monto_total,
                proximo_resumen=False,
            ),
        )
        transaccion = transaccion_service.crear_transaccion(
            db=db,
            usuario_id=usuario.id,
            data=data_tx,
            commit=False,
        )

        if conv_previa:
            conv_previa.accion_ejecutada = str(transaccion.id)
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "tarjetas")
        db.commit()

        primer_v = calcular_primer_vencimiento(fecha_obj, tarjeta.dia_cierre, tarjeta.dia_vencimiento, False)
        venc_mes = MESES_ES_GEN[primer_v.month - 1]
        venc_anio = primer_v.year
        cat_disp = _nombre_corto_categoria(entidades.get("categoria"))

        if cant_cuotas > 1:
            monto_cuota_fmt = formatear_monto(float(monto_cuota), tarjeta.moneda)
            msg_resp = f"Listo. {cant_cuotas} cuotas de {monto_cuota_fmt} en {cat_disp} con tarjeta {tarjeta.nombre} — registrado. Va a ingresar en el resumen de {venc_mes} {venc_anio}."
        else:
            total_fmt = formatear_monto(float(monto_total), tarjeta.moneda)
            msg_resp = f"Listo. 1 cuota de {total_fmt} en {cat_disp} con tarjeta {tarjeta.nombre} — registrado. Va a ingresar en el resumen de {venc_mes} {venc_anio}."

        return transaccion, msg_resp, False

    nombre_billetera = (
        entidades.get("billetera_destino")
        if tipo_val == "ingreso"
        else entidades.get("billetera_origen")
    )

    billetera_id = _resolver_billetera(nombre_billetera, usuario.id, db, moneda=moneda_solicitada)
    if not billetera_id:
        billetera_id = db.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == usuario.id,
                Billetera.estado == EstadoBilletera.ACTIVA,
                Billetera.moneda == moneda_solicitada,
                Billetera.es_principal == True,
            ).order_by(Billetera.nombre.asc(), Billetera.id.asc())
        ).scalars().first()

    if not billetera_id:
        billeteras_moneda = _obtener_billeteras_activas(usuario.id, db, moneda=moneda_solicitada)
        if len(billeteras_moneda) == 1:
            billetera_id = billeteras_moneda[0].id

    if not billetera_id:
        return None, "No pude resolver la billetera.", False

    billetera = db.execute(
        select(Billetera).where(Billetera.id == billetera_id).with_for_update()
    ).scalars().first()

    if not billetera or billetera.moneda != moneda_solicitada:
        return None, "La moneda de la billetera no coincide.", False

    categoria_id, subcategoria_id = _resolver_categoria_y_subcategoria(
        entidades.get("categoria"), usuario.id, db, tipo=tipo_val
    )
    fecha_obj, _ = _resolver_y_validar_fecha(entidades.get("fecha"))

    desc_candidata = entidades.get("descripcion")
    desc_final = ai_service.sanitizar_descripcion(
        desc_candidata,
        mensaje_original=conv_previa.mensaje_usuario if conv_previa else None,
        tipo=tipo_val,
    )

    transaccion = Transaccion(
        usuario_id=usuario.id,
        tipo=TipoTransaccion.INGRESO if tipo_val == "ingreso" else TipoTransaccion.EGRESO,
        monto=monto_decimal,
        moneda=moneda_solicitada,
        fecha=fecha_obj,
        descripcion=desc_final or _nombre_corto_categoria(entidades.get("categoria")),
        metodo_pago=deducir_metodo_pago(billetera, tarjeta_id=None),
        billetera_id=billetera_id,
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id,
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_recurrente=False,
        es_cuota_hija=False,
        es_padre_cuotas=False,
    )
    db.add(transaccion)
    db.flush()

    if transaccion.tipo == TipoTransaccion.INGRESO:
        billetera.saldo_actual += monto_decimal
    else:
        billetera.saldo_actual -= monto_decimal

    presupuesto_service.registrar_impacto_presupuesto(db, transaccion, revertir=False, commit=False)

    adicionales = entidades.get("transacciones_adicionales")
    descartadas = []
    adicionales_registradas = []
    if adicionales and isinstance(adicionales, list):
        for adic in adicionales:
            if isinstance(adic, dict):
                tx_ad, motivo = _crear_transaccion_adicional(adic, usuario.id, billetera, db)
                if tx_ad:
                    adicionales_registradas.append(tx_ad)
                if motivo:
                    descartadas.append(motivo)

    # Marcar la conversación previa como ejecutada
    if conv_previa:
        conv_previa.accion_ejecutada = str(transaccion.id)
    emitir_evento_actualizacion(db, usuario.id, "transacciones")
    emitir_evento_actualizacion(db, usuario.id, "billeteras")
    db.flush()

    monto_str = formatear_monto(float(transaccion.monto), transaccion.moneda)
    bill_nombre = billetera.nombre

    if adicionales and isinstance(adicionales, list) and len(adicionales) > 0:
        total_registrados = 1 + len(adicionales_registradas)
        cat_display = _nombre_corto_categoria(entidades.get("categoria"))
        fecha_p_nat = _formatear_fecha_natural(transaccion.fecha)
        fecha_p_disp = f" ({fecha_p_nat})" if fecha_p_nat else ""
        items_str = [f"{monto_str} en {cat_display}{fecha_p_disp}"]
        for tx_ad in adicionales_registradas:
            fecha_ad_nat = _formatear_fecha_natural(tx_ad.fecha)
            fecha_ad_disp = f" ({fecha_ad_nat})" if fecha_ad_nat else ""
            items_str.append(
                f"{formatear_monto(float(tx_ad.monto), tx_ad.moneda)} en {_nombre_corto_categoria(tx_ad.descripcion)}{fecha_ad_disp}"
            )
        origen_str = f" desde {bill_nombre}" if bill_nombre else (f" a {bill_nombre}" if transaccion.tipo == TipoTransaccion.INGRESO else "")
        mov_palabra = "movimientos" if total_registrados != 1 else "movimiento"
        reg_palabra = "registrados" if total_registrados != 1 else "registrado"
        msg_resp = f"Listo. {total_registrados} {mov_palabra}{origen_str}: {', '.join(items_str)} — {reg_palabra}."
    else:
        cat_nombre = None
        if transaccion.categoria_id:
            cat = db.execute(select(Categoria).where(Categoria.id == transaccion.categoria_id)).scalars().first()
            cat_nombre = cat.nombre if cat else None
        subcat_nombre = None
        if transaccion.subcategoria_id:
            subcat = db.execute(select(Subcategoria).where(Subcategoria.id == transaccion.subcategoria_id)).scalars().first()
            subcat_nombre = subcat.nombre if subcat else None
        nombre_categoria_display = subcat_nombre or cat_nombre or _nombre_corto_categoria(entidades.get("categoria")) or "Otros"

        fecha_nat = _formatear_fecha_natural(transaccion.fecha)
        fecha_disp = f" ({fecha_nat})" if fecha_nat else ""

        if transaccion.tipo == TipoTransaccion.INGRESO:
            partes = [f"Listo. Ingreso de {monto_str}"]
            if nombre_categoria_display:
                partes.append(f"en {nombre_categoria_display}")
            if bill_nombre:
                partes.append(f"a {bill_nombre}{fecha_disp}")
            partes.append("— registrado.")
        else:
            partes = [f"Listo. {monto_str}"]
            if nombre_categoria_display:
                partes.append(f"en {nombre_categoria_display}")
            if bill_nombre:
                partes.append(f"desde {bill_nombre}{fecha_disp}")
            partes.append("— registrado.")
        msg_resp = " ".join(partes)

    if descartadas:
        msg_resp += "\n" + "\n".join(descartadas)

    # REGLA DE PRIVACIDAD: Los saldos no se muestran tras registrar un movimiento,
    # salvo que el usuario los pida explícitamente (privacidad de pantalla).
    if billetera.saldo_actual < 0:
        msg_resp += "\nLa billetera quedó en negativo."

    return transaccion, msg_resp, False


def _es_registro_directo(
    fila: ConversacionWpp,
    es_credito: bool = False,
    es_imagen: bool = False,
    es_duplicado: bool = False,
    se_asumio_principal: bool = False,
) -> bool:
    """
    Determina si una propuesta cumple TODAS las condiciones para registro directo (Decisión 1):
    - intent registrar_transaccion
    - confianza >= 0.85
    - sin slot filling pendiente
    - monto, billetera y categoría resueltos
    - medio de pago que no es tarjeta de crédito
    - sin sospecha de duplicado (temporal ni de lote)
    - mensaje que no es imagen
    - al menos un ítem válido
    Ante cualquier dato ausente o dudoso devuelve False.
    """
    if fila.intent_detectado != "registrar_transaccion":
        return False

    if fila.confianza is None or fila.confianza < Decimal("0.85"):
        return False

    if fila.slot_filling_activo:
        return False

    if es_imagen or fila.tipo_mensaje == TipoMensajeWpp.IMAGEN:
        return False

    if es_duplicado:
        return False

    entidades = fila.entidades or {}
    if not isinstance(entidades, dict):
        return False

    if bool(entidades.get("origen_imagen")) or bool(entidades.get("es_imagen")):
        return False
    if bool(entidades.get("confianza_baja")):
        return False

    if entidades.get("datos_faltantes"):
        return False

    tipo_flujo = entidades.get("tipo_flujo")
    if tipo_flujo in ("verificacion_duplicado", "verificacion_lote_duplicado", "verificacion_duplicado_suscripcion"):
        return False

    if tipo_flujo == "propuesta_credito":
        return False
    if es_credito or entidades.get("tarjeta_id") or entidades.get("tarjeta") or entidades.get("tarjeta_nombre"):
        return False
    if entidades.get("cantidad_cuotas") and int(entidades.get("cantidad_cuotas", 1)) > 1:
        return False
    if entidades.get("medio_pago") == "tarjeta_credito":
        return False
    if entidades.get("tipo_operacion") in ("transferencia", "extraccion", "compra_usd", "venta_usd"):
        return False
    if entidades.get("intent_origen") == "transferir_fondos":
        return False

    adicionales = entidades.get("transacciones_adicionales")
    operaciones = entidades.get("operaciones")
    es_lote = bool((adicionales and isinstance(adicionales, list) and len(adicionales) > 0) or (operaciones and isinstance(operaciones, list) and len(operaciones) > 0))

    if es_lote:
        items_a_validar = []
        if operaciones and isinstance(operaciones, list):
            items_a_validar = operaciones
        else:
            items_a_validar = [entidades] + (adicionales if isinstance(adicionales, list) else [])

        items_validos = 0
        for it in items_a_validar:
            if not isinstance(it, dict):
                continue
            m = it.get("monto")
            if m is None:
                continue
            try:
                if Decimal(str(m)) <= Decimal("0"):
                    continue
            except (ValueError, TypeError):
                continue
            bill = it.get("billetera") or it.get("billetera_origen") or it.get("billetera_destino") or entidades.get("billetera") or entidades.get("billetera_origen") or entidades.get("billetera_destino")
            if not bill:
                continue
            cat = it.get("categoria")
            if not cat:
                continue
            items_validos += 1

        return items_validos > 0

    monto = entidades.get("monto")
    if monto is None:
        return False
    try:
        if Decimal(str(monto)) <= Decimal("0"):
            return False
    except (ValueError, TypeError):
        return False

    billetera = entidades.get("billetera") or entidades.get("billetera_origen") or entidades.get("billetera_destino") or entidades.get("billetera_resuelta_nombre")
    if not billetera:
        return False

    categoria = entidades.get("categoria")
    if not categoria:
        return False

    return True


def _registrar_directo_si_corresponde(
    usuario: Usuario,
    db: Session,
    fila: ConversacionWpp,
    es_credito: bool = False,
    es_imagen: bool = False,
    es_duplicado: bool = False,
    se_asumio_principal: bool = False,
) -> tuple[bool, str]:
    """
    Ejecuta el registro directo sobre una propuesta recién guardada y flusheada
    si cumple todas las condiciones de _es_registro_directo.
    Retorna (registrado, mensaje_respuesta).
    """
    if not _es_registro_directo(
        fila,
        es_credito=es_credito,
        es_imagen=es_imagen,
        es_duplicado=es_duplicado,
        se_asumio_principal=se_asumio_principal,
    ):
        return False, fila.mensaje_bot or ""

    tx_creada, msg_resp, _ = _confirmar_propuesta_transaccion(usuario, db, propuesta_id=fila.id)

    descartes = []
    for l in (fila.mensaje_bot or "").split("\n"):
        l_s = l.strip()
        if l_s.startswith("No se pudo registrar") and l_s not in descartes:
            descartes.append(l_s)
    for l in msg_resp.split("\n"):
        l_s = l.strip()
        if l_s.startswith("No se pudo registrar") and l_s not in descartes:
            descartes.append(l_s)

    resto = [l for l in msg_resp.split("\n") if not l.strip().startswith("No se pudo registrar")]
    if descartes:
        msg_resp = "\n".join(descartes + resto)

    if not tx_creada:
        fila.accion_ejecutada = "error"
        fila.mensaje_bot = msg_resp
        db.flush()
        db.commit()
        return False, msg_resp

    asumio = se_asumio_principal or (bool(fila.entidades.get("_asumio_principal")) if fila.entidades else False)
    if asumio and "Si fue con otra, decime cuál." not in msg_resp:
        msg_resp += "\nSi fue con otra, decime cuál."

    if fila.entidades is not None:
        entidades_copy = dict(fila.entidades)
        entidades_copy["registro_directo"] = True
        fila.entidades = entidades_copy

    fila.mensaje_bot = msg_resp
    db.flush()
    db.commit()
    return True, msg_resp


FACTOR_MIN_COTIZACION_DOLAR = Decimal("0.40")
FACTOR_MAX_COTIZACION_DOLAR = Decimal("2.50")


def _confirmar_propuesta_transferencia(
    usuario: Usuario,
    db: Session,
    propuesta_id: UUID | None = None,
) -> tuple[TransferenciaInterna | None, str, bool]:
    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)

    stmt_conv = (
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "transferir_fondos",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite_tiempo,
        )
    )
    if propuesta_id:
        stmt_conv = stmt_conv.where(ConversacionWpp.id == propuesta_id)

    conv_previa = db.execute(
        stmt_conv.order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc()).with_for_update()
    ).scalars().first()

    if not conv_previa:
        limite_reciente = datetime.now(timezone.utc) - timedelta(minutes=10)
        candidatas = db.execute(
            select(ConversacionWpp)
            .where(
                ConversacionWpp.usuario_id == usuario.id,
                ConversacionWpp.intent_detectado == "transferir_fondos",
                ConversacionWpp.accion_ejecutada.is_not(None),
                ConversacionWpp.fecha >= limite_reciente,
            )
            .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().all()

        for c in candidatas:
            if c.accion_ejecutada and str(c.accion_ejecutada).startswith("transferencia:"):
                return None, "Esa operación ya fue confirmada.", True
        return None, "No tenés ninguna transferencia pendiente para confirmar.", False

    entidades = conv_previa.entidades or {}
    b_origen_id_str = entidades.get("billetera_origen_id")
    b_destino_id_str = entidades.get("billetera_destino_id")
    if not b_origen_id_str or not b_destino_id_str:
        return None, "No pude procesar la transferencia.", False

    b_orig_id = UUID(str(b_origen_id_str))
    b_dest_id = UUID(str(b_destino_id_str))
    monto = Decimal(str(entidades["monto"]))
    monto_origen = Decimal(str(entidades.get("monto_origen", monto)))
    monto_destino = Decimal(str(entidades.get("monto_destino", monto)))
    moneda_origen = Moneda.USD if entidades.get("moneda_origen") == "USD" else Moneda.ARS
    moneda_destino = Moneda.USD if entidades.get("moneda_destino") == "USD" else Moneda.ARS
    monto_comision = Decimal(str(entidades["monto_comision"])) if entidades.get("monto_comision") else None

    data_tr = TransferenciaInternaCreate(
        billetera_origen_id=b_orig_id,
        billetera_destino_id=b_dest_id,
        monto=monto_origen,
        moneda=moneda_origen,
        monto_origen=monto_origen,
        monto_destino=monto_destino,
        moneda_origen=moneda_origen,
        moneda_destino=moneda_destino,
        monto_comision=monto_comision,
        fecha=hoy_argentina(),
        notas=entidades.get("notas", "Transferencia por WhatsApp"),
    )

    try:
        tr = transferencia_service.crear_transferencia(db, usuario.id, data_tr, commit=False)
        conv_previa.accion_ejecutada = f"transferencia:{tr.id}"
        db.commit()
    except HTTPException as exc:
        db.rollback()
        return None, str(exc.detail), False
    except Exception as exc:
        db.rollback()
        logger.error(f"Error al confirmar transferencia: {exc}")
        return None, "Hubo un problema al procesar la transferencia.", False

    b_origen = db.get(Billetera, b_orig_id)
    b_destino = db.get(Billetera, b_dest_id)
    nom_orig = b_origen.nombre if b_origen else "origen"
    nom_dest = b_destino.nombre if b_destino else "destino"

    tipo_op = entidades.get("tipo_operacion", "transferencia")
    if tipo_op == "extraccion":
        msg_resp = f"Listo. Extracción de {formatear_monto(float(monto_origen), moneda_origen)} de {nom_orig} a {nom_dest} registrada."
    elif tipo_op == "compra_usd":
        d_str = formatear_monto(float(monto_destino), Moneda.USD).replace("USD", "").replace("US$", "").strip()
        msg_resp = f"Listo. Compra de USD {d_str} por {formatear_monto(float(monto_origen), Moneda.ARS)} registrada."
    elif tipo_op == "venta_usd":
        d_str = formatear_monto(float(monto_origen), Moneda.USD).replace("USD", "").replace("US$", "").strip()
        msg_resp = f"Listo. Venta de USD {d_str} por {formatear_monto(float(monto_destino), Moneda.ARS)} registrada."
    else:
        msg_resp = f"Listo. Transferí {formatear_monto(float(monto_origen), moneda_origen)} de {nom_orig} a {nom_dest}."

    return tr, msg_resp, False


def _confirmar_propuesta_aporte_meta(
    usuario: Usuario,
    db: Session,
    propuesta_id: UUID | None = None,
) -> tuple[MovimientoMeta | None, str, bool]:
    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)

    stmt_conv = (
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "aportar_meta",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite_tiempo,
        )
    )
    if propuesta_id:
        stmt_conv = stmt_conv.where(ConversacionWpp.id == propuesta_id)

    conv_previa = db.execute(
        stmt_conv.order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc()).with_for_update()
    ).scalars().first()

    if not conv_previa:
        limite_reciente = datetime.now(timezone.utc) - timedelta(minutes=10)
        candidatas = db.execute(
            select(ConversacionWpp)
            .where(
                ConversacionWpp.usuario_id == usuario.id,
                ConversacionWpp.intent_detectado == "aportar_meta",
                ConversacionWpp.accion_ejecutada.is_not(None),
                ConversacionWpp.fecha >= limite_reciente,
            )
            .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        ).scalars().all()

        for c in candidatas:
            if c.accion_ejecutada and str(c.accion_ejecutada).startswith("aporte_meta:"):
                return None, "Esa operación ya fue confirmada.", True
        return None, "No tenés ningún aporte a meta pendiente para confirmar.", False

    entidades = conv_previa.entidades or {}
    meta_id_str = entidades.get("meta_id")
    billetera_id_str = entidades.get("billetera_id")
    monto_val = entidades.get("monto")
    moneda_str = entidades.get("moneda", "ARS")

    if not meta_id_str or not billetera_id_str or not monto_val:
        return None, "No pude procesar el aporte a la meta.", False

    meta_id = UUID(str(meta_id_str))
    billetera_id = UUID(str(billetera_id_str))
    monto = Decimal(str(monto_val))
    moneda_mov = Moneda.USD if moneda_str == "USD" else Moneda.ARS

    meta = db.get(Meta, meta_id)
    if not meta or meta.usuario_id != usuario.id:
        return None, "No encontré la meta seleccionada.", False

    billetera = db.get(Billetera, billetera_id)
    if not billetera or billetera.usuario_id != usuario.id:
        return None, "No encontré la billetera seleccionada.", False

    data_mov = MovimientoMetaCreate(
        tipo=TipoMovimientoMeta.APORTE,
        monto=monto,
        moneda_movimiento=moneda_mov,
        billetera_id=billetera_id,
        fecha=hoy_argentina(),
    )

    try:
        nuevo_mov = meta_service.registrar_movimiento(db, usuario.id, meta_id, data_mov)
        conv_previa.accion_ejecutada = f"aporte_meta:{nuevo_mov.id}"
        emitir_evento_actualizacion(db, usuario.id, "metas")
        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "billeteras")
        db.commit()
    except HTTPException as exc:
        db.rollback()
        return None, str(exc.detail), False
    except Exception as exc:
        db.rollback()
        logger.error(f"Error al confirmar aporte a meta: {exc}")
        return None, "Hubo un problema al procesar el aporte a la meta.", False

    monto_fmt = formatear_monto(float(monto), moneda_mov)
    if meta.monto_actual >= meta.monto_objetivo:
        msg_resp = f"Listo. Aporté {monto_fmt} a tu meta '{meta.nombre}'. ¡Felicitaciones! Completaste tu meta."
    else:
        msg_resp = f"Listo. Aporté {monto_fmt} a tu meta '{meta.nombre}' desde {billetera.nombre}."

    return nuevo_mov, msg_resp, False


def manejar_confirmacion_aporte_meta(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    if not _es_confirmacion(mensaje_texto):
        return False

    propuesta = _buscar_propuesta_confirmable_mas_reciente(usuario.id, db)
    if not propuesta or propuesta.intent_detectado != "aportar_meta":
        return False

    mov_creado, msg_confirm, ya_conf = _confirmar_propuesta_aporte_meta(usuario, db)
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_confirm,
        intent_detectado="confirmar_aporte_meta",
        entidades={},
        accion_ejecutada=f"aporte_meta:{mov_creado.id}" if mov_creado else ("ya_confirmado" if ya_conf else None),
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    enviar_whatsapp(from_number, msg_confirm)
    return True


def manejar_cancelacion_aporte_meta(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    if not _es_cancelacion(mensaje_texto):
        return False

    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    propuesta = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "aportar_meta",
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite_tiempo,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

    if not propuesta:
        return False

    propuesta.accion_ejecutada = "cancelada"
    propuesta.slot_filling_activo = False

    msg_cancel = "Listo, cancelado."
    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_cancel,
        intent_detectado="cancelar",
        entidades={},
        accion_ejecutada="cancelada",
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    enviar_whatsapp(from_number, msg_cancel)
    return True


def manejar_aporte_meta(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
    conv_activa: ConversacionWpp | None = None,
    estado_previo: dict | None = None,
) -> bool:
    """
    Detección determinística o procesamiento de aporte a meta de ahorro.
    Siempre pide confirmación antes de ejecutar.
    """
    billeteras_usuario = _obtener_billeteras_activas(usuario.id, db)

    # 1. Si hay un slot-filling activo de meta esperando elegir cuál (ambigüedad)
    if estado_previo and estado_previo.get("intent_origen") == "aportar_meta":
        if "meta_ambigua_cands" in estado_previo:
            cands_ids = estado_previo.get("meta_ambigua_cands", [])
            metas_cands = db.query(Meta).filter(Meta.id.in_([UUID(cid) for cid in cands_ids])).all()
            meta_elegida = None
            if mensaje_texto.strip().isdigit():
                idx = int(mensaje_texto.strip()) - 1
                if 0 <= idx < len(metas_cands):
                    meta_elegida = metas_cands[idx]
            else:
                m_txt_norm = normalizar_texto(mensaje_texto)
                for mc in metas_cands:
                    mc_norm = normalizar_texto(mc.nombre)
                    if m_txt_norm in mc_norm or mc_norm in m_txt_norm:
                        meta_elegida = mc
                        break

            if meta_elegida:
                monto = Decimal(str(estado_previo["monto"]))
                bill_id_str = estado_previo.get("billetera_id")
                billetera = db.get(Billetera, UUID(bill_id_str)) if bill_id_str else None
                if not billetera:
                    bills_mon = [b for b in billeteras_usuario if b.moneda == meta_elegida.moneda]
                    billetera = next((b for b in bills_mon if b.es_principal), None) or (bills_mon[0] if len(bills_mon) == 1 else None)

                if conv_activa:
                    conv_activa.slot_filling_activo = False
                    db.flush()

                if not billetera:
                    bills_mon = [b for b in billeteras_usuario if b.moneda == meta_elegida.moneda]
                    enc = f"¿Desde qué billetera querés aportar a '{meta_elegida.nombre}'?"
                    pregunta = _generar_menu_billeteras(bills_mon, tipo="egreso", encabezado=enc)
                    entidades_sf = {
                        "intent_origen": "aportar_meta",
                        "meta_id": str(meta_elegida.id),
                        "meta_nombre": meta_elegida.nombre,
                        "monto": float(monto),
                        "moneda": meta_elegida.moneda.value,
                    }
                    nueva_conv = ConversacionWpp(
                        usuario_id=usuario.id,
                        wamid=wamid,
                        mensaje_usuario=mensaje_texto,
                        tipo_mensaje=TipoMensajeWpp.TEXTO,
                        transcripcion=None,
                        mensaje_bot=pregunta,
                        intent_detectado="aportar_meta",
                        entidades=entidades_sf,
                        accion_ejecutada=None,
                        confianza=Decimal("1.000"),
                        slot_filling_activo=True,
                        slot_filling_estado=entidades_sf,
                    )
                    db.add(nueva_conv)
                    db.commit()
                    enviar_whatsapp(from_number, pregunta)
                    return True

                monto_fmt = formatear_monto(float(monto), meta_elegida.moneda)
                prop = f"Voy a aportar {monto_fmt} a tu meta '{meta_elegida.nombre}' desde {billetera.nombre}. ¿Confirmás?"
                entidades_prop = {
                    "meta_id": str(meta_elegida.id),
                    "meta_nombre": meta_elegida.nombre,
                    "billetera_id": str(billetera.id),
                    "billetera_nombre": billetera.nombre,
                    "monto": float(monto),
                    "moneda": meta_elegida.moneda.value,
                }
                nueva_conv = ConversacionWpp(
                    usuario_id=usuario.id,
                    wamid=wamid,
                    mensaje_usuario=mensaje_texto,
                    tipo_mensaje=TipoMensajeWpp.TEXTO,
                    transcripcion=None,
                    mensaje_bot=prop,
                    intent_detectado="aportar_meta",
                    entidades=entidades_prop,
                    accion_ejecutada=None,
                    confianza=Decimal("1.000"),
                    slot_filling_activo=False,
                    slot_filling_estado=None,
                )
                db.add(nueva_conv)
                db.commit()
                enviar_whatsapp(from_number, prop)
                return True

    # Parsear mensaje para aporte a meta
    m_norm = normalizar_texto(mensaje_texto)
    signals = ["meta", "metas", "aporte", "aportar", "aporta"]
    tiene_senal = any(s in m_norm for s in signals) or bool(re.search(r"\b(?:puse|guarde|separe|mande|meter)\b.*\b(?:meta|para la|a la)\b", m_norm))
    if not tiene_senal:
        return False

    # Extraer monto
    m_monto = re.search(r"(\$?\s*\d+(?:[.,]\d+)?(?:\s*(?:mil|k))?)\b", m_norm)
    if not m_monto:
        return False
    monto_val = _parsear_monto_argentino(m_monto.group(1))
    if not monto_val or monto_val <= Decimal("0"):
        return False

    # Extraer billetera mencionada
    billetera_encontrada = None
    for b in billeteras_usuario:
        b_low = normalizar_texto(b.nombre)
        if re.search(rf"\b(?:desde|con|en|de)\s+{re.escape(b_low)}\b", m_norm):
            billetera_encontrada = b
            break
        elif re.search(rf"\b{re.escape(b_low)}\b", m_norm) and b_low not in ("pesos", "dolares", "dólares", "efectivo"):
            if any(p in m_norm for p in [f"con {b_low}", f"desde {b_low}", f"por {b_low}"]):
                billetera_encontrada = b
                break

    # Extraer nombre de meta
    clean_text = mensaje_texto
    if billetera_encontrada:
        clean_text = re.sub(rf"\b(?:desde|con|en|de)\s+{re.escape(billetera_encontrada.nombre)}\b", "", clean_text, flags=re.IGNORECASE)
        clean_text = re.sub(rf"\b{re.escape(billetera_encontrada.nombre)}\b", "", clean_text, flags=re.IGNORECASE)

    m_meta = re.search(r"\b(?:a|para)\s+(?:la\s+|mi\s+)?meta\s+([a-zA-Z0-9_áéíóúÁÉÍÓÚñÑ\s]+)", clean_text, re.IGNORECASE)
    meta_name = None
    if m_meta:
        meta_name = m_meta.group(1).strip()
    else:
        m_meta2 = re.search(r"\b(?:aportar|aporte|aporta|aporté)\s+.*?\s+(?:a|para)\s+([a-zA-Z0-9_áéíóúÁÉÍÓÚñÑ\s]+)", clean_text, re.IGNORECASE)
        if m_meta2:
            cand = m_meta2.group(1).strip()
            cand = re.sub(r"^(?:mi|la|el)\s+", "", cand, flags=re.IGNORECASE).strip()
            meta_name = cand

    if meta_name:
        meta_name = re.sub(r"[.,;:!?]+$", "", meta_name).strip()
        meta_name = re.sub(r"\$?\s*\d+(?:[.,]\d+)?(?:\s*(?:mil|k))?", "", meta_name).strip()

    # Resolver meta
    meta, estado_meta, cands = _buscar_meta_activa_por_nombre(usuario.id, meta_name, db)

    if estado_meta == "no_metas":
        msg_resp = "No tenés metas activas."
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="aportar_meta",
            entidades={},
            accion_ejecutada="sin_efecto",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        enviar_whatsapp(from_number, msg_resp)
        return True

    if estado_meta == "no_encontrada":
        nombre_disp = meta_name or "esa"
        msg_resp = f"No encontré ninguna meta con el nombre '{nombre_disp}'. Podés consultar tus metas con 'mis metas'."
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="aportar_meta",
            entidades={},
            accion_ejecutada="sin_efecto",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        enviar_whatsapp(from_number, msg_resp)
        return True

    if estado_meta == "ambigua":
        nombres = ", ".join(f"'{m.nombre}'" for m in cands)
        msg_resp = f"Encontré más de una meta parecida: {nombres}. ¿A cuál querés aportar?"
        entidades_sf = {
            "intent_origen": "aportar_meta",
            "meta_ambigua_cands": [str(m.id) for m in cands],
            "monto": float(monto_val),
            "billetera_id": str(billetera_encontrada.id) if billetera_encontrada else None,
        }
        if conv_activa:
            conv_activa.slot_filling_activo = False
            db.flush()
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="aportar_meta",
            entidades=entidades_sf,
            accion_ejecutada=None,
            confianza=Decimal("1.000"),
            slot_filling_activo=True,
            slot_filling_estado=entidades_sf,
        )
        db.add(nueva_conv)
        db.commit()
        enviar_whatsapp(from_number, msg_resp)
        return True

    # Resolver billetera de origen en la moneda de la meta
    bills_mon = [b for b in billeteras_usuario if b.moneda == meta.moneda]
    billetera = billetera_encontrada
    if not billetera:
        billetera = next((b for b in bills_mon if b.es_principal), None) or (bills_mon[0] if len(bills_mon) == 1 else None)

    if not billetera:
        enc = f"¿Desde qué billetera querés aportar a '{meta.nombre}'?"
        pregunta = _generar_menu_billeteras(bills_mon, tipo="egreso", encabezado=enc)
        entidades_sf = {
            "intent_origen": "aportar_meta",
            "meta_id": str(meta.id),
            "meta_nombre": meta.nombre,
            "monto": float(monto_val),
            "moneda": meta.moneda.value,
        }
        if conv_activa:
            conv_activa.slot_filling_activo = False
            db.flush()
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=pregunta,
            intent_detectado="aportar_meta",
            entidades=entidades_sf,
            accion_ejecutada=None,
            confianza=Decimal("1.000"),
            slot_filling_activo=True,
            slot_filling_estado=entidades_sf,
        )
        db.add(nueva_conv)
        db.commit()
        enviar_whatsapp(from_number, pregunta)
        return True

    # Validar saldo suficiente
    if billetera.saldo_actual < monto_val:
        monto_disp = formatear_monto(float(billetera.saldo_actual), billetera.moneda)
        msg_resp = f"Saldo insuficiente en la billetera '{billetera.nombre}'. Tenés {monto_disp}."
        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=wamid,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg_resp,
            intent_detectado="aportar_meta",
            entidades={},
            accion_ejecutada="sin_efecto",
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()
        enviar_whatsapp(from_number, msg_resp)
        return True

    # Armar propuesta de confirmación
    monto_fmt = formatear_monto(float(monto_val), meta.moneda)
    prop = f"Voy a aportar {monto_fmt} a tu meta '{meta.nombre}' desde {billetera.nombre}. ¿Confirmás?"
    entidades_prop = {
        "meta_id": str(meta.id),
        "meta_nombre": meta.nombre,
        "billetera_id": str(billetera.id),
        "billetera_nombre": billetera.nombre,
        "monto": float(monto_val),
        "moneda": meta.moneda.value,
    }

    if conv_activa:
        conv_activa.slot_filling_activo = False
        db.flush()

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=prop,
        intent_detectado="aportar_meta",
        entidades=entidades_prop,
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    enviar_whatsapp(from_number, prop)
    return True


def _interpretar_transferencia(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    estado_previo: dict | None = None,
) -> tuple[bool, str | None, dict | None, str | None]:
    """
    Interpreta determinísticamente transferencias entre cuentas propias, extracciones de cajero
    y compra/venta de dólares (Punto 9B).
    Retorna: (es_transferencia, tipo_o_estado, entidades, respuesta_o_pregunta)
    """
    billeteras_usuario = _obtener_billeteras_activas(usuario.id, db)
    m_norm = normalizar_texto(mensaje_texto)

    # Si es señal de suscripción, no interpretar como transferencia/dólares
    if not estado_previo:
        if _es_senial_suscripcion(mensaje_texto) or (_extraer_frecuencia_mencionada(mensaje_texto) and _extraer_nombre_servicio(mensaje_texto)):
            return False, None, None, None

    # Manejo de slot-filling previo para transferir_fondos
    if estado_previo and (estado_previo.get("intent_origen") == "transferir_fondos" or estado_previo.get("tipo_operacion") in ("compra_usd", "venta_usd", "transferencia", "extraccion")):
        tipo_op = estado_previo.get("tipo_operacion")

        # 1. Cotización pendiente para compra/venta dólares
        if tipo_op in ("compra_usd", "venta_usd") and "cotizacion" in estado_previo.get("datos_faltantes", []):
            m_cot = _parsear_monto_argentino(mensaje_texto)
            if not m_cot:
                m_num = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\b", m_norm)
                if m_num:
                    m_cot = _parsear_monto_argentino(m_num.group(1))
            if m_cot:
                dolares = Decimal(str(estado_previo["monto_usd"]))
                # Candidato 1: m_cot interpretado como cotización unitaria
                c1_cotiz = m_cot
                c1_pesos = (dolares * c1_cotiz).quantize(Decimal("0.01"))

                # Candidato 2: m_cot interpretado como monto total en pesos
                c2_pesos = m_cot
                c2_cotiz = (c2_pesos / dolares).quantize(Decimal("0.01"))

                cot_ref = _obtener_cotizacion_referencia_usuario(usuario, db)

                if cot_ref is None:
                    # Sin cotización de referencia: preguntar al usuario mostrando ambas opciones
                    c1_c_str = formatear_monto(float(c1_cotiz), Moneda.ARS)
                    c1_p_str = formatear_monto(float(c1_pesos), Moneda.ARS)
                    c2_p_str = formatear_monto(float(c2_pesos), Moneda.ARS)
                    c2_c_str = formatear_monto(float(c2_cotiz), Moneda.ARS)
                    pregunta = (
                        f"¿Te referís a una cotización de {c1_c_str} por dólar (total {c1_p_str}) "
                        f"o a un total de {c2_p_str} ({c2_c_str} por dólar)?"
                    )
                    return True, "slot_filling", estado_previo, pregunta

                # Con cotización de referencia: evaluar plausibilidad y cercanía
                rango_min = (cot_ref * FACTOR_MIN_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                rango_max = (cot_ref * FACTOR_MAX_COTIZACION_DOLAR).quantize(Decimal("0.01"))

                c1_valida = (rango_min <= c1_cotiz <= rango_max)
                c2_valida = (rango_min <= c2_cotiz <= rango_max)

                d1 = abs(c1_cotiz - cot_ref)
                d2 = abs(c2_cotiz - cot_ref)

                if c1_valida and c2_valida:
                    # Ambas plausibles: si están demasiado cerca en distancia relativa, preguntar
                    umbral_ambiguedad = cot_ref * Decimal("0.25")
                    if abs(d1 - d2) < umbral_ambiguedad:
                        c1_c_str = formatear_monto(float(c1_cotiz), Moneda.ARS)
                        c1_p_str = formatear_monto(float(c1_pesos), Moneda.ARS)
                        c2_p_str = formatear_monto(float(c2_pesos), Moneda.ARS)
                        c2_c_str = formatear_monto(float(c2_cotiz), Moneda.ARS)
                        pregunta = (
                            f"¿Te referís a una cotización de {c1_c_str} por dólar (total {c1_p_str}) "
                            f"o a un total de {c2_p_str} ({c2_c_str} por dólar)?"
                        )
                        return True, "slot_filling", estado_previo, pregunta
                    if d1 <= d2:
                        cotiz = c1_cotiz
                        pesos = c1_pesos
                    else:
                        cotiz = c2_cotiz
                        pesos = c2_pesos
                elif c1_valida and not c2_valida:
                    cotiz = c1_cotiz
                    pesos = c1_pesos
                elif c2_valida and not c1_valida:
                    cotiz = c2_cotiz
                    pesos = c2_pesos
                else:
                    # Fuera de rango plausible
                    candidato_elegido = c1_cotiz if d1 <= d2 else c2_cotiz
                    c_str = str(int(candidato_elegido)) if candidato_elegido == int(candidato_elegido) else str(candidato_elegido)
                    ref_str = str(int(cot_ref)) if cot_ref == int(cot_ref) else str(cot_ref)
                    return (
                        True,
                        "absurda",
                        {},
                        f"La cotización de ${c_str} por dólar no parece razonable (la cotización de referencia es de ${ref_str}). Por favor verificá el valor e intentá de nuevo.",
                    )

                usd_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.USD and w.estado == EstadoBilletera.ACTIVA]
                ars_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and w.estado == EstadoBilletera.ACTIVA]
                b_usd = usd_wallets[0] if usd_wallets else None
                b_ars = next((w for w in ars_wallets if not w.es_efectivo and w.es_principal), (ars_wallets[0] if ars_wallets else None))

                if not b_usd or not b_ars:
                    return True, "error", {}, "No se encontraron las billeteras necesarias para operar en dólares."

                cotiz_fmt = formatear_monto(float(cotiz), Moneda.ARS)
                pesos_fmt = formatear_monto(float(pesos), Moneda.ARS)
                dolares_str = f"{int(dolares)}" if dolares == int(dolares) else f"{dolares:g}"

                if tipo_op == "compra_usd":
                    prop = f"Voy a registrar una compra de USD {dolares_str} a {cotiz_fmt}: salen {pesos_fmt} de {b_ars.nombre} y entran USD {dolares_str} a {b_usd.nombre}. ¿Confirmás?"
                    entidades = {
                        "tipo_operacion": "compra_usd",
                        "billetera_origen_id": str(b_ars.id),
                        "billetera_destino_id": str(b_usd.id),
                        "monto": float(pesos),
                        "monto_origen": float(pesos),
                        "monto_destino": float(dolares),
                        "moneda_origen": "ARS",
                        "moneda_destino": "USD",
                        "cotizacion": float(cotiz),
                    }
                else:
                    prop = f"Voy a registrar una venta de USD {dolares_str} a {cotiz_fmt}: salen USD {dolares_str} de {b_usd.nombre} y entran {pesos_fmt} a {b_ars.nombre}. ¿Confirmás?"
                    entidades = {
                        "tipo_operacion": "venta_usd",
                        "billetera_origen_id": str(b_usd.id),
                        "billetera_destino_id": str(b_ars.id),
                        "monto": float(dolares),
                        "monto_origen": float(dolares),
                        "monto_destino": float(pesos),
                        "moneda_origen": "USD",
                        "moneda_destino": "ARS",
                        "cotizacion": float(cotiz),
                    }
                return True, "propuesta", entidades, prop

        # 2. Billetera origen pendiente para transferencias
        if "billetera_origen" in estado_previo.get("datos_faltantes", []):
            b_dest_id_str = estado_previo.get("billetera_destino_id")
            b_dest = next((w for w in billeteras_usuario if str(w.id) == b_dest_id_str), None)
            cands = [w for w in billeteras_usuario if w.moneda == (b_dest.moneda if b_dest else Moneda.ARS) and str(w.id) != b_dest_id_str and w.estado == EstadoBilletera.ACTIVA]
            b_elegida = None
            if mensaje_texto.strip().isdigit():
                idx = int(mensaje_texto.strip()) - 1
                if 0 <= idx < len(cands):
                    b_elegida = cands[idx]
            else:
                b_match, _ = resolver_billetera_cascada(mensaje_texto.strip(), cands)
                if b_match:
                    b_elegida = b_match

            if b_elegida and b_dest:
                if b_elegida.id == b_dest.id:
                    return True, "misma_billetera", {}, "La billetera de origen y destino no pueden ser la misma."
                monto = Decimal(str(estado_previo["monto"]))
                monto_fmt = formatear_monto(float(monto), b_elegida.moneda)
                prop = f"Voy a transferir {monto_fmt} de {b_elegida.nombre} a {b_dest.nombre}. ¿Confirmás?"
                entidades = {
                    "tipo_operacion": "transferencia",
                    "billetera_origen_id": str(b_elegida.id),
                    "billetera_destino_id": str(b_dest.id),
                    "billetera_origen": b_elegida.nombre,
                    "billetera_destino": b_dest.nombre,
                    "monto": float(monto),
                    "monto_origen": float(monto),
                    "monto_destino": float(monto),
                    "moneda_origen": b_elegida.moneda.value,
                    "moneda_destino": b_dest.moneda.value,
                }
                return True, "propuesta", entidades, prop

    # 1. EXTRACCIÓN DE CAJERO
    if (re.search(r"\b(?:saque|retire|extraje|fui\s+al)\b.*\b(?:cajero|banco)\b", m_norm) or
        re.search(r"\bsaque\s+del\s+cajero\b", m_norm) or
        re.search(r"\bsaque\s+(?:plata|dinero)\b", m_norm) or
        re.search(r"\bextraccion(?:\s+de\s+cajero)?\b", m_norm)):

        m_num = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\b", m_norm)
        monto = _parsear_monto_argentino(m_num.group(1)) if m_num else None

        comision = None
        m_com = re.search(r"(?:con|mas)\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s+(?:de\s+)?comision", m_norm)
        if m_com:
            comision = _parsear_monto_argentino(m_com.group(1))

        cash_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and w.es_efectivo and w.estado == EstadoBilletera.ACTIVA]
        if not cash_wallets:
            return True, "no_cash", {}, "No tenés ninguna billetera de efectivo en pesos. Podés crearla desde la web de Argentum."

        b_dest = cash_wallets[0] if len(cash_wallets) == 1 else None

        non_cash = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and not w.es_efectivo and w.estado == EstadoBilletera.ACTIVA]
        b_orig = None
        for w in non_cash:
            if normalizar_texto(w.nombre) in m_norm:
                b_orig = w
                break
        if not b_orig:
            ppal = next((w for w in non_cash if w.es_principal), None)
            if ppal:
                b_orig = ppal
            elif len(non_cash) == 1:
                b_orig = non_cash[0]

        if not b_orig or not b_dest or not monto:
            cands_banco = [w for w in non_cash if w.estado == EstadoBilletera.ACTIVA]
            menu = _generar_menu_billeteras(cands_banco, tipo="egreso")
            return True, "slot_filling", {"intent_origen": "transferir_fondos", "tipo_operacion": "extraccion", "monto": float(monto) if monto else None, "datos_faltantes": ["billetera_origen"]}, f"¿De qué cuenta bancaria sale la plata?\n{menu}"

        monto_fmt = formatear_monto(float(monto), Moneda.ARS)
        if comision:
            com_fmt = formatear_monto(float(comision), Moneda.ARS)
            msg = f"Voy a registrar una extracción de {monto_fmt} de {b_orig.nombre} a {b_dest.nombre} (con {com_fmt} de comisión). ¿Confirmás?"
        else:
            msg = f"Voy a registrar una extracción de {monto_fmt} de {b_orig.nombre} a {b_dest.nombre}. ¿Confirmás?"

        entidades = {
            "tipo_operacion": "extraccion",
            "billetera_origen_id": str(b_orig.id),
            "billetera_destino_id": str(b_dest.id),
            "billetera_origen": b_orig.nombre,
            "billetera_destino": b_dest.nombre,
            "monto": float(monto),
            "monto_origen": float(monto),
            "monto_destino": float(monto),
            "moneda_origen": "ARS",
            "moneda_destino": "ARS",
            "monto_comision": float(comision) if comision else None,
        }
        return True, "propuesta", entidades, msg

    # 2. COMPRA Y VENTA DE DÓLARES
    if re.search(r"\b(?:d[oó]lares|verdes|usd|dolarice|cambie\s+pesos\s+a\s+d[oó]lares|cambie\s+d[oó]lares\s+a\s+pesos)\b", m_norm) and not any(w in m_norm for w in ["gaste", "pague", "compre una", "compre un"]):
        es_venta = bool(re.search(r"\b(?:vendi|cambie\s+d[oó]lares\s+a\s+pesos|vender)\b", m_norm))
        es_compra = not es_venta

        usd_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.USD and w.estado == EstadoBilletera.ACTIVA]
        if not usd_wallets:
            return True, "no_usd", {}, "No tenés ninguna billetera en dólares. Podés crearla desde la web de Argentum."
        b_usd = usd_wallets[0]

        ars_wallets = [w for w in billeteras_usuario if w.moneda == Moneda.ARS and w.estado == EstadoBilletera.ACTIVA]
        if not ars_wallets:
            return True, "error", {}, "No tenés ninguna billetera en pesos."
        b_ars = next((w for w in ars_wallets if not w.es_efectivo and w.es_principal), ars_wallets[0])

        m_cot = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s*(?:d[oó]lares|verdes|usd)\s+a\s+(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)", m_norm)
        if m_cot:
            dolares = _parsear_monto_argentino(m_cot.group(1))
            cotiz = _parsear_monto_argentino(m_cot.group(2))
            cot_ref = _obtener_cotizacion_referencia_usuario(usuario, db)
            if cot_ref is not None:
                rango_min = (cot_ref * FACTOR_MIN_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                rango_max = (cot_ref * FACTOR_MAX_COTIZACION_DOLAR).quantize(Decimal("0.01"))
                if cotiz < rango_min or cotiz > rango_max:
                    c_str = str(int(cotiz)) if cotiz == int(cotiz) else str(cotiz)
                    ref_str = str(int(cot_ref)) if cot_ref == int(cot_ref) else str(cot_ref)
                    return (
                        True,
                        "absurda",
                        {},
                        f"La cotización de ${c_str} por dólar no parece razonable (la cotización de referencia es de ${ref_str}). Por favor verificá el valor e intentá de nuevo.",
                    )
            pesos = (dolares * cotiz).quantize(Decimal("0.01"))

            cotiz_fmt = formatear_monto(float(cotiz), Moneda.ARS)
            pesos_fmt = formatear_monto(float(pesos), Moneda.ARS)
            dolares_str = f"{int(dolares)}" if dolares == int(dolares) else f"{dolares:g}"

            if es_compra:
                prop = f"Voy a registrar una compra de USD {dolares_str} a {cotiz_fmt}: salen {pesos_fmt} de {b_ars.nombre} y entran USD {dolares_str} a {b_usd.nombre}. ¿Confirmás?"
                entidades = {
                    "tipo_operacion": "compra_usd",
                    "billetera_origen_id": str(b_ars.id),
                    "billetera_destino_id": str(b_usd.id),
                    "monto": float(pesos),
                    "monto_origen": float(pesos),
                    "monto_destino": float(dolares),
                    "moneda_origen": "ARS",
                    "moneda_destino": "USD",
                    "cotizacion": float(cotiz),
                }
            else:
                prop = f"Voy a registrar una venta de USD {dolares_str} a {cotiz_fmt}: salen USD {dolares_str} de {b_usd.nombre} y entran {pesos_fmt} a {b_ars.nombre}. ¿Confirmás?"
                entidades = {
                    "tipo_operacion": "venta_usd",
                    "billetera_origen_id": str(b_usd.id),
                    "billetera_destino_id": str(b_ars.id),
                    "monto": float(dolares),
                    "monto_origen": float(dolares),
                    "monto_destino": float(pesos),
                    "moneda_origen": "USD",
                    "moneda_destino": "ARS",
                    "cotizacion": float(cotiz),
                }
            return True, "propuesta", entidades, prop
        else:
            m_d = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k)?)\s*(?:d[oó]lares|verdes|usd)", m_norm)
            if m_d:
                dolares = _parsear_monto_argentino(m_d.group(1))
                pregunta = "¿A qué cotización compraste o cuántos pesos pagaste?" if es_compra else "¿A qué cotización vendiste o cuántos pesos recibiste?"
                entidades = {
                    "intent_origen": "transferir_fondos",
                    "tipo_operacion": "compra_usd" if es_compra else "venta_usd",
                    "monto_usd": float(dolares),
                    "datos_faltantes": ["cotizacion"]
                }
                return True, "slot_filling", entidades, pregunta

    # 3. TRANSFERENCIAS ENTRE CUENTAS PROPIAS
    # Exclusión explícita de terceros: gastos a otra persona
    if (re.search(r"\b(?:le\s+transfer[ií]|le\s+mand[eé]|le\s+pas[eé]|le\s+pagu[eé])\b", m_norm) or
        re.search(r"\b(?:a\s+mi\s+hermano|a\s+mi\s+hermana|a\s+mi\s+mam[aá]|a\s+mi\s+pap[aá]|a\s+un\s+amigo|a\s+juan|a\s+pedro)\b", m_norm)):
        return False, "gasto_tercero", {}, None

    if re.search(r"\b(?:pas[eé]|transfer[ií]|me\s+transfer[ií]|mand[eé]|mov[ií]|cargu[eé]|mande\s+plata|pase\s+plata)\b", m_norm):
        if "sube" in m_norm:
            return False, "gasto_sube", {}, None

        m_num = re.search(r"(\$?\s*[0-9]+(?:[.,][0-9]+)?(?:\s*mil|\s*k|\s*lucas?|\s*palos?)?)\b", m_norm)
        monto = _parsear_monto_argentino(m_num.group(1)) if m_num else None

        # Patrón "de X a Y"
        m_de_a = re.search(r"de\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+?)\s+a\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+)", m_norm)
        b_orig = None
        b_dest = None
        if m_de_a:
            nom_orig = m_de_a.group(1).strip()
            nom_dest = m_de_a.group(2).strip()
            b_orig, _ = resolver_billetera_cascada(nom_orig, billeteras_usuario)
            b_dest, _ = resolver_billetera_cascada(nom_dest, billeteras_usuario)
        else:
            # Patrón "a Y"
            m_a = re.search(r"a\s+([a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+)", m_norm)
            if m_a:
                nom_dest = m_a.group(1).strip()
                b_dest, _ = resolver_billetera_cascada(nom_dest, billeteras_usuario)
                if b_dest:
                    ppal = next((w for w in billeteras_usuario if w.es_principal and w.moneda == b_dest.moneda and w.id != b_dest.id), None)
                    if ppal:
                        b_orig = ppal

        # Si no hubo coincidencia con ninguna billetera del usuario, es un gasto hacia un tercero
        if not b_dest and not m_de_a:
            return False, "no_es_transferencia_propia", {}, None

        if b_orig and b_dest and b_orig.id == b_dest.id:
            return True, "misma_billetera", {}, "La billetera de origen y destino no pueden ser la misma."

        if b_orig and b_dest and monto:
            monto_fmt = formatear_monto(float(monto), b_orig.moneda)
            prop = f"Voy a transferir {monto_fmt} de {b_orig.nombre} a {b_dest.nombre}. ¿Confirmás?"
            entidades = {
                "tipo_operacion": "transferencia",
                "billetera_origen_id": str(b_orig.id),
                "billetera_destino_id": str(b_dest.id),
                "billetera_origen": b_orig.nombre,
                "billetera_destino": b_dest.nombre,
                "monto": float(monto),
                "monto_origen": float(monto),
                "monto_destino": float(monto),
                "moneda_origen": b_orig.moneda.value,
                "moneda_destino": b_dest.moneda.value,
            }
            return True, "propuesta", entidades, prop

        if b_dest and monto and not b_orig:
            entidades = {
                "intent_origen": "transferir_fondos",
                "tipo_operacion": "transferencia",
                "billetera_destino_id": str(b_dest.id),
                "billetera_destino": b_dest.nombre,
                "monto": float(monto),
                "datos_faltantes": ["billetera_origen"]
            }
            cands = [w for w in billeteras_usuario if w.moneda == b_dest.moneda and w.id != b_dest.id and w.estado == EstadoBilletera.ACTIVA]
            menu = _generar_menu_billeteras(cands, tipo="egreso")
            pregunta = f"¿De qué billetera sale la plata?\n{menu}"
            return True, "slot_filling", entidades, pregunta

    return False, "no_match", {}, None


def _registrar_movimiento_directo(
    usuario: Usuario,
    entidades: dict,
    db: Session,
    registrar_adicionales: bool = True,
) -> tuple[Transaccion | None, str]:
    """Registra directamente un movimiento confirmado como nuevo o lote (Tareas 3.3 y 4.1)."""
    monto = entidades.get("monto")
    if monto is None:
        return None, "No pude procesar la operación."
    monto_decimal = Decimal(str(monto))
    tipo_val = entidades.get("tipo") or "egreso"
    moneda_sol = Moneda.USD if entidades.get("moneda") == "USD" else Moneda.ARS

    tarjeta_id_raw = entidades.get("tarjeta_id")
    if tarjeta_id_raw:
        tarjeta_id = UUID(str(tarjeta_id_raw))
        tarjeta = db.execute(
            select(TarjetaCredito).where(TarjetaCredito.id == tarjeta_id, TarjetaCredito.usuario_id == usuario.id)
        ).scalars().first()
        if not tarjeta:
            return None, "Tarjeta no encontrada."

        cant_cuotas = int(entidades.get("cantidad_cuotas", 1))
        monto_total = Decimal(str(entidades.get("monto_total", monto_decimal)))
        monto_cuota = Decimal(str(entidades.get("monto_cuota", monto_total / cant_cuotas)))

        cat_id, subcat_id = _resolver_categoria_y_subcategoria(entidades.get("categoria"), usuario.id, db, tipo="egreso")
        fecha_obj = _resolver_fecha_transaccion(entidades.get("fecha"))

        desc_candidata = entidades.get("descripcion")
        desc_final = ai_service.sanitizar_descripcion(
            desc_candidata,
            tipo="egreso",
        )

        data_tx = TransaccionCreate(
            tipo=TipoTransaccion.EGRESO,
            monto=monto_total,
            moneda=tarjeta.moneda,
            fecha=fecha_obj,
            descripcion=desc_final or _nombre_corto_categoria(entidades.get("categoria")),
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=tarjeta.billetera_id,
            tarjeta_id=tarjeta.id,
            categoria_id=cat_id,
            subcategoria_id=subcat_id,
            origen=OrigenTransaccion.IA_WPP,
            es_padre_cuotas=True,
            info_cuotas=InfoCuotas(
                cantidad_cuotas=cant_cuotas,
                cuota_inicial=1,
                tiene_interes=False,
                tasa_interes=None,
                monto_total=monto_total,
                proximo_resumen=False,
            ),
        )
        tx = transaccion_service.crear_transaccion(
            db=db,
            usuario_id=usuario.id,
            data=data_tx,
            commit=False,
        )

        emitir_evento_actualizacion(db, usuario.id, "transacciones")
        emitir_evento_actualizacion(db, usuario.id, "tarjetas")
        db.commit()

        primer_v = calcular_primer_vencimiento(fecha_obj, tarjeta.dia_cierre, tarjeta.dia_vencimiento, False)
        venc_mes = MESES_ES_GEN[primer_v.month - 1]
        venc_anio = primer_v.year
        cat_disp = _nombre_corto_categoria(entidades.get("categoria"))

        if cant_cuotas > 1:
            monto_cuota_fmt = formatear_monto(float(monto_cuota), tarjeta.moneda)
            msg_resp = f"Listo. {cant_cuotas} cuotas de {monto_cuota_fmt} en {cat_disp} con tarjeta {tarjeta.nombre} — registrado. Va a ingresar en el resumen de {venc_mes} {venc_anio}."
        else:
            total_fmt = formatear_monto(float(monto_total), tarjeta.moneda)
            msg_resp = f"Listo. 1 cuota de {total_fmt} en {cat_disp} con tarjeta {tarjeta.nombre} — registrado. Va a ingresar en el resumen de {venc_mes} {venc_anio}."

        return tx, msg_resp

    nombre_billetera = entidades.get("billetera_resuelta_nombre") or (
        entidades.get("billetera_destino") if tipo_val == "ingreso" else entidades.get("billetera_origen")
    )
    billetera_id = _resolver_billetera(nombre_billetera, usuario.id, db, moneda=moneda_sol)
    if not billetera_id:
        billetera_id = db.execute(
            select(Billetera.id).where(
                Billetera.usuario_id == usuario.id,
                Billetera.estado == EstadoBilletera.ACTIVA,
                Billetera.moneda == moneda_sol,
                Billetera.es_principal == True,
            ).order_by(Billetera.nombre.asc(), Billetera.id.asc())
        ).scalars().first()
    if not billetera_id:
        billeteras_moneda = _obtener_billeteras_activas(usuario.id, db, moneda=moneda_sol)
        if len(billeteras_moneda) == 1:
            billetera_id = billeteras_moneda[0].id

    if not billetera_id:
        return None, "No pude resolver la billetera."

    billetera = db.execute(select(Billetera).where(Billetera.id == billetera_id).with_for_update()).scalars().first()
    if not billetera:
        return None, "Billetera no encontrada."

    cat_id, subcat_id = _resolver_categoria_y_subcategoria(entidades.get("categoria"), usuario.id, db, tipo=tipo_val)
    fecha_obj = _resolver_fecha_transaccion(entidades.get("fecha"))

    desc_candidata = entidades.get("descripcion")
    desc_final = ai_service.sanitizar_descripcion(
        desc_candidata,
        tipo=tipo_val,
    )

    tx = Transaccion(
        usuario_id=usuario.id,
        tipo=TipoTransaccion.INGRESO if tipo_val == "ingreso" else TipoTransaccion.EGRESO,
        monto=monto_decimal,
        moneda=moneda_sol,
        fecha=fecha_obj,
        descripcion=desc_final or _nombre_corto_categoria(entidades.get("categoria")),
        metodo_pago=deducir_metodo_pago(billetera, tarjeta_id=None),
        billetera_id=billetera.id,
        categoria_id=cat_id,
        subcategoria_id=subcat_id,
        origen=OrigenTransaccion.IA_WPP,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        es_recurrente=False,
        es_cuota_hija=False,
        es_padre_cuotas=False,
    )
    db.add(tx)

    if tx.tipo == TipoTransaccion.INGRESO:
        billetera.saldo_actual += monto_decimal
    else:
        billetera.saldo_actual -= monto_decimal

    presupuesto_service.registrar_impacto_presupuesto(db, tx, revertir=False, commit=False)

    adicionales = entidades.get("transacciones_adicionales") if registrar_adicionales else None
    descartadas = []
    adicionales_registradas = []
    if adicionales and isinstance(adicionales, list):
        for adic in adicionales:
            if isinstance(adic, dict):
                tx_ad, motivo = _crear_transaccion_adicional(adic, usuario.id, billetera, db)
                if tx_ad:
                    adicionales_registradas.append(tx_ad)
                if motivo:
                    descartadas.append(motivo)

    emitir_evento_actualizacion(db, usuario.id, "transacciones")
    emitir_evento_actualizacion(db, usuario.id, "billeteras")
    db.flush()

    monto_str = formatear_monto(float(tx.monto), tx.moneda)
    bill_nombre = billetera.nombre

    if adicionales and isinstance(adicionales, list) and len(adicionales) > 0:
        total_registrados = 1 + len(adicionales_registradas)
        cat_display = _nombre_corto_categoria(entidades.get("categoria"))
        fecha_p_nat = _formatear_fecha_natural(tx.fecha)
        fecha_p_disp = f" ({fecha_p_nat})" if fecha_p_nat else ""
        items_str = [f"{monto_str} en {cat_display}{fecha_p_disp}"]
        for tx_ad in adicionales_registradas:
            fecha_ad_nat = _formatear_fecha_natural(tx_ad.fecha)
            fecha_ad_disp = f" ({fecha_ad_nat})" if fecha_ad_nat else ""
            items_str.append(
                f"{formatear_monto(float(tx_ad.monto), tx_ad.moneda)} en {_nombre_corto_categoria(tx_ad.descripcion)}{fecha_ad_disp}"
            )
        origen_str = f" desde {bill_nombre}" if bill_nombre else (f" a {bill_nombre}" if tx.tipo == TipoTransaccion.INGRESO else "")
        mov_palabra = "movimientos" if total_registrados != 1 else "movimiento"
        reg_palabra = "registrados" if total_registrados != 1 else "registrado"
        msg_resp = f"Listo. {total_registrados} {mov_palabra}{origen_str}: {', '.join(items_str)} — {reg_palabra}."
    else:
        cat_nombre = None
        if tx.categoria_id:
            cat = db.execute(select(Categoria).where(Categoria.id == tx.categoria_id)).scalars().first()
            cat_nombre = cat.nombre if cat else None
        subcat_nombre = None
        if tx.subcategoria_id:
            subcat = db.execute(select(Subcategoria).where(Subcategoria.id == tx.subcategoria_id)).scalars().first()
            subcat_nombre = subcat.nombre if subcat else None
        nombre_categoria_display = subcat_nombre or cat_nombre or "Otros"

        fecha_nat = _formatear_fecha_natural(tx.fecha)
        fecha_disp = f" ({fecha_nat})" if fecha_nat else ""

        if tx.tipo == TipoTransaccion.INGRESO:
            partes = [f"Listo. Ingreso de {monto_str}"]
            if nombre_categoria_display:
                partes.append(f"en {nombre_categoria_display}")
            if bill_nombre:
                partes.append(f"a {bill_nombre}{fecha_disp}")
            partes.append("— registrado.")
        else:
            partes = [f"Listo. {monto_str}"]
            if nombre_categoria_display:
                partes.append(f"en {nombre_categoria_display}")
            if bill_nombre:
                partes.append(f"desde {bill_nombre}{fecha_disp}")
            partes.append("— registrado.")
        msg_resp = " ".join(partes)

    if descartadas:
        msg_resp += "\n" + "\n".join(descartadas)

    # REGLA DE PRIVACIDAD: Los saldos no se muestran tras registrar un movimiento,
    # salvo que el usuario los pida explícitamente (privacidad de pantalla).
    if billetera.saldo_actual < 0:
        msg_resp += "\nLa billetera quedó en negativo."

    return tx, msg_resp


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

    verbos_op_nueva = r"^(?:gaste|pague|compre|cargue|cobre|ingrese|transferi|meti|puse|pase|saque|extraje|retire|vendi|dolarice|mande|movi)\b"
    if re.search(verbos_op_nueva, norm):
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

    # 3. Detectar fecha: "fue ayer", "era ayer", "ayer", "fue anteayer", etc.
    m_fecha = re.search(r"\b(ayer|anteayer|hoy|el\s+\d+\s+de\s+[a-z]+(?:\s+de\s+\d+)?)\b", norm)
    if m_fecha:
        f_str = m_fecha.group(1)
        hoy = hoy_argentina()
        if f_str == "ayer":
            f_res = hoy - timedelta(days=1)
        elif f_str == "anteayer":
            f_res = hoy - timedelta(days=2)
        elif f_str == "hoy":
            f_res = hoy
        else:
            f_res, _ = _resolver_y_validar_fecha(f_str)
        if f_res != tx_actual.fecha:
            cambios["fecha"] = f_res.isoformat()
            texto_restante = re.sub(r"\b(ayer|anteayer|hoy|el\s+\d+\s+de\s+[a-z]+(?:\s+de\s+\d+)?)\b", "", texto_restante, flags=re.IGNORECASE).strip()

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
        f_nueva_nat = _formatear_fecha_natural(date.fromisoformat(str(cambios["fecha"])))
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

# ==============================================================================
# HELPERS Y GESTIÓN DE SUSCRIPCIONES POR WHATSAPP (ETAPA B)
# ==============================================================================



















def _confirmar_propuesta_suscripcion(
    usuario: Usuario,
    db: Session,
) -> tuple[Suscripcion | None, str, bool]:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    conv_sub = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "agregar_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .with_for_update()
    ).scalars().first()

    if not conv_sub:
        return None, "No tenés ninguna suscripción pendiente para confirmar.", False

    entidades = conv_sub.entidades or {}
    nombre = entidades.get("servicio")
    monto_val = entidades.get("monto")
    frecuencia_val = entidades.get("frecuencia", "mensual")
    moneda_val = entidades.get("moneda", "ARS")
    billetera_id_str = entidades.get("billetera_id")
    tarjeta_id_str = entidades.get("tarjeta_id")
    proximo_cobro_str = entidades.get("proximo_cobro")

    if not nombre or monto_val is None or not proximo_cobro_str:
        return None, "Faltan datos para crear la suscripción.", False

    proximo_cobro = date.fromisoformat(proximo_cobro_str)
    billetera_id = UUID(billetera_id_str) if billetera_id_str else None
    tarjeta_id = UUID(tarjeta_id_str) if tarjeta_id_str else None

    cat_id = None
    subcat_id = None
    cat_sugerida = entidades.get("categoria")
    if cat_sugerida:
        c_id, s_id = _resolver_categoria_y_subcategoria(cat_sugerida, usuario.id, db, tipo="egreso")
        cat_id = c_id
        subcat_id = s_id

    data_create = SuscripcionCreate(
        nombre=nombre,
        monto=Decimal(str(monto_val)),
        moneda=moneda_val,
        frecuencia=frecuencia_val,
        proximo_cobro=proximo_cobro,
        billetera_id=billetera_id,
        tarjeta_id=tarjeta_id,
        categoria_id=cat_id,
        subcategoria_id=subcat_id,
        vigente_desde=hoy_argentina(),
    )

    nueva_sub = suscripcion_service.crear_suscripcion(db, usuario.id, data_create)

    conv_sub.accion_ejecutada = str(nueva_sub.id)
    db.commit()

    mon_enum = Moneda.USD if moneda_val == "USD" else Moneda.ARS
    monto_fmt = formatear_monto(float(monto_val), mon_enum)
    medio_pago_txt = entidades.get("medio_pago_txt", "")
    fecha_fmt = f"{proximo_cobro.day} de {MESES_ES_GEN[proximo_cobro.month - 1]}"

    msg_resp = f"Listo. Suscripción a {nombre} por {monto_fmt} {frecuencia_val} programada {medio_pago_txt} (primer cobro el {fecha_fmt})."
    return nueva_sub, msg_resp, False


def _confirmar_propuesta_baja_suscripcion(
    usuario: Usuario,
    db: Session,
) -> tuple[Suscripcion | None, str, bool]:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    conv_baja = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "dar_baja_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .with_for_update()
    ).scalars().first()

    if not conv_baja:
        return None, "No tenés ninguna baja de suscripción pendiente para confirmar.", False

    entidades = conv_baja.entidades or {}
    sub_id_str = entidades.get("suscripcion_id")
    nombre = entidades.get("nombre", "el servicio")

    if not sub_id_str:
        return None, "No pude procesar la baja.", False

    sub_id = UUID(sub_id_str)
    sub = suscripcion_service.cambiar_estado(db, usuario.id, sub_id, EstadoSuscripcion.CANCELADA)

    conv_baja.accion_ejecutada = f"baja:{sub.id}"
    db.commit()

    return sub, f"Listo, dimos de baja tu suscripción a {nombre}.", False


def _confirmar_propuesta_cambio_precio(
    usuario: Usuario,
    db: Session,
) -> tuple[HistorialSuscripcion | None, str, bool]:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    conv_cp = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario.id,
            ConversacionWpp.intent_detectado == "cambiar_precio_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .with_for_update()
    ).scalars().first()

    if not conv_cp:
        return None, "No tenés ningún cambio de precio pendiente para confirmar.", False

    entidades = conv_cp.entidades or {}
    sub_id_str = entidades.get("suscripcion_id")
    nuevo_monto = entidades.get("nuevo_monto")
    moneda = entidades.get("moneda", "ARS")
    nombre = entidades.get("nombre", "el servicio")

    if not sub_id_str or nuevo_monto is None:
        return None, "No pude procesar el cambio de precio.", False

    sub_id = UUID(sub_id_str)
    req = ActualizarPrecioRequest(
        monto=Decimal(str(nuevo_monto)),
        moneda=moneda,
        vigente_desde=hoy_argentina(),
    )
    hist = suscripcion_service.actualizar_precio(db, usuario.id, sub_id, req)

    conv_cp.accion_ejecutada = f"precio:{hist.id}"
    db.commit()

    mon_enum = Moneda.USD if moneda == "USD" else Moneda.ARS
    nuevo_fmt = formatear_monto(float(nuevo_monto), mon_enum)
    return hist, f"Listo, actualicé el precio de {nombre} a {nuevo_fmt}.", False


def _crear_transaccion_adicional(
    datos: dict,
    usuario_id: UUID,
    billetera: Billetera,
    db: Session,
) -> tuple[Transaccion | None, str | None]:
    billeteras = _obtener_billeteras_activas(usuario_id, db)
    tarjetas = _obtener_tarjetas_activas(usuario_id, db)
    datos_item = dict(datos)
    if not datos_item.get("billetera") and not datos_item.get("billetera_origen") and not datos_item.get("billetera_destino") and not datos_item.get("billetera_id"):
        datos_item["billetera"] = billetera.nombre
        datos_item["billetera_id"] = str(billetera.id)
    return _registrar_item_batch(datos_item, usuario_id, billeteras, tarjetas, db)


def _ejecutar_intent(resultado_ia: dict, usuario: Usuario, db: Session) -> str | None:
    try:
        intent = resultado_ia.get("intent")
        confianza = resultado_ia.get("confianza", 0.0)
        slot_filling = resultado_ia.get("slot_filling", False)

        if intent == "registrar_transaccion" and confianza >= 0.85 and not slot_filling:
            # No crear todavía — el system prompt ya pidió confirmación al usuario
            # Solo retornar None; el estado queda en slot_filling_estado para el turno siguiente
            return None

        elif intent == "confirmar":
            propuesta_ganadora = _buscar_propuesta_confirmable_mas_reciente(usuario.id, db)
            intent_ganador = propuesta_ganadora.intent_detectado if propuesta_ganadora else None

            if intent_ganador == "deshacer":
                tx, msg_resp, ya_conf = _confirmar_propuesta_deshacer(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(tx.id) if tx else None

            elif intent_ganador == "corregir":
                tx, msg_resp, ya_conf = _confirmar_propuesta_corregir(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(tx.id) if tx else None

            elif intent_ganador == "aportar_meta":
                mov, msg_resp, ya_conf = _confirmar_propuesta_aporte_meta(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(mov.id) if mov else None

            elif intent_ganador == "transferir_fondos":
                tr, msg_resp, ya_conf = _confirmar_propuesta_transferencia(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(tr.id) if tr else None

            elif intent_ganador == "dar_baja_suscripcion":
                sub, msg_resp, ya_conf = _confirmar_propuesta_baja_suscripcion(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(sub.id) if sub else None

            elif intent_ganador == "cambiar_precio_suscripcion":
                hist, msg_resp, ya_conf = _confirmar_propuesta_cambio_precio(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(hist.id) if hist else None

            elif intent_ganador == "agregar_suscripcion":
                sub, msg_resp, ya_conf = _confirmar_propuesta_suscripcion(usuario, db)
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(sub.id) if sub else None

            else:
                tx, msg_resp, ya_conf = _confirmar_propuesta_transaccion(
                    usuario, db, entidades_actuales=resultado_ia.get("entidades")
                )
                resultado_ia["_mensaje_confirmacion_directo"] = msg_resp
                return str(tx.id) if tx else None

        elif intent == "cancelar":
            txs_pendientes = db.execute(
                select(Transaccion)
                .where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.origen == OrigenTransaccion.IA_WPP,
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE,
                )
            ).scalars().all()

            for tx in txs_pendientes:
                db.delete(tx)
            if txs_pendientes:
                emitir_evento_actualizacion(db, usuario.id, "transacciones")

            # Desactivar y marcar cancelado cualquier slot filling activo del usuario
            convs_activas = db.execute(
                select(ConversacionWpp)
                .where(
                    ConversacionWpp.usuario_id == usuario.id,
                    ConversacionWpp.slot_filling_activo == True,
                )
            ).scalars().all()
            for c in convs_activas:
                c.slot_filling_activo = False
                c.accion_ejecutada = "cancelada"

            # Marcar canceladas de forma definitiva todas las propuestas previas no ejecutadas
            propuestas_previas = db.execute(
                select(ConversacionWpp)
                .where(
                    ConversacionWpp.usuario_id == usuario.id,
                    ConversacionWpp.intent_detectado == "registrar_transaccion",
                    ConversacionWpp.accion_ejecutada.is_(None),
                )
            ).scalars().all()
            for p in propuestas_previas:
                p.accion_ejecutada = "cancelada"

            db.flush()
            return None

        return None

    except Exception as e:
        logger.error(f"Error al ejecutar intent {resultado_ia.get('intent')} para usuario {usuario.id}: {str(e)}")
        return None


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_webhook(request: Request) -> PlainTextResponse:
    """
    Handshake de verificación de webhook de Meta (WhatsApp Business Cloud API).
    Meta envía hub.mode, hub.verify_token y hub.challenge por GET.
    """
    mode = request.query_params.get("hub.mode")
    verify_token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode and verify_token and settings.WHATSAPP_VERIFY_TOKEN:
        if mode == "subscribe" and secrets.compare_digest(verify_token, settings.WHATSAPP_VERIFY_TOKEN):
            logger.info("Webhook de WhatsApp verificado exitosamente.")
            return PlainTextResponse(content=challenge or "", status_code=status.HTTP_200_OK)
        else:
            logger.warning("Fallo en verificación de Webhook: token incorrecto.")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verificación fallida")

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parámetros inválidos")


def _preprocesar_webhook_whatsapp_sync(
    body_bytes: bytes, t_inicio: float
) -> tuple[PlainTextResponse, dict | None]:
    """
    Parte A (rápida): parsea el payload, extrae identificadores y persiste el wamid
    de forma idempotente en su propia sesión de BD. Retorna inmediatamente el 200 OK y
    los datos necesarios para que la Parte B corra en background.
    """
    try:
        payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
    except Exception:
        logger.warning("Error al decodificar JSON del webhook")
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

    # Extraer mensajes del payload de Meta
    entries = payload.get("entry", [])
    if not entries:
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

    changes = entries[0].get("changes", [])
    if not changes:
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

    value = changes[0].get("value", {})
    messages = value.get("messages", [])
    if not messages:
        # Eventos de estado (sent, delivered, read, failed, etc.)
        statuses = value.get("statuses")
        logger.info(
            "whatsapp_webhook_status_event",
            statuses=statuses,
            value=value,
        )
        if statuses and isinstance(statuses, list):
            for st in statuses:
                if isinstance(st, dict) and st.get("status") == "failed":
                    errors = st.get("errors", [])
                    if errors and isinstance(errors, list):
                        for err in errors:
                            if isinstance(err, dict):
                                logger.warning(
                                    "whatsapp_message_status_failed",
                                    wamid=st.get("id"),
                                    recipient_id=st.get("recipient_id"),
                                    code=err.get("code"),
                                    title=err.get("title"),
                                    error_details=err,
                                )
                            else:
                                logger.warning(
                                    "whatsapp_message_status_failed",
                                    wamid=st.get("id"),
                                    recipient_id=st.get("recipient_id"),
                                    error_details=err,
                                )
                    else:
                        logger.warning(
                            "whatsapp_message_status_failed",
                            wamid=st.get("id"),
                            recipient_id=st.get("recipient_id"),
                            status_details=st,
                        )
        return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

    msg = messages[0]
    wamid = msg.get("id")
    from_number = msg.get("from", "")
    msg_type = msg.get("type", "text")

    db = SessionLocal()
    try:
        # Idempotencia: Verificar y persistir wamid ANTES de ejecutar lógica de negocio
        if wamid:
            wamid_existente = db.execute(
                select(MensajeWhatsappProcesado.id).where(MensajeWhatsappProcesado.wamid == wamid)
            ).scalar_one_or_none()

            if wamid_existente:
                logger.info("whatsapp_wamid_duplicado_ignorado", wamid=wamid, from_number=from_number)
                return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None

            try:
                registro_wamid = MensajeWhatsappProcesado(
                    wamid=wamid,
                    telefono=from_number,
                    tipo_mensaje=msg_type,
                )
                db.add(registro_wamid)
                db.commit()
            except IntegrityError:
                db.rollback()
                logger.info("whatsapp_wamid_concurrente_duplicado_ignorado", wamid=wamid)
                return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), None
            except Exception as e:
                db.rollback()
                logger.error("whatsapp_error_registro_wamid", wamid=wamid, error=str(e))
    finally:
        db.close()

    t_ack = time.perf_counter() - t_inicio
    logger.info("[LATENCIA][WEBHOOK_ACK] Ack HTTP 200 retornado en: %.2fs", t_ack)

    datos_mensaje = {
        "msg": msg,
        "wamid": wamid,
        "from_number": from_number,
        "msg_type": msg_type,
        "t_inicio": t_inicio,
    }
    return PlainTextResponse(content="OK", status_code=status.HTTP_200_OK), datos_mensaje


def _procesar_mensaje_whatsapp_background(datos_mensaje: dict) -> None:
    """
    Parte B (background): ejecutada vía BackgroundTasks con su propia sesión de BD.
    Incluye feedback visual (leído + reacción ⏳), transcripción/visión, IA, guardado y envío.
    """
    msg = datos_mensaje["msg"]
    wamid = datos_mensaje.get("wamid")
    from_number = datos_mensaje.get("from_number", "")
    msg_type = datos_mensaje.get("msg_type", "text")
    t_inicio = datos_mensaje.get("t_inicio", time.perf_counter())

    # Feedback visual temprano: marcar como leído y mostrar escribiendo
    if wamid:
        marcar_leido_y_escribiendo(wamid)

    db = SessionLocal()
    try:
        try:
            usuario = _buscar_usuario_por_telefono(from_number, db)

            # 2.1 Detección del código de vinculación: solo para remitentes no vinculados
            if msg_type == "text" and not usuario:
                tel_identificador = normalizar_telefono_ar(from_number) or from_number
                if not _verificar_rate_limit_vinculacion(tel_identificador, db=db):
                    logger.warning(
                        "whatsapp_rate_limit_vinculacion_superado",
                        from_number=from_number,
                    )
                    return

                texto_candidato = msg.get("text", {}).get("body", "").strip()
                logger.info("whatsapp_webhook_mensaje_recibido", from_number=from_number, texto=texto_candidato, msg_type=msg_type)
                codigo_vinc, entrada_vinc, es_vencido = buscar_codigo_vinculacion(texto_candidato)
                if codigo_vinc:
                    if es_vencido or not entrada_vinc:
                        enviar_whatsapp(
                            from_number,
                            "El código de vinculación expiró. Por favor solicitá un código nuevo desde la app.",
                        )
                        return

                    # Código activo encontrado -> Consumir para asegurar un solo uso
                    consumir_codigo_vinculacion(codigo_vinc)

                    # 3.1 Normalizar teléfono del payload de Meta
                    tel_norm = normalizar_telefono_ar(from_number)
                    tel_guardar = f"+{from_number.lstrip('+')}"

                    usuario_dueno = db.execute(
                        select(Usuario).where(Usuario.id == entrada_vinc.usuario_id)
                    ).scalar_one_or_none()

                    if not usuario_dueno:
                        logger.error("whatsapp_vinculacion_usuario_inexistente", usuario_id=entrada_vinc.usuario_id)
                        return

                    # 3.2 Si el número ya pertenece a otro usuario: no vincular, responder y salir
                    otro_usuario = db.execute(
                        select(Usuario).where(
                            (Usuario.telefono == tel_guardar) |
                            (Usuario.telefono == from_number) |
                            (Usuario.telefono_normalizado == tel_norm),
                            Usuario.id != usuario_dueno.id,
                        )
                    ).scalar_one_or_none()

                    if otro_usuario:
                        logger.warning(
                            "whatsapp_vinculacion_telefono_duplicado",
                            from_number=from_number,
                            dueno_actual=str(otro_usuario.id),
                        )
                        enviar_whatsapp(
                            from_number,
                            "Ese número de teléfono ya está asociado a otra cuenta de Argentum.",
                        )
                        return

                    # 3.3 Si ya tenía otro número verificado, avisar al número viejo por WhatsApp
                    tel_viejo = usuario_dueno.telefono
                    if tel_viejo and usuario_dueno.telefono_verificado and normalizar_telefono_ar(tel_viejo) != tel_norm:
                        try:
                            enviar_whatsapp(
                                tel_viejo,
                                "Tu cuenta de Argentum fue desvinculada de este número porque se asoció a un nuevo número de WhatsApp. Si no fuiste vos, contactanos inmediatamente.",
                            )
                        except Exception as e:
                            logger.warning("No se pudo enviar aviso de desvinculación a número anterior: %s", e)

                    # 3.4 Guardar telefono y telefono_verificado=True en una sola transacción
                    usuario_dueno.telefono = tel_guardar
                    usuario_dueno.telefono_normalizado = tel_norm
                    usuario_dueno.telefono_verificado = True

                    # 3.7 Emitir evento de actualización
                    emitir_evento_actualizacion(db, usuario_dueno.id, "usuario")
                    db.commit()

                    # 3.5 Responder por WhatsApp confirmando vinculación
                    enviar_whatsapp(
                        from_number,
                        "¡Tu cuenta de Argentum fue vinculada con éxito!\n"
                        "A partir de ahora podés registrar tus gastos e ingresos directamente desde acá. "
                        "Probá mandarme un mensaje o audio como: *Almuerzo $3500 con Galicia*.",
                    )

                    # 3.6 Mandar mail de aviso
                    try:
                        from app.services.email_service import enviar_email_telefono_vinculado
                        enviar_email_telefono_vinculado(
                            destinatario=usuario_dueno.email,
                            telefono=tel_guardar,
                            nombre=usuario_dueno.nombre,
                        )
                    except Exception as e:
                        logger.error("Error al enviar email de confirmación de vinculación: %s", e)

                    logger.info("whatsapp_vinculacion_exitosa", usuario_id=str(usuario_dueno.id), telefono=from_number)
                    return
            if not usuario:
                telefono_norm = normalizar_telefono_ar(from_number)
                debe_responder = _debe_responder_no_registrado(telefono_norm)
                logger.warning(
                    "whatsapp_usuario_no_encontrado",
                    telefono_ultimos_4=telefono_norm[-4:] if telefono_norm else None,
                    respondido=debe_responder,
                )
                if debe_responder:
                    enviar_whatsapp(from_number, "No encontramos tu cuenta. Registrate en miargentum.com")
                return

            # Rate limit para usuario registrado (evaluado antes de llamar a Whisper, GPT-4o Vision o ai_service)
            es_medio = (msg_type in ("audio", "image"))
            tel_usuario = usuario.telefono_normalizado or normalizar_telefono_ar(from_number) or from_number
            permitido, motivo_rate_limit = _verificar_rate_limit_registrado(tel_usuario, es_medio=es_medio)
            if not permitido:
                logger.warning(
                    "whatsapp_rate_limit_registrado_superado",
                    usuario_id=str(usuario.id),
                    tipo_mensaje=msg_type,
                )
                if motivo_rate_limit:
                    enviar_whatsapp(from_number, motivo_rate_limit)
                return

            logger.info(
                "whatsapp_mensaje_recibido",
                usuario_id=str(usuario.id),
                tipo_mensaje=msg_type,
            )

            mensaje_texto = ""
            transcripcion = None
            es_imagen = False
            es_credito = False
            es_lote = False
            caption_imagen = ""

            if msg_type == "text":
                mensaje_texto = msg.get("text", {}).get("body", "").strip()

            elif msg_type == "audio":
                audio_obj = msg.get("audio", {})
                media_id = audio_obj.get("id")
                mime_type = audio_obj.get("mime_type", "audio/ogg")

                if media_id:
                    t_media_start = time.perf_counter()
                    transcripcion, error_audio = _transcribir_audio(media_id, mime_type, duracion_maxima_segundos=120)
                    t_media_end = time.perf_counter()
                    logger.info(
                        "[LATENCIA][MEDIA-AUDIO] Transcripción Whisper: %.2fs",
                        t_media_end - t_media_start,
                    )

                    if error_audio == "DURACION_EXCEDIDA":
                        enviar_whatsapp(
                            from_number,
                            "El audio es muy largo (máximo 2 minutos). Por favor mandá un audio más corto o escribí el gasto en texto.",
                        )
                        return

                    if error_audio == "TAMANO_EXCEDIDO":
                        enviar_whatsapp(
                            from_number,
                            "El audio es muy pesado (máximo 8 MB). Por favor mandá un audio más corto o escribí el gasto en texto.",
                        )
                        return

                    if transcripcion:
                        mensaje_texto = transcripcion
                        if settings.ENVIRONMENT == "production":
                            logger.info("Audio transcripto exitosamente (longitud: %d caracteres)", len(transcripcion))
                        else:
                            logger.info("Audio transcripto: '%s'", transcripcion[:100])
                    else:
                        enviar_whatsapp(
                            from_number, "No pude escuchar el audio. Mandame el mensaje en texto."
                        )
                        return
                else:
                    enviar_whatsapp(
                        from_number, "No pude escuchar el audio. Mandame el mensaje en texto."
                    )
                    return

            elif msg_type == "image":
                image_obj = msg.get("image", {})
                caption = image_obj.get("caption") or ""
                media_id = image_obj.get("id")
                mime_type = image_obj.get("mime_type", "image/jpeg")
                nombre_usuario = f"{usuario.nombre or ''} {usuario.apellido or ''}".strip()

                if media_id:
                    t_media_start = time.perf_counter()
                    descripcion_imagen, error_img = _extraer_transaccion_de_imagen(
                        media_id, mime_type, nombre_usuario, max_bytes=5 * 1024 * 1024
                    )
                    t_media_end = time.perf_counter()
                    logger.info(
                        "[LATENCIA][MEDIA-IMAGEN] Análisis GPT-4o Vision: %.2fs",
                        t_media_end - t_media_start,
                    )

                    if error_img == "TAMANO_EXCEDIDO":
                        enviar_whatsapp(
                            from_number,
                            "La imagen es muy pesada (máximo 5 MB). Por favor mandá una foto más liviana.",
                        )
                        return

                    if descripcion_imagen:
                        es_imagen = True
                        caption_imagen = caption
                        mensaje_texto = descripcion_imagen
                        if settings.ENVIRONMENT == "production":
                            logger.info("Imagen analizada exitosamente (longitud: %d caracteres)", len(descripcion_imagen))
                        else:
                            logger.info("Imagen analizada: '%s'", descripcion_imagen[:100])
                    else:
                        enviar_whatsapp(
                            from_number, "No pude leer el comprobante. Mandame los datos en texto."
                        )
                        return
                else:
                    enviar_whatsapp(
                        from_number, "No pude leer el comprobante. Mandame los datos en texto."
                    )
                    return

            if not mensaje_texto:
                enviar_whatsapp(
                    from_number,
                    "No entendí bien lo que quisiste decir. Podés contarme qué gastaste, por ejemplo: *Almuerzo $1.500*",
                )
                return

            if len(mensaje_texto) > 1500:
                logger.warning("whatsapp_texto_limite_caracteres_superado", longitud=len(mensaje_texto))
                enviar_whatsapp(
                    from_number,
                    "El mensaje es muy largo (máximo 1500 caracteres). Por favor mandalo más resumido.",
                )
                return
            # 0. Chequeo determinístico de pedido de pago de resumen de tarjeta (Tarea 7)
            if manejar_pago_resumen(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 1. Chequeo determinístico de saludo rioplatense (Tarea 4)
            if manejar_saludo(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 2. Si hay una propuesta pendiente y el usuario menciona una billetera, corregirla determinísticamente
            # Evaluado antes de cancelación para que "no, fue en Mercado Pago" o "no fue en galicia" corrijan y no cancelen
            propuesta_pendiente = _buscar_propuesta_pendiente(usuario.id, db)
            conv_activa = _buscar_slot_filling_activo(usuario.id, db)
            if propuesta_pendiente and not conv_activa:
                es_corr, b_nueva, cands, err_moneda = _evaluar_correccion_billetera(
                    mensaje_texto, usuario.id, propuesta_pendiente, db
                )
                if es_corr:
                    if err_moneda:
                        enviar_whatsapp(from_number, err_moneda)
                        return
                    if len(cands) > 1:
                        tipo_prop = propuesta_pendiente.entidades.get("tipo", "egreso")
                        menu = _generar_menu_billeteras(cands, tipo=tipo_prop)
                        propuesta_pendiente.slot_filling_activo = True
                        clave_bill = "billetera_destino" if tipo_prop == "ingreso" else "billetera_origen"
                        propuesta_pendiente.slot_filling_estado = {
                            **propuesta_pendiente.entidades,
                            "datos_faltantes": [clave_bill],
                        }
                        db.commit()
                        enviar_whatsapp(from_number, f"¿A cuál te referís?\n{menu}")
                        return
                    if b_nueva:
                        tipo_prop = propuesta_pendiente.entidades.get("tipo", "egreso")
                        clave_bill = "billetera_destino" if tipo_prop == "ingreso" else "billetera_origen"
                        clave_otra = "billetera_origen" if tipo_prop == "ingreso" else "billetera_destino"

                        entidades_nuevas = dict(propuesta_pendiente.entidades)
                        entidades_nuevas[clave_bill] = b_nueva.nombre
                        entidades_nuevas.pop(clave_otra, None)

                        nuevo_msg = _construir_propuesta_transaccion(
                            entidades_nuevas, b_nueva.nombre, se_asumio_principal=False, billetera_moneda=b_nueva.moneda
                        )

                        propuesta_pendiente.entidades = entidades_nuevas
                        propuesta_pendiente.mensaje_bot = nuevo_msg
                        propuesta_pendiente.fecha = datetime.now(timezone.utc)

                        nueva_conv = ConversacionWpp(
                            usuario_id=usuario.id,
                            wamid=wamid,
                            mensaje_usuario=mensaje_texto,
                            tipo_mensaje=TipoMensajeWpp.TEXTO,
                            transcripcion=None,
                            mensaje_bot=nuevo_msg,
                            intent_detectado="registrar_transaccion",
                            entidades=entidades_nuevas,
                            accion_ejecutada=None,
                            confianza=Decimal("1.000"),
                            slot_filling_activo=False,
                            slot_filling_estado=None,
                        )
                        db.add(nueva_conv)
                        db.commit()
                        enviar_whatsapp(from_number, nuevo_msg)
                        return

            # Chequeo determinístico de cancelación para aporte a meta
            if manejar_cancelacion_aporte_meta(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 3. Chequeo determinístico de cancelación (Tarea 5)
            if manejar_cancelacion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 3. Chequeo de respuesta a verificación de duplicado o lote pendiente
            if manejar_verificaciones_slot_filling(
                mensaje_texto, usuario, db, from_number, wamid=wamid
            ):
                return

            # Chequeo determinístico de confirmación para aporte a meta
            if manejar_confirmacion_aporte_meta(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 4. Chequeo determinístico de confirmación con bloqueo de concurrencia (Tarea 2)
            if manejar_confirmacion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 4. Buscar conversación activa previa con slot_filling dentro del plazo (Tarea 2)
            conv_activa = _buscar_slot_filling_activo(usuario.id, db)
            conv_vencida = _buscar_slot_filling_vencido(usuario.id, db) if not conv_activa else None

            # Si hay una conversación vencida, apagarla en base
            if conv_vencida:
                conv_vencida.slot_filling_activo = False
                conv_vencida.accion_ejecutada = "vencida"
                db.flush()
                # Si el usuario mandó una respuesta numérica o intenta responder al menú vencido
                if mensaje_texto.strip().isdigit() or _es_pregunta_billetera(conv_vencida):
                    msg_vencida = "Esa operación ya venció. Podés volver a mandarla."
                    nueva_conv = ConversacionWpp(
                        usuario_id=usuario.id,
                        wamid=wamid,
                        mensaje_usuario=mensaje_texto,
                        tipo_mensaje=TipoMensajeWpp.TEXTO,
                        transcripcion=None,
                        mensaje_bot=msg_vencida,
                        intent_detectado="slot_filling",
                        entidades={},
                        accion_ejecutada=None,
                        confianza=Decimal("1.000"),
                        slot_filling_activo=False,
                        slot_filling_estado=None,
                    )
                    db.add(nueva_conv)
                    db.commit()
                    enviar_whatsapp(from_number, msg_vencida)
                    return

            estado_previo = (
                dict(conv_activa.slot_filling_estado)
                if conv_activa and conv_activa.slot_filling_estado
                else None
            )

            # 5. Si hay pregunta de billetera pendiente activa, resolver selección numérica o por nombre
            if manejar_menu_billetera(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
            ):
                return

            # 5.1 Si hay pregunta de tarjeta pendiente activa
            if manejar_menu_tarjeta(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa
            ):
                return
            # 5.2 Si hay aclaración de cuotas pendiente activa ("¿Los $80.000 son el total o el valor de cada cuota?")
            if manejar_aclaracion_cuotas(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa
            ):
                return
            # 7. Si el mensaje es únicamente un número sin pregunta pendiente
            if manejar_numero_aislado(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
            ):
                return

            # 7.5 Detección determinística de deshacer (Tarea 2)
            if manejar_deshacer(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.6 Detección determinística de corregir (Tarea 3)
            if manejar_corregir(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return
            # 7.7 Detección determinística de transferencias / cajero / dólares (Punto 9B)
            if manejar_transferencias(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
            ):
                return

            # 7.7b Detección determinística de aporte a meta
            if manejar_aporte_meta(
                mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa, estado_previo=estado_previo
            ):
                return

            # 7.8 Detección determinística de consultas de suscripciones (Tarea 7)
            if manejar_consulta_suscripciones(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.9 Detección determinística de baja de suscripciones (Tarea 5)
            if manejar_baja_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.10 Detección determinística de cambio de precio de suscripción (Tarea 6)
            if manejar_cambio_precio_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.11 Detección determinística de ambigüedad suscripción vs gasto suelto (Tarea 3.4)
            if manejar_ambiguedad_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            # 7.12 Detección determinística de alta de suscripción (Tarea 4)
            if manejar_alta_suscripcion(mensaje_texto, usuario, db, from_number, wamid=wamid):
                return

            if manejar_consulta_gastos(mensaje_texto, usuario, db, from_number, wamid=wamid, conv_activa=conv_activa):
                return

            # 8. Procesamiento normal de IA
            t_ia_start = time.perf_counter()
            resultado_ia = ai_service.procesar_mensaje(
                mensaje=mensaje_texto,
                usuario=usuario,
                db=db,
                historial=_obtener_historial_reciente(usuario.id, db),
                estado_previo=estado_previo,
            )
            t_ia_end = time.perf_counter()
            logger.info("[LATENCIA][IA] Procesamiento: %.2fs", t_ia_end - t_ia_start)

            # Si el intent es desconocido o la confianza es baja (< 0.60), dar respuesta clara con ejemplo
            intent_ia_raw = resultado_ia.get("intent")
            confianza_ia_raw = float(resultado_ia.get("confianza", 1.0))

            # Salvaguarda: si la IA clasifica como deshacer pero el mensaje contiene monto y no es frase de deshacer
            if intent_ia_raw == "deshacer" and not _es_pedido_deshacer(mensaje_texto) and resultado_ia.get("entidades", {}).get("monto"):
                resultado_ia["intent"] = "registrar_transaccion"
                intent_ia_raw = "registrar_transaccion"

            if intent_ia_raw == "desconocido" or confianza_ia_raw < 0.60:
                resp_ia = resultado_ia.get("respuesta_usuario") or ""
                if any(k in resp_ia.lower() for k in ["límite", "limite", "tandas"]):
                    resultado_ia["respuesta_usuario"] = MSG_TOPE_MOVIMIENTOS_SUPERADO
                    resultado_ia["intent"] = "tope_superado"
                    resultado_ia["slot_filling"] = False
                elif any(k in resp_ia.lower() for k in ["separad", "transferenc"]):
                    resultado_ia["respuesta_usuario"] = MSG_NO_MEZCLAR_TRANSFERENCIAS
                    resultado_ia["intent"] = "no_mezclar_transferencias"
                    resultado_ia["slot_filling"] = False
                else:
                    msg_desc = (
                        "No entendí ese mensaje. Por ahora puedo registrar gastos e ingresos, "
                        "o consultar tus saldos y proyecciones. Por ejemplo: 'gasté 5000 en el kiosco' o 'cuánta plata tengo'."
                    )
                    resultado_ia["respuesta_usuario"] = msg_desc
                    resultado_ia["intent"] = "desconocido"
                    resultado_ia["slot_filling"] = False

            # Detección de cambio de tema con nueva operación (Tarea 3 / Cierre Punto 4)
            aviso_cambio_tema = None
            entidades_ia = resultado_ia.get("entidades") or {}
            if resultado_ia.get("intent") in ("registrar_transaccion", "slot_filling"):
                monto_ia = entidades_ia.get("monto")
                cat_ia = entidades_ia.get("categoria")
                if conv_activa and conv_activa.slot_filling_estado:
                    monto_prev = conv_activa.slot_filling_estado.get("monto")
                    cat_prev = conv_activa.slot_filling_estado.get("categoria")
                    if monto_ia is not None and (monto_ia != monto_prev or (cat_ia and cat_ia != cat_prev)):
                        # Es una nueva operación: descartar estado previo y preparar aviso
                        conv_activa.slot_filling_activo = False
                        conv_activa.accion_ejecutada = "descartada_por_nueva_operacion"
                        db.flush()
                        estado_previo = None

                        mon_prev = conv_activa.slot_filling_estado.get("moneda", "ARS")
                        mon_prev_enum = Moneda.USD if mon_prev == "USD" else Moneda.ARS
                        cat_prev_disp = _nombre_corto_categoria(cat_prev) if cat_prev else ""
                        monto_prev_fmt = formatear_monto(float(monto_prev), mon_prev_enum) if monto_prev is not None else ""

                        mon_nuevo = entidades_ia.get("moneda", "ARS")
                        mon_nuevo_enum = Moneda.USD if mon_nuevo == "USD" else Moneda.ARS
                        cat_nuevo_disp = _nombre_corto_categoria(cat_ia) if cat_ia else ""
                        monto_nuevo_fmt = formatear_monto(float(monto_ia), mon_nuevo_enum) if monto_ia is not None else ""

                        if cat_prev_disp and cat_nuevo_disp:
                            aviso_cambio_tema = f"Descarté la de {monto_prev_fmt} en {cat_prev_disp}. Para los {monto_nuevo_fmt} en {cat_nuevo_disp}:"
                        elif cat_prev_disp:
                            aviso_cambio_tema = f"Descarté la de {monto_prev_fmt} en {cat_prev_disp}. Para los {monto_nuevo_fmt}:"
                        else:
                            aviso_cambio_tema = f"Descarté la operación anterior de {monto_prev_fmt}."

            # Mergear determinísticamente entidades para no perder campos de turnos previos si corresponde
            if estado_previo:
                resultado_ia["entidades"] = _merge_entidades(
                    estado_previo,
                    resultado_ia.get("entidades", {}),
                    intent_nuevo=resultado_ia.get("intent"),
                )

            if conv_activa and conv_activa.slot_filling_estado and isinstance(resultado_ia.get("entidades"), dict):
                for marca in ("origen_imagen", "confianza_baja"):
                    if conv_activa.slot_filling_estado.get(marca):
                        resultado_ia["entidades"][marca] = True

            if confianza_ia_raw < 0.85 and isinstance(resultado_ia.get("entidades"), dict):
                resultado_ia["entidades"]["confianza_baja"] = True

            entidades_actuales = resultado_ia.get("entidades", {})

            # 5. Resolución determinística de billetera para movimientos (Tareas 2, 3, 4, 8)
            tipo_act = entidades_actuales.get("tipo") or "egreso"
            clave_bill = "billetera_destino" if tipo_act == "ingreso" else "billetera_origen"
            clave_otra = "billetera_origen" if tipo_act == "ingreso" else "billetera_destino"

            if resultado_ia.get("intent") in ("registrar_transaccion", "slot_filling") or entidades_actuales.get("monto") is not None:
                tarjetas_usuario = _obtener_tarjetas_activas(usuario.id, db)
                m_norm = normalizar_texto(mensaje_texto)

                adicionales = entidades_actuales.get("transacciones_adicionales")
                es_lote = bool(adicionales and isinstance(adicionales, list) and len(adicionales) > 0)

                if es_lote:
                    total_movimientos = 1 + len(adicionales)
                    if total_movimientos > MAX_MOVIMIENTOS_POR_LOTE:
                        resultado_ia["intent"] = "tope_superado"
                        resultado_ia["slot_filling"] = False
                        resultado_ia["respuesta_usuario"] = MSG_TOPE_MOVIMIENTOS_SUPERADO
                        resultado_ia["entidades"] = {}
                    elif any(p in m_norm for p in (
                        "transferi", "transferir", "transferencia", "pase a", "pasé a",
                        "extraje", "extraccion", "extracción", "cajero",
                        "compre dolares", "compré dólares", "vendi dolares", "vendí dólares",
                        "comprar dolares", "comprar dólares", "vender dolares", "vender dólares",
                    )):
                        resultado_ia["intent"] = "mezcla_transferencia_invalida"
                        resultado_ia["slot_filling"] = False
                        resultado_ia["respuesta_usuario"] = MSG_NO_MEZCLAR_TRANSFERENCIAS
                        resultado_ia["entidades"] = {}
                    else:
                        billeteras_todas = _obtener_billeteras_activas(usuario.id, db)
                        operaciones = []

                        op_0 = {
                            "id": 0,
                            "monto": entidades_actuales.get("monto"),
                            "tipo": entidades_actuales.get("tipo", "egreso"),
                            "moneda": entidades_actuales.get("moneda", "ARS"),
                            "categoria": entidades_actuales.get("categoria"),
                            "descripcion": entidades_actuales.get("descripcion"),
                            "fecha": entidades_actuales.get("fecha"),
                            "billetera": entidades_actuales.get("billetera") or entidades_actuales.get("billetera_origen") or entidades_actuales.get("billetera_destino"),
                            "tarjeta": entidades_actuales.get("tarjeta"),
                            "resuelta": False,
                        }
                        operaciones.append(op_0)

                        for idx, ad in enumerate(adicionales, 1):
                            if isinstance(ad, dict):
                                op_ad = {
                                    "id": idx,
                                    "monto": ad.get("monto"),
                                    "tipo": ad.get("tipo", "egreso"),
                                    "moneda": ad.get("moneda", "ARS"),
                                    "categoria": ad.get("categoria"),
                                    "descripcion": ad.get("descripcion"),
                                    "fecha": ad.get("fecha"),
                                    "billetera": ad.get("billetera") or ad.get("billetera_origen") or ad.get("billetera_destino"),
                                    "tarjeta": ad.get("tarjeta"),
                                    "resuelta": False,
                                }
                                operaciones.append(op_ad)

                        for op in operaciones:
                            if op.get("tarjeta") and tarjetas_usuario:
                                t_match, _ = resolver_tarjeta_cascada(op["tarjeta"], tarjetas_usuario)
                                if t_match:
                                    op["tarjeta_id"] = str(t_match.id)
                                    op["tarjeta"] = t_match.nombre
                                    op["resuelta"] = True
                                    continue

                            mon_op = Moneda.USD if op.get("moneda") == "USD" else Moneda.ARS
                            bills_mon = [b for b in billeteras_todas if b.moneda == mon_op]

                            if op.get("billetera"):
                                b_match, _ = resolver_billetera_cascada(op["billetera"], bills_mon)
                                if b_match:
                                    op["billetera_id"] = str(b_match.id)
                                    op["billetera"] = b_match.nombre
                                    op["resuelta"] = True
                                    continue

                            b_ppal = next((b for b in bills_mon if b.es_principal), None)
                            if b_ppal:
                                op["billetera_id"] = str(b_ppal.id)
                                op["billetera"] = b_ppal.nombre
                                op["se_asumio_principal"] = True
                                op["resuelta"] = True
                            elif len(bills_mon) == 1:
                                op["billetera_id"] = str(bills_mon[0].id)
                                op["billetera"] = bills_mon[0].nombre
                                op["resuelta"] = True
                            else:
                                op["resuelta"] = False

                        unresolved = [
                            op for op in operaciones
                            if not op.get("resuelta") and len([b for b in billeteras_todas if b.moneda == (Moneda.USD if op.get("moneda") == "USD" else Moneda.ARS)]) > 1
                        ]

                        if unresolved:
                            mon_r = unresolved[0].get("moneda", "ARS")
                            tipo_r = unresolved[0].get("tipo", "egreso")
                            todas_iguales = all(op.get("moneda", "ARS") == mon_r and op.get("tipo", "egreso") == tipo_r for op in unresolved)
                            bills_r = [b for b in billeteras_todas if b.moneda == (Moneda.USD if mon_r == "USD" else Moneda.ARS)]

                            if todas_iguales:
                                enc = "¿A qué billetera entraron los ingresos?" if tipo_r == "ingreso" else "¿Desde qué billetera salieron los gastos?"
                                pregunta = _generar_menu_billeteras(bills_r, tipo=tipo_r, encabezado=enc)
                                pend_ids = [op["id"] for op in unresolved]
                            else:
                                op_u = unresolved[0]
                                m_fmt = formatear_monto(float(op_u["monto"]), Moneda.USD if mon_r == "USD" else Moneda.ARS)
                                c_disp = _nombre_corto_categoria(op_u.get("categoria"))
                                if tipo_r == "ingreso":
                                    enc = f"¿A qué billetera entró el ingreso de {m_fmt} en {c_disp}?"
                                else:
                                    enc = f"¿Desde qué billetera salió el gasto de {m_fmt} en {c_disp}?"
                                pregunta = _generar_menu_billeteras(bills_r, tipo=tipo_r, encabezado=enc)
                                pend_ids = [op_u["id"]]

                            resultado_ia["intent"] = "slot_filling"
                            resultado_ia["slot_filling"] = True
                            resultado_ia["datos_faltantes"] = ["billetera_lote"]
                            resultado_ia["respuesta_usuario"] = pregunta
                            entidades_actuales["tipo_flujo"] = "lote_slot_filling"
                            entidades_actuales["datos_faltantes"] = ["billetera_lote"]
                            entidades_actuales["operaciones"] = operaciones
                            entidades_actuales["ops_pendientes_ids"] = pend_ids
                            entidades_actuales["moneda"] = mon_r
                            entidades_actuales["tipo"] = tipo_r
                        else:
                            entidades_actuales["billetera"] = operaciones[0].get("billetera")
                            entidades_actuales["tarjeta"] = operaciones[0].get("tarjeta")
                            entidades_actuales["transacciones_adicionales"] = [dict(o) for o in operaciones[1:]]

                            hay_lote_dup, m_dup, mon_dup, cat_dup = _detectar_duplicados_en_lote(entidades_actuales)
                            if hay_lote_dup:
                                b_nom_dup = operaciones[0].get("billetera") or "tu billetera"
                                m_dup_fmt = formatear_monto(float(m_dup), Moneda.USD if mon_dup == "USD" else Moneda.ARS)
                                pregunta_lote = f"Mandaste 2 movimientos iguales de {m_dup_fmt} en {cat_dup} desde {b_nom_dup}. ¿Son dos gastos distintos o se te repitió?"
                                resultado_ia["intent"] = "verificar_lote_duplicado"
                                resultado_ia["slot_filling"] = True
                                resultado_ia["datos_faltantes"] = ["confirmar_lote"]
                                entidades_actuales["tipo_flujo"] = "verificacion_lote_duplicado"
                                entidades_actuales["billetera_resuelta_nombre"] = b_nom_dup
                                resultado_ia["respuesta_usuario"] = pregunta_lote
                            else:
                                limite_rec = datetime.now(timezone.utc) - timedelta(hours=1)
                                txs_hist = db.execute(
                                    select(Transaccion).where(
                                        Transaccion.usuario_id == usuario.id,
                                        Transaccion.fecha_creacion >= limite_rec,
                                        Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                                        Transaccion.es_cuota_hija == False,
                                        Transaccion.es_padre_cuotas == False,
                                        Transaccion.es_recurrente == False,
                                        Transaccion.suscripcion_id.is_(None),
                                        Transaccion.pago_origen_id.is_(None),
                                        Transaccion.pago_resumen_vencimiento.is_(None),
                                    ).order_by(Transaccion.fecha_creacion.desc(), Transaccion.id.desc())
                                ).scalars().all()

                                tx_dup_found = None
                                op_dup_found = None
                                for op in operaciones:
                                    cat_id_chk, _ = _resolver_categoria_y_subcategoria(
                                        op.get("categoria"), usuario.id, db, tipo=op.get("tipo", "egreso")
                                    )
                                    mon_chk = Moneda.USD if op.get("moneda") == "USD" else Moneda.ARS
                                    m_chk = Decimal(str(op["monto"]))
                                    for th in txs_hist:
                                        if th.monto == m_chk and th.moneda == mon_chk and th.categoria_id == cat_id_chk:
                                            tx_dup_found = th
                                            op_dup_found = op
                                            break
                                    if tx_dup_found:
                                        break

                                if tx_dup_found and op_dup_found:
                                    hora_dup = tx_dup_found.fecha_creacion.astimezone(TZ_ARGENTINA).strftime("%H:%M")
                                    cat_disp = _nombre_corto_categoria(op_dup_found.get("categoria"))
                                    m_fmt = formatear_monto(float(op_dup_found["monto"]), Moneda.USD if op_dup_found.get("moneda") == "USD" else Moneda.ARS)
                                    pregunta_dup = f"A las {hora_dup} ya registraste {m_fmt} en {cat_disp}. ¿Es un movimiento nuevo o se te repitió?"
                                    resultado_ia["intent"] = "verificar_duplicado"
                                    resultado_ia["slot_filling"] = True
                                    resultado_ia["datos_faltantes"] = ["confirmar_duplicado"]
                                    entidades_actuales["tipo_flujo"] = "verificacion_duplicado"
                                    entidades_actuales["billetera_resuelta_nombre"] = op_dup_found.get("billetera") or "tu billetera"
                                    entidades_actuales["hora_anterior"] = hora_dup
                                    resultado_ia["respuesta_usuario"] = pregunta_dup
                                else:
                                    resultado_ia["intent"] = "registrar_transaccion"
                                    resultado_ia["slot_filling"] = False
                                    if confianza_ia_raw < 0.85:
                                        entidades_actuales["confianza_baja"] = True
                                    resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                                    resultado_ia["_asumio_principal"] = bool(operaciones[0].get("se_asumio_principal", False))
                                    resultado_ia["respuesta_usuario"] = _construir_propuesta_transaccion(
                                        entidades_actuales,
                                        billetera_nombre=operaciones[0].get("billetera"),
                                        se_asumio_principal=operaciones[0].get("se_asumio_principal", False),
                                        billeteras_usuario=billeteras_todas,
                                    )
                                    if "No se puede registrar ningún movimiento." in resultado_ia["respuesta_usuario"]:
                                        resultado_ia["intent"] = "desconocido"
                                        resultado_ia["slot_filling"] = False
                else:
                    # Chequear si fuerza débito (Tarea 4.4)
                    es_debito_explicito = any(d in m_norm for d in PALABRAS_FUERZAN_DEBITO)

                    # Chequear intención de crédito (Tareas 3 y 4)
                    tiene_cuotas = any(c in m_norm for c in ["cuota", "cuotas", "en cuotas", "pagos"])
                    tiene_palabras_credito = any(w in m_norm for w in PALABRAS_FUERZAN_CREDITO)
                    tiene_mencion_tarjeta = any(g in m_norm for g in FORMAS_GENERICAS_TARJETA)

                    # Tarjetas candidatas específicas por mención en el mensaje
                    if not es_debito_explicito and tarjetas_usuario:
                        tarjeta_match_mencion, tarjeta_mencionada_cands = _resolver_mencion_tarjeta_en_texto(
                            mensaje_texto, tarjetas_usuario
                        )
                    else:
                        tarjeta_match_mencion, tarjeta_mencionada_cands = None, []

                    # Evaluar colisión con billeteras (Tarea 3)
                    billeteras_todas = _obtener_billeteras_activas(usuario.id, db)
                    bill_raw_cands = []
                    for b in billeteras_todas:
                        b_nom_norm = normalizar_texto(b.nombre)
                        if b_nom_norm in m_norm:
                            bill_raw_cands.append(b)

                    # Si matchea a la vez billetera y tarjeta sin palabra que fuerce crédito (Tarea 3.2)
                    hay_colision_sin_credito = (
                        not es_debito_explicito
                        and not tiene_cuotas
                        and not tiene_palabras_credito
                        and len(bill_raw_cands) == 1
                        and len(tarjeta_mencionada_cands) == 1
                        and normalizar_texto(bill_raw_cands[0].nombre) == normalizar_texto(tarjeta_mencionada_cands[0].nombre)
                    )

                    if hay_colision_sin_credito:
                        b_col = bill_raw_cands[0]
                        t_col = tarjeta_mencionada_cands[0]
                        resultado_ia["intent"] = "slot_filling"
                        resultado_ia["slot_filling"] = True
                        resultado_ia["datos_faltantes"] = ["billetera_o_tarjeta"]
                        entidades_actuales["datos_faltantes"] = ["billetera_o_tarjeta"]
                        resultado_ia["respuesta_usuario"] = (
                            f"¿Te referís a la billetera o a la tarjeta de crédito?\n"
                            f"1. Billetera {b_col.nombre}\n"
                            f"2. Tarjeta de crédito {t_col.nombre}"
                        )
                        es_credito = False
                    else:
                        es_credito = (not es_debito_explicito) and (
                            tiene_cuotas
                            or tiene_palabras_credito
                            or tiene_mencion_tarjeta
                            or len(tarjeta_mencionada_cands) > 0
                            or entidades_actuales.get("tarjeta_id") is not None
                            or entidades_actuales.get("tarjeta") is not None
                        )

                    if hay_colision_sin_credito:
                        pass
                    elif es_credito:
                        # 1. Si no tiene ninguna tarjeta cargada (Tarea 4.3)
                        if not tarjetas_usuario:
                            resultado_ia["respuesta_usuario"] = (
                                "No tenés ninguna tarjeta de crédito cargada en Argentum. "
                                "Podés agregarla desde la web, o registrar este movimiento como un gasto común con alguna de tus billeteras."
                            )
                            resultado_ia["intent"] = "sin_tarjetas"
                            resultado_ia["slot_filling"] = False
                            resultado_ia["datos_faltantes"] = []
                        elif entidades_actuales.get("monto") is not None:
                            monto_actual_val = Decimal(str(entidades_actuales["monto"]))
                            cant_cuotas, monto_cuota, monto_total, es_ambiguo, err_msg = _interpretar_cuotas(
                                mensaje_texto, monto_actual_val
                            )

                            if err_msg:
                                resultado_ia["respuesta_usuario"] = err_msg
                                resultado_ia["intent"] = "error_cuotas"
                                resultado_ia["slot_filling"] = False
                                resultado_ia["datos_faltantes"] = []
                            elif es_ambiguo:
                                m_fmt = formatear_monto(float(monto_actual_val), Moneda.ARS)
                                resultado_ia["respuesta_usuario"] = f"¿Los {m_fmt} son el total o el valor de cada cuota?"
                                resultado_ia["intent"] = "slot_filling"
                                resultado_ia["slot_filling"] = True
                                resultado_ia["datos_faltantes"] = ["aclarar_cuotas"]
                                entidades_actuales["datos_faltantes"] = ["aclarar_cuotas"]
                                entidades_actuales["cantidad_cuotas"] = cant_cuotas
                                entidades_actuales["monto"] = float(monto_actual_val)
                            else:
                                # Resolver tarjeta (Tareas 2.4, 2.5)
                                t_match = None
                                cands_res = []
                                se_asumio_tarjeta = False

                                if tarjeta_match_mencion:
                                    t_match = tarjeta_match_mencion
                                    cands_res = [tarjeta_match_mencion]
                                elif len(tarjeta_mencionada_cands) > 1:
                                    t_match = None
                                    cands_res = tarjeta_mencionada_cands
                                else:
                                    # No nombró tarjeta específica: verificar si dijo forma genérica ("con la tarjeta")
                                    if tiene_mencion_tarjeta or "tarjeta" in m_norm:
                                        if len(tarjetas_usuario) == 1:
                                            t_match = tarjetas_usuario[0]
                                            cands_res = tarjetas_usuario
                                        else:
                                            t_match = None
                                            cands_res = tarjetas_usuario
                                    else:
                                        # No nombró tarjeta en absoluto (ej. "compré una tele en 12 cuotas de 80000")
                                        if len(tarjetas_usuario) == 1:
                                            t_match = tarjetas_usuario[0]
                                            cands_res = tarjetas_usuario
                                            se_asumio_tarjeta = False
                                        else:
                                            t_match = next((t for t in tarjetas_usuario if t.billetera and t.billetera.es_principal), tarjetas_usuario[0])
                                            cands_res = [t_match]
                                            se_asumio_tarjeta = True

                                if len(cands_res) > 1 and not t_match:
                                    resultado_ia["intent"] = "slot_filling"
                                    resultado_ia["slot_filling"] = True
                                    resultado_ia["datos_faltantes"] = ["tarjeta"]
                                    entidades_actuales["datos_faltantes"] = ["tarjeta"]
                                    entidades_actuales["candidatas_tarjetas_ids"] = [str(c.id) for c in cands_res]
                                    entidades_actuales["cantidad_cuotas"] = cant_cuotas
                                    entidades_actuales["monto_cuota"] = float(monto_cuota)
                                    entidades_actuales["monto_total"] = float(monto_total)
                                    entidades_actuales["monto"] = float(monto_total)
                                    resultado_ia["respuesta_usuario"] = _generar_menu_tarjetas(cands_res)
                                elif t_match:
                                    fecha_obj, _ = _resolver_y_validar_fecha(entidades_actuales.get("fecha"))
                                    primer_v = calcular_primer_vencimiento(fecha_obj, t_match.dia_cierre, t_match.dia_vencimiento, False)
                                    entidades_actuales["tarjeta_id"] = str(t_match.id)
                                    entidades_actuales["tarjeta_nombre"] = t_match.nombre
                                    entidades_actuales["tarjeta_billetera_id"] = str(t_match.billetera_id)
                                    entidades_actuales["cantidad_cuotas"] = cant_cuotas
                                    entidades_actuales["monto_cuota"] = float(monto_cuota)
                                    entidades_actuales["monto_total"] = float(monto_total)
                                    entidades_actuales["monto"] = float(monto_total)
                                    if "datos_faltantes" in entidades_actuales:
                                        entidades_actuales["datos_faltantes"] = [d for d in entidades_actuales["datos_faltantes"] if "tarjeta" not in d]

                                    propuesta_cred = _construir_propuesta_credito(
                                        entidades_actuales, t_match, cant_cuotas, monto_cuota, monto_total, primer_v, se_asumio_tarjeta=se_asumio_tarjeta
                                    )
                                    resultado_ia["intent"] = "registrar_transaccion"
                                    resultado_ia["slot_filling"] = False
                                    if confianza_ia_raw < 0.85:
                                        entidades_actuales["confianza_baja"] = True
                                    resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                                    resultado_ia["respuesta_usuario"] = propuesta_cred
                    else:
                        moneda_sol_str = entidades_actuales.get("moneda")
                        moneda_sol = Moneda.USD if moneda_sol_str == "USD" else Moneda.ARS

                        billeteras_moneda = [b for b in billeteras_todas if b.moneda == moneda_sol]
                        if not billeteras_moneda:
                            nom_moneda = "dólares" if moneda_sol == Moneda.USD else "pesos"
                            resultado_ia["respuesta_usuario"] = f"No tenés ninguna billetera en {nom_moneda} para registrar este movimiento. Podés crear una desde la web de Argentum."
                            resultado_ia["intent"] = "sin_billetera_moneda"
                            resultado_ia["slot_filling"] = False
                            resultado_ia["datos_faltantes"] = []
                        elif entidades_actuales.get("monto") is not None:
                            billetera_raw = entidades_actuales.get(clave_bill) or entidades_actuales.get(clave_otra)
                            billetera_final = None
                            se_asumio_principal = False

                            if billetera_raw:
                                b_match, cands = resolver_billetera_cascada(billetera_raw, billeteras_moneda)
                                if b_match:
                                    billetera_final = b_match
                                elif len(cands) > 1:
                                    resultado_ia["intent"] = "slot_filling"
                                    resultado_ia["slot_filling"] = True
                                    resultado_ia["datos_faltantes"] = [clave_bill]
                                    entidades_actuales["datos_faltantes"] = [clave_bill]
                                    entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                                    entidades_actuales[clave_bill] = None
                                    resultado_ia["respuesta_usuario"] = f"¿A cuál te referís?\n{_generar_menu_billeteras(cands, tipo=tipo_act)}"
                                else:
                                    # Verificar si nombró billetera de otra moneda
                                    todas = billeteras_todas
                                    b_otra, _ = resolver_billetera_cascada(billetera_raw, todas)
                                    if b_otra and b_otra.moneda != moneda_sol:
                                        nom_otra = "dólares" if b_otra.moneda == Moneda.USD else "pesos"
                                        nom_mov = "pesos" if moneda_sol == Moneda.ARS else "dólares"
                                        resultado_ia["intent"] = "slot_filling"
                                        resultado_ia["slot_filling"] = True
                                        resultado_ia["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                                        entidades_actuales[clave_bill] = None
                                        menu = _generar_menu_billeteras(billeteras_moneda, tipo=tipo_act)
                                        resultado_ia["respuesta_usuario"] = f"No podés usar una billetera en {nom_otra} para un movimiento en {nom_mov}.\n{menu}"
                                    else:
                                        resultado_ia["intent"] = "slot_filling"
                                        resultado_ia["slot_filling"] = True
                                        resultado_ia["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                                        entidades_actuales[clave_bill] = None
                                        menu = _generar_menu_billeteras(billeteras_moneda, tipo=tipo_act)
                                        resultado_ia["respuesta_usuario"] = f"No encontré esa billetera entre las tuyas.\n\n{menu}"
                            else:
                                # Usuario NO nombró billetera
                                if len(billeteras_moneda) == 1:
                                    # Exactamente una activa en esa moneda: usarla sin preguntar (2.3)
                                    billetera_final = billeteras_moneda[0]
                                    se_asumio_principal = False
                                else:
                                    # Buscar principal en esa moneda (2.2)
                                    b_ppal = next((b for b in billeteras_moneda if b.es_principal), None)
                                    if b_ppal:
                                        billetera_final = b_ppal
                                        se_asumio_principal = True
                                    else:
                                        # NUNCA ELEGIR POR DESCARTE: mostrar menú (2.1, 2.2)
                                        resultado_ia["intent"] = "slot_filling"
                                        resultado_ia["slot_filling"] = True
                                        resultado_ia["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["datos_faltantes"] = [clave_bill]
                                        entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                                        entidades_actuales[clave_bill] = None
                                        resultado_ia["respuesta_usuario"] = _generar_menu_billeteras(billeteras_moneda, tipo=tipo_act)

                            if billetera_final:
                                entidades_actuales[clave_bill] = billetera_final.nombre
                                entidades_actuales.pop(clave_otra, None)
                                if "datos_faltantes" in entidades_actuales:
                                    entidades_actuales["datos_faltantes"] = [
                                        d for d in entidades_actuales["datos_faltantes"]
                                        if d not in ("billetera_origen", "billetera_destino", "billetera")
                                    ]

                                # Chequeo 1: Lote con movimientos idénticos (Tarea 4)
                                hay_lote_dup, m_dup, mon_dup, cat_dup = _detectar_duplicados_en_lote(entidades_actuales)
                                if hay_lote_dup:
                                    m_dup_fmt = formatear_monto(float(m_dup), Moneda.USD if mon_dup == "USD" else Moneda.ARS)
                                    pregunta_lote = f"Mandaste 2 movimientos iguales de {m_dup_fmt} en {cat_dup} desde {billetera_final.nombre}. ¿Son dos gastos distintos o se te repitió?"
                                    resultado_ia["intent"] = "verificar_lote_duplicado"
                                    resultado_ia["slot_filling"] = True
                                    resultado_ia["datos_faltantes"] = ["confirmar_lote"]
                                    entidades_actuales["tipo_flujo"] = "verificacion_lote_duplicado"
                                    entidades_actuales["billetera_resuelta_nombre"] = billetera_final.nombre
                                    resultado_ia["respuesta_usuario"] = pregunta_lote
                                else:
                                    sub_cobrada, tx_cobrada = (None, None)
                                    if tipo_act == "egreso":
                                        sub_cobrada, tx_cobrada = _buscar_suscripcion_cobrada_periodo_actual(
                                            usuario_id=usuario.id,
                                            monto=Decimal(str(entidades_actuales["monto"])),
                                            nombre_servicio_o_concepto=entidades_actuales.get("servicio") or entidades_actuales.get("descripcion") or entidades_actuales.get("concepto") or mensaje_texto,
                                            db=db,
                                        )
                                    if sub_cobrada and tx_cobrada:
                                        m_fmt = formatear_monto(float(tx_cobrada.monto), tx_cobrada.moneda)
                                        aviso_dup_sub = f"Aviso: ya se cobró automáticamente {m_fmt} de {sub_cobrada.nombre} este período. ¿Es un gasto aparte o querés cancelarlo?"
                                        resultado_ia["intent"] = "verificar_duplicado_suscripcion"
                                        resultado_ia["slot_filling"] = True
                                        resultado_ia["datos_faltantes"] = ["confirmar_gasto_aparte"]
                                        entidades_actuales["tipo_flujo"] = "verificacion_duplicado_suscripcion"
                                        entidades_actuales["suscripcion_id"] = str(sub_cobrada.id)
                                        entidades_actuales["servicio_nombre"] = sub_cobrada.nombre
                                        entidades_actuales["billetera_resuelta_nombre"] = billetera_final.nombre
                                        resultado_ia["respuesta_usuario"] = aviso_dup_sub
                                    else:
                                        # Chequeo 2: Transacción previa en la última hora (Tarea 3)
                                        cat_id_chk, _ = _resolver_categoria_y_subcategoria(
                                            entidades_actuales.get("categoria"), usuario.id, db, tipo=tipo_act
                                        )
                                        tx_dup = _buscar_transaccion_duplicada_reciente(
                                            usuario_id=usuario.id,
                                            monto=Decimal(str(entidades_actuales["monto"])),
                                            moneda=moneda_sol,
                                            categoria_id=cat_id_chk,
                                            db=db,
                                        )
                                        if tx_dup:
                                            hora_dup = tx_dup.fecha_creacion.astimezone(TZ_ARGENTINA).strftime("%H:%M")
                                            cat_disp = _nombre_corto_categoria(entidades_actuales.get("categoria"))
                                            m_fmt = formatear_monto(float(entidades_actuales["monto"]), moneda_sol)
                                            pregunta_dup = f"A las {hora_dup} ya registraste {m_fmt} en {cat_disp}. ¿Es un movimiento nuevo o se te repitió?"
                                            resultado_ia["intent"] = "verificar_duplicado"
                                            resultado_ia["slot_filling"] = True
                                            resultado_ia["datos_faltantes"] = ["confirmar_duplicado"]
                                            entidades_actuales["tipo_flujo"] = "verificacion_duplicado"
                                            entidades_actuales["billetera_resuelta_nombre"] = billetera_final.nombre
                                            entidades_actuales["hora_anterior"] = hora_dup
                                            resultado_ia["respuesta_usuario"] = pregunta_dup
                                        else:
                                            resultado_ia["intent"] = "registrar_transaccion"
                                            resultado_ia["slot_filling"] = False
                                            if confianza_ia_raw < 0.85:
                                                entidades_actuales["confianza_baja"] = True
                                            resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                                            resultado_ia["_asumio_principal"] = se_asumio_principal
                                            resultado_ia["respuesta_usuario"] = _construir_propuesta_transaccion(
                                                entidades_actuales,
                                                billetera_final.nombre,
                                                se_asumio_principal=se_asumio_principal,
                                                billetera_moneda=billetera_final.moneda,
                                            )
                                            if "No se puede registrar ningún movimiento." in resultado_ia["respuesta_usuario"]:
                                                resultado_ia["intent"] = "desconocido"
                                                resultado_ia["slot_filling"] = False

            # Enriquecer respuesta con datos reales para intents de consulta
            intent_detectado = resultado_ia.get("intent")

            intent_detectado = enriquecer_respuesta_por_intent(
                intent_detectado, resultado_ia, mensaje_texto, usuario, db
            )

            if intent_detectado == "aportar_meta":
                entidades_ia = resultado_ia.get("entidades") or {}
                monto_ia = entidades_ia.get("monto")
                meta_ia = entidades_ia.get("meta") or entidades_ia.get("descripcion")
                bill_ia = entidades_ia.get("billetera_origen") or entidades_ia.get("billetera")
                monto_val = Decimal(str(monto_ia)) if monto_ia else None
                meta_obj, estado_meta, cands = _buscar_meta_activa_por_nombre(usuario.id, meta_ia, db)
                if estado_meta == "no_metas":
                    resultado_ia["respuesta_usuario"] = "No tenés metas activas."
                    resultado_ia["slot_filling"] = False
                elif estado_meta == "no_encontrada":
                    nombre_disp = meta_ia or "esa"
                    resultado_ia["respuesta_usuario"] = f"No encontré ninguna meta con el nombre '{nombre_disp}'. Podés consultar tus metas con 'mis metas'."
                    resultado_ia["slot_filling"] = False
                elif estado_meta == "ambigua":
                    nombres = ", ".join(f"'{m.nombre}'" for m in cands)
                    resultado_ia["respuesta_usuario"] = f"Encontré más de una meta parecida: {nombres}. ¿A cuál querés aportar?"
                    resultado_ia["slot_filling"] = True
                    resultado_ia["entidades"]["intent_origen"] = "aportar_meta"
                    resultado_ia["entidades"]["meta_ambigua_cands"] = [str(m.id) for m in cands]
                    if monto_val:
                        resultado_ia["entidades"]["monto"] = float(monto_val)
                elif meta_obj and monto_val:
                    bills_mon = _obtener_billeteras_activas(usuario.id, db, moneda=meta_obj.moneda)
                    billetera_obj = None
                    if bill_ia:
                        billetera_obj, _ = resolver_billetera_cascada(bill_ia, bills_mon)
                    if not billetera_obj:
                        billetera_obj = next((b for b in bills_mon if b.es_principal), None) or (bills_mon[0] if len(bills_mon) == 1 else None)
                    if not billetera_obj:
                        enc = f"¿Desde qué billetera querés aportar a '{meta_obj.nombre}'?"
                        resultado_ia["respuesta_usuario"] = _generar_menu_billeteras(bills_mon, tipo="egreso", encabezado=enc)
                        resultado_ia["slot_filling"] = True
                    elif billetera_obj.saldo_actual < monto_val:
                        monto_disp = formatear_monto(float(billetera_obj.saldo_actual), billetera_obj.moneda)
                        resultado_ia["respuesta_usuario"] = f"Saldo insuficiente en la billetera '{billetera_obj.nombre}'. Tenés {monto_disp}."
                        resultado_ia["slot_filling"] = False
                    else:
                        monto_fmt = formatear_monto(float(monto_val), meta_obj.moneda)
                        resultado_ia["respuesta_usuario"] = f"Voy a aportar {monto_fmt} a tu meta '{meta_obj.nombre}' desde {billetera_obj.nombre}. ¿Confirmás?"
                        resultado_ia["entidades"] = {
                            "meta_id": str(meta_obj.id),
                            "meta_nombre": meta_obj.nombre,
                            "billetera_id": str(billetera_obj.id),
                            "billetera_nombre": billetera_obj.nombre,
                            "monto": float(monto_val),
                            "moneda": meta_obj.moneda.value,
                        }
                        resultado_ia["slot_filling"] = False

            transaccion_id = _ejecutar_intent(resultado_ia, usuario, db)

            # Si se confirmó o intentó confirmar, usar el mensaje del gestor de confirmación
            if intent_detectado == "confirmar" and resultado_ia.get("_mensaje_confirmacion_directo"):
                resultado_ia["respuesta_usuario"] = resultado_ia["_mensaje_confirmacion_directo"]

            # Si se canceló, asegurar tono rioplatense
            if intent_detectado == "cancelar":
                resultado_ia["respuesta_usuario"] = "Listo, cancelado."

            # Si hubo descarte por cambio de tema, anteponer aviso en una línea (Cierre Punto 4)
            if aviso_cambio_tema and resultado_ia.get("respuesta_usuario"):
                resp_actual = resultado_ia["respuesta_usuario"]
                if resp_actual.startswith("¿"):
                    resultado_ia["respuesta_usuario"] = f"{aviso_cambio_tema}\n\n{resp_actual}"
                else:
                    resultado_ia["respuesta_usuario"] = f"{aviso_cambio_tema}\n{resp_actual}"

            slot_activo = resultado_ia.get("slot_filling", False)
            confianza_val = resultado_ia.get("confianza", 0.0)
            try:
                confianza_float = max(0.0, min(1.0, float(confianza_val)))
                confianza_dec = Decimal(f"{confianza_float:.3f}")
            except (ValueError, TypeError):
                confianza_dec = Decimal("0.000")

            if es_imagen:
                tipo_msg_guardar = TipoMensajeWpp.IMAGEN
                mensaje_usuario_guardar = caption_imagen
                transcripcion_guardar = mensaje_texto
                if isinstance(resultado_ia.get("entidades"), dict):
                    resultado_ia["entidades"]["origen_imagen"] = True
            elif transcripcion:
                tipo_msg_guardar = TipoMensajeWpp.AUDIO
                mensaje_usuario_guardar = transcripcion
                transcripcion_guardar = transcripcion
            else:
                tipo_msg_guardar = TipoMensajeWpp.TEXTO
                mensaje_usuario_guardar = mensaje_texto
                transcripcion_guardar = None

            nueva_conv = ConversacionWpp(
                usuario_id=usuario.id,
                wamid=wamid,
                mensaje_usuario=mensaje_usuario_guardar,
                tipo_mensaje=tipo_msg_guardar,
                transcripcion=transcripcion_guardar,
                mensaje_bot=resultado_ia["respuesta_usuario"],
                intent_detectado=resultado_ia.get("intent"),
                entidades=resultado_ia.get("entidades"),
                accion_ejecutada=str(transaccion_id) if transaccion_id else None,
                confianza=confianza_dec,
                slot_filling_activo=slot_activo,
                slot_filling_estado=resultado_ia.get("entidades") if slot_activo else None,
            )
            db.add(nueva_conv)
            db.flush()

            es_dup = bool(resultado_ia.get("intent") in ("verificar_duplicado", "verificar_lote_duplicado", "verificar_duplicado_suscripcion"))
            asumio_ppal = bool(resultado_ia.get("_asumio_principal", False))
            registrado_dir, msg_dir = _registrar_directo_si_corresponde(
                usuario,
                db,
                nueva_conv,
                es_credito=es_credito,
                es_imagen=es_imagen,
                es_duplicado=es_dup,
                se_asumio_principal=asumio_ppal,
            )
            if registrado_dir or nueva_conv.accion_ejecutada == "error":
                resultado_ia["respuesta_usuario"] = msg_dir
            else:
                db.commit()

            # Envío saliente vía Meta Graph API
            t_envio_start = time.perf_counter()
            enviar_whatsapp(from_number, resultado_ia["respuesta_usuario"])
            t_envio_end = time.perf_counter()
            logger.info("[LATENCIA][ENVIO_META] Envío de mensaje: %.2fs", t_envio_end - t_envio_start)

            t_total = time.perf_counter() - t_inicio
            logger.info(
                "[LATENCIA][TOTAL][TIPO=%s] Duración total del webhook: %.2fs",
                msg_type.upper(),
                t_total,
            )

            return

        except Exception as e:
            db.rollback()
            logger.error("whatsapp_webhook_error", error=str(e), exc_info=True)
            try:
                if from_number:
                    enviar_whatsapp(
                        from_number, "Hubo un problema al procesar tu mensaje. Intentá de nuevo."
                    )
            except Exception:
                logger.exception("Error al enviar mensaje de fallback")
            return

    finally:
        db.close()


def _procesar_webhook_whatsapp_sync(body_bytes: bytes, t_inicio: float) -> PlainTextResponse:
    """
    Wrapper de compatibilidad para suites de regresión que ejecutan de forma síncrona.
    """
    resp, datos_mensaje = _preprocesar_webhook_whatsapp_sync(body_bytes, t_inicio)
    if datos_mensaje:
        _procesar_mensaje_whatsapp_background(datos_mensaje)
    return resp


@router.post("/webhook", response_class=PlainTextResponse)
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> PlainTextResponse:
    """
    Webhook de WhatsApp Cloud API (Meta Graph API).
    Valida firma HMAC-SHA256 en el event loop, ejecuta la deduplicación de forma síncrona
    en thread worker (Parte A) respondiendo inmediatamente HTTP 200, y delega el procesamiento
    pesado de IA, transcripción y envío (Parte B) a BackgroundTasks.
    """
    t_inicio = time.perf_counter()
    body_bytes = await request.body()

    # Validación de firma Meta HMAC-SHA256 obligatoria (Fail-Closed)
    if not settings.WHATSAPP_APP_SECRET:
        logger.error("WHATSAPP_APP_SECRET no configurado en el servidor")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma inválida o no configurada",
        )

    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Falta o es inválido el header X-Hub-Signature-256")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma inválida",
        )

    expected_sig = signature_header.split("sha256=", 1)[1]
    calculated_sig = hmac.new(
        settings.WHATSAPP_APP_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, calculated_sig):
        logger.warning("Firma Meta HMAC-SHA256 no coincide")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Firma inválida",
        )

    resp, datos_mensaje = await anyio.to_thread.run_sync(
        _preprocesar_webhook_whatsapp_sync, body_bytes, t_inicio
    )
    if datos_mensaje:
        background_tasks.add_task(_procesar_mensaje_whatsapp_background, datos_mensaje)
    return resp


class TestIAMessageRequest(BaseModel):
    mensaje: str


@router.post("/test")
def test_ia(
    body: TestIAMessageRequest | None = None,
    mensaje: str | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
) -> dict:
    texto = (body.mensaje if body else None) or mensaje
    if not texto:
        raise HTTPException(status_code=400, detail="Debe ingresar un mensaje para probar la IA.")
    return ai_service.procesar_mensaje(
        mensaje=texto,
        usuario=current_user,
        db=db,
        historial=None,
        estado_previo=None,
    )
