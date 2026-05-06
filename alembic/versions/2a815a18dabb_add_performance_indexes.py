"""add_performance_indexes

Revision ID: 2a815a18dabb
Revises: a40d550dae2e
Create Date: 2026-05-06 19:28:44.644085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a815a18dabb'
down_revision: Union[str, Sequence[str], None] = 'a40d550dae2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # transacciones
    op.create_index('ix_transacciones_categoria_id', 'transacciones', ['categoria_id'])
    op.create_index('ix_transacciones_subcategoria_id', 'transacciones', ['subcategoria_id'])
    op.create_index('ix_transacciones_tarjeta_id', 'transacciones', ['tarjeta_id'])
    op.create_index('ix_transacciones_usuario_tipo_fecha', 'transacciones', ['usuario_id', 'tipo', 'fecha'])
    
    # presupuestos
    op.create_index('ix_presupuestos_usuario_id', 'presupuestos', ['usuario_id'])
    op.create_index('ix_presupuestos_estado', 'presupuestos', ['estado'])
    
    # metas
    op.create_index('ix_metas_usuario_id', 'metas', ['usuario_id'])
    op.create_index('ix_metas_estado', 'metas', ['estado'])
    
    # notificaciones
    op.create_index('ix_notificaciones_usuario_leida', 'notificaciones', ['usuario_id', 'leida'])
    op.create_index('ix_notificaciones_fecha_creacion', 'notificaciones', ['fecha_creacion'])
    
    # tarjetas_credito
    op.create_index('ix_tarjetas_credito_usuario_id', 'tarjetas_credito', ['usuario_id'])
    op.create_index('ix_tarjetas_credito_billetera_id', 'tarjetas_credito', ['billetera_id'])
    op.create_index('ix_tarjetas_credito_estado', 'tarjetas_credito', ['estado'])
    
    # transferencias_internas
    op.create_index('ix_transferencias_internas_usuario_fecha', 'transferencias_internas', ['usuario_id', 'fecha'])
    op.create_index('ix_transferencias_internas_billetera_origen_id', 'transferencias_internas', ['billetera_origen_id'])
    op.create_index('ix_transferencias_internas_billetera_destino_id', 'transferencias_internas', ['billetera_destino_id'])
    
    # transacciones_recurrentes
    op.create_index('ix_transacciones_recurrentes_usuario_id', 'transacciones_recurrentes', ['usuario_id'])
    op.create_index('ix_transacciones_recurrentes_estado', 'transacciones_recurrentes', ['estado'])
    op.create_index('ix_transacciones_recurrentes_billetera_id', 'transacciones_recurrentes', ['billetera_id'])
    
    # movimientos_meta
    op.create_index('ix_movimientos_meta_meta_id', 'movimientos_meta', ['meta_id'])
    op.create_index('ix_movimientos_meta_billetera_id', 'movimientos_meta', ['billetera_id'])
    
    # categorias
    op.create_index('ix_categorias_creador_id', 'categorias', ['creador_id'])
    op.create_index('ix_categorias_es_global_estado', 'categorias', ['es_global', 'estado'])
    
    # subcategorias
    op.create_index('ix_subcategorias_categoria_id', 'subcategorias', ['categoria_id'])
    op.create_index('ix_subcategorias_creador_id', 'subcategorias', ['creador_id'])
    
    # presupuestos_categorias
    op.create_index('ix_presupuestos_categorias_presupuesto_id', 'presupuestos_categorias', ['presupuesto_id'])
    op.create_index('ix_presupuestos_categorias_categoria_id', 'presupuestos_categorias', ['categoria_id'])
    op.create_index('ix_presupuestos_categorias_subcategoria_id', 'presupuestos_categorias', ['subcategoria_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_presupuestos_categorias_subcategoria_id', table_name='presupuestos_categorias')
    op.drop_index('ix_presupuestos_categorias_categoria_id', table_name='presupuestos_categorias')
    op.drop_index('ix_presupuestos_categorias_presupuesto_id', table_name='presupuestos_categorias')
    op.drop_index('ix_subcategorias_creador_id', table_name='subcategorias')
    op.drop_index('ix_subcategorias_categoria_id', table_name='subcategorias')
    op.drop_index('ix_categorias_es_global_estado', table_name='categorias')
    op.drop_index('ix_categorias_creador_id', table_name='categorias')
    op.drop_index('ix_movimientos_meta_billetera_id', table_name='movimientos_meta')
    op.drop_index('ix_movimientos_meta_meta_id', table_name='movimientos_meta')
    op.drop_index('ix_transacciones_recurrentes_billetera_id', table_name='transacciones_recurrentes')
    op.drop_index('ix_transacciones_recurrentes_estado', table_name='transacciones_recurrentes')
    op.drop_index('ix_transacciones_recurrentes_usuario_id', table_name='transacciones_recurrentes')
    op.drop_index('ix_transferencias_internas_billetera_destino_id', table_name='transferencias_internas')
    op.drop_index('ix_transferencias_internas_billetera_origen_id', table_name='transferencias_internas')
    op.drop_index('ix_transferencias_internas_usuario_fecha', table_name='transferencias_internas')
    op.drop_index('ix_tarjetas_credito_estado', table_name='tarjetas_credito')
    op.drop_index('ix_tarjetas_credito_billetera_id', table_name='tarjetas_credito')
    op.drop_index('ix_tarjetas_credito_usuario_id', table_name='tarjetas_credito')
    op.drop_index('ix_notificaciones_fecha_creacion', table_name='notificaciones')
    op.drop_index('ix_notificaciones_usuario_leida', table_name='notificaciones')
    op.drop_index('ix_metas_estado', table_name='metas')
    op.drop_index('ix_metas_usuario_id', table_name='metas')
    op.drop_index('ix_presupuestos_estado', table_name='presupuestos')
    op.drop_index('ix_presupuestos_usuario_id', table_name='presupuestos')
    op.drop_index('ix_transacciones_usuario_tipo_fecha', table_name='transacciones')
    op.drop_index('ix_transacciones_tarjeta_id', table_name='transacciones')
    op.drop_index('ix_transacciones_subcategoria_id', table_name='transacciones')
    op.drop_index('ix_transacciones_categoria_id', table_name='transacciones')
