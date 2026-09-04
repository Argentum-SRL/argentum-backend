"""
app/services/ai_service.py — Servicio central de IA para Argentum.
Único módulo autorizado para llamar a OpenAI. No importar openai en ningún otro archivo.
"""
import json
import logging
import re
import time
from datetime import date
from decimal import Decimal
from typing import Any
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


def extraer_concepto_mensaje(mensaje: str | None, tipo: str = "egreso") -> str:
    """Extrae el concepto limpio del mensaje del usuario cuando no hay descripción provista."""
    if not mensaje:
        return "Ingreso" if tipo == "ingreso" else "Gasto"

    texto = mensaje.strip()
    # Quitar signos monetarios y números
    texto = re.sub(r"[\$€£]\s*[\d\.,]+", "", texto)
    texto = re.sub(r"\b\d+[\.,]?\d*\b", "", texto)
    texto = re.sub(r"\b(k|lucas?|palos?)\b", "", texto, flags=re.IGNORECASE)

    # Quitar menciones de billeteras y métodos de pago
    billeteras_regex = (
        r"\b(mercado\s*pago|mercadopago|mp|merca|galicia|gali|santander|rio|"
        r"bbva|frances|lemon|ual[aá]|efectivo|cash|brubank|bru|tarjeta|d[eé]bito|cr[eé]dito)\b"
    )
    texto = re.sub(billeteras_regex, "", texto, flags=re.IGNORECASE)

    # Quitar frases y verbos iniciales de gasto / ingreso
    verbos_regex = (
        r"^\s*(gast[eé]|pagu[eé]|compr[eé]|anot[aá]|anotar|met[ií]|pus[eé]|"
        r"transfer[ií]|cobr[eé]|ingres[eé]|se\s+me\s+fue|me\s+entraron|me\s+entr[oó]|me\s+depositaron)\b"
    )
    texto = re.sub(verbos_regex, "", texto, flags=re.IGNORECASE)

    # Quitar conectores iniciales comunes
    conectores_regex = r"^\s*(en|de|con|el|la|los|las|un|una|unos|unas|por|para|desde|a)\s+"
    while re.search(conectores_regex, texto, flags=re.IGNORECASE):
        texto = re.sub(conectores_regex, "", texto, flags=re.IGNORECASE)

    # Limpiar signos de puntuación sobrantes
    texto = re.sub(r"[^\w\s\.,áéíóúñÁÉÍÓÚÑ-]", "", texto).strip(" ,.-")
    texto = re.sub(r"\s+", " ", texto).strip()

    if len(texto) >= 2:
        return texto[:60].strip().capitalize()
    return "Ingreso" if tipo == "ingreso" else "Gasto"


def sanitizar_descripcion(desc: str | None, mensaje_original: str | None = None, tipo: str = "egreso") -> str:
    """Sanitiza la descripción limitando longitud y evitando nombres genéricos de categoría."""
    if desc and isinstance(desc, str):
        limpio = desc.strip(" \"'.,")
        # Quitar montos tipo $5000 o 5000 que se hayan colado
        limpio = re.sub(r"[\$€£]\s*[\d\.,]+", "", limpio)
        limpio = re.sub(r"\b\d+[\.,]?\d*\b", "", limpio)
        limpio = re.sub(r"\s+", " ", limpio).strip(" ,.-")
        limpio = re.sub(r"^(?:el|la|los|las|un|una|unos|unas)\s+", "", limpio, flags=re.IGNORECASE).strip()
        if len(limpio) >= 2:
            return limpio[:60].strip().capitalize()

    if mensaje_original:
        return extraer_concepto_mensaje(mensaje_original, tipo=tipo)

    return ""


SYSTEM_PROMPT = """Sos el asistente financiero de Argentum, una app de finanzas personales para Argentina.

PERSONALIDAD:
- Hablás en español rioplatense, directo y conciso
- Nunca usás "¡Excelente!", "¡Genial!", "¡Perfecto!" ni ninguna exclamación entusiasta
- Sos operacional: confirmás acciones, das contexto útil, nada más
- Si falta info, preguntás UNA SOLA COSA por vez
- Nunca usás frases corporativas ni neutras — siempre rioplatense

TRATO Y GÉNERO:
- En el contexto financiero contás con "usuario.sexo" ("femenino", "masculino", "no_binario", "prefiero_no_decir" o null) y "usuario.nombre".
- Si el sexo es "femenino", tratá a la usuaria con concordancia gramatical femenina cuando corresponda (ej: "¡Bienvenida!", "lista", etc.).
- Si el sexo es "masculino", tratá al usuario con concordancia gramatical masculina cuando corresponda (ej: "¡Bienvenido!", "listo", etc.).
- Si es "no_binario", "prefiero_no_decir" o no está definido, utilizá expresiones neutras (ej: "¡Te damos la bienvenida!", "¿Todo listo?", o redacción directa sin flexión).
- Mantené siempre el tono rioplatense, conciso y natural.

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
- 1 luca = 1.000, lucas = miles
- 1 palo = 1.000.000, palos = millones
- mp / mercadopago / merca = Mercado Pago
- bru = Brubank
- gali / galicia = Banco Galicia
- santander / rio = Santander
- bbva / frances = BBVA
- lemon = Lemon
- uala / ualá = Ualá
- verdes / usd / dólares / dolar = USD
- me entró / me depositaron / cobré = ingreso
- me cobraron / saqué / pagué / gasté / puse / gatillé / garpé = egreso
- cuotas / en X cuotas = compra en cuotas
- efectivo / cash / plata física = Efectivo ARS

MAPEO DE JERGA ARGENTINA A CATEGORÍAS Y SUBCATEGORÍAS REALES (OBLIGATORIO):
Al categorizar, debés mapear la jerga rioplatense al nombre exacto de la categoría o subcategoría más específica del listado (sin prefijo "Categoría > "):
- Kiosco y golosinas -> Kiosco:
  kiosco, maxikiosco, golosinas, caramelos, alfajor, alfajores, chocolates, chicles, galletitas, pastillas, puchos, cigarrillos, tabaco, kiosquero.
- Verdulería -> Verdulería:
  verdulería, verduleria, verduras, verdura, frutas, fruta, papas, tomate, cebolla, palta, lechuga, bananas, manzanas, verdulero.
- Carnicería -> Carnicería:
  carnicería, carniceria, carne, asado, asadito, vacío, vacio, colita de cuadril, entraña, matambre, milanesas, milas, picada (de carne), pollo, granja, achuras, chorizo, choris, mollejas, carnicero.
- Supermercado, almacén, chino -> Supermercado:
  supermercado, súper, super, coto, carrefour, dia, jumbo, changomas, vea, almacén, almacen, chino, súper chino, despensa, fiambrería, fiambreria, fiambre, quesos, embutidos, minimarket, compras del mes.
- Panadería y facturas -> Panadería:
  panadería, panaderia, pan, facturas, medialunas, chipá, panadero.
- Delivery y pedidos de comida -> Delivery:
  delivery, pedidos ya, pedidosya, peya, rappi, pedir comida, pedí pizza, empanadas, sushi, hamburguesa, hamburguesas, lomito, lomo.
- Restaurantes y comer afuera -> Restaurantes:
  restaurante, resto, bodegón, bodegon, pizzería, pizzeria, parrilla, cenar afuera, cena, almuerzo en restaurante, tenedor libre.
- Café y cafeterías -> Cafetería:
  café, cafe, cafecito, cafetería, cafeteria, starbucks, cortado, merienda, desayunar afuera, café con leche, tostadas, cafetín.
- Bar, boliche y birra -> Salidas:
  bar, boliche, birra, birras, cerveza, cervecería, cerveceria, trago, tragos, previas, joda, joda bailable, entradas al cine, cine, recital, teatro, show, fiesta, after, after office.
- Gimnasio y deportes -> Deportes y gimnasio:
  gimnasio, gym, crossfit, pilates, yoga, natación, natacion, fútbol, futbol 5, pádel, padel, tenis, cancha, entrenamiento, cuota del club.
- Streaming, videojuegos y hobbies -> Hobbies y juegos:
  streaming, netflix, spotify, hbo, disney, prime video, crunchyroll, youtube premium, videojuegos, playstation, steam, juegos, hobby.
- Viajes y turismo -> Viajes:
  viaje, pasajes, vuelo, hotel, hostel, excursión, vacaciones, escapada.
- Remis, taxi y apps de viaje -> Taxi / Apps:
  remis, remís, taxi, tacho, uber, cabify, didi, viajecito en app.
- Colectivo, subte, tren y bondi -> Transporte público (NUNCA "Transporte"):
  bondi, bondis, colectivo, colectivos, micro, micros, cole, subte, tren, sube, carga de sube, tarjeta sube, boleto.
- Nafta, combustible y carga -> Combustible:
  nafta, combustible, cargar nafta, nafta súper, infinia, premium, v-power, gasoil, diesel, gnc, tanque lleno, cargar gas, ypf, shell, axion, puma.
- Peaje -> Peajes:
  peaje, peajes, telepase, pase.
- Estacionamiento -> Estacionamiento:
  estacionamiento, cochera, parquímetro, parquimetro, garaje, trapito, cuidar el auto, parking.
- Mantenimiento y seguro del auto -> Mantenimiento y seguro del auto:
  mecánico, mecanico, taller, service, cambio de aceite, cubiertas, gomería, gomeria, repuestos del auto, vtv, seguro del auto.
- Farmacia y remedios -> Farmacia:
  farmacia, remedios, medicamentos, pastillas, aspirinas, ibuprofeno, farmacity, protector solar, curitas, gasas, botiquín.
- Obra social y prepaga -> Obra social / Prepaga (NUNCA "Salud"):
  prepaga, obra social, osde, swiss medical, galeno, sancor salud, ioma, osecac, medifé, cuota médica, cuota de la prepaga.
- Médico y consultas -> Médico / Consulta:
  médico, medico, doctor, consulta médica, pediatra, clínico, especialista, copago.
- Dentista -> Odontología:
  dentista, odontólogo, odontologo, muela, brackets, ortodoncia.
- Peluquería y cuidado personal -> Cuidado personal:
  peluquería, peluqueria, corte de pelo, me corté el pelo, me corte el pelo, corté el pelo, corte el pelo, pelo, peluquero, barbero, barbería, barberia, depilación, depilacion, uñas, manicura, pedicura, spa, perfume, cosméticos, cremas, shampoo, estética, estetica, pelu.
- Mascotas y veterinaria -> Mascotas:
  veterinaria, vet, alimento de perro, alimento de gato, piedras sanitarias, pipeta, vacunas de la mascota, paseador de perros.
- Regalos -> Regalos:
  regalo, obsequio, presente, regalo de cumpleaños.
- Alquiler y expensas -> Alquiler / Expensas:
  alquiler, pago de alquiler, pagar el alquiler, expensas, expensas del depto, administración.
- Luz, gas y agua -> Luz / Gas / Agua:
  luz, edenor, edesur, epec, boleta de luz, metrogas, naturgy, camuzzi, boleta de gas, aysa, aguas cordobesas.
- Impuestos -> Impuestos:
  abl, rentas, municipalidad, arba, agip, monotributo.
- Celular -> Celular:
  celular, abono celular, recarga celular, personal, claro, movistar, pack de datos, línea telefónica.
- Internet y cable -> Internet y cable:
  internet, wifi, cable, fibertel, telecentro, flow, claro fibra, movistar fibra, iplan.
- Ropa e indumentaria -> Ropa:
  ropa, remera, remeras, pantalón, pantalon, jean, jeans, campera, buzo, buzos, camisa, medias, ropa interior, pilcha, pilchas, sweater.
- Calzado y zapatillas -> Calzado:
  calzado, zapatillas, zapas, zapatos, botas, sandalias, ojotas, pantuflas, crocs.
- Útiles y librería -> Materiales y libros:
  útiles, utiles, librería, libreria, fotocopias, cuadernos, hojas, biromes, lapiceras, carpetas, libros escolares.
- Cuotas educativas -> Cuotas:
  cuota del colegio, cuota de la facultad, cuota de la universidad, mensualidad escolar.
- Tarjeta de crédito -> Tarjeta de crédito:
  tarjeta, resumen de la tarjeta, pago de tarjeta, visa, mastercard, amex, resumen mensual, cuotas de la tarjeta.
- Sueldo -> Sueldo (NUNCA "Empleo"):
  sueldo, quincena, cobré el mes, cobré el sueldo, cobré sueldo, salario, recibo de haberes. Toda mención de sueldo o cobrar sueldo SIEMPRE tiene categoria "Sueldo", jamás la categoría padre "Empleo".
- Aguinaldo -> Aguinaldo:
  aguinaldo, sac, medio aguinaldo.
- Bono y horas extras -> Bonos y horas extras:
  bono, premio, gratificación, horas extras, plus, comisión.
- Freelance y changas -> Honorarios:
  changa, changas, laburo particular, trabajo freelance, honorarios, facturé un trabajo, cliente particular.

CONCEPTOS RAROS O DESCONOCIDOS:
Si el usuario menciona un gasto o ingreso con un concepto que no conocés, no existe o no tiene sentido financiero conocido (ej: 'un coso cuántico intergaláctico', 'la mar en coche', 'sarasa', etc.), clasificalo SIEMPRE como "Otros". NUNCA inventes categorías o subcategorías inexistentes.

DESCRIPCIÓN DE LA TRANSACCIÓN:
- El campo 'descripcion' debe ser SIEMPRE lo que dijo el usuario, limpio y en pocas palabras, sin el monto ni la billetera.
- Ejemplos:
  * Si el usuario dice 'gasté 5000 en golosinas' -> descripcion: 'Golosinas'
  * Si dice 'gasté 8000 en la verdulería' -> descripcion: 'Verdulería'
  * Si dice 'cargué 30000 de nafta' -> descripcion: 'Nafta'
  * Si dice 'pagué 12000 de la prepaga' -> descripcion: 'Prepaga'
  * Si dice 'gasté 4000 en el bondi' -> descripcion: 'Bondi'
  * Si dice 'me corté el pelo, 15000' -> descripcion: 'Corte de pelo'
  * Si dice 'almuerzo 4500' -> descripcion: 'Almuerzo'
  * Si dice 'pizza con amigos 8000' -> descripcion: 'Pizza con amigos'
  * Si dice 'cobré 800000 de sueldo' -> categoria: 'Sueldo' (NUNCA 'Empleo'), tipo: 'ingreso', descripcion: 'Sueldo'
- NUNCA pongas el nombre genérico de la categoría si el usuario especificó otra cosa (ej: si dijo 'peluquería', poné 'Peluquería' o 'Corte de pelo', NUNCA 'Otros').
- Si el usuario mandó un monto sin ningún concepto (ej: 'gasté 5000' o 'pagué 10000'), poné 'Gasto' o 'Ingreso' según corresponda, NUNCA el nombre de la categoría.

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
- deshacer
- corregir
- confirmar
- cancelar
- saludo
- desconocido

REGLAS DE CLASIFICACIÓN DE INTENTS:
- "borrá eso", "borralo", "eliminá eso", "me equivoqué", "eso estaba mal", "anulá eso", "cancelá el último" → deshacer
- "eran 3.000 no 30.000", "eso era supermercado", "fue con Galicia", "fue ayer", "no, 5000" (corrección de dato suelto del último movimiento registrado) → corregir
- CRITERIO OPERACIÓN NUEVA VS CORRECCIÓN: Si el mensaje contiene un monto Y un concepto/categoría propios independientes (ej: "gasté 12000 en verdulería", "almuerzo 4500", "cargué 30000 de nafta"), es SIEMPRE "registrar_transaccion". Si solo contiene un dato suelto corrigiendo el valor anterior (ej: "no, 5000", "eran 3000 no 30000", "eso era verdulería", "fue con Galicia", "fue ayer"), es "corregir".
- "puse X", "metí X", "deposité X" SIN contexto claro → slot_filling=true, preguntá "¿Fue un gasto, ingreso o transferencia?"
- "cuánta plata tengo", "cuánto tengo", "mi saldo" → consultar_saldo
- "cómo voy", "cómo estoy este mes" → consultar_balance
- Consultas de gastos o totales por un concepto, comercio o categoría específica (ej: "cuánto gasté en pizza", "cuánto gasté en el super", "cuánto se me fue en salidas") NO están soportadas → intent="desconocido", confianza=1.0, slot_filling=false.
- "llego a fin de mes", "me alcanza", "cuánto me queda" → consultar_proyeccion
- "cancelar", "no importa", "dejá", "olvidalo" → cancelar
- "sí", "dale", "confirmá", "ok", "va" → confirmar

FLUJO DE REGISTRO DE TRANSACCIÓN — MUY IMPORTANTE:
Cuando tenés todos los datos para registrar una transacción (monto + tipo + billetera):
1. NO registres todavía
2. Respondé con un resumen y pedí confirmación. En el mensaje al usuario, mostrá solo el nombre corto: si la categoría es "Farmacia", mostrá "Farmacia". Ejemplo: "Voy a anotar $5.000 en Farmacia desde Mercado Pago. ¿Va?"
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
   - REGLA DE ESPECIFICIDAD ESTRICTA: Debés devolver SIEMPRE la subcategoría más específica (ej: "Kiosco", "Verdulería", "Combustible", "Obra social / Prepaga", "Transporte público", "Cuidado personal", "Farmacia", "Supermercado", "Sueldo"). NUNCA elijas la categoría padre general ("Alimentación", "Salud", "Transporte", "Servicios", "Empleo", etc.) cuando exista una subcategoría aplicable al concepto (por ejemplo, para golosinas/alfajores/kiosco usá SIEMPRE "Kiosco", NUNCA "Alimentación"; para verdulería usá SIEMPRE "Verdulería", NUNCA "Alimentación"; para bondi/colectivo usá SIEMPRE "Transporte público", NUNCA "Transporte"; para sueldo usá SIEMPRE "Sueldo", NUNCA "Empleo"; para prepaga u obra social usá SIEMPRE "Obra social / Prepaga", NUNCA "Salud").
   - NUNCA uses el formato "Categoría > Subcategoría", devolvé únicamente el nombre exacto de la subcategoría o categoría seleccionada (ej: "Verdulería", "Obra social / Prepaga", "Kiosco").
   - Si no hay ninguna subcategoría aplicable pero sí una categoría general que aplique, usá la categoría general.
   - Si el mensaje NO contiene ninguna pista (ej: monto pelado como "gasté 500", "pagué 2000", "me entraron 10000"), o si es un concepto desconocido o raro (ej: "un coso cuántico intergaláctico"), devolvé SIEMPRE: "Otros".
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
  * Si el usuario responde a una pregunta de billetera con un número o texto (ej: "1", "1 (billetera: Mercado Pago)", "mercado pago"), interpretalo como la selección de la billetera que faltaba para completar la transacción previa, NUNCA como un nuevo monto ni como una transacción nueva de $1.
  * Devolvé en el JSON de salida TODAS las entidades acumuladas (monto previo, tipo previo, categoría previa, transacciones_adicionales previas + la nueva billetera resuelta).
  * Si con este dato ya contás con monto, tipo y billetera, establecé intent="registrar_transaccion", confianza >= 0.85, slot_filling=false, y generá la propuesta pidiendo confirmación: "Voy a anotar $X en [Categoría] desde [Billetera]. ¿Va?" (o listando todos los movimientos si hay adicionales).
- CAMBIO DE TEMA O NUEVA OPERACIÓN:
  * Si el mensaje nuevo introduce una transacción independiente con su propio monto y concepto/categoría (por ejemplo: "gasté 12000 en verdulería" cuando había datos previos de kiosco), DESCARTÁ por completo los datos confirmados previos. NO los fusiones ni los agregues como transacciones adicionales. Procesá únicamente la nueva operación.
  * Si el mensaje nuevo es un saludo, una consulta (saldo, balance, proyección) o una cancelación, DESCARTÁ los datos confirmados previos.
  * La fusión o acumulación de datos previos aplica ÚNICAMENTE cuando el nuevo mensaje es una respuesta directa a lo que el sistema preguntó para completar la operación.

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
        "usuario": {
            "nombre": usuario.nombre,
            "sexo": usuario.sexo.value if (usuario.sexo and hasattr(usuario.sexo, "value")) else (usuario.sexo or None),
        },
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


_SCHEMA_CACHE: dict[str, Any] | None = None
_SCHEMA_CACHE_EXPIRY: float = 0.0
_SCHEMA_CACHE_TTL: float = 300.0  # 5 minutos


def invalidar_cache_schema() -> None:
    """Invalida la cache en memoria del esquema JSON estricto."""
    global _SCHEMA_CACHE, _SCHEMA_CACHE_EXPIRY
    _SCHEMA_CACHE = None
    _SCHEMA_CACHE_EXPIRY = 0.0


def obtener_categorias_permitidas(db: Session) -> list[str]:
    """Obtiene la lista deduplicada y ordenada de categorías y subcategorías globales permitidas."""
    from app.core.constants import CATEGORIAS_SISTEMA
    cats_globales, subs_globales = categoria_service.obtener_categorias_globales(db)
    permitidas: set[str] = set()
    for cg in cats_globales:
        nombre = cg.get("nombre")
        if nombre and nombre not in CATEGORIAS_SISTEMA:
            permitidas.add(nombre)
    for sg in subs_globales:
        nombre = sg.get("nombre")
        if nombre:
            permitidas.add(nombre)
    return sorted(list(permitidas))


def _construir_schema_estricto(db: Session) -> dict[str, Any]:
    """
    Construye y cachea en memoria el esquema JSON Schema estricto (strict: true)
    para Structured Outputs de OpenAI, conteniendo el enum cerrado de categorías.
    """
    global _SCHEMA_CACHE, _SCHEMA_CACHE_EXPIRY
    ahora = time.time()
    if _SCHEMA_CACHE is not None and ahora < _SCHEMA_CACHE_EXPIRY:
        return _SCHEMA_CACHE

    categorias_enum: list[str | None] = sorted(obtener_categorias_permitidas(db))
    categorias_enum.append(None)

    intents_enum = [
        "registrar_transaccion",
        "consultar_saldo",
        "consultar_resumen",
        "consultar_presupuesto",
        "consultar_meta",
        "consultar_proyeccion",
        "consultar_deuda",
        "consultar_ahorro",
        "transferir_fondos",
        "deshacer",
        "corregir",
        "cancelar",
        "confirmar",
        "saludo",
        "ayuda",
        "desconocido",
    ]

    schema_dict = {
        "type": "json_schema",
        "json_schema": {
            "name": "whatsapp_response",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": intents_enum,
                    },
                    "entidades": {
                        "type": "object",
                        "properties": {
                            "monto": {"type": ["number", "null"]},
                            "moneda": {"type": ["string", "null"], "enum": ["ARS", "USD", None]},
                            "tipo": {"type": ["string", "null"], "enum": ["egreso", "ingreso", "transferencia", None]},
                            "categoria": {"type": ["string", "null"], "enum": categorias_enum},
                            "descripcion": {"type": ["string", "null"]},
                            "billetera": {"type": ["string", "null"]},
                            "billetera_origen": {"type": ["string", "null"]},
                            "billetera_destino": {"type": ["string", "null"]},
                            "cantidad_cuotas": {"type": ["integer", "null"]},
                            "fecha": {"type": ["string", "null"]},
                            "destinatario": {"type": ["string", "null"]},
                            "persona": {"type": ["string", "null"]},
                            "tasa_cambio": {"type": ["number", "null"]},
                            "confirmado": {"type": ["boolean", "null"]},
                            "transacciones_adicionales": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "monto": {"type": "number"},
                                        "moneda": {"type": ["string", "null"], "enum": ["ARS", "USD", None]},
                                        "tipo": {"type": "string", "enum": ["egreso", "ingreso"]},
                                        "categoria": {"type": ["string", "null"], "enum": categorias_enum},
                                        "descripcion": {"type": ["string", "null"]},
                                        "fecha": {"type": ["string", "null"]},
                                    },
                                    "required": [
                                        "monto",
                                        "moneda",
                                        "tipo",
                                        "categoria",
                                        "descripcion",
                                        "fecha",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": [
                            "monto",
                            "moneda",
                            "tipo",
                            "categoria",
                            "descripcion",
                            "billetera",
                            "billetera_origen",
                            "billetera_destino",
                            "cantidad_cuotas",
                            "fecha",
                            "destinatario",
                            "persona",
                            "tasa_cambio",
                            "confirmado",
                            "transacciones_adicionales",
                        ],
                        "additionalProperties": False,
                    },
                    "confianza": {"type": "number"},
                    "slot_filling": {"type": "boolean"},
                    "datos_faltantes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "respuesta_usuario": {"type": "string"},
                },
                "required": [
                    "intent",
                    "entidades",
                    "confianza",
                    "slot_filling",
                    "datos_faltantes",
                    "respuesta_usuario",
                ],
                "additionalProperties": False,
            },
        },
    }

    _SCHEMA_CACHE = schema_dict
    _SCHEMA_CACHE_EXPIRY = ahora + _SCHEMA_CACHE_TTL
    return _SCHEMA_CACHE


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
                    bot_payload = {
                        "intent": turno.get("intent", "desconocido"),
                        "entidades": turno.get("entidades", {}),
                        "slot_filling": turno.get("slot_filling", False),
                        "datos_faltantes": turno.get("datos_faltantes", []),
                        "respuesta_usuario": turno["bot"],
                    }
                    if turno.get("confianza") is not None:
                        bot_payload["confianza"] = turno["confianza"]
                    bot_json = json.dumps(bot_payload, ensure_ascii=False)
                    messages_openai.append({"role": "assistant", "content": bot_json})

        # Agregar mensaje actual
        messages_openai.append({"role": "user", "content": mensaje})

        if settings.ENVIRONMENT == "production":
            logger.info(f"Enviando {len(messages_openai)} mensajes a OpenAI. Longitud último mensaje: {len(messages_openai[-1]['content'])} caracteres")
        else:
            logger.info(f"Enviando {len(messages_openai)} mensajes a OpenAI. Último mensaje: {messages_openai[-1]['content'][:100]}")

        client = _get_client()
        model_name = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini-2024-07-18")
        schema_format = _construir_schema_estricto(db)

        # 1. Intento primario con Structured Outputs (strict: true)
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages_openai,
                temperature=0.1,
                max_tokens=800,
                response_format=schema_format,
            )
        except Exception as e_strict:
            logger.warning(
                "Fallo en Structured Outputs OpenAI (%s). Reintentando con json_object como fallback.",
                e_strict,
            )
            response = client.chat.completions.create(
                model=model_name,
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

        # Sanitizar descripción preservando lo que el usuario expresó
        if isinstance(parsed.get("entidades"), dict):
            entidades_dict = parsed["entidades"]
            desc_actual = entidades_dict.get("descripcion")
            tipo_ent = entidades_dict.get("tipo") or "egreso"
            if desc_actual or (parsed.get("intent") == "registrar_transaccion" and (entidades_dict.get("monto") is not None or entidades_dict.get("categoria") is not None)):
                entidades_dict["descripcion"] = sanitizar_descripcion(
                    desc_actual,
                    mensaje_original=mensaje,
                    tipo=tipo_ent,
                )

            # Normalizar subcategorías específicas si el modelo devolvió categoría padre general
            cat_actual = entidades_dict.get("categoria")
            texto_eval = f"{mensaje} {desc_actual or ''}".lower()
            if cat_actual == "Alimentación":
                if re.search(r"\b(golosinas?|caramelos?|alfajor(?:es)?|chocolates?|chicles?|kiosco|maxikiosco|puchos|cigarrillos)\b", texto_eval):
                    entidades_dict["categoria"] = "Kiosco"
                elif re.search(r"\b(verduler[ií]a|fruter[ií]a|verduras?|frutas?)\b", texto_eval):
                    entidades_dict["categoria"] = "Verdulería"
                elif re.search(r"\b(supermercado|s[uú]per|coto|carrefour|dia|disco|jumbo|chango|vea)\b", texto_eval):
                    entidades_dict["categoria"] = "Supermercado"
            elif cat_actual == "Transporte":
                if re.search(r"\b(nafta|combustible|gnc|gasoil|ypf|shell|axion)\b", texto_eval):
                    entidades_dict["categoria"] = "Combustible"
                elif re.search(r"\b(bondi|colectivo|subte|tren|transporte\s+p[uú]blico)\b", texto_eval):
                    entidades_dict["categoria"] = "Transporte público"
            elif cat_actual == "Salud":
                if re.search(r"\b(prepaga|obra\s+social|osde|swiss\s+medical|galeno|omint|medif[eé])\b", texto_eval):
                    entidades_dict["categoria"] = "Obra social / Prepaga"

            # Sanitizar descripciones en transacciones adicionales si existen
            if isinstance(entidades_dict.get("transacciones_adicionales"), list):
                for tx_ad in entidades_dict["transacciones_adicionales"]:
                    if isinstance(tx_ad, dict) and tx_ad.get("descripcion"):
                        tx_ad["descripcion"] = sanitizar_descripcion(
                            tx_ad["descripcion"],
                            tipo=tx_ad.get("tipo") or "egreso",
                        )

        logger.info(f"Mensaje procesado con éxito. Intent: {parsed['intent']}, Confianza: {parsed['confianza']}")
        return parsed

    except json.JSONDecodeError as jde:
        logger.error(f"Error al decodificar JSON de OpenAI: {str(jde)}")
        return fallback_res
    except Exception as e:
        logger.exception(f"Excepción al procesar mensaje con OpenAI: {str(e)}")
        return fallback_res
