from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from app.models.usuario import Usuario, CicloTipo, Moneda
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
    if not user.nombre or not user.apellido:
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
    
    # 4. Primera billetera
    billetera_count = db.execute(
        select(func.count()).select_from(Billetera).where(Billetera.usuario_id == user.id)
    ).scalar()
    
    # Ajuste de logica para Moneda:
    # Si moneda_principal es ARS pero no ha pasado por el paso 3, lo pedimos.
    # Como saberlo? Si onboarding_completo es False y aun no llegamos a la billetera.
    # El orden es: datos -> ciclo -> moneda -> billetera.
    
    if user.moneda_principal == Moneda.ARS and not user.ciclo_tipo: # Simplificacion
         # Si no tiene ciclo, todavia no llego a moneda
         pass
    
    # Volvamos a los pasos basicos:
    if not user.moneda_principal: # Si fuera nullable
        pasos_pendientes.append("moneda")
    elif user.onboarding_completo == False and "ciclo_financiero" not in pasos_pendientes:
        # Si ya tiene ciclo pero no ha terminado onboarding, tal vez le falta confirmar moneda
        # El usuario dice "falta si moneda_principal es null".
        pass

    # Re-evaluando: El usuario pide exactamente estos criterios:
    # 1. "datos_personales": falta si nombre o apellido son null
    # 2. "ciclo_financiero": falta si ciclo_tipo o ciclo_valor son null
    # 3. "moneda": falta si moneda_principal es null
    # 4. "primera_billetera": falta si el usuario no tiene ninguna billetera
    
    # Dado que el modelo tiene default para moneda_principal, nunca sera null 
    # a menos que cambiemos el modelo. 
    # Vamos a cambiar el modelo Usuario para que moneda_principal sea Mapped[Moneda | None] 
    # y nullable=True.
    
    if not user.nombre or not user.apellido:
        if "datos_personales" not in pasos_pendientes: pasos_pendientes.append("datos_personales")
    if not user.ciclo_tipo or not user.ciclo_valor:
        if "ciclo_financiero" not in pasos_pendientes: pasos_pendientes.append("ciclo_financiero")
    if not user.moneda_principal:
        if "moneda" not in pasos_pendientes: pasos_pendientes.append("moneda")
    if billetera_count == 0:
        if "primera_billetera" not in pasos_pendientes: pasos_pendientes.append("primera_billetera")

    return EstadoOnboardingResponse(
        onboarding_completo=user.onboarding_completo,
        pasos_pendientes=pasos_pendientes,
        datos_actuales=DatosActuales(
            nombre=user.nombre,
            apellido=user.apellido,
            moneda_principal=user.moneda_principal.value if user.moneda_principal else None,
            ciclo_tipo=user.ciclo_tipo.value if user.ciclo_tipo else None,
            ciclo_valor=user.ciclo_valor
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
    
    # 2. Efectivo ARS
    b_efectivo_ars = Billetera(
        usuario_id=user.id,
        nombre="Efectivo ARS",
        moneda=Moneda.ARS,
        saldo_inicial=0,
        saldo_actual=0,
        es_principal=False,
        es_efectivo=True
    )
    db.add(b_efectivo_ars)
    
    # 3. Efectivo USD
    b_efectivo_usd = Billetera(
        usuario_id=user.id,
        nombre="Efectivo USD",
        moneda=Moneda.USD,
        saldo_inicial=0,
        saldo_actual=0,
        es_principal=False,
        es_efectivo=True
    )
    db.add(b_efectivo_usd)
    
    user.onboarding_completo = True
    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()
