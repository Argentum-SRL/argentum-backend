"""create conversaciones_wpp

Revision ID: f8a6b4c3d2e1
Revises: e5a46c93b12d
Create Date: 2026-06-14 03:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f8a6b4c3d2e1'
down_revision: Union[str, Sequence[str], None] = 'e5a46c93b12d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    
    # Verificar si el tipo de enum ya existe en Postgres
    has_type = bind.execute(
        sa.text("SELECT exists (SELECT 1 FROM pg_type WHERE typname = 'tipo_mensaje_wpp_enum');")
    ).scalar()

    if not has_type:
        tipo_mensaje_wpp_enum = postgresql.ENUM('texto', 'audio', name='tipo_mensaje_wpp_enum')
        tipo_mensaje_wpp_enum.create(bind)

    # Verificar si la tabla ya existe
    has_table = bind.execute(
        sa.text("SELECT exists (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'conversaciones_wpp');")
    ).scalar()

    if not has_table:
        # Crear la tabla conversaciones_wpp
        op.create_table(
            'conversaciones_wpp',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column('usuario_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('usuarios.id'), nullable=False),
            sa.Column('mensaje_usuario', sa.Text(), nullable=False),
            sa.Column('tipo_mensaje', postgresql.ENUM('texto', 'audio', name='tipo_mensaje_wpp_enum', create_type=False), nullable=False, server_default='texto'),
            sa.Column('transcripcion', sa.Text(), nullable=True),
            sa.Column('mensaje_bot', sa.Text(), nullable=False),
            sa.Column('intent_detectado', sa.String(length=100), nullable=True),
            sa.Column('entidades', sa.JSON(), nullable=True),
            sa.Column('accion_ejecutada', sa.String(length=100), nullable=True),
            sa.Column('confianza', sa.Numeric(precision=4, scale=3), nullable=True),
            sa.Column('slot_filling_activo', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('slot_filling_estado', sa.JSON(), nullable=True),
            sa.Column('fecha', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()'))
        )


def downgrade() -> None:
    bind = op.get_bind()
    
    # Eliminar la tabla si existe
    has_table = bind.execute(
        sa.text("SELECT exists (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'conversaciones_wpp');")
    ).scalar()

    if has_table:
        op.drop_table('conversaciones_wpp')

    # Eliminar el enum tipo_mensaje_wpp_enum si existe
    has_type = bind.execute(
        sa.text("SELECT exists (SELECT 1 FROM pg_type WHERE typname = 'tipo_mensaje_wpp_enum');")
    ).scalar()
    
    if has_type:
        postgresql.ENUM(name='tipo_mensaje_wpp_enum').drop(bind)
