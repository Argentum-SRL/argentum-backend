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
from app.models.meta import Meta, EstadoMeta
from app.models.presupuesto import Presupuesto, EstadoPresupuesto
from app.models.usuario import Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.services.openai_client import get_openai_client
from app.services.proyeccion_service import calcular_proyeccion
from app.services import categoria_service
from app.utils.fecha import hoy_argentina


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
2. Respondé con un resumen y pedí confirmación. En el mensaje al usuario, mostrá solo el nombre corto: si la categoría es "Salud > Farmacia", mostrá solo "Farmacia". Si no hay subcategoría, mostrá la categoría principal. Ejemplo: "Voy a anotar $5.000 en Farmacia desde Mercado Pago. ¿Va?"
3. Esperá que el usuario confirme con "sí", "dale", "ok", etc.
4. Recién entonces el intent es "confirmar" y el backend ejecuta

MÚLTIPLES OPERACIONES EN UN SOLO MENSAJE:
- Si el mensaje describe 2 o más operaciones simples de gasto o ingreso (NO transferencias, NO compras en cuotas):
  * Tomá la primera como la transacción principal en los campos directos de "entidades" (monto, moneda, tipo, categoria, descripcion, fecha).
  * Colocá el resto en la lista "transacciones_adicionales" dentro de "entidades", donde cada elemento es un objeto con exactamente los campos: monto, moneda, tipo, categoria, descripcion, fecha (con el mismo significado y aplicando las mismas reglas de categorización automática y fecha relativa que la principal).
  * Si alguna operación adicional detectada es una transferencia o una compra en cuotas, NO la incluyas en "transacciones_adicionales" — tratá el mensaje como si solo mencionara la transacción principal.
  * Si falta la billetera y el usuario tiene más de una activa, hacé UNA SOLA pregunta de billetera que aplicará a todas las operaciones.
  * Cuando haya transacciones adicionales y la billetera esté resuelta, el resumen de confirmación en "respuesta_usuario" las lista todas en un solo mensaje. Ejemplo: "Voy a anotar 3 movimientos desde Efectivo ARS: $10.560 en Verdulería, $6.000 en Otros, $14.550 en Carnicería. ¿Va?"

FLUJO DE SLOT FILLING Y CATEGORIZACIÓN AUTOMÁTICA:
1. CATEGORIZACIÓN SIEMPRE AUTOMÁTICA (NUNCA PREGUNTAR CATEGORÍA):
   - La categoría NUNCA se pregunta al usuario bajo ninguna circunstancia.
   - Si conocés la categoría y la subcategoría, devolvé "Categoría > Subcategoría" (ej: "Alimentación > Verdulería", "Transporte > Taxi / Apps", "Salud > Farmacia").
   - Si conocés la categoría pero no la subcategoría, devolvé solo "Categoría" (ej: "Alimentación", "Transporte", "Restaurantes y delivery").
   - Si el mensaje NO contiene ninguna pista (ej: monto pelado como "gasté 500", "pagué 2000", "me entraron 10000"), devolvé SIEMPRE: "Otros".
   - Nunca inventes nombres de categorías o subcategorías que no estén en la lista provista.
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
  * Devolvé en el JSON de salida TODAS las entidades acumuladas (monto previo, tipo previo, categoría previa, transacciones_adicionales previas + la nueva billetera resuelta).
  * Si con este dato ya contás con monto, tipo y billetera, establecé intent="registrar_transaccion", confianza >= 0.85, slot_filling=false, y generá la propuesta pidiendo confirmación: "Voy a anotar $X en [Categoría] desde [Billetera]. ¿Va?" (o listando todos los movimientos si hay adicionales).

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
    "descripcion": null o string,
    "transacciones_adicionales": [
      {
        "monto": número,
        "moneda": null o "ARS" o "USD",
        "tipo": "ingreso" o "egreso",
        "categoria": null o string,
        "descripcion": null o string,
        "fecha": null o "YYYY-MM-DD"
      }
    ]
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
- Usá "fecha_actual" del contexto financiero como única referencia de qué día es hoy.
- Si el usuario menciona una fecha relativa ("ayer", "el lunes pasado", "hace 3 días"), calculala a partir de "fecha_actual", NUNCA de tu conocimiento propio.
- Si el usuario menciona sólo un día del mes sin mes ni año (ej: "el 12", "el 5"), asumí el mes y año de "fecha_actual" (a menos que ese día aún no haya ocurrido en el mes corriente, en cuyo caso usá el mes anterior — igual criterio que usaría una persona).
- Si no se menciona fecha, o si hay cualquier ambigüedad o no podés resolverla con certeza, devolvé "fecha": null (el backend asignará hoy por defecto).
- Al categorizar un gasto o ingreso, usá EXACTAMENTE los nombres de categorías y subcategorías del contexto. Si podés identificar la subcategoría, indicala en el campo "categoria" con el formato "Categoría > Subcategoría". Si conocés la categoría pero no la subcategoría, usá solo la categoría principal. Si no hay pista, usá "Otros". Nunca inventes nombres que no estén en la lista provista.
- Nunca respondas fuera del JSON. Solo JSON, nada más.
"""

_DIAS_SEMANA_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]


def construir_contexto_financiero(usuario: Usuario, db: Session) -> dict:
    from app.core.constants import CATEGORIAS_SISTEMA

    billeteras = db.execute(
        select(Billetera).where(
            Billetera.usuario_id == usuario.id,
            Billetera.estado == EstadoBilletera.ACTIVA
        )
    ).scalars().all()

    # 1. Categorías y subcategorías globales desde cache en memoria
    cats_globales, subs_globales = categoria_service.obtener_categorias_globales(db)

    subcats_por_cat: dict[str, list[str]] = {}
    for s in subs_globales:
        key = str(s["categoria_id"])
        if key not in subcats_por_cat:
            subcats_por_cat[key] = []
        subcats_por_cat[key].append(s["nombre"])

    categorias_lista = [
        {
            "nombre": cg["nombre"],
            "tipo": cg["tipo"].value if hasattr(cg["tipo"], "value") else str(cg["tipo"]),
            "subcategorias": subcats_por_cat.get(str(cg["id"]), [])
        }
        for cg in cats_globales
        if cg["nombre"] not in CATEGORIAS_SISTEMA
    ]

    hoy = hoy_argentina()
    dia_semana_str = _DIAS_SEMANA_ES[hoy.weekday()]
    mes_str = _MESES_ES[hoy.month - 1]
    texto_fecha = f"{dia_semana_str} {hoy.day} de {mes_str} de {hoy.year}"

    fecha_inicio, fecha_fin = get_ciclo_fechas(usuario, hoy)

    metas = db.execute(
        select(Meta).where(
            Meta.usuario_id == usuario.id,
            Meta.estado == EstadoMeta.ACTIVA
        )
    ).scalars().all()

    from sqlalchemy.orm import selectinload
    presupuestos = db.execute(
        select(Presupuesto)
        .options(selectinload(Presupuesto.periodos))
        .where(
            Presupuesto.usuario_id == usuario.id,
            Presupuesto.estado == EstadoPresupuesto.ACTIVO
        )
    ).scalars().all()

    try:
        from app.services.contexto_financiero_service import _calcular_saldo_disponible_sync
        disp_ctx = _calcular_saldo_disponible_sync(db, usuario.id, wallets_override=billeteras)
        saldo_disponible_ars = float(disp_ctx["ars"]["total_billeteras"])
        disponible_real_ars = float(disp_ctx["ars"]["saldo_disponible"])
        saldo_disponible_usd = float(disp_ctx["usd"]["total_billeteras"])
        disponible_real_usd = float(disp_ctx["usd"]["saldo_disponible"])
    except Exception:
        logger.exception("Error al obtener disponible real en ai_service")
        saldo_disponible_ars = 0.0
        disponible_real_ars = 0.0
        saldo_disponible_usd = 0.0
        disponible_real_usd = 0.0

    def _obtener_monto_usado_presupuesto(p: Presupuesto) -> float:
        if getattr(p, "monto_usado_actual", None) is not None:
            return float(p.monto_usado_actual)
        if p.periodos:
            return float(p.periodos[-1].monto_usado)
        return 0.0

    res = {
        "fecha_actual": {
            "iso": hoy.isoformat(),
            "texto": texto_fecha,
        },
        "billeteras": [
            {"id": str(b.id), "nombre": b.nombre, "moneda": b.moneda.value, "saldo": float(b.saldo_actual)}
            for b in billeteras
        ],
        "categorias": categorias_lista,
        "ciclo_actual": {
            "fecha_inicio": fecha_inicio.isoformat(),
            "fecha_fin": fecha_fin.isoformat(),
        },

        "metas_activas": [
            {"nombre": m.nombre, "objetivo": float(m.monto_objetivo), "acumulado": float(m.monto_actual), "moneda": m.moneda.value}
            for m in metas
        ],
        "presupuestos_activos": [
            {
                "nombre": p.nombre,
                "limite": float(p.monto),
                "monto_usado": _obtener_monto_usado_presupuesto(p),
                "monto_disponible": max(0.0, float(p.monto) - _obtener_monto_usado_presupuesto(p)),
                "moneda": p.moneda.value
            }
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

        if settings.ENVIRONMENT == "production":
            logger.info(f"Enviando {len(messages_openai)} mensajes a OpenAI. Longitud último mensaje: {len(messages_openai[-1]['content'])} caracteres")
        else:
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
        if settings.ENVIRONMENT == "production":
            logger.info(f"Respuesta OpenAI recibida: {'OK' if content else 'VACÍA'} (longitud: {len(content) if content else 0} caracteres)")
        else:
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
