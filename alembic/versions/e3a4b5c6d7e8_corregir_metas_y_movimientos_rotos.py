"""corregir_metas_y_movimientos_rotos

Revision ID: e3a4b5c6d7e8
Revises: d2a3b4c5d6e7
Create Date: 2026-09-02 20:30:00.000000

Corrige el saldo corrompido y el estado en las dos metas y dos movimientos causados
por la carga con cotización 1.0000 de un aporte en pesos a meta en dólares.

Metas y movimientos referenciados exclusivamente por su ID:
- Movimiento 5692dc3f-c86b-4274-a275-00fcf84895ee (Meta 57b9875c-5b1f-4eae-9eac-791327f9360e)
- Movimiento 7db93528-2765-44e8-856b-24101754c534 (Meta 95db6e18-26e2-465d-a36d-8fa01660414b)

Verifica valores esperados antes de modificar:
- Movimientos: cotizacion_usada = 1.0000
- Metas: monto_actual = 800000.00 y estado = 'completada'
Si difieren, la migración aborta inmediatamente para proteger la integridad de los datos.
Corrige monto_actual a 524.59 y estado a 'activa'.
"""
from decimal import Decimal
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e3a4b5c6d7e8'
down_revision: Union[str, None] = 'd2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MOVIMIENTOS_ESPERADOS = {
    '5692dc3f-c86b-4274-a275-00fcf84895ee': Decimal('1.0000'),
    '7db93528-2765-44e8-856b-24101754c534': Decimal('1.0000'),
}

METAS_ESPERADAS = {
    '57b9875c-5b1f-4eae-9eac-791327f9360e': {
        'monto_actual': Decimal('800000.00'),
        'estado': 'completada',
    },
    '95db6e18-26e2-465d-a36d-8fa01660414b': {
        'monto_actual': Decimal('800000.00'),
        'estado': 'completada',
    },
}


def upgrade() -> None:
    # Soporte para generación estática con --sql (modo offline)
    if context.is_offline_mode():
        for mov_id in MOVIMIENTOS_ESPERADOS:
            op.execute(f"UPDATE movimientos_meta SET cotizacion_usada = 1525 WHERE id = '{mov_id}'")
        for meta_id in METAS_ESPERADAS:
            op.execute(f"UPDATE metas SET monto_actual = 524.59, estado = 'activa' WHERE id = '{meta_id}'")
        return

    # Modo online: verificación previa estricta de valores actuales en base de datos
    conn = op.get_bind()
    for mov_id, cot_esperada in MOVIMIENTOS_ESPERADOS.items():
        res = conn.execute(
            sa.text("SELECT cotizacion_usada FROM movimientos_meta WHERE id = :id"),
            {"id": mov_id},
        ).fetchone()
        if not res:
            raise RuntimeError(f"Abortando migración: Movimiento meta con id '{mov_id}' no fue encontrado.")
        cot_actual = res[0]
        if cot_actual != cot_esperada:
            raise RuntimeError(
                f"Abortando migración: Movimiento meta '{mov_id}' tiene cotizacion_usada={cot_actual}, "
                f"pero se esperaba={cot_esperada}. Se detiene la ejecución para no sobreescribir datos."
            )

    for meta_id, valores_esp in METAS_ESPERADAS.items():
        res = conn.execute(
            sa.text("SELECT monto_actual, estado FROM metas WHERE id = :id"),
            {"id": meta_id},
        ).fetchone()
        if not res:
            raise RuntimeError(f"Abortando migración: Meta con id '{meta_id}' no fue encontrada.")
        monto_actual, estado_actual = res[0], str(res[1])
        if monto_actual != valores_esp['monto_actual']:
            raise RuntimeError(
                f"Abortando migración: Meta '{meta_id}' tiene monto_actual={monto_actual}, "
                f"pero se esperaba={valores_esp['monto_actual']}. Se detiene la ejecución para no sobreescribir datos."
            )
        if estado_actual != valores_esp['estado']:
            raise RuntimeError(
                f"Abortando migración: Meta '{meta_id}' tiene estado='{estado_actual}', "
                f"pero se esperaba='{valores_esp['estado']}'. Se detiene la ejecución para no sobreescribir datos."
            )

    # Aplicar corrección sobre movimientos (cotizacion_usada = 1525)
    for mov_id in MOVIMIENTOS_ESPERADOS:
        conn.execute(
            sa.text("UPDATE movimientos_meta SET cotizacion_usada = 1525 WHERE id = :id"),
            {"id": mov_id},
        )

    # Aplicar corrección sobre metas (monto_actual = 524.59 y estado = 'activa')
    for meta_id in METAS_ESPERADAS:
        conn.execute(
            sa.text("UPDATE metas SET monto_actual = 524.59, estado = 'activa' WHERE id = :id"),
            {"id": meta_id},
        )


def downgrade() -> None:
    # No se revierten los datos corregidos: revertir volvería a dejar el dato corrompido
    # (monto_actual en 800.000 USD, cotizacion_usada en 1.0000 y estado en 'completada').
    pass
