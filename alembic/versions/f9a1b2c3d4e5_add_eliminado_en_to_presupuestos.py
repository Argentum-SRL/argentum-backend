"""add eliminado_en to presupuestos

Revision ID: f9a1b2c3d4e5
Revises: f8e9d0c1b2a3
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# Identificadores de revisión de Alembic
revision: str = 'f9a1b2c3d4e5'
down_revision: Union[str, Sequence[str], None] = 'f8e9d0c1b2a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columnas = [c['name'] for c in inspector.get_columns('presupuestos')]

    if 'eliminado_en' not in columnas:
        op.add_column(
            'presupuestos',
            sa.Column('eliminado_en', sa.DateTime(timezone=True), nullable=True)
        )
        # A todo presupuesto con estado 'finalizado' se le asigna eliminado_en = NOW()
        op.execute(sa.text("UPDATE presupuestos SET eliminado_en = NOW() WHERE estado = 'finalizado'"))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columnas = [c['name'] for c in inspector.get_columns('presupuestos')]

    if 'eliminado_en' in columnas:
        op.drop_column('presupuestos', 'eliminado_en')
