# -*- coding: utf-8 -*-
"""
app/services/conveniencia_service.py — Motor puro de comparación "Cuotas vs contado" (Bloque T2).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Sequence

from app.models.usuario import Moneda
from app.services.fin_de_mes_service import redondear_mil
from app.utils.formato import formatear_monto

UMBRAL_INDIFERENCIA = Decimal("0.01")


def comparar_cuotas_contado(
    precio_contado: Decimal | float | int | str,
    cantidad: int,
    valor_cuota: Decimal | float | int | str,
    inflacion_mensual: Sequence[Decimal | float | int | str],
) -> dict[str, Any]:
    """
    Compara el pago de contado contra N cuotas iguales descontadas por inflación mes a mes.
    Si la lista de inflación es más corta que `cantidad`, repite el último valor.
    Función pura.
    """
    contado_d = Decimal(str(precio_contado))
    cuota_d = Decimal(str(valor_cuota))
    cant = int(cantidad)

    if contado_d <= Decimal("0"):
        raise ValueError("El precio de contado tiene que ser mayor a 0.")
    if cant <= 0:
        raise ValueError("La cantidad de cuotas tiene que ser mayor a 0.")
    if cuota_d <= Decimal("0"):
        raise ValueError("El valor de cada cuota tiene que ser mayor a 0.")
    if not inflacion_mensual:
        raise ValueError("Falta la lista de inflación mensual.")

    total = (cuota_d * Decimal(cant)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    vp_total = Decimal("0")
    factor = Decimal("1")
    detalle_por_mes: list[dict[str, Any]] = []
    cuota_nom_f = float(cuota_d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    for k in range(1, cant + 1):
        inf_k = (
            inflacion_mensual[k - 1]
            if k - 1 < len(inflacion_mensual)
            else inflacion_mensual[-1]
        )
        tasa_k = Decimal(str(inf_k)) / Decimal("100")
        factor = factor * (Decimal("1") + tasa_k)
        vp_k = cuota_d / factor
        vp_total += vp_k
        detalle_por_mes.append({
            "mes": k,
            "cuota_nominal": cuota_nom_f,
            "cuota_valor_presente": float(
                vp_k.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            ),
        })

    vp = vp_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    diferencia_real = (contado_d - vp).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    ratio = abs(diferencia_real) / contado_d

    if ratio < UMBRAL_INDIFERENCIA:
        resultado = "indiferente"
    elif diferencia_real > Decimal("0"):
        resultado = "conviene_cuotas"
    else:
        resultado = "conviene_contado"

    extra = (total - contado_d).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    extra_pct = int(
        ((extra / contado_d) * Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )

    return {
        "total": total,
        "vp": vp,
        "extra": extra,
        "extra_pct": extra_pct,
        "resultado": resultado,
        "diferencia_real": diferencia_real,
        "detalle_por_mes": detalle_por_mes,
    }


def textos_conveniencia(
    resultado: str,
    cantidad: int,
    valor_cuota: Decimal | float | int,
    total: Decimal | float | int,
    extra: Decimal | float | int,
    extra_pct: int,
    vp: Decimal | float | int,
    fuente_texto: str,
) -> dict[str, str]:
    """
    Arma las tres líneas canónicas de D-T2.3 para web y WhatsApp.
    """
    if resultado == "conviene_cuotas":
        titulo = "Te conviene en cuotas."
    elif resultado == "conviene_contado":
        titulo = "Te conviene de contado."
    else:
        titulo = "Da casi lo mismo."

    cuota_fmt = formatear_monto(valor_cuota, Moneda.ARS)
    total_fmt = formatear_monto(total, Moneda.ARS)
    extra_d = Decimal(str(extra))

    if extra_d > Decimal("0"):
        extra_fmt = formatear_monto(extra_d, Moneda.ARS)
        linea_2 = (
            f"{cantidad} cuotas de {cuota_fmt} son {total_fmt}: "
            f"{extra_fmt} más que de contado ({extra_pct}%)."
        )
    elif extra_d == Decimal("0"):
        linea_2 = f"{cantidad} cuotas de {cuota_fmt} son {total_fmt}, lo mismo que de contado."
    else:
        menos_fmt = formatear_monto(-extra_d, Moneda.ARS)
        linea_2 = (
            f"{cantidad} cuotas de {cuota_fmt} son {total_fmt}: "
            f"{menos_fmt} menos que de contado."
        )

    vp_red = redondear_mil(Decimal(str(vp)))
    vp_fmt = formatear_monto(vp_red, Moneda.ARS, con_decimales=False)
    linea_3 = f"Con {fuente_texto}, en plata de hoy son unos {vp_fmt}."

    return {
        "titulo": titulo,
        "linea_2": linea_2,
        "linea_3": linea_3,
    }
