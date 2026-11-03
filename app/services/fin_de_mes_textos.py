"""
app/services/fin_de_mes_textos.py — Generación pura de textos para la tarjeta de fin de mes y WhatsApp.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.routers.whatsapp.aviso_frescura_wpp import linea_frescura_wpp
from app.utils.formato import formatear_monto


def _m(x: Optional[Decimal]) -> str:
    """Formatea un monto en ARS sin decimales."""
    return formatear_monto(x, "ARS", con_decimales=False)


def signo(y: Decimal) -> str:
    """Devuelve el monto formateado con prefijo '+' si es positivo, '-' si es negativo o '$0'."""
    if y > Decimal("0"):
        return "+" + _m(y)
    if y < Decimal("0"):
        return _m(y)
    return "$0"


def linea_cobertura(factor: Decimal) -> Optional[str]:
    """Genera la advertencia cuando la cobertura histórica es imperfecta."""
    if factor > Decimal("1"):
        return "Puede que no hayas cargado todo: el rango ya lo tiene en cuenta."
    return None


def texto_rango(bajo: Optional[Decimal], alto: Optional[Decimal]) -> str:
    """Devuelve la frase del rango en formato Unos $X o Entre $X y $Y."""
    if bajo == alto:
        return f"Unos {_m(bajo)}"
    return f"Entre {_m(bajo)} y {_m(alto)}"


def textos_tarjeta(r: Any) -> Optional[Dict[str, Optional[str]]]:
    """
    Genera los textos para la tarjeta web ¿Cómo llegás a fin de mes?.
    Retorna None si el estado no es 'completo'.
    """
    if r.estado != "completo":
        return None

    fin_ciclo = r.fin_ciclo
    proximo_ingreso = r.proximo_ingreso

    if r.cobra_en_ciclo and proximo_ingreso:
        subtitulo = f"Es con lo que llegarías al {fin_ciclo:%d/%m}, contando tu cobro del {proximo_ingreso:%d/%m}."
    else:
        subtitulo = f"Es con lo que llegarías al {fin_ciclo:%d/%m}, cuando termina tu ciclo."

    linea_riesgo = None
    if r.veces is not None and r.veces >= 1:
        linea_riesgo = f"En {r.veces} de tus últimos {r.ciclos} meses no te alcanzó."

    linea_cobro = None
    if r.cobra_en_ciclo and proximo_ingreso and r.libre_hasta_cobro is not None:
        linea_cobro = f"Hasta tu cobro del {proximo_ingreso:%d/%m} te quedan {_m(r.libre_hasta_cobro)} libres."

    linea_cobertura_val = linea_cobertura(r.factor_cobertura)

    return {
        "titulo": "¿Cómo llegás a fin de mes?",
        "monto": texto_rango(r.rango_bajo, r.rango_alto),
        "subtitulo": subtitulo,
        "linea_riesgo": linea_riesgo,
        "linea_cobro": linea_cobro,
        "linea_cobertura": linea_cobertura_val,
    }


def linea_saldo(r: Any) -> str:
    """Genera la línea resumen de saldo al inicio y movimiento del ciclo."""
    return f"Empezaste el ciclo con {_m(r.saldo_inicio)} y se movieron {signo(r.movimiento)}: hoy tenés {_m(r.saldo_hoy)}."


def mensaje_wpp(r: Any) -> str:
    """
    Genera el mensaje consolidado para responder 'consultar_proyeccion' en WhatsApp.
    Un solo mensaje con líneas unidas por '\n', cada una de 90 caracteres o menos.
    """
    lineas: List[str] = []

    if r.estado == "completo":
        fin_ciclo = r.fin_ciclo
        bajo = r.rango_bajo
        alto = r.rango_alto
        if bajo == alto:
            lineas.append(f"Llegarías al {fin_ciclo:%d/%m} con unos {_m(bajo)}.")
        else:
            lineas.append(f"Llegarías al {fin_ciclo:%d/%m} con entre {_m(bajo)} y {_m(alto)}.")

        if r.veces is not None and r.veces >= 1:
            lineas.append(f"En {r.veces} de tus últimos {r.ciclos} meses no te alcanzó.")

        if r.cobra_en_ciclo and r.proximo_ingreso and r.libre_hasta_cobro is not None:
            lineas.append(f"Hasta tu cobro del {r.proximo_ingreso:%d/%m} te quedan {_m(r.libre_hasta_cobro)} libres.")

        cob_txt = linea_cobertura(r.factor_cobertura)
        if cob_txt is not None:
            lineas.append(cob_txt)
    else:
        lineas.append("Todavía no tengo suficientes meses tuyos para decirte con cuánto llegás.")
        if r.libre_ars >= Decimal("0"):
            lineas.append(f"Hoy tenés {_m(r.libre_ars)} libres.")
        else:
            lineas.append(f"Hoy te faltan {_m(abs(r.libre_ars))} para cubrir lo que vence.")

        if r.estado == "datos_viejos":
            frescura_txt = linea_frescura_wpp(r.frescura)
            if frescura_txt is not None:
                lineas.append(frescura_txt)

    if r.libre_usd != Decimal("0"):
        lineas.append(f"En dólares: {formatear_monto(r.libre_usd, 'USD', con_decimales=False)} libres.")

    return "\n".join(lineas)
