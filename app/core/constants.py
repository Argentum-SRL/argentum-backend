from decimal import Decimal
from typing import FrozenSet

# Categorías de sistema reservadas que no deben ser ofrecidas en flujos de IA
# ni en la creación/edición manual de transacciones.
CATEGORIAS_SISTEMA: FrozenSet[str] = frozenset({"Ahorro"})

# Control de integridad cuantitativo máximo para montos monetarios (1 billón ARS = 10^12).
# NOTA DE DISEÑO: Esto NO es una regla de negocio ni un límite presupuestario artificial,
# sino un control de integridad contra desbordamiento numérico (overflow) en columnas
# de base de datos Numeric(15, 2) y prevención de errores de tipeo catastróficos.
MAX_MONTO_INTEGRIDAD: Decimal = Decimal("1000000000000")
MAX_MONTO_FLOAT: float = 1_000_000_000_000.0

