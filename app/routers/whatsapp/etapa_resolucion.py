"""
Etapa de resolución de billeteras, tarjetas, cuotas, lotes y propuestas para WhatsApp:
1. Resolución y validación de operaciones en lote (tope máximo, mezcla, duplicados).
2. Detección y resolución de pagos con tarjeta de crédito y cálculo de cuotas.
3. Detección y resolución de pagos en débito/efectivo, detección de duplicados en la última hora y suscripciones ya cobradas.
4. Construcción de propuestas de transacciones y créditos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
import structlog
from sqlalchemy import select

from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.core.entidades import entidad_de_texto
from app.core.jerga import monedas_dolares_rx
from app.core.reglas_texto_wpp import nombre_tarjeta_wpp
from app.models.usuario import Moneda
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.db_lookups import (
    _buscar_suscripcion_cobrada_periodo_actual,
    _buscar_transaccion_duplicada_reciente,
    _obtener_billeteras_activas,
    _obtener_tarjetas_activas,
    _resolver_categoria_y_subcategoria,
)
from app.routers.whatsapp.detectors import _debe_bloquear_mezcla_lote
from app.routers.whatsapp.regla_registro import confianza_ia_baja, montos_del_texto
from app.routers.whatsapp.parsers import (
    _interpretar_cuotas,
    _nombre_corto_categoria,
    _resolver_fecha_transaccion,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.propuestas import (
    _construir_propuesta_credito,
    _construir_propuesta_transaccion,
    _resolver_mencion_tarjeta_en_texto,
)
from app.routers.whatsapp.resolvers_cascada import (
    ALIAS_BANCOS_ARGENTINOS,
    ALIAS_BILLETERAS,
    FORMAS_GENERICAS_TARJETA,
    _buscar_billetera_en_texto,
    _detectar_duplicados_en_lote,
    _generar_menu_billeteras,
    _generar_menu_tarjetas,
    resolver_billetera_cascada,
    resolver_tarjeta_cascada,
)
from app.services import calendario_tarjeta_service
from app.utils.fecha import TZ_ARGENTINA
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto

logger = structlog.get_logger(__name__)

MAX_MOVIMIENTOS_POR_LOTE = 10
MSG_TOPE_MOVIMIENTOS_SUPERADO = "Mandame hasta 10 movimientos por mensaje."
MSG_NO_MEZCLAR_TRANSFERENCIAS = (
    "Mandame aparte las transferencias, retiros de cajero y compras de dólares."
)

PALABRAS_FUERZAN_CREDITO = [
    "credito", "crédito",
    "visa", "master", "mastercard", "amex", "american express", "american",
    "naranja", "cabal", "tarje", "la tarje", "la de credito", "la de crédito",
    "la credi", "tarjeta de credito", "tarjeta de crédito"
]

PALABRAS_FUERZAN_DEBITO = [
    "debito", "débito", "tarjeta de debito", "tarjeta de débito",
    "debito automatico", "débito automático"
]


def _parsear_monto_ia(valor: object) -> Decimal | None:
    """Convierte un monto de la IA a Decimal positivo o None si no es claro."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float, Decimal)):
        try:
            d = Decimal(str(valor))
        except (InvalidOperation, ValueError, TypeError):
            return None
        return d if d.is_finite() and d > 0 else None
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        d = Decimal(texto)
        return d if d.is_finite() and d > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        pass
    montos = montos_del_texto(texto)
    if len(montos) == 1 and montos[0] > 0:
        return montos[0]
    return None


def _resolver_lote_movimientos(
    ctx: ContextoMensaje,
    entidades_actuales: dict,
    tarjetas_usuario: list,
    billeteras_todas: list,
    adicionales: list,
) -> None:
    """Resuelve la validación, asignación de billeteras y detección de duplicados para mensajes con lote de movimientos."""
    resultado_ia = ctx.resultado_ia
    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario
    db = ctx.db
    confianza_ia_raw = ctx.confianza_ia_raw

    total_movimientos = 1 + len(adicionales)
    if total_movimientos > MAX_MOVIMIENTOS_POR_LOTE:
        resultado_ia["intent"] = "tope_superado"
        resultado_ia["slot_filling"] = False
        resultado_ia["respuesta_usuario"] = MSG_TOPE_MOVIMIENTOS_SUPERADO
        resultado_ia["entidades"] = {}
        return

    if _debe_bloquear_mezcla_lote(mensaje_texto, usuario.id, db):
        resultado_ia["intent"] = "mezcla_transferencia_invalida"
        resultado_ia["slot_filling"] = False
        resultado_ia["respuesta_usuario"] = MSG_NO_MEZCLAR_TRANSFERENCIAS
        resultado_ia["entidades"] = {}
        return

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
        "cantidad_cuotas": entidades_actuales.get("cantidad_cuotas"),
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
                "cantidad_cuotas": ad.get("cantidad_cuotas"),
                "resuelta": False,
            }
            operaciones.append(op_ad)

    for op in operaciones:
        m_norm_ia = _parsear_monto_ia(op.get("monto"))
        op["monto"] = float(m_norm_ia) if m_norm_ia is not None else None
        if op.get("tarjeta") and tarjetas_usuario:
            t_match, _ = resolver_tarjeta_cascada(op["tarjeta"], tarjetas_usuario)
            if t_match:
                op["tarjeta_id"] = str(t_match.id)
                op["tarjeta"] = nombre_tarjeta_wpp(t_match) or t_match.nombre
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
            enc = (
                "¿A qué cuenta entraron los ingresos?"
                if tipo_r == "ingreso"
                else "¿De qué cuenta salieron los gastos?"
            )
            pregunta = _generar_menu_billeteras(bills_r, tipo=tipo_r, encabezado=enc)
            pend_ids = [op["id"] for op in unresolved]
        else:
            op_u = unresolved[0]
            if op_u.get("monto") is not None:
                m_fmt = formatear_monto(
                    float(op_u["monto"]),
                    Moneda.USD if mon_r == "USD" else Moneda.ARS,
                    con_decimales=False,
                )
                c_disp = _nombre_corto_categoria(op_u.get("categoria"))
                if tipo_r == "ingreso":
                    enc = f"¿A qué cuenta entraron los {m_fmt} de {c_disp}?"
                else:
                    enc = f"¿De qué cuenta salieron los {m_fmt} de {c_disp}?"
            else:
                enc = (
                    "¿A qué cuenta entró?"
                    if tipo_r == "ingreso"
                    else "¿De qué cuenta salió?"
                )
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
        entidades_actuales["monto"] = operaciones[0].get("monto")
        entidades_actuales["billetera"] = operaciones[0].get("billetera")
        entidades_actuales["tarjeta"] = operaciones[0].get("tarjeta")
        entidades_actuales["transacciones_adicionales"] = [dict(o) for o in operaciones[1:]]

        hay_lote_dup, m_dup, mon_dup, cat_dup = _detectar_duplicados_en_lote(entidades_actuales)
        if hay_lote_dup:
            b_nom_dup = operaciones[0].get("billetera") or "tu cuenta"
            m_dup_fmt = formatear_monto(
                float(m_dup),
                Moneda.USD if mon_dup == "USD" else Moneda.ARS,
                con_decimales=False,
            )
            pregunta_lote = (
                f"Mandaste 2 veces {m_dup_fmt} en {cat_dup}. ¿Anoto los dos?"
            )
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
                    Transaccion.suscripcion_id.is_(None),
                    Transaccion.pago_origen_id.is_(None),
                    Transaccion.pago_resumen_vencimiento.is_(None),
                ).order_by(Transaccion.fecha_creacion.desc(), Transaccion.id.desc())
            ).scalars().all()

            tx_dup_found = None
            op_dup_found = None
            for op in operaciones:
                cat_id_chk, _ = _resolver_categoria_y_subcategoria(
                    op.get("categoria"),
                    usuario.id,
                    db,
                    tipo=op.get("tipo", "egreso"),
                    detalle=op,
                )
                mon_chk = Moneda.USD if op.get("moneda") == "USD" else Moneda.ARS
                m_chk = _parsear_monto_ia(op.get("monto"))
                if m_chk is None:
                    continue
                f_op = _resolver_fecha_transaccion(op.get("fecha"))
                tipo_op_enum = TipoTransaccion.INGRESO if op.get("tipo") == "ingreso" else TipoTransaccion.EGRESO
                for th in txs_hist:
                    if (th.monto == m_chk and th.moneda == mon_chk and th.categoria_id == cat_id_chk
                            and th.fecha == f_op and th.tipo == tipo_op_enum):
                        tx_dup_found = th
                        op_dup_found = op
                        break
                if tx_dup_found:
                    break

            if tx_dup_found and op_dup_found:
                hora_dup = tx_dup_found.fecha_creacion.astimezone(TZ_ARGENTINA).strftime("%H:%M")
                cat_disp = _nombre_corto_categoria(op_dup_found.get("categoria"))
                m_fmt = formatear_monto(
                    float(op_dup_found["monto"]),
                    Moneda.USD if op_dup_found.get("moneda") == "USD" else Moneda.ARS,
                    con_decimales=False,
                )
                pregunta_dup = (
                    f"A las {hora_dup} ya anotaste {m_fmt} en {cat_disp}. "
                    "¿Lo anoto otra vez?"
                )
                resultado_ia["intent"] = "verificar_duplicado"
                resultado_ia["slot_filling"] = True
                resultado_ia["datos_faltantes"] = ["confirmar_duplicado"]
                entidades_actuales["tipo_flujo"] = "verificacion_duplicado"
                entidades_actuales["billetera_resuelta_nombre"] = (
                    op_dup_found.get("billetera") or "tu cuenta"
                )
                entidades_actuales["hora_anterior"] = hora_dup
                resultado_ia["respuesta_usuario"] = pregunta_dup
            else:
                resultado_ia["intent"] = "registrar_transaccion"
                resultado_ia["slot_filling"] = False
                if confianza_ia_baja(confianza_ia_raw):
                    entidades_actuales["confianza_baja"] = True
                resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                resultado_ia["_asumio_principal"] = bool(operaciones[0].get("se_asumio_principal", False))
                resultado_ia["respuesta_usuario"] = _construir_propuesta_transaccion(
                    entidades_actuales,
                    billetera_nombre=operaciones[0].get("billetera"),
                    se_asumio_principal=operaciones[0].get("se_asumio_principal", False),
                    billeteras_usuario=billeteras_todas,
                    db=db,
                    usuario_id=usuario.id,
                )
                if "No pude anotar ninguno." in resultado_ia["respuesta_usuario"]:
                    resultado_ia["intent"] = "desconocido"
                    resultado_ia["slot_filling"] = False


def _resolver_tarjeta_y_cuotas(
    ctx: ContextoMensaje,
    entidades_actuales: dict,
    tarjetas_usuario: list,
    m_norm: str,
    tiene_mencion_tarjeta: bool,
    tarjeta_match_mencion,
    tarjeta_mencionada_cands: list,
) -> None:
    """Resuelve la selección de tarjeta de crédito, cálculo de cuotas y construcción de la propuesta de crédito."""
    resultado_ia = ctx.resultado_ia
    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario
    db = ctx.db
    confianza_ia_raw = ctx.confianza_ia_raw

    if not tarjetas_usuario:
        resultado_ia["respuesta_usuario"] = (
            "No tenés tarjetas de crédito cargadas.\n"
            "Agregala en miargentum.com o decime de qué cuenta salió y "
            "lo anoto como gasto."
        )
        resultado_ia["intent"] = "sin_tarjetas"
        resultado_ia["slot_filling"] = False
        resultado_ia["datos_faltantes"] = []
    elif entidades_actuales.get("monto") is not None:
        if (
            re.search(rf"(?<!\w)(?:u\$s|{monedas_dolares_rx()})(?!\w)", m_norm)
            or "u$s" in m_norm
        ):
            entidades_actuales["moneda"] = "USD"
        moneda_cred = (
            Moneda.USD
            if str(entidades_actuales.get("moneda") or "").upper() == "USD"
            else Moneda.ARS
        )
        monto_actual_val = _parsear_monto_ia(entidades_actuales.get("monto"))
        if monto_actual_val is None:
            entidades_actuales["monto"] = None
            resultado_ia["respuesta_usuario"] = _construir_propuesta_transaccion(
                entidades_actuales, db=db, usuario_id=usuario.id
            )
            resultado_ia["intent"] = "desconocido"
            resultado_ia["slot_filling"] = False
            resultado_ia["datos_faltantes"] = []
            return
        entidades_actuales["monto"] = float(monto_actual_val)
        cant_cuotas, monto_cuota, monto_total, es_ambiguo, err_msg = _interpretar_cuotas(
            mensaje_texto, monto_actual_val
        )

        if err_msg:
            resultado_ia["respuesta_usuario"] = err_msg
            resultado_ia["intent"] = "error_cuotas"
            resultado_ia["slot_filling"] = False
            resultado_ia["datos_faltantes"] = []
        elif es_ambiguo:
            m_fmt = formatear_monto(
                float(monto_actual_val), moneda_cred, con_decimales=False
            )
            resultado_ia["respuesta_usuario"] = f"¿Los {m_fmt} son el total o cada cuota?"
            resultado_ia["intent"] = "slot_filling"
            resultado_ia["slot_filling"] = True
            resultado_ia["datos_faltantes"] = ["aclarar_cuotas"]
            entidades_actuales["datos_faltantes"] = ["aclarar_cuotas"]
            entidades_actuales["cantidad_cuotas"] = cant_cuotas
            entidades_actuales["monto"] = float(monto_actual_val)
        else:
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
                if tiene_mencion_tarjeta or "tarjeta" in m_norm:
                    if len(tarjetas_usuario) == 1:
                        t_match = tarjetas_usuario[0]
                        cands_res = tarjetas_usuario
                    else:
                        t_match = None
                        cands_res = tarjetas_usuario
                else:
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
                _, primer_v = calendario_tarjeta_service.resumen_de_compra(t_match, fecha_obj, proximo_resumen=False)
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
                    entidades_actuales,
                    t_match,
                    cant_cuotas,
                    monto_cuota,
                    monto_total,
                    primer_v,
                    se_asumio_tarjeta=se_asumio_tarjeta,
                    db=db,
                    usuario_id=usuario.id,
                )
                resultado_ia["intent"] = "registrar_transaccion"
                resultado_ia["slot_filling"] = False
                if confianza_ia_baja(confianza_ia_raw):
                    entidades_actuales["confianza_baja"] = True
                resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                resultado_ia["respuesta_usuario"] = propuesta_cred


def _es_mencion_persona_no_cuenta(
    billetera_raw: str,
    mensaje_texto: str,
    billeteras_todas: list,
    vino_de_otra_clave: bool,
) -> bool:
    """True si billetera_raw refiere a una persona ('a juan') y no a una cuenta."""
    b_match, cands = resolver_billetera_cascada(billetera_raw, billeteras_todas)
    if b_match or cands:
        return False
    norm_raw = normalizar_texto(billetera_raw)
    if not norm_raw:
        return True
    if norm_raw in ALIAS_BILLETERAS or entidad_de_texto(billetera_raw):
        return False
    if vino_de_otra_clave:
        return True
    m_norm = normalizar_texto(mensaje_texto)
    return bool(
        re.search(
            rf"\b(?:a|al|para)\s+(?:el\s+|la\s+)?{re.escape(norm_raw)}(?!\w)",
            m_norm,
        )
    )


def _resolver_billetera_y_duplicados(
    ctx: ContextoMensaje,
    entidades_actuales: dict,
    billeteras_todas: list,
    clave_bill: str,
    clave_otra: str,
    tipo_act: str,
) -> None:
    """Resuelve la billetera monetaria para débitos/efectivo y valida posibles transacciones duplicadas o cobros recurrentes."""
    resultado_ia = ctx.resultado_ia
    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario
    db = ctx.db
    confianza_ia_raw = ctx.confianza_ia_raw

    moneda_sol_str = entidades_actuales.get("moneda")
    moneda_sol = Moneda.USD if moneda_sol_str == "USD" else Moneda.ARS

    # Gasto en dólares desde billetera en pesos (Decisión 2, fase_f2a)
    if tipo_act == "egreso" and moneda_sol == Moneda.USD and entidades_actuales.get("monto") is not None:
        from app.routers.whatsapp.dolares_desde_pesos import (
            armar_propuesta_usd_pesos,
            billetera_pesos_para_usd,
        )
        b_pesos = billetera_pesos_para_usd(entidades_actuales, billeteras_todas, clave_bill, clave_otra)
        if b_pesos is not None:
            armar_propuesta_usd_pesos(ctx, entidades_actuales, b_pesos, clave_bill, clave_otra)
            return

    billeteras_moneda = [b for b in billeteras_todas if b.moneda == moneda_sol]
    if not billeteras_moneda:
        nom_moneda = "dólares" if moneda_sol == Moneda.USD else "pesos"
        resultado_ia["respuesta_usuario"] = (
            f"No tenés cuentas en {nom_moneda}. Creala en miargentum.com"
        )
        resultado_ia["intent"] = "sin_billetera_moneda"
        resultado_ia["slot_filling"] = False
        resultado_ia["datos_faltantes"] = []
    elif entidades_actuales.get("monto") is not None:
        bill_principal = entidades_actuales.get(clave_bill) or entidades_actuales.get(
            "billetera"
        )
        bill_otra = entidades_actuales.get(clave_otra)
        billetera_raw = bill_principal or bill_otra
        if billetera_raw and _es_mencion_persona_no_cuenta(
            str(billetera_raw),
            ctx.mensaje_texto,
            billeteras_todas,
            vino_de_otra_clave=not bool(bill_principal),
        ):
            billetera_raw = None
            entidades_actuales.pop(clave_bill, None)
            entidades_actuales.pop("billetera", None)
            entidades_actuales.pop(clave_otra, None)
        billetera_final = None
        se_asumio_principal = False
        b_texto_nombrada = _buscar_billetera_en_texto(
            ctx.mensaje_texto,
            billeteras_moneda,
            excluir_efectivo=False,
            solo_con_preposicion=bool(billetera_raw),
        )

        if billetera_raw:
            b_match, cands = resolver_billetera_cascada(billetera_raw, billeteras_moneda)
            if b_texto_nombrada and (not b_match or b_match.id != b_texto_nombrada.id):
                billetera_final = b_texto_nombrada
            elif b_match:
                billetera_final = b_match
            elif len(cands) > 1:
                resultado_ia["intent"] = "slot_filling"
                resultado_ia["slot_filling"] = True
                resultado_ia["datos_faltantes"] = [clave_bill]
                entidades_actuales["datos_faltantes"] = [clave_bill]
                entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                entidades_actuales[clave_bill] = None
                resultado_ia["respuesta_usuario"] = _generar_menu_billeteras(
                    cands, tipo=tipo_act
                )
            else:
                todas = billeteras_todas
                b_otra, _ = resolver_billetera_cascada(billetera_raw, todas)
                if b_otra and b_otra.moneda != moneda_sol:
                    nom_otra = "dólares" if b_otra.moneda == Moneda.USD else "pesos"
                    nom_mov = "pesos" if moneda_sol == Moneda.ARS else "dólares"
                    sust_mov = "ingreso" if tipo_act == "ingreso" else "gasto"
                    resultado_ia["intent"] = "slot_filling"
                    resultado_ia["slot_filling"] = True
                    resultado_ia["datos_faltantes"] = [clave_bill]
                    entidades_actuales["datos_faltantes"] = [clave_bill]
                    entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                    entidades_actuales[clave_bill] = None
                    enc_mon = (
                        f"{b_otra.nombre} es en {nom_otra} y el {sust_mov} "
                        f"es en {nom_mov}. Elegí otra:"
                    )
                    resultado_ia["respuesta_usuario"] = _generar_menu_billeteras(
                        billeteras_moneda, tipo=tipo_act, encabezado=enc_mon
                    )
                else:
                    resultado_ia["intent"] = "slot_filling"
                    resultado_ia["slot_filling"] = True
                    resultado_ia["datos_faltantes"] = [clave_bill]
                    entidades_actuales["datos_faltantes"] = [clave_bill]
                    entidades_actuales["moneda"] = "USD" if moneda_sol == Moneda.USD else "ARS"
                    entidades_actuales[clave_bill] = None
                    resultado_ia["respuesta_usuario"] = _generar_menu_billeteras(
                        billeteras_moneda,
                        tipo=tipo_act,
                        encabezado="No encontré esa cuenta. Elegí una:",
                    )
        else:
            if b_texto_nombrada:
                billetera_final = b_texto_nombrada
                se_asumio_principal = False
            elif len(billeteras_moneda) == 1:
                billetera_final = billeteras_moneda[0]
                se_asumio_principal = False
            else:
                b_ppal = next((b for b in billeteras_moneda if b.es_principal), None)
                if b_ppal:
                    billetera_final = b_ppal
                    se_asumio_principal = True
                else:
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

            hay_lote_dup, m_dup, mon_dup, cat_dup = _detectar_duplicados_en_lote(entidades_actuales)
            if hay_lote_dup:
                m_dup_fmt = formatear_monto(
                    float(m_dup),
                    Moneda.USD if mon_dup == "USD" else Moneda.ARS,
                    con_decimales=False,
                )
                pregunta_lote = (
                    f"Mandaste 2 veces {m_dup_fmt} en {cat_dup}. ¿Anoto los dos?"
                )
                resultado_ia["intent"] = "verificar_lote_duplicado"
                resultado_ia["slot_filling"] = True
                resultado_ia["datos_faltantes"] = ["confirmar_lote"]
                entidades_actuales["tipo_flujo"] = "verificacion_lote_duplicado"
                entidades_actuales["billetera_resuelta_nombre"] = billetera_final.nombre
                resultado_ia["respuesta_usuario"] = pregunta_lote
            else:
                monto_act_val = _parsear_monto_ia(entidades_actuales.get("monto"))
                entidades_actuales["monto"] = (
                    float(monto_act_val) if monto_act_val is not None else None
                )
                sub_cobrada, tx_cobrada = (None, None)
                if tipo_act == "egreso" and monto_act_val is not None:
                    sub_cobrada, tx_cobrada = _buscar_suscripcion_cobrada_periodo_actual(
                        usuario_id=usuario.id,
                        monto=monto_act_val,
                        nombre_servicio_o_concepto=(
                            entidades_actuales.get("servicio")
                            or entidades_actuales.get("descripcion")
                            or entidades_actuales.get("concepto")
                            or mensaje_texto
                        ),
                        db=db,
                    )
                if sub_cobrada and tx_cobrada:
                    m_fmt = formatear_monto(
                        float(tx_cobrada.monto),
                        tx_cobrada.moneda,
                        con_decimales=False,
                    )
                    aviso_dup_sub = (
                        f"{sub_cobrada.nombre} ({m_fmt}) ya se cobró solo "
                        "este mes. ¿Lo anoto igual?"
                    )
                    resultado_ia["intent"] = "verificar_duplicado_suscripcion"
                    resultado_ia["slot_filling"] = True
                    resultado_ia["datos_faltantes"] = ["confirmar_gasto_aparte"]
                    entidades_actuales["tipo_flujo"] = "verificacion_duplicado_suscripcion"
                    entidades_actuales["suscripcion_id"] = str(sub_cobrada.id)
                    entidades_actuales["servicio_nombre"] = sub_cobrada.nombre
                    entidades_actuales["billetera_resuelta_nombre"] = billetera_final.nombre
                    resultado_ia["respuesta_usuario"] = aviso_dup_sub
                else:
                    cat_id_chk, _ = _resolver_categoria_y_subcategoria(
                        entidades_actuales.get("categoria"),
                        usuario.id,
                        db,
                        tipo=tipo_act,
                        detalle=entidades_actuales,
                    )
                    for ad in entidades_actuales.get("transacciones_adicionales") or []:
                        if isinstance(ad, dict):
                            _resolver_categoria_y_subcategoria(
                                ad.get("categoria"),
                                usuario.id,
                                db,
                                tipo=ad.get("tipo", tipo_act),
                                detalle=ad,
                            )
                    tx_dup = None
                    if monto_act_val is not None:
                        tx_dup = _buscar_transaccion_duplicada_reciente(
                            usuario_id=usuario.id,
                            monto=monto_act_val,
                            moneda=moneda_sol,
                            categoria_id=cat_id_chk,
                            db=db,
                            fecha=_resolver_fecha_transaccion(
                                entidades_actuales.get("fecha")
                            ),
                            tipo=tipo_act,
                        )
                    if tx_dup:
                        hora_dup = tx_dup.fecha_creacion.astimezone(TZ_ARGENTINA).strftime("%H:%M")
                        cat_disp = _nombre_corto_categoria(entidades_actuales.get("categoria"))
                        m_fmt = formatear_monto(
                            float(entidades_actuales["monto"]),
                            moneda_sol,
                            con_decimales=False,
                        )
                        pregunta_dup = (
                            f"A las {hora_dup} ya anotaste {m_fmt} en {cat_disp}. "
                            "¿Lo anoto otra vez?"
                        )
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
                        if confianza_ia_baja(confianza_ia_raw):
                            entidades_actuales["confianza_baja"] = True
                        resultado_ia["confianza"] = max(float(resultado_ia.get("confianza", 0.0)), 0.85)
                        resultado_ia["_asumio_principal"] = se_asumio_principal
                        resultado_ia["respuesta_usuario"] = _construir_propuesta_transaccion(
                            entidades_actuales,
                            billetera_final.nombre,
                            se_asumio_principal=se_asumio_principal,
                            billetera_moneda=billetera_final.moneda,
                            db=db,
                            usuario_id=usuario.id,
                        )
                        if "No pude anotar ninguno." in resultado_ia["respuesta_usuario"]:
                            resultado_ia["intent"] = "desconocido"
                            resultado_ia["slot_filling"] = False


def procesar_resolucion_billetera_y_propuestas(ctx: ContextoMensaje) -> None:
    """
    Orquestador de la resolución determinística de billeteras, cuotas, lotes y propuestas.
    Evalúa si corresponde resolver transacciones y deriva a los handlers especializados.
    """
    resultado_ia = ctx.resultado_ia
    if resultado_ia and resultado_ia.get("intent") == "transferir_fondos":
        from app.routers.whatsapp.transferencias_flujo import manejar_transferencia_ia
        manejar_transferencia_ia(
            resultado_ia.get("entidades", {}),
            ctx.mensaje_texto,
            ctx.usuario,
            ctx.db,
            ctx.from_number,
            ctx.wamid,
        )
        ctx.terminado = True
        return
    entidades_actuales = resultado_ia.get("entidades", {})
    tipo_act = entidades_actuales.get("tipo") or "egreso"
    clave_bill = "billetera_destino" if tipo_act == "ingreso" else "billetera_origen"
    clave_otra = "billetera_origen" if tipo_act == "ingreso" else "billetera_destino"

    if resultado_ia.get("intent") in ("registrar_transaccion", "slot_filling") or entidades_actuales.get("monto") is not None:
        tarjetas_usuario = _obtener_tarjetas_activas(ctx.usuario.id, ctx.db)
        m_norm = normalizar_texto(ctx.mensaje_texto)

        adicionales = entidades_actuales.get("transacciones_adicionales")
        es_lote = bool(adicionales and isinstance(adicionales, list) and len(adicionales) > 0)
        ctx.es_lote = es_lote

        if es_lote:
            billeteras_todas = _obtener_billeteras_activas(ctx.usuario.id, ctx.db)
            _resolver_lote_movimientos(ctx, entidades_actuales, tarjetas_usuario, billeteras_todas, adicionales)
        else:
            es_debito_explicito = any(d in m_norm for d in PALABRAS_FUERZAN_DEBITO)
            tiene_cuotas = bool(re.search(r"\b\d+\s*(?:cuotas?|pagos)\b", m_norm))
            tiene_palabras_credito = any(w in m_norm for w in PALABRAS_FUERZAN_CREDITO)
            tiene_mencion_tarjeta = any(g in m_norm for g in FORMAS_GENERICAS_TARJETA)

            if not es_debito_explicito and tarjetas_usuario:
                tarjeta_match_mencion, tarjeta_mencionada_cands = _resolver_mencion_tarjeta_en_texto(
                    ctx.mensaje_texto, tarjetas_usuario
                )
            else:
                tarjeta_match_mencion, tarjeta_mencionada_cands = None, []

            billeteras_todas = _obtener_billeteras_activas(ctx.usuario.id, ctx.db)
            bill_raw_cands = []
            for b in billeteras_todas:
                b_nom_norm = normalizar_texto(b.nombre)
                if b_nom_norm in m_norm:
                    bill_raw_cands.append(b)

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
                nom_t = nombre_tarjeta_wpp(t_col) or t_col.nombre
                resultado_ia["respuesta_usuario"] = (
                    "¿Fue con la cuenta o con la tarjeta?\n"
                    f"1. Cuenta {b_col.nombre}\n"
                    f"2. Tarjeta {nom_t}"
                )
                ctx.es_credito = False
            else:
                tiene_ult4_o_nombre_t = any(
                    (
                        normalizar_texto(t.nombre) in m_norm
                        and not any(
                            normalizar_texto(b.nombre) == normalizar_texto(t.nombre)
                            for b in billeteras_todas
                        )
                    )
                    or (
                        getattr(t, "apodo", None)
                        and normalizar_texto(t.apodo) in m_norm
                    )
                    or any(
                        len(d) >= 4 and d[-4:] in m_norm
                        for d in re.findall(r"\d+", t.nombre)
                    )
                    for t in tarjetas_usuario
                )
                tiene_modismo_la_del_banco = any(
                    re.search(
                        rf"\b(?:con\s+)?la\s+del?\s+{re.escape(bv)}(?!\w)",
                        m_norm,
                    )
                    for t in tarjetas_usuario
                    if t.billetera
                    for bv in (
                        [normalizar_texto(t.billetera.nombre)]
                        + ALIAS_BANCOS_ARGENTINOS.get(
                            normalizar_texto(t.billetera.nombre), []
                        )
                    )
                    if bv
                )
                es_cred_texto = (
                    tiene_cuotas
                    or tiene_palabras_credito
                    or tiene_mencion_tarjeta
                    or bool(re.search(r"\btarjeta\b", m_norm))
                    or tiene_ult4_o_nombre_t
                    or tiene_modismo_la_del_banco
                )
                if (
                    not es_debito_explicito
                    and not es_cred_texto
                    and _buscar_billetera_en_texto(
                        ctx.mensaje_texto, billeteras_todas
                    )
                    is not None
                ):
                    tarjeta_match_mencion = None
                    tarjeta_mencionada_cands = []
                    entidades_actuales.pop("tarjeta", None)
                    entidades_actuales.pop("tarjeta_id", None)
                    entidades_actuales.pop("tarjeta_nombre", None)

                es_credito = (not es_debito_explicito) and (
                    tiene_cuotas
                    or tiene_palabras_credito
                    or tiene_mencion_tarjeta
                    or len(tarjeta_mencionada_cands) > 0
                    or entidades_actuales.get("tarjeta_id") is not None
                    or entidades_actuales.get("tarjeta") is not None
                )
                ctx.es_credito = es_credito

                if es_credito:
                    _resolver_tarjeta_y_cuotas(
                        ctx,
                        entidades_actuales,
                        tarjetas_usuario,
                        m_norm,
                        tiene_mencion_tarjeta,
                        tarjeta_match_mencion,
                        tarjeta_mencionada_cands,
                    )
                else:
                    _resolver_billetera_y_duplicados(
                        ctx,
                        entidades_actuales,
                        billeteras_todas,
                        clave_bill,
                        clave_otra,
                        tipo_act,
                    )
