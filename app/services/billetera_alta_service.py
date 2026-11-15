# -*- coding: utf-8 -*-
"""
Servicio de alta rápida de billeteras para WhatsApp y operaciones automáticas.
Permite crear cuentas sin saldo cargado y sin punto de partida.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.usuario import Moneda


def crear_billetera_sin_saldo(
    db: Session,
    usuario_id: UUID,
    nombre: str,
    moneda: Moneda | str,
    entidad_id: str | None,
    commit: bool = True,
) -> Billetera:
    """
    Crea una billetera sin saldo inicial ni actual (0.00), sin fecha de saldo
    y con tiene_punto_partida=False, no principal, no efectivo, no inversión.
    """
    moneda_enum = (
        moneda if isinstance(moneda, Moneda)
        else (Moneda.USD if str(moneda).upper() == "USD" else Moneda.ARS)
    )

    nueva_billetera = Billetera(
        usuario_id=usuario_id,
        nombre=nombre.strip(),
        moneda=moneda_enum,
        saldo_inicial=Decimal("0"),
        saldo_actual=Decimal("0"),
        tiene_punto_partida=False,
        fecha_saldo=None,
        entidad_id=entidad_id,
        es_principal=False,
        es_efectivo=False,
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )
    db.add(nueva_billetera)
    if commit:
        db.commit()
        db.refresh(nueva_billetera)
    else:
        db.flush()
    return nueva_billetera
