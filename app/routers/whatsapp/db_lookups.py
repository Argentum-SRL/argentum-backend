"""
Consultas y operaciones de base de datos para el flujo de WhatsApp IA.
Incluye verificaciones de rate limiting, resolución de usuarios, billeteras, tarjetas, categorías, cotizaciones, propuestas y suscripciones.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.catalogo_suscripciones import buscar_servicio_por_texto
from app.core.constants import CATEGORIAS_SISTEMA
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.meta import EstadoMeta, Meta
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.suscripcion import EstadoSuscripcion, Suscripcion
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    OrigenTransaccion,
    Transaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import EstadoUsuario, Moneda, Usuario
from app.routers.whatsapp.resolvers_cascada import resolver_billetera_cascada
from app.services.rate_limit_service import verificar_rate_limit
from app.utils.fecha import hoy_argentina
from app.utils.telefono import normalizar_telefono_ar
from app.utils.texto import normalizar_texto

logger = structlog.get_logger("whatsapp")

# Plazos de expiración
PLAZO_EXPIRACION_ESTADO_MINUTOS = 15
PLAZO_DESHACER_CORREGIR_MINUTOS = 30

MAX_INTENTOS_VINCULACION_POR_VENTANA = 5
VENTANA_VINCULACION_SEGUNDOS = 15 * 60  # 15 minutos
MAX_MENSAJES_POR_MINUTO_REGISTRADO = 12
MAX_MEDIOS_POR_MINUTO_REGISTRADO = 4
VENTANA_RATE_LIMIT_WPP_SEGUNDOS = 60

def _verificar_rate_limit_vinculacion(telefono_norm: str, db: Session | None = None) -> bool:
    permitido, _, _ = verificar_rate_limit(
        accion="vinculacion_no_registrado",
        identificador=telefono_norm,
        max_intentos=MAX_INTENTOS_VINCULACION_POR_VENTANA,
        ventana_segundos=VENTANA_VINCULACION_SEGUNDOS,
        db=db,
    )
    return permitido

def _verificar_rate_limit_registrado(telefono_norm: str, es_medio: bool = False) -> tuple[bool, str | None]:
    """
    Verifica si el usuario registrado superó el límite de mensajes o medios por minuto en Postgres.
    Retorna (permitido, motivo_error_o_none).
    """
    # 1. Chequeo de ráfaga de medios (audios / imágenes a Whisper / Vision)
    if es_medio:
        permitido_medio, _, _ = verificar_rate_limit(
            accion="medios_registrados",
            identificador=telefono_norm,
            max_intentos=MAX_MEDIOS_POR_MINUTO_REGISTRADO,
            ventana_segundos=VENTANA_RATE_LIMIT_WPP_SEGUNDOS,
        )
        if not permitido_medio:
            return False, "Estás enviando muchos audios o comprobantes seguidos. Por favor, esperá un minuto antes de enviar otro."

    # 2. Chequeo de mensajes totales por minuto
    permitido_msg, _, _ = verificar_rate_limit(
        accion="mensajes_registrados",
        identificador=telefono_norm,
        max_intentos=MAX_MENSAJES_POR_MINUTO_REGISTRADO,
        ventana_segundos=VENTANA_RATE_LIMIT_WPP_SEGUNDOS,
    )
    if not permitido_msg:
        return False, "Estás enviando muchos mensajes seguidos. Por favor, esperá un momento antes de volver a escribir."

    return True, None

def _buscar_usuario_por_telefono(telefono_raw: str, db: Session) -> Usuario | None:
    telefono_norm = normalizar_telefono_ar(telefono_raw)
    if not telefono_norm:
        return None

    usuario = db.execute(
        select(Usuario).where(
            Usuario.telefono_normalizado == telefono_norm,
            Usuario.estado == EstadoUsuario.ACTIVO,
            Usuario.telefono_verificado.is_(True),
        )
    ).scalar_one_or_none()

    return usuario

def _resolver_billetera(
    nombre: str | None,
    usuario_id: UUID,
    db: Session,
    moneda: Moneda | None = None,
) -> UUID | None:
    if not nombre:
        return None
    billeteras = _obtener_billeteras_activas(usuario_id, db, moneda=moneda)
    b_match, _ = resolver_billetera_cascada(nombre, billeteras)
    return b_match.id if b_match else None

def _obtener_fallback_otros(
    tipo: str,
    categorias: list[Categoria],
    db: Session | None = None,
) -> tuple[UUID | None, UUID | None]:
    """
    Retorna (categoria_otros_id, None) de forma determinística.
    Elimina por completo cualquier selección arbitraria o aleatoria de subcategorías.
    """
    cat_otros = next(
        (c for c in categorias if normalizar_texto(c.nombre) == "otros"),
        None
    )
    if not cat_otros:
        logger.error("No se encontró la categoría 'Otros' para el tipo '%s'", tipo)
        return None, None
    return cat_otros.id, None

def _resolver_categoria_y_subcategoria(
    nombre: str | None,
    usuario_id: UUID,
    db: Session,
    tipo: str = "egreso",
) -> tuple[UUID | None, UUID | None]:
    """
    Parsea y valida el campo categoría/subcategoría contra las tablas reales de la base de datos
    de manera 100% determinística y en memoria (máximo 2 consultas con ORDER BY estable).

    Cascada de resolución:
    1. Carga categorías activas del tipo correspondiente y subcategorías activas con ORDER BY explícito.
    2. Excluye categorías del sistema (CATEGORIAS_SISTEMA como 'Ahorro').
    3. Separa string por '>' en nombre_categoria y nombre_subcategoria.
    4. Resuelve categoría:
       a) Coincidencia exacta normalizada.
       b) Coincidencia por contención (min 4 chars) SOLO SI produce exactamente 1 candidata.
       c) Coincidencia exacta normalizada por subcategoría única en todo el conjunto.
       d) Fallback a categoría 'Otros' con subcategoría NULL.
    5. Resuelve subcategoría SOLO dentro de la categoría determinada:
       a) Coincidencia exacta normalizada.
       b) Coincidencia por contención única dentro de esa categoría (min 4 chars).
       c) Si no hay match -> NULL.

    Retorna (categoria_id, subcategoria_id).
    """
    tipo_enum = TipoCategoria.INGRESO if tipo == "ingreso" else TipoCategoria.EGRESO

    # 1. Cargar UNA sola vez por invocación con order_by estable
    stmt_cats = (
        select(Categoria)
        .where(
            Categoria.estado == EstadoCategoria.ACTIVA,
            Categoria.tipo == tipo_enum
        )
        .order_by(Categoria.nombre.asc(), Categoria.id.asc())
    )
    categorias = db.execute(stmt_cats).scalars().all()

    # Excluir categorías de sistema ("Ahorro")
    categorias_candidatas = [
        c for c in categorias
        if normalizar_texto(c.nombre) not in {normalizar_texto(s) for s in CATEGORIAS_SISTEMA}
    ]

    cat_ids_validas = {c.id for c in categorias_candidatas}

    stmt_subs = (
        select(Subcategoria)
        .where(
            Subcategoria.estado == EstadoSubcategoria.ACTIVA,
            Subcategoria.categoria_id.in_(cat_ids_validas)
        )
        .order_by(Subcategoria.orden.asc(), Subcategoria.nombre.asc(), Subcategoria.id.asc())
    )
    subcategorias_candidatas = db.execute(stmt_subs).scalars().all()

    if not nombre:
        cat_id, _ = _obtener_fallback_otros(tipo, categorias_candidatas, db)
        logger.debug("[CATEGORIZACION] Fallback por nombre vacío -> (%s, None), confianza=fallback", cat_id)
        return cat_id, None

    # 2. Separar por '>' si viene con formato 'Cat > Subcat'
    if ">" in nombre:
        partes = [p.strip() for p in nombre.split(">", 1)]
        nombre_categoria_raw = partes[0]
        nombre_subcategoria_raw = partes[1] if len(partes) > 1 else None
    else:
        nombre_categoria_raw = nombre.strip()
        nombre_subcategoria_raw = None

    norm_cat = normalizar_texto(nombre_categoria_raw)
    categoria_match: Categoria | None = None
    sub_unica_detectada: Subcategoria | None = None
    confianza_cat = "fallback"

    # 3. Resolución de categoría
    if norm_cat:
        # a) Coincidencia exacta del nombre normalizado de categoría
        for c in categorias_candidatas:
            if normalizar_texto(c.nombre) == norm_cat:
                categoria_match = c
                confianza_cat = "exacta"
                break

        # b) Coincidencia por contención SOLO SI produce exactamente 1 candidata (min 4 chars)
        if not categoria_match:
            candidatas_b = []
            for c in categorias_candidatas:
                c_norm = normalizar_texto(c.nombre)
                min_len = min(len(norm_cat), len(c_norm))
                if min_len >= 4 and (norm_cat in c_norm or c_norm in norm_cat):
                    candidatas_b.append(c)
            if len(candidatas_b) == 1:
                categoria_match = candidatas_b[0]
                confianza_cat = "aproximada"

        # c) Búsqueda por subcategoría exacta y única en todo el árbol (solo si a y b fallaron)
        if not categoria_match:
            candidatas_sub = []
            for s in subcategorias_candidatas:
                s_norm = normalizar_texto(s.nombre)
                if s_norm == norm_cat:
                    candidatas_sub.append(s)
            if len(candidatas_sub) == 1:
                sub_unica_detectada = candidatas_sub[0]
                cat_padre = next((c for c in categorias_candidatas if c.id == sub_unica_detectada.categoria_id), None)
                if cat_padre:
                    categoria_match = cat_padre
                    confianza_cat = "aproximada"

    # d) Fallback si nada resolvió
    if not categoria_match:
        cat_id, _ = _obtener_fallback_otros(tipo, categorias_candidatas, db)
        logger.debug(
            "[CATEGORIZACION] Fallback categoría 'Otros' para input='%s' -> (%s, None), confianza=fallback",
            nombre,
            cat_id,
        )
        return cat_id, None

    # 4. Resolución de subcategoría
    subcategoria_match: Subcategoria | None = None
    confianza_sub = "ninguna"

    # Caso especial: la categoría se resolvió por match exacto de subcategoría única (paso 3.c)
    if sub_unica_detectada and sub_unica_detectada.categoria_id == categoria_match.id:
        subcategoria_match = sub_unica_detectada
        confianza_sub = "exacta"
    elif nombre_subcategoria_raw:
        # ÚNICAMENTE se evalúa el texto explícito posterior al '>'
        norm_sub = normalizar_texto(nombre_subcategoria_raw)
        if norm_sub:
            subcategorias_de_cat = [
                s for s in subcategorias_candidatas if s.categoria_id == categoria_match.id
            ]
            # a) Coincidencia exacta normalizada
            for s in subcategorias_de_cat:
                if normalizar_texto(s.nombre) == norm_sub:
                    subcategoria_match = s
                    confianza_sub = "exacta"
                    break

            # b) Coincidencia por contención única dentro de la categoría (min 4 chars)
            if not subcategoria_match:
                candidatas_sub_b = []
                for s in subcategorias_de_cat:
                    s_norm = normalizar_texto(s.nombre)
                    min_len = min(len(norm_sub), len(s_norm))
                    if min_len >= 4 and (norm_sub in s_norm or s_norm in norm_sub):
                        candidatas_sub_b.append(s)
                if len(candidatas_sub_b) == 1:
                    subcategoria_match = candidatas_sub_b[0]
                    confianza_sub = "aproximada"
    else:
        # No vino texto de subcategoría tras '>' -> NULL estricto
        subcategoria_match = None

    sub_id = subcategoria_match.id if subcategoria_match else None
    logger.debug(
        "[CATEGORIZACION] Input='%s' -> Cat=%s (%s), Sub=%s (%s)",
        nombre,
        categoria_match.nombre,
        confianza_cat,
        subcategoria_match.nombre if subcategoria_match else "NULL",
        confianza_sub,
    )
    return categoria_match.id, sub_id

def _obtener_billeteras_activas(usuario_id: UUID, db: Session, moneda: Moneda | None = None) -> list[Billetera]:
    query = select(Billetera).where(
        Billetera.usuario_id == usuario_id,
        Billetera.estado == EstadoBilletera.ACTIVA,
        Billetera.es_inversion == False,
    )
    if moneda:
        query = query.where(Billetera.moneda == moneda)
    return db.execute(
        query.order_by(Billetera.es_principal.desc(), Billetera.nombre.asc(), Billetera.id.asc())
    ).scalars().all()

def _obtener_tarjetas_activas(usuario_id: UUID, db: Session) -> list[TarjetaCredito]:
    """Carga todas las tarjetas de crédito activas del usuario con su billetera vinculada."""
    return db.execute(
        select(TarjetaCredito)
        .options(joinedload(TarjetaCredito.billetera))
        .where(
            TarjetaCredito.usuario_id == usuario_id,
            TarjetaCredito.estado == EstadoTarjeta.ACTIVA,
        )
        .order_by(TarjetaCredito.nombre.asc(), TarjetaCredito.id.asc())
    ).scalars().all()

def _obtener_cotizacion_referencia_usuario(usuario: Usuario, db: Session) -> Decimal | None:
    """
    Obtiene la cotización de referencia según la preferencia del usuario desde la tabla
    cotizaciones_dolar (nunca inventa un valor ni consulta servicios externos).
    Retorna None si la tabla está vacía o no hay registros disponibles.
    """
    from app.services.dolar_service import obtener_cotizacion_por_fecha
    from app.models.cotizacion_dolar import CotizacionDolar
    from sqlalchemy import desc

    tipo_pref = getattr(usuario, "tipo_dolar", "blue") or "blue"
    hoy = hoy_argentina()

    # 1. Búsqueda por preferencia del usuario (fecha exacta o anterior más cercana)
    cot = obtener_cotizacion_por_fecha(db, tipo_pref, hoy)
    if cot is not None:
        val = cot.promedio or cot.venta or cot.compra
        if val and val > Decimal("0"):
            return val

    # 2. Fallback a 'blue' si la preferencia era distinta
    if tipo_pref.lower() != "blue":
        cot_blue = obtener_cotizacion_por_fecha(db, "blue", hoy)
        if cot_blue is not None:
            val = cot_blue.promedio or cot_blue.venta or cot_blue.compra
            if val and val > Decimal("0"):
                return val

    # 3. Fallback al registro más reciente en la tabla independientemente del tipo o fecha
    stmt_any = select(CotizacionDolar).order_by(desc(CotizacionDolar.fecha)).limit(1)
    cot_any = db.execute(stmt_any).scalars().first()
    if cot_any is not None:
        val = cot_any.promedio or cot_any.venta or cot_any.compra
        if val and val > Decimal("0"):
            return val

    return None

def _buscar_slot_filling_activo(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    conv = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.slot_filling_activo == True,
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()
    return conv

def _buscar_slot_filling_vencido(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    conv = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.slot_filling_activo == True,
            ConversacionWpp.fecha < limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()
    return conv

def _buscar_propuesta_pendiente(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    return db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.intent_detectado == "registrar_transaccion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.confianza >= Decimal("0.85"),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

def _buscar_propuesta_confirmable_mas_reciente(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    """
    Busca la propuesta pendiente más reciente entre todos los tipos confirmables (deshacer,
    corregir, transferir, suscripciones, registrar movimiento) dentro de la ventana de vigencia (30 min).
    Garantiza que la confirmación ('sí', 'dale') aplique a lo último que el bot propuso.
    """
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    intents_confirmables = [
        "deshacer",
        "corregir",
        "transferir_fondos",
        "dar_baja_suscripcion",
        "cambiar_precio_suscripcion",
        "agregar_suscripcion",
        "aportar_meta",
        "registrar_transaccion",
    ]
    return db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.intent_detectado.in_(intents_confirmables),
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
            or_(
                ConversacionWpp.intent_detectado != "registrar_transaccion",
                ConversacionWpp.confianza >= Decimal("0.85"),
            ),
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .limit(1)
    ).scalars().first()

def _buscar_transaccion_duplicada_reciente(
    usuario_id: UUID,
    monto: Decimal,
    moneda: Moneda,
    categoria_id: UUID | None,
    db: Session,
) -> Transaccion | None:
    """
    Busca una transacción confirmada del mismo usuario con el mismo monto, moneda y categoría,
    creada en la última hora (Tarea 3.1).
    Excluye movimientos generados de forma automática o diferida:
    - Cuotas hijas y padres de cuotas (planes de tarjeta de crédito)
    - Pagos automáticos de resúmenes de tarjeta
    - Débitos automáticos de suscripciones / recurrentes
    """
    limite = datetime.now(timezone.utc) - timedelta(hours=1)
    query = (
        select(Transaccion)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.monto == monto,
            Transaccion.moneda == moneda,
            Transaccion.fecha_creacion >= limite,
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.es_cuota_hija == False,
            Transaccion.es_padre_cuotas == False,
            Transaccion.es_recurrente == False,
            Transaccion.suscripcion_id.is_(None),
            Transaccion.pago_origen_id.is_(None),
            Transaccion.pago_resumen_vencimiento.is_(None),
        )
    )
    if categoria_id is not None:
        query = query.where(Transaccion.categoria_id == categoria_id)
    else:
        query = query.where(Transaccion.categoria_id.is_(None))
    return db.execute(query.order_by(Transaccion.fecha_creacion.desc(), Transaccion.id.desc())).scalars().first()

def _buscar_propuesta_transferencia_pendiente(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    return db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.intent_detectado == "transferir_fondos",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

def _buscar_ultimo_movimiento_whatsapp(usuario_id: UUID, db: Session) -> tuple[Transaccion | None, str | None]:
    """
    Identifica el último movimiento registrado por WhatsApp por el usuario dentro del plazo permitido.
    Retorna (transaccion, motivo_error_o_none).
    """
    conv_reciente = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.accion_ejecutada.is_not(None),
            ConversacionWpp.accion_ejecutada.not_in((
                "cancelada", "vencida", "descartado_por_duplicado",
                "descartada_por_nueva_operacion", "interrumpida_por_saludo",
                "test", "test_setup", "test_reset"
            )),
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

    tx_target_id = None
    if conv_reciente:
        accion = str(conv_reciente.accion_ejecutada)
        if accion.startswith("deshecho:"):
            return None, "YA_DESHECHO"
        if accion.startswith("lote:"):
            try:
                ids_str = accion.replace("lote:", "").split(",")
                uuids = [UUID(s.strip()) for s in ids_str if s.strip()]
                txs = db.execute(
                    select(Transaccion).where(
                        Transaccion.id.in_(uuids),
                        Transaccion.usuario_id == usuario_id,
                    ).order_by(Transaccion.fecha_creacion.asc(), Transaccion.id.asc())
                ).scalars().all()
                if not txs:
                    return None, "YA_BORRADO"
                limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_DESHACER_CORREGIR_MINUTOS)
                if txs[0].fecha_creacion < limite:
                    return None, "PLAZO_VENCIDO"
                return txs, None
            except Exception:
                pass
        if accion.startswith("transferencia:"):
            try:
                tr_id = UUID(accion.replace("transferencia:", ""))
                tr = db.execute(
                    select(TransferenciaInterna).where(
                        TransferenciaInterna.id == tr_id,
                        TransferenciaInterna.usuario_id == usuario_id,
                    )
                ).scalar_one_or_none()
                if not tr:
                    return None, "YA_BORRADO"
                limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_DESHACER_CORREGIR_MINUTOS)
                if tr.fecha_creacion < limite:
                    return None, "PLAZO_VENCIDO"
                return tr, None
            except ValueError:
                pass
        if accion.startswith("aporte_meta:"):
            try:
                mov_id = UUID(accion.replace("aporte_meta:", ""))
                tx = db.execute(
                    select(Transaccion).where(
                        Transaccion.movimiento_meta_id == mov_id,
                        Transaccion.usuario_id == usuario_id,
                    )
                ).scalar_one_or_none()
                if not tx:
                    return None, "YA_BORRADO"
                limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_DESHACER_CORREGIR_MINUTOS)
                if tx.fecha_creacion < limite:
                    return None, "PLAZO_VENCIDO"
                return tx, None
            except ValueError:
                pass
        try:
            tx_target_id = UUID(accion.replace("corregido:", ""))
        except ValueError:
            pass

    if tx_target_id:
        tx = db.execute(
            select(Transaccion).where(Transaccion.id == tx_target_id, Transaccion.usuario_id == usuario_id)
        ).scalar_one_or_none()
        if not tx:
            return None, "YA_BORRADO"
    else:
        tx = db.execute(
            select(Transaccion)
            .where(
                Transaccion.usuario_id == usuario_id,
                Transaccion.origen == OrigenTransaccion.IA_WPP,
            )
            .order_by(Transaccion.fecha_creacion.desc(), Transaccion.id.desc())
        ).scalars().first()
        if not tx:
            return None, "SIN_MOVIMIENTOS"

    # Verificar plazo temporal
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_DESHACER_CORREGIR_MINUTOS)
    if tx.fecha_creacion < limite:
        return None, "PLAZO_VENCIDO"

    # Restricciones (2.8 y Decisiones de Producto)
    if tx.origen != OrigenTransaccion.IA_WPP:
        return None, "ORIGEN_INVALIDO"
    if tx.es_cuota_hija:
        return None, "ES_CUOTA"
    if tx.pago_resumen_vencimiento is not None or tx.pago_origen_id is not None:
        return None, "ES_RESUMEN"
    if tx.movimiento_meta_id is not None or tx.descripcion.startswith("Aporte a la meta:") or tx.descripcion.startswith("Retiro de la meta:"):
        return None, "ES_META"
    if tx.es_recurrente:
        return None, "ES_RECURRENTE"

    return tx, None

def _buscar_propuesta_deshacer_pendiente(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_DESHACER_CORREGIR_MINUTOS)
    return db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.intent_detectado == "deshacer",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

def _buscar_propuesta_corregir_pendiente(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_DESHACER_CORREGIR_MINUTOS)
    return db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.intent_detectado == "corregir",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

def _buscar_suscripcion_cobrada_periodo_actual(
    usuario_id: UUID,
    monto: Decimal,
    nombre_servicio_o_concepto: str,
    db: Session,
) -> tuple[Suscripcion | None, Transaccion | None]:
    if not nombre_servicio_o_concepto or monto <= 0:
        return None, None

    subs_activas = db.query(Suscripcion).filter(
        Suscripcion.usuario_id == usuario_id,
        Suscripcion.estado == EstadoSuscripcion.ACTIVA,
    ).all()

    norm_concepto = normalizar_texto(nombre_servicio_o_concepto)
    hoy = hoy_argentina()
    limite_periodo = hoy - timedelta(days=32)

    for s in subs_activas:
        s_norm = normalizar_texto(s.nombre)
        coincide = (s_norm == norm_concepto or s_norm in norm_concepto or norm_concepto in s_norm)
        if not coincide:
            srv_cat = buscar_servicio_por_texto(s.nombre)
            if srv_cat:
                variantes = [normalizar_texto(v) for v in srv_cat.get("variantes", [])]
                if any(v in norm_concepto for v in variantes):
                    coincide = True

        if coincide:
            tx = db.execute(
                select(Transaccion)
                .where(
                    Transaccion.usuario_id == usuario_id,
                    Transaccion.suscripcion_id == s.id,
                    Transaccion.fecha >= limite_periodo,
                    Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
                )
                .order_by(Transaccion.fecha.desc())
            ).scalars().first()

            if tx and tx.monto == monto:
                return s, tx

    return None, None

def _buscar_propuesta_suscripcion_pendiente(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    return db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.intent_detectado == "agregar_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

def _buscar_propuesta_baja_suscripcion_pendiente(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    return db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.intent_detectado == "dar_baja_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

def _buscar_propuesta_cambio_precio_pendiente(usuario_id: UUID, db: Session) -> ConversacionWpp | None:
    limite = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)
    return db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.intent_detectado == "cambiar_precio_suscripcion",
            ConversacionWpp.slot_filling_activo == False,
            ConversacionWpp.accion_ejecutada.is_(None),
            ConversacionWpp.fecha >= limite,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
    ).scalars().first()

def _buscar_suscripcion_activa_por_nombre(usuario_id: UUID, nombre: str | None, db: Session) -> Suscripcion | None:
    subs = db.query(Suscripcion).options(joinedload(Suscripcion.historial)).filter(
        Suscripcion.usuario_id == usuario_id,
        Suscripcion.estado == EstadoSuscripcion.ACTIVA,
    ).all()
    if not subs:
        return None
    if not nombre:
        if len(subs) == 1:
            return subs[0]
        return None

    norm = normalizar_texto(nombre)
    for s in subs:
        s_norm = normalizar_texto(s.nombre)
        if s_norm == norm or norm in s_norm or s_norm in norm:
            return s
        srv_cat = buscar_servicio_por_texto(s.nombre)
        if srv_cat:
            variantes = [normalizar_texto(v) for v in srv_cat.get("variantes", [])]
            if any(v in norm or norm in v for v in variantes):
                return s
    return None

_STOPWORDS_META = {"de", "la", "el", "los", "las", "un", "una", "unos", "unas", "en", "para", "mi", "mis", "tu", "tus", "a", "al", "del"}

def _max_subcadena_comun_str(s1: str, s2: str) -> int:
    m = [[0] * (len(s2) + 1) for _ in range(len(s1) + 1)]
    max_len = 0
    for i in range(len(s1)):
        for j in range(len(s2)):
            if s1[i] == s2[j]:
                m[i + 1][j + 1] = m[i][j] + 1
                if m[i + 1][j + 1] > max_len:
                    max_len = m[i + 1][j + 1]
            else:
                m[i + 1][j + 1] = 0
    return max_len

def _buscar_meta_activa_por_nombre(
    usuario_id: UUID,
    nombre: str | None,
    db: Session,
) -> tuple[Meta | None, str, list[Meta]]:
    """
    Busca una meta activa del usuario por nombre.
    Retorna: (meta_encontrada_o_none, estado, candidatos)
    Estados posibles: 'ok', 'no_metas', 'no_encontrada', 'ambigua'.
    Reglas:
    - Match exacto primero (normalizado).
    - Si no hay match exacto: busca candidatos aproximados con 4+ caracteres comunes
      (subcadena común >= 4 en palabras significativas) o inclusión.
    - Si hay exactamente 1 candidato: retorna (cand, 'ok', [cand]).
    - Si hay más de 1 candidato: retorna (None, 'ambigua', cands).
    - Si hay 0 candidatos: retorna (None, 'no_encontrada', []).
    """
    metas = db.query(Meta).filter(
        Meta.usuario_id == usuario_id,
        Meta.estado == EstadoMeta.ACTIVA,
    ).all()

    if not metas:
        return None, "no_metas", []

    if not nombre or not nombre.strip():
        if len(metas) == 1:
            return metas[0], "ok", metas
        return None, "ambigua", metas

    n_norm = normalizar_texto(nombre).strip()

    # 1. Match exacto
    for m in metas:
        if normalizar_texto(m.nombre) == n_norm:
            return m, "ok", [m]

    # Limpiar stopwords de palabras significativas
    n_words = [w for w in n_norm.split() if w not in _STOPWORDS_META]
    n_clean = " ".join(n_words)

    # 2. Match aproximado
    candidatos = []
    for m in metas:
        m_norm = normalizar_texto(m.nombre)
        m_words = [w for w in m_norm.split() if w not in _STOPWORDS_META]
        m_clean = " ".join(m_words)

        match = False
        if n_clean and m_clean:
            if n_clean in m_clean or m_clean in n_clean:
                match = True
            else:
                for w1 in n_words:
                    if len(w1) >= 4:
                        for w2 in m_words:
                            if len(w2) >= 4 and _max_subcadena_comun_str(w1, w2) >= 4:
                                match = True
                                break
                    if match:
                        break
        elif n_norm in m_norm or m_norm in n_norm:
            match = True

        if match:
            candidatos.append(m)

    if len(candidatos) == 1:
        return candidatos[0], "ok", candidatos
    elif len(candidatos) > 1:
        return None, "ambigua", candidatos
    else:
        return None, "no_encontrada", []

def _obtener_historial_reciente(usuario_id: UUID, db: Session, n: int = 6) -> list[dict]:
    """
    Obtiene los últimos N turnos de conversación del usuario (por defecto 6).
    Solo incluye conversaciones de los últimos 30 minutos (PLAZO_EXPIRACION_ESTADO_MINUTOS).
    Incluye las preguntas que hizo el sistema para que la IA entienda a qué responde un 'sí'
    o una selección suelta, y utiliza la confianza y estado reales sin inventar valores fijos.
    """
    limite_tiempo = datetime.now(timezone.utc) - timedelta(minutes=PLAZO_EXPIRACION_ESTADO_MINUTOS)

    convs = db.execute(
        select(ConversacionWpp)
        .where(
            ConversacionWpp.usuario_id == usuario_id,
            ConversacionWpp.fecha >= limite_tiempo,
        )
        .order_by(ConversacionWpp.fecha.desc(), ConversacionWpp.id.desc())
        .limit(n)
    ).scalars().all()

    # Revertir para orden cronológico
    convs = list(reversed(convs))

    resultado = []
    for c in convs:
        # Excluir únicamente fallbacks genéricos de error técnico que no aportan contexto conversacional
        if (
            c.mensaje_bot.startswith("Hubo un problema al procesar tu mensaje")
            or c.mensaje_bot.startswith("No pude escuchar el audio")
            or c.mensaje_bot.startswith("No pude leer el comprobante")
        ):
            continue
        if c.intent_detectado is None:
            continue

        estado = c.slot_filling_estado or {}
        resultado.append({
            "usuario": c.mensaje_usuario,
            "bot": c.mensaje_bot,
            "intent": c.intent_detectado or "desconocido",
            "entidades": c.entidades or {},
            "confianza": float(c.confianza) if c.confianza is not None else None,
            "slot_filling": c.slot_filling_activo,
            "datos_faltantes": estado.get("datos_faltantes", []) if c.slot_filling_activo else [],
        })
    return resultado
