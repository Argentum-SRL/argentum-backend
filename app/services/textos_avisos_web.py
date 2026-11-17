"""Textos exactos de los avisos de la campanita web (familias N1 a N17)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence, Union

from app.core.reglas_texto_web import fecha_dm
from app.models.usuario import Moneda
from app.utils.formato import formatear_monto

Monto = Union[Decimal, float, int]


def texto_cambio_contrasena() -> str:
    """N1: Cambio de contraseña."""
    return (
        "Se cambió la contraseña de tu cuenta.\n"
        "Si no fuiste vos, cambiala ya."
    )


def texto_cambio_email() -> str:
    """N2: Cambio de email."""
    return (
        "Se cambió el email de tu cuenta.\n"
        "Si no fuiste vos, cambiá tu contraseña ya."
    )


def texto_billetera_sin_saldo(nombre: str) -> str:
    """N3: Billetera sin saldo (SALDO_CERO)."""
    return f"Tu billetera {nombre} se quedó sin saldo."


def texto_meta_cumplida(
    nombre: str,
    monto_objetivo: Monto,
    moneda: Union[Moneda, str] = Moneda.ARS,
) -> str:
    """N4: Meta cumplida."""
    monto_fmt = formatear_monto(monto_objetivo, moneda, con_decimales=False)
    return (
        f"¡Completaste tu meta {nombre}!\n"
        f"Juntaste {monto_fmt}."
    )


def texto_factura_vence(
    descripcion: str,
    monto: Monto,
    moneda: Union[Moneda, str],
    fecha_vencimiento: date,
    hoy: date,
) -> str:
    """N5: Factura por vencer (3 días antes o el mismo día)."""
    monto_fmt = formatear_monto(monto, moneda)
    if fecha_vencimiento == hoy:
        return f"La factura de {descripcion} de {monto_fmt} vence hoy."
    return (
        f"La factura de {descripcion} de {monto_fmt} "
        f"vence el {fecha_dm(fecha_vencimiento)}."
    )


def texto_resumen_tarjeta_vence(
    tarjeta_nombre: str,
    fecha_cierre: date,
    fecha_vencimiento: date,
) -> str:
    """N6: Resumen de tarjeta por vencer."""
    return (
        f"El resumen de tu {tarjeta_nombre} "
        f"cierra el {fecha_dm(fecha_cierre)} "
        f"y vence el {fecha_dm(fecha_vencimiento)}."
    )


def texto_cuota_sin_tarjeta_vence(
    numero: int | str,
    total: int | str,
    descripcion: str,
    fecha_vencimiento: date,
    monto: Monto,
    moneda: Union[Moneda, str] = Moneda.ARS,
) -> str:
    """N7: Cuota sin tarjeta por vencer."""
    monto_fmt = formatear_monto(monto, moneda)
    return (
        f"La cuota {numero} de {total} de {descripcion} "
        f"vence el {fecha_dm(fecha_vencimiento)}: {monto_fmt}."
    )


def texto_suscripcion_hoy(
    nombre: str,
    monto: Monto,
    moneda: Union[Moneda, str] = Moneda.ARS,
) -> str:
    """N8: Suscripción que se cobra hoy."""
    monto_fmt = formatear_monto(monto, moneda, con_decimales=False)
    return f"Hoy se cobra {nombre}: {monto_fmt}."


def texto_suscripcion_proxima(
    nombre: str,
    monto: Monto,
    moneda: Union[Moneda, str],
    fecha_cobro: date,
    hoy: date,
) -> str:
    """N9: Suscripción próxima a cobrar."""
    monto_fmt = formatear_monto(monto, moneda, con_decimales=False)
    if (fecha_cobro - hoy).days == 1:
        return f"Mañana se cobra {nombre}: {monto_fmt}."
    return (
        f"{nombre} se cobra el {fecha_dm(fecha_cobro)}: {monto_fmt}."
    )


def texto_inactividad(dias: int) -> str:
    """N10: Recordatorio por inactividad."""
    return (
        f"Hace {dias} días que no cargás nada.\n"
        "Cargá tus gastos para ver en qué se va tu plata."
    )


def _hay_movimiento(ingresos: Monto | None, gastos: Monto | None) -> bool:
    return (ingresos is not None and Decimal(str(ingresos)) > 0) or (
        gastos is not None and Decimal(str(gastos)) > 0
    )


def texto_resumen_semanal(
    ingresos_ars: Monto | None = None,
    gastos_ars: Monto | None = None,
    ingresos_usd: Monto | None = None,
    gastos_usd: Monto | None = None,
    categoria_top: str | None = None,
) -> str:
    """N11: Resumen semanal en una o dos monedas."""
    hay_ars = _hay_movimiento(ingresos_ars, gastos_ars)
    hay_usd = _hay_movimiento(ingresos_usd, gastos_usd)
    lineas: list[str] = []

    if hay_ars and hay_usd:
        ing_ars = formatear_monto(ingresos_ars, Moneda.ARS, con_decimales=False)
        gas_ars = formatear_monto(gastos_ars, Moneda.ARS, con_decimales=False)
        ing_usd = formatear_monto(ingresos_usd, Moneda.USD, con_decimales=False)
        gas_usd = formatear_monto(gastos_usd, Moneda.USD, con_decimales=False)
        lineas.append("Tu resumen de la semana.")
        lineas.append(f"- En pesos: entraron {ing_ars} y gastaste {gas_ars}.")
        lineas.append(f"- En dólares: entraron {ing_usd} y gastaste {gas_usd}.")
    else:
        mon = Moneda.USD if hay_usd else Moneda.ARS
        ing = ingresos_usd if hay_usd else (ingresos_ars or 0)
        gas = gastos_usd if hay_usd else (gastos_ars or 0)
        ing_fmt = formatear_monto(ing, mon, con_decimales=False)
        gas_fmt = formatear_monto(gas, mon, con_decimales=False)
        lineas.append(f"Esta semana entraron {ing_fmt} y gastaste {gas_fmt}.")

    if categoria_top:
        lineas.append(f"Lo que más pesó: {categoria_top}.")

    return "\n".join(lineas)


def texto_resumen_ciclo(
    ingresos_ars: Monto | None = None,
    gastos_ars: Monto | None = None,
    ingresos_usd: Monto | None = None,
    gastos_usd: Monto | None = None,
    categoria_top: str | None = None,
    monto_categoria_top: Monto | None = None,
    gastos_hormiga: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """N12: Fin del mes (RESUMEN_CICLO) en una o dos monedas."""
    hay_ars = _hay_movimiento(ingresos_ars, gastos_ars)
    hay_usd = _hay_movimiento(ingresos_usd, gastos_usd)
    moneda_top = Moneda.USD if (hay_usd and not hay_ars) else Moneda.ARS
    lineas: list[str] = []

    if hay_ars and hay_usd:
        ing_ars = formatear_monto(ingresos_ars, Moneda.ARS, con_decimales=False)
        gas_ars = formatear_monto(gastos_ars, Moneda.ARS, con_decimales=False)
        ing_usd = formatear_monto(ingresos_usd, Moneda.USD, con_decimales=False)
        gas_usd = formatear_monto(gastos_usd, Moneda.USD, con_decimales=False)
        lineas.append("Terminó tu mes.")
        lineas.append(f"- En pesos: entraron {ing_ars} y gastaste {gas_ars}.")
        lineas.append(f"- En dólares: entraron {ing_usd} y gastaste {gas_usd}.")
    else:
        mon = Moneda.USD if hay_usd else Moneda.ARS
        ing_dec = Decimal(str((ingresos_usd if hay_usd else ingresos_ars) or 0))
        gas_dec = Decimal(str((gastos_usd if hay_usd else gastos_ars) or 0))
        bal_dec = ing_dec - gas_dec
        if bal_dec > 0:
            bal_fmt = formatear_monto(bal_dec, mon, con_decimales=False)
            lineas.append(f"Terminó tu mes: te sobraron {bal_fmt}.")
        elif bal_dec < 0:
            falta_fmt = formatear_monto(abs(bal_dec), mon, con_decimales=False)
            lineas.append(
                f"Terminó tu mes: gastaste {falta_fmt} más de lo que entró."
            )
        else:
            lineas.append("Terminó tu mes: gastaste lo mismo que entró.")
        ing_fmt = formatear_monto(ing_dec, mon, con_decimales=False)
        gas_fmt = formatear_monto(gas_dec, mon, con_decimales=False)
        lineas.append(f"Entraron {ing_fmt} y gastaste {gas_fmt}.")

    if categoria_top and monto_categoria_top is not None:
        cat_fmt = formatear_monto(
            monto_categoria_top,
            moneda_top,
            con_decimales=False,
        )
        lineas.append(f"Lo que más pesó: {categoria_top} ({cat_fmt}).")

    if gastos_hormiga:
        lineas.append("Gastos chicos que se repiten:")
        for g in list(gastos_hormiga)[:2]:
            cat = g["categoria"]
            cant = int(g["cantidad"])
            total_fmt = formatear_monto(g["total"], moneda_top, con_decimales=False)
            if cant == 1:
                lineas.append(f"- {cat}: {total_fmt} en 1 compra")
            else:
                lineas.append(f"- {cat}: {total_fmt} en {cant} compras")

    return "\n".join(lineas)


def texto_presupuesto_superado(
    nombre: str,
    gastado: Monto,
    limite: Monto,
    moneda: Union[Moneda, str] = Moneda.ARS,
) -> str:
    """N13: Presupuesto superado (web y respaldo WhatsApp)."""
    gastado_fmt = formatear_monto(gastado, moneda, con_decimales=False)
    limite_fmt = formatear_monto(limite, moneda, con_decimales=False)
    return (
        f"Te pasaste del presupuesto {nombre}: "
        f"gastaste {gastado_fmt} de {limite_fmt}."
    )


def texto_presupuesto_limite(
    porcentaje: int,
    nombre: str,
    gastado: Monto,
    limite: Monto,
    moneda: Union[Moneda, str] = Moneda.ARS,
) -> str:
    """N14: Presupuesto cerca del límite (web y respaldo WhatsApp)."""
    gastado_fmt = formatear_monto(gastado, moneda, con_decimales=False)
    limite_fmt = formatear_monto(limite, moneda, con_decimales=False)
    return (
        f"Ya usaste el {int(porcentaje)}% del presupuesto {nombre}: "
        f"{gastado_fmt} de {limite_fmt}."
    )


def texto_reintegro_pendiente(
    monto: Monto,
    moneda: Union[Moneda, str],
    descripcion: str,
) -> str:
    """N15: Reintegro pendiente a los 30 días."""
    monto_fmt = formatear_monto(monto, moneda, con_decimales=False)
    return f"¿Te llegó el reintegro de {monto_fmt} de {descripcion}?"


def texto_resumen_impago(
    tarjeta_nombre: str,
    fecha_vencimiento: date,
    monto: Monto,
    moneda: Union[Moneda, str] = Moneda.ARS,
) -> str:
    """N16: Resumen de tarjeta impago."""
    monto_fmt = formatear_monto(monto, moneda, con_decimales=False)
    return (
        f"El resumen de tu {tarjeta_nombre} "
        f"venció el {fecha_dm(fecha_vencimiento)}: {monto_fmt}.\n"
        "¿Ya lo pagaste?"
    )


def texto_cobros_suscripcion_faltaban(cantidad: int, nombre: str) -> str:
    """N17: Cobros de suscripción que faltaban."""
    if cantidad == 1:
        return f"Se anotó 1 cobro de {nombre} que faltaba."
    return f"Se anotaron {cantidad} cobros de {nombre} que faltaban."
