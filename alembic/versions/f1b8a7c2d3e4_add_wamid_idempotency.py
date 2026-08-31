"""add_wamid_idempotency

Revision ID: f1b8a7c2d3e4
Revises: e4c82b19a702
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1b8a7c2d3e4'
down_revision: Union[str, Sequence[str], None] = 'e4c82b19a702'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Crear tabla mensajes_whatsapp_procesados si no existe
    if "mensajes_whatsapp_procesados" not in tables:
        op.create_table(
            'mensajes_whatsapp_procesados',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column('wamid', sa.String(length=255), nullable=False),
            sa.Column('telefono', sa.String(length=50), nullable=True),
            sa.Column('tipo_mensaje', sa.String(length=50), nullable=True),
            sa.Column('fecha_recepcion', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('wamid', name='uq_mensajes_whatsapp_procesados_wamid')
        )
        op.create_index('ix_mensajes_whatsapp_procesados_wamid', 'mensajes_whatsapp_procesados', ['wamid'], unique=True)

    # 2. Agregar columna wamid a conversaciones_wpp si no existe
    if "conversaciones_wpp" in tables:
        columns = [c["name"] for c in inspector.get_columns("conversaciones_wpp")]
        if "wamid" not in columns:
            op.add_column(
                'conversaciones_wpp',
                sa.Column('wamid', sa.String(length=255), nullable=True)
            )
            op.create_index('ix_conversaciones_wpp_wamid', 'conversaciones_wpp', ['wamid'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "conversaciones_wpp" in tables:
        columns = [c["name"] for c in inspector.get_columns("conversaciones_wpp")]
        if "wamid" in columns:
            op.drop_index('ix_conversaciones_wpp_wamid', table_name='conversaciones_wpp')
            op.drop_column('conversaciones_wpp', 'wamid')

    if "mensajes_whatsapp_procesados" in tables:
        op.drop_index('ix_mensajes_whatsapp_procesados_wamid', table_name='mensajes_whatsapp_procesados')
        op.drop_table('mensajes_whatsapp_procesados')
