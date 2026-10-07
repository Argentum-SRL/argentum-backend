"""
Etapa de llamada a IA y normalización de entidades para WhatsApp:
1. Invocación de ai_service.procesar_mensaje y registro de latencia.
2. Detección y reintento por montos perdidos en silencio.
3. Ajuste determinístico de categorías por marcas comerciales y propagación de fechas.
4. Manejo de intents desconocidos o baja confianza.
5. Detección de cambio de tema y preparación del aviso correspondiente.
6. Fusión determinística de entidades con el estado de turnos previos.
"""
from __future__ import annotations

from decimal import Decimal
import time
import structlog

from app.models.usuario import Moneda
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.db_lookups import _obtener_historial_reciente
from app.routers.whatsapp.detectors import _es_pedido_deshacer
from app.routers.whatsapp.marcas import ajustar_categoria_marcas
from app.routers.whatsapp.parsers import (
    _nombre_corto_categoria,
    montos_de_dinero_en_texto,
    parsear_monto_marca,
    propagar_fechas_lote,
)
from app.routers.whatsapp.resolvers_cascada import _merge_entidades
from app.routers.whatsapp.verificacion_texto_ia import TextoIA
from app.services import ai_service
from app.utils.formato import formatear_monto

logger = structlog.get_logger(__name__)

MSG_TOPE_MOVIMIENTOS_SUPERADO = (
    "El límite es de 10 movimientos por mensaje. "
    "Por favor mandalos en tandas más chicas o usá la importación desde la web de Argentum."
)
MSG_NO_MEZCLAR_TRANSFERENCIAS = (
    "Las transferencias, extracciones de cajero y compra de dólares deben registrarse "
    "en mensajes separados de los gastos o ingresos. Por favor mandalas por separado."
)


def aplicar_marcas_y_memoria(db, usuario_id, entidades: dict) -> None:
    """Ajuste determinístico de categorías según marcas comerciales y memoria histórica por comercio."""
    from app.services.memoria_comercio_service import aplicar_memoria_a_movimiento
    if not isinstance(entidades, dict):
        return
    propagar_fechas_lote(entidades)
    ajustar_categoria_marcas(entidades)
    tipo_principal = "ingreso" if entidades.get("tipo") == "ingreso" else "egreso"
    aplicar_memoria_a_movimiento(db, usuario_id, entidades, tipo_principal)
    for ad in entidades.get("transacciones_adicionales", []):
        if isinstance(ad, dict):
            ajustar_categoria_marcas(ad)
            tipo_ad = "ingreso" if ad.get("tipo") == "ingreso" else "egreso"
            aplicar_memoria_a_movimiento(db, usuario_id, ad, tipo_ad)


def procesar_llamada_ia_y_normalizacion(ctx: ContextoMensaje) -> None:
    """
    Ejecuta el procesamiento del modelo de IA sobre el mensaje y normaliza entidades.
    Actualiza resultado_ia, aviso_montos_faltantes, aviso_cambio_tema y confianza_ia_raw en ctx.
    """
    from app.services.memoria_comercio_service import aplicar_memoria_a_movimiento

    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario
    db = ctx.db
    estado_previo = ctx.estado_previo
    conv_activa = ctx.conv_activa

    # Invocación de servicio de IA
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
    # Marcar el texto escrito por la IA antes de cualquier procesamiento posterior (Decisión 2)
    resultado_ia["respuesta_usuario"] = TextoIA(resultado_ia.get("respuesta_usuario") or "")

    # Verificación de montos perdidos en silencio
    aviso_montos_faltantes = None
    if resultado_ia.get("intent") == "registrar_transaccion":
        entidades_ia = resultado_ia.get("entidades") or {}
        adic_ia = entidades_ia.get("transacciones_adicionales") or []
        cant_items = (1 if entidades_ia.get("monto") is not None else 0) + (len(adic_ia) if isinstance(adic_ia, list) else 0)
        montos_detectados = montos_de_dinero_en_texto(mensaje_texto)
        if len(montos_detectados) > cant_items:
            n_montos = len(montos_detectados)
            lista_str = ", ".join(montos_detectados)
            msg_reintento = f"{mensaje_texto} (Atención: el mensaje tiene {n_montos} montos: {lista_str}. Devolvé un ítem por cada monto.)"
            res_reintento = ai_service.procesar_mensaje(
                mensaje=msg_reintento,
                usuario=usuario,
                db=db,
                historial=_obtener_historial_reciente(usuario.id, db),
                estado_previo=estado_previo,
            )
            if res_reintento.get("intent") == "registrar_transaccion" and res_reintento.get("entidades"):
                resultado_ia = res_reintento
                # Marcar también el texto si proviene del reintento de la IA
                resultado_ia["respuesta_usuario"] = TextoIA(resultado_ia.get("respuesta_usuario") or "")
                entidades_ia = resultado_ia.get("entidades") or {}
                adic_ia = entidades_ia.get("transacciones_adicionales") or []
                cant_items = (1 if entidades_ia.get("monto") is not None else 0) + (len(adic_ia) if isinstance(adic_ia, list) else 0)

            if len(montos_detectados) > cant_items:
                montos_reg = []
                if entidades_ia.get("monto") is not None:
                    montos_reg.append(Decimal(str(entidades_ia["monto"])))
                if isinstance(adic_ia, list):
                    for ad in adic_ia:
                        if isinstance(ad, dict) and ad.get("monto") is not None:
                            montos_reg.append(Decimal(str(ad["monto"])))
                faltantes = []
                reg_disp = list(montos_reg)
                for m_txt in montos_detectados:
                    val_m = parsear_monto_marca(m_txt)
                    match_idx = None
                    if val_m is not None:
                        for i, r in enumerate(reg_disp):
                            if abs(r - val_m) < Decimal("0.01"):
                                match_idx = i
                                break
                    if match_idx is not None:
                        reg_disp.pop(match_idx)
                    else:
                        faltantes.append(m_txt)
                if not faltantes:
                    faltantes = montos_detectados[cant_items:]
                aviso_montos_faltantes = (
                    f"Ojo: en tu mensaje también vi {', '.join(faltantes)} y no lo registré. "
                    "Mandámelo en un mensaje aparte así lo cargo bien."
                )

    # Ajuste determinístico de categorías según marcas comerciales y propagación de fechas
    if isinstance(resultado_ia.get("entidades"), dict):
        aplicar_marcas_y_memoria(db, usuario.id, resultado_ia["entidades"])

    intent_ia_raw = resultado_ia.get("intent")
    confianza_ia_raw = float(resultado_ia.get("confianza", 1.0))

    # Salvaguarda: si la IA clasifica como deshacer pero el mensaje contiene monto y no es frase de deshacer
    if intent_ia_raw == "deshacer" and not _es_pedido_deshacer(mensaje_texto) and resultado_ia.get("entidades", {}).get("monto"):
        resultado_ia["intent"] = "registrar_transaccion"
        intent_ia_raw = "registrar_transaccion"

    # Si el intent es desconocido o la confianza es baja (< 0.60), dar respuesta clara con ejemplo
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

    # Detección de cambio de tema con nueva operación
    aviso_cambio_tema = None
    entidades_ia = resultado_ia.get("entidades") or {}
    if resultado_ia.get("intent") in ("registrar_transaccion", "slot_filling"):
        monto_ia = entidades_ia.get("monto")
        cat_ia = entidades_ia.get("categoria")
        if conv_activa and conv_activa.slot_filling_estado:
            monto_prev = conv_activa.slot_filling_estado.get("monto")
            cat_prev = conv_activa.slot_filling_estado.get("categoria")
            if monto_ia is not None and (monto_ia != monto_prev or (cat_ia and cat_ia != cat_prev)):
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

    # Fusión determinística de entidades para turnos previos
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

    # Actualizar estado en el contexto
    ctx.resultado_ia = resultado_ia
    ctx.aviso_montos_faltantes = aviso_montos_faltantes
    ctx.aviso_cambio_tema = aviso_cambio_tema
    ctx.confianza_ia_raw = confianza_ia_raw
    ctx.estado_previo = estado_previo
