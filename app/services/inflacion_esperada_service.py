# -*- coding: utf-8 -*-
"""
app/services/inflacion_esperada_service.py — Servicio de inflación esperada (REM del BCRA e INDEC).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import io
import logging
from uuid import uuid4

import httpx
import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.expectativa_inflacion import ExpectativaInflacion
from app.services.alerta_service import enviar_alerta_admin
from app.models.tools import IPCCache
from app.services.tools_service import _calcular_y_asignar_valor_mensual

logger = logging.getLogger("inflacion_esperada_service")

MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
)


@dataclass(frozen=True)
class InflacionEsperada:
    tasas: list[Decimal]
    fuente_texto: str
    es_respaldo: bool


def leer_rem_xlsx(contenido: bytes) -> tuple[str, list[tuple[str, Decimal]], Decimal | None]:
    """
    Lee la tabla de 'Precios minoristas (IPC nivel general...)' del Excel del REM.
    Retorna: (relevamiento 'AAAA-MM', mensuales: [(periodo, mediana), ...], prox12: Decimal | None).
    """
    wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
    if "Cuadros de resultados" not in wb.sheetnames:
        raise ValueError("No se encontró la hoja 'Cuadros de resultados' en el archivo del REM")

    sheet = wb["Cuadros de resultados"]
    
    # Buscar el encabezado de Precios minoristas IPC nivel general
    start_row = None
    for r in range(1, 40):
        val = sheet.cell(row=r, column=2).value
        if val and "precios minoristas" in str(val).lower() and "ipc nivel general" in str(val).lower():
            start_row = r
            break

    if start_row is None:
        raise ValueError("No se encontró la tabla de Precios minoristas (IPC nivel general) en el REM")

    # La fila siguiente al título (start_row + 1) tiene las columnas: Período, Referencia, Mediana...
    # Las filas de datos empiezan en start_row + 2
    mensuales: list[tuple[str, Decimal]] = []
    prox12: Decimal | None = None
    relevamiento: str | None = None

    for r in range(start_row + 2, start_row + 25):
        periodo_val = sheet.cell(row=r, column=2).value
        mediana_val = sheet.cell(row=r, column=4).value

        if periodo_val is None:
            if mensuales:
                break
            continue

        if isinstance(periodo_val, (datetime, date)):
            if prox12 is not None:
                break
            periodo_str = periodo_val.strftime("%Y-%m")
            if mediana_val is not None:
                med_dec = Decimal(str(mediana_val))
                mensuales.append((periodo_str, med_dec))
                if relevamiento is None:
                    relevamiento = periodo_str
        elif isinstance(periodo_val, str):
            val_clean = periodo_val.strip().lower()
            if "próx. 12" in val_clean or "prox. 12" in val_clean or "próximos 12" in val_clean:
                if mediana_val is not None:
                    prox12 = Decimal(str(mediana_val))
            elif any(val_clean.startswith(x) for x in ["próx. 24", "año ", "ano ", "año", "ano"]):
                continue
            else:
                if mensuales:
                    break

    if relevamiento is None:
        # Intentar extraer del título general (fila 2 celda B2: "... - Septiembre 2026")
        title_val = str(sheet.cell(row=2, column=2).value or "")
        for i, m_name in enumerate(MESES, 1):
            if m_name in title_val.lower():
                import re
                m_year = re.search(r"\b(20\d\d)\b", title_val)
                if m_year:
                    relevamiento = f"{m_year.group(1)}-{i:02d}"
                    break

    if relevamiento is None:
        relevamiento = "2026-09"

    return relevamiento, mensuales, prox12


def tasas_desde_rem(
    mensuales: list[tuple[str, Decimal]],
    prox12: Decimal | None,
    desde: str,
    meses: int,
) -> list[Decimal] | None:
    """
    Calcula una lista de tasas mensuales proyectadas a partir de los datos del REM.
    Para cada mes desde 'desde':
      - la mediana de ese mes si está;
      - si no, la tasa mensual compuesta equivalente a prox12 = ((1 + prox12/100)^(1/12) - 1) * 100;
      - si no hay prox12, la última tasa mensual disponible;
      - si no hay mensuales ni prox12: None.
    Valores redondeados a 4 decimales con ROUND_HALF_UP.
    Pura.
    """
    if not mensuales and prox12 is None:
        return None

    mensuales_dict = {m: v for m, v in mensuales}
    y, m = map(int, desde.split("-"))

    tasa_eq_prox12 = None
    if prox12 is not None:
        # ((1 + prox12/100)**(1/12) - 1) * 100
        factor = float(Decimal("1") + Decimal(str(prox12)) / Decimal("100"))
        eq_float = ((factor ** (1.0 / 12.0)) - 1.0) * 100.0
        tasa_eq_prox12 = Decimal(str(eq_float)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    ultima_mensual = None
    if mensuales:
        ultima_mensual = Decimal(str(mensuales[-1][1])).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    res: list[Decimal] = []
    for _ in range(meses):
        periodo_actual = f"{y:04d}-{m:02d}"
        if periodo_actual in mensuales_dict:
            tasa = Decimal(str(mensuales_dict[periodo_actual])).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            res.append(tasa)
        elif tasa_eq_prox12 is not None:
            res.append(tasa_eq_prox12)
        elif ultima_mensual is not None:
            res.append(ultima_mensual)
        else:
            return None

        # Avanzar al siguiente mes
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1

    return res


def _mes_anterior(y: int, m: int) -> tuple[int, int]:
    if m == 1:
        return y - 1, 12
    return y, m - 1


def bajar_rem(db: Session, hoy: date) -> None:
    """
    Descarga el REM del BCRA una vez por mes y actualiza la tabla expectativas_inflacion.
    Nunca lanza excepciones.
    """
    try:
        # Mes anterior a hoy
        y_ant, m_ant = _mes_anterior(hoy.year, hoy.month)
        relevamiento_esperado = f"{y_ant:04d}-{m_ant:02d}"

        # 1. Si ya hay filas de ese relevamiento, no hace nada
        count = db.query(ExpectativaInflacion).filter(
            ExpectativaInflacion.relevamiento == relevamiento_esperado
        ).count()
        if count > 0:
            return

        # 2. Probar URL de ese mes y luego la del mes anterior
        y_ant2, m_ant2 = _mes_anterior(y_ant, m_ant)
        relevamiento_anterior = f"{y_ant2:04d}-{m_ant2:02d}"

        urls = [
            f"https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/relevamiento-expectativas-mercado-tablas-{relevamiento_esperado}.xlsx",
            f"https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/relevamiento-expectativas-mercado-tablas-{relevamiento_anterior}.xlsx",
        ]

        contenido_descargado = None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        for u in urls:
            try:
                with httpx.Client(timeout=20.0, verify=False, follow_redirects=True, headers=headers) as client:
                    resp = client.get(u)
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        contenido_descargado = resp.content
                        break
            except Exception as e:
                logger.warning(f"Error descargando {u}: {e}")

        if contenido_descargado:
            relevamiento, mensuales, prox12 = leer_rem_xlsx(contenido_descargado)
            ahora = datetime.now(timezone.utc)
            # Guardar/upsert mensuales
            for periodo_str, med_val in mensuales:
                existente = db.query(ExpectativaInflacion).filter(
                    ExpectativaInflacion.relevamiento == relevamiento,
                    ExpectativaInflacion.periodo == periodo_str,
                ).first()
                if existente:
                    existente.valor = med_val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    existente.fecha_actualizacion = ahora
                else:
                    db.add(ExpectativaInflacion(
                        id=uuid4(),
                        relevamiento=relevamiento,
                        periodo=periodo_str,
                        valor=med_val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                        fuente="REM BCRA",
                        fecha_actualizacion=ahora,
                    ))

            # Guardar/upsert prox12
            if prox12 is not None:
                existente_prox12 = db.query(ExpectativaInflacion).filter(
                    ExpectativaInflacion.relevamiento == relevamiento,
                    ExpectativaInflacion.periodo == "prox12",
                ).first()
                if existente_prox12:
                    existente_prox12.valor = prox12.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    existente_prox12.fecha_actualizacion = ahora
                else:
                    db.add(ExpectativaInflacion(
                        id=uuid4(),
                        relevamiento=relevamiento,
                        periodo="prox12",
                        valor=prox12.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                        fuente="REM BCRA",
                        fecha_actualizacion=ahora,
                    ))

            db.commit()
            logger.info(f"REM relevamiento {relevamiento} guardado exitosamente.")
            return

        # 3. Si no se pudo descargar: control de alerta admin si hoy >= 10
        if hoy.day >= 10:
            latest = db.query(ExpectativaInflacion).order_by(
                ExpectativaInflacion.relevamiento.desc()
            ).first()

            alerta_necesaria = False
            dias_pasados = None
            if not latest:
                alerta_necesaria = True
            else:
                ry, rm = map(int, latest.relevamiento.split("-"))
                last_day = calendar.monthrange(ry, rm)[1]
                fecha_fin_relevamiento = date(ry, rm, last_day)
                dias_pasados = (hoy - fecha_fin_relevamiento).days
                if dias_pasados > 45:
                    alerta_necesaria = True

            if alerta_necesaria:
                cuerpo = (
                    f"No se pudo descargar el REM del BCRA para hoy {hoy}."
                )
                if dias_pasados is not None:
                    cuerpo += f" Pasaron {dias_pasados} días desde el último relevamiento guardado ({latest.relevamiento})."
                else:
                    cuerpo += " No hay ningún relevamiento previo guardado."

                enviar_alerta_admin(
                    asunto="REM del BCRA: no pude bajarlo",
                    cuerpo=cuerpo,
                    clave="rem_sin_datos",
                )

    except Exception as e:
        logger.error(f"Error inesperado en bajar_rem: {e}", exc_info=True)


def inflacion_esperada(db: Session, hoy: date | None = None, meses: int = 12) -> InflacionEsperada | None:
    """
    Obtiene la inflación mensual esperada para los próximos N meses a partir del mes siguiente a hoy.
    Usa el REM si tiene como máximo 2 meses de antigüedad respecto a hoy.
    Si no, recurre al último IPC del INDEC y avisa al admin.
    """
    if hoy is None:
        from app.utils.fecha import hoy_argentina
        hoy = hoy_argentina()

    # Mes siguiente a hoy
    if hoy.month == 12:
        desde_y, desde_m = hoy.year + 1, 1
    else:
        desde_y, desde_m = hoy.year, hoy.month + 1
    desde = f"{desde_y:04d}-{desde_m:02d}"

    # Buscar último relevamiento disponible
    latest = db.query(ExpectativaInflacion).order_by(
        ExpectativaInflacion.relevamiento.desc()
    ).first()

    if latest:
        rel_y, rel_m = map(int, latest.relevamiento.split("-"))
        diff_meses = (hoy.year * 12 + hoy.month) - (rel_y * 12 + rel_m)

        if diff_meses <= 2:
            filas = db.query(ExpectativaInflacion).filter(
                ExpectativaInflacion.relevamiento == latest.relevamiento
            ).all()

            mensuales: list[tuple[str, Decimal]] = []
            prox12: Decimal | None = None
            for f in filas:
                if f.periodo == "prox12":
                    prox12 = f.valor
                else:
                    mensuales.append((f.periodo, f.valor))

            mensuales.sort(key=lambda x: x[0])
            tasas = tasas_desde_rem(mensuales, prox12, desde=desde, meses=meses)
            if tasas is not None:
                mes_nombre = MESES[rel_m - 1]
                return InflacionEsperada(
                    tasas=tasas,
                    fuente_texto=f"REM de {mes_nombre}",
                    es_respaldo=False,
                )

    # Respaldo: INDEC (solo lectura de base, sin llamadas de red ni commit)
    ultimo_ipc = db.execute(
        select(IPCCache).order_by(IPCCache.fecha_dato.desc())
    ).scalars().first()
    ipc_cache = _calcular_y_asignar_valor_mensual(db, ultimo_ipc) if ultimo_ipc else None
    if ipc_cache and ipc_cache.valor_mensual is not None:
        tasa_mensual = Decimal(str(ipc_cache.valor_mensual)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        tasas = [tasa_mensual] * meses
        ipc_y, ipc_m = map(int, ipc_cache.fecha_dato.split("-"))
        mes_nombre = MESES[ipc_m - 1]

        enviar_alerta_admin(
            asunto="Inflación esperada: uso el INDEC",
            cuerpo=f"No hay datos del REM del BCRA actualizados a la fecha {hoy}. Se utilizó el IPC del INDEC de {mes_nombre} ({ipc_cache.valor_mensual}%).",
            clave="rem_respaldo",
        )

        return InflacionEsperada(
            tasas=tasas,
            fuente_texto=f"inflación INDEC de {mes_nombre}",
            es_respaldo=True,
        )

    return None

