"""recreate_missing_performance_indexes

Revision ID: e4c82b19a702
Revises: b4f81c92a101
Create Date: 2026-08-30 17:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4c82b19a702'
down_revision: Union[str, Sequence[str], None] = 'b4f81c92a101'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema with concurrent performance indexes."""
    with op.get_context().autocommit_block():
        # --- 1. Los 4 índices perdidos de 329646d68fe9 ---
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_recurrente_fecha
            ON transacciones (recurrente_id, fecha)
            WHERE recurrente_id IS NOT NULL
        """)

        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_grupos_cuotas_tarjeta_usuario
            ON grupos_cuotas (tarjeta_id, usuario_id)
        """)

        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_cuotas_grupo_vencimiento
            ON cuotas (grupo_id, fecha_vencimiento)
            WHERE pagada = false
        """)

        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_historial_suscripcion_id_fecha
            ON historial_suscripciones (suscripcion_id, vigente_desde DESC)
        """)

        # --- 2. Recreación de índices de performance de 7abc12345def y 2a815a18dabb ---
        # transacciones
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_usuario_id ON transacciones (usuario_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_usuario_fecha ON transacciones (usuario_id, fecha)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_usuario_tipo_fecha ON transacciones (usuario_id, tipo, fecha)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_billetera_id ON transacciones (billetera_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_categoria_id ON transacciones (categoria_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_subcategoria_id ON transacciones (subcategoria_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_tarjeta_id ON transacciones (tarjeta_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_estado_verificacion ON transacciones (estado_verificacion)")

        # billeteras
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_billeteras_usuario_id ON billeteras (usuario_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_billeteras_estado ON billeteras (estado)")

        # user_refresh_tokens
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_user_refresh_tokens_usuario_revocado_exp ON user_refresh_tokens (usuario_id, revocado, fecha_expiracion)")

        # cuotas
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_cuotas_grupo_id ON cuotas (grupo_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_cuotas_pagada_vencimiento ON cuotas (pagada, fecha_vencimiento)")

        # suscripciones
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_suscripciones_usuario_estado ON suscripciones (usuario_id, estado)")

        # presupuestos
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_presupuestos_usuario_id ON presupuestos (usuario_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_presupuestos_estado ON presupuestos (estado)")

        # metas
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_metas_usuario_id ON metas (usuario_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_metas_estado ON metas (estado)")

        # tarjetas_credito
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tarjetas_credito_usuario_id ON tarjetas_credito (usuario_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tarjetas_credito_billetera_id ON tarjetas_credito (billetera_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tarjetas_credito_estado ON tarjetas_credito (estado)")

        # transferencias_internas
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transferencias_internas_usuario_fecha ON transferencias_internas (usuario_id, fecha)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transferencias_internas_billetera_origen_id ON transferencias_internas (billetera_origen_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transferencias_internas_billetera_destino_id ON transferencias_internas (billetera_destino_id)")

        # transacciones_recurrentes
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_recurrentes_usuario_id ON transacciones_recurrentes (usuario_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_recurrentes_estado ON transacciones_recurrentes (estado)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transacciones_recurrentes_billetera_id ON transacciones_recurrentes (billetera_id)")

        # movimientos_meta
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_movimientos_meta_meta_id ON movimientos_meta (meta_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_movimientos_meta_billetera_id ON movimientos_meta (billetera_id)")

        # categorias
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_categorias_creador_id ON categorias (creador_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_categorias_es_global_estado ON categorias (es_global, estado)")

        # subcategorias
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_subcategorias_categoria_id ON subcategorias (categoria_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_subcategorias_creador_id ON subcategorias (creador_id)")

        # presupuestos_categorias
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_presupuestos_categorias_presupuesto_id ON presupuestos_categorias (presupuesto_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_presupuestos_categorias_categoria_id ON presupuestos_categorias (categoria_id)")
        op.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_presupuestos_categorias_subcategoria_id ON presupuestos_categorias (subcategoria_id)")


def downgrade() -> None:
    """Downgrade schema."""
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_presupuestos_categorias_subcategoria_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_presupuestos_categorias_categoria_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_presupuestos_categorias_presupuesto_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_subcategorias_creador_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_subcategorias_categoria_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_categorias_es_global_estado")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_categorias_creador_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_movimientos_meta_billetera_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_movimientos_meta_meta_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_recurrentes_billetera_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_recurrentes_estado")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_recurrentes_usuario_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transferencias_internas_billetera_destino_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transferencias_internas_billetera_origen_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transferencias_internas_usuario_fecha")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tarjetas_credito_estado")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tarjetas_credito_billetera_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_tarjetas_credito_usuario_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_metas_estado")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_metas_usuario_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_presupuestos_estado")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_presupuestos_usuario_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_suscripciones_usuario_estado")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_cuotas_pagada_vencimiento")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_cuotas_grupo_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_user_refresh_tokens_usuario_revocado_exp")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_billeteras_estado")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_billeteras_usuario_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_estado_verificacion")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_tarjeta_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_subcategoria_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_categoria_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_billetera_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_usuario_tipo_fecha")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_usuario_fecha")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_usuario_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_historial_suscripcion_id_fecha")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_cuotas_grupo_vencimiento")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_grupos_cuotas_tarjeta_usuario")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transacciones_recurrente_fecha")
