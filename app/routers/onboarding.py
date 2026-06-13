from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.schemas.onboarding import (
    EstadoOnboardingResponse,
    CotizacionesDolarResponse,
    DatosPersonalesRequest,
    CicloFinancieroRequest,
    MonedaRequest,
    OnboardingStepResponse
)
from app.services.onboarding_service import (
    get_estado_onboarding,
    validar_ciclo
)
from app.services.dolar_service import get_cotizaciones_dolar

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/cotizaciones-dolar", response_model=CotizacionesDolarResponse)
def get_cotizaciones_onboarding(
    current_user: Usuario = Depends(get_current_user),
):
    _ = current_user
    return get_cotizaciones_dolar()

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
    from datetime import date
    if current_user.onboarding_completo:
        return OnboardingStepResponse(completado=True, siguiente_paso=None)
        
    nombre = body.nombre.strip()
    apellido = body.apellido.strip()
    
    if not nombre or not apellido:
        raise HTTPException(status_code=400, detail="Nombre y apellido son obligatorios.")
        
    # Validaciones adicionales
    if body.fecha_nacimiento > date.today():
        raise HTTPException(status_code=400, detail="La fecha de nacimiento no puede ser futura.")
    
    # El sexo ya viene validado por el Enum en el schema (Pydantic devuelve 422)
    # Pero si queremos forzar el 400 como pide el usuario:
    from app.models.usuario import Sexo
    if body.sexo not in Sexo:
        raise HTTPException(status_code=400, detail="El valor de sexo no es valido.")

    current_user.nombre = nombre
    current_user.apellido = apellido
    current_user.fecha_nacimiento = body.fecha_nacimiento
    current_user.sexo = body.sexo
    db.commit()
    db.refresh(current_user)
    
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
    if not current_user.nombre or not current_user.apellido or not current_user.fecha_nacimiento or not current_user.sexo:
        raise HTTPException(status_code=400, detail="Primero completá tus datos personales.")

    ok, error = validar_ciclo(body.ciclo_tipo, body.ciclo_valor)
    if not ok:
        raise HTTPException(status_code=400, detail=error)
        
    current_user.ciclo_tipo = body.ciclo_tipo
    current_user.ciclo_valor = body.ciclo_valor
    db.commit()
    db.refresh(current_user)
    
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


    if (body.moneda_principal == "USD" or body.moneda_secundaria_activa) and not body.tipo_dolar:
        raise HTTPException(status_code=400, detail="El tipo de dólar es obligatorio.")
        
    # Compatibilidad: "bolsa" historico se guarda como "mep"
    if body.tipo_dolar == "bolsa":
        body.tipo_dolar = "mep"

    valid_dolares = ['oficial', 'blue', 'tarjeta', 'mep']
    if body.tipo_dolar and body.tipo_dolar not in valid_dolares:
        raise HTTPException(status_code=400, detail="Tipo de dólar no válido.")

    current_user.moneda_principal = body.moneda_principal
    current_user.moneda_secundaria_activa = body.moneda_secundaria_activa
    if body.tipo_dolar:
        current_user.tipo_dolar = body.tipo_dolar
    
    # Marcar onboarding como completo al terminar el paso de moneda
    current_user.onboarding_completo = True
    from datetime import datetime, timezone
    current_user.ultimo_acceso = datetime.now(timezone.utc)
    
    db.commit()
    
    return OnboardingStepResponse(completado=True, siguiente_paso=None)


@router.get("/preview-fecha-cobro")
async def preview_fecha_cobro(
    dia: int,
    current_user: Usuario = Depends(get_current_user)
):
    _ = current_user
    if not (1 <= dia <= 31):
        raise HTTPException(status_code=400, detail="El día debe estar entre 1 y 31.")
    try:
        from app.services import dias_habiles_service
        import calendar
        from datetime import date
        
        proxima_fecha = await dias_habiles_service.calcular_proxima_fecha_cobro(dia)
        
        ultimo_dia_mes = calendar.monthrange(proxima_fecha.year, proxima_fecha.month)[1]
        dia_real_nominal = min(dia, ultimo_dia_mes)
        fecha_nominal = date(proxima_fecha.year, proxima_fecha.month, dia_real_nominal)
        
        feriados = await dias_habiles_service.obtener_feriados_argentina(proxima_fecha.year)
        es_habil = dias_habiles_service.es_dia_habil(fecha_nominal, feriados)
        
        return {
            "dia_nominal": dia,
            "proxima_fecha_cobro": proxima_fecha.isoformat(),
            "es_dia_habil": es_habil
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al calcular la fecha de cobro: {str(e)}"
        )



