# Funciones de calendario, inflación y contexto de generación de personas sintéticas
from __future__ import annotations

import calendar
import random
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.models.transaccion import EstadoVerificacionTransaccion, TipoTransaccion
from app.models.usuario import Moneda
from tests.motor.personas.catalogo import CATALOGO, IPC_MAP
from tests.motor.personas.modelos import (
    GrupoVerdad,
    ItemCatalogo,
    MovimientoSintetico,
    TipoVerdadMovimiento,
)


# Rango de 12 meses fijos finalizando el 31/08/2026 (Decisión 3)
MESES_HISTORIA = [
    (2025, 9),
    (2025, 10),
    (2025, 11),
    (2025, 12),
    (2026, 1),
    (2026, 2),
    (2026, 3),
    (2026, 4),
    (2026, 5),
    (2026, 6),
    (2026, 7),
    (2026, 8),
]

FECHA_FIN_HISTORIA = date(2026, 8, 31)


def obtener_ciclos_12_meses() -> list[tuple[date, date]]:
    """Genera los 12 ciclos mensuales regulares desde septiembre 2025 hasta agosto 2026."""
    ciclos = []
    for anio, mes in MESES_HISTORIA:
        ultimo_dia = calendar.monthrange(anio, mes)[1]
        ciclos.append((date(anio, mes, 1), date(anio, mes, ultimo_dia)))
    return ciclos


def factor_inflacion(anio: int, mes: int) -> Decimal:
    """Calcula el factor de inflación acumulada respecto al mes base (2025-09)."""
    clave = f"{anio:04d}-{mes:02d}"
    base = IPC_MAP["2025-09"]
    actual = IPC_MAP.get(clave, base)
    return (actual / base).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def factor_inflacion_rezago(anio: int, mes: int, rezago_meses: int = 2) -> Decimal:
    """Calcula el factor de inflación con N meses de rezago (para haberes jubilatorios indexados)."""
    idx = None
    for i, (a, m) in enumerate(MESES_HISTORIA):
        if a == anio and m == mes:
            idx = i
            break
    if idx is None or idx < rezago_meses:
        mes_rezagado = mes - rezago_meses
        anio_rezagado = anio
        if mes_rezagado <= 0:
            mes_rezagado += 12
            anio_rezagado -= 1
        clave = f"{anio_rezagado:04d}-{mes_rezagado:02d}"
    else:
        a_r, m_r = MESES_HISTORIA[idx - rezago_meses]
        clave = f"{a_r:04d}-{m_r:02d}"

    base = IPC_MAP.get("2025-07", IPC_MAP["2025-09"])
    actual = IPC_MAP.get(clave, base)
    return (actual / base).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


class ContextoGeneracionPersona:
    """Contexto de ayuda para armar movimientos coherentes y deterministas."""

    def __init__(self, persona_id: str, semilla: int):
        self.persona_id = persona_id
        self.semilla = semilla
        self.rng = random.Random(semilla)
        self.secuencia_tx = 0
        self.movimientos: list[MovimientoSintetico] = []
        self.grupos: dict[str, GrupoVerdad] = {}

    def nuevo_id(self) -> str:
        self.secuencia_tx += 1
        return f"{self.persona_id}-tx-{self.secuencia_tx:04d}"

    def registrar_grupo(
        self,
        nombre: str,
        tipo_verdadero: TipoVerdadMovimiento,
        descripcion: str = "",
    ):
        clase_esperada = "NO_EVALUADO"
        if tipo_verdadero == "gasto_fijo":
            clase_esperada = "COMPROMISO"
        elif tipo_verdadero == "costumbre":
            clase_esperada = "HABITO"
        elif tipo_verdadero in ("gasto_diario", "eventual"):
            clase_esperada = "VARIABLE"

        self.grupos[nombre] = GrupoVerdad(
            nombre=nombre,
            tipo_verdadero=tipo_verdadero,
            clase_esperada=clase_esperada,
            descripcion_concepto=descripcion,
        )

    def agregar_movimiento(
        self,
        fecha: date,
        monto: Decimal,
        tipo: TipoTransaccion,
        descripcion: str,
        nombre_cat: str,
        nombre_subcat: str | None,
        grupo_verdadero: str,
        tipo_verdadero: TipoVerdadMovimiento,
        moneda: Moneda = Moneda.ARS,
        es_recurrente: bool = False,
    ):
        if fecha > FECHA_FIN_HISTORIA:
            return

        cat = CATALOGO.categoria(nombre_cat)
        subcat = CATALOGO.subcategoria(nombre_subcat, nombre_cat) if nombre_subcat else None
        monto_final = monto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        tx = MovimientoSintetico(
            id=self.nuevo_id(),
            fecha=fecha,
            monto=monto_final,
            moneda=moneda,
            tipo=tipo,
            descripcion=descripcion,
            categoria_id=cat.id,
            categoria=cat,
            subcategoria_id=subcat.id if subcat else None,
            subcategoria=subcat,
            billetera_id=f"bil-{self.persona_id}",
            billetera=ItemCatalogo(f"bil-{self.persona_id}", f"Billetera {self.persona_id}"),
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            es_recurrente=es_recurrente,
            tipo_verdadero=tipo_verdadero,
            grupo_verdadero=grupo_verdadero,
        )
        self.movimientos.append(tx)
