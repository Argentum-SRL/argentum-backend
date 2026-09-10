from __future__ import annotations

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from app.schemas.billetera import BilleteraRead


class PeriodoDashboard(BaseModel):
    fecha_inicio: str = Field(..., description="Fecha de inicio del ciclo en formato YYYY-MM-DD")
    fecha_fin: str = Field(..., description="Fecha de fin del ciclo en formato YYYY-MM-DD")
    primera_transaccion: Optional[str] = Field(default=None, description="Fecha de la primera transacción histórica")


class BalanceMoneda(BaseModel):
    ingresos: float = Field(..., ge=0, description="Total de ingresos en el ciclo")
    egresos: float = Field(..., ge=0, description="Total de egresos en el ciclo")
    balance: float = Field(..., description="Balance neto (ingresos - egresos)")
    variacion_vs_ciclo_anterior: Optional[float] = Field(default=None, description="Porcentaje de variación respecto al ciclo previo")


class BalanceDashboard(BaseModel):
    ars: BalanceMoneda
    usd: BalanceMoneda


class DisponibleRealMoneda(BaseModel):
    saldo_billeteras: float = Field(..., description="Saldo total actual en billeteras activas")
    cuotas_proximo_ciclo: float = Field(..., ge=0, description="Cuotas comprometidas a vencer")
    suscripciones_mensuales: float = Field(default=0.0, ge=0, description="Costo equivalente mensual de suscripciones")
    disponible: float = Field(..., description="Saldo disponible real (saldo - cuotas - suscripciones)")


class DisponibleRealDashboard(BaseModel):
    ars: DisponibleRealMoneda
    usd: DisponibleRealMoneda


class MovimientoDashboard(BaseModel):
    id: str
    descripcion: str
    fecha: str = Field(..., description="Fecha del movimiento en formato YYYY-MM-DD")
    monto: float = Field(..., ge=0, description="Monto del movimiento")
    tipo: str = Field(..., description="Tipo de movimiento: 'ingreso' o 'egreso'")
    moneda: str = Field(..., description="Moneda: 'ARS' o 'USD'")
    billetera_nombre: str
    categoria_nombre: Optional[str] = None
    estado_verificacion: Optional[str] = None
    subcategoria_nombre: Optional[str] = None


class PagoDashboard(BaseModel):
    id: str
    nombre: str
    monto: float = Field(..., ge=0, description="Monto a pagar")
    moneda: str = Field(..., description="Moneda: 'ARS' o 'USD'")
    fecha_cobro: str = Field(..., description="Fecha de vencimiento/cobro en formato YYYY-MM-DD")
    dias_restantes: int = Field(..., description="Días restantes hasta la fecha de cobro")
    tipo: str = Field(..., description="Tipo: 'suscripcion', 'cuota', 'resumen_tarjeta'")
    color: Optional[str] = None
    red: Optional[str] = None
    billetera_nombre: Optional[str] = None
    billetera_id: Optional[str] = None
    es_vencido: Optional[bool] = False


class CategoriaGastoItem(BaseModel):
    categoria_id: Optional[str] = None
    categoria_nombre: str
    monto: float = Field(..., ge=0, description="Monto real gastado en el ciclo")


class GastosPorCategoriaDashboard(BaseModel):
    ars: List[CategoriaGastoItem] = Field(default_factory=list, description="Desglose real de gastos en ARS")
    usd: List[CategoriaGastoItem] = Field(default_factory=list, description="Desglose real de gastos en USD")


class DashboardResumenResponse(BaseModel):
    periodo: PeriodoDashboard
    balance: BalanceDashboard
    disponible_real: DisponibleRealDashboard
    gastos_por_categoria: GastosPorCategoriaDashboard = Field(
        default_factory=lambda: GastosPorCategoriaDashboard(ars=[], usd=[]),
        description="Desglose de gastos reales acumulados por categoría en el ciclo actual"
    )
    ultimos_movimientos: List[MovimientoDashboard]
    proximos_pagos: List[PagoDashboard]


class CotizacionDolarResponse(BaseModel):
    tipo: str
    nombre: Optional[str] = None
    compra: Optional[float] = Field(default=None, ge=0)
    venta: Optional[float] = Field(default=None, ge=0)
    promedio: Optional[float] = Field(default=None, ge=0)
    moneda: str = "USD"
    fecha_actualizacion: Optional[str] = None
    error: Optional[str] = None


class BilleteraDashboardItem(BaseModel):
    id: str
    nombre: str
    moneda: str
    saldo_actual: float
    saldo_inicial: float = 0.0
    es_principal: bool = False
    es_efectivo: bool = False
    estado: str = "activa"
    fecha_creacion: Optional[str] = None
    bank_id: Optional[str] = None
    tiene_transacciones: bool = False


class ResumenCompletoResponse(BaseModel):
    billeteras: List[BilleteraDashboardItem]
    resumen: DashboardResumenResponse
    cotizacion: CotizacionDolarResponse


class SubcategoriaGastoResponse(BaseModel):
    subcategoria_id: str
    subcategoria_nombre: str
    gasto_actual_ciclo: Dict[str, float]


class ProyeccionCategoria(BaseModel):
    categoria_id: Optional[str] = None
    categoria_nombre: str
    gasto_actual_ciclo: float = Field(..., ge=0)
    promedio_historico: float = Field(..., ge=0)
    proyectado: float = Field(..., ge=0)
    rango_piso: float = Field(..., ge=0)
    rango_techo: float = Field(..., ge=0)
    fuera_de_patron: bool


class PeriodoProyeccion(BaseModel):
    fecha_inicio: str
    fecha_fin: str
    dias_transcurridos: int = Field(..., ge=0)
    dias_restantes: int = Field(..., ge=0)
    dias_totales: int = Field(..., ge=1)


class CertezasProyeccion(BaseModel):
    cuotas_restantes: float = Field(..., ge=0)
    suscripciones_restantes: float = Field(..., ge=0)
    compromisos_restantes: float = Field(default=0.0, ge=0)
    total: float = Field(..., ge=0)


class PesosProyeccion(BaseModel):
    historial: float = Field(..., ge=0.0, le=1.0)
    ciclo_actual: float = Field(..., ge=0.0, le=1.0)


class CalibracionAuditoria(BaseModel):
    pasa_puerta: bool = Field(..., description="Indica si la proyección superó la prueba de calibración individual sobre ciclos pasados")
    ciclos_evaluados: int = Field(..., ge=0, description="Cantidad de ciclos cerrados con proyección previa evaluados en backtest")
    cobertura_50: Optional[float] = Field(default=None, description="Cobertura empírica observada en el intervalo al 50%")
    cobertura_80: Optional[float] = Field(default=None, description="Cobertura empírica observada en el intervalo al 80%")
    cobertura_95: Optional[float] = Field(default=None, description="Cobertura empírica observada en el intervalo al 95%")
    ancho_medio_80_rel: Optional[float] = Field(default=None, description="Ancho relativo medio del intervalo al 80% respecto al gasto real")
    motivo: Optional[str] = Field(default=None, description="Motivo de rechazo de calibración si no pasa la puerta")
    mensaje: Optional[str] = Field(default=None, description="Explicación en lenguaje claro para el usuario")


class ProyeccionMoneda(BaseModel):
    periodo: PeriodoProyeccion
    gasto_proyectado_total: Optional[float] = Field(default=None, ge=0)
    rango: Optional[Dict[str, float]] = None
    rango_poco_informativo: Optional[bool] = None
    balance_proyectado: Optional[float] = None
    ingresos_proyectados: Optional[float] = Field(default=None, ge=0)
    certezas: CertezasProyeccion
    desglose_por_categoria: Optional[List[ProyeccionCategoria]] = None
    nivel_confianza: Optional[Literal["alto", "medio", "bajo", "sin_datos", "inicial", "alta", "media", "baja"]] = None
    ciclos_analizados: Optional[int] = Field(default=0, ge=0)
    pesos: Optional[PesosProyeccion] = None
    advertencias: List[str] = Field(default_factory=list)
    datos_suficientes: Optional[bool] = False
    clasificacion: Optional[Dict[str, int]] = None
    distribucion: Optional[Dict[str, float]] = None
    intervalos: Optional[Dict[str, Dict[str, float]]] = None
    descomposicion: Optional[Dict[str, Optional[float]]] = None
    mensaje_insuficiente: Optional[str] = None
    mensaje: Optional[str] = None
    calibracion: Optional[CalibracionAuditoria] = None


class ProyeccionesResponse(BaseModel):
    ars: ProyeccionMoneda
    usd: ProyeccionMoneda


class PeriodoActualResponse(BaseModel):
    fecha_inicio: str
    fecha_fin: str
