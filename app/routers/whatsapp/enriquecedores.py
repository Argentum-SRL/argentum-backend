"""
Módulo de enriquecimiento de respuestas post-IA para WhatsApp.
Contiene la lógica de resolución de consultas financieras y fallbacks determinísticos.
"""
from __future__ import annotations

import structlog
from sqlalchemy.orm import Session

from app.models.usuario import Usuario, Moneda
from app.models.transferencia_interna import TransferenciaInterna
from app.utils.formato import formatear_monto
from app.routers.whatsapp.detectors import _es_pedido_deshacer
from app.routers.whatsapp.parsers import _fmt, _resolver_y_validar_fecha
from app.routers.whatsapp.resolvers_cascada import resolver_billetera_cascada
from app.routers.whatsapp.db_lookups import (
    _buscar_ultimo_movimiento_whatsapp,
    _obtener_billeteras_activas,
    _resolver_categoria_y_subcategoria,
)

logger = structlog.get_logger("whatsapp")


def enriquecer_respuesta_por_intent(
    intent_detectado: str | None,
    resultado_ia: dict,
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
) -> str | None:
    """
    Enriquece la respuesta de usuario en resultado_ia según el intent_detectado
    para consultas financieras (proyección, saldo, balance, cotización) y fallbacks
    de deshacer y corregir vía IA.
    Muta resultado_ia in-place y devuelve intent_detectado (que puede ser reasignado).
    """
    from app.routers.whatsapp.deshacer_corregir import (
        _construir_propuesta_corregir,
        _construir_propuesta_deshacer,
        _detectar_correccion_ultimo_movimiento,
    )

    if intent_detectado == "consultar_proyeccion":
        try:
            from app.services.fin_de_mes_service import calcular_fin_de_mes
            from app.services.fin_de_mes_textos import mensaje_wpp
            r = calcular_fin_de_mes(db, usuario)
            respuesta_usuario = mensaje_wpp(r)
            resultado_ia["respuesta_usuario"] = respuesta_usuario
        except Exception:
            logger.exception("Error al calcular proyección para WhatsApp")
            resultado_ia["respuesta_usuario"] = (
                "No pude ver cómo llegás a fin de mes ahora. "
                "Probá de nuevo en unos minutos."
            )

    elif intent_detectado == "consultar_saldo":
        try:
            from app.services.contexto_financiero_service import _calcular_saldo_disponible_sync
            disp_ctx = _calcular_saldo_disponible_sync(db, usuario.id)
            ars_total = float(disp_ctx["ars"]["total_billeteras"])
            ars_disp = float(disp_ctx["ars"]["saldo_disponible"])
            usd_total = float(disp_ctx["usd"]["total_billeteras"])
            usd_disp = float(disp_ctx["usd"]["saldo_disponible"])
            fecha_fin = disp_ctx.get("fecha_fin_ciclo")
            fecha_fin_str = fecha_fin.strftime("%d/%m") if fecha_fin else ""

            tot_fmt = formatear_monto(ars_total, Moneda.ARS, con_decimales=False)
            lib_fmt = formatear_monto(ars_disp, Moneda.ARS, con_decimales=False)
            msg = (
                f"Tenés {tot_fmt} en tus cuentas.\n"
                f"Libres: {lib_fmt}, ya descontado lo que vence hasta el {fecha_fin_str}."
            )
            if usd_total > 0 or usd_disp > 0:
                ut_fmt = formatear_monto(usd_total, Moneda.USD, con_decimales=False)
                ul_fmt = formatear_monto(usd_disp, Moneda.USD, con_decimales=False)
                msg += f"\nEn dólares: {ut_fmt} (libres {ul_fmt})."

            try:
                from app.services.frescura_service import calcular_frescura
                from app.routers.whatsapp.aviso_frescura_wpp import linea_frescura_wpp
                linea = linea_frescura_wpp(calcular_frescura(db, usuario.id))
                if linea is not None:
                    msg += "\n" + linea
            except Exception:
                logger.exception("Error al calcular frescura para WhatsApp")

            resultado_ia["respuesta_usuario"] = msg
        except Exception:
            logger.exception("Error al calcular saldo para WhatsApp")
            resultado_ia["respuesta_usuario"] = (
                "No pude ver tu saldo ahora. Probá de nuevo en unos minutos."
            )

    elif intent_detectado == "consultar_balance":
        try:
            from app.services.dashboard_service import (
                calcular_balance_ciclo,
                get_ciclo_fechas,
            )
            from app.utils.fecha import hoy_argentina

            bal_ciclo = calcular_balance_ciclo(db, usuario)
            ini_mes, _ = get_ciclo_fechas(usuario, hoy_argentina())
            dd_mm = ini_mes.strftime("%d/%m")
            b_ars = bal_ciclo["ars"]
            ing_ars = formatear_monto(
                b_ars.get("ingresos", 0.0), Moneda.ARS, con_decimales=False
            )
            egr_ars = formatear_monto(
                b_ars.get("egresos", 0.0), Moneda.ARS, con_decimales=False
            )
            msg = f"Este mes (desde el {dd_mm}) entraron {ing_ars} y gastaste {egr_ars}."

            b_usd = bal_ciclo["usd"]
            ing_u_val = b_usd.get("ingresos", 0.0)
            egr_u_val = b_usd.get("egresos", 0.0)
            if ing_u_val > 0 or egr_u_val > 0:
                ing_usd = formatear_monto(ing_u_val, Moneda.USD, con_decimales=False)
                egr_usd = formatear_monto(egr_u_val, Moneda.USD, con_decimales=False)
                msg += f"\nEn dólares: entraron {ing_usd} y gastaste {egr_usd}."

            resultado_ia["respuesta_usuario"] = msg
        except Exception:
            logger.exception("Error al calcular balance para WhatsApp")
            resultado_ia["respuesta_usuario"] = (
                "No pude ver tus ingresos y gastos ahora. Probá de nuevo en unos minutos."
            )

    elif intent_detectado == "consultar_cotizacion":
        try:
            from app.services.dolar_service import get_cotizaciones_dolar
            cots_data = get_cotizaciones_dolar()
            cots = cots_data.get("cotizaciones", {})
            blue = cots.get("blue", {})
            oficial = cots.get("oficial", {})
            mep = cots.get("mep", {})

            msg_parts = []
            if blue and blue.get("venta"):
                v_b = formatear_monto(blue["venta"], Moneda.ARS, con_decimales=False)
                msg_parts.append(f"Blue: {v_b}")
            if mep and mep.get("venta"):
                v_m = formatear_monto(mep["venta"], Moneda.ARS, con_decimales=False)
                msg_parts.append(f"MEP: {v_m}")
            if oficial and oficial.get("venta"):
                v_o = formatear_monto(oficial["venta"], Moneda.ARS, con_decimales=False)
                msg_parts.append(f"Oficial: {v_o}")

            if msg_parts:
                items_cots = "\n".join(f"- {p}" for p in msg_parts)
                resultado_ia["respuesta_usuario"] = f"Dólar hoy:\n{items_cots}"
            else:
                resultado_ia["respuesta_usuario"] = (
                    "No pude ver el dólar ahora. Probá de nuevo en unos minutos."
                )
        except Exception:
            logger.exception("Error al consultar cotizaciones para WhatsApp")
            resultado_ia["respuesta_usuario"] = (
                "No pude ver el dólar ahora. Probá de nuevo en unos minutos."
            )

    elif intent_detectado == "consultar_meta":
        try:
            from app.services.contexto_financiero_service import _resumen_metas_activas_sync
            metas_res = _resumen_metas_activas_sync(db, usuario.id)
            if not metas_res:
                resultado_ia["respuesta_usuario"] = "No tenés metas activas."
            else:
                partes_meta = []
                for meta_i in metas_res[:8]:
                    mon_meta = Moneda.USD if meta_i["moneda"] == "USD" else Moneda.ARS
                    pct = f" ({round(meta_i['acumulado'] / meta_i['objetivo'] * 100)}%)" if meta_i["objetivo"] > 0 else ""
                    ac_fmt = formatear_monto(
                        meta_i["acumulado"], mon_meta, con_decimales=False
                    )
                    ob_fmt = formatear_monto(
                        meta_i["objetivo"], mon_meta, con_decimales=False
                    )
                    partes_meta.append(
                        f"- {meta_i['nombre']}: {ac_fmt} de {ob_fmt}{pct}"
                    )
                if len(metas_res) > 8:
                    partes_meta.append(f"- y {len(metas_res) - 8} más")
                resultado_ia["respuesta_usuario"] = (
                    "Tus metas:\n" + "\n".join(partes_meta)
                )
        except Exception:
            logger.exception("Error al consultar metas para WhatsApp")
            resultado_ia["respuesta_usuario"] = (
                "No pude ver tus metas ahora. Probá de nuevo en unos minutos."
            )

    elif intent_detectado == "consultar_presupuesto":
        try:
            from app.services.contexto_financiero_service import _resumen_presupuestos_activos_sync
            pres_res = _resumen_presupuestos_activos_sync(db, usuario.id)
            if not pres_res:
                resultado_ia["respuesta_usuario"] = "No tenés presupuestos activos."
            else:
                partes_pres = []
                for pres_i in pres_res[:8]:
                    mon_pres = Moneda.USD if pres_i["moneda"] == "USD" else Moneda.ARS
                    if pres_i["usado"] > pres_i["limite"]:
                        dif_fmt = formatear_monto(
                            pres_i["usado"] - pres_i["limite"],
                            mon_pres,
                            con_decimales=False,
                        )
                        detalle_pres = f"te pasaste por {dif_fmt}"
                    else:
                        dif_fmt = formatear_monto(
                            pres_i["limite"] - pres_i["usado"],
                            mon_pres,
                            con_decimales=False,
                        )
                        detalle_pres = f"te quedan {dif_fmt}"
                    us_fmt = formatear_monto(
                        pres_i["usado"], mon_pres, con_decimales=False
                    )
                    lim_fmt = formatear_monto(
                        pres_i["limite"], mon_pres, con_decimales=False
                    )
                    partes_pres.append(
                        f"- {pres_i['nombre']}: usaste {us_fmt} de "
                        f"{lim_fmt} ({detalle_pres})"
                    )
                if len(pres_res) > 8:
                    partes_pres.append(f"- y {len(pres_res) - 8} más")
                resultado_ia["respuesta_usuario"] = (
                    "Tus presupuestos:\n" + "\n".join(partes_pres)
                )
        except Exception:
            logger.exception("Error al consultar presupuestos para WhatsApp")
            resultado_ia["respuesta_usuario"] = (
                "No pude ver tus presupuestos ahora. "
                "Probá de nuevo en unos minutos."
            )

    elif intent_detectado == "deshacer":
        if not _es_pedido_deshacer(mensaje_texto) and resultado_ia.get("entidades", {}).get("monto"):
            resultado_ia["intent"] = "registrar_transaccion"
            intent_detectado = "registrar_transaccion"
        else:
            tx_last, motivo_err = _buscar_ultimo_movimiento_whatsapp(usuario.id, db)
            if not tx_last:
                if motivo_err in ("YA_DESHECHO", "YA_BORRADO"):
                    msg_undo_resp = "No hay nada para borrar."
                elif motivo_err == "PLAZO_VENCIDO":
                    msg_undo_resp = "Pasaron más de 30 minutos. Borralo en miargentum.com"
                elif motivo_err == "ES_CUOTA":
                    msg_undo_resp = (
                        "Las cuotas de tarjeta no se borran por acá. "
                        "Borrala en miargentum.com"
                    )
                elif motivo_err == "ES_RESUMEN":
                    msg_undo_resp = (
                        "Los pagos de resumen no se borran por acá. "
                        "Hacelo en miargentum.com"
                    )
                elif motivo_err == "ES_META":
                    msg_undo_resp = (
                        "Los aportes a metas no se borran por acá. "
                        "Hacelo en miargentum.com"
                    )
                elif motivo_err == "ES_SUSCRIPCION":
                    msg_undo_resp = "Los cobros de suscripciones se cambian en miargentum.com"
                else:
                    msg_undo_resp = (
                        "No anotaste nada por acá en los últimos 30 minutos. "
                        "Borralo en miargentum.com"
                    )
                resultado_ia["respuesta_usuario"] = msg_undo_resp
                resultado_ia["entidades"] = {}
            else:
                resultado_ia["respuesta_usuario"] = _construir_propuesta_deshacer(tx_last, db)
                if isinstance(tx_last, list):
                    resultado_ia["entidades"] = {"lote_ids": [str(t.id) for t in tx_last]}
                elif isinstance(tx_last, TransferenciaInterna):
                    resultado_ia["entidades"] = {"transferencia_id": str(tx_last.id)}
                else:
                    resultado_ia["entidades"] = {"transaccion_id": str(tx_last.id)}

    elif intent_detectado == "corregir":
        tx_last_corr, motivo_corr = _buscar_ultimo_movimiento_whatsapp(usuario.id, db)
        if not tx_last_corr:
            if motivo_corr == "PLAZO_VENCIDO":
                msg_resp = "Pasaron más de 30 minutos. Cambialo en miargentum.com"
            elif motivo_corr == "ES_CUOTA":
                msg_resp = "Las cuotas de tarjeta no se cambian por acá. Cambiala en miargentum.com"
            else:
                msg_resp = (
                    "No anotaste nada por acá en los últimos 30 minutos. "
                    "Cambialo en miargentum.com"
                )
            resultado_ia["respuesta_usuario"] = msg_resp
            resultado_ia["entidades"] = {}
        else:
            es_c, cambios, err_c = _detectar_correccion_ultimo_movimiento(
                mensaje_texto, usuario.id, db, tx_last_corr
            )
            if err_c:
                resultado_ia["respuesta_usuario"] = err_c
                resultado_ia["entidades"] = {}
            elif cambios:
                resultado_ia["respuesta_usuario"] = _construir_propuesta_corregir(tx_last_corr, cambios, db)
                resultado_ia["entidades"] = {"transaccion_id": str(tx_last_corr.id), "cambios": cambios}
            else:
                ent_ia = resultado_ia.get("entidades") or {}
                cambios_ia = {}
                if ent_ia.get("monto") is not None:
                    cambios_ia["monto"] = float(ent_ia["monto"])
                if ent_ia.get("categoria"):
                    c_id, s_id = _resolver_categoria_y_subcategoria(ent_ia["categoria"], usuario.id, db, tipo=tx_last_corr.tipo.value)
                    if c_id:
                        cambios_ia["categoria_id"] = str(c_id)
                        cambios_ia["subcategoria_id"] = str(s_id) if s_id else None
                b_raw = ent_ia.get("billetera_origen") or ent_ia.get("billetera_destino") or ent_ia.get("billetera")
                if b_raw:
                    b_m, _ = resolver_billetera_cascada(b_raw, _obtener_billeteras_activas(usuario.id, db))
                    if b_m and b_m.moneda == tx_last_corr.moneda:
                        cambios_ia["billetera_id"] = str(b_m.id)
                        cambios_ia["billetera_nombre"] = b_m.nombre
                if ent_ia.get("fecha"):
                    f_obj, _ = _resolver_y_validar_fecha(ent_ia["fecha"])
                    cambios_ia["fecha"] = f_obj.isoformat()

                if cambios_ia:
                    resultado_ia["respuesta_usuario"] = _construir_propuesta_corregir(tx_last_corr, cambios_ia, db)
                    resultado_ia["entidades"] = {"transaccion_id": str(tx_last_corr.id), "cambios": cambios_ia}
                else:
                    resultado_ia["respuesta_usuario"] = (
                        "No entendí qué querés cambiar. "
                        "Por ejemplo: _eran 3000_ o _fue con Santander_."
                    )

    return intent_detectado
