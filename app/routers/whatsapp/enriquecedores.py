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
    from app.routers.whatsapp_ia import (
        _construir_propuesta_corregir,
        _construir_propuesta_deshacer,
        _detectar_correccion_ultimo_movimiento,
    )

    if intent_detectado == "consultar_proyeccion":
        try:
            from app.services.proyeccion_service import calcular_proyeccion
            proyeccion = calcular_proyeccion(db, usuario)

            # Pesos
            p_ars = proyeccion["ars"]
            calib_ars = p_ars.get("calibracion") or {}
            pasa_ars = calib_ars.get("pasa_puerta") is True
            balance_ars = p_ars.get("balance_proyectado")
            dias_rest = p_ars.get("periodo", {}).get("dias_restantes", 0)
            confianza_ars = p_ars.get("nivel_confianza", "bajo")
            advertencias_ars = p_ars.get("advertencias", [])
            certezas_ars = p_ars.get("certezas") or {}
            total_certezas_ars = certezas_ars.get("total", 0.0)

            # Dolares
            p_usd = proyeccion["usd"]
            calib_usd = p_usd.get("calibracion") or {}
            pasa_usd = calib_usd.get("pasa_puerta") is True
            balance_usd = p_usd.get("balance_proyectado")
            confianza_usd = p_usd.get("nivel_confianza", "bajo")
            advertencias_usd = p_usd.get("advertencias", [])
            certezas_usd = p_usd.get("certezas") or {}
            total_certezas_usd = certezas_usd.get("total", 0.0)

            if not pasa_ars or balance_ars is None:
                msg_cierre = p_ars.get("mensaje") or (advertencias_ars[0] if advertencias_ars else "Mostramos tus compromisos ciertos.")
                if total_certezas_ars > 0:
                    msg = f"{msg_cierre} Tenés compromisos ciertos pendientes por {_fmt(total_certezas_ars)} ({dias_rest} días restantes)."
                else:
                    msg = f"{msg_cierre} ({dias_rest} días restantes)."
            elif confianza_ars == "bajo":
                msg = "Todavía no tenés suficiente historial para una proyección confiable en pesos."
            elif balance_ars >= 0:
                msg = f"Si seguís así en pesos, terminás el ciclo con aproximadamente {_fmt(balance_ars)} disponibles ({dias_rest} días restantes)."
            else:
                msg = f"Ojo — si seguís así en pesos, terminarías el ciclo con {_fmt(abs(balance_ars))} en rojo ({dias_rest} días restantes)."

            if pasa_ars and advertencias_ars:
                msg += f" {advertencias_ars[0]}"

            # USD
            gasto_proy_usd = p_usd.get("gasto_proyectado_total") or 0.0
            ingresos_proy_usd = p_usd.get("ingresos_proyectados") or 0.0
            tiene_usd = (gasto_proy_usd > 0 or ingresos_proy_usd > 0 or total_certezas_usd > 0)
            if tiene_usd:
                if not pasa_usd or balance_usd is None:
                    msg_cierre_usd = p_usd.get("mensaje") or (advertencias_usd[0] if advertencias_usd else "")
                    if msg_cierre_usd:
                        msg += f" En dólares: {msg_cierre_usd}"
                elif confianza_usd == "bajo":
                    msg += " Aún no tenés historial suficiente para una proyección en dólares."
                elif balance_usd >= 0:
                    msg += f" En dólares, terminarías con aproximadamente {_fmt(balance_usd, Moneda.USD)}."
                else:
                    msg += f" Ojo: en dólares terminarías con {_fmt(abs(balance_usd), Moneda.USD)} en rojo."

                if pasa_usd and advertencias_usd:
                    msg += f" {advertencias_usd[0]}"

            resultado_ia["respuesta_usuario"] = msg
        except Exception:
            logger.exception("Error al calcular proyección para WhatsApp")
            resultado_ia["respuesta_usuario"] = "No pude calcular tu proyección en este momento. Probá de nuevo en unos minutos."

    elif intent_detectado == "consultar_saldo":
        try:
            from app.services.contexto_financiero_service import _calcular_saldo_disponible_sync
            disp_ctx = _calcular_saldo_disponible_sync(db, usuario.id)
            ars_total = float(disp_ctx["ars"]["total_billeteras"])
            ars_disp = float(disp_ctx["ars"]["saldo_disponible"])
            usd_total = float(disp_ctx["usd"]["total_billeteras"])
            usd_disp = float(disp_ctx["usd"]["saldo_disponible"])

            msg = f"Tenés {_fmt(ars_total)} en tus billeteras en pesos. Disponible real (descontando cuotas): {_fmt(ars_disp)}."
            if usd_total > 0 or usd_disp > 0:
                msg += f" Y tenés {_fmt(usd_total, Moneda.USD)} en tus billeteras en dólares. Disponible real: {_fmt(usd_disp, Moneda.USD)}."
            resultado_ia["respuesta_usuario"] = msg
        except Exception:
            logger.exception("Error al calcular saldo para WhatsApp")
            resultado_ia["respuesta_usuario"] = "No pude consultar tu saldo en este momento. Probá de nuevo en unos minutos."

    elif intent_detectado == "consultar_balance":
        try:
            from app.services.dashboard_service import calcular_balance_ciclo
            bal_ciclo = calcular_balance_ciclo(db, usuario)
            b_ars = bal_ciclo["ars"]
            ing_ars = b_ars.get("ingresos", 0.0)
            egr_ars = b_ars.get("egresos", 0.0)
            bal_ars = b_ars.get("balance", 0.0)

            signo_ars = "+" if bal_ars >= 0 else ""
            msg = f"En este ciclo llevás ingresados {_fmt(ing_ars)} y gastados {_fmt(egr_ars)} en pesos (balance: {signo_ars}{_fmt(bal_ars)})."

            b_usd = bal_ciclo["usd"]
            ing_usd = b_usd.get("ingresos", 0.0)
            egr_usd = b_usd.get("egresos", 0.0)
            bal_usd = b_usd.get("balance", 0.0)
            if ing_usd > 0 or egr_usd > 0:
                signo_usd = "+" if bal_usd > 0 else ""
                msg += f" En dólares: ingresos {_fmt(ing_usd, Moneda.USD)}, gastos {_fmt(egr_usd, Moneda.USD)} (balance: {signo_usd}{_fmt(bal_usd, Moneda.USD)})."

            resultado_ia["respuesta_usuario"] = msg
        except Exception:
            logger.exception("Error al calcular balance para WhatsApp")
            resultado_ia["respuesta_usuario"] = "No pude calcular tu balance en este momento. Probá de nuevo en unos minutos."

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
                msg_parts.append(f"Dólar Blue: {formatear_monto(blue['venta'], Moneda.ARS)}")
            if mep and mep.get("venta"):
                msg_parts.append(f"MEP: {formatear_monto(mep['venta'], Moneda.ARS)}")
            if oficial and oficial.get("venta"):
                msg_parts.append(f"Oficial: {formatear_monto(oficial['venta'], Moneda.ARS)}")

            if msg_parts:
                resultado_ia["respuesta_usuario"] = "Cotizaciones del dólar: " + " | ".join(msg_parts)
            else:
                resultado_ia["respuesta_usuario"] = "No pude obtener la cotización del dólar en este momento. Probá de nuevo en unos minutos."
        except Exception:
            logger.exception("Error al consultar cotizaciones para WhatsApp")
            resultado_ia["respuesta_usuario"] = "No pude obtener la cotización del dólar en este momento. Probá de nuevo en unos minutos."

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
                    parte_meta = f"{meta_i['nombre']}: {_fmt(meta_i['acumulado'], mon_meta)} de {_fmt(meta_i['objetivo'], mon_meta)}"
                    if meta_i["objetivo"] > 0:
                        parte_meta += f" ({round(meta_i['acumulado'] / meta_i['objetivo'] * 100)}%)"
                    partes_meta.append(parte_meta)
                if len(metas_res) > 8:
                    partes_meta.append(f"y {len(metas_res) - 8} más")
                resultado_ia["respuesta_usuario"] = "Tus metas activas: " + " | ".join(partes_meta)
        except Exception:
            logger.exception("Error al consultar metas para WhatsApp")
            resultado_ia["respuesta_usuario"] = "No pude consultar tus metas en este momento. Probá de nuevo en unos minutos."

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
                        detalle_pres = f"te pasaste por {_fmt(pres_i['usado'] - pres_i['limite'], mon_pres)}"
                    else:
                        detalle_pres = f"te quedan {_fmt(pres_i['limite'] - pres_i['usado'], mon_pres)}"
                    partes_pres.append(f"{pres_i['nombre']}: usaste {_fmt(pres_i['usado'], mon_pres)} de {_fmt(pres_i['limite'], mon_pres)} ({detalle_pres})")
                if len(pres_res) > 8:
                    partes_pres.append(f"y {len(pres_res) - 8} más")
                resultado_ia["respuesta_usuario"] = "Tus presupuestos activos: " + " | ".join(partes_pres)
        except Exception:
            logger.exception("Error al consultar presupuestos para WhatsApp")
            resultado_ia["respuesta_usuario"] = "No pude consultar tus presupuestos en este momento. Probá de nuevo en unos minutos."

    elif intent_detectado == "deshacer":
        if not _es_pedido_deshacer(mensaje_texto) and resultado_ia.get("entidades", {}).get("monto"):
            resultado_ia["intent"] = "registrar_transaccion"
            intent_detectado = "registrar_transaccion"
        else:
            tx_last, motivo_err = _buscar_ultimo_movimiento_whatsapp(usuario.id, db)
            if not tx_last:
                if motivo_err in ("YA_DESHECHO", "YA_BORRADO"):
                    msg_undo_resp = "No hay nada para deshacer."
                elif motivo_err == "PLAZO_VENCIDO":
                    msg_undo_resp = "El último movimiento fue hace más de 30 minutos. Para eliminarlo, ingresá a la web de Argentum."
                elif motivo_err == "ES_CUOTA":
                    msg_undo_resp = "Ese movimiento corresponde a una cuota de tarjeta y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum."
                elif motivo_err == "ES_RESUMEN":
                    msg_undo_resp = "Ese movimiento corresponde al pago de un resumen y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum."
                elif motivo_err == "ES_META":
                    msg_undo_resp = "Ese movimiento corresponde a una meta de ahorro y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum."
                elif motivo_err == "ES_RECURRENTE":
                    msg_undo_resp = "Ese movimiento fue generado automáticamente y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum."
                else:
                    msg_undo_resp = "No tenés ningún movimiento reciente registrado por WhatsApp para deshacer. Podés gestionarlo desde la web de Argentum."
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
                msg_resp = "El último movimiento fue hace más de 30 minutos. Para modificarlo, ingresá a la web de Argentum."
            elif motivo_corr == "ES_CUOTA":
                msg_resp = "Ese movimiento corresponde a una cuota de tarjeta y no se puede modificar por WhatsApp. Podés gestionarlo desde la web de Argentum."
            else:
                msg_resp = "No tenés ningún movimiento reciente registrado por WhatsApp para corregir. Podés gestionarlo desde la web de Argentum."
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
                    resultado_ia["respuesta_usuario"] = "No entendí qué dato querés corregir del último movimiento."

    return intent_detectado
