"""add_performance_indices

Revision ID: 7abc12345def
Revises: 6febb95b85d5
Create Date: 2026-05-04 15:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7abc12345def'
down_revision: Union[str, Sequence[str], None] = '6febb95b85d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # transacciones
    op.create_index('ix_transacciones_usuario_id', 'transacciones', ['usuario_id'])
    op.create_index('ix_transacciones_usuario_fecha', 'transacciones', ['usuario_id', 'fecha'])
    op.create_index('ix_transacciones_billetera_id', 'transacciones', ['billetera_id'])
    op.create_index('ix_transacciones_estado_verificacion', 'transacciones', ['estado_verificacion'])
    
    # billeteras
    op.create_index('ix_billeteras_usuario_id', 'billeteras', ['usuario_id'])
    op.create_index('ix_billeteras_estado', 'billeteras', ['estado'])
    
    # user_refresh_tokens
    op.create_index('ix_user_refresh_tokens_usuario_revocado_exp', 'user_refresh_tokens', ['usuario_id', 'revocado', 'fecha_expiracion'])
    
    # cuotas
    op.create_index('ix_cuotas_grupo_id', 'cuotas', ['grupo_id'])
    op.create_index('ix_cuotas_pagada_vencimiento', 'cuotas', ['pagada', 'fecha_vencimiento'])
    
    # suscripciones
    op.create_index('ix_suscripciones_usuario_estado', 'suscripciones', ['usuario_id', 'estado'])

def downgrade() -> None:
    op.drop_index('ix_transacciones_usuario_id', table_name='transacciones')
    op.drop_index('ix_transacciones_usuario_fecha', table_name='transacciones')
    op.drop_index('ix_transacciones_billetera_id', table_name='transacciones')
    op.drop_index('ix_transacciones_estado_verificacion', table_name='transacciones')
    op.drop_index('ix_billeteras_usuario_id', table_name='billeteras')
    op.drop_index('ix_billeteras_estado', table_name='billeteras')
    op.drop_index('ix_user_refresh_tokens_usuario_revocado_exp', table_name='user_refresh_tokens')
    op.drop_index('ix_cuotas_grupo_id', table_name='cuotas')
    op.drop_index('ix_cuotas_pagada_vencimiento', table_name='cuotas')
    op.drop_index('ix_suscripciones_usuario_estado', table_name='suscripciones')
