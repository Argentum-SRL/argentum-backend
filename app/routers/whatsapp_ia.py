"""
app/routers/whatsapp_ia.py — Webhook de WhatsApp para IA conversacional de Argentum.
Recibe mensajes de Twilio, los procesa con ai_service y responde en TwiML.
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.core.auth import get_current_admin_user, get_db
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Usuario, Moneda
from app.services import ai_service
from app.services.whatsapp_service import enviar_whatsapp, formatear_numero_whatsapp
from app.services.proyeccion_service import calcular_proyeccion

logger = logging.getLogger(__name__)


def _fmt(monto: float) -> str:
    """Formatea un número con formato argentino: punto para miles, sin decimales."""
    return f"${monto:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _nombre_corto_categoria(nombre: str | None) -> str | None:
    """
    Si la categoría viene en formato 'Categoría > Subcategoría',
    devuelve solo 'Subcategoría'. Si no tiene '>', devuelve el nombre tal cual.
    """
    if not nombre:
        return None
    if ">" in nombre:
        return nombre.split(">", 1)[1].strip()
    return nombre


router = APIRouter(prefix="/whatsapp", tags=["whatsapp-ia"])


def _buscar_usuario_por_telefono(telefono_raw: str, db: Session) -> Usuario | None:
    telefono_limpio = telefono_raw
    if telefono_raw.startswith("whatsapp:"):
        telefono_limpio = telefono_raw[9:]
    
    usuario = db.execute(
        select(Usuario).where(Usuario.telefono == telefono_limpio)
    ).scalar_one_or_none()
    return usuario


def _buscar_slot_filling_activo(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    conv = db.execute(
        select(ConversacionWpp)
        .where(ConversacionWpp.usuario_id == usuario_id, ConversacionWpp.slot_filling_activo == True)
        .order_by(ConversacionWpp.fecha.desc())
    ).scalars().first()
    return conv


def _resolver_billetera(nombre: str | None, usuario_id: UUID, db: Session) -> UUID | None:
    if not nombre:
        return None
    billetera = db.execute(
        select(Billetera)
        .where(
            Billetera.usuario_id == usuario_id,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.nombre.ilike(f"%{nombre}%")
        )
    ).scalars().first()
    return billetera.id if billetera else None


def _resolver_categoria(nombre: str | None, usuario_id: UUID, db: Session) -> UUID | None:
    if not nombre:
        return None
    categoria = db.execute(
        select(Categoria)
        .where(
            Categoria.estado == EstadoCategoria.ACTIVA,
            (Categoria.es_global == True) | (Categoria.creador_id == usuario_id),
            Categoria.nombre.ilike(f"%{nombre}%")
        )
    ).scalars().first()
    return categoria.id if categoria else None


def _resolver_categoria_y_subcategoria(
    nombre: str | None,
    usuario_id: UUID,
    db: Session,
) -> tuple[UUID | None, UUID | None]:
    """
    Parsea el campo categoria que puede venir en formato:
    - "Alimentación" → solo categoría
    - "Alimentación > Verdulería" → categoría + subcategoría
    Retorna (categoria_id, subcategoria_id).
    """
    if not nombre:
        return None, None

    from app.models.subcategoria import Subcategoria

    if ">" in nombre:
        partes = [p.strip() for p in nombre.split(">", 1)]
        nombre_cat = partes[0]
        nombre_subcat = partes[1]
    else:
        nombre_cat = nombre
        nombre_subcat = None

    categoria = db.execute(
        select(Categoria).where(
            Categoria.estado == EstadoCategoria.ACTIVA,
            (Categoria.es_global == True) | (Categoria.creador_id == usuario_id),
            Categoria.nombre.ilike(f"%{nombre_cat}%")
        )
    ).scalars().first()

    if not categoria:
        return None, None

    if not nombre_subcat:
        return categoria.id, None

    subcategoria = db.execute(
        select(Subcategoria).where(
            Subcategoria.categoria_id == categoria.id,
            Subcategoria.nombre.ilike(f"%{nombre_subcat}%")
        )
    ).scalars().first()

    return categoria.id, (subcategoria.id if subcategoria else None)


def _obtener_billeteras_activas(usuario_id: UUID, db: Session) -> list[Billetera]:
    return db.execute(
        select(Billetera)
        .where(
            Billetera.usuario_id == usuario_id,
            Billetera.estado == EstadoBilletera.ACTIVA,
        )
        .order_by(Billetera.es_principal.desc(), Billetera.nombre)
    ).scalars().all()


def _generar_menu_billeteras(billeteras: list[Billetera]) -> str:
    lineas = ["¿Desde qué billetera?\n"]
    for i, b in enumerate(billeteras, 1):
        saldo_str = _fmt(float(b.saldo_actual))
        lineas.append(f"{i}. {b.nombre} — {saldo_str}")
    return "\n".join(lineas)


def _resolver_seleccion_numerica(
    mensaje: str,
    usuario_id: UUID,
    db: Session,
    conv_activa: ConversacionWpp | None,
) -> tuple[bool, str | None]:
    """
    Detecta si el mensaje es una selección numérica de billetera (1, 2, 3...).
    Retorna (es_seleccion_numerica, nombre_billetera_seleccionada).
    Solo actúa si hay una conversación activa con slot_filling y datos_faltantes incluye billetera.
    """
    mensaje_limpio = mensaje.strip()
    if not mensaje_limpio.isdigit():
        return False, None
    
    numero = int(mensaje_limpio)
    
    if not conv_activa or not conv_activa.slot_filling_activo:
        return False, None
    
    estado = conv_activa.slot_filling_estado or {}
    datos_faltantes = estado.get("datos_faltantes", [])
    if "billetera_origen" not in datos_faltantes and "billetera" not in datos_faltantes:
        return False, None
    
    billeteras = _obtener_billeteras_activas(usuario_id, db)
    if numero < 1 or numero > len(billeteras):
        return True, None
    
    billetera_seleccionada = billeteras[numero - 1]
    return True, billetera_seleccionada.nombre


def _obtener_historial_reciente(usuario_id: UUID, db: Session, n: int = 6) -> list[dict]:
    """
    Obtiene los últimos N turnos de conversación del usuario.
    Retorna lista de dicts con keys 'usuario' y 'bot'.
    Solo incluye conversaciones de los últimos 30 minutos para mantener contexto relevante.
    """
    from datetime import datetime, timezone, timedelta
    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=30)
    
    convs = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.fecha >= limite_tiempo,
        )
        .order_by(ConversacionWpp.fecha.desc())
        .limit(n)
    ).scalars().all()
    
    # Revertir para orden cronológico
    convs = list(reversed(convs))
    
    resultado = []
    for c in convs:
        # Excluir mensajes generados por el backend (menús, errores)
        if c.mensaje_bot.startswith("¿Desde qué billetera?"):
            continue
        if c.mensaje_bot.startswith("No pude procesar"):
            continue
        if c.mensaje_bot.startswith("No pude leer"):
            continue
        if c.mensaje_bot.startswith("No entendí"):
            continue
        if c.mensaje_bot.startswith("Opción inválida"):
            continue
        if c.intent_detectado is None:
            continue
        resultado.append({
            "usuario": c.mensaje_usuario,
            "bot": c.mensaje_bot,
            "intent": c.intent_detectado or "desconocido",
            "entidades": c.entidades or {},
            "confianza": float(c.confianza) if c.confianza else 0.9,
        })
    return resultado


def _ejecutar_intent(resultado_ia: dict, usuario: Usuario, db: Session) -> str | None:
    try:
        intent = resultado_ia.get("intent")
        confianza = resultado_ia.get("confianza", 0.0)
        slot_filling = resultado_ia.get("slot_filling", False)

        if intent == "registrar_transaccion" and confianza >= 0.85 and not slot_filling:
            # No crear todavía — el system prompt ya pidió confirmación al usuario
            # Solo retornar None; el estado queda en slot_filling_estado para el turno siguiente
            return None

        elif intent == "confirmar":
            # Buscar transacción pendiente existente de IA
            tx = db.execute(
                select(Transaccion)
                .where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.origen == OrigenTransaccion.IA_WPP,
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
                )
                .order_by(Transaccion.fecha_creacion.desc())
            ).scalars().first()

            if tx:
                # Confirmar transacción existente
                tx.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
                db.flush()
                return str(tx.id)
            else:
                # Buscar conversación previa con datos de transacción pendiente de confirmar
                from datetime import datetime, timezone, timedelta

                # Solo buscar conversaciones de los últimos 10 minutos
                limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=10)

                conv_previa = db.execute(
                    select(ConversacionWpp)
                    .where(
                        ConversacionWpp.usuario_id == usuario.id,
                        ConversacionWpp.intent_detectado == "registrar_transaccion",
                        ConversacionWpp.slot_filling_activo == False,
                        ConversacionWpp.confianza >= Decimal("0.85"),
                        ConversacionWpp.fecha >= limite_tiempo,
                    )
                    .order_by(ConversacionWpp.fecha.desc())
                ).scalars().first()

                if conv_previa and conv_previa.entidades:
                    entidades = conv_previa.entidades
                    monto = entidades.get("monto")
                    if monto is None:
                        return None

                    tipo_val = entidades.get("tipo") or "egreso"
                    # Para ingresos usar billetera_destino, para egresos billetera_origen
                    nombre_billetera = (
                        entidades.get("billetera_destino")
                        if tipo_val == "ingreso"
                        else entidades.get("billetera_origen")
                    ) or entidades.get("billetera_origen") or entidades.get("billetera_destino")
                    billetera_id = _resolver_billetera(nombre_billetera, usuario.id, db)
                    if not billetera_id:
                        billetera_id = db.execute(
                            select(Billetera.id).where(
                                Billetera.usuario_id == usuario.id,
                                Billetera.estado == EstadoBilletera.ACTIVA,
                                Billetera.es_principal == True
                            )
                        ).scalar_one_or_none()
                    if not billetera_id:
                        billetera_id = db.execute(
                            select(Billetera.id).where(
                                Billetera.usuario_id == usuario.id,
                                Billetera.estado == EstadoBilletera.ACTIVA
                            )
                        ).scalars().first()

                    if not billetera_id:
                        return None

                    categoria_id, subcategoria_id = _resolver_categoria_y_subcategoria(
                        entidades.get("categoria"), usuario.id, db
                    )
                    moneda_val = Moneda.USD if entidades.get("moneda") == "USD" else Moneda.ARS

                    fecha_val = entidades.get("fecha")
                    fecha_obj = date.today()
                    if fecha_val:
                        try:
                            fecha_obj = date.fromisoformat(str(fecha_val))
                        except Exception:
                            fecha_obj = date.today()

                    transaccion = Transaccion(
                        usuario_id=usuario.id,
                        tipo=TipoTransaccion.INGRESO if tipo_val == "ingreso" else TipoTransaccion.EGRESO,
                        monto=Decimal(str(monto)),
                        moneda=moneda_val,
                        fecha=fecha_obj,
                        descripcion=entidades.get("descripcion") or _nombre_corto_categoria(entidades.get("categoria")) or "Transacción por WhatsApp",
                        billetera_id=billetera_id,
                        categoria_id=categoria_id,
                        subcategoria_id=subcategoria_id,
                        origen=OrigenTransaccion.IA_WPP,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                        es_recurrente=False,
                        es_cuota_hija=False,
                        es_padre_cuotas=False
                    )
                    db.add(transaccion)
                    db.flush()
                    return str(transaccion.id)

            return None

        elif intent == "cancelar":
            tx = db.execute(
                select(Transaccion)
                .where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.origen == OrigenTransaccion.IA_WPP,
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
                )
                .order_by(Transaccion.fecha_creacion.desc())
            ).scalars().first()

            if tx:
                db.delete(tx)
                db.flush()
            return None

        return None

    except Exception as e:
        logger.error(f"Error al ejecutar intent {resultado_ia.get('intent')} para usuario {usuario.id}: {str(e)}")
        return None


def _transcribir_audio(media_url: str, media_content_type: str) -> str | None:
    """
    Descarga un audio de Twilio y lo transcribe con Whisper.
    Retorna el texto transcripto o None si falla.
    """
    import httpx
    import tempfile
    import os
    from app.core.config import settings
    from openai import OpenAI

    try:
        # Descargar el audio desde Twilio con autenticación básica
        twilio_sid = settings.TWILIO_ACCOUNT_SID
        twilio_token = settings.TWILIO_AUTH_TOKEN

        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(
                media_url,
                auth=(twilio_sid, twilio_token),
            )
            response.raise_for_status()
            audio_bytes = response.content

        # Determinar extensión según content type
        ext_map = {
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".mp4",
            "audio/wav": ".wav",
            "audio/webm": ".webm",
            "audio/amr": ".amr",
        }
        ext = ext_map.get(media_content_type, ".ogg")

        # Guardar en archivo temporal y transcribir
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            client_oai = OpenAI(api_key=settings.OPENAI_API_KEY)
            with open(tmp_path, "rb") as audio_file:
                transcripcion = client_oai.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="es",
                )
            return transcripcion.text
        finally:
            os.unlink(tmp_path)

    except Exception:
        logger.exception("Error al transcribir audio de WhatsApp")
        return None


def _extraer_transaccion_de_imagen(media_url: str, media_content_type: str, usuario_nombre: str = "") -> str | None:
    """
    Descarga una imagen de Twilio y usa GPT-4o Vision para extraer
    información de un ticket, factura o comprobante.
    Retorna una descripción en texto de lo que encontró, o None si falla.
    """
    import httpx
    import base64
    from app.core.config import settings
    from openai import OpenAI

    nombre_anonimo = ""
    if usuario_nombre:
        partes = usuario_nombre.strip().split()
        if len(partes) > 1:
            nombre_anonimo = " ".join(partes[:-1]) + f" {partes[-1][0]}."
        elif partes:
            nombre_anonimo = partes[0]

    try:
        twilio_sid = settings.TWILIO_ACCOUNT_SID
        twilio_token = settings.TWILIO_AUTH_TOKEN

        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(
                media_url,
                auth=(twilio_sid, twilio_token),
            )
            response.raise_for_status()
            image_bytes = response.content

        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        client_oai = OpenAI(api_key=settings.OPENAI_API_KEY)

        vision_response = client_oai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Sos un asistente que analiza tickets, facturas y comprobantes de pago argentinos. "
                        "Extraé la información y respondé SOLO con una descripción en español rioplatense, "
                        "como si el usuario de la app lo hubiera escrito. "
                        "\n\nREGLAS IMPORTANTES:"
                        "\n- Si es un ticket de compra o factura: 'gasté [monto] en [comercio]'"
                        "\n- Si es un comprobante de transferencia: determiná quién envió y quién recibió"
                        "\n  * Si el usuario es el DESTINATARIO (aparece en 'Para', 'A', 'Destinatario'): 'me entraron [monto] de [nombre origen]'"
                        "\n  * Si el usuario es el ORIGEN (aparece en 'De', 'Origen', 'Remitente'): 'transferí [monto] a [nombre destinatario]'"
                        "\n- Si hay fecha distinta a hoy, mencionala al final: 'el [fecha]'"
                        "\n- Incluí el monto exacto con el símbolo $ tal como aparece en el comprobante"
                        "\n- Si no podés identificar el monto, respondé exactamente: NO_IDENTIFICADO"
                        + (f"\n\nNOMBRE DEL USUARIO DE LA APP (ANONIMIZADO): '{nombre_anonimo}'. "
                           "Comparalo con los nombres en el comprobante para determinar si es ingreso o egreso. "
                           "Buscá coincidencias en el comprobante (ej: si el nombre es 'Sebastián G.', puede coincidir con 'Sebastián Gómez', 'Sebastián Ariel Gómez', etc)."
                           if nombre_anonimo else "")
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_content_type};base64,{image_b64}",
                                "detail": "high"
                            }
                        },
                        {
                            "type": "text",
                            "text": (
                                "Analizá este comprobante y describí la transacción. "
                                + (f"IMPORTANTE: el usuario de la app se llama '{nombre_anonimo}' (nombre minimizado por privacidad). "
                                   f"Buscá coincidencias con este nombre en el comprobante (ej: si es 'Sebastián G.', puede coincidir con 'Sebastián Gómez', 'SEBASTIAN ARIEL GOMEZ', etc.). "
                                   f"Si el usuario aparece como destinatario (en el campo 'Para', 'A', o 'Destinatario'), es un INGRESO: respondé 'me entraron [monto] de [origen]'. "
                                   f"Si el usuario aparece como origen (en el campo 'De', 'Desde', o 'Remitente'), es un EGRESO: respondé 'transferí [monto] a [destinatario]'."
                                   if nombre_anonimo else "")
                            )
                        }
                    ]
                }
            ],
            max_tokens=200,
        )

        resultado = vision_response.choices[0].message.content
        if not resultado or resultado.strip() == "NO_IDENTIFICADO":
            return None

        logger.info(f"Imagen analizada: '{resultado[:100]}'")
        return resultado.strip()

    except Exception:
        logger.exception("Error al analizar imagen de WhatsApp")
        return None



@router.post("/webhook", response_class=PlainTextResponse)
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(default=""),
    From: str = Form(default=""),
    NumMedia: str = Form(default="0"),
    MediaUrl0: str = Form(default=""),
    MediaContentType0: str = Form(default=""),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    # Validación de firma Twilio (solo si el token está configurado)
    if settings.TWILIO_AUTH_TOKEN:
        twilio_signature = request.headers.get("X-Twilio-Signature", "")
        webhook_url = str(request.url)
        form_params = {
            "Body": Body,
            "From": From,
            "NumMedia": NumMedia,
            "MediaUrl0": MediaUrl0,
            "MediaContentType0": MediaContentType0,
        }
        validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
        if not validator.validate(webhook_url, form_params, twilio_signature):
            raise HTTPException(status_code=403, detail="Firma Twilio inválida")

    try:
        usuario = _buscar_usuario_por_telefono(From, db)
        if not usuario:
            resp = MessagingResponse()
            resp.message("No encontramos tu cuenta. Registrate en argentum.app")
            return PlainTextResponse(content=str(resp), media_type="application/xml")

        # Procesar audio si viene un mensaje de voz
        mensaje_texto = Body.strip()
        transcripcion = None

        if NumMedia != "0" and MediaUrl0:
            if MediaContentType0.startswith("audio"):
                transcripcion = _transcribir_audio(MediaUrl0, MediaContentType0)
                if transcripcion:
                    mensaje_texto = transcripcion
                    logger.info(f"Audio transcripto: '{transcripcion[:100]}'")
                else:
                    resp = MessagingResponse()
                    resp.message("No pude escuchar el audio. Mandame el mensaje en texto.")
                    return PlainTextResponse(content=str(resp), media_type="application/xml")

            elif MediaContentType0.startswith("image"):
                nombre_usuario = f"{usuario.nombre or ''} {usuario.apellido or ''}".strip()
                descripcion_imagen = _extraer_transaccion_de_imagen(MediaUrl0, MediaContentType0, nombre_usuario)
                if descripcion_imagen:
                    mensaje_texto = descripcion_imagen
                    logger.info(f"Imagen analizada: '{descripcion_imagen[:100]}'")
                else:
                    resp = MessagingResponse()
                    resp.message("No pude leer el comprobante. Mandame los datos en texto.")
                    return PlainTextResponse(content=str(resp), media_type="application/xml")
        
        if not mensaje_texto:
            resp = MessagingResponse()
            resp.message("No entendí bien lo que quisiste decir. Podés contarme qué gastaste, por ejemplo: *Almuerzo $1500*")
            return PlainTextResponse(content=str(resp), media_type="application/xml")

        # Solo para menú numérico de billeteras
        conv_activa = _buscar_slot_filling_activo(usuario.id, db)

        # Detectar selección numérica de billetera
        es_seleccion, nombre_billetera = _resolver_seleccion_numerica(
            mensaje_texto, usuario.id, db, conv_activa
        )
        if es_seleccion:
            if nombre_billetera is None:
                # Número fuera de rango
                billeteras = _obtener_billeteras_activas(usuario.id, db)
                resp = MessagingResponse()
                resp.message(f"Opción inválida. Elegí un número del 1 al {len(billeteras)}.")
                return PlainTextResponse(content=str(resp), media_type="application/xml")
            
            # Inyectar la billetera seleccionada en el mensaje para que la IA lo procese
            mensaje_enriquecido = f"{mensaje_texto} (billetera: {nombre_billetera})"
            if conv_activa and conv_activa.slot_filling_estado:
                estado_previo = dict(conv_activa.slot_filling_estado)
                estado_previo["billetera_origen"] = nombre_billetera
                if "datos_faltantes" in estado_previo:
                    estado_previo["datos_faltantes"] = [
                        d for d in estado_previo["datos_faltantes"]
                        if d not in ("billetera_origen", "billetera")
                    ]
            else:
                estado_previo = None
            
            resultado_ia = ai_service.procesar_mensaje(
                mensaje=mensaje_enriquecido,
                usuario=usuario,
                db=db,
                historial=_obtener_historial_reciente(usuario.id, db),
            )
        else:
            resultado_ia = ai_service.procesar_mensaje(
                mensaje=mensaje_texto,
                usuario=usuario,
                db=db,
                historial=_obtener_historial_reciente(usuario.id, db),
            )

        # Enriquecer respuesta con datos reales para intents de consulta
        intent_detectado = resultado_ia.get("intent")

        if intent_detectado == "consultar_proyeccion":
            try:
                from app.services.proyeccion_service import calcular_proyeccion
                proyeccion = calcular_proyeccion(db, usuario)
                balance_proy = proyeccion.get("balance_proyectado", 0)
                dias_rest = proyeccion.get("periodo", {}).get("dias_restantes", 0)
                confianza_proy = proyeccion.get("nivel_confianza", "bajo")
                advertencias = proyeccion.get("advertencias", [])
                
                if confianza_proy == "bajo":
                    msg = "Todavía no tenés suficiente historial para una proyección confiable."
                elif balance_proy >= 0:
                    msg = f"Si seguís así, terminás el ciclo con aproximadamente {_fmt(balance_proy)} disponibles ({dias_rest} días restantes)."
                else:
                    msg = f"Ojo — si seguís así, terminarías el ciclo con {_fmt(abs(balance_proy))} en rojo ({dias_rest} días restantes)."
                
                if advertencias:
                    msg += f" {advertencias[0]}"
                
                resultado_ia["respuesta_usuario"] = msg
            except Exception:
                logger.exception("Error al calcular proyección para WhatsApp")

        elif intent_detectado == "consultar_saldo":
            try:
                from app.services.dashboard_service import get_dashboard_resumen
                resumen = get_dashboard_resumen(db, usuario)
                total = resumen["disponible_real"]["total_billeteras"]
                disponible = resumen["disponible_real"]["disponible"]
                resultado_ia["respuesta_usuario"] = f"Tenés {_fmt(total)} en tus billeteras. Disponible real (descontando cuotas): {_fmt(disponible)}."
            except Exception:
                logger.exception("Error al calcular saldo para WhatsApp")

        # Sobreescribir propuesta de transacción con mensaje limpio generado por backend
        if (
            intent_detectado == "registrar_transaccion"
            and resultado_ia.get("confianza", 0) >= 0.85
            and not resultado_ia.get("slot_filling", False)
        ):
            try:
                entidades = resultado_ia.get("entidades", {})
                monto = entidades.get("monto")
                categoria_raw = entidades.get("categoria")
                tipo = entidades.get("tipo", "egreso")
                billetera_raw = (
                    entidades.get("billetera_destino")
                    if tipo == "ingreso"
                    else entidades.get("billetera_origen")
                ) or entidades.get("billetera_origen") or entidades.get("billetera_destino")

                if monto is not None:
                    monto_str = _fmt(float(monto))
                    
                    # Nombre corto de categoría
                    cat_display = _nombre_corto_categoria(categoria_raw) if categoria_raw else None
                    
                    # Nombre de billetera
                    bill_display = None
                    if billetera_raw:
                        bill = db.execute(
                            select(Billetera).where(
                                Billetera.usuario_id == usuario.id,
                                Billetera.estado == EstadoBilletera.ACTIVA,
                                Billetera.nombre.ilike(f"%{billetera_raw}%")
                            )
                        ).scalars().first()
                        bill_display = bill.nombre if bill else billetera_raw

                    if tipo == "ingreso":
                        partes = [f"Voy a registrar un ingreso de {monto_str}"]
                        if cat_display:
                            partes.append(f"en {cat_display}")
                        if bill_display:
                            partes.append(f"a {bill_display}")
                    else:
                        partes = [f"Voy a anotar {monto_str}"]
                        if cat_display:
                            partes.append(f"en {cat_display}")
                        if bill_display:
                            partes.append(f"desde {bill_display}")
                    partes.append("¿Va?")

                    resultado_ia["respuesta_usuario"] = " ".join(partes)
            except Exception:
                logger.exception("Error al construir propuesta de transacción")

        # Si la IA pide que el usuario elija billetera, mostrar menú numerado
        if resultado_ia.get("slot_filling") and resultado_ia.get("entidades", {}).get("billetera_origen") is None:
            datos_faltantes = resultado_ia.get("datos_faltantes", [])
            entidades = resultado_ia.get("entidades", {})
            necesita_billetera = (
                "billetera_origen" in datos_faltantes
                or "billetera" in datos_faltantes
                or (
                    resultado_ia.get("intent") == "registrar_transaccion"
                    and entidades.get("monto") is not None
                    and entidades.get("billetera_origen") is None
                )
            )
            if necesita_billetera:
                billeteras = _obtener_billeteras_activas(usuario.id, db)
                if len(billeteras) > 1:
                    resultado_ia["respuesta_usuario"] = _generar_menu_billeteras(billeteras)
                    # Guardar datos_faltantes en slot_filling_estado para que _resolver_seleccion_numerica lo detecte
                    if resultado_ia.get("entidades") is None:
                        resultado_ia["entidades"] = {}
                    resultado_ia["entidades"]["datos_faltantes"] = ["billetera_origen"]

        transaccion_id = _ejecutar_intent(resultado_ia, usuario, db)

        # Si se confirmó o creó una transacción, sobreescribir la respuesta con confirmación real
        if transaccion_id and intent_detectado == "confirmar":
            try:
                tx = db.execute(
                    select(Transaccion).where(Transaccion.id == UUID(transaccion_id))
                ).scalars().first()
                if tx:
                    tipo_str = "ingreso" if tx.tipo == TipoTransaccion.INGRESO else "egreso"
                    monto_str = _fmt(float(tx.monto))
                    
                    # Obtener nombre de categoría
                    cat_nombre = None
                    if tx.categoria_id:
                        from app.models.categoria import Categoria
                        cat = db.execute(
                            select(Categoria).where(Categoria.id == tx.categoria_id)
                        ).scalars().first()
                        cat_nombre = cat.nombre if cat else None

                    # Si hay subcategoría, mostrar su nombre en vez de la categoría principal
                    subcat_nombre = None
                    if tx.subcategoria_id:
                        from app.models.subcategoria import Subcategoria
                        subcat = db.execute(
                            select(Subcategoria).where(Subcategoria.id == tx.subcategoria_id)
                        ).scalars().first()
                        subcat_nombre = subcat.nombre if subcat else None

                    nombre_categoria_display = subcat_nombre or cat_nombre
                    
                    # Obtener nombre de billetera
                    bill_nombre = None
                    if tx.billetera_id:
                        bill = db.execute(
                            select(Billetera).where(Billetera.id == tx.billetera_id)
                        ).scalars().first()
                        bill_nombre = bill.nombre if bill else None
                    
                    if tx.tipo == TipoTransaccion.INGRESO:
                        partes = [f"Listo. Ingreso de {monto_str}"]
                        if nombre_categoria_display:
                            partes.append(f"en {nombre_categoria_display}")
                        if bill_nombre:
                            partes.append(f"a {bill_nombre}")
                        partes.append("— registrado.")
                    else:
                        partes = [f"Listo. {monto_str}"]
                        if nombre_categoria_display:
                            partes.append(f"en {nombre_categoria_display}")
                        if bill_nombre:
                            partes.append(f"desde {bill_nombre}")
                        partes.append("— registrado.")
                    
                    resultado_ia["respuesta_usuario"] = " ".join(partes)
            except Exception:
                logger.exception("Error al construir mensaje de confirmación")

        # Si se canceló, asegurar tono rioplatense
        if intent_detectado == "cancelar":
            resultado_ia["respuesta_usuario"] = "Listo, cancelado."

        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            mensaje_usuario=mensaje_texto,
            tipo_mensaje=TipoMensajeWpp.AUDIO if transcripcion else TipoMensajeWpp.TEXTO,
            transcripcion=transcripcion,
            mensaje_bot=resultado_ia["respuesta_usuario"],
            intent_detectado=resultado_ia.get("intent"),
            entidades=resultado_ia.get("entidades"),
            accion_ejecutada=str(transaccion_id) if transaccion_id else None,
            confianza=Decimal(str(resultado_ia.get("confianza", 0))),
            slot_filling_activo=resultado_ia.get("slot_filling", False),
            slot_filling_estado=resultado_ia.get("entidades") if resultado_ia.get("slot_filling") else None,
        )
        db.add(nueva_conv)
        db.commit()

        resp = MessagingResponse()
        resp.message(resultado_ia["respuesta_usuario"])
        return PlainTextResponse(content=str(resp), media_type="application/xml")

    except Exception as e:
        db.rollback()
        logger.exception("Error procesando mensaje en webhook")
        resp = MessagingResponse()
        resp.message("Hubo un problema al procesar tu mensaje. Intentá de nuevo.")
        return PlainTextResponse(content=str(resp), media_type="application/xml")


@router.post("/test")
def test_ia(
    mensaje: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
) -> dict:
    return ai_service.procesar_mensaje(
        mensaje=mensaje,
        usuario=current_user,
        db=db,
        historial=None,
    )
