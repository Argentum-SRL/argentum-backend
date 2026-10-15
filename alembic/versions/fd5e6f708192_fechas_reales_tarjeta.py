"""fechas reales tarjeta

Revision ID: fd5e6f708192
Revises: fb3c4d5e6f70
Create Date: 2026-10-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'fd5e6f708192'
down_revision: Union[str, Sequence[str], None] = 'fb3c4d5e6f70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # 1. Nuevas columnas en tarjetas_credito
    cols_tarjetas = [c["name"] for c in inspector.get_columns("tarjetas_credito")]
    if "regla_cierre" not in cols_tarjetas:
        op.add_column(
            "tarjetas_credito",
            sa.Column("regla_cierre", sa.String(length=20), nullable=False, server_default="dia_fijo")
        )
    if "semana_cierre" not in cols_tarjetas:
        op.add_column("tarjetas_credito", sa.Column("semana_cierre", sa.Integer(), nullable=True))
    if "dia_semana_cierre" not in cols_tarjetas:
        op.add_column("tarjetas_credito", sa.Column("dia_semana_cierre", sa.Integer(), nullable=True))
    if "dias_hasta_vencimiento" not in cols_tarjetas:
        op.add_column("tarjetas_credito", sa.Column("dias_hasta_vencimiento", sa.Integer(), nullable=True))

    # 2. Nueva tabla fechas_resumen_tarjeta
    tables = inspector.get_table_names()
    if "fechas_resumen_tarjeta" not in tables:
        op.create_table(
            "fechas_resumen_tarjeta",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tarjeta_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tarjetas_credito.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("fecha_cierre", sa.Date(), nullable=False),
            sa.Column("fecha_vencimiento", sa.Date(), nullable=False),
            sa.Column(
                "fecha_creacion",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("tarjeta_id", "fecha_cierre", name="uq_fechas_resumen_tarjeta_tarjeta_id_fecha_cierre"),
        )
        op.create_index(
            "ix_fechas_resumen_tarjeta_tarjeta_id",
            "fechas_resumen_tarjeta",
            ["tarjeta_id"],
            unique=False,
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    tables = inspector.get_table_names()
    if "fechas_resumen_tarjeta" in tables:
        op.drop_table("fechas_resumen_tarjeta")

    cols_tarjetas = [c["name"] for c in inspector.get_columns("tarjetas_credito")]
    if "dias_hasta_vencimiento" in cols_tarjetas:
        op.drop_column("tarjetas_credito", "dias_hasta_vencimiento")
    if "dia_semana_cierre" in cols_tarjetas:
        op.drop_column("tarjetas_credito", "dia_semana_cierre")
    if "semana_cierre" in cols_tarjetas:
        op.drop_column("tarjetas_credito", "semana_cierre")
    if "regla_cierre" in cols_tarjetas:
        op.drop_column("tarjetas_credito", "regla_cierre")
