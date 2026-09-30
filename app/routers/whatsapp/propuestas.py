"""
app/routers/whatsapp/propuestas.py — Construcción de propuestas de transacciones y crédito para WhatsApp.
"""
import re
from datetime import date
from decimal import Decimal

from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.usuario import Moneda
from app.routers.whatsapp.constantes import MESES_ES_GEN
from app.routers.whatsapp.parsers import (
    _formatear_fecha_natural,
    _nombre_corto_categoria,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.resolvers_cascada import (
    FORMAS_GENERICAS_TARJETA,
    _validar_item_movimiento,
    construir_alias_tarjeta,
    resolver_tarjeta_cascada,
)
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto


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


def _unir_items_multilinea(items: list[str], encabezado: str, cierre: str) -> str:
    """
    Une los items de un lote en formato de lista con viñetas nativas de WhatsApp:
    encabezado

    - item 1
    - item 2

    cierre
    """
    def _limpiar(it: str) -> str:
        if it.startswith("- "):
            return it[2:]
        if it.startswith("• "):
            return it[2:]
        return it

    items_formateados = [f"- {_limpiar(it)}" for it in items]
    bloque_items = "\n".join(items_formateados)
    partes = [encabezado, bloque_items]
    if cierre:
        partes.append(cierre)
    return "\n\n".join(partes)


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

        if misma_billetera and b_comun:
            if mismo_tipo:
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
                mov_palabra = "movimientos" if total_movs != 1 else "movimiento"
                encabezado = f"Voy a anotar {total_movs} {mov_palabra}{origen_str}:"
                texto = _unir_items_multilinea(items_desc, encabezado, "¿Va?")
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
                    signo = "+" if it.get("tipo") == "ingreso" else "-"
                    items_desc.append(f"*{signo}{m_fmt}* en {cat_d}{f_disp}")

                mov_palabra = "movimientos" if total_movs != 1 else "movimiento"
                encabezado = f"Voy a anotar {total_movs} {mov_palabra} en *{b_comun}*:"
                texto = _unir_items_multilinea(items_desc, encabezado, "¿Va?")
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
            mov_palabra = "movimientos" if total_movs != 1 else "movimiento"
            encabezado = f"Voy a anotar {total_movs} {mov_palabra}:"
            texto = _unir_items_multilinea(items_desc, encabezado, "¿Va?")
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
