"""etapa3c_tarjetas_multimoneda

Revision ID: f5c6d7e8f9a0
Revises: f4b5c6d7e8f9
Create Date: 2026-09-02 22:30:00.000000

Etapa 3C: Consumos en dólares con tarjetas en pesos y pagos bimonetarios.
- Agrega percepcion_moneda_extranjera a tarjetas_credito (Numeric(5, 2), default 30.00).
- Agrega pago_origen_id a transacciones para vincular percepciones impositivas al pago origen.
- Actualiza índice único de saldos_arrastrados_tarjeta para soportar múltiples monedas por resumen.
- Asegura existencia de subcategoría 'Impuestos' en categoría 'Banco'.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f5c6d7e8f9a0'
down_revision: Union[str, None] = 'f4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Agregar columna percepcion_moneda_extranjera a tarjetas_credito
    op.add_column(
        'tarjetas_credito',
        sa.Column(
            'percepcion_moneda_extranjera',
            sa.Numeric(5, 2),
            nullable=False,
            server_default='30.00',
            comment='Porcentaje de percepción sobre consumos en moneda extranjera (ej. 30 para 30%)'
        )
    )

    # 2. Agregar columna pago_origen_id a transacciones
    op.add_column(
        'transacciones',
        sa.Column(
            'pago_origen_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('transacciones.id', ondelete='CASCADE'),
            nullable=True
        )
    )
    op.create_index(
        'ix_transacciones_pago_origen_id',
        'transacciones',
        ['pago_origen_id'],
        postgresql_where=sa.text("pago_origen_id IS NOT NULL")
    )

    # 3. Actualizar índice uq_saldos_arrastrados_tarjeta_resumen_activo para incluir moneda
    op.drop_index(
        'uq_saldos_arrastrados_tarjeta_resumen_activo',
        table_name='saldos_arrastrados_tarjeta',
        postgresql_where=sa.text("estado = 'activo'")
    )
    op.create_index(
        'uq_saldos_arrastrados_tarjeta_resumen_moneda_activo',
        'saldos_arrastrados_tarjeta',
        ['tarjeta_id', 'fecha_vencimiento_resumen', 'moneda'],
        unique=True,
        postgresql_where=sa.text("estado = 'activo'")
    )

    # 4. Asegurar subcategoría 'Impuestos' en categoría 'Banco'
    op.execute("""
        DO $$
        DECLARE
            banco_cat_id UUID;
        BEGIN
            SELECT id INTO banco_cat_id FROM categorias WHERE LOWER(nombre) = 'banco' LIMIT 1;
            IF banco_cat_id IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM subcategorias 
                    WHERE categoria_id = banco_cat_id AND LOWER(nombre) = 'impuestos'
                ) THEN
                    INSERT INTO subcategorias (id, categoria_id, nombre, orden, estado)
                    VALUES (gen_random_uuid(), banco_cat_id, 'Impuestos', 10, 'activa');
                END IF;
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # 1. Revertir índice saldos arrastrados
    op.drop_index(
        'uq_saldos_arrastrados_tarjeta_resumen_moneda_activo',
        table_name='saldos_arrastrados_tarjeta',
        postgresql_where=sa.text("estado = 'activo'")
    )
    op.create_index(
        'uq_saldos_arrastrados_tarjeta_resumen_activo',
        'saldos_arrastrados_tarjeta',
        ['tarjeta_id', 'fecha_vencimiento_resumen'],
        unique=True,
        postgresql_where=sa.text("estado = 'activo'")
    )

    # 2. Revertir pago_origen_id en transacciones
    op.drop_index(
        'ix_transacciones_pago_origen_id',
        table_name='transacciones',
        postgresql_where=sa.text("pago_origen_id IS NOT NULL")
    )
    op.drop_column('transacciones', 'pago_origen_id')

    # 3. Revertir percepcion_moneda_extranjera en tarjetas_credito
    op.drop_column('tarjetas_credito', 'percepcion_moneda_extranjera')

    # 4. Revertir subcategoría Impuestos en Banco si fue creada
    op.execute("""
        DELETE FROM subcategorias 
        WHERE LOWER(nombre) = 'impuestos' 
          AND categoria_id IN (SELECT id FROM categorias WHERE LOWER(nombre) = 'banco');
    """)
