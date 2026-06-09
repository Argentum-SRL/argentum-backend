"""add_missing_performance_indexes_v2

Revision ID: 329646d68fe9
Revises: 75381af94d37
Create Date: 2026-06-08 20:03:10.422796

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '329646d68fe9'
down_revision: Union[str, Sequence[str], None] = 'db377b3256b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_transacciones_recurrente_fecha
        ON transacciones (recurrente_id, fecha)
        WHERE recurrente_id IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_grupos_cuotas_tarjeta_usuario
        ON grupos_cuotas (tarjeta_id, usuario_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_cuotas_grupo_vencimiento
        ON cuotas (grupo_id, fecha_vencimiento)
        WHERE pagada = false
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_historial_suscripcion_id_fecha
        ON historial_suscripciones (suscripcion_id, vigente_desde DESC)
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_transacciones_recurrente_fecha")
    op.execute("DROP INDEX IF EXISTS ix_grupos_cuotas_tarjeta_usuario")
    op.execute("DROP INDEX IF EXISTS ix_cuotas_grupo_vencimiento")
    op.execute("DROP INDEX IF EXISTS ix_historial_suscripcion_id_fecha")
