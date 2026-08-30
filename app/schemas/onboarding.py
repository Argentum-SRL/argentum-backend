from __future__ import annotations

from datetime import date
from pydantic import BaseModel, Field, model_validator
from typing_extensions import Self
from app.models.usuario import CicloTipo, CicloRegla, Moneda, Sexo, CicloAjusteDireccion
from app.schemas.usuario import UsuarioRead

class DatosActuales(BaseModel):
    nombre: str | None = None
    apellido: str | None = None
    moneda_principal: str | None = None
    moneda_secundaria_activa: bool | None = None
    tipo_dolar: str | None = None
    ciclo_tipo: str | None = None
    ciclo_valor: str | None = None
    ciclo_ajuste_direccion: str | None = None
    fecha_nacimiento: date | None = None
    sexo: str | None = None

class EstadoOnboardingResponse(BaseModel):
    onboarding_completo: bool
    pasos_pendientes: list[str]
    datos_actuales: DatosActuales


class CotizacionDolar(BaseModel):
    tipo: str
    nombre: str
    compra: float | None = None
    venta: float | None = None
    promedio: float | None = None
    moneda: str
    fecha_actualizacion: str | None = None


class CotizacionesDolarResponse(BaseModel):
    fuente: str
    actualizado_en: str
    cotizaciones: dict[str, CotizacionDolar]

class DatosPersonalesRequest(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    apellido: str = Field(..., min_length=1, max_length=100)
    fecha_nacimiento: date
    sexo: Sexo

    @model_validator(mode="after")
    def validar_datos_personales(self) -> Self:
        if not self.nombre.strip():
            raise ValueError("El nombre no puede estar vacío.")
        if not self.apellido.strip():
            raise ValueError("El apellido no puede estar vacío.")
        from app.utils.fecha import hoy_argentina
        hoy = hoy_argentina()
        if self.fecha_nacimiento > hoy:
            raise ValueError("La fecha de nacimiento no puede ser futura.")
        edad = hoy.year - self.fecha_nacimiento.year - ((hoy.month, hoy.day) < (self.fecha_nacimiento.month, self.fecha_nacimiento.day))
        if edad < 18:
            raise ValueError("Tenés que ser mayor de 18 años para crear una cuenta en Argentum.")
        if edad > 120:
            raise ValueError("La fecha de nacimiento no es válida.")
        return self

class CicloFinancieroRequest(BaseModel):
    ciclo_tipo: CicloTipo
    ciclo_valor: str
    ciclo_ajuste_direccion: CicloAjusteDireccion | None = None

    @model_validator(mode="after")
    def validar_ciclo_polimorfico(self) -> Self:
        if self.ciclo_tipo == CicloTipo.DIA_FIJO:
            try:
                dia = int(self.ciclo_valor)
                if not (1 <= dia <= 31):
                    raise ValueError("El día fijo debe ser un número entero entre 1 y 31.")
            except ValueError:
                raise ValueError("El día fijo debe ser un número entero entre 1 y 31.")
        elif self.ciclo_tipo == CicloTipo.REGLA:
            reglas_validas = {e.value for e in CicloRegla}
            if self.ciclo_valor not in reglas_validas:
                raise ValueError(f"Regla de ciclo no válida. Opciones válidas: {', '.join(sorted(reglas_validas))}")
        return self



class MonedaRequest(BaseModel):
    moneda_principal: Moneda
    moneda_secundaria_activa: bool
    tipo_dolar: str | None = None

    @model_validator(mode="after")
    def validar_moneda(self) -> Self:
        if self.moneda_principal == Moneda.USD or self.moneda_secundaria_activa:
            if not self.tipo_dolar:
                raise ValueError("El tipo de dólar de referencia es obligatorio.")
            tipo = self.tipo_dolar.lower().strip()
            if tipo == "bolsa":
                tipo = "mep"
            valid_dolares = {"oficial", "blue", "tarjeta", "mep"}
            if tipo not in valid_dolares:
                raise ValueError("El tipo de dólar seleccionado no es válido.")
            self.tipo_dolar = tipo
        else:
            if self.tipo_dolar:
                tipo = self.tipo_dolar.lower().strip()
                if tipo == "bolsa":
                    tipo = "mep"
                self.tipo_dolar = tipo
        return self



class OnboardingStepResponse(BaseModel):
    completated: bool = Field(..., alias="completado")
    siguiente_paso: str | None = None

    class Config:
        populate_by_name = True


