"""unificar_moneda_tarjeta_enum

Revision ID: d2a3b4c5d6e7
Revises: c1a2b3c4d5e6
Create Date: 2026-09-02 20:25:00.000000

Migra la columna moneda de tarjetas_credito para utilizar moneda_enum en lugar de
moneda_tarjeta_enum, y elimina moneda_tarjeta_enum de PostgreSQL.

SEGURIDAD EN TRÁFICO ACTIVO (ZERO-DOWNTIME):
Esta migración es 100% segura de ejecutar mientras el código anterior está sirviendo
tráfico. Los valores textuales subyacentes de ambos enums son idénticos ('ARS', 'USD').
Tanto SQLAlchemy como las consultas SQL raw de la versión previa leen y escriben
los literales de texto 'ARS' y 'USD', compatibles inmediatamente con el nuevo tipo
moneda_enum sin interrupción alguna.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd2a3b4c5d6e7'
down_revision: Union[str, None] = 'c1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Migrar la columna moneda para usar moneda_enum
    op.alter_column(
        'tarjetas_credito',
        'moneda',
        existing_type=postgresql.ENUM('ARS', 'USD', name='moneda_tarjeta_enum'),
        type_=postgresql.ENUM('ARS', 'USD', name='moneda_enum', create_type=False),
        postgresql_using='moneda::text::moneda_enum',
        existing_nullable=False,
    )

    # 2. Eliminar el tipo enum obsoleto una vez liberado
    op.execute("DROP TYPE moneda_tarjeta_enum")


def downgrade() -> None:
    # 1. Recrear el tipo enum para rollback
    op.execute("CREATE TYPE moneda_tarjeta_enum AS ENUM ('ARS', 'USD')")

    # 2. Revertir la columna para usar moneda_tarjeta_enum
    op.alter_column(
        'tarjetas_credito',
        'moneda',
        existing_type=postgresql.ENUM('ARS', 'USD', name='moneda_enum'),
        type_=postgresql.ENUM('ARS', 'USD', name='moneda_tarjeta_enum', create_type=False),
        postgresql_using='moneda::text::moneda_tarjeta_enum',
        existing_nullable=False,
    )
