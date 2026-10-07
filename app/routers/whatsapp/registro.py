"""
app/routers/whatsapp/registro.py — Registro directo y confirmación de propuestas de transacciones y cuotas para WhatsApp.
"""
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.catalogo_suscripciones import (
    buscar_servicio_por_texto,
    identificar_servicio_en_texto,
    resolver_categoria_sugerida,
)
from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.mensaje_whatsapp_procesado import MensajeWhatsappProcesado
from app.models.movimiento_meta import MovimientoMeta
from app.models.subcategoria import Subcategoria
from app.models.suscripcion import Suscripcion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.constantes import MESES_ES_GEN, logger
from app.routers.whatsapp.db_lookups import (
    PLAZO_EXPIRACION_ESTADO_MINUTOS,
    _buscar_suscripcion_cobrada_periodo_actual,
    _buscar_transaccion_duplicada_reciente,
    _buscar_usuario_por_telefono,
    _obtener_billeteras_activas,
    _obtener_tarjetas_activas,
    _resolver_billetera,
    _resolver_categoria_y_subcategoria,
)
from app.routers.whatsapp.detectors import (
    _detectar_ambiguedad_suscripcion,
    _es_senial_gasto_suelto,
)
from app.routers.whatsapp.parsers import (
    _fmt,
    _formatear_fecha_natural,
    _nombre_corto_categoria,
    _parsear_monto_argentino,
    _resolver_fecha_transaccion,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.propuestas import _unir_items_multilinea
from app.routers.whatsapp.resolvers_cascada import (
    _detectar_duplicados_en_lote,
    _entidades_completas,
    _validar_item_movimiento,
    resolver_billetera_cascada,
    resolver_tarjeta_cascada,
)
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.services import (
    ai_service,
    transaccion_service,
    whatsapp_service,
)
from app.services.evento_service import emitir_evento_actualizacion
from app.services.tarjeta_service import calcular_primer_vencimiento
from app.services.transaccion_service import (
    actualizar_transaccion,
    deducir_metodo_pago,
    eliminar_transaccion,
)
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto



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

    cat_id, subcat_id = _resolver_categoria_y_subcategoria(
        datos.get("categoria"), usuario_id, db, tipo=tipo_item
    )
    fecha_obj, _ = _resolver_y_validar_fecha(datos.get("fecha"))
    desc_final = ai_service.sanitizar_descripcion(
        datos.get("descripcion"),
        mensaje_original=mensaje_original,
        tipo=tipo_item,
    )

    try:
        data_tx = TransaccionCreate(
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
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        tx = transaccion_service.crear_transaccion(
            db=db,
            usuario_id=usuario_id,
            data=data_tx,
            commit=False,
        )
        return tx, None
    except HTTPException as e:
        return None, f"No se pudo registrar {desc}: {str(e.detail).rstrip('.')}."


def _formatear_confirmacion_lote_unificada(
    txs_todas: list[Transaccion],
    usuario_id: UUID,
    db: Session,
    items_info: list[dict] | None = None,
) -> str:
    """
    Decisión F: Formato unificado de confirmación de lote (igual al registro directo):
    signo -/+, categoría o subcategoría y fecha natural.
    """
    total_registrados = len(txs_todas)
    mov_palabra = "movimientos" if total_registrados != 1 else "movimiento"
    reg_palabra = "Registrados." if total_registrados != 1 else "Registrado."

    billeteras_todas = _obtener_billeteras_activas(usuario_id, db)
    tarjetas_todas = _obtener_tarjetas_activas(usuario_id, db)
    b_map = {b.id: b for b in billeteras_todas}
    t_map = {t.id: t for t in tarjetas_todas}

    b_ids = [t.billetera_id for t in txs_todas if t.metodo_pago != MetodoPago.CREDITO]
    todas_misma_billetera = len(set(b_ids)) <= 1 and not any(t.metodo_pago == MetodoPago.CREDITO for t in txs_todas)
    mismo_tipo = len(set(t.tipo for t in txs_todas)) == 1
    tipos_mezclados = any(t.tipo == TipoTransaccion.INGRESO for t in txs_todas) and any(t.tipo == TipoTransaccion.EGRESO for t in txs_todas)

    def _cat_disp_item(item_d: dict | None, tx_item: Transaccion) -> str:
        if item_d and item_d.get("categoria"):
            return _nombre_corto_categoria(item_d["categoria"])
        if tx_item.subcategoria_id:
            s_obj = db.get(Subcategoria, tx_item.subcategoria_id)
            if s_obj:
                return s_obj.nombre
        if tx_item.categoria_id:
            c_obj = db.get(Categoria, tx_item.categoria_id)
            if c_obj:
                return c_obj.nombre
        return _nombre_corto_categoria(tx_item.descripcion) or "Otros"

    items_dict = items_info if items_info and len(items_info) == len(txs_todas) else [None] * len(txs_todas)

    if todas_misma_billetera and b_ids:
        b_comun = b_map.get(b_ids[0])
        nom_b = b_comun.nombre if b_comun else "tu billetera"
        if mismo_tipo:
            tipo_comun = txs_todas[0].tipo
            origen_str = f" a {nom_b}" if tipo_comun == TipoTransaccion.INGRESO else f" desde {nom_b}"
            items_str = []
            for t, it_d in zip(txs_todas, items_dict):
                m_fmt = formatear_monto(float(t.monto), t.moneda)
                cat_d = _cat_disp_item(it_d, t)
                f_nat = _formatear_fecha_natural(t.fecha)
                f_disp = f" ({f_nat})" if f_nat else ""
                items_str.append(f"{m_fmt} en {cat_d}{f_disp}")
            encabezado = f"Listo, {total_registrados} {mov_palabra}{origen_str}:"
            return _unir_items_multilinea(items_str, encabezado, reg_palabra)
        else:
            items_str = []
            for t, it_d in zip(txs_todas, items_dict):
                m_fmt = formatear_monto(float(t.monto), t.moneda)
                cat_d = _cat_disp_item(it_d, t)
                f_nat = _formatear_fecha_natural(t.fecha)
                f_disp = f" ({f_nat})" if f_nat else ""
                signo = "+" if t.tipo == TipoTransaccion.INGRESO else "-"
                items_str.append(f"*{signo}{m_fmt}* en {cat_d}{f_disp}")
            encabezado = f"Listo, {total_registrados} {mov_palabra} en *{nom_b}*:"
            return _unir_items_multilinea(items_str, encabezado, reg_palabra)
    else:
        items_str = []
        for t, it_d in zip(txs_todas, items_dict):
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
        encabezado = f"Listo, {total_registrados} {mov_palabra}:"
        return _unir_items_multilinea(items_str, encabezado, reg_palabra)


def _anotar_rendimientos_confirmados(
    db: Session,
    usuario: Usuario,
    entidades: dict,
) -> str:
    """Anota en la base de datos los rendimientos confirmados de la propuesta y devuelve el texto para el mensaje."""
    rendimientos_conf = entidades.get("rendimientos")
    if not rendimientos_conf or not isinstance(rendimientos_conf, list):
        return ""

    from datetime import date
    from fastapi import HTTPException
    import app.services.rendimiento_billetera_service as rbs_mod

    texto_res = ""
    for r_item in rendimientos_conf:
        if isinstance(r_item, dict) and r_item.get("billetera_id") and r_item.get("monto") is not None:
            try:
                m_r = Decimal(str(r_item["monto"]))
                b_id_r = UUID(str(r_item["billetera_id"]))
                f_r_raw = r_item.get("fecha")
                f_r_dt = None
                if f_r_raw:
                    if isinstance(f_r_raw, str):
                        d_r = date.fromisoformat(f_r_raw)
                    elif isinstance(f_r_raw, date):
                        d_r = f_r_raw
                    else:
                        d_r = None
                    if d_r:
                        f_r_dt = datetime(d_r.year, d_r.month, d_r.day, 12, 0, 0, tzinfo=timezone.utc)
                with db.begin_nested():
                    rbs_mod.confirmar_rendimiento(
                        db=db,
                        usuario_id=usuario.id,
                        billetera_id=b_id_r,
                        monto=m_r,
                        fecha=f_r_dt,
                        commit=False,
                    )
                b_nom_r = r_item.get("billetera_nombre")
                if not b_nom_r:
                    b_obj_r = db.get(Billetera, b_id_r)
                    b_nom_r = b_obj_r.nombre if b_obj_r else "tu billetera"
                m_fmt_r = formatear_monto(float(m_r), Moneda.ARS)
                texto_res += f"\nRendimiento de {m_fmt_r} anotado en {b_nom_r}."
            except HTTPException as e_rend:
                detail = e_rend.detail.rstrip(".")
                texto_res += f"\nNo pude anotar el rendimiento: {detail}."
            except Exception:
                texto_res += "\nNo pude anotar el rendimiento."

    return texto_res


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

    # La rama de transacción pendiente IA se eliminó por no tener emisores ni registros (Decisión 3).

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
        msg_resp = _formatear_confirmacion_lote_unificada(
            txs_registradas, usuario.id, db, items_registrados
        )

        if descartadas:
            msg_resp += "\n" + "\n".join(descartadas)

        b_map = {b.id: b for b in billeteras_todas}
        billeteras_tocadas = {t.billetera_id for t in txs_registradas if t.billetera_id and t.metodo_pago != MetodoPago.CREDITO}
        for bid in billeteras_tocadas:
            b_chk = b_map.get(bid)
            if b_chk and b_chk.saldo_actual < 0:
                msg_resp += f"\nLa billetera quedó en negativo."

        msg_resp += _anotar_rendimientos_confirmados(db, usuario, entidades)

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

    try:
        data_tx = TransaccionCreate(
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
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        transaccion = transaccion_service.crear_transaccion(
            db=db,
            usuario_id=usuario.id,
            data=data_tx,
            commit=False,
        )
    except HTTPException as e:
        return None, e.detail, False

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
        txs_lote = [transaccion] + adicionales_registradas
        items_lote = [entidades] + [a for a in adicionales if isinstance(a, dict)]
        msg_resp = _formatear_confirmacion_lote_unificada(txs_lote, usuario.id, db, items_lote)
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

    msg_resp += _anotar_rendimientos_confirmados(db, usuario, entidades)

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

    try:
        data_tx = TransaccionCreate(
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
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        tx = transaccion_service.crear_transaccion(
            db=db,
            usuario_id=usuario.id,
            data=data_tx,
            commit=False,
        )
    except HTTPException as e:
        return None, e.detail

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
        txs_lote = [tx] + adicionales_registradas
        items_lote = [entidades] + [a for a in adicionales if isinstance(a, dict)]
        msg_resp = _formatear_confirmacion_lote_unificada(txs_lote, usuario.id, db, items_lote)
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
