"""
Etapa de resolución de billeteras, tarjetas, cuotas, lotes y propuestas para WhatsApp:
1. Resolución y validación de operaciones en lote (tope máximo, mezcla, duplicados).
2. Detección y resolución de pagos con tarjeta de crédito y cálculo de cuotas.
3. Detección y resolución de pagos en débito/efectivo, detección de duplicados en la última hora y suscripciones ya cobradas.
4. Construcción de propuestas de transacciones y créditos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import structlog
from sqlalchemy import select

from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    TipoTransaccion,
    Transaccion,
)
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
    FORMAS_GENERICAS_TARJETA,
    _detectar_duplicados_en_lote,
    _generar_menu_billeteras,
    _generar_menu_tarjetas,
    resolver_billetera_cascada,
    resolver_tarjeta_cascada,
)
from app.services.tarjeta_service import calcular_primer_vencimiento
from app.utils.fecha import TZ_ARGENTINA
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto

logger = structlog.get_logger(__name__)

MAX_MOVIMIENTOS_POR_LOTE = 10
MSG_TOPE_MOVIMIENTOS_SUPERADO = (
    "El límite es de 10 movimientos por mensaje. "
    "Por favor mandalos en tandas más chicas o usá la importación desde la web de Argentum."
)
MSG_NO_MEZCLAR_TRANSFERENCIAS = (
    "Las transferencias, extracciones de cajero y compra de dólares deben registrarse "
    "en mensajes separados de los gastos o ingresos. Por favor mandalas por separado."
)

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
    confianza_ia_raw = ctx.confianza_ia_raw

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
            if len(billeteras_moneda) == 1:
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
                    cat_id_chk, _ = _resolver_categoria_y_subcategoria(
                        entidades_actuales.get("categoria"), usuario.id, db, tipo=tipo_act
                    )
                    tx_dup = _buscar_transaccion_duplicada_reciente(
                        usuario_id=usuario.id,
                        monto=Decimal(str(entidades_actuales["monto"])),
                        moneda=moneda_sol,
                        categoria_id=cat_id_chk,
                        db=db,
                        fecha=_resolver_fecha_transaccion(entidades_actuales.get("fecha")),
                        tipo=tipo_act,
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


def procesar_resolucion_billetera_y_propuestas(ctx: ContextoMensaje) -> None:
    """
    Orquestador de la resolución determinística de billeteras, cuotas, lotes y propuestas.
    Evalúa si corresponde resolver transacciones y deriva a los handlers especializados.
    """
    resultado_ia = ctx.resultado_ia
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
            tiene_cuotas = any(c in m_norm for c in ["cuota", "cuotas", "en cuotas", "pagos"])
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
                resultado_ia["respuesta_usuario"] = (
                    f"¿Te referís a la billetera o a la tarjeta de crédito?\n"
                    f"1. Billetera {b_col.nombre}\n"
                    f"2. Tarjeta de crédito {t_col.nombre}"
                )
                ctx.es_credito = False
            else:
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
