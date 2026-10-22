"""fecha_ultimo_control

Revision ID: a3d4e5f6a7b8
Revises: a2c3d4e5f6a7
Create Date: 2026-10-22

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "a3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "a2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("billeteras", sa.Column("fecha_ultimo_control", sa.Date(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE billeteras SET fecha_ultimo_control = ("
            "SELECT max(fecha) FROM ajustes_saldo WHERE ajustes_saldo.billetera_id = billeteras.id"
            ")"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("billeteras") as batch_op:
            batch_op.drop_column("fecha_ultimo_control")
    else:
        op.drop_column("billeteras", "fecha_ultimo_control")
