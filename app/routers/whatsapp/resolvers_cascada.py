"""
Resolvers en cascada y validadores de entidades para el flujo de WhatsApp IA.
Maneja la resolución de billeteras, tarjetas de crédito, menús interactivos y validación de ítems.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.usuario import Moneda
from app.routers.whatsapp.parsers import _nombre_corto_categoria
from app.services import ai_service
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto

ALIAS_BILLETERAS = {
    "mp": "mercado pago",
    "merca": "mercado pago",
    "mercadopago": "mercado pago",
    "bru": "brubank",
    "gali": "galicia",
    "santander": "santander",
    "rio": "santander",
    "bbva": "bbva",
    "frances": "bbva",
    "lemon": "lemon",
    "uala": "ualá",
    "efectivo": "efectivo",
    "cash": "efectivo",
}

ALIAS_REDES_ARGENTINAS = {
    "visa": ["visa", "la visa", "tarjeta visa", "visita"],
    "mastercard": ["master", "mastercard", "la master", "la mastercard", "master card"],
    "amex": ["amex", "american express", "american", "la amex", "la american"],
    "naranja": ["naranja", "tarjeta naranja", "la naranja", "naranja x"],
    "cabal": ["cabal", "la cabal"],
}

ALIAS_BANCOS_ARGENTINOS = {
    "galicia": ["gali", "banco galicia"],
    "santander": ["rio", "banco santander", "santander rio"],
    "bbva": ["frances", "banco frances", "bbva frances"],
    "macro": ["banco macro"],
    "nacion": ["banco nacion"],
    "provincia": ["bapro", "banco provincia"],
    "ciudad": ["banco ciudad"],
    "brubank": ["bru"],
}

FORMAS_GENERICAS_TARJETA = [
    "la tarjeta", "la tarje", "la de credito", "la de crédito",
    "la credi", "tarjeta", "tarje", "de credito", "de crédito",
    "credi", "credito", "crédito", "con tarjeta", "con la tarjeta",
    "con credito", "con crédito"
]

def resolver_billetera_cascada(
    nombre: str | None,
    billeteras: list[Billetera],
) -> tuple[Billetera | None, list[Billetera]]:
    """
    Resuelve determinísticamente una billetera por nombre aplicando cascada estricta (3.1, 3.2, 3.3):
    1. Coincidencia exacta normalizada.
    2. Coincidencia por alias argentinos.
    3. Coincidencia por contención (substring bidireccional) SOLO si produce exactamente una candidata (mínimo 3 caracteres).

    Retorna:
    - (billetera, [billetera]) si se resolvió de forma unívoca.
    - (None, [candidatas]) si hay 2 o más candidatas ambiguas.
    - (None, []) si no hubo coincidencias o el nombre es vacío.
    """
    if not nombre or not billeteras:
        return None, []

    nombre_norm = normalizar_texto(nombre)
    if not nombre_norm:
        return None, []

    # 1. Coincidencia exacta normalizada
    exactas = [b for b in billeteras if normalizar_texto(b.nombre) == nombre_norm]
    if len(exactas) == 1:
        return exactas[0], exactas
    elif len(exactas) > 1:
        return None, exactas

    # 2. Coincidencia por alias argentinos
    alias_target = ALIAS_BILLETERAS.get(nombre_norm)
    if alias_target:
        alias_target_norm = normalizar_texto(alias_target)
        coincidencias_alias = [
            b for b in billeteras if normalizar_texto(b.nombre) == alias_target_norm
        ]
        if not coincidencias_alias:
            coincidencias_alias = [
                b for b in billeteras if alias_target_norm in normalizar_texto(b.nombre)
            ]
        if len(coincidencias_alias) == 1:
            return coincidencias_alias[0], coincidencias_alias
        elif len(coincidencias_alias) > 1:
            return None, coincidencias_alias

    # 3. Coincidencia por contención SOLO si produce una única candidata (mínimo 3 caracteres)
    if len(nombre_norm) >= 3:
        candidatas_contencion = [
            b for b in billeteras
            if nombre_norm in normalizar_texto(b.nombre) or normalizar_texto(b.nombre) in nombre_norm
        ]
        if len(candidatas_contencion) == 1:
            return candidatas_contencion[0], candidatas_contencion
        elif len(candidatas_contencion) > 1:
            return None, candidatas_contencion

    return None, []

def construir_alias_tarjeta(t: TarjetaCredito) -> set[str]:
    """Construye el conjunto de alias reconocibles para una tarjeta de crédito (Tareas 2.1, 2.2, 2.3)."""
    if hasattr(t, "_alias_cache") and t._alias_cache is not None:
        return t._alias_cache

    alias = set()

    # 1. Apodo si lo tiene
    if getattr(t, "apodo", None):
        ap_norm = normalizar_texto(t.apodo)
        if ap_norm:
            alias.add(ap_norm)
            alias.add(f"la {ap_norm}")
            alias.add(f"la de {ap_norm}")

    # 2. Nombre visible y últimos 4 dígitos
    nom_norm = normalizar_texto(t.nombre)
    if nom_norm:
        alias.add(nom_norm)
    digitos = "".join(re.findall(r"\d+", t.nombre))
    if len(digitos) >= 4:
        ult4 = digitos[-4:]
        alias.add(ult4)
        alias.add(f"terminada en {ult4}")
        alias.add(f"finalizada en {ult4}")
        alias.add(f"la {ult4}")

    # 3. Red y modismos argentinos
    red_val = t.red.value.lower() if hasattr(t.red, "value") else str(t.red).lower()
    red_aliases = ALIAS_REDES_ARGENTINAS.get(red_val, [red_val])
    for r in red_aliases:
        alias.add(r)
        if len(digitos) >= 4:
            alias.add(f"{r} {digitos[-4:]}")

    # 4. Banco de la billetera vinculada y combinaciones de red + banco
    banco_norm = normalizar_texto(t.billetera.nombre) if t.billetera else ""
    if banco_norm:
        banco_variantes = [banco_norm] + ALIAS_BANCOS_ARGENTINOS.get(banco_norm, [])
        for b_var in banco_variantes:
            alias.add(f"del {b_var}")
            alias.add(f"de {b_var}")
            alias.add(f"la del {b_var}")
            alias.add(f"la de {b_var}")
            alias.add(f"tarjeta {b_var}")
            alias.add(f"la tarjeta del {b_var}")
            alias.add(f"la tarjeta de {b_var}")
            alias.add(f"la de credito del {b_var}")
            alias.add(f"la de credito de {b_var}")
            for r in red_aliases:
                alias.add(f"{r} {b_var}")
                alias.add(f"{b_var} {r}")
                alias.add(f"{r} del {b_var}")
                alias.add(f"{r} de {b_var}")
                alias.add(f"la {r} del {b_var}")
                alias.add(f"la {r} de {b_var}")

    # 5. Formas genéricas
    for g in FORMAS_GENERICAS_TARJETA:
        alias.add(normalizar_texto(g))

    t._alias_cache = alias
    return alias

def resolver_tarjeta_cascada(
    nombre: str | None,
    tarjetas: list[TarjetaCredito],
) -> tuple[TarjetaCredito | None, list[TarjetaCredito]]:
    """
    Resuelve determinísticamente una tarjeta aplicando cascada igual a la de billeteras (Tareas 2.4 y 2.5):
    1. Coincidencia exacta normalizada (por nombre, apodo o últimos 4 dígitos).
    2. Coincidencia por alias reconocibles.
    3. Coincidencia por contención SOLO si produce exactamente una candidata (mínimo 3 caracteres).
    """
    if not nombre or not tarjetas:
        return None, []

    nombre_norm = normalizar_texto(nombre)
    if not nombre_norm:
        return None, []

    # 1. Coincidencia exacta normalizada
    exactas = []
    for t in tarjetas:
        t_nom = normalizar_texto(t.nombre)
        t_apo = normalizar_texto(t.apodo) if getattr(t, "apodo", None) else None
        digitos = "".join(re.findall(r"\d+", t.nombre))
        if nombre_norm == t_nom or (t_apo and nombre_norm == t_apo) or (len(digitos) >= 4 and nombre_norm == digitos[-4:]):
            exactas.append(t)
    if len(exactas) == 1:
        return exactas[0], exactas
    elif len(exactas) > 1:
        return None, exactas

    # 2. Coincidencia por alias
    coincidentes_alias = []
    for t in tarjetas:
        aliases = construir_alias_tarjeta(t)
        if nombre_norm in aliases:
            coincidentes_alias.append(t)
    if len(coincidentes_alias) == 1:
        return coincidentes_alias[0], coincidentes_alias
    elif len(coincidentes_alias) > 1:
        return None, coincidentes_alias

    # 3. Contención SOLO si produce una única candidata (mínimo 3 caracteres)
    if len(nombre_norm) >= 3:
        genericas_norm = {normalizar_texto(g) for g in FORMAS_GENERICAS_TARJETA}
        cands_cont = []
        for t in tarjetas:
            aliases = construir_alias_tarjeta(t)
            alias_especificos = [a for a in aliases if a not in genericas_norm]
            if any(nombre_norm in a or (len(a) >= 4 and a in nombre_norm) for a in alias_especificos):
                cands_cont.append(t)
        if len(cands_cont) == 1:
            return cands_cont[0], cands_cont
        elif len(cands_cont) > 1:
            return None, cands_cont

    return None, []

def _merge_entidades(
    estado_previo: dict | None,
    entidades_nuevas: dict | None,
    intent_nuevo: str | None = None,
) -> dict:
    """
    Fusiona entidades únicamente si el mensaje nuevo es una respuesta a lo que el sistema preguntó
    (slot filling), NUNCA si el mensaje nuevo representa una operación nueva o cambio de tema.

    Reglas de fusión:
    1. Si no hay estado previo o no hay entidades nuevas, no hay fusión.
    2. Si el intent detectado es un cambio de tema explícito (saludo, cancelación, consultas, etc.),
       se descarta el estado previo y se devuelven las entidades nuevas.
    3. Si el mensaje nuevo trae monto y concepto/categoría propios y difiere del monto/categoría previos,
       es una nueva operación: se descarta el estado previo por completo.
    4. La fusión solo tiene sentido cuando se completan datos faltantes solicitados por el sistema
       (datos_faltantes) o se resuelve la billetera requerida.
    """
    if not estado_previo:
        return entidades_nuevas or {}
    if not entidades_nuevas:
        return dict(estado_previo)

    if intent_nuevo in (
        "saludo",
        "cancelar",
        "consultar_saldo",
        "consultar_balance",
        "consultar_proyeccion",
        "consultar_cotizacion",
        "desconocido",
    ):
        return dict(entidades_nuevas)

    monto_nuevo = entidades_nuevas.get("monto")
    cat_nueva = entidades_nuevas.get("categoria")
    desc_nueva = entidades_nuevas.get("descripcion")

    monto_prev = estado_previo.get("monto")
    cat_prev = estado_previo.get("categoria")

    # Si el mensaje nuevo trae monto y categoría/descripción propios, es una operación independiente
    # (incluso si tiene el mismo monto y categoría que la anterior, ej: gasto repetido).
    # Solo se fusiona si el sistema estaba esperando explícitamente que el usuario completara el monto.
    if monto_nuevo is not None and (cat_nueva is not None or desc_nueva is not None):
        if "monto" not in estado_previo.get("datos_faltantes", []):
            return dict(entidades_nuevas)

    datos_faltantes = estado_previo.get("datos_faltantes", [])
    merged = dict(estado_previo)
    # Nunca arrastrar transacciones adicionales de un estado previo
    merged.pop("transacciones_adicionales", None)

    for k, v in entidades_nuevas.items():
        if k == "datos_faltantes":
            continue
        if v is not None:
            # Solo fusionar si era un campo pendiente o si es resolución de billetera
            if k in datos_faltantes or (k in ("billetera_origen", "billetera_destino") and any("billetera" in d for d in datos_faltantes)):
                merged[k] = v
            elif k == "transacciones_adicionales":
                if v:
                    merged[k] = v
            elif estado_previo.get(k) is None:
                merged[k] = v

    return merged

def _generar_menu_billeteras(billeteras: list[Billetera], tipo: str = "egreso", encabezado: str | None = None) -> str:
    """
    Genera el menú de selección de billeteras sin mostrar saldos (4.1).
    Pregunta según el tipo: egreso (de cuál salió) o ingreso (a cuál entró) (4.2).
    Si hay más de 8 opciones, muestra las primeras 8 y avisa que puede escribir el nombre (4.4).
    """
    if encabezado is None:
        if tipo == "ingreso":
            encabezado = "¿A qué billetera entró la plata?"
        else:
            encabezado = "¿Desde qué billetera salió la plata?"

    total = len(billeteras)
    limite = 8
    mostradas = billeteras[:limite]

    lineas = [f"{encabezado}\n"]
    for i, b in enumerate(mostradas, 1):
        lineas.append(f"{i}. {b.nombre}")

    if total > limite:
        lineas.append("\nPodés responder con el número o escribir el nombre de la billetera.")

    return "\n".join(lineas)

def _generar_menu_tarjetas(tarjetas: list[TarjetaCredito]) -> str:
    """Genera menú de selección de tarjetas de crédito sin datos sensibles ni saldos."""
    lineas = ["¿Con qué tarjeta de crédito fue?"]
    for idx, t in enumerate(tarjetas[:8], 1):
        nom_b = t.billetera.nombre if t.billetera else ""
        red_disp = t.red.value.capitalize() if hasattr(t.red, "value") else str(t.red).capitalize()
        apo_disp = f' "{t.apodo}"' if getattr(t, "apodo", None) else ""
        if nom_b:
            lineas.append(f"{idx}. {t.nombre}{apo_disp} ({red_disp} - {nom_b})")
        else:
            lineas.append(f"{idx}. {t.nombre}{apo_disp} ({red_disp})")
    return "\n".join(lineas)

def _validar_item_movimiento(
    datos: dict,
    billetera_nombre: str | None = None,
    billetera_moneda: Moneda | None = None,
    billeteras_activas_usuario: list[Billetera] | None = None,
) -> tuple[dict | None, str | None]:
    """
    Valida un movimiento antes de incluirlo en la propuesta o registrarlo:
    - Monto presente y numérico
    - Monto > 0 y <= 1.000.000.000.000
    - Coincidencia de moneda con la billetera o existencia de billetera en esa moneda
    Retorna (item_limpio, motivo_descarte).
    """
    desc = ai_service.sanitizar_descripcion(datos.get("descripcion"), tipo=datos.get("tipo", "egreso")) or _nombre_corto_categoria(datos.get("categoria")) or "un movimiento"
    monto_raw = datos.get("monto")
    if monto_raw is None:
        return None, f"No se pudo registrar {desc} porque no tiene un monto válido."
    try:
        monto_decimal = Decimal(str(monto_raw))
    except Exception:
        return None, f"No se pudo registrar {desc} porque el monto no es válido."

    if monto_decimal <= Decimal("0"):
        return None, f"No se pudo registrar {desc} porque el monto debe ser mayor a cero."
    if monto_decimal > MAX_MONTO_INTEGRIDAD:
        return None, f"No se pudo registrar {desc} porque el monto supera el límite permitido."

    moneda_solicitada_str = datos.get("moneda")
    if moneda_solicitada_str:
        moneda_solicitada = Moneda.USD if moneda_solicitada_str == "USD" else Moneda.ARS
    else:
        moneda_solicitada = Moneda.USD if (billetera_nombre and "usd" in billetera_nombre.lower()) else Moneda.ARS

    if billetera_moneda and moneda_solicitada != billetera_moneda:
        moneda_sol_str = "dólares" if moneda_solicitada == Moneda.USD else "pesos"
        moneda_bill_str = "dólares" if billetera_moneda == Moneda.USD else "pesos"
        monto_fmt = formatear_monto(monto_decimal, moneda_solicitada)
        nom_b = billetera_nombre or "seleccionada"
        motivo = (
            f"No se pudo registrar {desc} de {monto_fmt} porque es en {moneda_sol_str} "
            f"y la billetera {nom_b} es en {moneda_bill_str}."
        )
        return None, motivo

    if billeteras_activas_usuario is not None:
        tiene_moneda = any(b.moneda == moneda_solicitada for b in billeteras_activas_usuario)
        if not tiene_moneda:
            moneda_sol_str = "dólares" if moneda_solicitada == Moneda.USD else "pesos"
            monto_fmt = formatear_monto(monto_decimal, moneda_solicitada)
            motivo = f"No se pudo registrar {desc} de {monto_fmt} porque es en {moneda_sol_str} y no tenés ninguna billetera en {moneda_sol_str}."
            return None, motivo

    item_valido = dict(datos)
    item_valido["monto"] = float(monto_decimal)
    item_valido["moneda"] = moneda_solicitada.value
    return item_valido, None

def _entidades_completas(entidades: dict | None) -> bool:
    """
    Evalúa si un diccionario de entidades contiene todos los datos requeridos
    para registrar la transacción directamente sin necesidad de recurrir a conv_previa.
    """
    if not entidades or not isinstance(entidades, dict):
        return False
    monto = entidades.get("monto")
    if monto is None:
        return False
    try:
        if Decimal(str(monto)) <= 0:
            return False
    except Exception:
        return False

    tiene_billetera = bool(
        entidades.get("billetera")
        or entidades.get("billetera_origen")
        or entidades.get("billetera_destino")
        or entidades.get("tarjeta_id")
        or entidades.get("tarjeta")
    )
    if not tiene_billetera:
        return False

    datos_faltantes = entidades.get("datos_faltantes") or []
    for df in datos_faltantes:
        if df in ("billetera_origen", "billetera") and not (entidades.get("billetera_origen") or entidades.get("billetera")):
            return False
        if df in ("billetera_destino", "billetera") and not (entidades.get("billetera_destino") or entidades.get("billetera")):
            return False
        if df == "monto" and entidades.get("monto") is None:
            return False
        if df in ("tarjeta", "tarjeta_id") and not (entidades.get("tarjeta") or entidades.get("tarjeta_id")):
            return False

    return True

def _detectar_duplicados_en_lote(entidades: dict) -> tuple[bool, Decimal | None, str | None, str | None]:
    """
    Verifica si dentro del mismo mensaje hay dos o más movimientos idénticos
    (mismo monto, moneda y categoría) (Tarea 4.1).
    Retorna: (hay_duplicados, monto, moneda, categoria_display).
    """
    monto_p = entidades.get("monto")
    if monto_p is None:
        return False, None, None, None
    adicionales = entidades.get("transacciones_adicionales")
    if not adicionales or not isinstance(adicionales, list) or len(adicionales) == 0:
        return False, None, None, None

    moneda_p = entidades.get("moneda") or "ARS"
    cat_p = normalizar_texto(_nombre_corto_categoria(entidades.get("categoria")))

    movimientos = [(Decimal(str(monto_p)), str(moneda_p), cat_p, _nombre_corto_categoria(entidades.get("categoria")))]
    for ad in adicionales:
        if isinstance(ad, dict) and ad.get("monto") is not None:
            m_ad = Decimal(str(ad.get("monto")))
            mon_ad = str(ad.get("moneda") or "ARS")
            c_ad = normalizar_texto(_nombre_corto_categoria(ad.get("categoria")))
            c_disp = _nombre_corto_categoria(ad.get("categoria"))
            movimientos.append((m_ad, mon_ad, c_ad, c_disp))

    vistos = set()
    for m, mon, cat, c_disp in movimientos:
        clave = (m, mon, cat)
        if clave in vistos:
            return True, m, mon, c_disp
        vistos.add(clave)

    return False, None, None, None
