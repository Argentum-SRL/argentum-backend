from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from app.models.usuario import Usuario, CicloTipo, Moneda, Sexo
from app.models.billetera import Billetera
from app.schemas.onboarding import (
    EstadoOnboardingResponse, 
    DatosActuales,
    CicloFinancieroRequest,
    MonedaRequest,
    PrimeraBilleteraRequest
)

def get_estado_onboarding(db: Session, user: Usuario) -> EstadoOnboardingResponse:
    pasos_pendientes = []
    # 1. Datos personales: nombre, apellido, fecha_nacimiento, sexo
    if not user.nombre or not user.apellido or not user.fecha_nacimiento or not user.sexo:
        pasos_pendientes.append("datos_personales")
    
    # 2. Ciclo financiero: tipo y valor
    if not user.ciclo_tipo or not user.ciclo_valor:
        pasos_pendientes.append("ciclo_financiero")
        
    # 3. Moneda: obligatoria si el onboarding no está completo
    # Esto asegura que el usuario siempre pase por el paso de moneda para finalizar
    if not user.onboarding_completo:
        # Solo lo agregamos si no hay pasos anteriores críticos pendientes 
        # (para mantener el orden 1 -> 2 -> 3)
        if not pasos_pendientes:
            pasos_pendientes.append("moneda")
        else:
            # Si hay pasos anteriores, moneda vendrá después
            if "moneda" not in pasos_pendientes:
                pasos_pendientes.append("moneda")

    return EstadoOnboardingResponse(
        onboarding_completo=user.onboarding_completo,
        pasos_pendientes=pasos_pendientes,
        datos_actuales=DatosActuales(
            nombre=user.nombre,
            apellido=user.apellido,
            moneda_principal=user.moneda_principal.value if user.moneda_principal else None,
            ciclo_tipo=user.ciclo_tipo.value if user.ciclo_tipo else None,
            ciclo_valor=user.ciclo_valor,
            fecha_nacimiento=user.fecha_nacimiento,
            sexo=user.sexo.value if user.sexo else None
        )
    )

def validar_ciclo(ciclo_tipo: CicloTipo, ciclo_valor: str) -> tuple[bool, str | None]:
    if ciclo_tipo == CicloTipo.DIA_FIJO:
        try:
            dia = int(ciclo_valor)
            if not (1 <= dia <= 31):
                return False, "El día debe estar entre 1 y 31."
        except ValueError:
            return False, "El valor debe ser un número para el tipo día fijo."
    elif ciclo_tipo == CicloTipo.REGLA:
        valid_reglas = [
            'primer_lunes', 'primer_martes', 'primer_miercoles', 'primer_jueves', 'primer_viernes',
            'ultimo_lunes', 'ultimo_martes', 'ultimo_miercoles', 'ultimo_jueves', 'ultimo_viernes'
        ]
        if ciclo_valor not in valid_reglas:
            return False, "La regla seleccionada no es válida."
    return True, None

def crear_billeteras_onboarding(db: Session, user: Usuario, request: PrimeraBilleteraRequest) -> None:
    # 1. Billetera principal
    b_principal = Billetera(
        usuario_id=user.id,
        nombre=request.nombre,
        moneda=request.moneda,
        saldo_inicial=request.saldo_inicial,
        saldo_actual=request.saldo_inicial,
        es_principal=True,
        es_efectivo=False
    )
    db.add(b_principal)
    
    # 2 y 3. Billeteras de efectivo default
    from app.services import usuario_service
    usuario_service.crear_billeteras_efectivo_default(db, user.id)
    
    user.onboarding_completo = True
    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()
