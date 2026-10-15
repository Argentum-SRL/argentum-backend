"""
Servicio oficial de historial unificado de movimientos financieros de Argentum.
Ubicación: app/services/historial_service.py

Integra en una única lista cronológica con etiquetas determinísticas y totales de control:
1. Transacciones visibles (modo general o por billetera).
2. Transferencias internas entre cuentas.
3. Rendimientos de cuentas remuneradas e inversiones.
4. Ajustes de saldo manuales/de auditoría.
5. Saldo inicial de billetera (modo billetera).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import Date, String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.subcategoria import Subcategoria
from app.models.transaccion import (
    MetodoPago,
    TipoTransaccion,
    Transaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.schemas.movimiento import (
    MovimientoItem,
    MovimientosResponse,
    TotalesMoneda,
)
from app.schemas.transaccion import TransaccionRead
from app.services.definiciones_service import (
    cargar_contexto,
    etiqueta_movimiento,
    visible_en_historial,
)
from app.services.saldo_billetera_service import fecha_creacion_billetera
from app.services.transaccion_service import _normalizar_texto_busqueda
from app.utils.fecha import hoy_argentina

# Constantes de etiquetas para movimientos no transaccionales
ETIQUETA_TRANSFERENCIA = ("Transferencia", "nada", "Entre tus cuentas: no es gasto ni ingreso")
ETIQUETA_RENDIMIENTO = ("Rendimiento", "nada", "No suma a tus ingresos")
ETIQUETA_AJUSTE_SALDO = ("Ajuste de saldo", "nada", "Corrección del saldo: no es gasto ni ingreso")
ETIQUETA_SALDO_INICIAL = ("Saldo inicial", "nada", "Con lo que arranca el historial de esta billetera")


def _norm_dt(dt: Optional[datetime]) -> datetime:
    """Normaliza un datetime a UTC y timezone-aware para ordenamientos seguros."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def listar_movimientos(
    db: Session,
    usuario_id: UUID,
    modo: str = "general",
    billetera_id: Optional[UUID] = None,
    billetera_ids: Optional[List[UUID]] = None,
    tipo: Optional[TipoTransaccion | str] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    categoria_id: Optional[UUID] = None,
    categoria_ids: Optional[List[UUID]] = None,
    subcategoria_id: Optional[UUID] = None,
    moneda: Optional[str] = None,
    estado_verificacion: Optional[str] = None,
    busqueda: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    hoy: Optional[date] = None,
) -> MovimientosResponse:
    """
    Obtiene la lista de movimientos unificada para el usuario según el modo y filtros solicitados.
    """
    ref_hoy = hoy or hoy_argentina()
    ctx = cargar_contexto(db, usuario_id, hoy=ref_hoy)

    # 1. Billeteras del usuario para resolución rápida de nombres y monedas
    billeteras = db.execute(
        select(Billetera).where(Billetera.usuario_id == usuario_id)
    ).scalars().all()
    billeteras_map: Dict[UUID, Billetera] = {b.id: b for b in billeteras}

    # Billetera específica si estamos en modo billetera
    billetera_actual: Optional[Billetera] = billeteras_map.get(billetera_id) if billetera_id else None

    # Mapeo de padres de cuotas para títulos de cuotas hijas
    padres_rows = db.execute(
        select(Transaccion.grupo_cuotas_id, Transaccion.descripcion).where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.es_padre_cuotas.is_(True),
        )
    ).all()
    padres_desc_map: Dict[UUID, str] = {
        r[0]: r[1] for r in padres_rows if r[0] is not None
    }

    # Mapeo de número de cuota y cantidad total
    cuotas_info_rows = db.execute(
        select(Cuota.transaccion_id, Cuota.numero_cuota, GrupoCuotas.cantidad_cuotas)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .where(GrupoCuotas.usuario_id == usuario_id)
    ).all()
    cuotas_info_map: Dict[UUID, tuple[int, int]] = {
        r[0]: (r[1], r[2]) for r in cuotas_info_rows
    }

    # Reglas de activación de filtros exclusivos de transacciones
    solo_transacciones = bool(
        tipo is not None
        or categoria_id is not None
        or (categoria_ids is not None and len(categoria_ids) > 0)
        or subcategoria_id is not None
        or estado_verificacion is not None
    )
    busqueda_activa = bool(busqueda and busqueda.strip())
    term = f"%{busqueda.strip().lower()}%" if busqueda_activa else None

    # Lista intermedia para acumular todos los movimientos antes de orden y paginado
    raw_movimientos: List[dict] = []

    # ==========================================================================
    # A. TRANSACCIONES
    # ==========================================================================
    tx_conds = [Transaccion.usuario_id == usuario_id]

    if modo == "billetera":
        if billetera_id is not None:
            tx_conds.append(Transaccion.billetera_id == billetera_id)
        tx_conds.append(
            or_(
                Transaccion.metodo_pago != MetodoPago.CREDITO,
                Transaccion.metodo_pago.is_(None),
            )
        )
        tx_conds.append(Transaccion.es_padre_cuotas.is_(False))
        tx_conds.append(Transaccion.es_cuota_hija.is_(False))
    else:
        # Modo general
        if billetera_id is not None:
            tx_conds.append(Transaccion.billetera_id == billetera_id)
        if billetera_ids:
            tx_conds.append(Transaccion.billetera_id.in_(billetera_ids))

    if fecha_desde is not None:
        tx_conds.append(Transaccion.fecha >= fecha_desde)
    if fecha_hasta is not None:
        tx_conds.append(Transaccion.fecha <= fecha_hasta)
    if tipo is not None:
        t_val = tipo.value if hasattr(tipo, "value") else str(tipo)
        tx_conds.append(Transaccion.tipo == t_val)
    if categoria_id is not None:
        tx_conds.append(Transaccion.categoria_id == categoria_id)
    if categoria_ids:
        tx_conds.append(Transaccion.categoria_id.in_(categoria_ids))
    if subcategoria_id is not None:
        tx_conds.append(Transaccion.subcategoria_id == subcategoria_id)
    if moneda is not None:
        m_val = moneda.value if hasattr(moneda, "value") else str(moneda)
        tx_conds.append(Transaccion.moneda == m_val)
    if estado_verificacion is not None:
        e_val = estado_verificacion.value if hasattr(estado_verificacion, "value") else str(estado_verificacion)
        tx_conds.append(Transaccion.estado_verificacion == e_val)

    if busqueda_activa and busqueda:
        term = _normalizar_texto_busqueda(busqueda.strip())
        patron = f"%{term}%"
        # Búsqueda por descripción, categoría, subcategoría, nombre de la billetera y metodo_pago
        # Además se conserva la búsqueda por la descripción del padre para las hijas
        grupos_coincidentes_sq = (
            select(Transaccion.grupo_cuotas_id).where(
                Transaccion.usuario_id == usuario_id,
                Transaccion.es_padre_cuotas.is_(True),
                func.translate(func.lower(Transaccion.descripcion), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
            )
        )
        tx_conds.append(
            or_(
                func.translate(func.lower(Transaccion.descripcion), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
                func.translate(func.lower(Categoria.nombre), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
                func.translate(func.lower(Subcategoria.nombre), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
                func.translate(func.lower(Billetera.nombre), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
                func.translate(func.lower(cast(Transaccion.metodo_pago, String)), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
                and_(
                    Transaccion.es_cuota_hija.is_(True),
                    Transaccion.grupo_cuotas_id.in_(grupos_coincidentes_sq),
                ),
            )
        )

    stmt_tx = (
        select(Transaccion)
        .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
        .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
        .outerjoin(Billetera, Transaccion.billetera_id == Billetera.id)
        .where(and_(*tx_conds))
    )
    tx_list = db.execute(stmt_tx).scalars().all()

    for tx in tx_list:
        if modo == "general" and not visible_en_historial(tx, ctx):
            continue

        etiqueta, cuenta_como, aclaracion = etiqueta_movimiento(tx, ctx)

        # Descripción y cuota texto
        cuota_texto = None
        descripcion = tx.descripcion or "Transacción"
        if tx.es_cuota_hija:
            c_info = cuotas_info_map.get(tx.id)
            if c_info:
                n, m = c_info
                cuota_texto = f"{n}/{m}"
                p_desc = padres_desc_map.get(tx.grupo_cuotas_id) if tx.grupo_cuotas_id else None
                base_desc = p_desc or tx.descripcion or "Compra"
                descripcion = f"{base_desc} {cuota_texto}"
            else:
                cuota_texto = None

        monto_dec = abs(Decimal(str(tx.monto)))
        moneda_str = tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda)
        tipo_str = tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo)
        sentido = "entra" if tipo_str == "ingreso" else "sale"

        b_obj = billeteras_map.get(tx.billetera_id)
        b_nom = b_obj.nombre if b_obj else None

        # Evaluación de impacto en saldo
        est_val = (
            tx.estado_verificacion.value
            if hasattr(tx.estado_verificacion, "value")
            else (str(tx.estado_verificacion) if tx.estado_verificacion is not None else None)
        )
        cuenta_en_saldo = (est_val != "pendiente") and (tx.fecha <= ref_hoy)

        raw_movimientos.append({
            "id": f"transaccion:{tx.id}",
            "clase": "transaccion",
            "fecha": tx.fecha,
            "fecha_creacion": tx.fecha_creacion,
            "descripcion": descripcion,
            "monto": monto_dec,
            "moneda": moneda_str,
            "sentido": sentido,
            "etiqueta": etiqueta,
            "cuenta_como": cuenta_como,
            "aclaracion": aclaracion,
            "cuenta_en_saldo": cuenta_en_saldo,
            "saldo_despues": None,
            "cuota_texto": cuota_texto,
            "grupo_cuotas_id": tx.grupo_cuotas_id,
            "billetera_id": tx.billetera_id,
            "billetera_nombre": b_nom,
            "billetera_destino_nombre": None,
            "monto_destino": None,
            "moneda_destino": None,
            "transaccion": None,
            "prioridad_saldo_inicial": 1,
            "entity": tx,
        })

    # ==========================================================================
    # B. TRANSFERENCIAS INTERNAS
    # ==========================================================================
    if not solo_transacciones:
        tr_conds = [TransferenciaInterna.usuario_id == usuario_id]

        if fecha_desde is not None:
            tr_conds.append(TransferenciaInterna.fecha >= fecha_desde)
        if fecha_hasta is not None:
            tr_conds.append(TransferenciaInterna.fecha <= fecha_hasta)

        if modo == "billetera" and billetera_id is not None:
            tr_conds.append(
                or_(
                    TransferenciaInterna.billetera_origen_id == billetera_id,
                    TransferenciaInterna.billetera_destino_id == billetera_id,
                )
            )
        elif modo == "general":
            if billetera_id is not None:
                tr_conds.append(
                    or_(
                        TransferenciaInterna.billetera_origen_id == billetera_id,
                        TransferenciaInterna.billetera_destino_id == billetera_id,
                    )
                )
            elif billetera_ids:
                tr_conds.append(
                    or_(
                        TransferenciaInterna.billetera_origen_id.in_(billetera_ids),
                        TransferenciaInterna.billetera_destino_id.in_(billetera_ids),
                    )
                )

        if moneda is not None:
            m_val = moneda.value if hasattr(moneda, "value") else str(moneda)
            tr_conds.append(
                or_(
                    TransferenciaInterna.moneda_origen == m_val,
                    TransferenciaInterna.moneda_destino == m_val,
                    TransferenciaInterna.moneda == m_val,
                )
            )

        if busqueda_activa and busqueda:
            term = _normalizar_texto_busqueda(busqueda.strip())
            patron = f"%{term}%"
            b_orig = Billetera.__table__.alias("b_orig")
            b_dest = Billetera.__table__.alias("b_dest")
            stmt_tr = (
                select(TransferenciaInterna)
                .join(b_orig, TransferenciaInterna.billetera_origen_id == b_orig.c.id)
                .join(b_dest, TransferenciaInterna.billetera_destino_id == b_dest.c.id)
                .where(
                    and_(*tr_conds),
                    or_(
                        func.translate(func.lower(TransferenciaInterna.notas), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
                        func.translate(func.lower(b_orig.c.nombre), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
                        func.translate(func.lower(b_dest.c.nombre), 'áéíóúü', 'aeiouu').like(patron, escape="\\"),
                    ),
                )
            )
        else:
            stmt_tr = select(TransferenciaInterna).where(and_(*tr_conds))

        tr_list = db.execute(stmt_tr).scalars().all()

        for tr in tr_list:
            orig = billeteras_map.get(tr.billetera_origen_id)
            dest = billeteras_map.get(tr.billetera_destino_id)
            orig_nombre = orig.nombre if orig else "Origen"
            dest_nombre = dest.nombre if dest else "Destino"

            m_orig = tr.monto_origen if tr.monto_origen is not None else tr.monto
            m_dest = tr.monto_destino if tr.monto_destino is not None else tr.monto
            mon_orig = (
                tr.moneda_origen.value
                if hasattr(tr.moneda_origen, "value")
                else (str(tr.moneda_origen) if tr.moneda_origen else (tr.moneda.value if hasattr(tr.moneda, "value") else str(tr.moneda)))
            )
            mon_dest = (
                tr.moneda_destino.value
                if hasattr(tr.moneda_destino, "value")
                else (str(tr.moneda_destino) if tr.moneda_destino else mon_orig)
            )

            etiqueta, cuenta_como, aclaracion = ETIQUETA_TRANSFERENCIA
            cuenta_en_saldo = tr.fecha <= ref_hoy

            if modo == "general":
                raw_movimientos.append({
                    "id": f"transferencia:{tr.id}",
                    "clase": "transferencia",
                    "fecha": tr.fecha,
                    "fecha_creacion": tr.fecha_creacion,
                    "descripcion": f"{orig_nombre} → {dest_nombre}",
                    "monto": abs(Decimal(str(m_orig))),
                    "moneda": mon_orig,
                    "sentido": "neutro",
                    "etiqueta": etiqueta,
                    "cuenta_como": cuenta_como,
                    "aclaracion": aclaracion,
                    "cuenta_en_saldo": cuenta_en_saldo,
                    "saldo_despues": None,
                    "cuota_texto": None,
                    "grupo_cuotas_id": None,
                    "billetera_id": tr.billetera_origen_id,
                    "billetera_nombre": orig_nombre,
                    "billetera_destino_nombre": dest_nombre,
                    "monto_destino": abs(Decimal(str(m_dest))),
                    "moneda_destino": mon_dest,
                    "transaccion": None,
                    "prioridad_saldo_inicial": 1,
                    "entity": tr,
                })
            else:
                # Modo billetera: si es origen sale, si es destino entra
                es_saliente = tr.billetera_origen_id == billetera_id
                desc_tr = f"A {dest_nombre}" if es_saliente else f"De {orig_nombre}"
                monto_tr = m_orig if es_saliente else m_dest
                moneda_tr = mon_orig if es_saliente else mon_dest
                sentido_tr = "sale" if es_saliente else "entra"

                raw_movimientos.append({
                    "id": f"transferencia:{tr.id}",
                    "clase": "transferencia",
                    "fecha": tr.fecha,
                    "fecha_creacion": tr.fecha_creacion,
                    "descripcion": desc_tr,
                    "monto": abs(Decimal(str(monto_tr))),
                    "moneda": moneda_tr,
                    "sentido": sentido_tr,
                    "etiqueta": etiqueta,
                    "cuenta_como": cuenta_como,
                    "aclaracion": aclaracion,
                    "cuenta_en_saldo": cuenta_en_saldo,
                    "saldo_despues": None,
                    "cuota_texto": None,
                    "grupo_cuotas_id": None,
                    "billetera_id": tr.billetera_origen_id if es_saliente else tr.billetera_destino_id,
                    "billetera_nombre": orig_nombre if es_saliente else dest_nombre,
                    "billetera_destino_nombre": dest_nombre if es_saliente else None,
                    "monto_destino": abs(Decimal(str(m_dest))) if es_saliente else None,
                    "moneda_destino": mon_dest if es_saliente else None,
                    "transaccion": None,
                    "prioridad_saldo_inicial": 1,
                    "entity": tr,
                    "es_saliente": es_saliente,
                })

    # ==========================================================================
    # C. RENDIMIENTOS (rendimientos_billetera)
    # ==========================================================================
    if not solo_transacciones and not busqueda_activa:
        rend_conds = [Billetera.usuario_id == usuario_id]
        if db.bind and db.bind.dialect.name == "sqlite":
            if fecha_desde is not None:
                rend_conds.append(func.date(RendimientoBilletera.fecha) >= str(fecha_desde))
            if fecha_hasta is not None:
                rend_conds.append(func.date(RendimientoBilletera.fecha) <= str(fecha_hasta))
        else:
            if fecha_desde is not None:
                rend_conds.append(cast(RendimientoBilletera.fecha, Date) >= fecha_desde)
            if fecha_hasta is not None:
                rend_conds.append(cast(RendimientoBilletera.fecha, Date) <= fecha_hasta)
        if billetera_id is not None:
            rend_conds.append(RendimientoBilletera.billetera_id == billetera_id)
        elif billetera_ids:
            rend_conds.append(RendimientoBilletera.billetera_id.in_(billetera_ids))
        if moneda is not None:
            m_val = moneda.value if hasattr(moneda, "value") else str(moneda)
            rend_conds.append(Billetera.moneda == m_val)

        stmt_rend = (
            select(RendimientoBilletera)
            .join(Billetera, RendimientoBilletera.billetera_id == Billetera.id)
            .where(and_(*rend_conds))
        )
        rend_list = db.execute(stmt_rend).scalars().all()

        for r in rend_list:
            b_obj = billeteras_map.get(r.billetera_id)
            b_nom = b_obj.nombre if b_obj else None
            mon_b = (
                b_obj.moneda.value
                if (b_obj and hasattr(b_obj.moneda, "value"))
                else (str(b_obj.moneda) if b_obj else "ARS")
            )
            r_fecha_date = r.fecha.date() if isinstance(r.fecha, datetime) else r.fecha
            etiqueta, cuenta_como, aclaracion = ETIQUETA_RENDIMIENTO
            cuenta_en_saldo = r_fecha_date <= ref_hoy

            raw_movimientos.append({
                "id": f"rendimiento:{r.id}",
                "clase": "rendimiento",
                "fecha": r_fecha_date,
                "fecha_creacion": r.fecha_creacion or r.fecha,
                "descripcion": "Rendimiento",
                "monto": abs(Decimal(str(r.monto))),
                "moneda": mon_b,
                "sentido": "entra",
                "etiqueta": etiqueta,
                "cuenta_como": cuenta_como,
                "aclaracion": aclaracion,
                "cuenta_en_saldo": cuenta_en_saldo,
                "saldo_despues": None,
                "cuota_texto": None,
                "grupo_cuotas_id": None,
                "billetera_id": r.billetera_id,
                "billetera_nombre": b_nom,
                "billetera_destino_nombre": None,
                "monto_destino": None,
                "moneda_destino": None,
                "transaccion": None,
                "prioridad_saldo_inicial": 1,
                "entity": r,
            })

    # ==========================================================================
    # D. AJUSTES DE SALDO (ajustes_saldo)
    # ==========================================================================
    if not solo_transacciones and not busqueda_activa:
        aj_conds = [Billetera.usuario_id == usuario_id]
        if fecha_desde is not None:
            aj_conds.append(AjusteSaldo.fecha >= fecha_desde)
        if fecha_hasta is not None:
            aj_conds.append(AjusteSaldo.fecha <= fecha_hasta)
        if billetera_id is not None:
            aj_conds.append(AjusteSaldo.billetera_id == billetera_id)
        elif billetera_ids:
            aj_conds.append(AjusteSaldo.billetera_id.in_(billetera_ids))
        if moneda is not None:
            m_val = moneda.value if hasattr(moneda, "value") else str(moneda)
            aj_conds.append(Billetera.moneda == m_val)

        stmt_aj = (
            select(AjusteSaldo)
            .join(Billetera, AjusteSaldo.billetera_id == Billetera.id)
            .where(and_(*aj_conds))
        )
        aj_list = db.execute(stmt_aj).scalars().all()

        for aj in aj_list:
            b_obj = billeteras_map.get(aj.billetera_id)
            b_nom = b_obj.nombre if b_obj else None
            mon_b = (
                b_obj.moneda.value
                if (b_obj and hasattr(b_obj.moneda, "value"))
                else (str(b_obj.moneda) if b_obj else "ARS")
            )
            etiqueta, cuenta_como, aclaracion = ETIQUETA_AJUSTE_SALDO
            sentido = "entra" if aj.monto >= 0 else "sale"
            cuenta_en_saldo = aj.fecha <= ref_hoy

            raw_movimientos.append({
                "id": f"ajuste:{aj.id}",
                "clase": "ajuste",
                "fecha": aj.fecha,
                "fecha_creacion": aj.fecha_creacion,
                "descripcion": "Ajuste de saldo",
                "monto": abs(Decimal(str(aj.monto))),
                "moneda": mon_b,
                "sentido": sentido,
                "etiqueta": etiqueta,
                "cuenta_como": cuenta_como,
                "aclaracion": aclaracion,
                "cuenta_en_saldo": cuenta_en_saldo,
                "saldo_despues": None,
                "cuota_texto": None,
                "grupo_cuotas_id": None,
                "billetera_id": aj.billetera_id,
                "billetera_nombre": b_nom,
                "billetera_destino_nombre": None,
                "monto_destino": None,
                "moneda_destino": None,
                "transaccion": None,
                "prioridad_saldo_inicial": 1,
                "entity": aj,
            })

    # ==========================================================================
    # E. SALDO INICIAL (Solo modo billetera)
    # ==========================================================================
    if modo == "billetera" and billetera_actual and not solo_transacciones and not busqueda_activa:
        fc_arg = fecha_creacion_billetera(billetera_actual)
        fechas_movs_saldo = [
            m["fecha"] for m in raw_movimientos if m.get("cuenta_en_saldo")
        ]
        if fechas_movs_saldo:
            fecha_saldo_inicial = min(fc_arg, min(fechas_movs_saldo))
        else:
            fecha_saldo_inicial = fc_arg

        cumple_desde = fecha_desde is None or fecha_saldo_inicial >= fecha_desde
        cumple_hasta = fecha_hasta is None or fecha_saldo_inicial <= fecha_hasta
        mon_b = (
            billetera_actual.moneda.value
            if hasattr(billetera_actual.moneda, "value")
            else str(billetera_actual.moneda)
        )
        cumple_moneda = moneda is None or mon_b == (moneda.value if hasattr(moneda, "value") else str(moneda))

        if cumple_desde and cumple_hasta and cumple_moneda:
            saldo_ini_dec = billetera_actual.saldo_inicial or Decimal("0.00")
            raw_movimientos.append({
                "id": f"saldo_inicial:{billetera_actual.id}",
                "clase": "saldo_inicial",
                "fecha": fecha_saldo_inicial,
                "fecha_creacion": datetime.min.replace(tzinfo=timezone.utc),
                "descripcion": "Saldo inicial",
                "monto": abs(saldo_ini_dec),
                "moneda": mon_b,
                "sentido": "entra" if saldo_ini_dec >= 0 else "sale",
                "etiqueta": ETIQUETA_SALDO_INICIAL[0],
                "cuenta_como": ETIQUETA_SALDO_INICIAL[1],
                "aclaracion": ETIQUETA_SALDO_INICIAL[2],
                "cuenta_en_saldo": True,
                "saldo_despues": saldo_ini_dec,
                "cuota_texto": None,
                "grupo_cuotas_id": None,
                "billetera_id": billetera_actual.id,
                "billetera_nombre": billetera_actual.nombre,
                "billetera_destino_nombre": None,
                "monto_destino": None,
                "moneda_destino": None,
                "transaccion": None,
                "prioridad_saldo_inicial": 0,
                "entity": None,
            })

    # ==========================================================================
    # F. CÁLCULO DE SALDO_DESPUES (Solo modo billetera)
    # ==========================================================================
    if modo == "billetera" and billetera_actual:
        # Orden ascendente: Saldo inicial primero, luego fecha asc y fecha_creacion asc
        raw_movimientos.sort(
            key=lambda item: (
                item["prioridad_saldo_inicial"],
                item["fecha"],
                _norm_dt(item["fecha_creacion"]),
            )
        )

        running_saldo = Decimal("0.00")
        for m in raw_movimientos:
            clase = m["clase"]
            if clase == "saldo_inicial":
                running_saldo = billetera_actual.saldo_inicial or Decimal("0.00")
                m["saldo_despues"] = running_saldo
                m["cuenta_en_saldo"] = True
            elif clase == "transaccion":
                if m["cuenta_en_saldo"]:
                    if m["sentido"] == "entra":
                        running_saldo += m["monto"]
                    else:
                        running_saldo -= m["monto"]
                    m["saldo_despues"] = running_saldo
                else:
                    m["saldo_despues"] = None
            elif clase == "transferencia":
                if m["cuenta_en_saldo"]:
                    if m.get("es_saliente", False):
                        running_saldo -= m["monto"]
                    else:
                        running_saldo += m["monto"]
                    m["saldo_despues"] = running_saldo
                else:
                    m["saldo_despues"] = None
            elif clase == "rendimiento":
                if m["cuenta_en_saldo"]:
                    running_saldo += m["monto"]
                    m["saldo_despues"] = running_saldo
                else:
                    m["saldo_despues"] = None
            elif clase == "ajuste":
                if m["cuenta_en_saldo"]:
                    aj_monto = m["entity"].monto
                    running_saldo += Decimal(str(aj_monto))
                    m["saldo_despues"] = running_saldo
                else:
                    m["saldo_despues"] = None

    # ==========================================================================
    # G. ORDEN FINAL DE PANTALLA (Descendente por fecha y fecha_creacion)
    if modo == "billetera":
        raw_movimientos.sort(
            key=lambda item: (
                item["prioridad_saldo_inicial"],
                item["fecha"],
                _norm_dt(item["fecha_creacion"]),
            ),
            reverse=True,
        )
    else:
        raw_movimientos.sort(
            key=lambda item: (
                item["fecha"],
                _norm_dt(item["fecha_creacion"]),
            ),
            reverse=True,
        )

    # ==========================================================================
    # H. TOTALES GENERALES (Calculados antes de paginar)
    # ==========================================================================
    totales_dict: Dict[str, TotalesMoneda] = {
        "ARS": TotalesMoneda(gastos=Decimal("0.00"), ingresos=Decimal("0.00"), balance=Decimal("0.00")),
        "USD": TotalesMoneda(gastos=Decimal("0.00"), ingresos=Decimal("0.00"), balance=Decimal("0.00")),
    }

    for m in raw_movimientos:
        mon = m["moneda"]
        if mon not in totales_dict:
            totales_dict[mon] = TotalesMoneda(gastos=Decimal("0.00"), ingresos=Decimal("0.00"), balance=Decimal("0.00"))

        if m["cuenta_como"] == "gasto":
            totales_dict[mon].gastos += m["monto"]
        elif m["cuenta_como"] == "ingreso":
            totales_dict[mon].ingresos += m["monto"]

    for tot in totales_dict.values():
        tot.balance = tot.ingresos - tot.gastos

    total_items = len(raw_movimientos)
    paged_raw = raw_movimientos[skip : skip + limit]
    hay_mas = (skip + limit) < total_items

    # Conversión a esquemas Pydantic (solo se valida para la página solicitada)
    items = []
    for m in paged_raw:
        tx_read = None
        if m["clase"] == "transaccion" and m.get("entity") is not None:
            try:
                tx_read = TransaccionRead.model_validate(m["entity"])
            except Exception:
                tx_read = None

        items.append(
            MovimientoItem(
                id=m["id"],
                clase=m["clase"],
                fecha=m["fecha"],
                descripcion=m["descripcion"],
                monto=m["monto"],
                moneda=m["moneda"],
                sentido=m["sentido"],
                etiqueta=m["etiqueta"],
                cuenta_como=m["cuenta_como"],
                aclaracion=m["aclaracion"],
                cuenta_en_saldo=m["cuenta_en_saldo"],
                saldo_despues=m["saldo_despues"],
                cuota_texto=m["cuota_texto"],
                grupo_cuotas_id=m["grupo_cuotas_id"],
                billetera_id=m["billetera_id"],
                billetera_nombre=m["billetera_nombre"],
                billetera_destino_nombre=m["billetera_destino_nombre"],
                monto_destino=m["monto_destino"],
                moneda_destino=m["moneda_destino"],
                transaccion=tx_read,
            )
        )

    return MovimientosResponse(
        items=items,
        hay_mas=hay_mas,
        total_items=total_items,
        totales=totales_dict,
    )
