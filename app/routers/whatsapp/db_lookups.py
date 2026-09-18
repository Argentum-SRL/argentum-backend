"""
Consultas y operaciones de base de datos para el flujo de WhatsApp IA.
Incluye verificaciones de rate limiting, resolución de usuarios, billeteras, tarjetas, categorías y cotizaciones.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.core.constants import CATEGORIAS_SISTEMA
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.models.usuario import EstadoUsuario, Moneda, Usuario
from app.routers.whatsapp.resolvers_cascada import resolver_billetera_cascada
from app.services.rate_limit_service import verificar_rate_limit
from app.utils.fecha import hoy_argentina
from app.utils.telefono import normalizar_telefono_ar
from app.utils.texto import normalizar_texto

logger = structlog.get_logger("whatsapp")

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
