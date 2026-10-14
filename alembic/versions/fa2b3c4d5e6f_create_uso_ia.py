"""create uso_ia

Revision ID: fa2b3c4d5e6f
Revises: f9a1b2c3d4e5
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'fa2b3c4d5e6f'
down_revision: Union[str, Sequence[str], None] = 'f9a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "uso_ia" not in tables:
        op.create_table(
            'uso_ia',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column('fecha', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('usuario_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True),
            sa.Column('funcion', sa.String(length=40), nullable=False),
            sa.Column('modelo', sa.String(length=60), nullable=False),
            sa.Column('tokens_entrada', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('tokens_entrada_cache', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('tokens_salida', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('segundos_audio', sa.Integer(), server_default=sa.text('0'), nullable=False),
            sa.Column('costo_usd', sa.Numeric(precision=12, scale=6), server_default=sa.text('0'), nullable=False),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_uso_ia_fecha', 'uso_ia', ['fecha'], unique=False)
        op.create_index('ix_uso_ia_usuario_id', 'uso_ia', ['usuario_id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "uso_ia" in tables:
        op.drop_index('ix_uso_ia_usuario_id', table_name='uso_ia')
        op.drop_index('ix_uso_ia_fecha', table_name='uso_ia')
        op.drop_table('uso_ia')
