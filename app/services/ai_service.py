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
from app.models.subcategoria import Subcategoria
from app.models.usuario import Usuario
from app.services.dashboard_service import get_ciclo_fechas, get_dashboard_resumen
from app.services.openai_client import get_openai_client
from app.services.proyeccion_service import calcular_proyeccion

logger = logging.getLogger(__name__)

_openai_client: OpenAI | None = None


def _get_client() -> OpenAI:
    return get_openai_client()


SYSTEM_PROMPT = """Sos el asistente financiero de Argentum, una app de finanzas personales para Argentina.

PERSONALIDAD:
- Hablás en español rioplatense, directo y conciso
- Nunca usás "¡Excelente!", "¡Genial!", "¡Perfecto!" ni ninguna exclamación entusiasta
- Sos operacional: confirmás acciones, das contexto útil, nada más
- Si falta info, preguntás UNA SOLA COSA por vez
- Nunca usás frases corporativas ni neutras — siempre rioplatense

TONO — ejemplos de lo que SÍ decís:
- "Anotado. $5.000 en Supermercado desde Mercado Pago. ¿Confirmás?"
- "¿Cuánto gastaste?"
- "¿Fue un gasto, ingreso o transferencia?"
- "Listo, cancelado."
- "Tenés $302.000 en tus billeteras."
- "Si seguís así, terminás el ciclo con $45.000 disponibles."

TONO — ejemplos de lo que NUNCA decís:
- "¡Excelente decisión!"
- "Se ha cancelado la acción."
- "Es bueno saberlo. ¿Necesitás ayuda con algo específico?"
- "¿Deseas continuar?"

JERGA ARGENTINA QUE DEBÉS ENTENDER:
- 5k / 10k = 5.000 / 10.000
- 1 luca = 1.000
- 1 palo = 1.000.000
- mp / mercadopago / merca = Mercado Pago
- bru = Brubank
- gali / galicia = Banco Galicia
- verdes / usd / dólares / dolar = USD
- me entró / me depositaron / cobré = ingreso
- me cobraron / saqué / pagué / gasté / puse = egreso
- cuotas / en X cuotas = compra en cuotas
- efectivo / cash / plata física = Efectivo ARS

ERRORES ORTOGRÁFICOS: ignoralos completamente y procesá el mensaje igual. El usuario puede escribir muy mal.

INTENTS VÁLIDOS — respondé siempre con exactamente uno de estos:
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

REGLAS DE CLASIFICACIÓN DE INTENTS:
- "puse X", "metí X", "deposité X" SIN contexto claro → slot_filling=true, preguntá "¿Fue un gasto, ingreso o transferencia?"
- "cuánta plata tengo", "cuánto tengo", "mi saldo" → consultar_saldo
- "cómo voy", "cómo estoy este mes" → consultar_balance
- "llego a fin de mes", "me alcanza", "cuánto me queda" → consultar_proyeccion
- "cancelar", "no importa", "dejá", "olvidalo" → cancelar
- "sí", "dale", "confirmá", "ok", "va" → confirmar

FLUJO DE REGISTRO DE TRANSACCIÓN — MUY IMPORTANTE:
Cuando tenés todos los datos para registrar una transacción (monto + tipo + billetera):
1. NO registres todavía
2. Respondé con un resumen y pedí confirmación. En el mensaje al usuario, mostrá solo el nombre corto: si la categoría es "Salud y cuidado personal > Farmacia", mostrá solo "Farmacia". Si no hay subcategoría, mostrá la categoría principal. Ejemplo: "Voy a anotar $5.000 en Farmacia desde Mercado Pago. ¿Va?"
3. Esperá que el usuario confirme con "sí", "dale", "ok", etc.
4. Recién entonces el intent es "confirmar" y el backend ejecuta

FLUJO DE SLOT FILLING Y CATEGORIZACIÓN AUTOMÁTICA:
1. CATEGORIZACIÓN SIEMPRE AUTOMÁTICA (NUNCA PREGUNTAR CATEGORÍA):
   - La categoría NUNCA se pregunta al usuario bajo ninguna circunstancia.
   - Si el mensaje contiene alguna pista, concepto, rubro o comercio (ej: "nafta", "coto", "almuerzo", "farmacity", "gym", "sueldo"), asigná SIEMPRE de forma automática la categoría y subcategoría canónica más semejante del contexto (formato "Categoría > Subcategoría").
   - Si el mensaje NO contiene ninguna pista (ej: monto pelado como "gasté 500", "pagué 2000", "me entraron 10000"), asigná SIEMPRE:
     * Para egresos: "Otros > Otros"
     * Para ingresos: "Otros > Otros"
   - No generes NUNCA preguntas tipo "¿En qué categoría?".
2. BILLETERA (NO ASUMIR SI HAY VARIAS):
   - Si el usuario tiene más de una billetera activa y no la especificó, preguntá "¿Desde qué billetera?" (o "¿A qué billetera?" si es ingreso). NUNCA asumas una billetera por tu cuenta si no fue indicada y hay múltiples opciones.
3. MONTO:
   - Si falta el monto → preguntá solo "¿Cuánto fue?".
4. PREGUNTAS POR TURNO:
   - Si faltan monto Y billetera → preguntá "¿Cuánto fue y desde qué billetera?".
   - Nunca hagas más de una pregunta de slot filling por turno salvo que sean exactamente 2 cosas faltantes.

MANEJO DE ESTADO PREVIO Y RESPUESTAS A MENÚS / SELECCIONES:
- Si se te proporciona un bloque de "DATOS YA CONFIRMADOS/RESUELTOS EN ESTA CONVERSACIÓN", esos datos son la verdad establecida:
  * NO los descartes, NO los pises con null, NO los vuelvas a preguntar.
  * Si el usuario responde a una pregunta de billetera con un número o texto (ej: "1", "1 (billetera: Mercado Pago)", "mercado pago"), interpretalo como la selección de la billetera que faltaba para completar la transacción previa, NUNCA como un nuevo monto ni como una transacción nueva de $1.
  * Devolvé en el JSON de salida TODAS las entidades acumuladas (monto previo, tipo previo, categoría previa + la nueva billetera resuelta).
  * Si con este dato ya contás con monto, tipo y billetera, establecé intent="registrar_transaccion", confianza >= 0.85, slot_filling=false, y generá la propuesta pidiendo confirmación: "Voy a anotar $X en [Categoría] desde [Billetera]. ¿Va?".

FORMATO DE RESPUESTA — siempre respondé con un JSON válido con exactamente esta estructura, sin texto fuera del JSON:
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
- NUNCA inventes montos, saldos ni fechas que no estén en el mensaje o en el contexto
- NUNCA registres una transacción sin pedir confirmación primero
- Si el monto no está claro → slot_filling=true
- Si la billetera no está clara y tiene más de una → slot_filling=true
- confianza >= 0.85 y todos los datos presentes → pedí confirmación (NO registres todavía)
- confianza entre 0.60-0.84 → pedí confirmación explícita
- confianza < 0.60 → preguntá qué quiso decir
- Para transferencias: tipo="egreso" en billetera_origen, billetera_destino obligatorio
- La fecha por defecto es hoy si no se menciona
- Al categorizar un gasto o ingreso, usá EXACTAMENTE los nombres de categorías y subcategorías del contexto. Si podés identificar la subcategoría, indicala en el campo "categoria" con el formato "Categoría > Subcategoría" (ej: "Alimentación > Verdulería", "Transporte > Taxi / Remis", "Salud y cuidado personal > Farmacia"). Si no podés identificar la subcategoría, usá solo la categoría principal.
- Nunca respondas fuera del JSON. Solo JSON, nada más.
"""


def construir_contexto_financiero(usuario: Usuario, db: Session) -> dict:
    billeteras = db.execute(
        select(Billetera).where(
            Billetera.usuario_id == usuario.id,
            Billetera.estado == EstadoBilletera.ACTIVA
        )
    ).scalars().all()

    categorias_raw = db.execute(
        select(Categoria).where(
            Categoria.estado == EstadoCategoria.ACTIVA,
            (Categoria.es_global == True) | (Categoria.creador_id == usuario.id)
        )
    ).scalars().all()

    subcategorias_raw = db.execute(
        select(Subcategoria).where(
            Subcategoria.categoria_id.in_([c.id for c in categorias_raw])
        )
    ).scalars().all()

    subcats_por_cat: dict[str, list[str]] = {}
    for s in subcategorias_raw:
        key = str(s.categoria_id)
        if key not in subcats_por_cat:
            subcats_por_cat[key] = []
        subcats_por_cat[key].append(s.nombre)

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

    try:
        resumen = get_dashboard_resumen(db, usuario)
        saldo_disponible_ars = resumen["disponible_real"]["ars"]["saldo_billeteras"]
        disponible_real_ars = resumen["disponible_real"]["ars"]["disponible"]
        saldo_disponible_usd = resumen["disponible_real"]["usd"]["saldo_billeteras"]
        disponible_real_usd = resumen["disponible_real"]["usd"]["disponible"]
    except Exception:
        logger.exception("Error al obtener resumen del dashboard en ai_service")
        saldo_disponible_ars = 0.0
        disponible_real_ars = 0.0
        saldo_disponible_usd = 0.0
        disponible_real_usd = 0.0

    res = {
        "billeteras": [
            {"id": str(b.id), "nombre": b.nombre, "moneda": b.moneda.value, "saldo": float(b.saldo_actual)}
            for b in billeteras
        ],
        "categorias": [
            {
                "nombre": c.nombre,
                "tipo": c.tipo.value,
                "subcategorias": subcats_por_cat.get(str(c.id), [])
            }
            for c in categorias_raw
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
        "saldo_total_billeteras_pesos": saldo_disponible_ars,
        "disponible_real_pesos": disponible_real_ars,
        "saldo_total_billeteras_dolares": saldo_disponible_usd,
        "disponible_real_dolares": disponible_real_usd,
    }

    try:
        from app.services.perfil_financiero_service import _obtener_perfil_sync, generar_texto_contexto_ia

        perfil = _obtener_perfil_sync(db, usuario.id)
        if perfil:
            texto_contexto = generar_texto_contexto_ia(perfil)
            if texto_contexto:
                res["perfil_financiero"] = texto_contexto
    except Exception as e:
        logger.error(f"Error al inyectar perfil financiero en el AI bootstrap: {str(e)}", exc_info=True)

    return res


def construir_contexto_proyeccion(usuario: Usuario, db: Session) -> dict:
    try:
        proyeccion = calcular_proyeccion(db, usuario)
        return {
            "ars": {
                "gasto_proyectado_total": proyeccion["ars"].get("gasto_proyectado_total"),
                "balance_proyectado": proyeccion["ars"].get("balance_proyectado"),
                "ingresos_proyectados": proyeccion["ars"].get("ingresos_proyectados"),
                "nivel_confianza": proyeccion["ars"].get("nivel_confianza"),
                "advertencias": proyeccion["ars"].get("advertencias", []),
                "dias_restantes": proyeccion["ars"].get("periodo", {}).get("dias_restantes"),
                "certezas_total": proyeccion["ars"].get("certezas", {}).get("total"),
                "datos_suficientes": proyeccion["ars"].get("datos_suficientes", True)
            },
            "usd": {
                "gasto_proyectado_total": proyeccion["usd"].get("gasto_proyectado_total"),
                "balance_proyectado": proyeccion["usd"].get("balance_proyectado"),
                "ingresos_proyectados": proyeccion["usd"].get("ingresos_proyectados"),
                "nivel_confianza": proyeccion["usd"].get("nivel_confianza"),
                "advertencias": proyeccion["usd"].get("advertencias", []),
                "dias_restantes": proyeccion["usd"].get("periodo", {}).get("dias_restantes"),
                "certezas_total": proyeccion["usd"].get("certezas", {}).get("total"),
                "datos_suficientes": proyeccion["usd"].get("datos_suficientes", True)
            }
        }
    except Exception:
        logger.exception("Error al construir contexto de proyección")
        return {}


def procesar_mensaje(
    mensaje: str,
    usuario: Usuario,
    db: Session,
    historial: list[dict] | None = None,
    estado_previo: dict | None = None,
) -> dict:
    fallback_res = {
        "intent": "desconocido",
        "entidades": {},
        "confianza": 0.0,
        "slot_filling": False,
        "datos_faltantes": [],
        "respuesta_usuario": "Hubo un problema al procesar tu mensaje. Intentá de nuevo.",
        "error": True,
    }

    try:
        contexto = construir_contexto_financiero(usuario, db)
        
        # System prompt limpio sin contexto financiero
        messages_openai = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Contexto financiero como primer mensaje del sistema (separado)
        contexto_msg = f"CONTEXTO FINANCIERO ACTUAL DEL USUARIO:\n{json.dumps(contexto, ensure_ascii=False)}"
        messages_openai.append({"role": "user", "content": contexto_msg})
        messages_openai.append({"role": "assistant", "content": "Contexto recibido. Listo para procesar mensajes."})

        # Si hay estado previo acumulado de slot filling, inyectarlo explícitamente como sistema
        if estado_previo:
            estado_limpio = {
                k: v for k, v in estado_previo.items()
                if v is not None and k != "datos_faltantes"
            }
            if estado_limpio:
                estado_msg = (
                    "DATOS YA CONFIRMADOS/RESUELTOS EN ESTA CONVERSACIÓN (NO los vuelvas a preguntar, NO los pierdas, usalos para completar la transacción):\n"
                    f"{json.dumps(estado_limpio, ensure_ascii=False)}\n"
                    "Completá los campos faltantes con el nuevo mensaje del usuario y devolvé el set completo de entidades acumuladas."
                )
                messages_openai.append({"role": "system", "content": estado_msg})

        # Agregar historial de conversación (últimos N turnos)
        if historial:
            for turno in historial:
                if turno.get("usuario"):
                    messages_openai.append({"role": "user", "content": turno["usuario"]})
                if turno.get("bot"):
                    # Pasar respuesta del bot como JSON para mantener el formato
                    bot_json = json.dumps({
                        "intent": turno.get("intent", "desconocido"),
                        "entidades": turno.get("entidades", {}),
                        "confianza": turno.get("confianza", 0.9),
                        "slot_filling": False,
                        "datos_faltantes": [],
                        "respuesta_usuario": turno["bot"]
                    }, ensure_ascii=False)
                    messages_openai.append({"role": "assistant", "content": bot_json})

        # Agregar mensaje actual
        messages_openai.append({"role": "user", "content": mensaje})

        logger.info(f"Enviando {len(messages_openai)} mensajes a OpenAI. Último mensaje: {messages_openai[-1]['content'][:100]}")

        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages_openai,
            temperature=0.1,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        logger.info(f"Respuesta OpenAI cruda: '{content[:200] if content else 'VACÍA'}'")
        logger.info(f"finish_reason: {response.choices[0].finish_reason}")
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
