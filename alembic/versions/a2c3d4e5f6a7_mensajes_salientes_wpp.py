"""mensajes_salientes_wpp

Revision ID: a2c3d4e5f6a7
Revises: a0b1c2d3e4f5
Create Date: 2026-10-22

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36)

    op.create_table(
        "mensajes_salientes_wpp",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("fecha", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("usuario_id", uuid_type, sa.ForeignKey("usuarios.id", ondelete="SET NULL"), nullable=True),
        sa.Column("telefono_hash", sa.String(length=64), nullable=True),
        sa.Column("tipo", sa.String(length=60), nullable=False),
        sa.Column("es_plantilla", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("partes", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("aviso_tipo", sa.String(length=40), nullable=True),
        sa.Column("wamid_entrante", sa.String(length=255), nullable=True),
        sa.Column("enviado", sa.Boolean(), nullable=False),
        sa.Column("motivo_no_enviado", sa.String(length=40), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("codigo_error_meta", sa.Integer(), nullable=True),
        sa.Column("wamid_meta", sa.String(length=255), nullable=True, unique=True),
        sa.Column("estado_meta", sa.String(length=20), nullable=True),
        sa.Column("tipo_cobro", sa.String(length=40), nullable=True),
        sa.Column("categoria_cobro", sa.String(length=40), nullable=True),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_mensajes_salientes_wpp_fecha", "mensajes_salientes_wpp", ["fecha"])
    op.create_index("ix_mensajes_salientes_wpp_usuario_id", "mensajes_salientes_wpp", ["usuario_id"])
    op.create_index("ix_mensajes_salientes_wpp_telefono_hash", "mensajes_salientes_wpp", ["telefono_hash"])
    op.create_index("ix_mensajes_salientes_wpp_wamid_entrante", "mensajes_salientes_wpp", ["wamid_entrante"])


def downgrade() -> None:
    op.drop_index("ix_mensajes_salientes_wpp_wamid_entrante", table_name="mensajes_salientes_wpp")
    op.drop_index("ix_mensajes_salientes_wpp_telefono_hash", table_name="mensajes_salientes_wpp")
    op.drop_index("ix_mensajes_salientes_wpp_usuario_id", table_name="mensajes_salientes_wpp")
    op.drop_index("ix_mensajes_salientes_wpp_fecha", table_name="mensajes_salientes_wpp")
    op.drop_table("mensajes_salientes_wpp")
