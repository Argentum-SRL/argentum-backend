"""create_eventos_actualizacion

Revision ID: c8e1f2a3b4c5
Revises: f1b8a7c2d3e4
Create Date: 2026-08-31 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c8e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'f1b8a7c2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "eventos_actualizacion" not in tables:
        op.create_table(
            'eventos_actualizacion',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column('usuario_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('usuarios.id', ondelete='CASCADE'), nullable=False),
            sa.Column('entidad', sa.String(length=50), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_eventos_actualizacion_usuario_id', 'eventos_actualizacion', ['usuario_id'], unique=False)
        op.create_index('ix_eventos_actualizacion_created_at', 'eventos_actualizacion', ['created_at'], unique=False)
        op.create_index('ix_eventos_actualizacion_usuario_created', 'eventos_actualizacion', ['usuario_id', 'created_at'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "eventos_actualizacion" in tables:
        op.drop_index('ix_eventos_actualizacion_usuario_created', table_name='eventos_actualizacion')
        op.drop_index('ix_eventos_actualizacion_created_at', table_name='eventos_actualizacion')
        op.drop_index('ix_eventos_actualizacion_usuario_id', table_name='eventos_actualizacion')
        op.drop_table('eventos_actualizacion')
