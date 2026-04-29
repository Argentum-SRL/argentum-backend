from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.schemas.onboarding import (
    EstadoOnboardingResponse,
    DatosPersonalesRequest,
    CicloFinancieroRequest,
    MonedaRequest,
    PrimeraBilleteraRequest,
    OnboardingStepResponse,
    FinalizarOnboardingResponse
)
from app.services.onboarding_service import (
    get_estado_onboarding,
    validar_ciclo,
    crear_billeteras_onboarding
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

@router.get("/estado", response_model=EstadoOnboardingResponse)
def estado_onboarding(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    return get_estado_onboarding(db, current_user)

@router.post("/datos-personales", response_model=OnboardingStepResponse)
def post_datos_personales(
    body: DatosPersonalesRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    if current_user.onboarding_completo:
        return OnboardingStepResponse(completado=True, siguiente_paso=None)
        
    if current_user.nombre and current_user.apellido:
        estado = get_estado_onboarding(db, current_user)
        siguiente = estado.pasos_pendientes[0] if estado.pasos_pendientes else None
        return OnboardingStepResponse(completado=True, siguiente_paso=siguiente)

    nombre = body.nombre.strip()
    apellido = body.apellido.strip()
    
    if not nombre or not apellido:
        raise HTTPException(status_code=400, detail="Nombre y apellido son obligatorios.")
        
    current_user.nombre = nombre
    current_user.apellido = apellido
    db.commit()
    
    estado = get_estado_onboarding(db, current_user)
    siguiente = estado.pasos_pendientes[0] if estado.pasos_pendientes else None
    
    return OnboardingStepResponse(completado=True, siguiente_paso=siguiente)

@router.post("/ciclo-financiero", response_model=OnboardingStepResponse)
def post_ciclo_financiero(
    body: CicloFinancieroRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    if current_user.onboarding_completo:
        return OnboardingStepResponse(completado=True, siguiente_paso=None)

    # Validar paso anterior
    if not current_user.nombre or not current_user.apellido:
        raise HTTPException(status_code=400, detail="Primero completá tus datos personales.")

    if current_user.ciclo_tipo and current_user.ciclo_valor:
        estado = get_estado_onboarding(db, current_user)
        siguiente = estado.pasos_pendientes[0] if estado.pasos_pendientes else None
        return OnboardingStepResponse(completado=True, siguiente_paso=siguiente)

    ok, error = validar_ciclo(body.ciclo_tipo, body.ciclo_valor)
    if not ok:
        raise HTTPException(status_code=400, detail=error)
        
    current_user.ciclo_tipo = body.ciclo_tipo
    current_user.ciclo_valor = body.ciclo_valor
    db.commit()
    
    estado = get_estado_onboarding(db, current_user)
    siguiente = estado.pasos_pendientes[0] if estado.pasos_pendientes else None
    
    return OnboardingStepResponse(completado=True, siguiente_paso=siguiente)

@router.post("/moneda", response_model=OnboardingStepResponse)
def post_moneda(
    body: MonedaRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    if current_user.onboarding_completo:
        return OnboardingStepResponse(completado=True, siguiente_paso=None)

    # Validar paso anterior
    if not current_user.ciclo_tipo or not current_user.ciclo_valor:
        raise HTTPException(status_code=400, detail="Primero configurá tu ciclo financiero.")

    if current_user.moneda_principal:
        estado = get_estado_onboarding(db, current_user)
        siguiente = estado.pasos_pendientes[0] if estado.pasos_pendientes else None
        return OnboardingStepResponse(completado=True, siguiente_paso=siguiente)

    if (body.moneda_principal == "USD" or body.moneda_secundaria_activa) and not body.tipo_dolar:
        raise HTTPException(status_code=400, detail="El tipo de dólar es obligatorio.")
        
    valid_dolares = ['oficial', 'blue', 'tarjeta', 'bolsa', 'cripto']
    if body.tipo_dolar and body.tipo_dolar not in valid_dolares:
        raise HTTPException(status_code=400, detail="Tipo de dólar no válido.")

    current_user.moneda_principal = body.moneda_principal
    current_user.moneda_secundaria_activa = body.moneda_secundaria_activa
    if body.tipo_dolar:
        current_user.tipo_dolar = body.tipo_dolar
        
    db.commit()
    
    estado = get_estado_onboarding(db, current_user)
    siguiente = estado.pasos_pendientes[0] if estado.pasos_pendientes else None
    
    return OnboardingStepResponse(completado=True, siguiente_paso=siguiente)

@router.post("/primera-billetera", response_model=FinalizarOnboardingResponse)
def post_primera_billetera(
    body: PrimeraBilleteraRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    if current_user.onboarding_completo:
        return FinalizarOnboardingResponse(completado=True, usuario=current_user)

    # Validar paso anterior
    if not current_user.moneda_principal:
        raise HTTPException(status_code=400, detail="Primero seleccioná tu moneda principal.")

    crear_billeteras_onboarding(db, current_user, body)
    
    return FinalizarOnboardingResponse(completado=True, usuario=current_user)
