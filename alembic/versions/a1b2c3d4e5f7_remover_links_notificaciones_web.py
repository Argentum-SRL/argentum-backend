"""remover_links_notificaciones_web

Revision ID: a1b2c3d4e5f7
Revises: f5d6e7a8b9c0
Create Date: 2026-10-08

Remueve enlaces y URLs de mensajes en la tabla de notificaciones para que nunca
se muestren links en el centro de notificaciones web.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'f5d6e7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    engine_name = bind.engine.name if hasattr(bind, 'engine') else bind.dialect.name

    # 1. Caso específico de cambio de contraseña
    op.execute(sa.text("""
        UPDATE notificaciones
        SET mensaje = 'Tu contraseña de Argentum fue actualizada. Si no fuiste vos, cambiala de inmediato.'
        WHERE tipo = 'CAMBIO_CONTRASENA' AND mensaje LIKE '%http%';
    """))

    # 2. Caso genérico si hay otras notificaciones con URLs (en PostgreSQL)
    if engine_name == 'postgresql':
        op.execute(sa.text("""
            UPDATE notificaciones
            SET mensaje = REGEXP_REPLACE(
                REGEXP_REPLACE(
                    mensaje,
                    '\\s*(?:,\\s*)?(?:desde|en|ingresando a|a través de)?\\s*https?://\\S+',
                    '',
                    'gi'
                ),
                '([^.!?])$',
                '\\1.'
            )
            WHERE mensaje ~* 'https?://';
        """))


def downgrade() -> None:
    pass
