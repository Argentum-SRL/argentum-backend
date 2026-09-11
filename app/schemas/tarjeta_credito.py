from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from app.models.tarjeta_credito import RedTarjeta, EstadoTarjeta
from app.models.usuario import Moneda
from app.schemas.tipos import DecimalJSON

class TarjetaCreditoBase(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=100)
    apodo: str | None = Field(default=None, max_length=50)
    red: RedTarjeta
    dia_cierre: int = Field(..., ge=1, le=28)
    dia_vencimiento: int = Field(..., ge=1, le=28)
    limite_credito: DecimalJSON | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    moneda: Moneda = Moneda.ARS
    percepcion_moneda_extranjera: DecimalJSON = Field(
        default=Decimal("30.00"), ge=0, le=100, max_digits=5, decimal_places=2,
        description="Porcentaje de percepción sobre consumos en moneda extranjera (ej. 30 para 30%)"
    )
    color: str | None = Field(None, max_length=7)

    @field_validator("nombre")
    @classmethod
    def validar_nombre(cls, v: str) -> str:
        nombre = v.strip()
        if not nombre:
            raise ValueError("El nombre de la tarjeta no puede estar vacío.")
        return nombre

class TarjetaCreditoCreate(TarjetaCreditoBase):
    billetera_id: UUID

class TarjetaCreditoUpdate(BaseModel):
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    apodo: str | None = Field(default=None, max_length=50)
    red: RedTarjeta | None = None
    dia_cierre: int | None = Field(None, ge=1, le=28)
    dia_vencimiento: int | None = Field(None, ge=1, le=28)
    limite_credito: DecimalJSON | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    moneda: Moneda | None = None
    percepcion_moneda_extranjera: DecimalJSON | None = Field(default=None, ge=0, le=100, max_digits=5, decimal_places=2)
    color: str | None = Field(None, max_length=7)

    @field_validator("nombre")
    @classmethod
    def validar_nombre(cls, v: str | None) -> str | None:
        if v is None:
            return v
        nombre = v.strip()
        if not nombre:
            raise ValueError("El nombre de la tarjeta no puede estar vacío.")
        return nombre

class CuotaResumen(BaseModel):
    id: UUID
    descripcion: str
    subcategoria_nombre: str | None = None
    numero_cuota: int
    total_cuotas: int
    monto: DecimalJSON
    moneda: str
    fecha_vencimiento: date
    pagada: bool

    class Config:
        from_attributes = True

class ResumenAnterior(BaseModel):
    mes: str
    fecha_vencimiento: date
    fecha_cierre: date
    total: DecimalJSON
    moneda: str
    pagado: bool
    cuotas: list[CuotaResumen]
    total_ars: DecimalJSON = Decimal("0")
    total_usd: DecimalJSON = Decimal("0")
    totales_por_moneda: dict[str, DecimalJSON] = Field(default_factory=dict)

class ResumenFuturo(BaseModel):
    mes: str           # "Junio 2026"
    mes_fecha: date    # primer día del mes, para ordenar
    total: DecimalJSON
    moneda: str
    cantidad_cuotas: int
    cuotas: list[CuotaResumen] = []
    total_ars: DecimalJSON = Decimal("0")
    total_usd: DecimalJSON = Decimal("0")
    totales_por_moneda: dict[str, DecimalJSON] = Field(default_factory=dict)

class ItemSaldoArrastrado(BaseModel):
    id: UUID
    fecha_vencimiento_origen: date
    monto_inicial: DecimalJSON
    monto_restante: DecimalJSON
    moneda: str
    descripcion: str

    class Config:
        from_attributes = True


class BloqueResumenMoneda(BaseModel):
    moneda: str
    total_cuotas_periodo: DecimalJSON = Decimal("0")
    total_original_periodo: DecimalJSON = Decimal("0")
    total_deuda_vencida_anterior: DecimalJSON = Decimal("0")
    saldo_arrastrado_impago: DecimalJSON = Decimal("0")
    items_saldo_arrastrado: list[ItemSaldoArrastrado] = []
    total_a_pagar: DecimalJSON = Decimal("0")
    pago_minimo_estimado: DecimalJSON = Decimal("0")
    # Para moneda extranjera (USD):
    cotizacion_oficial_estimada: DecimalJSON | None = None
    porcentaje_percepcion: DecimalJSON | None = None
    total_estimado_ars: DecimalJSON | None = None


class ResumenTarjeta(BaseModel):
    fecha_cierre_proximo: date
    fecha_vencimiento_proximo: date
    total_comprometido_resumen_actual: DecimalJSON
    total_comprometido_resumen_siguiente: DecimalJSON
    total_original_resumen_actual: DecimalJSON = Decimal("0")
    total_original_resumen_siguiente: DecimalJSON = Decimal("0")
    total_deuda_vencida_anterior: DecimalJSON = Decimal("0")
    saldo_arrastrado_impago: DecimalJSON = Decimal("0")
    items_saldo_arrastrado: list[ItemSaldoArrastrado] = []
    total_a_pagar_resumen_actual: DecimalJSON = Decimal("0")
    pago_minimo_estimado: DecimalJSON = Decimal("0")
    pago_minimo_es_estimado: bool = True
    pago_minimo_aclaracion: str = "Monto de referencia orientativo. El valor definitivo lo establece la entidad bancaria en el resumen de cuenta."
    total_actual_ars: DecimalJSON = Decimal("0")
    total_actual_usd: DecimalJSON = Decimal("0")
    total_siguiente_ars: DecimalJSON = Decimal("0")
    total_siguiente_usd: DecimalJSON = Decimal("0")
    totales_moneda_actual: dict[str, DecimalJSON] = Field(default_factory=dict)
    totales_moneda_siguiente: dict[str, DecimalJSON] = Field(default_factory=dict)
    totales_por_moneda: dict[str, BloqueResumenMoneda] = Field(default_factory=dict)
    cuotas_resumen_actual: list[CuotaResumen]
    cuotas_resumen_siguiente: list[CuotaResumen]
    resumenes_futuros: list[ResumenFuturo]
    resumenes_anteriores: list[ResumenAnterior] = []


class CuotaPendienteOtraMoneda(BaseModel):
    id: UUID
    descripcion: str
    monto: DecimalJSON
    moneda: str
    numero_cuota: int
    total_cuotas: int
    fecha_vencimiento: date


class ResultadoPagoTarjeta(BaseModel):
    id: UUID
    usuario_id: UUID
    tipo: str
    monto: DecimalJSON
    moneda: str
    fecha: date
    descripcion: str
    categoria_id: UUID | None = None
    subcategoria_id: UUID | None = None
    metodo_pago: str | None = None
    billetera_id: UUID | None = None
    tarjeta_id: UUID | None = None
    es_recurrente: bool = False
    estado_verificacion: str | None = None
    fecha_creacion: datetime | None = None
    cuotas_pagadas_count: int = 0
    moneda_pagada: str = ""
    monto_pagado: DecimalJSON = Decimal("0")
    saldo_arrastrado_generado: DecimalJSON | None = None
    saldo_arrastrado_restante: DecimalJSON | None = None
    cuotas_pendientes_otra_moneda: list[CuotaPendienteOtraMoneda] = []
    mensaje_advertencia: str | None = None
    # Campos de trazabilidad multimoneda y percepción (Etapa 3C)
    transaccion_percepcion_id: UUID | None = None
    monto_percepcion: DecimalJSON | None = None
    monto_convertido_pesos: DecimalJSON | None = None
    monto_pesos_total: DecimalJSON | None = None
    monto_original: DecimalJSON | None = None
    moneda_original: str | None = None
    cotizacion_aplicada: DecimalJSON | None = None
    tipo_dolar_usado: str | None = None

    class Config:
        from_attributes = True

class TarjetaCreditoResponse(TarjetaCreditoBase):
    id: UUID
    usuario_id: UUID
    billetera_id: UUID
    estado: EstadoTarjeta
    fecha_creacion: datetime
    resumen_actual: ResumenTarjeta | None = None
    cuotas_recalculadas: int = 0

    class Config:
        from_attributes = True


class PagarTarjetaBody(BaseModel):
    monto: DecimalJSON | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    fecha_pago: date | None = None
    fecha_resumen: date | None = None
    moneda: Moneda | None = None
    billetera_id: UUID | None = None
    pesificar: bool = False
    cotizacion_personalizada: DecimalJSON | None = Field(default=None, gt=0, max_digits=15, decimal_places=4)
    monto_pesos_personalizado: DecimalJSON | None = Field(default=None, gt=0, max_digits=15, decimal_places=2)
    monto_percepcion_personalizado: DecimalJSON | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)


class SimularPesificacionResponse(BaseModel):
    fecha_cierre: date
    monto_usd: DecimalJSON
    cotizacion_oficial: DecimalJSON | None = None
    cotizacion_disponible: bool = True
    porcentaje_percepcion: DecimalJSON
    monto_convertido_ars: DecimalJSON | None = None
    monto_percepcion_ars: DecimalJSON | None = None
    total_estimado_ars: DecimalJSON | None = None


class DetalleTarjetaMes(BaseModel):
    tarjeta_id: str
    tarjeta_nombre: str
    total: float
    moneda: str | None = None


class MesPresionFutura(BaseModel):
    anio: int
    mes: int
    mes_label: str
    total: dict[str, float]
    tarjetas: list[DetalleTarjetaMes]


class PresionFuturaData(BaseModel):
    meses: list[MesPresionFutura]
    total_comprometido: dict[str, float]

    class Config:
        from_attributes = True


class PresionFuturaResponse(BaseModel):
    success: bool
    data: PresionFuturaData

    class Config:
        from_attributes = True

