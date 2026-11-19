"""
app/routers/whatsapp/handlers_permitirse.py

Handler determinístico y funciones puras para la consulta '¿Me lo puedo permitir?' por WhatsApp,
con paridad de números con la web (tools_service.calcular_puede_permitirse).
"""
from __future__ import annotations

from decimal import Decimal
import re
import structlog

from app.core.jerga import (
    monedas_dolares_rx,
    monedas_pesos_rx,
    sufijo_multiplicador_rx,
)
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.parsers import _parsear_monto_argentino, montos_de_dinero_en_texto
from app.routers.whatsapp.permitirse_flujo import (
    armar_estado_pregunta,
    buscar_pregunta_pendiente,
    leer_respuesta_pendiente,
)
from app.routers.whatsapp.regla_registro import FRASES_PREGUNTA_PLATA
from app.services import tools_service, whatsapp_service
from app.services.uso_service import registrar_evento
from app.utils.texto import normalizar_texto

logger = structlog.get_logger(__name__)

# Excluyen siempre
EXCLUSIONES = [
    "fin de mes",
]

# Excluyen solo si no hay una frase fuerte ("me puedo permitir pagar el gimnasio" sí es consulta)
EXCLUSIONES_SIN_FRASE_FUERTE = [
    "pagar",
]

MARCAS_DOLAR = ["dolar", "usd", "u$s", "us$"]

FRASES_FUERTES = [
    "me lo puedo permitir",
    "me la puedo permitir",
    "me los puedo permitir",
    "me las puedo permitir",
    "me puedo permitir",
    "puedo permitirme",
    "me da para",
    "dar el gusto",
    "darme el gusto",
    "llego a comprar",
    "llego a comprarme",
    "tengo para comprar",
    "tengo para comprarme",
    "me lo compro",
    "me la compro",
    "me alcanza la plata para",
    "me conviene comprar",
]

FRASES_DEBILES = [
    "me alcanza para",
    "puedo comprarme",
    "puedo comprar",
    "puedo gastar",
]

FRASES_MENSUAL = ["por mes", "al mes", "mensual", "todos los meses", "cada mes"]

_NUMERO = rf"\d+(?:[\.,]\d+)*(?:\s*{sufijo_multiplicador_rx()})?"
_DOLAR = rf"(?:{monedas_dolares_rx()}|us\$)"

# Los dólares son lo que se compra ("comprar 100 dólares"): no es una consulta de permitirse
PATRON_COMPRA_DE_DOLARES = re.compile(
    rf"\bcomprar(?:me|te|nos)?\s+(?:unos\s+)?(?:{_NUMERO}\s*)?(?:de\s+)?{_DOLAR}"
)

# Los dólares son el precio ("de 1000 dólares", "de US$1000", "sale 1000 dólares")
PATRON_PRECIO_EN_DOLARES = re.compile(
    rf"(?:\bde|\bsalen?|\bcuestan?)\s+"
    rf"(?:(?:us\$|u\$s|usd)\s*({_NUMERO})|({_NUMERO})\s*{_DOLAR})"
)


def _precio_en_dolares(norm: str) -> Decimal | None:
    """El precio en dólares que trae el mensaje, o None si los dólares no van como precio."""
    if PATRON_COMPRA_DE_DOLARES.search(norm):
        return None
    encontrado = PATRON_PRECIO_EN_DOLARES.search(norm)
    if not encontrado:
        return None
    valor = _parsear_monto_argentino(encontrado.group(1) or encontrado.group(2))
    return valor if valor and valor > 0 else None


def analizar_consulta_permitirse(texto: str) -> dict | None:
    """
    Entiende un mensaje de WhatsApp como consulta de '¿Me lo puedo permitir?'.
    Devuelve None si no aplica o si es una frase débil sin precio. Si aplica, devuelve todo lo
    que entendió (precio, modo, cuotas, interés y moneda) y, en "falta", el dato que falta.
    """
    if not texto:
        return None

    norm = normalizar_texto(texto)
    if not norm:
        return None

    # a) Exclusiones: devuelve None si contiene cualquiera de las palabras excluidas
    for excl in EXCLUSIONES:
        if excl in norm:
            return None

    # b) y c) Frases fuertes y frases débiles
    es_fuerte = any(f in norm for f in FRASES_FUERTES)
    es_debil = False
    if not es_fuerte:
        sin_signos = re.sub(r"[^\w\s]", "", norm).strip()
        termina_llego = bool(sin_signos and sin_signos.split()[-1] == "llego")
        es_debil = (
            any(f in norm for f in FRASES_DEBILES)
            or any(
                re.search(rf"(?<!\w){re.escape(f)}(?!\w)", norm)
                for f in FRASES_PREGUNTA_PLATA
            )
            or termina_llego
        )

    if not es_fuerte and not es_debil:
        return None

    if not es_fuerte and any(excl in norm for excl in EXCLUSIONES_SIN_FRASE_FUERTE):
        return None

    # Dólares: solo valen si van como precio; si son lo que se compra, no es esta consulta
    moneda: str = "ARS"
    precio_dolares: Decimal | None = None
    if any(marca in norm for marca in MARCAS_DOLAR):
        precio_dolares = _precio_en_dolares(norm)
        if precio_dolares is None:
            return None
        moneda = "USD"

    # d) y e) Cuotas y Precio
    # Caso especial: "N cuotas de X" (ej: "12 cuotas de 50 lucas", "12 cuotas de $50.000")
    suf_cuotas = rf"(?:{monedas_pesos_rx()}|{sufijo_multiplicador_rx()})"
    m_cuotas_de = re.search(
        rf"\b(\d+)\s+cuotas?\s+de\s+(?:cada\s+una\s+de\s+)?"
        rf"([\$€£]|us\$|usd)?\s*([\d\.,]+(?:\s*{suf_cuotas})?)",
        norm,
        flags=re.IGNORECASE,
    )

    modo: str = "contado"
    cantidad_cuotas: int | None = 1
    precio: Decimal | None = None

    if m_cuotas_de:
        cant_n = int(m_cuotas_de.group(1))
        simbolo = m_cuotas_de.group(2) or ""
        monto_str = m_cuotas_de.group(3)
        texto_x = f"{simbolo} {monto_str}".strip()
        val_x = _parsear_monto_argentino(texto_x)
        if val_x and val_x > 0:
            precio = Decimal(cant_n) * val_x
            modo = "cuotas"
            cantidad_cuotas = cant_n

    if precio is None:
        # e) Cuotas
        m_cuotas = re.search(r"\b(\d+)\s+cuotas?\b", norm, flags=re.IGNORECASE)
        # "el gimnasio" o "la cuota del ..." con "por mes" o "al mes" es un gasto de todos los meses
        gasto_del_mes = ("el gimnasio" in norm or "la cuota del" in norm) and (
            "por mes" in norm or "al mes" in norm
        )
        if gasto_del_mes and not m_cuotas:
            modo = "mensual"
            cantidad_cuotas = 1
        elif m_cuotas:
            modo = "cuotas"
            cantidad_cuotas = int(m_cuotas.group(1))
        elif re.search(r"\ben\s+cuotas?\b", norm, flags=re.IGNORECASE) or re.search(r"\bcuotas?\b", norm, flags=re.IGNORECASE):
            modo = "cuotas"
            cantidad_cuotas = None
        else:
            if any(f in norm for f in FRASES_MENSUAL):
                modo = "mensual"
            else:
                modo = "contado"
            cantidad_cuotas = 1

        # d) Precio:
        # Si el precio viene en dólares, ya se leyó arriba
        if precio_dolares is not None:
            precio = precio_dolares

        # Si no, primero buscar con montos_de_dinero_en_texto
        montos_marca = montos_de_dinero_en_texto(texto) if precio is None else []
        for m_str in montos_marca:
            val_m = _parsear_monto_argentino(m_str)
            if val_m and val_m > 0:
                precio = val_m
                break

        # Si no hay ninguno: buscar números sueltos (con o sin sufijo) que no sean cuotas ni %
        if precio is None:
            suf_num = rf"(?:{monedas_pesos_rx()}|{sufijo_multiplicador_rx()})"
            pat_num = rf"\b\d+(?:[\.,]\d+)*(?:\s*{suf_num})?\b"
            candidatos_num = []
            for m in re.finditer(pat_num, norm):
                num_token = m.group(0)
                end_pos = m.end()
                resto = norm[end_pos:].lstrip()

                # Es porcentaje
                if resto.startswith("%"):
                    continue

                # Es la mención de cuotas (ej: "6 cuotas")
                if re.match(r"^cuotas?\b", resto, flags=re.IGNORECASE):
                    continue

                candidatos_num.append(num_token)

            if len(candidatos_num) == 1:
                val_suelto = _parsear_monto_argentino(candidatos_num[0])
                if val_suelto and val_suelto > 0:
                    precio = val_suelto

    # f) Interés
    tiene_interes: bool = False
    tna: Decimal | None = None
    nota_interes: bool = False

    if modo == "mensual":
        tiene_interes = False
        tna = None
        nota_interes = False
    elif "interes" in norm:
        m_pct = re.search(r"(\d+(?:[\.,]\d+)?)\s*%", norm)
        if m_pct:
            tiene_interes = True
            tna = Decimal(m_pct.group(1).replace(",", "."))
            nota_interes = False
        elif "sin interes" not in norm:
            tiene_interes = False
            tna = None
            nota_interes = True
        else:
            tiene_interes = False
            tna = None
            nota_interes = False

    # g) Qué devuelve:
    # - frase débil sin precio: None (el mensaje sigue a la IA)
    if precio is None and es_debil:
        return None

    # - frase fuerte sin precio: falta "precio"; modo cuotas sin cantidad: falta "cuotas"
    falta: str | None = None
    if precio is None:
        falta = "precio"
    elif modo == "cuotas" and cantidad_cuotas is None:
        falta = "cuotas"

    return {
        "falta": falta,
        "precio": precio,
        "modo": modo,
        "cantidad_cuotas": cantidad_cuotas if modo == "cuotas" else 1,
        "tiene_interes": tiene_interes,
        "tna": tna,
        "nota_interes": nota_interes,
        "moneda": moneda,
    }


def detectar_consulta_permitirse(texto: str) -> dict | None:
    """
    Detecta si un mensaje de WhatsApp es una consulta sobre si el usuario puede permitirse un gasto.
    Devuelve None si no aplica o si es una frase débil sin precio.
    Si falta un dato devuelve solo {"falta": "precio"} o {"falta": "cuotas"};
    si está completa, el diccionario con todo lo que entendió.
    """
    analisis = analizar_consulta_permitirse(texto)
    if analisis is None:
        return None
    if analisis["falta"] is not None:
        return {"falta": analisis["falta"]}
    return analisis


def armar_respuesta_permitirse(resultado: dict, consulta: dict) -> str:
    """
    Construye la respuesta textual determinística para WhatsApp según el resultado de
    calcular_puede_permitirse o los datos faltantes de la consulta.
    Usa la lógica unificada de permitirse_textos.
    """
    from app.services.permitirse_textos import armar_respuesta_permitirse as _armar
    return _armar(resultado, consulta)


def manejar_consulta_permitirse(ctx: ContextoMensaje) -> bool:
    """
    Handler determinístico para responder a consultas de '¿Me lo puedo permitir?'.
    Evalúa antes de la IA. Si aplica, calcula con tools_service.calcular_puede_permitirse,
    persiste ConversacionWpp, responde por WhatsApp y retorna True.
    Si no aplica, retorna False.
    """
    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario

    if not usuario:
        return False

    consulta = analizar_consulta_permitirse(mensaje_texto)
    if consulta is None:
        return False

    return _responder_consulta(ctx, consulta)


def manejar_respuesta_pendiente_permitirse(ctx: ContextoMensaje) -> bool:
    """
    Si hay una pregunta pendiente de '¿Me lo puedo permitir?' (precio o cuotas) y el mensaje
    es un monto o "N cuotas", completa la consulta, responde y retorna True.
    Si el mensaje es otra cosa, cierra la pregunta y retorna False: el mensaje sigue su camino.
    """
    if not ctx.usuario:
        return False

    pregunta = buscar_pregunta_pendiente(ctx.db, ctx.usuario.id)
    if pregunta is None:
        return False

    consulta = leer_respuesta_pendiente(ctx.mensaje_texto, pregunta.slot_filling_estado)
    pregunta.slot_filling_activo = False
    if consulta is None:
        ctx.db.commit()
        return False

    return _responder_consulta(ctx, consulta)


def _responder_consulta(ctx: ContextoMensaje, consulta: dict) -> bool:
    """
    Responde una consulta ya entendida: si falta un dato lo pregunta y deja la pregunta
    pendiente; si está completa, calcula con la misma función que la web.
    Siempre manda un solo mensaje.
    """
    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario
    db = ctx.db
    from_number = ctx.from_number
    wamid = ctx.wamid

    estado_pregunta: dict | None = None
    calculado = False
    if consulta.get("falta") is not None:
        msg_resp = armar_respuesta_permitirse({}, consulta)
        estado_pregunta = armar_estado_pregunta(consulta)
    else:
        try:
            precio_total = float(consulta["precio"])
            tna = float(consulta["tna"]) if consulta.get("tna") is not None else None
            resultado = tools_service.calcular_puede_permitirse(
                user_id=usuario.id,
                precio_total=precio_total,
                modo=consulta["modo"],
                cantidad_cuotas=consulta["cantidad_cuotas"],
                tiene_interes=consulta["tiene_interes"],
                tna=tna,
                ingreso_manual=None,
                db=db,
                moneda=consulta.get("moneda") or "ARS",
            )
            msg_resp = armar_respuesta_permitirse(resultado, consulta)
            calculado = True
        except Exception as e:
            logger.warning("Error calculando si puede permitirse", error=str(e), exc_info=True)
            msg_resp = "No pude calcularlo en este momento. Probá de nuevo en unos minutos."

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_resp,
        intent_detectado="puede_permitirse",
        entidades={},
        accion_ejecutada="consulta",
        confianza=Decimal("1.000"),
        slot_filling_activo=estado_pregunta is not None,
        slot_filling_estado=estado_pregunta,
    )
    db.add(nueva_conv)
    if calculado:
        registrar_evento(db, usuario.id, "permitirse_wpp", commit=False)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_resp)
    return True
