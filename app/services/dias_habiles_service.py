from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional
import httpx
import logging

logger = logging.getLogger(__name__)

# Cache en memoria: { año: [date, date, ...] }
_feriados_cache: dict[int, list[date]] = {}


async def obtener_feriados_argentina(anio: int) -> list[date]:
    """
    Obtiene los feriados argentinos para un año dado.
    Fuente: apis.datos.gob.ar (ya usada en tools_service para IPC).
    Cachea en memoria por año.
    """
    if anio in _feriados_cache:
        return _feriados_cache[anio]

    url = f"https://apis.datos.gob.ar/servicios/feriados/{anio}/"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            feriados = []
            for item in data:
                try:
                    fecha_str = item.get("fecha", "")
                    if fecha_str:
                        feriados.append(date.fromisoformat(fecha_str))
                except (ValueError, AttributeError):
                    continue
            _feriados_cache[anio] = feriados
            return feriados
    except Exception as e:
        logger.warning(f"No se pudieron obtener feriados para {anio}: {e}")
        return []


def es_dia_habil(fecha: date, feriados: list[date]) -> bool:
    """
    Retorna True si la fecha es un día hábil 
    (no sábado, no domingo, no feriado argentino).
    """
    if fecha.weekday() >= 5:  # 5=sábado, 6=domingo
        return False
    if fecha in feriados:
        return False
    return True


async def calcular_fecha_cobro(dia_nominal: int, mes: int, anio: int) -> date:
    """
    Dado un día nominal (ej: 28), mes y año, calcula la fecha 
    real de cobro aplicando la regla de día hábil anterior.
    
    Si el día nominal no existe en ese mes (ej: día 31 en febrero),
    usa el último día del mes.
    
    Retrocede hasta encontrar un día hábil.
    Nunca retrocede más de 7 days (límite de seguridad).
    """
    # Ajustar si el día no existe en el mes
    ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
    dia_real = min(dia_nominal, ultimo_dia_mes)
    
    fecha = date(anio, mes, dia_real)
    feriados = await obtener_feriados_argentina(anio)
    
    intentos = 0
    while not es_dia_habil(fecha, feriados) and intentos < 7:
        fecha -= timedelta(days=1)
        intentos += 1
        # Si el año cambia al retroceder, cargar feriados del año anterior
        if fecha.year != anio:
            feriados_prev = await obtener_feriados_argentina(fecha.year)
            feriados = feriados_prev + feriados
    
    return fecha


async def calcular_proxima_fecha_cobro(dia_nominal: int) -> date:
    """
    Calcula la próxima fecha de cobro a partir de hoy.
    Usa el mes actual; si ya pasó ese día en el mes actual,
    calcula para el mes siguiente.
    """
    hoy = date.today()
    mes = hoy.month
    anio = hoy.year
    
    fecha_este_mes = await calcular_fecha_cobro(dia_nominal, mes, anio)
    
    if fecha_este_mes >= hoy:
        return fecha_este_mes
    
    # Ya pasó este mes, calcular para el mes siguiente
    if mes == 12:
        return await calcular_fecha_cobro(dia_nominal, 1, anio + 1)
    else:
        return await calcular_fecha_cobro(dia_nominal, mes + 1, anio)
