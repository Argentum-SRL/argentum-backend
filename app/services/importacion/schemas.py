from datetime import date
from decimal import Decimal
from pydantic import BaseModel, Field


class TransaccionCruda(BaseModel):
    """
    Representa una transacción cruda extraída del resumen de tarjeta.
    
    Campos:
        fecha (date): Fecha de realización de la transacción.
        descripcion (str): Nombre del comercio o descripción de la transacción limpia de caracteres prefijos.
        monto (Decimal): Monto de la transacción (positivo para consumos/cargos, negativo para créditos/devoluciones).
        moneda (str): Moneda de la transacción ('ARS' o 'USD').
        cuota_actual (int | None): Número de la cuota actual, en caso de consumos cuotificados.
        cuota_total (int | None): Cantidad total de cuotas, en caso de consumos cuotificados.
        es_cargo_bancario (bool): Indica si la transacción es un cargo directo del banco (por ejemplo, impuesto de sellos).
        titular_seccion (str | None): Nombre del titular de la tarjeta que realizó la compra (usado por ciertos bancos).
    """
    fecha: date
    descripcion: str
    monto: Decimal
    moneda: str
    cuota_actual: int | None = None
    cuota_total: int | None = None
    es_cargo_bancario: bool = False
    titular_seccion: str | None = None


class ResultadoParseo(BaseModel):
    """
    Representa el resultado consolidado del parseo de un resumen de tarjeta.
    
    Campos:
        banco (str): Identificador del banco emisor detectado ('galicia', 'bna_visa', etc.).
        titular_detectado (str | None): Nombre del titular del resumen de la tarjeta.
        ultimos_4_digitos (str | None): Últimos 4 dígitos de la tarjeta de crédito principal.
        periodo_desde (date | None): Fecha de inicio del período de facturación del resumen.
        periodo_hasta (date | None): Fecha de fin del período de facturación del resumen.
        transacciones (list[TransaccionCruda]): Lista de transacciones individuales parseadas con éxito.
        confianza (float): Nivel de confianza del resultado (0.0 significa error/ilegible, 1.0 significa máxima certeza).
        capa_usada (str): Identificador de la capa de procesamiento utilizada ('deterministic', etc.).
        escalado (bool): Indica si se aplicó alguna lógica de fallback/escalado durante el procesamiento.
    """
    banco: str
    titular_detectado: str | None = None
    ultimos_4_digitos: str | None = None
    periodo_desde: date | None = None
    periodo_hasta: date | None = None
    transacciones: list[TransaccionCruda] = Field(default_factory=list)
    confianza: float
    capa_usada: str
    escalado: bool = False
