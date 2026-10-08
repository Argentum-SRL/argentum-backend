from __future__ import annotations

from datetime import date, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.factura import Factura
from app.models.transaccion import TipoTransaccion, Transaccion
from app.models.usuario import Moneda
from app.services.memoria_comercio_service import clave_comercio
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


def crear_factura_pendiente(
    db: Session,
    usuario_id: UUID,
    descripcion: str,
    monto: Decimal,
    moneda: str | Moneda,
    fecha_vencimiento: date,
    origen: str,
    categoria_id: UUID | None = None,
    subcategoria_id: UUID | None = None,
    commit: bool = True,
) -> Factura:
    monto_dec = Decimal(str(monto))
    if monto_dec <= Decimal("0"):
        raise ValueError("El monto debe ser mayor a 0.")

    moneda_val = Moneda(moneda) if isinstance(moneda, str) else moneda

    # Si ya hay una pendiente del usuario con el mismo monto, moneda y fecha_vencimiento,
    # la devuelve con ya_existia = True y no crea otra.
    stmt = select(Factura).where(
        Factura.usuario_id == usuario_id,
        Factura.estado == "pendiente",
        Factura.monto == monto_dec,
        Factura.moneda == moneda_val,
        Factura.fecha_vencimiento == fecha_vencimiento,
    )
    existente = db.execute(stmt).scalars().first()
    if existente:
        setattr(existente, "ya_existia", True)
        return existente

    nueva = Factura(
        usuario_id=usuario_id,
        descripcion=descripcion,
        monto=monto_dec,
        moneda=moneda_val,
        fecha_vencimiento=fecha_vencimiento,
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id,
        estado="pendiente",
        pagada_automaticamente=False,
        transaccion_id=None,
        origen=origen,
    )
    setattr(nueva, "ya_existia", False)
    db.add(nueva)
    if commit:
        db.commit()
        db.refresh(nueva)
    else:
        db.flush()
    setattr(nueva, "ya_existia", False)
    return nueva


def listar_facturas(
    db: Session,
    usuario_id: UUID,
) -> list[Factura]:
    """
    GET /facturas: pendientes y pagadas automáticamente con vencimiento de hoy en adelante,
    por fecha_vencimiento ascendente.
    """
    hoy = hoy_argentina()
    stmt = (
        select(Factura)
        .options(joinedload(Factura.categoria), joinedload(Factura.subcategoria))
        .where(
            Factura.usuario_id == usuario_id,
            or_(
                Factura.estado == "pendiente",
                and_(
                    Factura.estado == "pagada",
                    Factura.pagada_automaticamente == True,
                    Factura.fecha_vencimiento >= hoy,
                ),
            ),
        )
        .order_by(Factura.fecha_vencimiento.asc())
    )
    return list(db.execute(stmt).scalars().all())


def marcar_pagada(
    db: Session,
    factura_id: UUID,
    usuario_id: UUID,
    transaccion_id: UUID | None = None,
    pagada_automaticamente: bool = False,
    commit: bool = True,
) -> Factura:
    factura = db.get(Factura, factura_id)
    if not factura or factura.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontré esa factura.")
    if factura.estado != "pendiente":
        raise HTTPException(status_code=400, detail="Esa factura ya no está pendiente.")

    factura.estado = "pagada"
    factura.pagada_automaticamente = pagada_automaticamente
    factura.transaccion_id = transaccion_id
    if commit:
        db.commit()
        db.refresh(factura)
    else:
        db.flush()
    return factura


def marcar_pagada_por_coincidencia(
    db: Session,
    transaccion: Transaccion,
    commit: bool = False,
) -> Factura | None:
    """
    Decisión 2:
    Al crear una transacción de egreso que no es hija de cuotas, se busca una factura que cumpla todo esto:
    - del mismo usuario, pendiente, misma moneda y monto exactamente igual (Decimal);
    - fecha de la transacción entre el día de llegada de la factura (created_at en hora de Argentina)
      y fecha_vencimiento + 10 días, ambos inclusive;
    - y además, al menos una de estas tres:
      a. misma subcategoría, si la factura tiene subcategoría;
      b. misma categoría, si la factura no tiene subcategoría y su categoría no es "Otros";
      c. clave_comercio de las dos descripciones no nula e igual.
    Si hay exactamente una candidata, queda pagada, con pagada_automaticamente = true y transaccion_id.
    Con 0 o 2 o más, no se marca nada.
    """
    if transaccion.tipo != TipoTransaccion.EGRESO and str(transaccion.tipo) != "egreso":
        return None
    if getattr(transaccion, "es_cuota_hija", False):
        return None

    stmt = (
        select(Factura)
        .options(joinedload(Factura.categoria), joinedload(Factura.subcategoria))
        .where(
            Factura.usuario_id == transaccion.usuario_id,
            Factura.estado == "pendiente",
            Factura.moneda == transaccion.moneda,
            Factura.monto == transaccion.monto,
        )
    )
    facturas_pendientes = db.execute(stmt).scalars().all()

    candidatas: list[Factura] = []
    clave_tx = clave_comercio(transaccion.descripcion)

    for f in facturas_pendientes:
        # 1. Ventana de fechas: entre día de llegada (created_at en hora Argentina) y vencimiento + 10 días
        if f.created_at.tzinfo is None:
            created_dt = f.created_at.replace(tzinfo=timezone.utc).astimezone(TZ_ARGENTINA)
        else:
            created_dt = f.created_at.astimezone(TZ_ARGENTINA)
        dia_llegada = created_dt.date()
        dia_max = f.fecha_vencimiento + timedelta(days=10)

        if not (dia_llegada <= transaccion.fecha <= dia_max):
            continue

        # 2. Criterios a, b, c
        # a. misma subcategoría, si la factura tiene subcategoría
        match_a = False
        if f.subcategoria_id is not None and transaccion.subcategoria_id is not None:
            if f.subcategoria_id == transaccion.subcategoria_id:
                match_a = True

        # b. misma categoría, si la factura no tiene subcategoría y su categoría no es "Otros"
        match_b = False
        if f.subcategoria_id is None and f.categoria_id is not None and transaccion.categoria_id is not None:
            cat_nombre = f.categoria.nombre if f.categoria else ""
            if f.categoria_id == transaccion.categoria_id and cat_nombre.strip().lower() != "otros":
                match_b = True

        # c. clave_comercio de las dos descripciones no nula e igual
        match_c = False
        clave_fac = clave_comercio(f.descripcion)
        if clave_tx is not None and clave_fac is not None and clave_tx == clave_fac:
            match_c = True

        if match_a or match_b or match_c:
            candidatas.append(f)

    if len(candidatas) == 1:
        f_elegida = candidatas[0]
        return marcar_pagada(
            db=db,
            factura_id=f_elegida.id,
            usuario_id=transaccion.usuario_id,
            transaccion_id=transaccion.id,
            pagada_automaticamente=True,
            commit=commit,
        )

    return None


def desmarcar(
    db: Session,
    factura_id: UUID,
    usuario_id: UUID,
    commit: bool = True,
) -> Factura:
    factura = db.get(Factura, factura_id)
    if not factura or factura.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontré esa factura.")
    if factura.estado != "pagada":
        raise HTTPException(status_code=400, detail="Esa factura no está marcada como pagada.")

    factura.estado = "pendiente"
    factura.pagada_automaticamente = False
    factura.transaccion_id = None
    if commit:
        db.commit()
        db.refresh(factura)
    else:
        db.flush()
    return factura


def descartar(
    db: Session,
    factura_id: UUID,
    usuario_id: UUID,
    commit: bool = True,
) -> Factura:
    factura = db.get(Factura, factura_id)
    if not factura or factura.usuario_id != usuario_id:
        raise HTTPException(status_code=404, detail="No encontré esa factura.")
    if factura.estado != "pendiente":
        raise HTTPException(status_code=400, detail="Solo podés descartar facturas pendientes.")

    factura.estado = "descartada"
    if commit:
        db.commit()
        db.refresh(factura)
    else:
        db.flush()
    return factura


def facturas_pagadas_por(
    db: Session,
    transaccion_ids: list[UUID],
) -> list[Factura]:
    if not transaccion_ids:
        return []
    stmt = select(Factura).where(Factura.transaccion_id.in_(transaccion_ids))
    return list(db.execute(stmt).scalars().all())


def al_eliminar_transaccion(
    db: Session,
    transaccion_id: UUID,
    commit: bool = False,
) -> list[Factura]:
    stmt = select(Factura).where(Factura.transaccion_id == transaccion_id)
    facturas = list(db.execute(stmt).scalars().all())
    for f in facturas:
        f.estado = "pendiente"
        f.transaccion_id = None
        f.pagada_automaticamente = False
    if commit:
        db.commit()
    else:
        db.flush()
    return facturas


def items_proximos_pagos(
    db: Session,
    usuario_id: UUID,
    hoy: date,
    limite_pagos: date,
) -> list[dict]:
    facturas_query = db.query(Factura).filter(
        Factura.usuario_id == usuario_id,
        or_(
            and_(
                Factura.estado == "pendiente",
                Factura.fecha_vencimiento >= hoy - timedelta(days=30),
                Factura.fecha_vencimiento <= limite_pagos,
            ),
            and_(
                Factura.estado == "pagada",
                Factura.pagada_automaticamente == True,
                Factura.fecha_vencimiento >= hoy,
                Factura.fecha_vencimiento <= limite_pagos,
            ),
        ),
    ).all()

    items = []
    for f in facturas_query:
        dias_rest = (f.fecha_vencimiento - hoy).days
        estado_fac = "vencida" if (f.estado == "pendiente" and f.fecha_vencimiento < hoy) else ("pagada" if f.estado == "pagada" else "pendiente")
        items.append({
            "id": str(f.id),
            "nombre": f"Factura de {f.descripcion}",
            "monto": float(f.monto),
            "moneda": f.moneda.value if hasattr(f.moneda, "value") else str(f.moneda),
            "fecha_cobro": f.fecha_vencimiento.isoformat(),
            "dias_restantes": dias_rest,
            "tipo": "factura",
            "color": None,
            "red": None,
            "billetera_nombre": None,
            "billetera_id": None,
            "es_vencido": dias_rest < 0,
            "factura_id": str(f.id),
            "estado_factura": estado_fac,
        })
    return items

