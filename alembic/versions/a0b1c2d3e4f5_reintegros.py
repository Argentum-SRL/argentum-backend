"""reintegros

Revision ID: a0b1c2d3e4f5
Revises: ff2b3c4d5e6f
Create Date: 2026-10-21

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, Sequence[str], None] = "ff2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Valor nuevo de enum en TipoNotificacion: REINTEGRO_PENDIENTE
    if bind.dialect.name == "postgresql":
        op.execute("COMMIT")
        op.execute("ALTER TYPE tipo_notificacion_sa_enum ADD VALUE IF NOT EXISTS 'REINTEGRO_PENDIENTE';")

    # 2. Columnas nuevas en transacciones
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns("transacciones")]

    if "reintegro_esperado" not in columns:
        op.add_column(
            "transacciones",
            sa.Column("reintegro_esperado", sa.Numeric(15, 2), nullable=True),
        )

    if "reintegro_de_id" not in columns:
        op.add_column(
            "transacciones",
            sa.Column(
                "reintegro_de_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("transacciones.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )

    indexes = [idx["name"] for idx in inspector.get_indexes("transacciones")]
    if "ix_transacciones_reintegro_de_id" not in indexes:
        op.create_index(
            "ix_transacciones_reintegro_de_id",
            "transacciones",
            ["reintegro_de_id"],
            postgresql_where=sa.text("reintegro_de_id IS NOT NULL") if bind.dialect.name == "postgresql" else None,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index(
            "ix_transacciones_reintegro_de_id",
            table_name="transacciones",
            postgresql_where=sa.text("reintegro_de_id IS NOT NULL"),
        )
    else:
        op.drop_index("ix_transacciones_reintegro_de_id", table_name="transacciones")

    op.drop_column("transacciones", "reintegro_de_id")
    op.drop_column("transacciones", "reintegro_esperado")
