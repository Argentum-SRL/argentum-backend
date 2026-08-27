"""add_feriados_ar_and_ciclo_ajuste_direccion

Revision ID: b4f81c92a101
Revises: a1e49a12aba0
Create Date: 2026-08-27 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b4f81c92a101'
down_revision: Union[str, Sequence[str], None] = 'a1e49a12aba0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Crear tabla feriados_ar
    op.create_table(
        'feriados_ar',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('fecha', sa.Date(), nullable=False),
        sa.Column('nombre', sa.String(length=255), nullable=True),
        sa.Column('anio', sa.Integer(), nullable=False),
        sa.Column('fecha_actualizacion', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('fecha', name='uq_feriados_ar_fecha')
    )
    op.create_index('ix_feriados_ar_fecha', 'feriados_ar', ['fecha'], unique=True)
    op.create_index('ix_feriados_ar_anio', 'feriados_ar', ['anio'], unique=False)

    # 2. Crear enum ciclo_ajuste_direccion_enum y columna en usuarios
    bind = op.get_bind()
    engine_name = bind.engine.name if hasattr(bind, 'engine') else bind.dialect.name
    
    if engine_name == 'postgresql':
        has_type = bind.execute(
            sa.text("SELECT 1 FROM pg_type WHERE typname = 'ciclo_ajuste_direccion_enum'")
        ).scalar()
        if not has_type:
            op.execute("CREATE TYPE ciclo_ajuste_direccion_enum AS ENUM ('anterior', 'posterior');")
    
    op.add_column(
        'usuarios',
        sa.Column(
            'ciclo_ajuste_direccion',
            sa.Enum('anterior', 'posterior', name='ciclo_ajuste_direccion_enum'),
            nullable=True
        )
    )


def downgrade() -> None:
    # 1. Eliminar columna y enum
    op.drop_column('usuarios', 'ciclo_ajuste_direccion')
    
    bind = op.get_bind()
    engine_name = bind.engine.name if hasattr(bind, 'engine') else bind.dialect.name
    if engine_name == 'postgresql':
        op.execute("DROP TYPE IF EXISTS ciclo_ajuste_direccion_enum;")

    # 2. Eliminar tabla feriados_ar
    op.drop_index('ix_feriados_ar_anio', table_name='feriados_ar')
    op.drop_index('ix_feriados_ar_fecha', table_name='feriados_ar')
    op.drop_table('feriados_ar')
