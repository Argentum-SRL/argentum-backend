from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario, CicloTipo, CicloAjusteDireccion
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
from app.services.email_service import enviar_email_bienvenida

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
    from app.utils.fecha import hoy_argentina
    hoy = hoy_argentina()
    if body.fecha_nacimiento > hoy:
        raise HTTPException(status_code=400, detail="La fecha de nacimiento no puede ser futura.")
    
    edad = hoy.year - body.fecha_nacimiento.year - ((hoy.month, hoy.day) < (body.fecha_nacimiento.month, body.fecha_nacimiento.day))
    if edad < 18:
        raise HTTPException(status_code=400, detail="Tenés que ser mayor de 18 años para crear una cuenta en Argentum")
    
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
    current_user.ciclo_ajuste_direccion = body.ciclo_ajuste_direccion or CicloAjusteDireccion.ANTERIOR
    db.commit()
    db.refresh(current_user)
    
    estado = get_estado_onboarding(db, current_user)
    siguiente = estado.pasos_pendientes[0] if estado.pasos_pendientes else None
    
    return OnboardingStepResponse(completado=True, siguiente_paso=siguiente)

@router.post("/moneda", response_model=OnboardingStepResponse)
def post_moneda(
    body: MonedaRequest,
    background_tasks: BackgroundTasks,
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

    # Enviar email de bienvenida la primera vez que completa el onboarding
    if not current_user.onboarding_completo and current_user.email:
        background_tasks.add_task(
            enviar_email_bienvenida,
            current_user.email,
            current_user.nombre or "Usuario",
            current_user.sexo
        )
    
    # Marcar onboarding como completo al terminar el paso de moneda
    current_user.onboarding_completo = True
    from datetime import datetime, timezone
    current_user.ultimo_acceso = datetime.now(timezone.utc)
    
    from app.services import usuario_service
    usuario_service.crear_billeteras_efectivo_default(db, current_user.id)
    
    db.commit()
    
    return OnboardingStepResponse(completado=True, siguiente_paso=None)


@router.get("/preview-fecha-cobro")
async def preview_fecha_cobro(
    tipo: CicloTipo,
    valor: str,
    direccion: CicloAjusteDireccion = CicloAjusteDireccion.ANTERIOR,
    current_user: Usuario = Depends(get_current_user)
):
    _ = current_user
    from app.services import dias_habiles_service
    from datetime import date
    import calendar

    dir_val = direccion.value if isinstance(direccion, CicloAjusteDireccion) else str(direccion)

    if tipo == CicloTipo.DIA_FIJO:
        try:
            dia = int(valor)
            if not (1 <= dia <= 31):
                raise ValueError()
        except ValueError:
            raise HTTPException(status_code=400, detail="El día debe ser un número entre 1 y 31.")

        try:
            proxima_fecha = await dias_habiles_service.calcular_proxima_fecha_cobro(dia, direccion=dir_val)
            ultimo_dia_mes = calendar.monthrange(proxima_fecha.year, proxima_fecha.month)[1]
            dia_real_nominal = min(dia, ultimo_dia_mes)
            fecha_nominal = date(proxima_fecha.year, proxima_fecha.month, dia_real_nominal)
            fue_ajustada = (proxima_fecha != fecha_nominal)

            return {
                "tipo": tipo.value,
                "valor": valor,
                "direccion": dir_val,
                "proxima_fecha_cobro": proxima_fecha.isoformat(),
                "fecha_nominal": fecha_nominal.isoformat(),
                "fue_ajustada": fue_ajustada,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error al calcular la fecha de cobro: {str(e)}")

    elif tipo == CicloTipo.REGLA:
        from app.models.usuario import CicloRegla
        reglas_validas = {e.value for e in CicloRegla}
        if valor not in reglas_validas:
            raise HTTPException(status_code=400, detail="Regla de ciclo no válida.")

        try:
            from app.services.dashboard_service import get_date_by_rule
            from app.utils.fecha import hoy_argentina
            hoy = hoy_argentina()

            await dias_habiles_service.obtener_feriados_argentina(hoy.year)
            fecha_nominal_este_mes = get_date_by_rule(valor, hoy.month, hoy.year)
            fecha_ajustada_este_mes = dias_habiles_service.ajustar_fecha_habil_sync(
                fecha_nominal_este_mes, direccion=dir_val
            )

            if fecha_ajustada_este_mes >= hoy:
                proxima_fecha = fecha_ajustada_este_mes
                fecha_nominal = fecha_nominal_este_mes
            else:
                if hoy.month == 12:
                    prox_month, prox_year = 1, hoy.year + 1
                else:
                    prox_month, prox_year = hoy.month + 1, hoy.year

                await dias_habiles_service.obtener_feriados_argentina(prox_year)
                fecha_nominal_prox = get_date_by_rule(valor, prox_month, prox_year)
                proxima_fecha = dias_habiles_service.ajustar_fecha_habil_sync(
                    fecha_nominal_prox, direccion=dir_val
                )
                fecha_nominal = fecha_nominal_prox

            fue_ajustada = (proxima_fecha != fecha_nominal)
            return {
                "tipo": tipo.value,
                "valor": valor,
                "direccion": dir_val,
                "proxima_fecha_cobro": proxima_fecha.isoformat(),
                "fecha_nominal": fecha_nominal.isoformat(),
                "fue_ajustada": fue_ajustada,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error al calcular la fecha de cobro: {str(e)}")

    raise HTTPException(status_code=400, detail="Tipo de ciclo no válido.")




