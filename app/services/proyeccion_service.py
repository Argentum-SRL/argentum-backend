from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import ceil
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, select, desc, or_
from sqlalchemy.orm import Session, joinedload

from app.models.usuario import Usuario, Moneda
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion
from app.models.categoria import Categoria
from app.models.suscripcion import Suscripcion, EstadoSuscripcion
from app.models.cuota import Cuota
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.transaccion_recurrente import TransaccionRecurrente, EstadoTransaccionRecurrente, FrecuenciaTransaccionRecurrente, TipoTransaccionRecurrente
from app.services.dashboard_service import get_ciclo_fechas

def calcular_proyeccion(db: Session, usuario: Usuario) -> Dict[str, Any]:
    # Argentum usa UTC-3 para presentacion
    hoy = (datetime.now(timezone.utc) - timedelta(hours=3)).date()
    
    # Paso 1: Obtener ciclos completos y actual
    fecha_inicio_actual, fecha_fin_actual = get_ciclo_fechas(usuario, hoy)
    
    # Calcular ciclos anteriores (hasta 6)
    ciclos_anteriores = []
    fecha_referencia = fecha_inicio_actual - timedelta(days=1)
    for _ in range(6):
        inicio_ant, fin_ant = get_ciclo_fechas(usuario, fecha_referencia)
        # Un ciclo es completo si su fecha de fin es menor a hoy
        if fin_ant < hoy:
            ciclos_anteriores.append((inicio_ant, fin_ant))
        fecha_referencia = inicio_ant - timedelta(days=1)

    n_ciclos = len(ciclos_anteriores)

    # Paso 2: Calcular promedio historico por categoria
    # Agrupamos por categoria los egresos de cada ciclo completo
    totales_por_categoria_y_ciclo = []
    categorias_nombres = {}

    for inicio, fin in ciclos_anteriores:
        stmt = (
            select(Transaccion.categoria_id, Categoria.nombre, func.sum(Transaccion.monto).label("total"))
            .join(Categoria, Transaccion.categoria_id == Categoria.id)
            .where(
                and_(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha >= inicio,
                    Transaccion.fecha <= fin,
                    Transaccion.tipo == TipoTransaccion.EGRESO,
                    Transaccion.es_padre_cuotas == False,
                    or_(
                        Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                        Transaccion.estado_verificacion == None
                    )
                )
            )
            .group_by(Transaccion.categoria_id, Categoria.nombre)
        )
        res = db.execute(stmt).all()
        ciclo_data = {row.categoria_id: row.total for row in res}
        for row in res:
            categorias_nombres[row.categoria_id] = row.nombre
        totales_por_categoria_y_ciclo.append(ciclo_data)

    promedios_historicos = {}
    if n_ciclos > 0:
        # Para cada categoria que aparecio en algun ciclo
        todas_las_categorias = set()
        for ciclo in totales_por_categoria_y_ciclo:
            todas_las_categorias.update(ciclo.keys())
        
        for cat_id in todas_las_categorias:
            valores = [ciclo[cat_id] for ciclo in totales_por_categoria_y_ciclo if cat_id in ciclo]
            count_con_gasto = len(valores)
            suma_total = sum(valores)
            
            # Si aparecio en >= mitad de los ciclos, dividir por los ciclos donde aparecio
            # Si no, dividir por el total de ciclos (porque es esporadico)
            if count_con_gasto >= ceil(n_ciclos / 2):
                promedios_historicos[cat_id] = suma_total / Decimal(count_con_gasto)
            else:
                promedios_historicos[cat_id] = suma_total / Decimal(n_ciclos)

    # Paso 3: Calcular ritmo del ciclo actual
    dias_totales = (fecha_fin_actual - fecha_inicio_actual).days + 1
    dias_transcurridos = (hoy - fecha_inicio_actual).days + 1
    dias_restantes = dias_totales - dias_transcurridos

    if dias_restantes < 0:
        # El ciclo ya termino, devolver datos reales
        stmt = (
            select(Transaccion.categoria_id, Categoria.nombre, func.sum(Transaccion.monto).label("total"))
            .join(Categoria, Transaccion.categoria_id == Categoria.id)
            .where(
                and_(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha >= fecha_inicio_actual,
                    Transaccion.fecha <= fecha_fin_actual,
                    Transaccion.tipo == TipoTransaccion.EGRESO,
                    Transaccion.es_padre_cuotas == False,
                    or_(
                        Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                        Transaccion.estado_verificacion == None
                    )
                )
            )
            .group_by(Transaccion.categoria_id, Categoria.nombre)
        )
        res_actual = db.execute(stmt).all()
        gasto_total_real = sum(row.total for row in res_actual)
        
        # Ingresos reales
        ingresos_actuales = db.execute(
            select(func.sum(Transaccion.monto))
            .where(
                and_(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha >= fecha_inicio_actual,
                    Transaccion.fecha <= fecha_fin_actual,
                    Transaccion.tipo == TipoTransaccion.INGRESO,
                    or_(
                        Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                        Transaccion.estado_verificacion == None
                    )
                )
            )
        ).scalar() or Decimal("0")

        return {
            "periodo": {
                "fecha_inicio": fecha_inicio_actual.isoformat(),
                "fecha_fin": fecha_fin_actual.isoformat(),
                "dias_transcurridos": dias_totales,
                "dias_restantes": 0,
                "dias_totales": dias_totales
            },
            "gasto_proyectado_total": float(gasto_total_real),
            "balance_proyectado": float(ingresos_actuales - gasto_total_real),
            "ingresos_proyectados": float(ingresos_actuales),
            "certezas": {"cuotas_restantes": 0, "suscripciones_restantes": 0, "total": 0},
            "desglose_por_categoria": [],
            "nivel_confianza": "alto",
            "ciclos_analizados": n_ciclos,
            "pesos": {"historial": 1.0, "ciclo_actual": 0.0},
            "advertencias": ["El ciclo actual ya finalizó."]
        }

    # Gasto actual por categoria
    stmt_actual = (
        select(Transaccion.categoria_id, Categoria.nombre, func.sum(Transaccion.monto).label("total"))
        .join(Categoria, Transaccion.categoria_id == Categoria.id)
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha >= fecha_inicio_actual,
                Transaccion.fecha <= hoy,
                Transaccion.tipo == TipoTransaccion.EGRESO,
                Transaccion.es_padre_cuotas == False,
                or_(
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                    Transaccion.estado_verificacion == None
                )
            )
        )
        .group_by(Transaccion.categoria_id, Categoria.nombre)
    )
    res_actual = db.execute(stmt_actual).all()
    gasto_actual_por_categoria = {row.categoria_id: row.total for row in res_actual}
    for row in res_actual:
        categorias_nombres[row.categoria_id] = row.nombre

    # Paso 4: Calcular pesos
    if n_ciclos == 0:
        peso_historial = 0.0
        peso_actual = 1.0
    elif n_ciclos == 1:
        peso_historial = 0.6
        peso_actual = 0.4
    elif n_ciclos <= 3:
        peso_historial = 0.7
        peso_actual = 0.3
    else:
        peso_historial = 0.75
        peso_actual = 0.25

    if dias_transcurridos < 5:
        peso_historial = 1.0
        peso_actual = 0.0

    # Paso 5: Proyectar por categoria
    desglose = []
    gasto_proyectado_categorias = Decimal("0")
    
    todas_cat_ids = set(promedios_historicos.keys()) | set(gasto_actual_por_categoria.keys())
    
    for cat_id in todas_cat_ids:
        promedio_hist = promedios_historicos.get(cat_id, Decimal("0"))
        actual = gasto_actual_por_categoria.get(cat_id, Decimal("0"))
        
        ritmo_actual = (actual / Decimal(dias_transcurridos)) * Decimal(dias_totales)
        
        proyectado = (promedio_hist * Decimal(peso_historial)) + (ritmo_actual * Decimal(peso_actual))
        
        # Deteccion de fuera de patron
        fuera_de_patron = False
        if dias_transcurridos >= 5 and promedio_hist > 0:
            ritmo_proyectado_al_paso_actual = promedio_hist * (Decimal(dias_transcurridos) / Decimal(dias_totales))
            if actual > ritmo_proyectado_al_paso_actual * Decimal("1.4"):
                fuera_de_patron = True
        
        gasto_proyectado_categorias += proyectado
        
        desglose.append({
            "categoria_id": str(cat_id),
            "categoria_nombre": categorias_nombres.get(cat_id, "Sin nombre"),
            "gasto_actual_ciclo": float(actual),
            "promedio_historico": float(promedio_hist),
            "proyectado": float(proyectado),
            "fuera_de_patron": fuera_de_patron
        })

    desglose.sort(key=lambda x: x["proyectado"], reverse=True)

    # Paso 6: Sumar certezas
    # Cuotas
    cuotas_restantes = db.execute(
        select(func.sum(func.coalesce(Cuota.monto_real, Cuota.monto_proyectado)))
        .join(Transaccion, Cuota.transaccion_id == Transaccion.id)
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Cuota.pagada == False,
                Cuota.fecha_vencimiento > hoy,
                Cuota.fecha_vencimiento <= fecha_fin_actual
            )
        )
    ).scalar() or Decimal("0")

    # Suscripciones
    # Necesitamos el monto del ultimo historial para cada suscripcion activa
    subquery_historial = (
        select(
            HistorialSuscripcion.suscripcion_id,
            func.max(HistorialSuscripcion.vigente_desde).label("max_vigente")
        )
        .group_by(HistorialSuscripcion.suscripcion_id)
        .subquery()
    )

    suscripciones_stmt = (
        select(HistorialSuscripcion.monto)
        .join(Suscripcion, HistorialSuscripcion.suscripcion_id == Suscripcion.id)
        .join(
            subquery_historial,
            and_(
                HistorialSuscripcion.suscripcion_id == subquery_historial.c.suscripcion_id,
                HistorialSuscripcion.vigente_desde == subquery_historial.c.max_vigente
            )
        )
        .where(
            and_(
                Suscripcion.usuario_id == usuario.id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
                Suscripcion.proximo_cobro > hoy,
                Suscripcion.proximo_cobro <= fecha_fin_actual
            )
        )
    )
    suscripciones_res = db.execute(suscripciones_stmt).scalars().all()
    suscripciones_restantes = sum(suscripciones_res) if suscripciones_res else Decimal("0")

    total_certezas = cuotas_restantes + suscripciones_restantes
    gasto_proyectado_total = gasto_proyectado_categorias + total_certezas

    # Paso 7: Ingresos proyectados
    ingresos_actuales = db.execute(
        select(func.sum(Transaccion.monto))
        .where(
            and_(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha >= fecha_inicio_actual,
                Transaccion.fecha <= hoy,
                Transaccion.tipo == TipoTransaccion.INGRESO,
                or_(
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                    Transaccion.estado_verificacion == None
                )
            )
        )
    ).scalar() or Decimal("0")

    ingresos_recurrentes_pendientes = Decimal("0")
    recurrentes_activas = db.execute(
        select(TransaccionRecurrente)
        .where(
            and_(
                TransaccionRecurrente.usuario_id == usuario.id,
                TransaccionRecurrente.tipo == TipoTransaccionRecurrente.INGRESO,
                TransaccionRecurrente.estado == EstadoTransaccionRecurrente.ACTIVA
            )
        )
    ).scalars().all()

    for rec in recurrentes_activas:
        # Verificar si ya genero transaccion hoy para no duplicar
        ya_genero_hoy = db.execute(
            select(Transaccion)
            .where(
                and_(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.recurrente_id == rec.id,
                    Transaccion.fecha == hoy
                )
            )
        ).first() is not None

        start_check = hoy + timedelta(days=1) if ya_genero_hoy or True else hoy # El requerimiento dice hoy+1
        # Re-leemos el requerimiento: "Verificar si su dia_registro todavia no ocurrio en el ciclo actual (entre hoy+1 y fecha_fin_ciclo)"
        start_check = hoy + (timedelta(days=0) if not ya_genero_hoy else timedelta(days=1))
        # Para simplificar segun el prompt: entre hoy+1 y fecha_fin_ciclo
        start_date = hoy + timedelta(days=1)
        
        if start_date > fecha_fin_actual:
            continue

        if rec.frecuencia == FrecuenciaTransaccionRecurrente.MENSUAL:
            # rec.dia_registro es el dia del mes
            if rec.dia_registro >= start_date.day and rec.dia_registro <= fecha_fin_actual.day:
                # Ojo con meses de distinta longitud, pero dia_registro es int.
                # Si el mes actual tiene menos dias que dia_registro, se asume el ultimo dia.
                # Pero la logica del prompt es simple: dia_registro > hoy.day y <= ultimo_dia_ciclo
                ingresos_recurrentes_pendientes += rec.monto
        
        elif rec.frecuencia == FrecuenciaTransaccionRecurrente.SEMANAL:
            # rec.dia_registro es weekday (0-6)
            current = start_date
            while current <= fecha_fin_actual:
                if current.weekday() == rec.dia_registro:
                    ingresos_recurrentes_pendientes += rec.monto
                current += timedelta(days=1)

        elif rec.frecuencia == FrecuenciaTransaccionRecurrente.QUINCENAL:
            # dia_registro y dia_registro + 15
            dias = [rec.dia_registro, (rec.dia_registro + 15) % 30 or 30]
            # Simplificamos: si cae en el rango
            # El prompt dice: verificar si dia_registro o dia_registro+15 caen entre hoy+1 y fecha_fin_ciclo
            # Vamos a iterar los dias del rango para ser precisos
            current = start_date
            while current <= fecha_fin_actual:
                if current.day == rec.dia_registro or current.day == ((rec.dia_registro + 15) % 30 or 30):
                    ingresos_recurrentes_pendientes += rec.monto
                current += timedelta(days=1)

    ingresos_proyectados = ingresos_actuales + ingresos_recurrentes_pendientes

    # Paso 8: Nivel de confianza
    if n_ciclos >= 4 and dias_transcurridos >= 5:
        nivel_confianza = "alto"
    elif n_ciclos == 0:
        nivel_confianza = "bajo"
    else:
        nivel_confianza = "medio"

    # Paso 9: Advertencias
    advertencias = []
    if n_ciclos == 0:
        advertencias.append("Proyección basada solo en este ciclo. Mejorará con el tiempo.")
    elif n_ciclos <= 2:
        advertencias.append("Todavía tenemos poco historial tuyo. La proyección va a mejorar.")
    
    if dias_transcurridos < 5:
        advertencias.append("Recién empieza el ciclo. La proyección se basa principalmente en tus ciclos anteriores.")

    categorias_fuera = [d["categoria_nombre"] for d in desglose if d["fuera_de_patron"]]
    if categorias_fuera:
        if len(categorias_fuera) > 1:
            cats_str = ", ".join(categorias_fuera[:-1]) + " y " + categorias_fuera[-1]
        else:
            cats_str = categorias_fuera[0]
        advertencias.append(f"Este ciclo estás gastando más de lo habitual en {cats_str}.")

    return {
        "periodo": {
            "fecha_inicio": fecha_inicio_actual.isoformat(),
            "fecha_fin": fecha_fin_actual.isoformat(),
            "dias_transcurridos": dias_transcurridos,
            "dias_restantes": dias_restantes,
            "dias_totales": dias_totales
        },
        "gasto_proyectado_total": float(gasto_proyectado_total),
        "balance_proyectado": float(ingresos_proyectados - gasto_proyectado_total),
        "ingresos_proyectados": float(ingresos_proyectados),
        "certezas": {
            "cuotas_restantes": float(cuotas_restantes),
            "suscripciones_restantes": float(suscripciones_restantes),
            "total": float(total_certezas)
        },
        "desglose_por_categoria": desglose,
        "nivel_confianza": nivel_confianza,
        "ciclos_analizados": n_ciclos,
        "pesos": {
            "historial": float(peso_historial),
            "ciclo_actual": float(peso_actual)
        },
        "advertencias": advertencias
    }
