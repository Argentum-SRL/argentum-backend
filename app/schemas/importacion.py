from datetime import date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator


class ProcesarResumenResponse(BaseModel):
    """Respuesta para el endpoint de procesamiento de resumen."""
    importacion_id: UUID
    banco_detectado: str
    estado: str
    titulares_detectados: list[str] | None = None
    total_detectadas: int
    confianza: float
    escalado: bool


class TransaccionPreview(BaseModel):
    """Estructura de una transacción parseada enriquecida con información de duplicados."""
    fecha: date
    descripcion: str
    monto: Decimal
    moneda: str
    cuota_actual: int | None = None
    cuota_total: int | None = None
    es_cargo_bancario: bool = False
    titular_seccion: str | None = None
    posible_duplicado: bool

    @field_validator('moneda')
    @classmethod
    def validar_moneda(cls, v: str) -> str:
        v_upper = v.upper().strip()
        if v_upper not in ('ARS', 'USD'):
            raise ValueError('La moneda debe ser ARS o USD')
        return v_upper


class PreviewImportacionResponse(BaseModel):
    """Respuesta para el endpoint de previsualización de importación."""
    id: UUID
    usuario_id: UUID
    banco_detectado: str
    estado: str
    total_detectadas: int
    periodo_desde: date | None = None
    periodo_hasta: date | None = None
    titulares_detectados: list[str] | None = None
    titulares_seleccionados: list[str] | None = None
    transacciones: list[TransaccionPreview]


class TransaccionConfirmarItem(BaseModel):
    """Estructura de un ítem de confirmación de transacción."""
    categoria_id: UUID | None = None
    incluir: bool = True


class ConfirmarImportacionRequest(BaseModel):
    """Cuerpo de la solicitud para confirmar la importación."""
    tarjeta_id: UUID
    billetera_id: UUID
    billetera_usd_id: UUID | None = None
    titulares_seleccionados: list[str] | None = None
    transacciones_finales: list[TransaccionConfirmarItem]

    @field_validator('transacciones_finales')
    @classmethod
    def validar_transacciones_finales(cls, v: list[TransaccionConfirmarItem]) -> list[TransaccionConfirmarItem]:
        if not v:
            raise ValueError('Debe enviar la lista de transacciones para confirmar la importación')
        return v


class ConfirmarImportacionResponse(BaseModel):
    """Respuesta para el endpoint de confirmación de importación."""
    importadas: int
    duplicadas: int
    sin_billetera_usd: int
    descartadas_manual: int = 0
    total_procesadas: int
