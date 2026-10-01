"""
Etapa de despacho por intent, ejecución, registro y respuesta para WhatsApp:
1. Enriquecimiento de consultas e intención aportar_meta.
2. Ejecución del intent detectado (_ejecutar_intent).
3. Creación y guardado de la fila en conversaciones_wpp.
4. Registro directo si corresponde (_registrar_directo_si_corresponde).
5. Envío saliente del mensaje de respuesta vía whatsapp_service.enviar_whatsapp y registro de métricas de latencia.
"""
from __future__ import annotations

from decimal import Decimal
import time
import structlog

from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.routers.whatsapp.confirmaciones import _ejecutar_intent
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.db_lookups import (
    _buscar_meta_activa_por_nombre,
    _obtener_billeteras_activas,
)
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.routers.whatsapp.registro import _registrar_directo_si_corresponde
from app.routers.whatsapp.resolvers_cascada import (
    _generar_menu_billeteras,
    resolver_billetera_cascada,
)
from app.services import whatsapp_service
from app.utils.formato import formatear_monto

logger = structlog.get_logger(__name__)


def procesar_despacho_y_respuesta(ctx: ContextoMensaje) -> None:
    """
    Despacha la ejecución del intent, guarda la conversación, evalúa el registro directo y envía la respuesta final.
    """
    resultado_ia = ctx.resultado_ia or {}
    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario
    db = ctx.db
    wamid = ctx.wamid
    from_number = ctx.from_number
    es_imagen = ctx.es_imagen
    caption_imagen = ctx.caption_imagen
    transcripcion = ctx.transcripcion
    es_credito = ctx.es_credito
    aviso_cambio_tema = ctx.aviso_cambio_tema
    aviso_montos_faltantes = ctx.aviso_montos_faltantes
    t_inicio = ctx.t_inicio
    msg_type = ctx.msg_type

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

    # Si hubo descarte por cambio de tema, anteponer aviso en una línea
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

    if aviso_montos_faltantes and aviso_montos_faltantes not in resultado_ia["respuesta_usuario"]:
        resultado_ia["respuesta_usuario"] = f"{resultado_ia['respuesta_usuario']}\n{aviso_montos_faltantes}"
        nueva_conv.mensaje_bot = resultado_ia["respuesta_usuario"]
        db.flush()
        db.commit()
