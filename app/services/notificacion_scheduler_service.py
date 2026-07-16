import logging
from datetime import date, datetime, timedelta, timezone
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import func, or_
from app.models.notificacion import TipoNotificacion, NivelNotificacion, Notificacion
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.usuario import Usuario
from app.services.notificacion_service import crear_notificacion
from app.services import notificacion_whatsapp_service as wpp_svc

logger = logging.getLogger(__name__)


def _job_notificaciones_cuotas(db_session_factory):
    """
    Corre diariamente a las 07:00 UTC.
    Detecta cuotas próximas a vencer y notifica según corresponda:
    - Para cuotas con tarjeta: notifica por el resumen consolidado de la tarjeta.
    - Para cuotas sin tarjeta: notifica por la cuota individual.
    """
    db: Session = db_session_factory()
    try:
        from app.models.cuota import Cuota
        from app.models.grupo_cuotas import GrupoCuotas
        from app.models.tarjeta_credito import TarjetaCredito, EstadoTarjeta
        from app.services.tarjeta_service import calcular_resumen_actual

        configs = db.query(
            ConfiguracionNotificacion.usuario_id,
            ConfiguracionNotificacion.cuota_vence_anticipacion_dias,
            ConfiguracionNotificacion.cuota_vence_web,
            ConfiguracionNotificacion.cuota_vence_whatsapp,
        ).all()

        hoy = date.today()

        for config in configs:
            usuario_id = config.usuario_id
            dias = config.cuota_vence_anticipacion_dias
            fecha_objetivo = hoy + timedelta(days=dias)

            # ─────────────────────────────────────────────────────────────────
            # PARTE 1: Cuotas CON tarjeta → notificar por resumen de tarjeta
            # ─────────────────────────────────────────────────────────────────
            tarjetas = db.query(TarjetaCredito).filter(
                TarjetaCredito.usuario_id == usuario_id,
                TarjetaCredito.estado == EstadoTarjeta.ACTIVA
            ).all()

            for tarjeta in tarjetas:
                resumen = calcular_resumen_actual(db, tarjeta)
                fecha_vencimiento_resumen = resumen.fecha_vencimiento_proximo

                if not fecha_vencimiento_resumen:
                    continue

                # Verificar si el vencimiento del resumen cae en la fecha objetivo (hoy + N días)
                if fecha_vencimiento_resumen != fecha_objetivo:
                    continue

                total_resumen = resumen.total_comprometido_resumen_actual

                if not total_resumen or float(total_resumen) <= 0:
                    continue

                mensaje = (
                    f"El resumen de tu {tarjeta.nombre} cierra el {resumen.fecha_cierre_proximo.strftime('%d/%m/%Y')} "
                    f"y vence el {fecha_vencimiento_resumen.strftime('%d/%m/%Y')}."
                )

                grupo_override = f"RESUMEN_TARJETA_{tarjeta.id}_{hoy.strftime('%Y%m%d')}"

                crear_notificacion(
                    db=db,
                    usuario_id=usuario_id,
                    tipo=TipoNotificacion.CUOTA_VENCE,
                    nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                    mensaje=mensaje,
                    entidad_tipo="tarjeta",
                    entidad_id=tarjeta.id,
                    deep_link="/app/tarjetas",
                    canal_web=config.cuota_vence_web,
                    canal_whatsapp=config.cuota_vence_whatsapp,
                    grupo_agrupacion_override=grupo_override,
                )

            # ─────────────────────────────────────────────────────────────────
            # PARTE 2: Cuotas SIN tarjeta → notificar por cuota individual
            # ─────────────────────────────────────────────────────────────────
            cuotas_sin_tarjeta = (
                db.query(Cuota)
                .options(joinedload(Cuota.grupo))
                .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
                .filter(
                    GrupoCuotas.usuario_id == usuario_id,
                    GrupoCuotas.tarjeta_id == None,
                    Cuota.fecha_vencimiento == fecha_objetivo,
                    Cuota.pagada == False,
                )
                .all()
            )

            for cuota in cuotas_sin_tarjeta:
                grupo = cuota.grupo
                descripcion = getattr(grupo, 'descripcion', 'Compra en cuotas') if grupo else 'Compra en cuotas'
                cantidad = getattr(grupo, 'cantidad_cuotas', '?') if grupo else '?'
                numero = getattr(cuota, 'numero_cuota', '?')
                monto = float(getattr(cuota, 'monto_proyectado', 0) or getattr(cuota, 'monto_real', 0))

                mensaje = (
                    f"Tu cuota {numero}/{cantidad} de '{descripcion}' vence el {fecha_objetivo.strftime('%d/%m/%Y')} (${monto:,.0f}). "
                    f"Acordate de tener el saldo disponible."
                )

                crear_notificacion(
                    db=db,
                    usuario_id=usuario_id,
                    tipo=TipoNotificacion.CUOTA_VENCE,
                    nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                    mensaje=mensaje,
                    entidad_tipo="cuota",
                    entidad_id=cuota.id,
                    deep_link="/app/tarjetas",
                    canal_web=config.cuota_vence_web,
                    canal_whatsapp=config.cuota_vence_whatsapp,
                )

        logger.info("Job notificaciones_cuotas completado")

    except Exception:
        logger.exception("Error en _job_notificaciones_cuotas")
    finally:
        db.close()


def _job_notificaciones_presupuestos(db_session_factory):
    """
    Corre diariamente a las 07:05 UTC.
    Detecta presupuestos que alcanzaron los umbrales configurados.
    """
    db: Session = db_session_factory()
    try:
        from app.services.presupuesto_service import calcular_gasto_en_periodo, obtener_periodo_activo
        from app.models.presupuesto import Presupuesto

        configs = db.query(
            ConfiguracionNotificacion.usuario_id,
            ConfiguracionNotificacion.presupuesto_umbral_1,
            ConfiguracionNotificacion.presupuesto_umbral_1_activo,
            ConfiguracionNotificacion.presupuesto_umbral_1_web,
            ConfiguracionNotificacion.presupuesto_umbral_1_whatsapp,
            ConfiguracionNotificacion.presupuesto_umbral_2_web,
            ConfiguracionNotificacion.presupuesto_umbral_2_whatsapp,
        ).all()

        for config in configs:
            usuario_id = config.usuario_id

            presupuestos = db.query(Presupuesto).filter(
                Presupuesto.usuario_id == usuario_id,
                Presupuesto.estado == "activo",
            ).all()

            for pres in presupuestos:
                limite = float(getattr(pres, 'monto', 0))
                if limite <= 0:
                    continue

                try:
                    periodo_activo = obtener_periodo_activo(db, pres)
                    if not periodo_activo:
                        continue
                    gastado = float(calcular_gasto_en_periodo(db, pres.usuario_id, pres.categorias, periodo_activo.fecha_inicio, periodo_activo.fecha_fin, moneda=pres.moneda))
                except Exception as e:
                    logger.warning("No se pudo calcular gasto del presupuesto %s: %s", pres.id, e)
                    continue

                if gastado <= 0:
                    continue

                porcentaje = int((gastado / limite) * 100)
                nombre_pres = pres.nombre

                # Alertas del umbral 1 (configurable, ej: 80%)
                if config.presupuesto_umbral_1_activo and porcentaje >= config.presupuesto_umbral_1 and porcentaje < 100:
                    mensaje = f"Llevás gastado el {porcentaje}% de tu presupuesto '{nombre_pres}' (${gastado:,.0f} de ${limite:,.0f})"
                    crear_notificacion(
                        db=db,
                        usuario_id=usuario_id,
                        tipo=TipoNotificacion.PRESUPUESTO_LIMITE,
                        nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
                        mensaje=mensaje,
                        entidad_tipo="presupuesto",
                        entidad_id=pres.id,
                        deep_link="/app/presupuestos",
                        canal_web=config.presupuesto_umbral_1_web,
                        canal_whatsapp=config.presupuesto_umbral_1_whatsapp,
                    )

                # Alertas del umbral 2 (100% agotado)
                if porcentaje >= 100:
                    mensaje = f"Gastaste ${gastado:,.0f} de ${limite:,.0f} en {nombre_pres}. Superaste el límite que te pusiste."
                    crear_notificacion(
                        db=db,
                        usuario_id=usuario_id,
                        tipo=TipoNotificacion.PRESUPUESTO_AGOTADO,
                        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                        mensaje=mensaje,
                        entidad_tipo="presupuesto",
                        entidad_id=pres.id,
                        deep_link="/app/presupuestos",
                        canal_web=config.presupuesto_umbral_2_web,
                        canal_whatsapp=config.presupuesto_umbral_2_whatsapp,
                    )

        logger.info("Job notificaciones_presupuestos completado")

    except Exception:
        logger.exception("Error en _job_notificaciones_presupuestos")
    finally:
        db.close()


def _job_notificaciones_suscripciones(db_session_factory):
    """
    Corre diariamente a las 07:10 UTC.
    Detecta suscripciones cobradas hoy o próximas a cobrar.
    """
    db: Session = db_session_factory()
    try:
        from app.models.suscripcion import Suscripcion, EstadoSuscripcion
        from app.services.suscripcion_service import obtener_precio_vigente

        configs = db.query(ConfiguracionNotificacion).all()
        hoy = date.today()

        for config in configs:
            usuario_id = config.usuario_id
            suscripciones = (
                db.query(Suscripcion)
                .options(selectinload(Suscripcion.historial))
                .filter(
                    Suscripcion.usuario_id == usuario_id,
                    Suscripcion.estado == EstadoSuscripcion.ACTIVA,
                )
                .all()
            )

            for s in suscripciones:
                # Cobro hoy
                if s.proximo_cobro == hoy:
                    precio = obtener_precio_vigente(db, s.id, hoy)
                    monto = float(precio.monto) if precio else 0.0
                    moneda = precio.moneda if precio else "ARS"
                    mensaje = f"Hoy se cobra la suscripción '{s.nombre}' por ${monto:,.0f} {moneda}"

                    crear_notificacion(
                        db=db,
                        usuario_id=usuario_id,
                        tipo=TipoNotificacion.SUSCRIPCION_HOY,
                        nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                        mensaje=mensaje,
                        entidad_tipo="suscripcion",
                        entidad_id=s.id,
                        deep_link="/app/suscripciones",
                        canal_web=config.suscripcion_hoy_web,
                        canal_whatsapp=config.suscripcion_hoy_whatsapp,
                    )

                # Recordatorio anticipado
                if config.suscripcion_recordatorio_activo:
                    anticipacion = config.suscripcion_recordatorio_dias
                    if s.proximo_cobro == hoy + timedelta(days=anticipacion):
                        precio = obtener_precio_vigente(db, s.id, s.proximo_cobro)
                        monto = float(precio.monto) if precio else 0.0
                        moneda = precio.moneda if precio else "ARS"
                        mensaje = f"La suscripción '{s.nombre}' se cobrará en {anticipacion} días (${monto:,.0f} {moneda})"

                        crear_notificacion(
                            db=db,
                            usuario_id=usuario_id,
                            tipo=TipoNotificacion.SUSCRIPCION_PROXIMA,
                            nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
                            mensaje=mensaje,
                            entidad_tipo="suscripcion",
                            entidad_id=s.id,
                            deep_link="/app/suscripciones",
                            canal_web=config.suscripcion_recordatorio_web,
                            canal_whatsapp=config.suscripcion_recordatorio_whatsapp,
                        )

        logger.info("Job notificaciones_suscripciones completado")

    except Exception:
        logger.exception("Error en _job_notificaciones_suscripciones")
    finally:
        db.close()


def _job_notificaciones_inactividad(db_session_factory):
    """
    Corre diariamente a las 07:15 UTC.
    Detecta inactividad del usuario si no registró movimientos en los días configurados.
    """
    db: Session = db_session_factory()
    try:
        from app.models.transaccion import Transaccion
        from app.models.transferencia_interna import TransferenciaInterna

        configs = db.query(ConfiguracionNotificacion).filter(
            ConfiguracionNotificacion.inactividad_activo == True
        ).all()

        hoy = date.today()

        for config in configs:
            usuario_id = config.usuario_id
            dias = config.inactividad_dias
            limite_fecha = hoy - timedelta(days=dias)

            # Verificar transacciones en ese periodo
            tx_count = db.query(Transaccion).filter(
                Transaccion.usuario_id == usuario_id,
                Transaccion.fecha >= limite_fecha
            ).count()

            # Verificar transferencias en ese periodo
            tf_count = db.query(TransferenciaInterna).filter(
                TransferenciaInterna.usuario_id == usuario_id,
                TransferenciaInterna.fecha >= limite_fecha
            ).count()

            if tx_count == 0 and tf_count == 0:
                # Comprobar si ya enviamos notificación de inactividad recientemente para evitar spam
                from datetime import timezone
                recent_notif = db.query(Notificacion).filter(
                    Notificacion.usuario_id == usuario_id,
                    Notificacion.tipo == TipoNotificacion.INACTIVIDAD,
                    Notificacion.created_at >= datetime.now(timezone.utc) - timedelta(days=dias)
                ).first()

                if not recent_notif:
                    mensaje = f"¿Todo bien? Hace {dias} días que no registrás nada. Registrar tus gastos te ayuda a entender mejor en qué se va tu plata."
                    crear_notificacion(
                        db=db,
                        usuario_id=usuario_id,
                        tipo=TipoNotificacion.INACTIVIDAD,
                        nivel=NivelNotificacion.SOFT,
                        mensaje=mensaje,
                        canal_web=config.inactividad_web,
                        canal_whatsapp=config.inactividad_whatsapp,
                    )

        logger.info("Job notificaciones_inactividad completado")

    except Exception:
        logger.exception("Error en _job_notificaciones_inactividad")
    finally:
        db.close()


def _job_entrega_whatsapp_batched(db_session_factory):
    """
    Corre cada minuto.
    Filtra configuraciones que correspondan a la hora y minuto local configurado del usuario.
    Huso local por defecto: UTC-3 (Argentina).
    """
    db: Session = db_session_factory()
    try:
        # Calcular hora y minuto local actual en Argentina (UTC-3)
        ahora_utc = datetime.utcnow()
        tiempo_local = ahora_utc - timedelta(hours=3)
        hora_local = tiempo_local.hour
        minuto_local = tiempo_local.minute

        # Obtener todos los usuarios que tengan configurado el envío para esta hora y minuto local
        usuarios = (
            db.query(Usuario)
            .join(ConfiguracionNotificacion, Usuario.id == ConfiguracionNotificacion.usuario_id)
            .filter(
                ConfiguracionNotificacion.whatsapp_hora_envio == hora_local,
                ConfiguracionNotificacion.whatsapp_minuto_envio == minuto_local,
                Usuario.telefono != None,
                Usuario.telefono != ""
            )
            .all()
        )

        for u in usuarios:
            # Obtener todas las notificaciones pendientes de WhatsApp para el usuario
            notifs = (
                db.query(Notificacion)
                .filter(
                    Notificacion.usuario_id == u.id,
                    Notificacion.canal_whatsapp == True,
                    Notificacion.enviada_whatsapp == False,
                    Notificacion.archivada == False,
                    (Notificacion.silenciada_hasta == None) | (Notificacion.silenciada_hasta < ahora_utc)
                )
                .all()
            )

            if not notifs:
                continue

            # Agrupar mensajes
            mensajes = [n.mensaje for n in notifs]
            if len(mensajes) == 1:
                wpp_mensaje = mensajes[0]
            else:
                wpp_mensaje = wpp_svc.formatear_resumen_diario(mensajes)

            # Enviar mensaje
            exito = wpp_svc.enviar_whatsapp_notificacion(u.telefono, wpp_mensaje)
            if exito:
                for n in notifs:
                    n.enviada_whatsapp = True
                db.commit()

        logger.info("Job entrega_whatsapp_batched completado")

    except Exception:
        logger.exception("Error en _job_entrega_whatsapp_batched")
    finally:
        db.close()


def _job_resumen_cierre_ciclo(db_session_factory):
    """
    Corre diariamente a las 07:20 UTC.
    Detecta usuarios cuyo ciclo cerró ayer y les manda un resumen por WhatsApp.
    """
    db: Session = db_session_factory()
    try:
        from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion
        from app.models.categoria import Categoria
        from app.services.dashboard_service import get_ciclo_fechas
        from sqlalchemy import func

        hoy = (datetime.now(timezone.utc if hasattr(timezone, 'utc') else __import__('datetime').timezone.utc) - timedelta(hours=3)).date()
        ayer = hoy - timedelta(days=1)

        # Obtener usuarios con WhatsApp verificado y configuración activa
        usuarios = (
            db.query(Usuario)
            .join(ConfiguracionNotificacion, Usuario.id == ConfiguracionNotificacion.usuario_id)
            .filter(
                Usuario.telefono != None,
                Usuario.telefono_verificado == True,
            )
            .all()
        )

        for usuario in usuarios:
            try:
                # Verificar si ayer fue el último día del ciclo del usuario
                fecha_inicio, fecha_fin = get_ciclo_fechas(usuario, ayer)
                if fecha_fin != ayer:
                    continue

                # Guardar snapshot del historial financiero del usuario para el ciclo cerrado
                try:
                    from app.services.perfil_financiero_service import guardar_snapshot_historial
                    guardar_snapshot_historial(db, usuario.id, fecha_inicio, fecha_fin)
                except Exception as e:
                    logger.error("Error al guardar snapshot historial para usuario %s: %s", usuario.id, e)


                # Verificar que no mandamos este resumen ya hoy
                ya_enviado = db.query(Notificacion).filter(
                    Notificacion.usuario_id == usuario.id,
                    Notificacion.tipo == TipoNotificacion.RESUMEN_CICLO,
                    Notificacion.created_at >= datetime.now(__import__('datetime').timezone.utc) - timedelta(hours=24),
                ).first()
                if ya_enviado:
                    continue

                # Calcular totales del ciclo cerrado
                ingresos = db.query(func.sum(Transaccion.monto)).filter(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.tipo == TipoTransaccion.INGRESO,
                    Transaccion.fecha >= fecha_inicio,
                    Transaccion.fecha <= fecha_fin,
                    Transaccion.es_padre_cuotas == False,
                ).scalar() or 0

                egresos = db.query(func.sum(Transaccion.monto)).filter(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.tipo == TipoTransaccion.EGRESO,
                    Transaccion.fecha >= fecha_inicio,
                    Transaccion.fecha <= fecha_fin,
                    Transaccion.es_padre_cuotas == False,
                ).scalar() or 0

                if float(ingresos) == 0 and float(egresos) == 0:
                    continue

                balance = float(ingresos) - float(egresos)

                # Categoría con más gasto
                cat_top = db.query(
                    Categoria.nombre,
                    func.sum(Transaccion.monto).label("total")
                ).join(Transaccion, Transaccion.categoria_id == Categoria.id).filter(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.tipo == TipoTransaccion.EGRESO,
                    Transaccion.fecha >= fecha_inicio,
                    Transaccion.fecha <= fecha_fin,
                    Transaccion.es_padre_cuotas == False,
                ).group_by(Categoria.nombre).order_by(func.sum(Transaccion.monto).desc()).first()

                # Gastos hormiga: categorías con muchas transacciones de monto bajo
                hormiga_threshold = 5
                gastos_hormiga_raw = db.query(
                    Categoria.nombre,
                    func.count(Transaccion.id).label("cantidad"),
                    func.sum(Transaccion.monto).label("total")
                ).join(Transaccion, Transaccion.categoria_id == Categoria.id).filter(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.tipo == TipoTransaccion.EGRESO,
                    Transaccion.fecha >= fecha_inicio,
                    Transaccion.fecha <= fecha_fin,
                    Transaccion.es_padre_cuotas == False,
                ).group_by(Categoria.nombre).having(
                    func.count(Transaccion.id) >= hormiga_threshold
                ).order_by(func.count(Transaccion.id).desc()).limit(3).all()

                gastos_hormiga = [
                    {"categoria": r.nombre, "cantidad": r.cantidad, "total": float(r.total)}
                    for r in gastos_hormiga_raw
                ] if gastos_hormiga_raw else None

                mensaje = wpp_svc.formatear_resumen_ciclo(
                    total_ingresos=float(ingresos),
                    total_egresos=float(egresos),
                    balance=balance,
                    categoria_top=cat_top.nombre if cat_top else None,
                    monto_categoria_top=float(cat_top.total) if cat_top else None,
                    gastos_hormiga=gastos_hormiga,
                )

                crear_notificacion(
                    db=db,
                    usuario_id=usuario.id,
                    tipo=TipoNotificacion.RESUMEN_CICLO,
                    nivel=NivelNotificacion.FINANCIERA_INFORMATIVA,
                    mensaje=mensaje,
                    canal_web=False,
                    canal_whatsapp=True,
                )

            except Exception:
                logger.exception("Error generando resumen de ciclo para usuario %s", usuario.id)
                continue

        logger.info("Job resumen_cierre_ciclo completado")

    except Exception:
        logger.exception("Error en _job_resumen_cierre_ciclo")
    finally:
        db.close()


def _job_resumen_semanal(db_session_factory):
    """
    Genera un resumen semanal de finanzas para cada usuario activo.
    Corre los lunes a las 08:00 UTC y resume la semana anterior (lunes a domingo).
    """
    from datetime import date, timedelta
    from sqlalchemy import func as sa_func, select
    from app.models.usuario import Usuario, EstadoUsuario
    from app.models.transaccion import Transaccion, TipoTransaccion
    from app.models.categoria import Categoria
    from app.models.configuracion_notificacion import ConfiguracionNotificacion
    from app.models.notificacion import TipoNotificacion, NivelNotificacion
    from app.services.notificacion_service import crear_notificacion

    hoy = date.today()
    # Semana anterior: lunes a domingo
    lunes_pasado = hoy - timedelta(days=hoy.weekday() + 7)
    domingo_pasado = lunes_pasado + timedelta(days=6)

    db: Session = db_session_factory()
    try:
        usuarios = db.execute(
            select(Usuario).where(Usuario.estado == EstadoUsuario.ACTIVO)
        ).scalars().all()

        for usuario in usuarios:
            try:
                config = db.execute(
                    select(ConfiguracionNotificacion).where(
                        ConfiguracionNotificacion.usuario_id == usuario.id
                    )
                ).scalar_one_or_none()

                if config and getattr(config, 'resumen_semanal_activo', True) is False:
                    continue  # si el usuario desactivó el resumen, saltar
                
                # Calcular egresos e ingresos de la semana anterior (excluyendo padre de cuotas para evitar duplicar)
                egresos_result = db.execute(
                    select(sa_func.sum(Transaccion.monto)).where(
                        Transaccion.usuario_id == usuario.id,
                        Transaccion.tipo == TipoTransaccion.EGRESO,
                        Transaccion.fecha >= lunes_pasado,
                        Transaccion.fecha <= domingo_pasado,
                        Transaccion.es_padre_cuotas == False,
                    )
                ).scalar()

                ingresos_result = db.execute(
                    select(sa_func.sum(Transaccion.monto)).where(
                        Transaccion.usuario_id == usuario.id,
                        Transaccion.tipo == TipoTransaccion.INGRESO,
                        Transaccion.fecha >= lunes_pasado,
                        Transaccion.fecha <= domingo_pasado,
                        Transaccion.es_padre_cuotas == False,
                    )
                ).scalar()

                egresos = float(egresos_result or 0)
                ingresos = float(ingresos_result or 0)

                # Si no hubo ningún movimiento en la semana, no notificar
                if egresos == 0 and ingresos == 0:
                    continue

                # Top categoría de la semana por egresos (excluyendo padre de cuotas)
                top_categoria_result = db.execute(
                    select(Categoria.nombre, sa_func.sum(Transaccion.monto).label("total"))
                    .join(Categoria, Transaccion.categoria_id == Categoria.id)
                    .where(
                        Transaccion.usuario_id == usuario.id,
                        Transaccion.tipo == TipoTransaccion.EGRESO,
                        Transaccion.fecha >= lunes_pasado,
                        Transaccion.fecha <= domingo_pasado,
                        Transaccion.categoria_id.isnot(None),
                        Transaccion.es_padre_cuotas == False,
                    )
                    .group_by(Categoria.nombre)
                    .order_by(sa_func.sum(Transaccion.monto).desc())
                    .limit(1)
                ).first()

                top_categoria = top_categoria_result[0] if top_categoria_result else None

                balance = ingresos - egresos
                signo = "+" if balance >= 0 else ""

                if top_categoria:
                    mensaje = (
                        f"Tu semana financiera: ingresaste ${ingresos:,.0f} y gastaste ${egresos:,.0f} "
                        f"(balance {signo}${balance:,.0f}). Tu mayor gasto fue en {top_categoria}."
                    )
                else:
                    mensaje = (
                        f"Tu semana financiera: ingresaste ${ingresos:,.0f} y gastaste ${egresos:,.0f} "
                        f"(balance {signo}${balance:,.0f})."
                    )

                # Usar los canales configurados por el usuario si config existe
                canal_web = config.resumen_semanal_web if config else True
                canal_whatsapp = config.resumen_semanal_whatsapp if config else True

                crear_notificacion(
                    db=db,
                    usuario_id=usuario.id,
                    tipo=TipoNotificacion.RESUMEN_SEMANAL,
                    nivel=NivelNotificacion.SOFT,
                    mensaje=mensaje,
                    deep_link="/app/dashboard",
                    canal_web=canal_web,
                    canal_whatsapp=canal_whatsapp,
                )

            except Exception as e:
                logger.error(f"[resumen_semanal] Error procesando usuario {usuario.id}: {e}")
                continue

        db.commit()
        logger.info("Job resumen_semanal completado")

    except Exception as e:
        logger.error(f"[resumen_semanal] Error general: {e}")
        db.rollback()
    finally:
        db.close()
