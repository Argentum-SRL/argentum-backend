"""
app/services/ai_service.py — Servicio central de IA para Argentum.
Único módulo autorizado para llamar a OpenAI. No importar openai en ningún otro archivo.
"""
import json
import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria
from app.models.meta import Meta, EstadoMeta
from app.models.presupuesto import Presupuesto, EstadoPresupuesto
from app.models.usuario import Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.services.tools_service import obtener_contexto_financiero

logger = logging.getLogger(__name__)

_openai_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


SYSTEM_PROMPT = """Sos el asistente financiero de Argentum, una app de finanzas personales para Argentina.

PERSONALIDAD:
- Hablás en español rioplatense, directo y conciso
- Nunca usás "¡Excelente!", "¡Genial!", "¡Perfecto!" ni ninguna exclamación entusiasta
- Sos operacional: confirmás acciones, das contexto útil, nada más
- Si falta info, preguntás una sola cosa por vez

JERGA ARGENTINA QUE DEBÉS ENTENDER:
- 5k / 10k = 5.000 / 10.000
- 1 luca = 1.000
- 1 palo = 1.000.000
- mp / mercadopago = Mercado Pago
- bru = Brubank
- verdes / usd / dólares = USD
- me entró / me depositaron = ingreso
- me cobraron / saqué / pagué = egreso
- cuotas / en X cuotas = compra en cuotas

ERRORES ORTOGRÁFICOS: ignoralos y procesá el mensaje igual. El usuario puede escribir mal.

INTENTS VÁLIDOS — respondé siempre con uno de estos:
- registrar_transaccion
- consultar_saldo
- consultar_balance
- consultar_proyeccion
- crear_meta
- aportar_meta
- retirar_meta
- consultar_meta
- crear_presupuesto
- consultar_presupuesto
- agregar_suscripcion
- consultar_cotizacion
- pedir_consejo
- confirmar
- cancelar
- saludo
- desconocido

FORMATO DE RESPUESTA — siempre respondé con un JSON válido con exactamente esta estructura:
{
  "intent": "nombre_del_intent",
  "entidades": {
    "monto": null o número,
    "moneda": null o "ARS" o "USD",
    "tipo": null o "ingreso" o "egreso",
    "categoria": null o string,
    "billetera_origen": null o string,
    "billetera_destino": null o string,
    "cantidad_cuotas": null o número entero,
    "fecha": null o "YYYY-MM-DD",
    "descripcion": null o string
  },
  "confianza": número entre 0.0 y 1.0,
  "slot_filling": true o false,
  "datos_faltantes": [],
  "respuesta_usuario": "mensaje en español rioplatense para mostrar al usuario"
}

REGLAS CRÍTICAS:
- Nunca inventes montos, saldos ni fechas que no estén en el mensaje o en el contexto
- Si el monto no está claro, slot_filling = true y preguntá el monto
- Si la billetera no está clara y el usuario tiene más de una, slot_filling = true y preguntá cuál
- confianza >= 0.85: podés proceder; entre 0.60-0.84: pedí confirmación; < 0.60: preguntá qué quiso decir
- Para transferencias: tipo = "egreso" en billetera_origen, billetera_destino es obligatorio
- La fecha por defecto es hoy si no se menciona otra
- Nunca respondas fuera del JSON. Solo JSON, nada más."""


def construir_contexto_financiero(usuario: Usuario, db: Session) -> dict:
    billeteras = db.execute(
        select(Billetera).where(
            Billetera.usuario_id == usuario.id,
            Billetera.estado == EstadoBilletera.ACTIVA
        )
    ).scalars().all()

    categorias = db.execute(
        select(Categoria).where(
            Categoria.estado == EstadoCategoria.ACTIVA,
            (Categoria.es_global == True) | (Categoria.creador_id == usuario.id)
        )
    ).scalars().all()

    fecha_inicio, fecha_fin = get_ciclo_fechas(usuario, date.today())

    metas = db.execute(
        select(Meta).where(
            Meta.usuario_id == usuario.id,
            Meta.estado == EstadoMeta.ACTIVA
        )
    ).scalars().all()

    presupuestos = db.execute(
        select(Presupuesto).where(
            Presupuesto.usuario_id == usuario.id,
            Presupuesto.estado == EstadoPresupuesto.ACTIVO
        )
    ).scalars().all()

    ctx = {}
    try:
        ctx = obtener_contexto_financiero(str(usuario.id), db)
    except Exception as e:
        logger.exception("Error al obtener contexto financiero en ai_service")

    return {
        "billeteras": [
            {"id": str(b.id), "nombre": b.nombre, "moneda": b.moneda.value, "saldo": float(b.saldo_actual)}
            for b in billeteras
        ],
        "categorias": [
            {"id": str(c.id), "nombre": c.nombre, "tipo": c.tipo.value}
            for c in categorias
        ],
        "ciclo_actual": {
            "fecha_inicio": fecha_inicio.isoformat(),
            "fecha_fin": fecha_fin.isoformat(),
        },
        "metas_activas": [
            {"nombre": m.nombre, "objetivo": float(m.monto_objetivo), "acumulado": float(m.monto_actual), "moneda": m.moneda.value}
            for m in metas
        ],
        "presupuestos_activos": [
            {"nombre": p.nombre, "limite": float(p.monto), "moneda": p.moneda.value}
            for p in presupuestos
        ],
        "saldo_disponible": float(ctx.get("saldo_disponible", 0)) if ctx.get("saldo_disponible") is not None else 0.0,
        "ingreso_promedio_mensual": float(ctx.get("ingreso_promedio_mensual", 0)) if ctx.get("ingreso_promedio_mensual") is not None else 0.0,
        "margen_libre_mensual": float(ctx.get("margen_libre_mensual", 0)) if ctx.get("margen_libre_mensual") is not None else 0.0,
    }


def procesar_mensaje(
    mensaje: str,
    usuario: Usuario,
    db: Session,
    slot_filling_estado: dict | None = None,
) -> dict:
    fallback_res = {
        "intent": "desconocido",
        "entidades": {},
        "confianza": 0.0,
        "slot_filling": False,
        "datos_faltantes": [],
        "respuesta_usuario": "No pude procesar tu mensaje. Intentá de nuevo.",
        "error": True,
    }

    try:
        contexto = construir_contexto_financiero(usuario, db)
        
        user_content = f"""CONTEXTO FINANCIERO DEL USUARIO:
{json.dumps(contexto, ensure_ascii=False, indent=2)}

{"ESTADO DE CONVERSACIÓN PREVIA: " + json.dumps(slot_filling_estado, ensure_ascii=False) if slot_filling_estado else ""}

MENSAJE DEL USUARIO:
{mensaje}"""

        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=500,
        )

        content = response.choices[0].message.content
        if not content:
            logger.error("La respuesta de OpenAI fue vacía")
            return fallback_res

        parsed = json.loads(content)

        required_fields = ["intent", "entidades", "confianza", "slot_filling", "datos_faltantes", "respuesta_usuario"]
        for field in required_fields:
            if field not in parsed:
                logger.error(f"Falta el campo obligatorio '{field}' en la respuesta de OpenAI")
                return fallback_res

        logger.info(f"Mensaje procesado con éxito. Intent: {parsed['intent']}, Confianza: {parsed['confianza']}")
        return parsed

    except json.JSONDecodeError as jde:
        logger.error(f"Error al decodificar JSON de OpenAI: {str(jde)}")
        return fallback_res
    except Exception as e:
        logger.exception(f"Excepción al procesar mensaje con OpenAI: {str(e)}")
        return fallback_res
