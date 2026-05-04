"""reset_refresh_tokens_structure

Revision ID: cafcf4463cd1
Revises: 1ee7ae8b471d
Create Date: 2026-05-04 14:53:06.915771

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'cafcf4463cd1'
down_revision: Union[str, Sequence[str], None] = '1ee7ae8b471d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Eliminar la tabla existente para limpiar inconsistencias
    op.execute("DROP TABLE IF EXISTS refresh_tokens CASCADE")

    # 2. Crear la tabla con la estructura exacta solicitada
    op.create_table(
        'refresh_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('usuario_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('token_id', sa.String(length=64), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('fecha_expiracion', sa.DateTime(timezone=True), nullable=False),
        sa.Column('fecha_creacion', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revocado', sa.Boolean(), nullable=False),
        sa.Column('device_info', sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_id')
    )

    # 3. Crear índices adicionales si son necesarios
    op.create_index('ix_refresh_tokens_token_id', 'refresh_tokens', ['token_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_refresh_tokens_token_id', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
