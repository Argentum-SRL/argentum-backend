# -*- coding: utf-8 -*-
"""
Servicio oficial de «¿Qué es esta plata?» y gestión de devoluciones vinculadas a gastos.
Ubicación: app/services/que_es_plata_service.py
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda
from app.services import presupuesto_service
from app.services.transaccion_service import obtener_transaccion


OPCIONES = ("me_pagaron", "parte_de_gasto", "paso_por_mi_cuenta")


def gastos_para_vincular(
    db: Session,
    usuario_id: UUID,
    moneda: Moneda | str,
    fecha: date,
    monto: Decimal,
    limite: int = 10,
) -> list[dict]:
    """
    Busca compras candidatas para vincular una plata que entró como parte de un gasto.
    Compras: tipo EGRESO, reintegro_de_id nulo, es_cuota_hija falso, pago_resumen_vencimiento nulo,
    movimiento_meta_id nulo, billetera que no es de inversión, estado confirmada o nulo,
    misma moneda, categoría distinta de «Ahorro», subcategoría distinta de Banco > «Tarjeta de crédito»
    y de «Pasó por mi cuenta», fecha entre fecha − 60 días y fecha + 60 días, y disponible >= monto.
    """
    moneda_val = moneda.value if hasattr(moneda, "value") else str(moneda)
    fecha_min = fecha - timedelta(days=60)
    fecha_max = fecha + timedelta(days=60)

    cat_ahorro_sq = select(Categoria.id).where(func.lower(Categoria.nombre) == "ahorro")
    subcat_tc_sq = (
        select(Subcategoria.id)
        .join(Categoria, Subcategoria.categoria_id == Categoria.id)
        .where(
            func.lower(Categoria.nombre) == "banco",
            func.lower(Subcategoria.nombre) == "tarjeta de crédito",
        )
    )
    subcat_paso_sq = select(Subcategoria.id).where(
        func.lower(Subcategoria.nombre) == "pasó por mi cuenta"
    )

    stmt = (
        select(Transaccion)
        .options(joinedload(Transaccion.categoria), joinedload(Transaccion.subcategoria))
        .join(Billetera, Transaccion.billetera_id == Billetera.id)
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.tipo == TipoTransaccion.EGRESO,
            Transaccion.reintegro_de_id.is_(None),
            Transaccion.es_cuota_hija.is_(False),
            Transaccion.pago_resumen_vencimiento.is_(None),
            Transaccion.movimiento_meta_id.is_(None),
            Billetera.es_inversion.is_(False),
            or_(
                Transaccion.estado_verificacion.is_(None),
                Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            ),
            Transaccion.moneda == moneda_val,
            or_(
                Transaccion.categoria_id.is_(None),
                Transaccion.categoria_id.not_in(cat_ahorro_sq),
            ),
            or_(
                Transaccion.subcategoria_id.is_(None),
                Transaccion.subcategoria_id.not_in(subcat_tc_sq),
            ),
            or_(
                Transaccion.subcategoria_id.is_(None),
                Transaccion.subcategoria_id.not_in(subcat_paso_sq),
            ),
            Transaccion.fecha >= fecha_min,
            Transaccion.fecha <= fecha_max,
        )
        .order_by(Transaccion.fecha.desc(), Transaccion.fecha_creacion.desc())
    )
    candidatos = db.execute(stmt).scalars().all()

    resultado = []
    for c in candidatos:
        suma_espejos = Decimal("0")
        if c.espejos_reintegro:
            for esp in c.espejos_reintegro:
                if not getattr(esp, "es_cuota_hija", False):
                    suma_espejos += abs(Decimal(str(esp.monto)))
        disponible = c.monto - suma_espejos
        if disponible >= monto:
            resultado.append({
                "id": c.id,
                "descripcion": c.descripcion,
                "fecha": c.fecha,
                "monto": c.monto,
                "disponible": disponible,
                "categoria_id": c.categoria_id,
                "subcategoria_id": c.subcategoria_id,
                "categoria_nombre": c.categoria.nombre if c.categoria else None,
                "subcategoria_nombre": c.subcategoria.nombre if c.subcategoria else None,
                "suscripcion_id": c.suscripcion_id,
            })
            if len(resultado) >= limite:
                break

    return resultado


def revertir_parte(db: Session, tx: Transaccion) -> Transaccion:
    """
    Revierte una fila espejo «parte» a un ingreso normal en Otros > Cobros de personas.
    No toca el saldo de la billetera.
    """
    tx.tipo = TipoTransaccion.INGRESO
    tx.monto = abs(Decimal(str(tx.monto)))
    tx.reintegro_de_id = None
    tx.tipo_espejo = None

    cat_otros = db.execute(
        select(Categoria).where(
            func.lower(Categoria.nombre) == "otros",
            Categoria.tipo == TipoTransaccion.INGRESO,
            Categoria.estado == "activa",
        )
    ).scalars().first()

    sub_cobros = None
    if cat_otros:
        sub_cobros = db.execute(
            select(Subcategoria).where(
                Subcategoria.categoria_id == cat_otros.id,
                func.lower(Subcategoria.nombre) == "cobros de personas",
                Subcategoria.estado == "activa",
            )
        ).scalars().first()

    tx.categoria_id = cat_otros.id if cat_otros else None
    tx.subcategoria_id = sub_cobros.id if sub_cobros else None
    return tx


def responder(
    db: Session,
    usuario_id: UUID,
    transaccion_id: UUID,
    opcion: str,
    gasto_id: Optional[UUID] = None,
    commit: bool = True,
) -> Transaccion:
    """
    Responde a la pregunta «¿Qué es esta plata?» para una transacción de ingreso o espejo «parte».
    Opciones: me_pagaron, parte_de_gasto, paso_por_mi_cuenta.
    """
    tx = obtener_transaccion(db, usuario_id, transaccion_id)

    billetera = tx.billetera or db.get(Billetera, tx.billetera_id)
    es_billetera_inv = billetera is not None and getattr(billetera, "es_inversion", False)

    es_ingreso_valido = (
        tx.tipo == TipoTransaccion.INGRESO
        and tx.movimiento_meta_id is None
        and not es_billetera_inv
    )
    es_espejo_parte = (
        tx.reintegro_de_id is not None
        and tx.tipo_espejo == "parte"
    )

    if not (es_ingreso_valido or es_espejo_parte):
        raise HTTPException(
            status_code=400,
            detail="Esto solo se puede hacer con plata que entró.",
        )

    if opcion not in OPCIONES:
        raise HTTPException(
            status_code=400,
            detail="Elegí una opción válida.",
        )

    def _get_cat_otros_ingreso():
        return db.execute(
            select(Categoria).where(
                func.lower(Categoria.nombre) == "otros",
                Categoria.tipo == TipoTransaccion.INGRESO,
                Categoria.estado == "activa",
            )
        ).scalars().first()

    def _get_subcat(cat_id: UUID, nombre: str):
        return db.execute(
            select(Subcategoria).where(
                Subcategoria.categoria_id == cat_id,
                func.lower(Subcategoria.nombre) == nombre.lower(),
                Subcategoria.estado == "activa",
            )
        ).scalars().first()

    if opcion == "me_pagaron":
        if tx.tipo_espejo == "parte":
            revertir_parte(db, tx)
        else:
            subcat_paso = db.execute(
                select(Subcategoria.id).where(func.lower(Subcategoria.nombre) == "pasó por mi cuenta")
            ).scalars().all()
            if tx.subcategoria_id in subcat_paso:
                cat_otros = _get_cat_otros_ingreso()
                if cat_otros:
                    sub_cobros = _get_subcat(cat_otros.id, "Cobros de personas")
                    tx.categoria_id = cat_otros.id
                    tx.subcategoria_id = sub_cobros.id if sub_cobros else None

    elif opcion == "paso_por_mi_cuenta":
        if tx.tipo_espejo == "parte":
            revertir_parte(db, tx)
        cat_otros = _get_cat_otros_ingreso()
        if cat_otros:
            sub_paso = _get_subcat(cat_otros.id, "Pasó por mi cuenta")
            tx.categoria_id = cat_otros.id
            tx.subcategoria_id = sub_paso.id if sub_paso else None

    elif opcion == "parte_de_gasto":
        if not gasto_id:
            raise HTTPException(status_code=400, detail="Elegí el gasto.")

        gasto = db.get(Transaccion, gasto_id)
        if not gasto or gasto.usuario_id != usuario_id:
            raise HTTPException(status_code=400, detail="Ese movimiento no es un gasto.")

        gasto_billetera = gasto.billetera or db.get(Billetera, gasto.billetera_id)
        es_gasto_inv = gasto_billetera is not None and getattr(gasto_billetera, "es_inversion", False)

        cat_gasto = gasto.categoria or (db.get(Categoria, gasto.categoria_id) if gasto.categoria_id else None)
        subcat_gasto = gasto.subcategoria or (db.get(Subcategoria, gasto.subcategoria_id) if gasto.subcategoria_id else None)

        es_cat_ahorro = cat_gasto is not None and getattr(cat_gasto, "nombre", "").lower() == "ahorro"
        es_subcat_tc = (
            cat_gasto is not None
            and getattr(cat_gasto, "nombre", "").lower() == "banco"
            and subcat_gasto is not None
            and getattr(subcat_gasto, "nombre", "").lower() == "tarjeta de crédito"
        )
        es_subcat_paso = subcat_gasto is not None and getattr(subcat_gasto, "nombre", "").lower() == "pasó por mi cuenta"
        estado_val = (
            gasto.estado_verificacion.value
            if hasattr(gasto.estado_verificacion, "value")
            else (str(gasto.estado_verificacion) if gasto.estado_verificacion else None)
        )

        if (
            gasto.tipo != TipoTransaccion.EGRESO
            or gasto.reintegro_de_id is not None
            or gasto.es_cuota_hija
            or gasto.pago_resumen_vencimiento is not None
            or gasto.movimiento_meta_id is not None
            or es_gasto_inv
            or estado_val not in (None, "confirmada")
            or es_cat_ahorro
            or es_subcat_tc
            or es_subcat_paso
        ):
            raise HTTPException(status_code=400, detail="Ese movimiento no es un gasto.")

        moneda_tx = tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda)
        moneda_gasto = gasto.moneda.value if hasattr(gasto.moneda, "value") else str(gasto.moneda)
        if moneda_gasto != moneda_tx:
            raise HTTPException(status_code=400, detail="El gasto tiene que estar en la misma moneda.")

        suma_espejos = Decimal("0")
        if gasto.espejos_reintegro:
            for esp in gasto.espejos_reintegro:
                if not getattr(esp, "es_cuota_hija", False) and esp.id != tx.id:
                    suma_espejos += abs(Decimal(str(esp.monto)))
        disponible_gasto = gasto.monto - suma_espejos

        monto_este = abs(Decimal(str(tx.monto)))
        if monto_este > disponible_gasto:
            raise HTTPException(status_code=400, detail="Es más de lo que queda de ese gasto.")

        tx.tipo = TipoTransaccion.EGRESO
        tx.monto = -monto_este
        tx.categoria_id = gasto.categoria_id
        tx.subcategoria_id = gasto.subcategoria_id
        tx.reintegro_de_id = gasto.id
        tx.tipo_espejo = "parte"
        if not (tx.descripcion or "").strip():
            tx.descripcion = f"Parte de: {gasto.descripcion}".strip()

    presupuesto_service.registrar_impacto_presupuesto(db, tx, revertir=False, commit=False)

    if commit:
        db.commit()
        db.refresh(tx)
    else:
        db.flush()

    return tx

