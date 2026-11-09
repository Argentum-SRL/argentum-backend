# -*- coding: utf-8 -*-
"""
app/services/consejos_extra.py
Funciones puras, constantes y dataclasses para los consejos C8 a C13 (fase_i2b_consejos).
Sin acceso a base de datos, sin red, sin hoy_argentina.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from app.services.consejos_reglas import (
    Consejo,
    DatosConsejos,
    FilaGasto,
    es_compra,
    formatear_monto,
    m,
    mad,
    mediana,
    puede_aconsejar,
    redondeo,
    redondeo_arriba,
)

# ==============================================================================
# E-C. CONSTANTES Y ESTRUCTURAS
# ==============================================================================

MESES_ES: tuple[str, ...] = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

SUBCATEGORIAS_SERVICIOS: tuple[tuple[str, str], ...] = (
    ("Vivienda", "Luz"),
    ("Vivienda", "Gas"),
    ("Vivienda", "Agua"),
    ("Vivienda", "Expensas"),
    ("Tecnología", "Internet"),
    ("Tecnología", "Celular"),
    ("Salud", "Prepaga y obra social"),
    ("Ocio", "Streaming"),
)


@dataclass(frozen=True)
class SaldoArrastradoDato:
    tarjeta: str
    tarjeta_id: str
    moneda: str
    monto_restante: Decimal
    vencimiento: date


@dataclass(frozen=True)
class ResumenUsdDato:
    tarjeta: str
    tarjeta_id: str
    cierre: date
    vencimiento: date
    usd_pendiente: Decimal
    percepcion_pct: Decimal


@dataclass(frozen=True)
class IngresoDato:
    id: str
    fecha: date
    monto: Decimal
    moneda: str
    categoria: str | None
    subcategoria: str | None


@dataclass(frozen=True)
class MetaDato:
    id: str
    nombre: str
    moneda: str
    objetivo: Decimal
    actual: Decimal
    fecha_limite: date | None
    dias_desde_creacion: int
    aportes_ciclos: tuple[Decimal, ...]
    cantidad_aportes: int


@dataclass(frozen=True)
class DatosExtra:
    saldos: tuple[SaldoArrastradoDato, ...] = ()
    resumenes_usd: tuple[ResumenUsdDato, ...] = ()
    usd_propios: Decimal = Decimal("0")
    cotizacion_venta: Decimal | None = None
    cuotas_proximas: tuple[tuple[date, dict[str, Decimal]], ...] = ()
    ingreso_habitual: dict[str, Decimal] = field(default_factory=dict)
    ingresos_7: tuple[IngresoDato, ...] = ()
    metas: tuple[MetaDato, ...] = ()


# ==============================================================================
# E-D. CONSEJOS (Funciones puras)
# ==============================================================================

def evaluar_carga(
    cargas_hist: list[Decimal],
    proxima: Decimal,
    siguiente: Decimal,
    ingreso: Decimal | None,
    moneda: str,
    inicio_proximo: date,
) -> Consejo | None:
    """C8: Cuotas que pesan."""
    if sum(1 for c in cargas_hist if c > Decimal("0")) < 3 or proxima <= Decimal("0"):
        return None

    me = mediana(cargas_hist)
    d = mad(cargas_hist)
    if not (proxima > me + Decimal("3") * d and proxima > max(cargas_hist)):
        return None

    pr = redondeo(proxima, moneda)
    n = len(cargas_hist)
    texto = f"El mes que viene ya tenés {m(pr, moneda)} comprometidos en cuotas, lo más alto de los últimos {n} meses."

    if ingreso is not None and ingreso > Decimal("0"):
        pct = int((proxima * Decimal("100") / ingreso).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        texto += f" Es el {pct}% de lo que te entra."

    libera = redondeo(proxima - siguiente, moneda)
    if libera > Decimal("0"):
        texto += f" Al mes siguiente se te liberan {m(libera, moneda)}."

    valores = {
        "proxima": str(proxima.quantize(Decimal("0.01"))),
        "mediana": str(me.quantize(Decimal("0.01"))),
        "libera": str(libera.quantize(Decimal("0.01"))),
    }

    return Consejo(
        tipo="carga",
        clave=f"carga/{moneda}/{inicio_proximo.isoformat()}",
        moneda=moneda,
        texto=texto,
        accion=None,
        accion_datos=None,
        valores=valores,
        ahorro=None,
    )


def evaluar_extra(
    ingresos_7: tuple[IngresoDato, ...] | list[IngresoDato],
    ingreso_habitual: dict[str, Decimal],
    metas: tuple[MetaDato, ...] | list[MetaDato],
    hoy: date,
) -> Consejo | None:
    """C9: Ingreso extra (aguinaldo, bono o cobro grande)."""
    candidatos_ing = sorted(ingresos_7, key=lambda i: (i.fecha, i.id), reverse=True)

    for ing in candidatos_ing:
        if not (hoy - timedelta(days=6) <= ing.fecha <= hoy):
            continue

        if ing.subcategoria == "Aguinaldo":
            frase = f"Te entraron {m(ing.monto, ing.moneda)} de aguinaldo."
        elif ing.subcategoria == "Bonos y extras":
            frase = f"Te entraron {m(ing.monto, ing.moneda)} de un bono o extra."
        else:
            habitual = ingreso_habitual.get(ing.moneda)
            if habitual is not None and ing.monto > habitual * Decimal("1.5"):
                frase = f"Te entraron {m(ing.monto, ing.moneda)}, más de lo habitual."
            else:
                continue

        sug = redondeo(ing.monto * Decimal("0.2"), ing.moneda)
        if sug <= Decimal("0"):
            continue

        metas_moneda = [meta for meta in metas if meta.moneda == ing.moneda]
        if metas_moneda:
            def _meta_sort_key(meta: MetaDato):
                return (1 if meta.fecha_limite is None else 0, meta.fecha_limite or date.max, meta.nombre)

            meta_elegida = sorted(metas_moneda, key=_meta_sort_key)[0]
            texto = frase + f" Si apartás {m(sug, ing.moneda)} ahora para {meta_elegida.nombre}, no se diluye en el mes."
            accion = "aportar_meta"
            accion_datos = None
        else:
            texto = frase + f" Si apartás {m(sug, ing.moneda)} ahora para una meta, no se diluye en el mes."
            accion = "crear_meta"
            accion_datos = {"nombre": "Ahorro", "monto": str(int(sug)), "moneda": ing.moneda}

        valores = {
            "monto": str(ing.monto.quantize(Decimal("0.01"))),
            "sugerido": str(sug.quantize(Decimal("0.01"))),
        }

        return Consejo(
            tipo="extra",
            clave=f"extra/{ing.id}",
            moneda=ing.moneda,
            texto=texto,
            accion=accion,
            accion_datos=accion_datos,
            valores=valores,
            ahorro=None,
        )

    return None


def evaluar_meta(meta: MetaDato, hoy: date) -> Consejo | None:
    """C10: Meta atrasada y meta sin fecha."""
    faltante = meta.objetivo - meta.actual
    if faltante <= Decimal("0") or meta.cantidad_aportes < 1 or len(meta.aportes_ciclos) == 0:
        return None

    ritmo = sum(meta.aportes_ciclos) / Decimal(len(meta.aportes_ciclos))
    if ritmo <= Decimal("0"):
        return None

    meses = math.ceil(faltante / ritmo)
    m_total = hoy.month - 1 + meses
    año_est = hoy.year + m_total // 12
    mes_est_num = (m_total % 12) + 1
    mes_est = f"{MESES_ES[mes_est_num - 1]} de {año_est}"

    if meta.fecha_limite is not None:
        if meta.dias_desde_creacion < 60 or meta.fecha_limite <= hoy:
            return None
        if (año_est, mes_est_num) <= (meta.fecha_limite.year, meta.fecha_limite.month):
            return None
        meses_hasta = max(1, (meta.fecha_limite.year - hoy.year) * 12 + (meta.fecha_limite.month - hoy.month))
        necesario = redondeo_arriba(faltante / Decimal(meses_hasta), meta.moneda)
        mes_lim = f"{MESES_ES[meta.fecha_limite.month - 1]} de {meta.fecha_limite.year}"
        texto = f"A este ritmo llegás a {meta.nombre} en {mes_est}, no en {mes_lim}. Para llegar a tiempo hacen falta unos {m(necesario, meta.moneda)} por mes."
        accion = "aportar_meta"
        clave = f"meta/{meta.id}/{hoy:%Y-%m}"
        valores = {
            "ritmo": str(ritmo.quantize(Decimal("0.01"))),
            "necesario": str(necesario.quantize(Decimal("0.01"))),
        }
    else:
        if meta.dias_desde_creacion < 30:
            return None
        texto = f"Ponerle fecha a {meta.nombre} la vuelve más real. A tu ritmo actual llegarías en {mes_est}."
        accion = "poner_fecha_meta"
        clave = f"meta/{meta.id}/sin_fecha"
        valores = {
            "ritmo": str(ritmo.quantize(Decimal("0.01"))),
        }

    return Consejo(
        tipo="meta",
        clave=clave,
        moneda=meta.moneda,
        texto=texto,
        accion=accion,
        accion_datos=None,
        valores=valores,
        ahorro=None,
    )


def indice_ipc(ipc: dict[str, Decimal], mes: str) -> Decimal | None:
    """
    Obtiene el índice IPC para un mes ("YYYY-MM").
    - Si el mes está en ipc -> ipc[mes].
    - Si es posterior al último mes con dato L y existe L-1 -> ipc[L] * (ipc[L]/ipc[L-1]) ** k
      (donde k es la cantidad de meses entre L y mes), redondeado a 4 decimales (ROUND_HALF_UP).
    - En cualquier otro caso (anterior al primer dato, hueco en el medio o no existe L-1) -> None.
    """
    if not ipc:
        return None

    if mes in ipc:
        return ipc[mes]

    meses_ordenados = sorted(ipc.keys())
    ultimo_mes = meses_ordenados[-1]

    # Anterior al primer dato o en un hueco intermedio
    if mes < ultimo_mes:
        return None

    # Mes posterior a L
    try:
        y_l, m_l = map(int, ultimo_mes.split("-"))
        y_mes, m_mes = map(int, mes.split("-"))
    except Exception:
        return None

    prev_m = 12 if m_l == 1 else m_l - 1
    prev_y = y_l - 1 if m_l == 1 else y_l
    mes_l_prev = f"{prev_y:04d}-{prev_m:02d}"

    if mes_l_prev not in ipc:
        return None

    val_l = ipc[ultimo_mes]
    val_prev = ipc[mes_l_prev]
    if val_l <= Decimal("0") or val_prev <= Decimal("0"):
        return None

    k = (y_mes - y_l) * 12 + (m_mes - m_l)
    if k <= 0:
        return None

    ratio = val_l / val_prev
    estimado = val_l * (ratio ** k)
    return estimado.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def evaluar_subio(
    filas: list[FilaGasto],
    ipc: dict[str, Decimal],
    hoy: date,
) -> list[Consejo]:
    """C11: Servicio que subió más que la inflación (solo pesos)."""
    resultado: list[Consejo] = []

    for cat, sub in SUBCATEGORIAS_SERVICIOS:
        pagos = sorted(
            [
                f for f in filas
                if f.moneda == "ARS"
                and f.categoria == cat
                and f.subcategoria == sub
                and es_compra(f)
                and not f.es_cuota
            ],
            key=lambda f: f.fecha,
        )
        if len(pagos) < 4:
            continue

        ultimo = pagos[-1]
        if ultimo.fecha <= hoy - timedelta(days=45):
            continue

        anteriores = pagos[-4:-1]
        antes = mediana([p.monto for p in anteriores])
        if antes <= Decimal("0"):
            continue

        ahora = ultimo.monto
        mes_antes = anteriores[1].fecha.strftime("%Y-%m")
        mes_ahora = ultimo.fecha.strftime("%Y-%m")

        idx_antes = indice_ipc(ipc, mes_antes)
        idx_ahora = indice_ipc(ipc, mes_ahora)
        if idx_antes is None or idx_ahora is None or idx_antes <= Decimal("0"):
            continue

        infl = idx_ahora / idx_antes - Decimal("1")
        suba = ahora / antes - Decimal("1")

        if not (suba * Decimal("100") > infl * Decimal("100") + Decimal("5")):
            continue

        infl_estimada = "si" if (mes_ahora not in ipc or mes_antes not in ipc) else "no"

        pct = int((suba * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        infl_pct = (infl * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        infl_txt = str(infl_pct).replace(".", ",")
        texto = f"{sub} pasó de {m(antes, 'ARS')} a {m(ahora, 'ARS')} ({pct}%), más que la inflación de esos meses ({infl_txt}%). Puede valer la pena revisar el plan."
        tipo = "subio"
        moneda = "ARS"
        clave = f"subio/{sub}/{ultimo.fecha:%Y-%m}"
        ahorro = (ahora - antes * (Decimal("1") + infl)) * Decimal("12")
        valores = {
            "antes": str(antes.quantize(Decimal("0.01"))),
            "ahora": str(ahora.quantize(Decimal("0.01"))),
            "infl": str(infl.quantize(Decimal("0.0001"))),
            "infl_estimada": infl_estimada,
            "idx_antes": str(idx_antes.quantize(Decimal("0.0001"))),
            "idx_ahora": str(idx_ahora.quantize(Decimal("0.0001"))),
            "pagos": [str(p.monto) for p in pagos[-4:]],
            "mes_antes": mes_antes,
            "mes_ahora": mes_ahora,
        }

        resultado.append(
            Consejo(
                tipo=tipo,
                clave=clave,
                moneda=moneda,
                texto=texto,
                accion=None,
                accion_datos=None,
                valores=valores,
                ahorro=ahorro,
            )
        )

    return resultado


def evaluar_arrastre(
    saldos: tuple[SaldoArrastradoDato, ...] | list[SaldoArrastradoDato],
) -> list[Consejo]:
    """C12: Saldo del resumen sin pagar (tarjeta a medias)."""
    grupos: dict[tuple[str, str], dict[str, object]] = {}
    for s in saldos:
        if s.monto_restante <= Decimal("0"):
            continue
        key = (s.tarjeta_id, s.moneda)
        if key not in grupos:
            grupos[key] = {
                "tarjeta": s.tarjeta,
                "tarjeta_id": s.tarjeta_id,
                "moneda": s.moneda,
                "total": Decimal("0"),
                "vencimiento": s.vencimiento,
            }
        grupos[key]["total"] = Decimal(str(grupos[key]["total"])) + s.monto_restante
        if s.vencimiento > grupos[key]["vencimiento"]:
            grupos[key]["vencimiento"] = s.vencimiento

    grupos_ordenados = sorted(grupos.values(), key=lambda g: (str(g["tarjeta"]), str(g["moneda"])))
    resultado: list[Consejo] = []

    for g in grupos_ordenados:
        total = Decimal(str(g["total"]))
        vencimiento = g["vencimiento"]
        tarjeta = str(g["tarjeta"])
        moneda = str(g["moneda"])
        tarjeta_id = str(g["tarjeta_id"])

        texto = f"Del resumen de {tarjeta} te quedaron {m(total, moneda)} sin pagar: la tarjeta cobra de los intereses más altos. Si podés, pagalo antes que otros gastos."
        tipo = "arrastre"
        clave = f"arrastre/{tarjeta_id}/{moneda}/{vencimiento.isoformat()}"
        valores = {
            "saldo": str(total.quantize(Decimal("0.01"))),
            "tarjeta_id": tarjeta_id,
            "moneda": moneda,
        }

        resultado.append(
            Consejo(
                tipo=tipo,
                clave=clave,
                moneda=moneda,
                texto=texto,
                accion=None,
                accion_datos=None,
                valores=valores,
                ahorro=None,
            )
        )

    return resultado


def evaluar_dolares(
    resumenes_usd: tuple[ResumenUsdDato, ...] | list[ResumenUsdDato],
    usd_propios: Decimal,
    cotizacion_venta: Decimal | None,
    hoy: date,
) -> list[Consejo]:
    """C13: Pagar resumen en dólares con dólares propios para evitar percepción."""
    if cotizacion_venta is None or usd_propios <= Decimal("0"):
        return []

    elegibles: list[ResumenUsdDato] = []
    for r in resumenes_usd:
        if (
            r.usd_pendiente > Decimal("0")
            and r.percepcion_pct > Decimal("0")
            and r.cierre < hoy <= r.vencimiento
        ):
            elegibles.append(r)

    if not elegibles:
        return []

    # Gana la de mayor saldo_usd; empate: id lexicográfico menor
    elegibles.sort(key=lambda x: (-x.usd_pendiente, str(x.tarjeta_id)))
    ganadora = elegibles[0]

    evitable = min(ganadora.usd_pendiente, usd_propios)
    if evitable <= Decimal("0"):
        return []

    perc = evitable * cotizacion_venta * ganadora.percepcion_pct / Decimal("100")
    pr = perc.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if pr <= Decimal("0"):
        return []

    if ganadora.usd_pendiente <= usd_propios:
        monto_mostrar = f"los {formatear_monto(ganadora.usd_pendiente, 'USD')}"
    else:
        monto_mostrar = f"{formatear_monto(evitable, 'USD')} de los {formatear_monto(ganadora.usd_pendiente, 'USD')}"

    texto = f"Si pagás {monto_mostrar} del resumen de {ganadora.tarjeta} con tus dólares, evitás unos {formatear_monto(pr, 'ARS')} de percepción."

    return [
        Consejo(
            tipo="dolares",
            clave=f"dolares/{ganadora.tarjeta_id}",
            moneda="ARS",
            texto=texto,
            accion=None,
            accion_datos=None,
            valores={
                "evitable": str(evitable.quantize(Decimal("0.01"))),
                "percepcion": str(perc.quantize(Decimal("0.01"))),
            },
            ahorro=perc,
        )
    ]


def evaluar_candidatos(datos: DatosConsejos) -> list[Consejo]:
    """Evalúa todos los candidatos de consejos (C1 a C15), incorporando DatosExtra si existen."""
    if not puede_aconsejar(datos):
        return []

    from app.services.consejos_reglas import evaluar_todos
    lista = evaluar_todos(datos)

    if datos.extra is not None:
        extra: DatosExtra = datos.extra

        # C12: Arrastre
        lista.extend(evaluar_arrastre(extra.saldos))

        # C13: Dólares
        lista.extend(evaluar_dolares(extra.resumenes_usd, extra.usd_propios, extra.cotizacion_venta, datos.hoy))

        # C8: Carga para ARS y USD
        for mon in ("ARS", "USD"):
            cargas_hist = [
                sum(
                    (f.monto for f in datos.filas if c[0] <= f.fecha <= c[1] and f.moneda == mon and f.es_cuota),
                    Decimal("0"),
                )
                for c in datos.cerrados
            ]
            inicio_prox = (
                extra.cuotas_proximas[0][0]
                if len(extra.cuotas_proximas) > 0
                else (datos.ciclo_actual[1] + timedelta(days=1))
            )
            prox = extra.cuotas_proximas[0][1].get(mon, Decimal("0")) if len(extra.cuotas_proximas) > 0 else Decimal("0")
            sig = extra.cuotas_proximas[1][1].get(mon, Decimal("0")) if len(extra.cuotas_proximas) > 1 else Decimal("0")
            ing = extra.ingreso_habitual.get(mon)
            c8 = evaluar_carga(cargas_hist, prox, sig, ing, mon, inicio_prox)
            if c8:
                lista.append(c8)

        # C9: Extra
        c9 = evaluar_extra(extra.ingresos_7, extra.ingreso_habitual, extra.metas, datos.hoy)
        if c9:
            lista.append(c9)

        # C10: Metas en el orden en que vienen
        for m_dato in extra.metas:
            c10 = evaluar_meta(m_dato, datos.hoy)
            if c10:
                lista.append(c10)

        # C11: Subió
        lista.extend(evaluar_subio(datos.filas, datos.ipc, datos.hoy))

    return lista

