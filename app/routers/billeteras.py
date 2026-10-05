from __future__ import annotations

from typing import List
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update, delete, exists, or_
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.ajuste_saldo import AjusteSaldo
from app.models.transaccion import Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.tarjeta_credito import TarjetaCredito
from app.core.entidades import ENTIDADES, entidad_de_billetera, opciones_de_entidad
from app.schemas.billetera import (
    BilleteraRead,
    BilleteraUpdate,
    RendimientoEstimadoResponse,
    ConfirmarRendimientoRequest,
)
from app.schemas.entidades import EntidadResponse, OpcionTasaEntidad, EstimacionRendimientoResponse
from app.schemas.ajuste_saldo import (
    ActualizarSaldoRequest,
    AjusteSaldoRead,
    AjustesBilleteraResponse,
    CoberturaRead,
    ControlSaldoPreview,
)
from app.services import usuario_service, rendimiento_billetera_service, ajuste_saldo_service
from app.services.tasas_service import ultimas_tasas, tasa_efectiva, rendimiento_por_saldos
from app.utils.fecha import hoy_argentina


router = APIRouter(prefix="/billeteras", tags=["billeteras"])


class CrearBilleteraRequest(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    moneda: Moneda
    saldo_inicial: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2, max_digits=15)
    es_principal: bool = False
    es_efectivo: bool = False
    es_inversion: bool = False
    tna: Decimal | None = Field(default=None, ge=Decimal("0"), decimal_places=2, max_digits=6)
    bank_id: str | None = Field(default=None, max_length=50)

    @field_validator("nombre")
    @classmethod
    def validate_nombre(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("El nombre de la billetera no puede estar vacío.")
        return v_clean


@router.get("", response_model=List[BilleteraRead])
def list_billeteras(
    db: Session = Depends(get_db), current_user: Usuario = Depends(get_current_user)
):
    """Devuelve las billeteras del usuario autenticado."""
    # Failsafe: asegurar que tenga las billeteras de efectivo default
    usuario_service.crear_billeteras_efectivo_default(db, current_user.id)
    
    # Optimización N+1: Usar subqueries correlacionadas para verificar transacciones, transferencias, rendimientos y ajustes
    exists_tx = exists().where(Transaccion.billetera_id == Billetera.id)
    exists_tr = exists().where(
        (TransferenciaInterna.billetera_origen_id == Billetera.id) | 
        (TransferenciaInterna.billetera_destino_id == Billetera.id)
    )
    exists_rend = exists().where(RendimientoBilletera.billetera_id == Billetera.id)
    exists_ajuste = exists().where(AjusteSaldo.billetera_id == Billetera.id)
    
    stmt = select(Billetera, (exists_tx | exists_tr | exists_rend | exists_ajuste).label("has_tx")).where(Billetera.usuario_id == current_user.id)
    rows = db.execute(stmt).all()
    
    results = []
    for b, has_tx in rows:
        b_read = BilleteraRead.model_validate(b)
        b_read.tiene_transacciones = has_tx
        results.append(b_read)

    return results


@router.get("/entidades", response_model=List[EntidadResponse])
def listar_entidades(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Devuelve el catálogo de entidades financieras soportadas,
    con sus opciones de tasas, topes y condiciones actualizadas.
    """
    hoy = hoy_argentina()

    # Recolectar todas las claves de tasas requeridas
    todas_claves = set()
    for ent_id in ENTIDADES:
        _, _, claves = opciones_de_entidad(ent_id)
        todas_claves.update(claves)

    tasas_map = ultimas_tasas(db, list(todas_claves)) if todas_claves else {}

    resultado = []
    for ent_id, info in ENTIDADES.items():
        nombre = info["nombre"]
        tipo_fuente, clave_base, claves_opciones = opciones_de_entidad(ent_id)

        if not tipo_fuente:
            resultado.append(
                EntidadResponse(
                    id=ent_id,
                    nombre=nombre,
                    tipo_fuente=None,
                    clave_base=None,
                    opciones=[],
                )
            )
            continue

        opciones = []
        for c in claves_opciones:
            fila = tasas_map.get(c)
            if fila:
                es_vieja = (hoy - fila.fecha_dato).days > 7
                opciones.append(
                    OpcionTasaEntidad(
                        clave=fila.clave,
                        tna=fila.tna,
                        tope=fila.tope,
                        condiciones=fila.condiciones,
                        fecha_dato=fila.fecha_dato,
                        vieja=es_vieja,
                    )
                )
            else:
                opciones.append(
                    OpcionTasaEntidad(
                        clave=c,
                        tna=None,
                        tope=None,
                        condiciones=None,
                        fecha_dato=None,
                        vieja=False,
                    )
                )

        resultado.append(
            EntidadResponse(
                id=ent_id,
                nombre=nombre,
                tipo_fuente=tipo_fuente,
                clave_base=clave_base,
                opciones=opciones,
            )
        )

    return resultado


@router.get("/estimar-rendimiento", response_model=EstimacionRendimientoResponse)
def estimar_rendimiento(
    saldo: Decimal = Query(..., ge=0, description="Saldo para la estimación"),
    entidad_id: str | None = Query(default=None),
    nivel: str | None = Query(default=None),
    tna: Decimal | None = Query(default=None, gt=0),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Estima el rendimiento diario y mensual para un saldo dado con tasa de catálogo o manual."""
    hoy = hoy_argentina()

    claves_buscar = []
    if entidad_id:
        _, _, claves_buscar = opciones_de_entidad(entidad_id)

    tasas_por_clave = ultimas_tasas(db, claves_buscar) if claves_buscar else {}

    billetera_mem = Billetera(
        es_efectivo=False,
        entidad_id=entidad_id,
        nivel_tasa=nivel,
        tna=tna,
        saldo_actual=saldo,
    )
    tasa_ef = tasa_efectiva(billetera_mem, tasas_por_clave, saldo, hoy)

    if tasa_ef.tna is not None and not tasa_ef.vieja and tasa_ef.tna > Decimal("0"):
        por_dia = rendimiento_por_saldos({hoy: saldo}, tasa_ef.tna, tasa_ef.tope)
        por_mes = rendimiento_por_saldos(
            {hoy - timedelta(days=i): saldo for i in range(30)}, tasa_ef.tna, tasa_ef.tope
        )
    else:
        por_dia = None
        por_mes = None

    return EstimacionRendimientoResponse(
        entidad_id=tasa_ef.entidad_id,
        tna=tasa_ef.tna,
        origen=tasa_ef.origen,
        clave=tasa_ef.clave,
        fecha_dato=tasa_ef.fecha_dato,
        vieja=tasa_ef.vieja,
        tope=tasa_ef.tope,
        por_dia=por_dia,
        por_mes=por_mes,
    )



@router.get("/{billetera_id}", response_model=BilleteraRead)
def get_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Obtiene una billetera específica por ID."""
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    
    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")
    
    # Verificamos transacciones, transferencias, rendimientos y ajustes por separado para mayor seguridad
    has_tx = db.query(exists().where(Transaccion.billetera_id == billetera_id)).scalar()
    has_tr = db.query(exists().where(or_(
        TransferenciaInterna.billetera_origen_id == billetera_id,
        TransferenciaInterna.billetera_destino_id == billetera_id
    ))).scalar()
    has_rend = db.query(exists().where(RendimientoBilletera.billetera_id == billetera_id)).scalar()
    has_ajuste = db.query(exists().where(AjusteSaldo.billetera_id == billetera_id)).scalar()
    
    b_read = BilleteraRead.model_validate(billetera)
    b_read.tiene_transacciones = bool(has_tx or has_tr or has_rend or has_ajuste)
    return b_read


@router.get("/{billetera_id}/rendimiento-estimado", response_model=RendimientoEstimadoResponse)
def get_rendimiento_estimado(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Calcula y devuelve el rendimiento estimado de una billetera de inversión."""
    return rendimiento_billetera_service.calcular_rendimiento_estimado(db, current_user.id, billetera_id)


@router.post("/{billetera_id}/rendimiento", response_model=BilleteraRead)
def registrar_rendimiento(
    billetera_id: str,
    body: ConfirmarRendimientoRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Registra y acredita un rendimiento manual en una billetera de inversión."""
    billetera = rendimiento_billetera_service.confirmar_rendimiento(
        db, current_user.id, billetera_id, monto=body.monto, fecha=body.fecha
    )
    has_tx = db.query(exists().where(Transaccion.billetera_id == billetera.id)).scalar()
    has_tr = db.query(exists().where(or_(
        TransferenciaInterna.billetera_origen_id == billetera.id,
        TransferenciaInterna.billetera_destino_id == billetera.id
    ))).scalar()
    has_rend = db.query(exists().where(RendimientoBilletera.billetera_id == billetera.id)).scalar()
    has_ajuste = db.query(exists().where(AjusteSaldo.billetera_id == billetera.id)).scalar()

    b_read = BilleteraRead.model_validate(billetera)
    b_read.tiene_transacciones = bool(has_tx or has_tr or has_rend or has_ajuste)
    return b_read


@router.post("", response_model=BilleteraRead, status_code=status.HTTP_201_CREATED)
def create_billetera(
    body: CrearBilleteraRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    if body.es_efectivo and body.tna is not None:
        raise HTTPException(
            status_code=400,
            detail="Una billetera de efectivo no puede tener tasa",
        )

    if body.tna is not None:
        fecha_ultimo_rendimiento = datetime.now(timezone.utc)
    else:
        fecha_ultimo_rendimiento = None

    if body.es_principal:
        db.execute(
            update(Billetera).where(Billetera.usuario_id == current_user.id).values(es_principal=False)
        )

    entidad_id = body.bank_id if (body.bank_id and body.bank_id in ENTIDADES) else None

    b = Billetera(
        usuario_id=current_user.id,
        nombre=body.nombre,
        moneda=body.moneda,
        saldo_inicial=body.saldo_inicial,
        saldo_actual=body.saldo_inicial,
        es_principal=body.es_principal,
        es_efectivo=body.es_efectivo,
        es_inversion=body.es_inversion,
        tna=body.tna,
        entidad_id=entidad_id,
        fecha_ultimo_rendimiento=fecha_ultimo_rendimiento,
    )
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@router.put("/{billetera_id}", response_model=BilleteraRead)
@router.patch("/{billetera_id}", response_model=BilleteraRead)
def update_billetera(
    billetera_id: str,
    body: BilleteraUpdate,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):

    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    es_efectivo_target = body.es_efectivo if body.es_efectivo is not None else billetera.es_efectivo
    if es_efectivo_target and body.tna is not None:
        raise HTTPException(
            status_code=400,
            detail="Una billetera de efectivo no puede tener tasa",
        )
    if body.es_efectivo is True and billetera.tna is not None and body.tna is None and "tna" not in body.model_fields_set:
        raise HTTPException(
            status_code=400,
            detail="Una billetera de efectivo no puede tener tasa",
        )

    # Validación de bank_id
    if "bank_id" in body.model_fields_set and body.bank_id is not None:
        if body.bank_id not in ENTIDADES:
            raise HTTPException(status_code=400, detail="Esa entidad no existe.")

    entidad_target = body.bank_id if "bank_id" in body.model_fields_set else entidad_de_billetera(billetera)

    # Validación de nivel_tasa
    if "nivel_tasa" in body.model_fields_set and body.nivel_tasa is not None:
        tipo, clave_base, valid_opts = opciones_de_entidad(entidad_target)
        if not valid_opts or body.nivel_tasa not in valid_opts:
            raise HTTPException(status_code=400, detail="Ese nivel no corresponde a esta billetera.")
        billetera.nivel_tasa = body.nivel_tasa
        if billetera.entidad_id is None and entidad_target is not None:
            billetera.entidad_id = entidad_target
    elif "nivel_tasa" in body.model_fields_set and body.nivel_tasa is None:
        billetera.nivel_tasa = None
    elif "bank_id" in body.model_fields_set and billetera.nivel_tasa is not None:
        # Si cambia la entidad y el nivel guardado deja de corresponder, el nivel pasa a None
        _, _, valid_opts = opciones_de_entidad(entidad_target)
        if billetera.nivel_tasa not in valid_opts:
            billetera.nivel_tasa = None

    if "bank_id" in body.model_fields_set:
        billetera.entidad_id = body.bank_id


    if "tna" in body.model_fields_set:
        if body.tna is not None:
            billetera.tna = body.tna
            if billetera.fecha_ultimo_rendimiento is None:
                billetera.fecha_ultimo_rendimiento = datetime.now(timezone.utc)
        else:
            billetera.tna = None
            # No borrar fecha_ultimo_rendimiento si tna pasa a None

    if body.es_principal:
        db.execute(
            update(Billetera).where(Billetera.usuario_id == current_user.id).values(es_principal=False)
        )

    if body.moneda is not None and body.moneda != billetera.moneda:
        has_tx = db.query(exists().where(Transaccion.billetera_id == billetera_id)).scalar()
        has_tr = db.query(exists().where(
            (TransferenciaInterna.billetera_origen_id == billetera_id) |
            (TransferenciaInterna.billetera_destino_id == billetera_id)
        )).scalar()
        has_rend = db.query(exists().where(RendimientoBilletera.billetera_id == billetera_id)).scalar()
        has_ajuste = db.query(exists().where(AjusteSaldo.billetera_id == billetera_id)).scalar()
        if has_tx or has_tr or has_rend or has_ajuste:
            raise HTTPException(
                status_code=400,
                detail="No podés cambiar la moneda de una billetera que ya tiene transacciones o transferencias asociadas."
            )

    if billetera.es_efectivo:
        # Solo se permite cambiar el nombre y el estado
        if body.nombre is not None:
            billetera.nombre = body.nombre
        if body.estado is not None:
            billetera.estado = body.estado
    else:
        for attr in ('nombre', 'moneda', 'es_principal', 'es_efectivo', 'es_inversion', 'estado'):
            val = getattr(body, attr, None)
            if val is not None:
                setattr(billetera, attr, val)

    db.commit()
    db.refresh(billetera)
    return billetera


@router.delete("/{billetera_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")

    if billetera.es_efectivo:
        raise HTTPException(status_code=400, detail="Las billeteras de efectivo (ARS/USD) no pueden eliminarse")

    # VERIFICACIÓN DE INTEGRIDAD
    from app.models.suscripcion import Suscripcion
    from app.models.grupo_cuotas import GrupoCuotas

    exists_tx = exists().where(Transaccion.billetera_id == billetera_id)
    exists_tr = exists().where(
        (TransferenciaInterna.billetera_origen_id == billetera_id) | 
        (TransferenciaInterna.billetera_destino_id == billetera_id)
    )
    exists_sub = exists().where(Suscripcion.billetera_id == billetera_id)
    exists_rend = exists().where(RendimientoBilletera.billetera_id == billetera_id)
    exists_ajuste = exists().where(AjusteSaldo.billetera_id == billetera_id)
    
    check_stmt = select(
        exists_tx.label("has_tx"),
        exists_tr.label("has_tr"),
        exists_sub.label("has_sub"),
        exists_rend.label("has_rend"),
        exists_ajuste.label("has_ajuste"),
    )
    check_res = db.execute(check_stmt).one()
    
    if check_res.has_tx:
        raise HTTPException(
            status_code=400, 
            detail="No se puede eliminar la billetera porque tiene transacciones asociadas. Por favor, archivala para mantener el historial."
        )

    if check_res.has_tr:
        raise HTTPException(
            status_code=400, 
            detail="No se puede eliminar la billetera porque tiene transferencias internas asociadas. Por favor, archivala."
        )

    if check_res.has_sub:
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar la billetera porque tiene suscripciones activas asociadas. Por favor, archivala o cancelá las suscripciones."
        )

    if check_res.has_rend:
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar la billetera porque tiene rendimientos asociados. Por favor, archivala."
        )

    if check_res.has_ajuste:
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar la billetera porque tiene ajustes de saldo. Por favor, archivala."
        )

    # Chequear las tarjetas de crédito asociadas
    cards = db.execute(select(TarjetaCredito).where(TarjetaCredito.billetera_id == billetera_id)).scalars().all()
    if cards:
        tarjetas_bloqueantes = []
        for c in cards:
            has_tx = db.execute(select(exists().where(Transaccion.tarjeta_id == c.id))).scalar()
            has_cuotas = db.execute(select(exists().where(GrupoCuotas.tarjeta_id == c.id))).scalar()
            has_sub = db.execute(select(exists().where(Suscripcion.tarjeta_id == c.id))).scalar()
            if has_tx or has_cuotas or has_sub:
                tarjetas_bloqueantes.append(c)

        if tarjetas_bloqueantes:
            nombres = ", ".join(t.nombre for t in tarjetas_bloqueantes)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"No podés eliminar esta billetera porque "
                    f"{'la tarjeta' if len(tarjetas_bloqueantes) == 1 else 'las tarjetas'} "
                    f"{nombres} "
                    f"{'tiene' if len(tarjetas_bloqueantes) == 1 else 'tienen'} "
                    f"transacciones registradas. "
                    f"Archivá o eliminá {'esa tarjeta' if len(tarjetas_bloqueantes) == 1 else 'esas tarjetas'} primero."
                )
            )
        
        # Si las tarjetas no tienen consumos ni dependencias, las eliminamos automáticamente
        db.execute(delete(TarjetaCredito).where(TarjetaCredito.billetera_id == billetera_id))

    res = db.execute(delete(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id))
    db.commit()
    if res.rowcount == 0:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")
    return


@router.post("/{billetera_id}/archivar", response_model=BilleteraRead)
def archivar_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")
    billetera.estado = EstadoBilletera.ARCHIVADA
    db.commit()
    db.refresh(billetera)
    return billetera


@router.post("/{billetera_id}/desarchivar", response_model=BilleteraRead)
def desarchivar_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    stmt = select(Billetera).where(Billetera.id == billetera_id, Billetera.usuario_id == current_user.id)
    billetera = db.execute(stmt).scalars().one_or_none()
    if not billetera:
        raise HTTPException(status_code=404, detail="No encontramos esa billetera.")
    billetera.estado = EstadoBilletera.ACTIVA
    db.commit()
    db.refresh(billetera)
    return billetera


@router.get("/{billetera_id}/control-saldo", response_model=ControlSaldoPreview)
def previsualizar_control_saldo(
    billetera_id: str,
    saldo: Decimal = Query(..., ge=0, description="Saldo declarado actual en la billetera"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Genera la vista previa de un control de saldo antes de su confirmación."""
    return ajuste_saldo_service.previsualizar_control(
        db, current_user.id, billetera_id, saldo_declarado=saldo
    )


@router.post("/{billetera_id}/ajustes", response_model=BilleteraRead)
def actualizar_saldo(
    billetera_id: str,
    body: ActualizarSaldoRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Registra un control de saldo ("Actualizar saldo") en la billetera."""
    ajuste_saldo_service.registrar_control(
        db,
        current_user.id,
        billetera_id,
        saldo_declarado=body.saldo_declarado,
        rendimiento=body.rendimiento,
        commit=True,
    )
    billetera = db.get(Billetera, billetera_id)
    has_tx = db.query(exists().where(Transaccion.billetera_id == billetera.id)).scalar()
    has_tr = db.query(exists().where(or_(
        TransferenciaInterna.billetera_origen_id == billetera.id,
        TransferenciaInterna.billetera_destino_id == billetera.id
    ))).scalar()
    has_rend = db.query(exists().where(RendimientoBilletera.billetera_id == billetera.id)).scalar()
    has_ajuste = db.query(exists().where(AjusteSaldo.billetera_id == billetera.id)).scalar()

    b_read = BilleteraRead.model_validate(billetera)
    b_read.tiene_transacciones = bool(has_tx or has_tr or has_rend or has_ajuste)
    return b_read


@router.get("/{billetera_id}/ajustes", response_model=AjustesBilleteraResponse)
def listar_ajustes_billetera(
    billetera_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Lista el historial de ajustes de saldo y la cobertura para la billetera."""
    ajustes = ajuste_saldo_service.listar_ajustes(db, current_user.id, billetera_id)
    cobertura_dict = ajuste_saldo_service.calcular_cobertura(db, billetera_id)
    return AjustesBilleteraResponse(
        ajustes=[AjusteSaldoRead.model_validate(a) for a in ajustes],
        cobertura=CoberturaRead(**cobertura_dict),
    )


@router.delete("/{billetera_id}/ajustes/{ajuste_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_ajuste_billetera(
    billetera_id: str,
    ajuste_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Elimina y revierte un ajuste de saldo."""
    ajuste_saldo_service.eliminar_ajuste(
        db, current_user.id, ajuste_id, billetera_id=billetera_id, commit=True
    )
    return None
