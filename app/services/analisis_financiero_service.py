from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.suscripcion import EstadoSuscripcion, Suscripcion
from app.models.tools import IPCCache
from app.models.transaccion import EstadoVerificacionTransaccion, TipoTransaccion, Transaccion
from app.models.transaccion_recurrente import EstadoTransaccionRecurrente, TipoTransaccionRecurrente, TransaccionRecurrente
from app.models.usuario import Moneda, Usuario
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.fecha import hoy_argentina
from app.utils.finanzas import ClasificacionGasto, StreamRecurrente, ZERO, clasificar_gastos, deflactar_monto, es_gasto_consumo, gasto_ciclo, mad, mediana, percentil, posicion_relativa


CONFIDENCE_BY_CYCLES = {0: "sin_datos", 1: "inicial", 2: "baja", 3: "media"}


def _ciclos(usuario: Usuario, fecha_inicio: date, hasta: date, maximo: int | None = None) -> list[tuple[date, date]]:
    result: list[tuple[date, date]] = []
    cursor = fecha_inicio
    while cursor <= hasta and (maximo is None or len(result) < maximo):
        inicio, fin = get_ciclo_fechas(usuario, cursor)
        result.append((inicio, min(fin, hasta)))
        cursor = fin + timedelta(days=1)
    return result


def _ciclos_anteriores(usuario: Usuario, hoy: date, cantidad: int = 12) -> list[tuple[date, date]]:
    inicio_actual, _ = get_ciclo_fechas(usuario, hoy)
    result = []
    cursor = inicio_actual - timedelta(days=1)
    for _ in range(cantidad):
        inicio, fin = get_ciclo_fechas(usuario, cursor)
        result.append((inicio, fin))
        cursor = inicio - timedelta(days=1)
    return list(reversed(result))


def _carga(db: Session, usuario: Usuario) -> dict[str, Any]:
    hoy = hoy_argentina()
    txs = db.execute(
        select(Transaccion)
        .options(
            joinedload(Transaccion.categoria),
            joinedload(Transaccion.subcategoria),
            joinedload(Transaccion.billetera),
        )
        .where(Transaccion.usuario_id == usuario.id, Transaccion.fecha <= hoy)
        .order_by(Transaccion.fecha)
    ).scalars().all()
    ipc = db.execute(select(IPCCache).order_by(IPCCache.fecha_dato)).scalars().all()
    cuotas = db.execute(
        select(Cuota, GrupoCuotas)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .where(GrupoCuotas.usuario_id == usuario.id)
    ).all()
    suscripciones = db.execute(
        select(Suscripcion).where(Suscripcion.usuario_id == usuario.id, Suscripcion.estado == EstadoSuscripcion.ACTIVA)
    ).scalars().all()
    historial_subs = db.execute(
        select(HistorialSuscripcion).join(Suscripcion).where(Suscripcion.usuario_id == usuario.id)
    ).scalars().all()
    recurrentes = db.execute(
        select(TransaccionRecurrente).where(
            TransaccionRecurrente.usuario_id == usuario.id,
            TransaccionRecurrente.estado == EstadoTransaccionRecurrente.ACTIVA,
        )
    ).scalars().all()
    return {"hoy": hoy, "txs": txs, "ipc": ipc, "cuotas": cuotas, "suscripciones": suscripciones, "historial_subs": historial_subs, "recurrentes": recurrentes}


def _tx_valido(tx: Any) -> bool:
    return tx.estado_verificacion in (None, EstadoVerificacionTransaccion.CONFIRMADA) and not tx.es_padre_cuotas


def _suma_deflactada(txs: list[Any], ipc: list[Any], destino: date, moneda: Moneda, tipo: TipoTransaccion | None = None) -> Decimal:
    if tipo == TipoTransaccion.EGRESO:
        return gasto_ciclo(txs, min((tx.fecha for tx in txs), default=destino), max((tx.fecha for tx in txs), default=destino), destino, ipc, moneda).deflactado
    total = ZERO
    for tx in txs:
        if tx.moneda != moneda or not _tx_valido(tx) or (tipo is not None and tx.tipo != tipo):
            continue
        if tipo == TipoTransaccion.EGRESO and not es_gasto_consumo(tx):
            continue
        ajuste = deflactar_monto(tx.monto, tx.fecha, destino, ipc, moneda)
        total += ajuste.monto
    return total


def _ciclos_montos(txs: list[Any], ciclos: list[tuple[date, date]], ipc: list[Any], destino: date, moneda: Moneda, tipo: TipoTransaccion) -> list[Decimal]:
    return [
        _suma_deflactada([tx for tx in txs if inicio <= tx.fecha <= fin], ipc, destino, moneda, tipo)
        for inicio, fin in ciclos
    ]


def _confidence(ciclos: int, cobertura: Decimal) -> str:
    if ciclos == 0:
        return "sin_datos"
    if ciclos == 1:
        return "inicial"
    if ciclos == 2:
        return "baja"
    if ciclos == 3 or cobertura < Decimal("0.75"):
        return "media"
    if ciclos >= 6 and cobertura >= Decimal("0.80"):
        return "alto"
    return "media"


def calcular_perfil_nuevo(db: Session, usuario: Usuario, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or _carga(db, usuario)
    hoy = data["hoy"]
    txs = data["txs"]
    ipc = data["ipc"]
    ciclos = _ciclos_anteriores(usuario, hoy, 12)
    ciclos_con_datos = [c for c in ciclos if any(c[0] <= tx.fecha <= c[1] for tx in txs)]
    cobertura = Decimal(len(ciclos_con_datos)) / Decimal(len(ciclos)) if ciclos else ZERO
    comprometidos_externos = [
        *(cuota for cuota, _ in data["cuotas"] if not cuota.pagada and cuota.fecha_vencimiento >= hoy),
        *data["suscripciones"],
    ]
    clasificacion = clasificar_gastos(txs, ciclos, ipc, hoy, comprometidos_externos)

    ingresos = _ciclos_montos(txs, ciclos, ipc, hoy, Moneda.ARS, TipoTransaccion.INGRESO)
    ingresos_con_datos = [v for v in ingresos if v > ZERO]
    gastos = []
    variables = []
    for inicio, fin in ciclos:
        ciclo_txs = [tx for tx in txs if inicio <= tx.fecha <= fin]
        gastos.append(_suma_deflactada([tx for tx in ciclo_txs if tx in list(clasificacion.recurrentes_detectados) or tx in list(clasificacion.variables)], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO))
        variables.append(_suma_deflactada([tx for tx in ciclo_txs if tx in list(clasificacion.variables)], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO))

    inicio_actual, fin_actual = get_ciclo_fechas(usuario, hoy)
    actual_ingreso = _suma_deflactada([tx for tx in txs if inicio_actual <= tx.fecha <= hoy], ipc, hoy, Moneda.ARS, TipoTransaccion.INGRESO)
    actual_gasto = _suma_deflactada([tx for tx in txs if inicio_actual <= tx.fecha <= hoy], ipc, hoy, Moneda.ARS, TipoTransaccion.EGRESO)
    observaciones_completas = [(ingreso, gasto) for ingreso, gasto in zip(ingresos, gastos) if ingreso > ZERO and gasto > ZERO]
    ingresos_completos = [ingreso for ingreso, _ in observaciones_completas]
    gastos_completos = [gasto for _, gasto in observaciones_completas]
    ingreso_tipico = mediana(ingresos_completos or ingresos_con_datos)
    gasto_tipico = mediana(gastos_completos)
    variable_tipico = mediana([v for v in variables if v > ZERO])
    variable_mad = mad([v for v in variables if v > ZERO])
    ahorro = (ingreso_tipico - gasto_tipico) / ingreso_tipico if ingreso_tipico and ingreso_tipico > ZERO and gasto_tipico is not None else None

    cuotas = data["cuotas"]
    comprometido = sum((c.monto_real or c.monto_proyectado or ZERO for c, grupo in cuotas if grupo.moneda == Moneda.ARS and not c.pagada and c.fecha_vencimiento >= hoy), ZERO)
    comprometido += sum((h.monto for h in data["historial_subs"] if h.moneda == Moneda.ARS and any(s.id == h.suscripcion_id for s in data["suscripciones"])), ZERO)
    comprometido_ratio = comprometido / ingreso_tipico if ingreso_tipico and ingreso_tipico > ZERO else None
    from app.models.billetera import Billetera
    saldo_ars = sum((b.saldo_actual for b in db.execute(select(Billetera).where(Billetera.usuario_id == usuario.id, Billetera.moneda == Moneda.ARS)).scalars()), ZERO)
    runway = saldo_ars / gasto_tipico if gasto_tipico and gasto_tipico > ZERO else None
    consistencia = Decimal(len(ciclos_con_datos)) / Decimal(len(ciclos)) if ciclos else None
    posicion_gasto = posicion_relativa(actual_gasto, [v for v in gastos if v > ZERO])
    posicion_ahorro = posicion_relativa(ahorro, [((i - g) / i) for i, g in observaciones_completas]) if ahorro is not None else None

    return {
        "ciclos_con_datos": len(ciclos_con_datos),
        "ciclos_observados": len(ciclos),
        "nivel_confianza": _confidence(len(ciclos_con_datos), cobertura),
        "cobertura_registro": cobertura,
        "ingreso_tipico_ars": ingreso_tipico,
        "estabilidad_ingreso_mad": (mad(ingresos_con_datos) / ingreso_tipico if ingreso_tipico else None),
        "ingreso_actual_percentil": posicion_relativa(actual_ingreso, ingresos_con_datos),
        "gasto_comprometido_ars": comprometido,
        "gasto_comprometido_ratio": comprometido_ratio,
        "capacidad_ahorro": ahorro,
        "capacidad_ahorro_percentil": posicion_ahorro,
        "runway_meses": runway,
        "volatilidad_gasto_variable": (variable_mad / variable_tipico if variable_tipico else None),
        "gasto_actual_percentil": posicion_gasto,
        "consistencia_registro": consistencia,
        "metodo": "mediana_MAD_percentiles_IPC",
    }


def _historial_categorias(txs: list[Any], ciclos: list[tuple[date, date]], ipc: list[Any], destino: date, moneda: Moneda, clasificacion: ClasificacionGasto) -> dict[Any, list[Decimal]]:
    resultado: dict[Any, list[Decimal]] = {}
    variables = set(clasificacion.variables) | set(clasificacion.recurrentes_detectados)
    for inicio, fin in ciclos:
        por_cat: dict[Any, Decimal] = {}
        for tx in txs:
            if tx not in variables or tx.moneda != moneda or not (inicio <= tx.fecha <= fin):
                continue
            valor = deflactar_monto(tx.monto, tx.fecha, destino, ipc, moneda).monto
            por_cat[tx.categoria_id] = por_cat.get(tx.categoria_id, ZERO) + valor
        for cat, valor in por_cat.items():
            resultado.setdefault(cat, []).append(valor)
    return resultado


def _cantidad_hasta(cuotas: list[tuple[Any, Any]], hoy: date, fin: date, moneda: Moneda) -> Decimal:
    return sum((c.monto_real or c.monto_proyectado or ZERO for c, grupo in cuotas if grupo.moneda == moneda and not c.pagada and hoy <= c.fecha_vencimiento <= fin), ZERO)


def calcular_proyeccion_nueva(db: Session, usuario: Usuario) -> dict[str, Any]:
    data = _carga(db, usuario)
    hoy = data["hoy"]
    inicio, fin = get_ciclo_fechas(usuario, hoy)
    anteriores = _ciclos_anteriores(usuario, hoy, 12)
    comprometidos_externos = [
        *(cuota for cuota, _ in data["cuotas"] if not cuota.pagada and cuota.fecha_vencimiento >= hoy),
        *data["suscripciones"],
    ]
    clasificacion = clasificar_gastos(data["txs"], anteriores, data["ipc"], hoy, comprometidos_externos)
    resultado: dict[str, Any] = {}
    for moneda in (Moneda.ARS, Moneda.USD):
        historiales = _historial_categorias(data["txs"], anteriores, data["ipc"], hoy, moneda, clasificacion)
        actuales: dict[Any, Decimal] = {}
        for tx in data["txs"]:
            if inicio <= tx.fecha <= hoy and tx.moneda == moneda and tx in list(clasificacion.recurrentes_detectados) + list(clasificacion.variables):
                actuales[tx.categoria_id] = actuales.get(tx.categoria_id, ZERO) + tx.monto
        categorias = []
        total_estimado = ZERO
        dias_totales = Decimal((fin - inicio).days + 1)
        transcurridos = Decimal(max(1, (hoy - inicio).days + 1))
        for cat_id, valores in historiales.items():
            actual = actuales.get(cat_id, ZERO)
            centro = mediana(valores) or ZERO
            p25 = percentil(valores, Decimal("0.25")) or centro
            p75 = percentil(valores, Decimal("0.75")) or centro
            presencia = len(valores) / len(anteriores) if anteriores else 0
            if presencia >= 0.60 or actual > ZERO:
                proyectado = centro
            else:
                continue
            if proyectado >= ZERO:
                pass
            elif presencia >= 0.60:
                proyectado = centro
            else:
                continue
            categorias.append({"categoria_id": str(cat_id) if cat_id else None, "categoria_nombre": next((getattr(tx.categoria, "nombre", None) for tx in data["txs"] if tx.categoria_id == cat_id), "Sin categoría"), "gasto_actual_ciclo": actual, "promedio_historico": centro, "proyectado": proyectado, "rango_piso": p25, "rango_techo": p75, "fuera_de_patron": False})
            total_estimado += proyectado
        cuotas = _cantidad_hasta(data["cuotas"], hoy, fin, moneda)
        subs = sum((h.monto for h in data["historial_subs"] if h.moneda == moneda and any(s.id == h.suscripcion_id and s.proximo_cobro <= fin for s in data["suscripciones"])), ZERO)
        ciclos_totales = [_suma_deflactada([tx for tx in data["txs"] if a <= tx.fecha <= b], data["ipc"], hoy, moneda, TipoTransaccion.EGRESO) for a, b in anteriores]
        ciclos_totales = [v for v in ciclos_totales if v > ZERO]
        variable_central = mediana(ciclos_totales) or total_estimado
        if total_estimado > ZERO and variable_central != total_estimado:
            escala = variable_central / total_estimado
            for categoria in categorias:
                categoria["proyectado"] *= escala
                categoria["rango_piso"] *= escala
                categoria["rango_techo"] *= escala
            total_estimado = variable_central
        total_comprometido = cuotas + subs
        total = variable_central + total_comprometido
        dispersion_mad = mad(ciclos_totales) or ZERO
        dispersion_iqr = ((percentil(ciclos_totales, Decimal("0.75")) or variable_central) - (percentil(ciclos_totales, Decimal("0.25")) or variable_central)) / Decimal("2")
        dispersion_p10_p90 = ((percentil(ciclos_totales, Decimal("0.90")) or variable_central) - (percentil(ciclos_totales, Decimal("0.10")) or variable_central)) / Decimal("2")
        dispersion = max(dispersion_mad, dispersion_iqr, dispersion_p10_p90)
        piso = max(ZERO, total - dispersion)
        techo = total + dispersion
        ingreso_actual = _suma_deflactada([tx for tx in data["txs"] if inicio <= tx.fecha <= hoy], data["ipc"], hoy, moneda, TipoTransaccion.INGRESO)
        ingresos_hist = _ciclos_montos(data["txs"], anteriores, data["ipc"], hoy, moneda, TipoTransaccion.INGRESO)
        ingreso_central = ingreso_actual if ingreso_actual > ZERO else (mediana([v for v in ingresos_hist if v > ZERO]) or ZERO)
        resultado[moneda.value.lower()] = {"periodo": {"fecha_inicio": inicio.isoformat(), "fecha_fin": fin.isoformat(), "dias_transcurridos": int(transcurridos), "dias_restantes": max(0, (fin - hoy).days), "dias_totales": int(dias_totales)}, "gasto_proyectado_total": total, "rango": {"piso": piso, "central": total, "techo": techo}, "rango_poco_informativo": dispersion > total if total > ZERO else False, "balance_proyectado": ingreso_central - total, "ingresos_proyectados": ingreso_central, "certezas": {"cuotas_restantes": cuotas, "suscripciones_restantes": subs, "total": total_comprometido}, "desglose_por_categoria": categorias, "nivel_confianza": _confidence(len(ciclos_totales), Decimal(len(ciclos_totales)) / Decimal(len(anteriores) or 1)), "ciclos_analizados": len(ciclos_totales), "pesos": {"historial": Decimal("1"), "ciclo_actual": Decimal("0")}, "advertencias": [], "datos_suficientes": bool(ciclos_totales or ingreso_actual > ZERO), "clasificacion": {"comprometidos": len(clasificacion.comprometidos), "recurrentes_detectados": len(clasificacion.recurrentes_detectados), "variables": len(clasificacion.variables)}}
    return resultado


def proyectar_ingreso_ciclo(db: Session, usuario: Usuario, fecha_ciclo: date) -> dict[str, Decimal | bool]:
    """Proyecta ingresos de un ciclo usando la estacionalidad disponible.

    Para junio y diciembre usa la mediana de los ingresos de esos meses
    históricos, preservando el aguinaldo observado en esos ciclos.
    """
    data = _carga(db, usuario)
    inicio, fin = get_ciclo_fechas(usuario, fecha_ciclo)
    ciclos = _ciclos_anteriores(usuario, fecha_ciclo, 12)
    ingresos = []
    ingresos_por_ciclo = []
    estacionales = []
    for ciclo_inicio, ciclo_fin in ciclos:
        valor = _suma_deflactada(
            [tx for tx in data["txs"] if ciclo_inicio <= tx.fecha <= ciclo_fin],
            data["ipc"],
            fecha_ciclo,
            Moneda.ARS,
            TipoTransaccion.INGRESO,
        )
        if valor > ZERO:
            ingresos.append(valor)
        ingresos_por_ciclo.append((ciclo_fin, valor))
        if valor > ZERO and ciclo_fin.month == fecha_ciclo.month:
            estacionales.append(valor)
    regular = mediana([valor for ciclo_fin, valor in ingresos_por_ciclo if ciclo_fin.month not in (6, 12)])
    base = mediana(estacionales) if estacionales and fecha_ciclo.month in (6, 12) else mediana(ingresos)
    aguinaldo = max(ZERO, (base or ZERO) - (regular or ZERO)) if fecha_ciclo.month in (6, 12) else ZERO
    return {
        "fecha_inicio": inicio.isoformat(),
        "fecha_fin": fin.isoformat(),
        "ingreso_proyectado": base or ZERO,
        "estacionalidad_aplicada": bool(estacionales and fecha_ciclo.month in (6, 12)),
        "ciclos_estacionales": Decimal(len(estacionales)),
        "ingreso_regular_referencia": regular or ZERO,
        "aguinaldo_incluido": aguinaldo,
    }


def backtest_ciclo(db: Session, usuario: Usuario, fecha_ciclo: date) -> dict[str, Decimal | str | bool]:
    """Compara estimadores estadísticos históricos (mediana deflactada vs promedio simple nominal),
    no las proyecciones reales completas del sistema que incluyen compromisos y ponderación de ritmo."""
    data = _carga(db, usuario)
    inicio, fin = get_ciclo_fechas(usuario, fecha_ciclo)
    anteriores = _ciclos_anteriores(usuario, fecha_ciclo, 6)
    historicos = []
    historicos_nominales = []
    historicos_por_ciclo = []
    for ciclo_inicio, ciclo_fin in anteriores:
        gasto_hoy = gasto_ciclo(data["txs"], ciclo_inicio, ciclo_fin, fin, data["ipc"], Moneda.ARS)
        gasto_nominal = gasto_ciclo(data["txs"], ciclo_inicio, ciclo_fin, ciclo_fin, data["ipc"], Moneda.ARS)
        historicos_por_ciclo.append((ciclo_inicio, ciclo_fin, gasto_hoy, gasto_nominal))
        historicos.append(gasto_hoy.deflactado)
        historicos_nominales.append(gasto_nominal.nominal)
    historicos = [valor for valor in historicos if valor > ZERO]
    historicos_nominales = [valor for valor in historicos_nominales if valor > ZERO]
    real = gasto_ciclo(data["txs"], inicio, fin, fin, data["ipc"], Moneda.ARS).deflactado
    estacionales = [gasto.deflactado for _, ciclo_fin, gasto, _ in historicos_por_ciclo if ciclo_fin.month == fecha_ciclo.month and gasto.deflactado > ZERO]
    nuevo = (mediana(estacionales) if estacionales and fecha_ciclo.month in (6, 12) else mediana(historicos)) or ZERO
    viejo_nominal = sum(historicos_nominales, ZERO) / Decimal(len(historicos_nominales)) if historicos_nominales else ZERO
    viejo = deflactar_monto(viejo_nominal, fin, fin, data["ipc"], Moneda.ARS).monto
    error_nuevo = abs(nuevo - real) / real * Decimal("100") if real else ZERO
    error_viejo = abs(viejo - real) / real * Decimal("100") if real else ZERO
    dispersion = max(
        mad(historicos) or ZERO,
        ((percentil(historicos, Decimal("0.75")) or nuevo) - (percentil(historicos, Decimal("0.25")) or nuevo)) / Decimal("2"),
        ((percentil(historicos, Decimal("0.90")) or nuevo) - (percentil(historicos, Decimal("0.10")) or nuevo)) / Decimal("2"),
    )
    piso = max(ZERO, nuevo - dispersion)
    techo = nuevo + dispersion
    return {"ciclo": fin.strftime("%Y-%m"), "real": real, "nuevo": nuevo, "error_nuevo": error_nuevo, "viejo": viejo, "error_viejo": error_viejo, "rango_piso": piso, "rango_techo": techo, "rango_contiene_real": piso <= real <= techo}
