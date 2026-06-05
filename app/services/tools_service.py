import logging
import requests
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.tools import IPCCache
from app.schemas.tools import InstallmentConvenienceRequest

logger = logging.getLogger("tools_service")


def get_current_ipc(db: Session) -> IPCCache:
    """
    Obtiene el último IPC mensual de la base de datos (si tiene menos de 24 horas)
    o consulta las APIs externas en orden (argly -> datos.gob.ar),
    cacheando el resultado si tiene éxito.
    """
    ahora = datetime.now(timezone.utc)
    limite_cache = ahora - timedelta(hours=24)

    # 1. Verificar caché en base de datos
    logger.info("Buscando IPC en caché de base de datos...")
    ultimo_cache = db.execute(
        select(IPCCache).order_by(IPCCache.fecha_actualizacion.desc())
    ).scalars().first()

    if ultimo_cache and ultimo_cache.fecha_actualizacion >= limite_cache and not ultimo_cache.es_estimado:
        logger.info(f"Caché válido encontrado: {ultimo_cache.valor_mensual}% ({ultimo_cache.fecha_dato})")
        return ultimo_cache

    # 2. Intentar API Argly (Principal)
    try:
        logger.info("Consultando API principal: api.argly.com.ar...")
        response = requests.get("https://api.argly.com.ar/v1/ipc", timeout=10)
        response.raise_for_status()
        data = response.json()
        ipc_data = data.get("data", {})
        
        valor = float(ipc_data["indice_ipc"])
        anio = int(ipc_data["anio"])
        mes = int(ipc_data["mes"])
        fecha_dato = f"{anio}-{mes:02d}"

        logger.info(f"Dato obtenido exitosamente de Argly: {valor}% para {fecha_dato}")
        
        # Guardar/Actualizar en base de datos
        nuevo_ipc = IPCCache(
            valor_mensual=valor,
            fecha_dato=fecha_dato,
            fecha_actualizacion=ahora,
            fuente="argly",
            es_estimado=False
        )
        db.add(nuevo_ipc)
        db.commit()
        db.refresh(nuevo_ipc)
        return nuevo_ipc
    except Exception as e:
        logger.error(f"Error al consultar api.argly.com.ar: {str(e)}")

    # 3. Intentar API Datos Gob (Fallback)
    try:
        logger.info("Consultando API fallback: apis.datos.gob.ar...")
        url_fallback = (
            "https://apis.datos.gob.ar/series/api/series/"
            "?ids=148.3_INIVELNAL_DICI_M_26&limit=2&sort=desc&format=json"
        )
        response = requests.get(url_fallback, timeout=10)
        response.raise_for_status()
        data = response.json()
        series_data = data.get("data", [])
        
        if len(series_data) >= 2:
            latest_point = series_data[0]
            prev_point = series_data[1]
            
            date_str = latest_point[0]  # "YYYY-MM-DD"
            val_latest = float(latest_point[1])
            val_prev = float(prev_point[1])
            
            # Calcular variación mensual
            valor = round(((val_latest / val_prev) - 1.0) * 100, 2)
            fecha_dato = date_str[:7]  # "YYYY-MM"
            
            logger.info(f"Dato calculado exitosamente de Datos Gob: {valor}% para {fecha_dato}")
            
            nuevo_ipc = IPCCache(
                valor_mensual=valor,
                fecha_dato=fecha_dato,
                fecha_actualizacion=ahora,
                fuente="datos.gob.ar",
                es_estimado=False
            )
            db.add(nuevo_ipc)
            db.commit()
            db.refresh(nuevo_ipc)
            return nuevo_ipc
    except Exception as e:
        logger.error(f"Error al consultar apis.datos.gob.ar: {str(e)}")

    # 4. Fallback final
    if ultimo_cache:
        logger.warning(
            f"Fallo la obtención externa. Retornando el último caché disponible "
            f"({ultimo_cache.valor_mensual}% - {ultimo_cache.fecha_dato}) marcado como estimado."
        )
        # Retornamos el último caché marcándolo como estimado para alertar al front
        ultimo_cache.es_estimado = True
        ultimo_cache.fecha_actualizacion = ahora
        db.commit()
        db.refresh(ultimo_cache)
        return ultimo_cache

    # Si nunca hubo caché, crear uno por defecto estimado
    logger.warning("No hay caché disponible. Retornando valor de IPC por defecto (3.0%) como estimado.")
    mes_anterior = datetime.now() - timedelta(days=30)
    fecha_dato_default = mes_anterior.strftime("%Y-%m")
    
    nuevo_ipc = IPCCache(
        valor_mensual=3.0,
        fecha_dato=fecha_dato_default,
        fecha_actualizacion=ahora,
        fuente="default",
        es_estimado=True
    )
    db.add(nuevo_ipc)
    db.commit()
    db.refresh(nuevo_ipc)
    return nuevo_ipc


def calcular_conveniencia_cuotas(req: InstallmentConvenienceRequest) -> dict:
    """
    Calcula si conviene pagar en cuotas o de contado bajo la inflación provista.
    """
    tasa = req.inflacion_mensual / 100  # Convertir a decimal
    monto_cuota = req.precio_total_cuotas / req.cantidad_cuotas
    
    detalle_cuotas = []
    costo_real_total = 0.0
    
    for n in range(1, req.cantidad_cuotas + 1):
        valor_presente = monto_cuota / ((1 + tasa) ** n)
        costo_real_total += valor_presente
        detalle_cuotas.append({
            "mes": n,
            "cuota_nominal": round(monto_cuota, 2),
            "cuota_valor_presente": round(valor_presente, 2)
        })
    
    diferencia = req.precio_contado - costo_real_total
    porcentaje_diferencia = (abs(diferencia) / req.precio_contado) * 100
    
    UMBRAL_INDIFERENCIA = 1.0  # Si la diferencia es menor al 1%, es indiferente
    
    if porcentaje_diferencia < UMBRAL_INDIFERENCIA:
        resultado = "indiferente"
    elif diferencia > 0:
        resultado = "conviene_cuotas"  # El costo real en cuotas es menor al contado
    else:
        resultado = "conviene_contado"  # El costo real en cuotas es mayor al contado
    
    return {
        "resultado": resultado,
        "precio_contado": round(req.precio_contado, 2),
        "precio_total_cuotas_nominal": round(req.precio_total_cuotas, 2),
        "costo_real_cuotas": round(costo_real_total, 2),
        "ahorro_real": round(abs(diferencia), 2),
        "porcentaje_ahorro": round(porcentaje_diferencia, 2),
        "monto_cuota": round(monto_cuota, 2),
        "cantidad_cuotas": req.cantidad_cuotas,
        "inflacion_mensual_usada": req.inflacion_mensual,
        "detalle_por_mes": detalle_cuotas
    }
