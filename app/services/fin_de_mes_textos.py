"""
app/services/fin_de_mes_textos.py — Generación pura de textos y calendario para fin de mes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.routers.whatsapp.aviso_frescura_wpp import linea_frescura_wpp
from app.utils.formato import formatear_monto


def _m(x: Optional[Decimal]) -> str:
    """Formatea un monto en ARS sin decimales."""
    return formatear_monto(x, "ARS", con_decimales=False)


def _signo(y: Decimal) -> str:
    """Devuelve el monto formateado con prefijo '+' si es positivo, '-' si es negativo o '$0'."""
    if y > Decimal("0"):
        return "+" + _m(y)
    if y < Decimal("0"):
        return _m(y)
    return "$0"


def signo(y: Decimal) -> str:
    """Devuelve el monto formateado con prefijo '+' si es positivo, '-' si es negativo o '$0'."""
    return _signo(y)


def linea_rango(bajo: Optional[Decimal], alto: Optional[Decimal]) -> Optional[str]:
    """Genera la frase de rango estimado a fecha límite."""
    if bajo is None or alto is None:
        return None
    if bajo == alto:
        return f"A esa fecha llegarías con unos {_m(bajo)}."
    return f"A esa fecha llegarías con entre {_m(bajo)} y {_m(alto)}."


def linea_cobertura(factor: Decimal) -> Optional[str]:
    """Genera la advertencia cuando la cobertura histórica es imperfecta."""
    if factor > Decimal("1"):
        return "Puede que no hayas cargado todo: el rango ya lo tiene en cuenta."
    return None


class TextosWebDict(dict):
    """Diccionario que permite acceso por atributos para conveniencia."""
    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def textos_web(r: Any) -> TextosWebDict:
    """Genera los textos para la tarjeta web en pesos."""
    limite = r.limite
    proximo_ingreso = r.proximo_ingreso
    hasta_ingreso = r.hasta_ingreso

    titulo = "Libre hasta tu próximo ingreso" if hasta_ingreso else "Libre hasta el fin del ciclo"
    monto = _m(r.libre_ars)

    if hasta_ingreso and proximo_ingreso:
        subtitulo = f"Hasta el {limite:%d/%m}, antes de cobrar el {proximo_ingreso:%d/%m}."
    else:
        subtitulo = f"Hasta el {limite:%d/%m}, cuando termina tu ciclo."

    linea_rango_val = None
    if r.estado == "completo" and r.rango.rango_bajo is not None and r.rango.rango_alto is not None:
        linea_rango_val = linea_rango(r.rango.rango_bajo, r.rango.rango_alto)

    linea_riesgo = None
    if r.estado == "completo" and r.rango.veces_no_alcanza is not None and r.rango.veces_no_alcanza >= 1:
        linea_riesgo = f"Gastando como en tus últimos {r.rango.ciclos} meses, en {r.rango.veces_no_alcanza} no te alcanzaría."

    linea_estado = None
    if r.estado == "solo_seguro":
        linea_estado = "Con unos meses más de datos vas a ver cómo llegás a esa fecha."
    elif r.estado == "sin_ingreso":
        linea_estado = "Cuando cargues tus ingresos vas a ver cómo llegás a esa fecha."

    linea_cobertura_val = None
    if r.estado == "completo" and r.rango.factor_cobertura > Decimal("1"):
        linea_cobertura_val = linea_cobertura(r.rango.factor_cobertura)

    linea_saldo = f"Empezaste el ciclo con {_m(r.saldo_inicio)} y se movieron {_signo(r.movimiento)}: hoy tenés {_m(r.saldo_hoy)}."

    return TextosWebDict({
        "titulo": titulo,
        "monto": monto,
        "subtitulo": subtitulo,
        "linea_rango": linea_rango_val,
        "linea_riesgo": linea_riesgo,
        "linea_estado": linea_estado,
        "linea_cobertura": linea_cobertura_val,
        "linea_saldo": linea_saldo,
    })


def textos_web_usd(r: Any) -> TextosWebDict:
    """Genera los textos para la tarjeta web en dólares."""
    limite = r.limite
    proximo_ingreso = r.proximo_ingreso
    hasta_ingreso = r.hasta_ingreso

    titulo = "Libre hasta tu próximo ingreso" if hasta_ingreso else "Libre hasta el fin del ciclo"
    monto = formatear_monto(r.libre_usd, "USD", con_decimales=False)

    if hasta_ingreso and proximo_ingreso:
        subtitulo = f"Hasta el {limite:%d/%m}, antes de cobrar el {proximo_ingreso:%d/%m}."
    else:
        subtitulo = f"Hasta el {limite:%d/%m}, cuando termina tu ciclo."

    return TextosWebDict({
        "titulo": titulo,
        "monto": monto,
        "subtitulo": subtitulo,
        "linea_rango": None,
        "linea_riesgo": None,
        "linea_estado": None,
        "linea_cobertura": None,
        "linea_saldo": None,
    })


def calendario(compromisos: List[Dict[str, Any]], moneda: str) -> List[Dict[str, str]]:
    """
    Formatea la lista de compromisos para el calendario:
    {"fecha": dd/mm de fecha_cobro, "nombre": nombre, "monto": formatear_monto(monto, moneda)}
    """
    res: List[Dict[str, str]] = []
    for c in compromisos:
        fc = c.get("fecha_cobro")
        if isinstance(fc, str):
            try:
                f_date = date.fromisoformat(fc)
                fecha_str = f_date.strftime("%d/%m")
            except Exception:
                fecha_str = fc
        elif isinstance(fc, date):
            fecha_str = fc.strftime("%d/%m")
        else:
            fecha_str = str(fc or "")

        nombre = str(c.get("nombre") or "")
        monto_val = c.get("monto")
        monto_str = formatear_monto(monto_val, moneda, con_decimales=False)
        res.append({
            "fecha": fecha_str,
            "nombre": nombre,
            "monto": monto_str,
        })
    return res


def mensaje_wpp(r: Any) -> str:
    """
    Genera el mensaje consolidado para responder 'consultar_proyeccion' en WhatsApp.
    Un solo mensaje con líneas unidas por '\n', cada una de 90 caracteres o menos.
    """
    lineas: List[str] = []
    limite_str = r.limite.strftime("%d/%m")

    # Línea 1
    if r.libre_ars >= Decimal("0"):
        if r.hasta_ingreso:
            l1 = f"Te quedan {_m(r.libre_ars)} libres hasta el {limite_str}, antes de tu próximo cobro."
        else:
            l1 = f"Te quedan {_m(r.libre_ars)} libres hasta el {limite_str}, fin de tu ciclo."
    else:
        l1 = f"Hasta el {limite_str} te faltan {_m(abs(r.libre_ars))} para cubrir lo que vence."
    lineas.append(l1)

    # Línea 2
    l2 = None
    if r.estado == "completo":
        if r.rango.rango_bajo is not None and r.rango.rango_alto is not None:
            l2 = linea_rango(r.rango.rango_bajo, r.rango.rango_alto)
    elif r.estado == "solo_seguro":
        l2 = "Con unos meses más de datos vas a ver cómo llegás a esa fecha."
    elif r.estado == "sin_ingreso":
        l2 = "Cuando cargues tus ingresos vas a ver cómo llegás a esa fecha."
    elif r.estado == "datos_viejos":
        l2 = linea_frescura_wpp(r.frescura)

    if l2:
        lineas.append(l2)

    # Línea 3 (solo "completo" con veces >= 1)
    if r.estado == "completo" and r.rango.veces_no_alcanza is not None and r.rango.veces_no_alcanza >= 1:
        l3 = f"Gastando como en tus últimos {r.rango.ciclos} meses, en {r.rango.veces_no_alcanza} no te alcanzaría."
        lineas.append(l3)

    # Línea 4 (si libre_usd != 0)
    if r.libre_usd != Decimal("0"):
        l4 = f"En dólares: {formatear_monto(r.libre_usd, 'USD', con_decimales=False)} libres."
        lineas.append(l4)

    return "\n".join(lineas)
