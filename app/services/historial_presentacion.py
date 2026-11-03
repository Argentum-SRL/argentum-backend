# -*- coding: utf-8 -*-
"""
Módulo oficial de presentación de historial de movimientos de Argentum.
Ubicación: app/services/historial_presentacion.py

Provee funciones puras para el cálculo de títulos de presentación e íconos identificadores
de movimientos, eliminando textos redundantes y carteles invasivos en las filas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional
from uuid import UUID

from app.models.transaccion import MetodoPago


@dataclass
class ContextoTitulos:
    """
    Contenedor de datos auxiliares para la resolución determinística de títulos de transacciones.
    """
    nombres_metas: dict[UUID, str] = field(default_factory=dict)
    titulos_base_compras: dict[UUID, str] = field(default_factory=dict)
    padres_desc_map: dict[UUID, str] = field(default_factory=dict)
    cuotas_info: dict[UUID, tuple[int, int]] = field(default_factory=dict)
    nombres_tarjetas: dict[UUID, str] = field(default_factory=dict)


def titulo_base(
    descripcion: Optional[str],
    subcategoria_nombre: Optional[str] = None,
    categoria_nombre: Optional[str] = None,
) -> str:
    """
    Retorna el título base de un movimiento:
    1. Descripción sin espacios al inicio ni al final si no está vacía.
    2. Si no, el nombre de la subcategoría si no está vacía.
    3. Si no, el nombre de la categoría si no está vacía.
    4. Si no, 'Movimiento'.
    """
    if descripcion is not None and str(descripcion).strip():
        return str(descripcion).strip()
    if subcategoria_nombre is not None and str(subcategoria_nombre).strip():
        return str(subcategoria_nombre).strip()
    if categoria_nombre is not None and str(categoria_nombre).strip():
        return str(categoria_nombre).strip()
    return "Movimiento"


def _obtener_mapa(ctx: Any, nombre_campo: str) -> dict:
    if ctx is None:
        return {}
    if hasattr(ctx, nombre_campo):
        val = getattr(ctx, nombre_campo)
        if isinstance(val, dict):
            return val
    if isinstance(ctx, dict):
        val = ctx.get(nombre_campo)
        if isinstance(val, dict):
            return val
    return {}


def _extraer_subcat_nom(tx: Any) -> Optional[str]:
    subcat = getattr(tx, "subcategoria", None)
    if subcat is not None and hasattr(subcat, "nombre"):
        return subcat.nombre
    return getattr(tx, "subcategoria_nombre", None)


def _extraer_cat_nom(tx: Any) -> Optional[str]:
    cat = getattr(tx, "categoria", None)
    if cat is not None and hasattr(cat, "nombre"):
        return cat.nombre
    return getattr(tx, "categoria_nombre", None)


def _extraer_titulo_base_tx(tx: Any) -> str:
    desc = getattr(tx, "descripcion", None)
    subcat_nom = _extraer_subcat_nom(tx)
    cat_nom = _extraer_cat_nom(tx)
    return titulo_base(desc, subcat_nom, cat_nom)


def titulo_movimiento(tx: Any, ctx_titulos: Any = None) -> str:
    """
    Calcula el título determinístico de presentación para una transacción según las reglas L-1b:
    1. Espejo (reintegro_de_id no nulo): el título base de la compra de reintegro_de_id (nunca 'Reintegro:' ni 'Parte de:').
    2. Movimiento de meta (movimiento_meta_id no nulo): el nombre de la meta; si no se encuentra, la descripción sin el prefijo 'Aporte a la meta: ' o 'Retiro de la meta: '.
    3. Cuota hija con número: f'{base} {n}/{m}' con base = descripción del padre o titulo_base de la hija.
    4. Pago de resumen (pago_resumen_vencimiento no nulo) con descripción vacía: 'Pago de tarjeta'.
    5. Si no: titulo_base(...).
    """
    reintegro_de_id = getattr(tx, "reintegro_de_id", None)
    if reintegro_de_id is not None:
        compras_map = _obtener_mapa(ctx_titulos, "titulos_base_compras")
        if reintegro_de_id in compras_map and compras_map[reintegro_de_id]:
            return compras_map[reintegro_de_id]
        # Fallback si no está en el mapa: limpiar prefijos redundantes
        desc = getattr(tx, "descripcion", "") or ""
        for prefijo in ("Reintegro: ", "Reintegro:", "Parte de: ", "Parte de:"):
            if desc.startswith(prefijo):
                desc = desc[len(prefijo):].strip()
                break
        return desc.strip() if desc.strip() else _extraer_titulo_base_tx(tx)

    movimiento_meta_id = getattr(tx, "movimiento_meta_id", None)
    if movimiento_meta_id is not None:
        metas_map = _obtener_mapa(ctx_titulos, "nombres_metas")
        nom_meta = metas_map.get(movimiento_meta_id)
        if nom_meta and str(nom_meta).strip():
            return str(nom_meta).strip()
        # Fallback si no se encontró en el contexto
        desc = getattr(tx, "descripcion", "") or ""
        for prefijo in (
            "Aporte a la meta: ",
            "Retiro de la meta: ",
            "Aporte a meta: ",
            "Retiro de meta: ",
        ):
            if desc.startswith(prefijo):
                desc = desc[len(prefijo):].strip()
                break
        return desc.strip() if desc.strip() else "Meta"

    es_cuota_hija = getattr(tx, "es_cuota_hija", False)
    if es_cuota_hija:
        tx_id = getattr(tx, "id", None)
        cuotas_info_map = _obtener_mapa(ctx_titulos, "cuotas_info")
        c_info = cuotas_info_map.get(tx_id) if tx_id is not None else None
        if c_info:
            n, m = c_info
            padres_map = _obtener_mapa(ctx_titulos, "padres_desc_map")
            grupo_id = getattr(tx, "grupo_cuotas_id", None)
            padre_desc = padres_map.get(grupo_id) if grupo_id is not None else None
            base = padre_desc.strip() if (padre_desc and padre_desc.strip()) else _extraer_titulo_base_tx(tx)
            return f"{base} {n}/{m}"

    pago_resumen_vencimiento = getattr(tx, "pago_resumen_vencimiento", None)
    if pago_resumen_vencimiento is not None:
        tarjeta_id = getattr(tx, "tarjeta_id", None)
        tarjetas_map = _obtener_mapa(ctx_titulos, "nombres_tarjetas")
        if tarjeta_id is not None and tarjeta_id in tarjetas_map:
            nom = tarjetas_map[tarjeta_id]
            if nom and str(nom).strip():
                return f"Pago de tarjeta {str(nom).strip()}"
        desc = getattr(tx, "descripcion", None)
        if desc is None or not str(desc).strip():
            return "Pago de tarjeta"
        return str(desc).strip()

    pago_origen_id = getattr(tx, "pago_origen_id", None)
    if pago_origen_id is not None:
        tarjeta_id = getattr(tx, "tarjeta_id", None)
        tarjetas_map = _obtener_mapa(ctx_titulos, "nombres_tarjetas")
        if tarjeta_id is not None and tarjeta_id in tarjetas_map:
            nom = tarjetas_map[tarjeta_id]
            if nom and str(nom).strip():
                return f"Percepción {str(nom).strip()}"
        return _extraer_titulo_base_tx(tx)

    return _extraer_titulo_base_tx(tx)


def iconos_movimiento(tx: Any, etiqueta: Optional[str] = None) -> List[str]:
    """
    Retorna la lista de códigos de íconos para un movimiento (como mucho los 2 primeros),
    en estricto orden de prioridad según regla L-1c:
    1. 'reintegro' (espejo con tipo_espejo 'reintegro')
    2. 'devolucion' (espejo con tipo_espejo 'parte')
    3. 'paso_por_mi_cuenta' (etiqueta 'Pasó por mi cuenta')
    4. 'inversion' (etiqueta 'Inversión')
    5. 'suscripcion' (suscripcion_id no nulo)
    6. 'credito' (metodo_pago CREDITO)
    """
    codigos: List[str] = []

    es_espejo = getattr(tx, "reintegro_de_id", None) is not None
    tipo_espejo = getattr(tx, "tipo_espejo", None)

    # 1. Reintegro
    if es_espejo and tipo_espejo == "reintegro":
        codigos.append("reintegro")

    # 2. Devolución
    if es_espejo and tipo_espejo == "parte":
        codigos.append("devolucion")

    # 3. Pasó por mi cuenta
    if etiqueta == "Pasó por mi cuenta":
        codigos.append("paso_por_mi_cuenta")

    # 4. Inversión
    if etiqueta == "Inversión":
        codigos.append("inversion")

    # 5. Suscripción
    if getattr(tx, "suscripcion_id", None) is not None:
        codigos.append("suscripcion")

    # 6. Crédito
    mp = getattr(tx, "metodo_pago", None)
    es_credito = False
    if mp is not None:
        if mp == MetodoPago.CREDITO or getattr(mp, "value", None) == "credito" or str(mp).lower() == "credito":
            es_credito = True
    if es_credito:
        codigos.append("credito")

    return codigos[:2]

