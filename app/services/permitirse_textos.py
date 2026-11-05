# -*- coding: utf-8 -*-
"""
app/services/permitirse_textos.py — Textos unificados para "¿Me lo puedo permitir?" (Web y WhatsApp).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.models.usuario import Moneda
from app.services.fin_de_mes_service import redondear_mil
from app.utils.formato import formatear_monto


def _fmt(monto: Any) -> str:
    """Formatea un monto como ARS entero sin decimales hacia el usuario."""
    return formatear_monto(monto, Moneda.ARS, con_decimales=False)


def textos_permitirse(r: Any) -> dict[str, Any]:
    """
    Genera los textos para el resultado de permitirse a partir del resultado del motor.
    Acepta DatosPermitirseResult o dict.
    """
    if hasattr(r, "__dict__"):
        d = r.__dict__
    elif isinstance(r, dict):
        d = r
    else:
        d = {}

    modo = d.get("modo", "contado")
    resultado = d.get("resultado", "justo")
    motivo = d.get("motivo")
    motivo_ingreso = d.get("motivo_ingreso")
    precio = d.get("precio", Decimal("0"))
    libre = d.get("libre", Decimal("0"))
    fin_ciclo: date = d.get("fin_ciclo") or date.today()
    fin_str = fin_ciclo.strftime("%d/%m")
    dias_sin_cargar = d.get("dias_sin_cargar")

    ahorros_total = Decimal(str(d.get("ahorros_total", 0)))
    veces = int(d.get("veces", 0))
    n = int(d.get("n", 0))
    falta = Decimal(str(d.get("falta", 0)))

    min_finales = d.get("min_finales")
    max_finales = d.get("max_finales")
    r_min = Decimal(str(d.get("r_min", 0)))
    deficit_total = Decimal(str(d.get("deficit_total", 0)))
    meses_deficit = int(d.get("meses_deficit", 0))
    deficit_mensual = Decimal(str(d.get("deficit_mensual", 0)))
    meses_alcanzan = int(d.get("meses_alcanzan", 0))

    plan = d.get("plan")
    cantidad_cuotas = int(d.get("cantidad_cuotas", 1))
    fuente_inflacion = d.get("fuente_inflacion")
    mes_liberacion_nombre = d.get("mes_liberacion_nombre")
    rubro_compensar = d.get("rubro_compensar")
    monto_compensar = d.get("monto_compensar")

    # 1. Título
    titulos = {
        "bien": "Te entra bien",
        "justo": "Te entra justo",
        "ahorros": "Solo tocando tus ahorros",
        "no_alcanza": "No te alcanza",
    }
    titulo = titulos.get(resultado, "Te entra justo")

    # 2. Línea principal
    linea_principal = ""
    if modo == "contado":
        if resultado == "bien":
            if min_finales is not None and max_finales is not None:
                b = redondear_mil(Decimal(str(min_finales)) - Decimal(str(precio)))
                a = redondear_mil(Decimal(str(max_finales)) - Decimal(str(precio)))
                if b == a:
                    linea_principal = f"Después de pagarlo llegarías al {fin_str} con unos {_fmt(b)}."
                else:
                    linea_principal = (
                        f"Después de pagarlo llegarías al {fin_str} con entre {_fmt(b)} y {_fmt(a)}."
                    )
            else:
                restante = Decimal(str(libre)) - Decimal(str(precio))
                linea_principal = f"Hoy te quedarían {_fmt(restante)} libres, pero todavía no conozco bien tus meses."
        elif resultado == "justo":
            if n > 0 and veces > 0:
                linea_principal = f"En {veces} de tus últimos {n} meses no te habría alcanzado hasta el {fin_str}."
            elif motivo == "datos_viejos":
                restante = Decimal(str(libre)) - Decimal(str(precio))
                linea_principal = f"Hoy te quedarían {_fmt(restante)} libres."
            else:
                restante = Decimal(str(libre)) - Decimal(str(precio))
                linea_principal = f"Hoy te quedarían {_fmt(restante)} libres, pero todavía no conozco bien tus meses."
        elif resultado == "ahorros":
            if Decimal(str(precio)) > Decimal(str(libre)):
                linea_principal = f"Con lo libre te faltan {_fmt(falta)}."
            else:
                linea_principal = f"En la mayoría de tus meses te faltarían unos {_fmt(falta)} para llegar al {fin_str}."
        elif resultado == "no_alcanza":
            if ahorros_total > Decimal("0"):
                linea_principal = f"Te faltan {_fmt(falta)} aun sumando tus ahorros."
            else:
                linea_principal = f"Te faltan {_fmt(falta)}."

    elif modo in ("cuotas", "mensual"):
        if motivo == "sin_ingreso" or d.get("sin_ingreso"):
            if resultado == "justo":
                if motivo_ingreso == "irregular":
                    linea_principal = "Tus cobros cambian mucho de un mes a otro: no sé si te entra cada mes."
                else:
                    linea_principal = "Cuando cargues lo que cobrás de 2 meses seguidos, te digo si te entra cada mes."
            elif resultado == "ahorros":
                linea_principal = f"Con lo libre te faltan {_fmt(falta)}."
            elif resultado == "no_alcanza":
                if ahorros_total > Decimal("0"):
                    linea_principal = f"Te faltan {_fmt(falta)} aun sumando tus ahorros."
                else:
                    linea_principal = f"Te faltan {_fmt(falta)}."
        elif resultado == "bien":
            linea_principal = f"Cada mes te quedarían al menos {_fmt(r_min)} después de tus gastos de siempre."
        elif resultado == "justo":
            if motivo == "datos_viejos":
                linea_principal = f"Cada mes te quedarían {_fmt(r_min)} después de tus gastos de siempre."
            elif motivo == "aprendiendo":
                linea_principal = f"Cada mes te quedarían {_fmt(r_min)}, pero todavía estoy aprendiendo tus cobros."
            elif motivo == "pocos_datos":
                linea_principal = f"Cada mes te quedarían {_fmt(r_min)}, pero todavía no conozco bien tus meses."
            else:
                linea_principal = f"Te quedarían solo {_fmt(r_min)} por mes después de tus gastos de siempre."
        elif resultado == "ahorros":
            if modo == "cuotas":
                k_str = "1 mes" if meses_deficit == 1 else f"{meses_deficit} meses"
                linea_principal = f"Te faltarían {_fmt(deficit_total)} en {k_str}: tendrías que sacarlos de tus ahorros."
            else:
                m_str = "1 mes" if meses_alcanzan == 1 else f"{meses_alcanzan} meses"
                linea_principal = f"Te faltarían {_fmt(deficit_mensual)} por mes: tus ahorros alcanzan para {m_str}."
        elif resultado == "no_alcanza":
            if modo == "cuotas":
                linea_principal = f"Te faltarían {_fmt(deficit_total)} y tus ahorros no llegan."
            else:
                linea_principal = f"Te faltarían {_fmt(deficit_mensual)} por mes y tus ahorros no llegan a un mes."

    # 3. Líneas del plan (solo en cuotas)
    lineas_plan: list[str] = []
    if modo == "cuotas" and plan is not None:
        cuota_monto = plan.monto_cuota
        total_monto = plan.total
        interes_monto = plan.interes
        vp_monto = plan.valor_presente

        if interes_monto > Decimal("0"):
            lineas_plan.append(f"Son {cantidad_cuotas} cuotas de {_fmt(cuota_monto)} ({_fmt(total_monto)} en total).")
            lineas_plan.append(f"Pagás {_fmt(interes_monto)} de interés.")
        else:
            lineas_plan.append(f"Son {cantidad_cuotas} cuotas de {_fmt(cuota_monto)}.")

        if vp_monto is not None:
            fuente_str = fuente_inflacion or "inflación estimada"
            lineas_plan.append(f"En plata de hoy equivalen a {_fmt(vp_monto)} ({fuente_str}).")

    # 4. Línea ahorros (ahorros y no_alcanza con total > 0)
    linea_ahorros: str | None = None
    if resultado in ("ahorros", "no_alcanza") and ahorros_total > Decimal("0"):
        linea_ahorros = f"Tus ahorros suman {_fmt(ahorros_total)}."

    # 5. Línea liberación
    linea_liberacion: str | None = None
    if mes_liberacion_nombre:
        linea_liberacion = f"Desde {mes_liberacion_nombre} te entra bien: terminás de pagar otras cuotas."

    # 6. Línea compensar
    linea_compensar: str | None = None
    if rubro_compensar and monto_compensar:
        if modo == "contado":
            linea_compensar = f"Si este mes gastás {_fmt(monto_compensar)} menos en {rubro_compensar}, te entra bien."
        else:
            linea_compensar = f"Si gastás {_fmt(monto_compensar)} menos por mes en {rubro_compensar}, te entra bien."

    # 7. Línea frescura
    linea_frescura: str | None = None
    if motivo == "datos_viejos" and dias_sin_cargar is not None and dias_sin_cargar > 0:
        linea_frescura = f"Ojo: hace {dias_sin_cargar} días que no cargás nada, puede no estar al día."

    return {
        "titulo": titulo,
        "linea_principal": linea_principal,
        "lineas_plan": lineas_plan,
        "linea_ahorros": linea_ahorros,
        "linea_liberacion": linea_liberacion,
        "linea_compensar": linea_compensar,
        "linea_frescura": linea_frescura,
    }


def armar_respuesta_permitirse(resultado: dict, consulta: dict) -> str:
    """
    Construye la respuesta textual determinística para WhatsApp según el resultado de
    calcular_puede_permitirse o los datos faltantes de la consulta.
    Cada línea tiene 90 caracteres o menos.
    """
    falta = consulta.get("falta")
    if falta == "precio":
        return 'Decime cuánto sale. Por ejemplo: "¿me puedo permitir algo de 300.000 en 6 cuotas?"'
    if falta == "cuotas":
        return 'Decime en cuántas cuotas. Por ejemplo: "¿me puedo permitir algo de 300.000 en 6 cuotas?"'

    titulo = resultado.get("titulo", "")
    lineas_plan = resultado.get("lineas_plan") or []
    linea_principal = resultado.get("linea_principal")
    linea_ahorros = resultado.get("linea_ahorros")
    linea_liberacion = resultado.get("linea_liberacion")
    linea_compensar = resultado.get("linea_compensar")
    linea_frescura = resultado.get("linea_frescura")

    partes: list[str] = [f"{titulo}."]

    # En cuotas, la primera línea del plan va después del título
    if lineas_plan:
        partes.append(lineas_plan[0])

    if linea_principal:
        partes.append(linea_principal)

    if linea_ahorros:
        partes.append(linea_ahorros)

    if linea_liberacion:
        partes.append(linea_liberacion)
    elif linea_compensar:
        partes.append(linea_compensar)

    if linea_frescura:
        partes.append(linea_frescura)

    if consulta.get("nota_interes"):
        partes.append("Lo calculé sin interés. Si tiene interés, decime la tasa anual (TNA).")

    return "\n".join(partes)
