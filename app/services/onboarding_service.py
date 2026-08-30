from __future__ import annotations

from sqlalchemy.orm import Session
from app.models.usuario import Usuario, CicloTipo, CicloRegla, Moneda, Sexo
from app.schemas.onboarding import (
    EstadoOnboardingResponse, 
    DatosActuales
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
            moneda_secundaria_activa=user.moneda_secundaria_activa,
            tipo_dolar=user.tipo_dolar,
            ciclo_tipo=user.ciclo_tipo.value if user.ciclo_tipo else None,
            ciclo_valor=user.ciclo_valor,
            ciclo_ajuste_direccion=user.ciclo_ajuste_direccion.value if user.ciclo_ajuste_direccion else "anterior",
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
            return False, "El valor debe ser un número entero entre 1 y 31."
    elif ciclo_tipo == CicloTipo.REGLA:
        valid_reglas = {e.value for e in CicloRegla}
        if ciclo_valor not in valid_reglas:
            return False, "La regla seleccionada no es válida."
    return True, None



