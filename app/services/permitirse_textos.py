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
from app.services.permitirse_evaluacion import meses_para_juntar
from app.utils.formato import formatear_monto

LINEA_ESPERA = "Si no es urgente, esperá 24 horas y volvé a fijarte."
LARGO_MAXIMO_WPP = 90
FUENTE_INFLACION_POR_DEFECTO = "la inflación que se espera"


def _fmt(monto: Any) -> str:
    """Formatea un monto como ARS entero sin decimales hacia el usuario."""
    return formatear_monto(monto, Moneda.ARS, con_decimales=False)


def _formateador(moneda_precio: str, cotizacion: Any):
    """
    Devuelve la función que muestra los montos en la moneda del precio (D-T1.4).
    Las cuentas se hacen en pesos; con precio en dólares cada monto se pasa de vuelta
    con el mismo dólar oficial que se usó para calcular.
    """
    if moneda_precio != "USD" or not cotizacion:
        return _fmt
    valor_dolar = Decimal(str(cotizacion))

    def _fmt_dolares(monto: Any) -> str:
        en_dolares = Decimal(str(monto)) / valor_dolar
        return formatear_monto(en_dolares, Moneda.USD, con_decimales=False)

    return _fmt_dolares


def _meses_texto(cantidad: int) -> str:
    """'1 mes' o 'N meses'."""
    return "1 mes" if cantidad == 1 else f"{cantidad} meses"


def linea_juntar_texto(falta_bruta: Any, sobrante: Any) -> str | None:
    """Línea "lo juntás" (D-T1d): solo si con lo que le sobra lo junta en 1 a 36 meses."""
    if falta_bruta is None or sobrante is None:
        return None
    meses = meses_para_juntar(Decimal(str(falta_bruta)), Decimal(str(sobrante)))
    if meses is None:
        return None
    return f"Con lo que te sobra por mes, lo juntás en {_meses_texto(meses)}."


def linea_reintegro_texto(rubro: str | None, reintegro: Any, fmt: Any = _fmt) -> str | None:
    """
    Línea de reintegros del rubro que se sugiere recortar (decisión 3 A+B).
    Con reintegros propios: sus datos; si no (o si esa línea no entra en WhatsApp ni
    partida en dos), el consejo general.
    """
    if not rubro:
        return None
    general = f"Fijate si tu banco o billetera tiene reintegros en {rubro}."
    if not reintegro:
        return general
    total, medio = reintegro
    comienzo = f"En {rubro} ya te devolvieron {fmt(total)} con {medio}:"
    if len(comienzo) > LARGO_MAXIMO_WPP:
        return general
    return f"{comienzo} pagá con esa."


def partir_linea_wpp(linea: str) -> list[str]:
    """
    Decisión de Sebastián del 15/11: en WhatsApp, si una línea pasa de 90 caracteres se corta
    después de los dos puntos, con las mismas palabras. Si no pasa, queda como está.
    """
    if len(linea) <= LARGO_MAXIMO_WPP or ": " not in linea:
        return [linea]
    antes, despues = linea.split(": ", 1)
    return [f"{antes}:", despues]


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
    deficit_mensual = Decimal(str(d.get("deficit_mensual", 0)))

    plan = d.get("plan")
    cantidad_cuotas = int(d.get("cantidad_cuotas", 1))
    fuente_inflacion = d.get("fuente_inflacion")
    mes_liberacion_nombre = d.get("mes_liberacion_nombre")
    rubro_compensar = d.get("rubro_compensar")
    monto_compensar = d.get("monto_compensar")

    # Datos nuevos del bloque T1 (si no vienen, todo queda como antes)
    moneda_precio = str(d.get("moneda") or "ARS").upper()
    cotizacion = d.get("cotizacion")
    _fmt = _formateador(moneda_precio, cotizacion)
    usa_cuentas = bool(d.get("usa_cuentas"))
    uso_mensual = Decimal(str(d.get("uso_mensual", 0)))
    meses_cuentas = int(d.get("meses_cuentas", 0))
    meses_total_raw = d.get("meses_total")
    if meses_total_raw is None:
        meses_total_raw = d.get("meses_alcanzan", 0)
    meses_total = int(meses_total_raw)

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

        # Ya viene en rojo: se separa lo que ya le falta de lo que suma esta compra
        libre_dec = Decimal(str(libre))
        if libre_dec < Decimal("0") and resultado in ("ahorros", "no_alcanza"):
            con_compra = Decimal(str(precio)) - libre_dec
            linea_principal = (
                f"Este ciclo ya te faltan {_fmt(-libre_dec)} para tus pagos; "
                f"con esta compra, {_fmt(con_compra)}."
            )

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
        elif resultado == "justo" and usa_cuentas:
            if modo == "cuotas":
                linea_principal = (
                    f"Cada mes vas a usar unos {_fmt(uso_mensual)} "
                    "de la plata que ya tenés en tus cuentas."
                )
            else:
                plazo = "más de 2 años" if meses_cuentas >= 24 else _meses_texto(meses_cuentas)
                linea_principal = f"Con la plata de tus cuentas lo bancás {plazo}."
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
                linea_principal = (
                    f"Usando la plata de tus cuentas te faltarían {_fmt(falta)}: "
                    "tendrías que sacarlos de tus ahorros."
                )
            elif meses_cuentas >= 1:
                linea_principal = (
                    f"Con la plata de tus cuentas lo bancás {_meses_texto(meses_cuentas)}; "
                    "después tendrías que usar tus ahorros."
                )
            else:
                # Sin plata en las cuentas para un mes entero: queda el texto de siempre
                linea_principal = (
                    f"Te faltarían {_fmt(deficit_mensual)} por mes: "
                    f"tus ahorros alcanzan para {_meses_texto(meses_total)}."
                )
        elif resultado == "no_alcanza":
            con_ahorros = ahorros_total > Decimal("0")
            if modo == "cuotas":
                if con_ahorros:
                    linea_principal = (
                        f"Te faltarían {_fmt(falta)} aun usando "
                        "la plata de tus cuentas y tus ahorros."
                    )
                else:
                    linea_principal = (
                        f"Te faltarían {_fmt(falta)} aun usando "
                        "la plata de tus cuentas."
                    )
            else:
                meses_bancados = meses_total if con_ahorros else meses_cuentas
                if meses_bancados == 0:
                    linea_principal = "No te alcanza ni para un mes."
                elif con_ahorros:
                    linea_principal = (
                        "Con la plata de tus cuentas y tus ahorros "
                        f"lo bancás {_meses_texto(meses_bancados)}."
                    )
                else:
                    linea_principal = (
                        f"Con la plata de tus cuentas lo bancás {_meses_texto(meses_bancados)}."
                    )

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
            fuente_str = fuente_inflacion or FUENTE_INFLACION_POR_DEFECTO
            lineas_plan.append(f"Con {fuente_str}, en plata de hoy equivalen a {_fmt(vp_monto)}.")

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

    linea_espera: str | None = None
    if resultado in ("justo", "ahorros"):
        linea_espera = LINEA_ESPERA

    # 8. Líneas nuevas del bloque T1
    linea_cotizacion: str | None = None
    if moneda_precio == "USD" and cotizacion:
        valor_dolar = formatear_monto(Decimal(str(cotizacion)), Moneda.ARS)
        linea_cotizacion = f"Lo calculé con el dólar oficial de hoy ({valor_dolar})."

    linea_cuentas: str | None = None
    if modo == "cuotas" and resultado == "justo" and usa_cuentas and not d.get("sin_ingreso"):
        linea_cuentas = f"Te alcanza para las {cantidad_cuotas} cuotas."

    linea_reintegro: str | None = None
    if resultado in ("justo", "ahorros") and rubro_compensar:
        linea_reintegro = linea_reintegro_texto(rubro_compensar, d.get("reintegro"), _fmt)

    linea_juntar: str | None = None
    if modo == "contado" and resultado in ("ahorros", "no_alcanza"):
        linea_juntar = linea_juntar_texto(d.get("falta_bruta"), d.get("sobrante"))

    return {
        "titulo": titulo,
        "linea_principal": linea_principal,
        "lineas_plan": lineas_plan,
        "linea_ahorros": linea_ahorros,
        "linea_liberacion": linea_liberacion,
        "linea_compensar": linea_compensar,
        "linea_frescura": linea_frescura,
        "linea_espera": linea_espera,
        "linea_cotizacion": linea_cotizacion,
        "linea_cuentas": linea_cuentas,
        "linea_reintegro": linea_reintegro,
        "linea_juntar": linea_juntar,
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
    linea_espera = resultado.get("linea_espera")

    partes: list[str] = [f"{titulo}."]

    # En cuotas, la primera línea del plan va después del título
    if lineas_plan:
        partes.append(lineas_plan[0])

    if resultado.get("linea_cotizacion"):
        partes.append(resultado["linea_cotizacion"])

    if linea_principal:
        partes.extend(partir_linea_wpp(linea_principal))

    if resultado.get("linea_cuentas"):
        partes.append(resultado["linea_cuentas"])

    if linea_ahorros:
        partes.append(linea_ahorros)

    if linea_liberacion:
        partes.append(linea_liberacion)
    elif linea_compensar:
        partes.append(linea_compensar)

    if resultado.get("linea_reintegro"):
        partes.extend(partir_linea_wpp(resultado["linea_reintegro"]))

    if resultado.get("linea_juntar"):
        partes.append(resultado["linea_juntar"])

    if linea_frescura:
        partes.append(linea_frescura)

    if linea_espera:
        partes.append(linea_espera)

    if consulta.get("nota_interes"):
        partes.append("Lo calculé sin interés. Si tiene interés, decime la tasa anual (TNA).")

    return "\n".join(partes)
