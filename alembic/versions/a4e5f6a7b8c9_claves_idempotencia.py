"""claves_idempotencia

Revision ID: a4e5f6a7b8c9
Revises: a3d4e5f6a7b8
Create Date: 2026-10-22

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "a3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36)
    json_type = postgresql.JSONB(astext_type=sa.Text()) if bind.dialect.name == "postgresql" else sa.JSON()

    op.create_table(
        "claves_idempotencia",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("usuario_id", uuid_type, sa.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("clave", sa.String(length=100), nullable=False),
        sa.Column("ruta", sa.String(length=200), nullable=False),
        sa.Column("hash_cuerpo", sa.String(length=64), nullable=False),
        sa.Column("codigo_http", sa.Integer(), nullable=False),
        sa.Column("respuesta", json_type, nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("usuario_id", "clave", name="uq_claves_idempotencia_usuario_clave"),
        comment="No guarda datos personales más allá de la respuesta de la propia operación.",
    )

    op.create_index("ix_claves_idempotencia_usuario_id", "claves_idempotencia", ["usuario_id"])
    op.create_index("ix_claves_idempotencia_creada_en", "claves_idempotencia", ["creada_en"])


def downgrade() -> None:
    op.drop_index("ix_claves_idempotencia_creada_en", table_name="claves_idempotencia")
    op.drop_index("ix_claves_idempotencia_usuario_id", table_name="claves_idempotencia")
    op.drop_table("claves_idempotencia")
