"""
app/routers/whatsapp/propuestas.py — Construcción de propuestas de transacciones y crédito para WhatsApp.
"""
import re
from datetime import date
from decimal import Decimal

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.reglas_texto_wpp import fecha_corta_wpp, nombre_tarjeta_wpp
from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import TarjetaCredito
from app.models.usuario import Moneda
from app.routers.whatsapp.db_lookups import _resolver_categoria_y_subcategoria
from app.routers.whatsapp.parsers import (
    _nombre_corto_categoria,
    _resolver_y_validar_fecha,
)
from app.utils.fecha import hoy_argentina
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


def _resolver_cat_display_wpp(
    cat_raw: str | None,
    db: Session | None,
    usuario_id: UUID | None,
    tipo: str = "egreso",
) -> str:
    """Resuelve el nombre visible de subcategoría/categoría igual que el registro."""
    if db is not None and usuario_id is not None:
        cat_id, subcat_id = _resolver_categoria_y_subcategoria(
            cat_raw, usuario_id, db, tipo=tipo
        )
        if subcat_id:
            sub_db = db.get(Subcategoria, subcat_id)
            if sub_db:
                return sub_db.nombre
        if cat_id:
            cat_db = db.get(Categoria, cat_id)
            if cat_db:
                return cat_db.nombre
    return _nombre_corto_categoria(cat_raw) if cat_raw else "Otros"


def _construir_propuesta_credito(
    entidades: dict,
    tarjeta: TarjetaCredito,
    cant_cuotas: int,
    monto_cuota: Decimal,
    monto_total: Decimal,
    fecha_vencimiento: date,
    se_asumio_tarjeta: bool = False,
    db: Session | None = None,
    usuario_id: UUID | None = None,
) -> str:
    """Construye la propuesta de consumo con tarjeta de crédito (D5)."""
    moneda_enum = tarjeta.moneda
    cuota_fmt = formatear_monto(float(monto_cuota), moneda_enum)
    total_fmt = formatear_monto(float(monto_total), moneda_enum, con_decimales=False)
    cat_disp = _resolver_cat_display_wpp(entidades.get("categoria"), db, usuario_id)
    venc_str = fecha_vencimiento.strftime("%d/%m")
    nom_t = nombre_tarjeta_wpp(tarjeta)

    if cant_cuotas > 1:
        total_cuotas_fmt = formatear_monto(float(monto_total), moneda_enum)
        return (
            f"{cant_cuotas} cuotas de {cuota_fmt} en {cat_disp} "
            f"con la {nom_t} ({total_cuotas_fmt}).\n"
            f"La primera vence el {venc_str}. ¿Las anoto?"
        )
    return (
        f"{total_fmt} en {cat_disp} con la {nom_t}, en un pago.\n"
        f"Vence el {venc_str}. ¿Lo anoto?"
    )


def _unir_items_multilinea(items: list[str], encabezado: str, cierre: str = "") -> str:
    """Une los items de un lote con viñetas nativas de WhatsApp sin renglones vacíos."""
    def _limpiar(it: str) -> str:
        if it.startswith("- ") or it.startswith("• "):
            return it[2:]
        return it

    lineas = [encabezado] + [f"- {_limpiar(it)}" for it in items]
    if cierre:
        lineas.append(cierre)
    return "\n".join(lineas)


def _construir_propuesta_transaccion(
    entidades: dict,
    billetera_nombre: str | None = None,
    se_asumio_principal: bool = False,
    billetera_moneda: Moneda | None = None,
    billeteras_usuario: list[Billetera] | None = None,
    db: Session | None = None,
    usuario_id: UUID | None = None,
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

    factura_data = entidades.get("factura")
    if factura_data and isinstance(factura_data, dict) and factura_data.get("vencimientos"):
        from app.routers.whatsapp.factura_wpp import texto_propuesta_factura

        return texto_propuesta_factura(
            entidades=entidades,
            billetera_nombre=b_nom_0,
            se_asumio_principal=se_asumio_principal,
            billetera_moneda=b_mon_0,
            billeteras_usuario=billeteras_usuario,
        )

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
            lineas_error.append("No pude anotar ninguno.")
            return "\n".join(lineas_error)
    else:
        entidades["monto"] = item_ppal_limpio["monto"]
        entidades["transacciones_adicionales"] = adicionales_validos

    hoy = hoy_argentina()
    if adicionales_validos:
        todos_items = [item_ppal_limpio] + adicionales_validos
        total_movs = len(todos_items)

        billeteras_items = [it.get("billetera") for it in todos_items if it.get("billetera")]
        misma_billetera = (
            len(set(billeteras_items)) <= 1
            and not any(it.get("tarjeta") for it in todos_items)
        )
        b_comun = billeteras_items[0] if billeteras_items else billetera_nombre
        mismo_tipo = len(set(it.get("tipo", "egreso") for it in todos_items)) == 1
        tipos_mezclados = (
            any(it.get("tipo") == "ingreso" for it in todos_items)
            and any(it.get("tipo", "egreso") == "egreso" for it in todos_items)
        )

        items_desc: list[str] = []
        for it in todos_items:
            tipo_it = it.get("tipo", "egreso")
            mon_it = Moneda.USD if it.get("moneda") == "USD" else Moneda.ARS
            m_fmt = formatear_monto(
                float(it["monto"]), mon_it, con_decimales=False
            )
            cat_d = _resolver_cat_display_wpp(
                it.get("categoria"), db, usuario_id, tipo=tipo_it
            )
            fecha_obj, av = _resolver_y_validar_fecha(it.get("fecha"))
            if av and av not in avisos_fechas:
                avisos_fechas.append(av)
            f_corta = fecha_corta_wpp(fecha_obj, hoy)
            f_suf = f" · {f_corta}" if f_corta else ""
            t_nom = it.get("tarjeta")
            b_nom = it.get("billetera") or b_comun
            ing_suf = " (ingreso)" if (tipos_mezclados and tipo_it == "ingreso") else ""

            if misma_billetera and b_comun:
                items_desc.append(f"{m_fmt} en {cat_d}{ing_suf}{f_suf}")
            elif t_nom:
                t_limpio = t_nom[3:] if t_nom.lower().startswith("la ") else t_nom
                items_desc.append(f"{m_fmt} en {cat_d}, en un pago · {t_limpio}{f_suf}")
            else:
                b_suf = f" · {b_nom}" if b_nom else ""
                items_desc.append(f"{m_fmt} en {cat_d}{ing_suf}{b_suf}{f_suf}")

        if misma_billetera and b_comun:
            if mismo_tipo:
                tipo_c = todos_items[0].get("tipo", "egreso")
                sust = "ingresos" if tipo_c == "ingreso" else "gastos"
                prep = "en" if tipo_c == "ingreso" else "desde"
                encabezado = f"¿Anoto estos {total_movs} {sust} {prep} {b_comun}?"
            else:
                encabezado = f"¿Anoto estos {total_movs} movimientos en {b_comun}?"
        else:
            if mismo_tipo:
                tipo_c = todos_items[0].get("tipo", "egreso")
                sust = "ingresos" if tipo_c == "ingreso" else "gastos"
                encabezado = f"¿Anoto estos {total_movs} {sust}?"
            else:
                encabezado = f"¿Anoto estos {total_movs} movimientos?"
        texto = _unir_items_multilinea(items_desc, encabezado)
    else:
        moneda_prop = Moneda.USD if item_ppal_limpio.get("moneda") == "USD" else Moneda.ARS
        tipo = item_ppal_limpio.get("tipo", "egreso")
        cat_display = _resolver_cat_display_wpp(
            item_ppal_limpio.get("categoria"), db, usuario_id, tipo=tipo
        )
        b_final_nom = (
            item_ppal_limpio.get("billetera")
            or billetera_nombre
            or "tu cuenta"
        )
        fecha_p_obj, aviso_p = _resolver_y_validar_fecha(item_ppal_limpio.get("fecha"))
        if aviso_p and aviso_p not in avisos_fechas:
            avisos_fechas.append(aviso_p)
        f_corta = fecha_corta_wpp(fecha_p_obj, hoy)
        monto_str = formatear_monto(
            float(item_ppal_limpio["monto"]), moneda_prop, con_decimales=False
        )

        t_nom_ppal = item_ppal_limpio.get("tarjeta")
        if t_nom_ppal:
            f_disp = f" ({f_corta})" if f_corta else ""
            texto = (
                f"{monto_str} en {cat_display} con la {t_nom_ppal}, "
                f"en un pago{f_disp}. ¿Lo anoto?"
            )
        elif tipo == "ingreso":
            if cat_display and f_corta:
                detalle = f"{cat_display}, {f_corta}"
            else:
                detalle = cat_display or f_corta
            par = f" ({detalle})" if detalle else ""
            texto = f"¿Anoto un ingreso de {monto_str} en {b_final_nom}{par}?"
        else:
            f_disp = f" ({f_corta})" if f_corta else ""
            en_cat = f" en {cat_display}" if cat_display else ""
            texto = f"¿Anoto {monto_str}{en_cat} desde {b_final_nom}{f_disp}?"

    lineas: list[str] = []
    if avisos_descarte:
        lineas.extend(avisos_descarte)
    if avisos_fechas:
        lineas.extend(avisos_fechas)
    lineas.append(texto)

    return "\n".join(lineas)
