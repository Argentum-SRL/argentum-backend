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
    
    # 1. Datos personales
    if not user.nombre or not user.apellido or not user.fecha_nacimiento or not user.sexo:
        pasos_pendientes.append("datos_personales")
    
    # 2. Ciclo financiero
    if not user.ciclo_tipo or not user.ciclo_valor:
        pasos_pendientes.append("ciclo_financiero")
        
    # 3. Moneda
    # Nota: moneda_principal tiene default ARS en el modelo, 
    # pero el user dice que falta si es null. 
    # En el modelo pusimos nullable=False default=Moneda.ARS.
    # Segun el requerimiento: "falta si moneda_principal es null".
    # Vamos a asumir que si el usuario no paso por este paso, lo pedimos.
    # Podemos usar un flag o chequear si ciclo_tipo ya se seteo pero moneda sigue en default.
    # Pero el requerimiento es explicito: "falta si moneda_principal es null".
    # Ajustaremos el modelo o la logica. 
    # Si el modelo tiene default ARS, nunca sera null.
    # Re-leamos: "moneda: falta si moneda_principal es null".
    # Cambiaremos el modelo para que moneda_principal sea opcional en la BD 
    # o usaremos otra señal.
    # Por ahora, si es el default y no tiene billeteras, asumimos que le falta elegir.
    # Pero mejor ser estrictos con lo que pide el usuario.
    
    # El paso 'primera_billetera' ha sido removido por pedido del usuario.
    # El onboarding termina en el paso 'moneda'.
    
    # Re-evaluando: El usuario pide exactamente estos criterios:
    # 1. "datos_personales": falta si nombre o apellido son null
    # 2. "ciclo_financiero": falta si ciclo_tipo o ciclo_valor son null
    # 3. "moneda": falta si moneda_principal es null
    
    # Dado que el modelo tiene default para moneda_principal, nunca sera null 
    # a menos que cambiemos el modelo. 
    # Vamos a cambiar el modelo Usuario para que moneda_principal sea Mapped[Moneda | None] 
    # y nullable=True.
    
    if not user.nombre or not user.apellido or not user.fecha_nacimiento or not user.sexo:
        if "datos_personales" not in pasos_pendientes: pasos_pendientes.append("datos_personales")
    if not user.ciclo_tipo or not user.ciclo_valor:
        if "ciclo_financiero" not in pasos_pendientes: pasos_pendientes.append("ciclo_financiero")
    if not user.moneda_principal:
        if "moneda" not in pasos_pendientes: pasos_pendientes.append("moneda")

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
            if not (1 <= dia <= 28):
                return False, "El día debe estar entre 1 y 28."
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
