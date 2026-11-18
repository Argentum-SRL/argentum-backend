# -*- coding: utf-8 -*-
"""Reglas puras para los porcentajes de aviso configurables por presupuesto."""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Sequence
from uuid import UUID

from fastapi import HTTPException

from app.models.notificacion import TipoNotificacion


def alertas_por_defecto(umbral_1: int, umbral_1_activo: bool) -> list[int]:
    """Devuelve los porcentajes iniciales de un presupuesto según el perfil."""
    if umbral_1_activo:
        # Respeta el porcentaje configurado dentro del rango válido de perfil (50 a 95)
        valor = max(50, min(95, int(umbral_1)))
        return [valor, 100]
    return [100]


def _es_alerta_valida(valor: object) -> bool:
    return (
        not isinstance(valor, bool)
        and isinstance(valor, int)
        and 10 <= valor <= 100
    )


def validar_alertas(lista: object) -> list[int]:
    """Valida de 1 a 3 porcentajes enteros entre 10 y 100 sin repetir."""
    es_valida = (
        isinstance(lista, list)
        and 1 <= len(lista) <= 3
        and all(_es_alerta_valida(v) for v in lista)
        and len(set(lista)) == len(lista)
    )
    if not es_valida:
        raise HTTPException(
            status_code=422,
            detail="Elegí de 1 a 3 avisos, de 10% a 100% y sin repetir.",
        )

    return sorted(lista)


def umbral_a_avisar(
    usado_pct: Decimal,
    umbrales: Sequence[int],
    ya_avisados: set[int],
) -> int | None:
    """Elige el mayor porcentaje alcanzado que supere al mayor ya avisado en el período."""
    usado_dec = Decimal(str(usado_pct))
    tope = max(ya_avisados, default=0)
    candidatos = [
        u for u in umbrales if Decimal(u) <= usado_dec and u > tope
    ]
    return max(candidatos, default=None)


def grupo_alerta(
    presupuesto_id: UUID | str,
    periodo_id: UUID | str,
    umbral: int,
) -> str:
    """Construye la clave única de notificación por presupuesto, período y porcentaje."""
    return f"presupuestos/{presupuesto_id}/{periodo_id}/umbral_{umbral}"


def extraer_umbrales_avisados(
    grupos: Iterable[str | None],
    prefijo: str,
    umbrales: Sequence[int],
) -> set[int]:
    """Interpreta los grupos ya notificados del período, incluidos los grupos previos."""
    avisados: set[int] = set()
    menores_100 = [u for u in umbrales if u < 100]
    mayor_menor_100 = max(menores_100, default=None)

    for grupo in grupos:
        if not grupo or not grupo.startswith(prefijo):
            continue
        sufijo = grupo[len(prefijo):]
        if sufijo.startswith("umbral_"):
            numero_str = sufijo[len("umbral_"):]
            if numero_str.isdigit():
                avisados.add(int(numero_str))
        elif sufijo == TipoNotificacion.PRESUPUESTO_AGOTADO.value:
            avisados.add(100)
        elif sufijo == TipoNotificacion.PRESUPUESTO_LIMITE.value:
            if mayor_menor_100 is not None:
                avisados.add(mayor_menor_100)

    return avisados
