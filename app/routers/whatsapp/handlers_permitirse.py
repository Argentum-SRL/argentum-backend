"""
app/routers/whatsapp/handlers_permitirse.py

Handler determinístico y funciones puras para la consulta '¿Me lo puedo permitir?' por WhatsApp,
con paridad de números con la web (tools_service.calcular_puede_permitirse).
"""
from __future__ import annotations

from decimal import Decimal
import re
import structlog

from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.usuario import Moneda
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.parsers import _parsear_monto_argentino, montos_de_dinero_en_texto
from app.services import tools_service, whatsapp_service
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto

logger = structlog.get_logger(__name__)

EXCLUSIONES = [
    "dolar",
    "usd",
    "u$s",
    "us$",
    "pagar",
    "fin de mes",
    "llegar",
]

FRASES_FUERTES = [
    "me lo puedo permitir",
    "me la puedo permitir",
    "me los puedo permitir",
    "me las puedo permitir",
    "me puedo permitir",
    "puedo permitirme",
]

FRASES_DEBILES = [
    "me alcanza para",
    "puedo comprarme",
    "puedo comprar",
    "puedo gastar",
]


def detectar_consulta_permitirse(texto: str) -> dict | None:
    """
    Detecta si un mensaje de WhatsApp es una consulta sobre si el usuario puede permitirse un gasto.
    Devuelve None si no aplica o si es una frase débil sin precio.
    Devuelve un diccionario estructurado si aplica.
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
        es_debil = any(f in norm for f in FRASES_DEBILES)

    if not es_fuerte and not es_debil:
        return None

    # d) y e) Cuotas y Precio
    # Caso especial: "N cuotas de X" (ej: "12 cuotas de 50 lucas", "12 cuotas de $50.000")
    m_cuotas_de = re.search(
        r"\b(\d+)\s+cuotas?\s+de\s+(?:cada\s+una\s+de\s+)?([\$€£]|us\$|usd)?\s*([\d\.,]+(?:\s*(?:pesos|mil|lucas?|k|palos?))?)",
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
        if m_cuotas:
            modo = "cuotas"
            cantidad_cuotas = int(m_cuotas.group(1))
        elif re.search(r"\ben\s+cuotas?\b", norm, flags=re.IGNORECASE) or re.search(r"\bcuotas?\b", norm, flags=re.IGNORECASE):
            modo = "cuotas"
            cantidad_cuotas = None
        else:
            modo = "contado"
            cantidad_cuotas = 1

        # d) Precio:
        # Primero buscar con montos_de_dinero_en_texto
        montos_marca = montos_de_dinero_en_texto(texto)
        for m_str in montos_marca:
            val_m = _parsear_monto_argentino(m_str)
            if val_m and val_m > 0:
                precio = val_m
                break

        # Si no hay ninguno: buscar números sueltos que no sean cantidad de cuotas ni porcentaje
        if precio is None:
            candidatos_num = []
            for m in re.finditer(r"\b\d+(?:[\.,]\d+)*\b", norm):
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

    if "interes" in norm:
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
    if precio is None:
        if es_debil:
            return None
        # - frase fuerte sin precio: {"falta": "precio"}
        return {"falta": "precio"}

    # - modo cuotas sin cantidad: {"falta": "cuotas"}
    if modo == "cuotas" and cantidad_cuotas is None:
        return {"falta": "cuotas"}

    # - completo
    return {
        "falta": None,
        "precio": precio,
        "modo": modo,
        "cantidad_cuotas": cantidad_cuotas if modo == "cuotas" else 1,
        "tiene_interes": tiene_interes,
        "tna": tna,
        "nota_interes": nota_interes,
    }


def armar_respuesta_permitirse(resultado: dict, consulta: dict) -> str:
    """
    Construye la respuesta textual determinística para WhatsApp según el resultado de
    calcular_puede_permitirse o los datos faltantes de la consulta.
    Todos los montos van con formatear_monto(..., Moneda.ARS) y porcentajes redondeados a entero.
    """
    falta = consulta.get("falta")
    if falta == "precio":
        return 'Decime cuánto sale. Por ejemplo: "¿me puedo permitir algo de 300.000 en 6 cuotas?"'
    if falta == "cuotas":
        return 'Decime en cuántas cuotas. Por ejemplo: "¿me puedo permitir algo de 300.000 en 6 cuotas?"'

    modo = resultado.get("modo") or consulta.get("modo") or "contado"
    semaforo = resultado.get("semaforo")

    if modo == "contado":
        mensaje_principal = resultado.get("mensaje_principal", "")
        saldo_disp = formatear_monto(resultado.get("saldo_disponible_actual", 0), Moneda.ARS)
        precio_fmt = formatear_monto(resultado.get("precio_total", consulta.get("precio", 0)), Moneda.ARS)

        if semaforo == "negro":
            texto = f"{mensaje_principal} Tenés {saldo_disp} disponibles y cuesta {precio_fmt}."
        else:
            saldo_rest = formatear_monto(resultado.get("saldo_restante_post_compra", 0), Moneda.ARS)
            texto = f"{mensaje_principal} Te quedarían {saldo_rest} de los {saldo_disp} que tenés disponibles."

    else:
        # Modo cuotas
        monto_cuota_fmt = formatear_monto(resultado.get("monto_cuota", 0), Moneda.ARS)

        if semaforo == "gris":
            texto = (
                f"La cuota sería de {monto_cuota_fmt} por mes. "
                "Para decirte si te entra necesito conocer tus cobros: "
                "cargá tus ingresos y volvé a preguntarme."
            )
        else:
            mensaje_principal = resultado.get("mensaje_principal", "")
            carga_total_fmt = formatear_monto(resultado.get("carga_mensual_nueva_total", 0), Moneda.ARS)
            pct = resultado.get("porcentaje_carga_sobre_ingreso")
            pct_str = f"{round(pct)}%" if pct is not None else ""
            texto = (
                f"{mensaje_principal} La cuota sería de {monto_cuota_fmt} por mes. "
                f"Con lo que ya pagás en cuotas y suscripciones llegarías a {carga_total_fmt} por mes, "
                f"el {pct_str} de tu ingreso."
            )

            margen = resultado.get("margen_libre_post_compra")
            if margen is not None:
                if margen >= 0:
                    margen_fmt = formatear_monto(margen, Moneda.ARS)
                    texto += f" Después de tus gastos de siempre te quedarían {margen_fmt} por mes."
                else:
                    margen_fmt = formatear_monto(abs(margen), Moneda.ARS)
                    texto += f" Después de tus gastos de siempre te faltarían {margen_fmt} por mes."

        # Interés
        tiene_interes = resultado.get("tiene_interes") or consulta.get("tiene_interes", False)
        if tiene_interes:
            precio_real_fmt = formatear_monto(resultado.get("precio_total_real", 0), Moneda.ARS)
            interes_total_fmt = formatear_monto(resultado.get("interes_total", 0), Moneda.ARS)
            texto += f" Con interés pagarías {precio_real_fmt} en total ({interes_total_fmt} de interés)."

        # Nota de interés
        if consulta.get("nota_interes", False):
            texto += "\nLo calculé sin interés. Si tiene interés, decime la tasa anual (TNA)."

    return texto


def manejar_consulta_permitirse(ctx: ContextoMensaje) -> bool:
    """
    Handler determinístico para responder a consultas de '¿Me lo puedo permitir?'.
    Evalúa antes de la IA. Si aplica, calcula con tools_service.calcular_puede_permitirse,
    persiste ConversacionWpp, responde por WhatsApp y retorna True.
    Si no aplica, retorna False.
    """
    mensaje_texto = ctx.mensaje_texto
    usuario = ctx.usuario
    db = ctx.db
    from_number = ctx.from_number
    wamid = ctx.wamid

    if not usuario:
        return False

    consulta = detectar_consulta_permitirse(mensaje_texto)
    if consulta is None:
        return False

    if consulta.get("falta") is not None:
        msg_resp = armar_respuesta_permitirse({}, consulta)
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
            )
            msg_resp = armar_respuesta_permitirse(resultado, consulta)
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
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_resp)
    return True
