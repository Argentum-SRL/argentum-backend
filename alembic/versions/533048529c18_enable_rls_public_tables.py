"""enable_rls_public_tables

Revision ID: 533048529c18
Revises: d1e2f3a4b5c6
Create Date: 2026-09-11 16:39:17.304392

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '533048529c18'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = [
    "analisis_ia",
    "billeteras",
    "calibraciones_usuario",
    "categorias",
    "categorias_excluidas",
    "configuracion_notificaciones",
    "conversaciones_wpp",
    "correcciones_importacion",
    "cotizaciones_dolar",
    "cuotas",
    "eventos_actualizacion",
    "feriados_ar",
    "grupos_cuotas",
    "historial_perfiles_financieros",
    "historial_suscripciones",
    "importaciones_resumen",
    "ipc_cache",
    "mensajes_whatsapp_procesados",
    "metas",
    "movimientos_meta",
    "notificaciones",
    "pagos_saldo_arrastrado",
    "perfiles_financieros",
    "periodos_presupuesto",
    "presupuestos",
    "presupuestos_categorias",
    "saldos_arrastrados_tarjeta",
    "subcategorias",
    "suscripciones",
    "tarjetas_credito",
    "transacciones",
    "transacciones_recurrentes",
    "transferencias_internas",
    "usuarios",
]


def upgrade() -> None:
    """Enable Row Level Security on all public data tables."""
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Disable Row Level Security on all public data tables."""
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")

