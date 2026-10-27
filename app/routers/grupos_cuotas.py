from uuid import UUID
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, joinedload

from app.core.database import get_db
from app.core.auth import get_current_user
from app.core.constants import MAX_MONTO_INTEGRIDAD
from app.models.usuario import Usuario
from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
from app.models.tarjeta_credito import TarjetaCredito
from app.models.cuota import Cuota
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, MetodoPago
from app.models.billetera import Billetera
from app.schemas.grupos_cuotas import GrupoCuotasResumen, GrupoCuotasUpdate, PlanCuotas, SimularCuotasResponse
from app.services.transaccion_service import _hoy_argentina
from app.services.cuotas_service import calcular_tasas_cuotas
from app.services import idempotencia_service
from app.utils.cuotas import monto_efectivo_cuota, calcular_plan_cuotas
from app.utils.tasas import tna_a_mensual

router = APIRouter(prefix="/grupos-cuotas", tags=["grupos_cuotas"])

def mapear_grupo_resumen(db: Session, grupo: GrupoCuotas) -> dict:
    cuotas = grupo.cuotas
    pagadas = [c for c in cuotas if c.pagada]
    pendientes = [c for c in cuotas if not c.pagada]
    
    cantidad_pagadas = len(pagadas)
    cantidad_pendientes = len(pendientes)
    
    proximo_vencimiento = None
    if pendientes:
        proximo_vencimiento = min(c.fecha_vencimiento for c in pendientes)
        
    if pendientes:
        pendientes_sorted = sorted(pendientes, key=lambda c: c.numero_cuota)
        monto_cuota = monto_efectivo_cuota(pendientes_sorted[0])
    elif pagadas:
        pagadas_sorted = sorted(pagadas, key=lambda c: c.numero_cuota)
        monto_cuota = monto_efectivo_cuota(pagadas_sorted[-1])
    else:
        monto_cuota = Decimal(0)
        
    total_pagado = sum(monto_efectivo_cuota(c) for c in pagadas)
    total_pendiente = sum(monto_efectivo_cuota(c) for c in pendientes)
    
    tarjeta_nombre = grupo.tarjeta.nombre if grupo.tarjeta else None

    categoria_id = None
    subcategoria_id = None
    if pendientes:
        pendientes_sorted = sorted(pendientes, key=lambda c: c.numero_cuota)
        if pendientes_sorted[0].transaccion:
            categoria_id = pendientes_sorted[0].transaccion.categoria_id
            subcategoria_id = pendientes_sorted[0].transaccion.subcategoria_id
    elif pagadas:
        pagadas_sorted = sorted(pagadas, key=lambda c: c.numero_cuota)
        if pagadas_sorted[-1].transaccion:
            categoria_id = pagadas_sorted[-1].transaccion.categoria_id
            subcategoria_id = pagadas_sorted[-1].transaccion.subcategoria_id
    elif grupo.transaccion_padre:
        categoria_id = grupo.transaccion_padre.categoria_id
        subcategoria_id = grupo.transaccion_padre.subcategoria_id

    tna = None
    tea = None
    cft_estimado = None
    if grupo.tiene_interes and grupo.tasa_interes is not None:
        tasas = calcular_tasas_cuotas(grupo.tasa_interes)
        if tasas:
            tna = tasas["tna"]
            tea = tasas["tea"]
            cft_estimado = tasas["cft_estimado"]

    suscripcion_id = None
    if grupo.transaccion_padre and grupo.transaccion_padre.suscripcion_id:
        suscripcion_id = grupo.transaccion_padre.suscripcion_id
    elif pendientes:
        for c in pendientes:
            if c.transaccion and c.transaccion.suscripcion_id:
                suscripcion_id = c.transaccion.suscripcion_id
                break
    elif pagadas:
        for c in pagadas:
            if c.transaccion and c.transaccion.suscripcion_id:
                suscripcion_id = c.transaccion.suscripcion_id
                break

    vencimiento_adelanto = None
    es_activo = (grupo.estado == EstadoGrupoCuotas.ACTIVO or getattr(grupo.estado, "value", str(grupo.estado)) == "activo")
    if grupo.tarjeta_id and es_activo:
        tarjeta = grupo.tarjeta or (db.get(TarjetaCredito, grupo.tarjeta_id) if grupo.tarjeta_id else None)
        if tarjeta:
            from app.services import calendario_tarjeta_service
            from app.utils.fecha import hoy_argentina
            try:
                _, vencimiento_adelanto = calendario_tarjeta_service.resumen_de_compra(tarjeta, hoy_argentina())
            except Exception:
                vencimiento_adelanto = None

    return {
        "id": grupo.id,
        "descripcion": grupo.descripcion,
        "monto_total": grupo.monto_total,
        "total_financiado": grupo.total_financiado,
        "cantidad_cuotas": grupo.cantidad_cuotas,
        "cantidad_pagadas": cantidad_pagadas,
        "cantidad_pendientes": cantidad_pendientes,
        "monto_cuota": monto_cuota,
        "proximo_vencimiento": proximo_vencimiento,
        "vencimiento_adelanto": vencimiento_adelanto,
        "total_pagado": total_pagado,
        "total_pendiente": total_pendiente,
        "moneda": grupo.moneda.value,
        "tarjeta_nombre": tarjeta_nombre,
        "fecha_compra": grupo.transaccion_padre.fecha if grupo.transaccion_padre else _hoy_argentina(),
        "transaccion_padre_id": grupo.transaccion_padre_id,
        "tiene_interes": grupo.tiene_interes,
        "tasa_interes": grupo.tasa_interes,
        "tna": tna,
        "tea": tea,
        "cft_estimado": cft_estimado,
        "estado": grupo.estado.value if hasattr(grupo.estado, "value") else str(grupo.estado),
        "categoria_id": categoria_id,
        "subcategoria_id": subcategoria_id,
        "suscripcion_id": suscripcion_id
    }

@router.get("", response_model=list[GrupoCuotasResumen])
def get_grupos_cuotas(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    stmt = (
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas).joinedload(Cuota.transaccion),
            joinedload(GrupoCuotas.transaccion_padre),
            joinedload(GrupoCuotas.tarjeta)
        )
        .where(GrupoCuotas.usuario_id == current_user.id)
    )
    grupos = db.execute(stmt).scalars().all()
    
    resumenes = []
    for g in grupos:
        # D5: El grupo del espejo NO aparece en la pestaña Cuotas
        if g.transaccion_padre and g.transaccion_padre.reintegro_de_id is not None:
            continue
        resumenes.append(mapear_grupo_resumen(db, g))
        
    def get_sort_key(res):
        val = res["proximo_vencimiento"]
        if val is None:
            return (1, date.max)
        return (0, val)
        
    resumenes.sort(key=get_sort_key)
    return resumenes

@router.get("/simular", response_model=SimularCuotasResponse)
def simular_cuotas(
    monto_total: Decimal = Query(..., gt=0, le=MAX_MONTO_INTEGRIDAD),
    cantidades: list[int] = Query(..., min_length=1, max_length=6),
    tasa_tna: Decimal | None = Query(None, ge=0, le=3000),
    precio_contado: Decimal | None = Query(None, gt=0, le=MAX_MONTO_INTEGRIDAD),
    current_user: Usuario = Depends(get_current_user),
):
    for c in cantidades:
        if c < 1 or c > 120:
            raise HTTPException(
                status_code=422,
                detail="Cada cantidad de cuotas debe estar entre 1 y 120."
            )

    tna = None
    tea = None
    cft_estimado = None
    tasa_tna_resp = None
    tasa_mensual = None

    if tasa_tna is not None and tasa_tna > Decimal("0"):
        tasa_tna_resp = tasa_tna
        tasas = calcular_tasas_cuotas(tasa_tna)
        if tasas:
            tna = tasas["tna"]
            tea = tasas["tea"]
            cft_estimado = tasas["cft_estimado"]
        tasa_mensual = tna_a_mensual(tasa_tna)

    planes = []
    for cant in cantidades:
        plan = calcular_plan_cuotas(monto_total, cant, tasa_mensual)
        recargo = None
        recargo_pct = None
        if precio_contado is not None:
            recargo = plan["total_financiado"] - precio_contado
            recargo_pct = ((recargo / precio_contado) * Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )

        planes.append(
            PlanCuotas(
                cantidad_cuotas=plan["cantidad_cuotas"],
                monto_cuota=plan["monto_cuota"],
                total_financiado=plan["total_financiado"],
                ultima_cuota=plan["ultima_cuota"],
                recargo=recargo,
                recargo_porcentaje=recargo_pct
            )
        )

    return SimularCuotasResponse(
        tasa_tna=tasa_tna_resp,
        tna=tna,
        tea=tea,
        cft_estimado=cft_estimado,
        planes=planes
    )

@router.patch("/{grupo_id}", response_model=GrupoCuotasResumen)
def update_grupo_cuotas(
    grupo_id: UUID,
    data: GrupoCuotasUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    grupo = db.execute(
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas).joinedload(Cuota.transaccion),
            joinedload(GrupoCuotas.transaccion_padre),
            joinedload(GrupoCuotas.tarjeta)
        )
        .where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == current_user.id)
    ).scalar_one_or_none()
    
    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo de cuotas.")
        
    from app.services.cuotas_service import actualizar_grupo
    actualizar_grupo(db, grupo, data)

    grupo_actualizado = db.execute(
        select(GrupoCuotas)
        .options(
            selectinload(GrupoCuotas.cuotas).joinedload(Cuota.transaccion),
            joinedload(GrupoCuotas.transaccion_padre),
            joinedload(GrupoCuotas.tarjeta)
        )
        .where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == current_user.id)
    ).scalar_one()
    
    return mapear_grupo_resumen(db, grupo_actualizado)

@router.delete("/{grupo_id}")
def delete_grupo_cuotas(
    grupo_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    grupo = db.execute(
        select(GrupoCuotas)
        .where(GrupoCuotas.id == grupo_id, GrupoCuotas.usuario_id == current_user.id)
    ).scalar_one_or_none()
    
    if not grupo:
        raise HTTPException(status_code=404, detail="No encontramos ese grupo de cuotas.")
        
    padre_id = grupo.transaccion_padre_id
    
    from app.services.transaccion_service import eliminar_transaccion
    eliminar_transaccion(db, current_user.id, padre_id)
    
    db.commit()
    return {"detail": "Grupo eliminado"}


from pydantic import BaseModel
from typing import Optional
from app.services.cuotas_service import cancelar_grupo, prepagar_grupo, adelantar_grupo

class PrepagoDatos(BaseModel):
    billetera_id: UUID
    categoria_id: Optional[UUID] = None

@router.post("/{grupo_id}/cancelar", response_model=GrupoCuotasResumen)
def cancelar_grupo_cuotas(
    grupo_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    grupo = cancelar_grupo(db, grupo_id, current_user.id)
    return mapear_grupo_resumen(db, grupo)

@router.post("/{grupo_id}/prepagar", response_model=GrupoCuotasResumen)
def prepagar_grupo_cuotas(
    grupo_id: UUID,
    request: Request,
    datos: PrepagoDatos,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    cuerpo = datos.model_dump(mode="json")
    return idempotencia_service.ejecutar(
        db=db,
        request=request,
        usuario_id=current_user.id,
        cuerpo=cuerpo,
        operar=lambda: prepagar_grupo(
            db, grupo_id, current_user.id, datos.billetera_id, datos.categoria_id, commit=False
        ),
        serializar=lambda g: GrupoCuotasResumen.model_validate(mapear_grupo_resumen(db, g)).model_dump(mode="json"),
        codigo=status.HTTP_200_OK,
    )

@router.post("/{grupo_id}/adelantar", response_model=GrupoCuotasResumen)
def adelantar_grupo_cuotas(
    grupo_id: UUID,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    grupo = adelantar_grupo(db, grupo_id, current_user.id)
    return mapear_grupo_resumen(db, grupo)
