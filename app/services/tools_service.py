import logging
import requests
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, or_

from app.models.tools import IPCCache
from app.schemas.tools import InstallmentConvenienceRequest
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, MetodoPago
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.services.dashboard_service import get_ciclo_fechas
from app.services.suscripcion_service import obtener_total_mensual


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
    
    if req.tiene_interes and req.tna is not None:
        i = (req.tna / 100) / 12  # tasa mensual decimal
        if i == 0:
            monto_cuota = req.precio_contado / req.cantidad_cuotas
        else:
            monto_cuota = req.precio_contado * (i * (1 + i) ** req.cantidad_cuotas) / ((1 + i) ** req.cantidad_cuotas - 1)
        precio_total_cuotas = monto_cuota * req.cantidad_cuotas
        interes_total = precio_total_cuotas - req.precio_contado
    else:
        monto_cuota = req.precio_total_cuotas / req.cantidad_cuotas
        precio_total_cuotas = req.precio_total_cuotas
        interes_total = 0.0

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
        "precio_total_cuotas_nominal": round(precio_total_cuotas, 2),
        "costo_real_cuotas": round(costo_real_total, 2),
        "ahorro_real": round(abs(diferencia), 2),
        "porcentaje_ahorro": round(porcentaje_diferencia, 2),
        "monto_cuota": round(monto_cuota, 2),
        "cantidad_cuotas": req.cantidad_cuotas,
        "inflacion_mensual_usada": req.inflacion_mensual,
        "detalle_por_mes": detalle_cuotas,
        "tiene_interes": req.tiene_interes,
        "tna_usada": req.tna if req.tiene_interes else None,
        "interes_total": round(interes_total, 2) if req.tiene_interes else None,
        "precio_total_cuotas_con_interes": round(precio_total_cuotas, 2) if req.tiene_interes else None
    }


def obtener_contexto_financiero(user_id: str, db: Session) -> dict:
    usuario = db.get(Usuario, user_id)
    if not usuario:
        return {}

    # Fecha actual ajustada (zona horaria -3 como en dashboard_service.py)
    hoy = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    fecha_inicio_curr, fecha_fin_curr = get_ciclo_fechas(usuario, hoy)

    # 1. Saldo disponible actual mediante servicio canónico
    from app.services.contexto_financiero_service import _calcular_saldo_disponible_sync
    disponible_res = _calcular_saldo_disponible_sync(db, usuario.id, Moneda.ARS)
    saldo_disponible = float(disponible_res["saldo_disponible"])
    carga_mensual_comprometida = float(disponible_res["cuotas_comprometidas"] + disponible_res["suscripciones_mensuales"])

    # 2. Ciclos con historia
    primera_tx = db.query(func.min(Transaccion.fecha)).filter(
        Transaccion.usuario_id == user_id
    ).scalar()

    if primera_tx is None:
        primera_tx = usuario.fecha_registro.date()

    fecha_fin_c1 = fecha_inicio_curr - timedelta(days=1)
    
    ciclos_con_historia = 0
    if primera_tx <= fecha_fin_c1:
        current_date = primera_tx
        while current_date <= fecha_fin_c1:
            inicio, fin = get_ciclo_fechas(usuario, current_date)
            if fin <= fecha_fin_c1:
                ciclos_con_historia += 1
                current_date = fin + timedelta(days=1)
            else:
                break

    # 3. Ingreso promedio mensual e ingresos de los ciclos
    # Rango de ciclos a consultar
    ingreso_es_estimacion_parcial = (ciclos_con_historia == 0)
    
    if ciclos_con_historia >= 1:
        # Buscamos las fechas de los ciclos pasados a promediar (máximo 3)
        # N = ciclos pasados disponibles
        n = min(ciclos_con_historia, 3)
        # El ciclo -1 termina en fecha_fin_c1
        end_range = fecha_fin_c1
        # Para encontrar el inicio del rango, retrocedemos N-1 ciclos
        start_date_c = end_range
        for _ in range(n - 1):
            inicio_c, _ = get_ciclo_fechas(usuario, start_date_c)
            start_date_c = inicio_c - timedelta(days=1)
        start_range, _ = get_ciclo_fechas(usuario, start_date_c)
        divisor = n
    else:
        # Usamos el ciclo actual incompleto como estimación parcial
        start_range = fecha_inicio_curr
        end_range = hoy
        divisor = 1

    # Incomes in range
    ingresos_total = db.query(func.sum(Transaccion.monto)).filter(
        Transaccion.usuario_id == user_id,
        Transaccion.tipo == TipoTransaccion.INGRESO,
        Transaccion.moneda == Moneda.ARS,
        or_(Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA, Transaccion.estado_verificacion == None),
        Transaccion.fecha >= start_range,
        Transaccion.fecha <= end_range
    ).scalar()

    tiene_ingresos_any = db.query(Transaccion.id).filter(
        Transaccion.usuario_id == user_id,
        Transaccion.tipo == TipoTransaccion.INGRESO,
        Transaccion.moneda == Moneda.ARS
    ).first() is not None

    if not tiene_ingresos_any:
        ingreso_promedio_mensual = None
    else:
        ingresos_sum = ingresos_total or Decimal("0")
        ingreso_promedio_mensual = float(ingresos_sum / Decimal(str(divisor)))

    # 4. Gasto promedio mensual variable
    # Gasto total variable en el mismo rango de ciclos
    # Excluyendo cuotas (installment parents: Transaccion.es_padre_cuotas == False)
    # y transferencias (que ya están fuera de Transaccion table, pero excluyendo egresos en tarjeta de crédito MetodoPago.CREDITO)
    gastos_total = db.query(func.sum(Transaccion.monto)).filter(
        Transaccion.usuario_id == user_id,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.moneda == Moneda.ARS,
        Transaccion.es_padre_cuotas == False,
        or_(Transaccion.metodo_pago != MetodoPago.CREDITO, Transaccion.metodo_pago == None),
        or_(Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA, Transaccion.estado_verificacion == None),
        Transaccion.fecha >= start_range,
        Transaccion.fecha <= end_range
    ).scalar() or Decimal("0")

    gasto_promedio_variable = float(gastos_total / Decimal(str(divisor)))

    # Margen libre mensual
    margen_libre_mensual = None
    if ingreso_promedio_mensual is not None:
        margen_libre_mensual = ingreso_promedio_mensual - carga_mensual_comprometida - gasto_promedio_variable

    return {
        "saldo_disponible": round(saldo_disponible, 2),
        "ingreso_promedio_mensual": round(ingreso_promedio_mensual, 2) if ingreso_promedio_mensual is not None else None,
        "ingreso_es_estimacion_parcial": ingreso_es_estimacion_parcial,
        "carga_mensual_comprometida": round(carga_mensual_comprometida, 2),
        "gasto_promedio_variable": round(gasto_promedio_variable, 2),
        "ciclos_con_historia": ciclos_con_historia,
        "margen_libre_mensual": round(margen_libre_mensual, 2) if margen_libre_mensual is not None else None
    }


def calcular_puede_permitirse(
    user_id: str,
    precio_total: float,
    modo: str,
    cantidad_cuotas: int,
    tiene_interes: bool,
    tna: float | None,
    ingreso_manual: float | None,
    db: Session
) -> dict:

    # 1. Obtener contexto financiero del usuario
    ctx = obtener_contexto_financiero(user_id, db)
    
    ingreso = ingreso_manual if ctx['ingreso_promedio_mensual'] is None else ctx['ingreso_promedio_mensual']
    saldo = ctx['saldo_disponible']
    carga_actual = ctx['carga_mensual_comprometida']
    gasto_variable = ctx['gasto_promedio_variable']

    if modo == 'contado':
        # ---- MODO CONTADO ----
        porcentaje_del_saldo = (precio_total / saldo * 100) if saldo > 0 else 999
        saldo_restante = saldo - precio_total
        porcentaje_del_ingreso = (precio_total / ingreso * 100) if ingreso else None
        
        # Semáforo
        if porcentaje_del_saldo <= 20:
            semaforo = 'verde'
            mensaje = 'Podés comprarlo sin comprometer tu estabilidad.'
        elif porcentaje_del_saldo <= 50:
            semaforo = 'amarillo'
            mensaje = 'Podés comprarlo, pero vas a usar una parte importante de tu saldo disponible.'
        elif porcentaje_del_saldo <= 100:
            semaforo = 'rojo'
            mensaje = 'Comprarlo te dejaría con muy poco margen disponible.'
        else:
            semaforo = 'negro'
            mensaje = 'No tenés suficiente saldo disponible para esta compra.'

        return {
            "modo": "contado",
            "precio_total": round(precio_total, 2),
            "saldo_disponible_actual": round(saldo, 2),
            "saldo_restante_post_compra": round(saldo_restante, 2),
            "porcentaje_del_saldo": round(porcentaje_del_saldo, 1),
            "porcentaje_del_ingreso_mensual": round(porcentaje_del_ingreso, 1) if porcentaje_del_ingreso else None,
            "semaforo": semaforo,
            "mensaje_principal": mensaje,
            "ingreso_promedio_usado": round(ingreso, 2) if ingreso else None,
            "ingreso_es_manual": ctx['ingreso_promedio_mensual'] is None,
        }

    else:
        # ---- MODO CUOTAS ----
        if tiene_interes and tna:
            # Amortización francesa
            i = (tna / 100) / 12  # tasa mensual decimal
            if i == 0:
                monto_cuota = precio_total / cantidad_cuotas
            else:
                monto_cuota = precio_total * (i * (1 + i) ** cantidad_cuotas) / ((1 + i) ** cantidad_cuotas - 1)
            precio_total_real = monto_cuota * cantidad_cuotas
            interes_total = precio_total_real - precio_total
        else:
            # Sin interés
            monto_cuota = precio_total / cantidad_cuotas
            precio_total_real = precio_total
            interes_total = 0.0

        nueva_carga_total = carga_actual + monto_cuota
        porcentaje_carga_sobre_ingreso = (nueva_carga_total / ingreso * 100) if ingreso else None
        nuevo_margen_libre = (ingreso - nueva_carga_total - gasto_variable) if ingreso else None
        
        # Semáforo basado en % de carga comprometida sobre ingreso
        if porcentaje_carga_sobre_ingreso is None:
            semaforo = 'gris'
            mensaje = 'Ingresá tu ingreso mensual para ver el análisis completo.'
        elif porcentaje_carga_sobre_ingreso <= 30:
            semaforo = 'verde'
            mensaje = 'La cuota entra bien en tu presupuesto mensual.'
        elif porcentaje_carga_sobre_ingreso <= 50:
            semaforo = 'amarillo'
            mensaje = 'La cuota es manejable, pero ya comprometés la mitad de tu ingreso en obligaciones fijas.'
        elif porcentaje_carga_sobre_ingreso <= 70:
            semaforo = 'rojo'
            mensaje = 'Con esta cuota, más del 70% de tu ingreso va a obligaciones fijas. Margen muy ajustado.'
        else:
            semaforo = 'negro'
            mensaje = 'Esta cuota supera tu capacidad de pago mensual según tu historial.'

        return {
            "modo": "cuotas",
            "precio_total": round(precio_total, 2),
            "monto_cuota": round(monto_cuota, 2),
            "cantidad_cuotas": cantidad_cuotas,
            "carga_mensual_previa": round(carga_actual, 2),
            "carga_mensual_nueva_total": round(nueva_carga_total, 2),
            "porcentaje_carga_sobre_ingreso": round(porcentaje_carga_sobre_ingreso, 1) if porcentaje_carga_sobre_ingreso else None,
            "margen_libre_post_compra": round(nuevo_margen_libre, 2) if nuevo_margen_libre else None,
            "semaforo": semaforo,
            "mensaje_principal": mensaje,
            "ingreso_promedio_usado": round(ingreso, 2) if ingreso else None,
            "ingreso_es_manual": ctx['ingreso_promedio_mensual'] is None,
            "gasto_variable_promedio": round(gasto_variable, 2),
            "tiene_interes": tiene_interes,
            "tna_usada": tna if tiene_interes else None,
            "precio_total_real": round(precio_total_real, 2),
            "interes_total": round(interes_total, 2),
        }

