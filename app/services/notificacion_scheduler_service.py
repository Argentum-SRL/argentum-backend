import logging
from datetime import date, datetime, timedelta
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
    Detecta cuotas próximas a vencer según la anticipación configurada por cada usuario.
    """
    db: Session = db_session_factory()
    try:
        from app.models.cuota import Cuota
        from app.models.grupo_cuotas import GrupoCuotas

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

            cuotas = (
                db.query(Cuota)
                .options(joinedload(Cuota.grupo))
                .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
                .filter(
                    GrupoCuotas.usuario_id == usuario_id,
                    Cuota.fecha_vencimiento == fecha_objetivo,
                    Cuota.pagada == False,
                )
                .all()
            )

            for cuota in cuotas:
                grupo = cuota.grupo
                descripcion = getattr(grupo, 'descripcion', 'Compra en cuotas') if grupo else 'Compra en cuotas'
                cantidad_cuotas = getattr(grupo, 'cantidad_cuotas', '?') if grupo else '?'
                numero_cuota = getattr(cuota, 'numero_cuota', '?')
                monto = float(getattr(cuota, 'monto_proyectado', 0) or getattr(cuota, 'monto_real', 0))

                mensaje = (
                    f"La cuota {numero_cuota}/{cantidad_cuotas} "
                    f"de '{descripcion}' vence el {fecha_objetivo.strftime('%d/%m/%Y')} "
                    f"(${monto:,.0f})"
                )

                crear_notificacion(
                    db=db,
                    usuario_id=usuario_id,
                    tipo=TipoNotificacion.CUOTA_VENCE,
                    nivel=NivelNotificacion.FINANCIERA_IMPORTANTE,
                    mensaje=mensaje,
                    entidad_tipo="cuota",
                    entidad_id=cuota.id,
                    deep_link="/app/transacciones",
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
                    gastado = float(calcular_gasto_en_periodo(db, pres.usuario_id, pres.categorias, periodo_activo.fecha_inicio, periodo_activo.fecha_fin))
                except Exception as e:
                    logger.warning("No se pudo calcular gasto del presupuesto %s: %s", pres.id, e)
                    continue

                if gastado <= 0:
                    continue

                porcentaje = int((gastado / limite) * 100)
                nombre_pres = pres.nombre

                # Alertas del umbral 1 (configurable, ej: 80%)
                if config.presupuesto_umbral_1_activo and porcentaje >= config.presupuesto_umbral_1 and porcentaje < 100:
                    mensaje = f"Vas gastando el {porcentaje}% de tu presupuesto '{nombre_pres}' (${gastado:,.0f} de ${limite:,.0f})"
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
                    mensaje = f"Agotaste tu presupuesto '{nombre_pres}' de ${limite:,.0f} (llevás gastado ${gastado:,.0f})"
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
                recent_notif = db.query(Notificacion).filter(
                    Notificacion.usuario_id == usuario_id,
                    Notificacion.tipo == TipoNotificacion.INACTIVIDAD,
                    Notificacion.created_at >= datetime.utcnow() - timedelta(days=dias)
                ).first()

                if not recent_notif:
                    mensaje = f"Hace {dias} días que no registrás movimientos en Argentum."
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
