"""monitor wpp

Revision ID: fb3c4d5e6f70
Revises: fa2b3c4d5e6f
Create Date: 2026-10-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb3c4d5e6f70'
down_revision: Union[str, Sequence[str], None] = 'fa2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. usuarios.permiso_lectura_mensajes_en (timestamptz, nullable)
    cols_usuarios = [c["name"] for c in inspector.get_columns("usuarios")]
    if "permiso_lectura_mensajes_en" not in cols_usuarios:
        op.add_column("usuarios", sa.Column("permiso_lectura_mensajes_en", sa.DateTime(timezone=True), nullable=True))

    # 2. conversaciones_wpp.tiempo_respuesta_ms (int, nullable)
    cols_conv = [c["name"] for c in inspector.get_columns("conversaciones_wpp")]
    if "tiempo_respuesta_ms" not in cols_conv:
        op.add_column("conversaciones_wpp", sa.Column("tiempo_respuesta_ms", sa.Integer(), nullable=True))

    # 3. conversaciones_wpp.error_ia (bool, not null, server_default false)
    if "error_ia" not in cols_conv:
        op.add_column("conversaciones_wpp", sa.Column("error_ia", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # 4. índice ix_conversaciones_wpp_fecha sobre conversaciones_wpp.fecha
    indexes = [ix["name"] for ix in inspector.get_indexes("conversaciones_wpp")]
    if "ix_conversaciones_wpp_fecha" not in indexes:
        op.create_index("ix_conversaciones_wpp_fecha", "conversaciones_wpp", ["fecha"], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    indexes = [ix["name"] for ix in inspector.get_indexes("conversaciones_wpp")]
    if "ix_conversaciones_wpp_fecha" in indexes:
        op.drop_index("ix_conversaciones_wpp_fecha", table_name="conversaciones_wpp")

    cols_conv = [c["name"] for c in inspector.get_columns("conversaciones_wpp")]
    if "error_ia" in cols_conv:
        op.drop_column("conversaciones_wpp", "error_ia")
    if "tiempo_respuesta_ms" in cols_conv:
        op.drop_column("conversaciones_wpp", "tiempo_respuesta_ms")

    cols_usuarios = [c["name"] for c in inspector.get_columns("usuarios")]
    if "permiso_lectura_mensajes_en" in cols_usuarios:
        op.drop_column("usuarios", "permiso_lectura_mensajes_en")
