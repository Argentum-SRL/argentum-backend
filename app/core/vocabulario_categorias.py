# -*- coding: utf-8 -*-
"""
app/core/vocabulario_categorias.py - Rescate determinístico por vocabulario.
Decisiones 10.c y 11.
"""
from __future__ import annotations

import re
from typing import Literal

from app.utils.texto import normalizar_texto

TipoTransaccionStr = Literal["egreso", "ingreso"]

# Mapeo de subcategoría destino -> Categoría padre
PADRES_DESTINO: dict[str, str] = {
    # Egreso
    "Panadería": "Alimentación",
    "Carnicería": "Alimentación",
    "Supermercado": "Alimentación",
    "Kiosco": "Alimentación",
    "Verdulería": "Alimentación",
    "Alimentación": "Alimentación",
    "Restaurantes": "Gastronomía",
    "Delivery": "Gastronomía",
    "Cafetería": "Gastronomía",
    "Transporte público": "Transporte",
    "Taxi y apps": "Transporte",
    "Combustible": "Transporte",
    "Estacionamiento": "Transporte",
    "Peajes": "Transporte",
    "Auto": "Transporte",
    "Alquiler": "Vivienda",
    "Expensas": "Vivienda",
    "Luz": "Vivienda",
    "Gas": "Vivienda",
    "Agua": "Vivienda",
    "Limpieza": "Hogar",
    "Muebles y electro": "Hogar",
    "Reparaciones": "Hogar",
    "Servicio doméstico": "Hogar",
    "Prepaga y obra social": "Salud",
    "Médico": "Salud",
    "Farmacia": "Salud",
    "Dentista": "Salud",
    "Terapias": "Salud",
    "Peluquería y belleza": "Cuidado personal",
    "Perfumería": "Cuidado personal",
    "Salidas": "Ocio",
    "Streaming": "Ocio",
    "Hobbies y juegos": "Ocio",
    "Deportes y gimnasio": "Ocio",
    "Viajes": "Ocio",
    "Colegio y facultad": "Educación",
    "Cursos": "Educación",
    "Libros y útiles": "Educación",
    "Ropa": "Indumentaria",
    "Calzado": "Indumentaria",
    "Accesorios": "Indumentaria",
    "Celular": "Tecnología",
    "Internet": "Tecnología",
    "Apps y programas": "Tecnología",
    "Equipos": "Tecnología",
    "ABL e inmobiliario": "Impuestos",
    "Patente": "Impuestos",
    "Monotributo": "Impuestos",
    "Comisiones": "Banco",
    "Préstamos": "Banco",
    "Intereses": "Banco",
    "Seguros": "Banco",
    "Regalos": "Otros",
    "Mascotas": "Otros",
    "Ayuda familiar": "Otros",
    "Pagos a personas": "Otros",
    # Ingreso
    "Sueldo": "Trabajo",
    "Aguinaldo": "Trabajo",
    "Bonos y extras": "Trabajo",
    "Freelance y changas": "Trabajo",
    "Ventas": "Trabajo",
    "Jubilación o pensión": "Jubilación y AUH",
    "AUH y asignaciones": "Jubilación y AUH",
    "Alquileres": "Inversiones",
    "Cobros de personas": "Otros",
}


def _compilar_regex_palabras(palabras: list[str]) -> re.Pattern:
    """Compila un regex que hace match de palabras o frases completas."""
    # Ordenar por longitud descendente para matchear frases más largas primero
    palabras_ord = sorted(palabras, key=len, reverse=True)
    patrones = []
    for p in palabras_ord:
        p_norm = normalizar_texto(p)
        patrones.append(r"\b" + re.escape(p_norm) + r"\b")
    return re.compile("|".join(patrones), flags=re.IGNORECASE)


# Palabras de conceptos para Egreso
RE_PANADERIA = _compilar_regex_palabras([
    "panaderia", "panificadora", "pan", "medialunas", "bizcochos", "budin"
])
RE_CARNICERIA = _compilar_regex_palabras([
    "polleria", "pollo", "granja", "pescaderia", "pescado"
])
RE_SUPERMERCADO = _compilar_regex_palabras([
    "almacen", "el chino", "al chino", "super chino", "mayorista", "despensa", "autoservicio",
    "supermercado", "super", "coto", "carrefour", "dia", "disco", "jumbo", "chango", "vea"
])
RE_ALIMENTACION_VACIA = _compilar_regex_palabras([
    "fiambreria", "dietetica"
])
RE_PELUQUERIA = _compilar_regex_palabras([
    "peluqueria", "pelu", "barberia", "barbero", "corte de pelo", "me corte el pelo",
    "manicura", "manicuria", "depilacion", "estetica"
])
RE_PERFUMERIA = _compilar_regex_palabras([
    "perfume", "perfumeria", "maquillaje", "shampoo", "cosmeticos"
])
RE_STREAMING = _compilar_regex_palabras([
    "netflix", "disney", "hbo", "prime video", "amazon prime", "paramount",
    "crunchyroll", "spotify", "youtube premium", "apple music", "apple tv", "deezer", "tidal"
])
RE_APPS_PROGRAMAS = _compilar_regex_palabras([
    "chatgpt", "chat gpt", "copilot", "icloud", "google one", "microsoft office",
    "office 365", "microsoft 365", "canva", "notion", "dropbox", "adobe", "capcut"
])
RE_EQUIPOS = _compilar_regex_palabras([
    "notebook", "computadora", "compu", "tablet", "auriculares", "televisor",
    "tele", "smart tv", "monitor"
])
RE_ABL = _compilar_regex_palabras([
    "abl", "inmobiliario", "rentas", "tasa municipal"
])
RE_PATENTE = _compilar_regex_palabras([
    "patente"
])
RE_MONOTRIBUTO = _compilar_regex_palabras([
    "monotributo", "autonomos"
])
RE_SERVICIO_DOMESTICO = _compilar_regex_palabras([
    "ninera", "mucama", "empleada domestica", "servicio domestico",
    "senora que limpia", "chica que limpia"
])
RE_KIOSCO = _compilar_regex_palabras([
    "golosina", "golosinas", "caramelo", "caramelos", "alfajor", "alfajores",
    "chocolate", "chocolates", "chicle", "chicles", "kiosco", "maxikiosco",
    "puchos", "cigarrillos"
])
RE_VERDULERIA = _compilar_regex_palabras([
    "verduleria", "fruteria", "verdura", "verduras", "fruta", "frutas"
])
RE_COMBUSTIBLE = _compilar_regex_palabras([
    "nafta", "combustible", "gnc", "gasoil", "ypf", "shell", "axion"
])
RE_TRANSPORTE_PUBLICO = _compilar_regex_palabras([
    "bondi", "colectivo", "subte", "tren", "transporte publico"
])
RE_PREPAGA = _compilar_regex_palabras([
    "prepaga", "obra social", "osde", "swiss medical", "galeno", "omint", "medife"
])

# Palabras de conceptos para Ingreso
RE_JUBILACION = _compilar_regex_palabras([
    "jubilacion", "pension", "jubilado"
])
RE_AUH = _compilar_regex_palabras([
    "auh", "asignacion", "asignaciones", "salario familiar"
])
RE_BONOS_EXTRAS = _compilar_regex_palabras([
    "propina", "propinas"
])
RE_SUELDO = _compilar_regex_palabras([
    "sueldo", "salario", "haberes", "quincena"
])

# Familia y Verbos
RE_FAMILIA = _compilar_regex_palabras([
    "mama", "papa", "mi vieja", "mi viejo", "mis viejos", "abuela", "abuelo",
    "mi hijo", "mi hija", "hijo", "hija"
])

RE_VERBO_PLATA_EGRESO = _compilar_regex_palabras([
    "le transferi", "le pase", "le mande", "le di", "le deposite", "transferencia a"
])

RE_VERBO_PLATA_INGRESO = _compilar_regex_palabras([
    "me transfirio", "me paso", "me mando", "me deposito", "transferencia de", "ingreso de"
])


def _es_reemplazable(categoria_ia: str | None, categoria_destino: str) -> bool:
    """
    Solo cambia la categoría si la IA devolvió "Otros" (o vacía) o la categoría padre del destino.
    Nunca pisa una subcategoría que eligió la IA.
    """
    if not categoria_ia or categoria_ia.strip() == "" or categoria_ia in ("Otros", "otros"):
        return True
    padre = PADRES_DESTINO.get(categoria_destino)
    if padre and categoria_ia.strip().lower() == padre.lower():
        return True
    return False


def rescatar_categoria(
    texto: str | None,
    tipo: TipoTransaccionStr = "egreso",
    categoria_ia: str | None = None,
) -> str | None:
    """
    Rescate determinístico por vocabulario de categoría / subcategoría.
    
    Aplica:
    1. Palabras de concepto específicas.
    2. Familia + verbo de plata entre personas -> Ayuda familiar.
    3. Verbo solo -> Pagos a personas (egreso) o Cobros de personas (ingreso).
    
    Retorna la categoría rescatada o categoria_ia si no hubo rescate.
    """
    cat_original = categoria_ia
    if not texto:
        return cat_original

    t_norm = normalizar_texto(texto)
    if not t_norm:
        return cat_original

    # 1. Conceptos específicos
    if tipo == "egreso":
        # Orden de conceptos según Decisión 11
        conceptos_egreso = [
            (RE_PANADERIA, "Panadería"),
            (RE_CARNICERIA, "Carnicería"),
            (RE_SUPERMERCADO, "Supermercado"),
            (RE_ALIMENTACION_VACIA, "Alimentación"),
            (RE_PELUQUERIA, "Peluquería y belleza"),
            (RE_PERFUMERIA, "Perfumería"),
            (RE_STREAMING, "Streaming"),
            (RE_APPS_PROGRAMAS, "Apps y programas"),
            (RE_EQUIPOS, "Equipos"),
            (RE_ABL, "ABL e inmobiliario"),
            (RE_PATENTE, "Patente"),
            (RE_MONOTRIBUTO, "Monotributo"),
            (RE_SERVICIO_DOMESTICO, "Servicio doméstico"),
            # Expresiones preservadas de ai_service
            (RE_KIOSCO, "Kiosco"),
            (RE_VERDULERIA, "Verdulería"),
            (RE_COMBUSTIBLE, "Combustible"),
            (RE_TRANSPORTE_PUBLICO, "Transporte público"),
            (RE_PREPAGA, "Prepaga y obra social"),
        ]
        for regex, destino in conceptos_egreso:
            if regex.search(t_norm):
                if _es_reemplazable(categoria_ia, destino):
                    return destino
                return cat_original

    elif tipo == "ingreso":
        conceptos_ingreso = [
            (RE_JUBILACION, "Jubilación o pensión"),
            (RE_AUH, "AUH y asignaciones"),
            (RE_BONOS_EXTRAS, "Bonos y extras"),
            (RE_SUELDO, "Sueldo"),
        ]
        for regex, destino in conceptos_ingreso:
            if regex.search(t_norm):
                if _es_reemplazable(categoria_ia, destino):
                    return destino
                return cat_original

    # 2. Familia + verbo de plata -> Ayuda familiar (egreso o ingreso)
    tiene_familia = bool(RE_FAMILIA.search(t_norm))
    if tiene_familia:
        if tipo == "egreso":
            if RE_VERBO_PLATA_EGRESO.search(t_norm):
                if _es_reemplazable(categoria_ia, "Ayuda familiar"):
                    return "Ayuda familiar"
                return cat_original
        elif tipo == "ingreso":
            if RE_VERBO_PLATA_INGRESO.search(t_norm):
                if _es_reemplazable(categoria_ia, "Ayuda familiar"):
                    return "Ayuda familiar"
                return cat_original

    # 3. Verbo solo -> Pagos a personas (egreso) / Cobros de personas (ingreso)
    if tipo == "egreso":
        if RE_VERBO_PLATA_EGRESO.search(t_norm):
            if _es_reemplazable(categoria_ia, "Pagos a personas"):
                return "Pagos a personas"
            return cat_original
    elif tipo == "ingreso":
        if RE_VERBO_PLATA_INGRESO.search(t_norm):
            if _es_reemplazable(categoria_ia, "Cobros de personas"):
                return "Cobros de personas"
            return cat_original

    return cat_original


def aplicar_rescate_a_entidades(entidades: dict, mensaje_completo: str) -> None:
    """
    Aplica el rescate por vocabulario a la operación principal y a cada
    transacción adicional de acuerdo con las reglas de texto:
    - Una sola operación: mensaje + descripción.
    - Transacciones adicionales: solo la descripción de cada una.
    """
    if not isinstance(entidades, dict):
        return

    txs_adicionales = entidades.get("transacciones_adicionales")
    hay_adicionales = isinstance(txs_adicionales, list) and len(txs_adicionales) > 0

    desc_principal = entidades.get("descripcion") or ""
    tipo_principal = entidades.get("tipo") or "egreso"
    cat_principal = entidades.get("categoria")

    if hay_adicionales:
        texto_eval_principal = desc_principal
    else:
        texto_eval_principal = f"{mensaje_completo} {desc_principal}".strip()

    cat_rescatada = rescatar_categoria(
        texto=texto_eval_principal,
        tipo=tipo_principal,
        categoria_ia=cat_principal,
    )
    if cat_rescatada != cat_principal:
        entidades["categoria"] = cat_rescatada

    if hay_adicionales:
        for tx in txs_adicionales:
            if isinstance(tx, dict):
                desc_tx = tx.get("descripcion") or ""
                tipo_tx = tx.get("tipo") or "egreso"
                cat_tx = tx.get("categoria")
                cat_tx_res = rescatar_categoria(
                    texto=desc_tx,
                    tipo=tipo_tx,
                    categoria_ia=cat_tx,
                )
                if cat_tx_res != cat_tx:
                    tx["categoria"] = cat_tx_res


INTENTS_CON_RESCATE = ("registrar_transaccion",)


def aplicar_rescate_si_corresponde(resultado_ia: dict | None, mensaje_completo: str) -> None:
    """
    Aplica el rescate por vocabulario si resultado_ia es dict, su intent
    está en INTENTS_CON_RESCATE y cuenta con entidades en formato dict.
    """
    if not isinstance(resultado_ia, dict):
        return
    if resultado_ia.get("intent") not in INTENTS_CON_RESCATE:
        return
    entidades = resultado_ia.get("entidades")
    if not isinstance(entidades, dict):
        return
    aplicar_rescate_a_entidades(entidades, mensaje_completo)
