# Modelos de datos para personas sintéticas con verdad conocida
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from app.models.transaccion import EstadoVerificacionTransaccion, TipoTransaccion
from app.models.usuario import Moneda


# Tipos de verdad por movimiento según Decisión 5
TipoVerdadMovimiento = Literal[
    "ingreso_habitual",
    "ingreso_extra",
    "gasto_fijo",
    "gasto_diario",
    "costumbre",
    "eventual",
    "obligacion_declarada",
]

# Tipos de ingreso por persona según Decisión 5
TipoIngresoPersona = Literal[
    "regular",
    "variable",
    "intermitente",
    "erratico",
    "indexado",
]


class ItemCatalogo:
    """Representación liviana para categoría, subcategoría o billetera con atributo nombre."""

    def __init__(self, id: str, nombre: str):
        self.id = id
        self.nombre = nombre

    def __repr__(self) -> str:
        return f"ItemCatalogo(id={self.id!r}, nombre={self.nombre!r})"


@dataclass
class MovimientoSintetico:
    """Movimiento financiero liviano generado en memoria con etiqueta de verdad conocida."""

    id: str
    fecha: date
    monto: Decimal
    moneda: Moneda
    tipo: TipoTransaccion
    descripcion: str
    categoria_id: str
    categoria: ItemCatalogo
    subcategoria_id: str | None = None
    subcategoria: ItemCatalogo | None = None
    billetera_id: str = "billetera_principal"
    billetera: ItemCatalogo = field(default_factory=lambda: ItemCatalogo("billetera_principal", "Cuenta Principal"))
    estado_verificacion: EstadoVerificacionTransaccion = EstadoVerificacionTransaccion.CONFIRMADA
    es_recurrente: bool = False
    es_cuota_hija: bool = False
    es_padre_cuotas: bool = False
    movimiento_meta_id: Any = None
    pago_resumen_vencimiento: Any = None

    # Verdad conocida (Ground Truth)
    tipo_verdadero: TipoVerdadMovimiento = "gasto_diario"
    grupo_verdadero: str = ""

    def __post_init__(self):
        if not isinstance(self.monto, Decimal):
            self.monto = Decimal(str(self.monto))


@dataclass
class GrupoVerdad:
    """Definición de un grupo o concepto con su clase esperada según Decisión 8."""

    nombre: str
    tipo_verdadero: TipoVerdadMovimiento
    clase_esperada: str  # "COMPROMISO", "HABITO", "VARIABLE" o "NO_EVALUADO"
    descripcion_concepto: str = ""


@dataclass
class Persona:
    """Perfil sintético completo con 12 meses de historia y verdad conocida."""

    id: str
    nombre: str
    descripcion_perfil: str
    semilla: int
    tipo_ingreso: TipoIngresoPersona
    cobertura_real: float
    ciclos: list[tuple[date, date]]
    movimientos: list[MovimientoSintetico] = field(default_factory=list)
    grupos_verdad: dict[str, GrupoVerdad] = field(default_factory=dict)

    def calcular_hash_contenido(self) -> str:
        """Calcula un hash SHA256 reproducible basado en la secuencia de movimientos."""
        import hashlib

        hasher = hashlib.sha256()
        for m in sorted(self.movimientos, key=lambda x: (x.fecha, x.id)):
            linea = f"{m.fecha.isoformat()}|{m.monto}|{m.tipo.value}|{m.descripcion}|{m.categoria_id}|{m.subcategoria_id}|{m.tipo_verdadero}|{m.grupo_verdadero};"
            hasher.update(linea.encode("utf-8"))
        return hasher.hexdigest()
