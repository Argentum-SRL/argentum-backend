from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional, Any
import httpx
import structlog

logger = structlog.get_logger("dias_habiles")

# Cache en memoria a nivel proceso: { año: [date, date, ...] }
_feriados_cache: dict[int, list[date]] = {}


def _obtener_feriados_db_sync(anio: int) -> list[date]:
    """Obtiene la lista de feriados para un año desde la tabla persistente FeriadoAR."""
    from app.core.database import SessionLocal
    from app.models.feriado import FeriadoAR
    from sqlalchemy import select

    db = SessionLocal()
    try:
        stmt = select(FeriadoAR.fecha).where(FeriadoAR.anio == anio).order_by(FeriadoAR.fecha)
        res = db.execute(stmt).scalars().all()
        return list(res)
    except Exception as e:
        logger.error("Error al leer feriados desde base de datos", anio=anio, error=str(e))
        return []
    finally:
        db.close()


def _guardar_feriados_en_db(items: list[dict[str, Any]]) -> None:
    """Guarda o actualiza feriados en la base de datos (upsert por fecha)."""
    from app.core.database import SessionLocal
    from app.models.feriado import FeriadoAR
    from sqlalchemy import select

    if not items:
        return

    db = SessionLocal()
    try:
        for item in items:
            f_obj = db.execute(
                select(FeriadoAR).where(FeriadoAR.fecha == item["fecha"])
            ).scalar_one_or_none()

            if f_obj:
                f_obj.nombre = item.get("nombre")
                f_obj.anio = item["anio"]
            else:
                f_obj = FeriadoAR(
                    fecha=item["fecha"],
                    nombre=item.get("nombre"),
                    anio=item["anio"],
                )
                db.add(f_obj)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Error persistiendo feriados en base de datos", error=str(e))
    finally:
        db.close()


def _get_feriados_cached_sync(anio: int) -> list[date]:
    """
    Retorna feriados desde la caché en memoria o BD de forma sincrónica.
    Si no existen datos para el año, emite advertencia explícita.
    """
    if anio in _feriados_cache and _feriados_cache[anio]:
        return _feriados_cache[anio]

    # Intentar cargar desde BD
    db_fechas = _obtener_feriados_db_sync(anio)
    if db_fechas:
        _feriados_cache[anio] = db_fechas
        return db_fechas

    # Si no está en cache ni en BD
    logger.warning(
        "ajuste de feriados no disponible para el año, solo se ajustó fin de semana",
        anio=anio,
    )
    _feriados_cache[anio] = []
    return []


async def obtener_feriados_argentina(anio: int, forzar_refresh: bool = False) -> list[date]:
    """
    Obtiene los feriados argentinos para un año dado.
    1. Si ya está en memoria (y no es refresh forzado), lo devuelve.
    2. Si está en BD FeriadoAR (y no es refresh forzado), puebla la cache y lo devuelve.
    3. Si no está o se fuerza refresh, consulta la API de datos.gob.ar, persiste en BD y actualiza cache.
    """
    if not forzar_refresh and anio in _feriados_cache and _feriados_cache[anio]:
        return _feriados_cache[anio]

    if not forzar_refresh:
        db_fechas = _obtener_feriados_db_sync(anio)
        if db_fechas:
            _feriados_cache[anio] = db_fechas
            return db_fechas

    url = f"https://api.argentinadatos.com/v1/feriados/{anio}"
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "Argentum-API/1.0"}) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            feriados_fechas: list[date] = []
            feriados_items: list[dict[str, Any]] = []

            for item in data:
                try:
                    tipo = str(item.get("tipo", "")).lower()
                    # Excluir días no laborables optativos si existiesen en la respuesta
                    if tipo in ("nolaborable", "no_laborable"):
                        continue

                    fecha_str = item.get("fecha", "")
                    if fecha_str:
                        f_date = date.fromisoformat(fecha_str)
                        nombre = item.get("nombre") or "Feriado"
                        feriados_fechas.append(f_date)
                        feriados_items.append({
                            "fecha": f_date,
                            "nombre": nombre,
                            "anio": anio,
                        })
                except (ValueError, AttributeError):
                    continue

            if feriados_fechas:
                feriados_ordenados = sorted(list(set(feriados_fechas)))
                _guardar_feriados_en_db(feriados_items)
                _feriados_cache[anio] = feriados_ordenados
                return feriados_ordenados
            else:
                logger.warning("Respuesta vacía de API de feriados", anio=anio)
                return _feriados_cache.get(anio, [])
    except Exception as e:
        logger.error("Error al consultar API externa de feriados", anio=anio, error=str(e))
        db_fechas = _obtener_feriados_db_sync(anio)
        if db_fechas:
            _feriados_cache[anio] = db_fechas
            return db_fechas
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


def ajustar_fecha_habil_sync(fecha: date, direccion: str | None = "anterior") -> date:
    """
    Ajusta una fecha nominal para que caiga en un día hábil según la dirección.
    - 'anterior': retrocede hasta encontrar un día hábil (lunes a viernes no feriado).
    - 'posterior': avanza hasta encontrar un día hábil.
    Límite de seguridad: máximo 7 intentos.
    """
    dir_norm = "posterior" if str(direccion).lower() == "posterior" else "anterior"
    delta_dias = 1 if dir_norm == "posterior" else -1

    feriados = _get_feriados_cached_sync(fecha.year)

    intentos = 0
    while not es_dia_habil(fecha, feriados) and intentos < 7:
        fecha += timedelta(days=delta_dias)
        intentos += 1
        if fecha.year not in _feriados_cache:
            feriados = _get_feriados_cached_sync(fecha.year) + feriados

    return fecha


def calcular_fecha_cobro_sync(
    dia_nominal: int, mes: int, anio: int, direccion: str | None = "anterior"
) -> date:
    """
    Versión sincrónica de cálculo de fecha de cobro para modo DIA_FIJO.
    Ajusta si el día no existe en el mes y luego aplica la regla de día hábil
    según la dirección ('anterior' o 'posterior').
    """
    ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
    dia_real = min(dia_nominal, ultimo_dia_mes)
    fecha = date(anio, mes, dia_real)
    return ajustar_fecha_habil_sync(fecha, direccion=direccion)


async def calcular_fecha_cobro(
    dia_nominal: int, mes: int, anio: int, direccion: str | None = "anterior"
) -> date:
    """
    Dado un día nominal, mes y año, calcula la fecha real aplicando
    la regla de día hábil (anterior o posterior).
    """
    ultimo_dia_mes = calendar.monthrange(anio, mes)[1]
    dia_real = min(dia_nominal, ultimo_dia_mes)

    fecha = date(anio, mes, dia_real)
    feriados = await obtener_feriados_argentina(anio)

    dir_norm = "posterior" if str(direccion).lower() == "posterior" else "anterior"
    delta_dias = 1 if dir_norm == "posterior" else -1

    intentos = 0
    while not es_dia_habil(fecha, feriados) and intentos < 7:
        fecha += timedelta(days=delta_dias)
        intentos += 1
        if fecha.year != anio:
            feriados_prev = await obtener_feriados_argentina(fecha.year)
            feriados = feriados_prev + feriados

    return fecha


async def calcular_proxima_fecha_cobro(
    dia_nominal: int, direccion: str | None = "anterior"
) -> date:
    """
    Calcula la próxima fecha de cobro a partir de hoy.
    """
    hoy = date.today()
    mes = hoy.month
    anio = hoy.year

    fecha_este_mes = await calcular_fecha_cobro(dia_nominal, mes, anio, direccion=direccion)

    if fecha_este_mes >= hoy:
        return fecha_este_mes

    if mes == 12:
        return await calcular_fecha_cobro(dia_nominal, 1, anio + 1, direccion=direccion)
    else:
        return await calcular_fecha_cobro(dia_nominal, mes + 1, anio, direccion=direccion)


async def asegurar_feriados_cargados() -> None:
    """
    Job de refresh diario: verifica y asegura que los feriados del año actual
    y año actual + 1 estén persistidos y en cache.
    """
    hoy = date.today()
    anios = [hoy.year, hoy.year + 1]

    for anio in anios:
        try:
            feriados = await obtener_feriados_argentina(anio, forzar_refresh=False)
            if not feriados:
                # Si falló o no tenía, intentar con forzar_refresh
                feriados = await obtener_feriados_argentina(anio, forzar_refresh=True)
            if not feriados:
                logger.error("Job de feriados: no se pudieron cargar feriados para el año", anio=anio)
            else:
                logger.info("Job de feriados: feriados verificados/cargados", anio=anio, count=len(feriados))
        except Exception as e:
            logger.error("Job de feriados: error procesando año", anio=anio, error=str(e))


async def recargar_feriados_anio(anio: int) -> dict[str, Any]:
    """
    Función para endpoint administrativo: fuerza la recarga de feriados desde la API externa.
    """
    try:
        feriados = await obtener_feriados_argentina(anio, forzar_refresh=True)
        if not feriados:
            return {
                "success": False,
                "anio": anio,
                "cantidad": 0,
                "feriados": [],
                "error": "No se encontraron feriados o la API externa no respondió.",
            }
        return {
            "success": True,
            "anio": anio,
            "cantidad": len(feriados),
            "feriados": [f.isoformat() for f in feriados],
            "error": None,
        }
    except Exception as e:
        logger.error("Error en recargar_feriados_anio", anio=anio, error=str(e))
        return {
            "success": False,
            "anio": anio,
            "cantidad": 0,
            "feriados": [],
            "error": str(e),
        }
