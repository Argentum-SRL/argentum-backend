"""tarjetas_credito_fixes_etapa3a

Revision ID: f3a4b5c6d7e8
Revises: e3a4b5c6d7e8
Create Date: 2026-09-02 21:00:00.000000

Etapa 3A: Arreglo de bugs críticos del módulo de tarjetas de crédito.
- Agrega campo pago_resumen_vencimiento (Date, nullable) en la tabla transacciones para
  vincular inequívocamente cada pago con el resumen que salda (anti doble débito).
- Crea índice ix_transacciones_pago_resumen_vencimiento para optimizar búsquedas por tarjeta y vencimiento.
- Agrega campo transaccion_pago_id (UUID, nullable, FK transacciones.id ON DELETE SET NULL) en la tabla
  cuotas para rastrear exactamente qué pago canceló cada cuota (reversión exacta de pagos sin heurísticas).
- Crea índice ix_cuotas_transaccion_pago_id en cuotas.
- Inserta con idempotencia la subcategoría canónica 'Tarjeta de crédito' bajo la categoría 'Banco'
  (orden 4, estado 'activa') si aún no existe.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Agregar columna pago_resumen_vencimiento a transacciones
    op.add_column(
        'transacciones',
        sa.Column('pago_resumen_vencimiento', sa.Date(), nullable=True)
    )

    # 2. Crear índice parcial en transacciones
    op.create_index(
        'ix_transacciones_pago_resumen_vencimiento',
        'transacciones',
        ['tarjeta_id', 'pago_resumen_vencimiento'],
        unique=False,
        postgresql_where=sa.text('pago_resumen_vencimiento IS NOT NULL')
    )

    # 3. Agregar columna transaccion_pago_id a cuotas
    op.add_column(
        'cuotas',
        sa.Column(
            'transaccion_pago_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('transacciones.id', ondelete='SET NULL'),
            nullable=True
        )
    )

    # 4. Crear índice parcial en cuotas
    op.create_index(
        'ix_cuotas_transaccion_pago_id',
        'cuotas',
        ['transaccion_pago_id'],
        unique=False,
        postgresql_where=sa.text('transaccion_pago_id IS NOT NULL')
    )

    # 5. Inserción idempotente de la subcategoría 'Tarjeta de crédito' en la categoría 'Banco'
    op.execute(sa.text("""
        INSERT INTO subcategorias (id, categoria_id, nombre, orden, estado)
        SELECT gen_random_uuid(), c.id, 'Tarjeta de crédito', 4, 'activa'
        FROM categorias c
        WHERE c.nombre = 'Banco' AND c.tipo = 'egreso'
          AND NOT EXISTS (
              SELECT 1 FROM subcategorias s
              WHERE s.categoria_id = c.id AND s.nombre = 'Tarjeta de crédito'
          );
    """))


def downgrade() -> None:
    # 1. Eliminar subcategoría 'Tarjeta de crédito' de la categoría 'Banco'
    op.execute(sa.text("""
        DELETE FROM subcategorias
        WHERE nombre = 'Tarjeta de crédito'
          AND categoria_id IN (SELECT id FROM categorias WHERE nombre = 'Banco' AND tipo = 'egreso');
    """))

    # 2. Eliminar índice y columna transaccion_pago_id de cuotas
    op.drop_index(
        'ix_cuotas_transaccion_pago_id',
        table_name='cuotas',
        postgresql_where=sa.text('transaccion_pago_id IS NOT NULL')
    )
    op.drop_column('cuotas', 'transaccion_pago_id')

    # 3. Eliminar índice y columna pago_resumen_vencimiento de transacciones
    op.drop_index(
        'ix_transacciones_pago_resumen_vencimiento',
        table_name='transacciones',
        postgresql_where=sa.text('pago_resumen_vencimiento IS NOT NULL')
    )
    op.drop_column('transacciones', 'pago_resumen_vencimiento')
