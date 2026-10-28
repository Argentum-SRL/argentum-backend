# -*- coding: utf-8 -*-
"""
Módulo para el manejo de gastos en dólares desde billeteras en pesos por WhatsApp.
Permite proponer gastos en pesos con cotización oficial + percepción, habilitar
la corrección previa del monto en pesos y registrar la transacción con trazabilidad
de moneda y monto original en dólares.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.models.billetera import Billetera
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
from app.routers.whatsapp.parsers import (
    _formatear_fecha_natural,
    _nombre_corto_categoria,
    _parsear_monto_argentino,
    _resolver_fecha_transaccion,
)
from app.routers.whatsapp.resolvers_cascada import resolver_billetera_cascada
from app.services import dolar_service, whatsapp_service
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto


def billetera_pesos_para_usd(
    entidades: dict,
    billeteras_todas: list[Billetera],
    clave_bill: str,
    clave_otra: str,
) -> Billetera | None:
    """
    Determina si un gasto en dólares puede debitarse de una billetera en pesos.

    Reglas:
    - raw = entidades[clave_bill] o entidades[clave_otra] o entidades["billetera"].
    - Con raw: resolver_billetera_cascada(raw, billeteras_todas); si da una sola
      billetera y es en pesos, esa; si no, None.
    - Sin raw: si hay alguna billetera en dólares entre billeteras_todas, None
      (sale de la de dólares como antes); si no, la principal en pesos;
      si no hay principal y hay una sola en pesos, esa; si no, None.
    """
    raw = entidades.get(clave_bill) or entidades.get(clave_otra) or entidades.get("billetera")

    if raw:
        b_match, candidatas = resolver_billetera_cascada(raw, billeteras_todas)
        if len(candidatas) == 1:
            cand = candidatas[0]
            moneda_cand = cand.moneda.value if hasattr(cand.moneda, "value") else str(cand.moneda)
            if moneda_cand == "ARS":
                return cand
        return None

    # Sin raw: verificar si el usuario tiene billeteras activas en dólares
    billeteras_usd = [
        b for b in billeteras_todas
        if (b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)) == "USD"
    ]
    if billeteras_usd:
        return None

    # Billeteras en pesos
    billeteras_pesos = [
        b for b in billeteras_todas
        if (b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)) == "ARS"
    ]

    # Buscar la principal en pesos
    principales_pesos = [b for b in billeteras_pesos if b.es_principal]
    if principales_pesos:
        return principales_pesos[0]

    # Si no hay principal y hay una sola en pesos, esa
    if len(billeteras_pesos) == 1:
        return billeteras_pesos[0]

    return None


def texto_propuesta_usd_pesos(entidades: dict, billetera_nombre: str) -> str:
    """
    Construye el texto de la propuesta para un gasto en dólares desde una billetera en pesos.

    Variantes:
    - Con monto y monto_editado falso:
      'Voy a anotar {usd} en {cat} desde {billetera}{fecha}: son {ars} (oficial {cot} + {percepcion}%).\\n¿Confirmás? Si te cobraron otro monto en pesos, decime cuánto.'
    - Con monto_editado verdadero:
      'Voy a anotar {usd} en {cat} desde {billetera}{fecha}: son {ars}.\\n¿Confirmás?'
    - Sin monto (no hay cotización):
      'Voy a anotar {usd} en {cat} desde {billetera}{fecha}.\\n¿Cuántos pesos te cobraron?'
    """
    monto_orig = entidades.get("monto_original") or entidades.get("monto") or 0
    usd = formatear_monto(float(monto_orig), Moneda.USD)

    cat = _nombre_corto_categoria(entidades.get("categoria"))

    fecha_obj = _resolver_fecha_transaccion(entidades.get("fecha"))
    fecha_nat = _formatear_fecha_natural(fecha_obj)
    fecha_str = f" ({fecha_nat})" if fecha_nat else ""

    monto = entidades.get("monto")
    monto_editado = bool(entidades.get("monto_editado"))

    if monto is not None and not monto_editado:
        ars = formatear_monto(float(monto), Moneda.ARS)
        cot_val = entidades.get("cotizacion_sugerida") or 0
        cot = formatear_monto(float(cot_val), Moneda.ARS)
        percepcion = entidades.get("percepcion", 30)
        return (
            f"Voy a anotar {usd} en {cat} desde {billetera_nombre}{fecha_str}: "
            f"son {ars} (oficial {cot} + {percepcion}%).\n"
            f"¿Confirmás? Si te cobraron otro monto en pesos, decime cuánto."
        )

    if monto is not None and monto_editado:
        ars = formatear_monto(float(monto), Moneda.ARS)
        return (
            f"Voy a anotar {usd} en {cat} desde {billetera_nombre}{fecha_str}: "
            f"son {ars}.\n"
            f"¿Confirmás?"
        )

    # Sin monto (no hubo cotización disponible)
    return (
        f"Voy a anotar {usd} en {cat} desde {billetera_nombre}{fecha_str}.\n"
        f"¿Cuántos pesos te cobraron?"
    )


def armar_propuesta_usd_pesos(
    ctx: ContextoMensaje,
    entidades: dict,
    billetera: Billetera,
    clave_bill: str,
    clave_otra: str,
) -> None:
    """
    Configura el contexto y entidades para la propuesta de gasto en dólares desde pesos.
    """
    monto_usd_raw = entidades.get("monto")
    monto_usd_dec = Decimal(str(monto_usd_raw))

    fecha_tx = _resolver_fecha_transaccion(entidades.get("fecha"))
    sug = dolar_service.sugerir_pesos_para_usd(ctx.db, monto_usd_dec, fecha=fecha_tx)

    monto_pesos = float(sug["monto_pesos"]) if sug.get("monto_pesos") is not None else None
    cotizacion = float(sug["cotizacion"]) if sug.get("cotizacion") is not None else None
    percepcion = sug.get("percepcion", 30)

    entidades["moneda"] = "ARS"
    entidades["monto"] = monto_pesos
    entidades["moneda_original"] = "USD"
    entidades["monto_original"] = float(monto_usd_dec)
    entidades["cotizacion_sugerida"] = cotizacion
    entidades["percepcion"] = percepcion
    entidades["tipo_flujo"] = "propuesta_usd_pesos"
    entidades["monto_editado"] = False
    entidades[clave_bill] = billetera.nombre
    entidades["billetera"] = billetera.nombre
    entidades.pop(clave_otra, None)

    # Limpiar datos faltantes relacionados a billetera
    if "datos_faltantes" in entidades and isinstance(entidades["datos_faltantes"], list):
        entidades["datos_faltantes"] = [
            k for k in entidades["datos_faltantes"]
            if k not in (clave_bill, clave_otra, "billetera", "billetera_origen", "billetera_destino")
        ]

    resultado_ia = ctx.resultado_ia
    resultado_ia["intent"] = "registrar_transaccion"
    resultado_ia["slot_filling"] = False
    resultado_ia["datos_faltantes"] = []

    confianza_previa = resultado_ia.get("confianza")
    try:
        conf_float = float(confianza_previa) if confianza_previa is not None else 0.85
    except (ValueError, TypeError):
        conf_float = 0.85
    resultado_ia["confianza"] = max(conf_float, 0.85)
    resultado_ia["_asumio_principal"] = False
    resultado_ia["respuesta_usuario"] = texto_propuesta_usd_pesos(entidades, billetera.nombre)


def extraer_monto_pesos(texto: str | None) -> Decimal | None:
    """
    Extrae un monto en pesos corregido por el usuario antes de confirmar.

    Reglas:
    - norm = normalizar_texto(texto)
    - None si está vacío o si tiene 'dolar', 'usd', 'us$' o 'u$s'
    - Se le saca adelante 'no, ' o 'no ' y luego una de:
      'fueron', 'fue', 'son', 'eran', 'era', 'me cobraron', 'me cobro', 'pague', 'en total'
    - Atrás ' pesos' o ' en pesos'
    - Lo que queda debe ser solo un monto (re.fullmatch r'\\$?\\s?\\d[\\d.,]*\\s?(?:k|mil|lucas?)?')
    - Valor sale de _parsear_monto_argentino y debe ser > 0 y <= MAX_MONTO_INTEGRIDAD
    """
    if not texto:
        return None
    norm = normalizar_texto(texto)
    if not norm:
        return None

    # Descartar si el usuario especifica moneda extranjera
    if any(d in norm for d in ("dolar", "usd", "us$", "u$s")):
        return None

    t = norm.strip()

    # Quitar prefijo inicial "no, " o "no "
    if t.startswith("no, "):
        t = t[4:].strip()
    elif t.startswith("no "):
        t = t[3:].strip()

    # Quitar verbos o frases de monto
    prefijos = [
        "me cobraron",
        "me cobro",
        "en total",
        "fueron",
        "eran",
        "pague",
        "fue",
        "son",
        "era",
    ]
    for p in prefijos:
        if t.startswith(p + " "):
            t = t[len(p) + 1:].strip()
            break
        elif t == p:
            return None

    # Quitar sufijos " pesos" o " en pesos"
    if t.endswith(" en pesos"):
        t = t[:-9].strip()
    elif t.endswith(" pesos"):
        t = t[:-6].strip()

    patron = r"\$?\s?\d[\d.,]*\s?(?:k|mil|lucas?)?"
    if not re.fullmatch(patron, t, flags=re.IGNORECASE):
        return None

    val = _parsear_monto_argentino(t)
    if val is None or val <= Decimal("0") or val > MAX_MONTO_INTEGRIDAD:
        return None
    return val


def manejar_monto_pesos_usd(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Handler determinístico para capturar la corrección del monto en pesos de una
    propuesta de gasto en dólares desde pesos.
    """
    monto_extraido = extraer_monto_pesos(mensaje_texto)
    if monto_extraido is None:
        return False

    prop = _buscar_propuesta_confirmable_mas_reciente(usuario.id, db)
    if not prop or prop.intent_detectado != "registrar_transaccion":
        return False

    entidades_prop = prop.entidades or {}
    if entidades_prop.get("tipo_flujo") != "propuesta_usd_pesos":
        return False

    entidades_nuevas = dict(entidades_prop)
    entidades_nuevas["monto"] = float(monto_extraido)
    entidades_nuevas["monto_editado"] = True

    billetera_nom = entidades_nuevas.get("billetera") or entidades_nuevas.get("billetera_origen") or "tu billetera"
    msg_bot = texto_propuesta_usd_pesos(entidades_nuevas, billetera_nom)

    prop.entidades = entidades_nuevas
    prop.mensaje_bot = msg_bot
    prop.fecha = datetime.now(timezone.utc)

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_bot,
        intent_detectado="editar_monto_pesos",
        entidades={"propuesta_id": str(prop.id), "monto": float(monto_extraido)},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_bot)
    return True

