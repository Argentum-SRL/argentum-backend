"""create ejecuciones_jobs

Revision ID: f8e9d0c1b2a3
Revises: f5e6d7c8b9a0
Create Date: 2026-10-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f8e9d0c1b2a3'
down_revision: Union[str, Sequence[str], None] = 'f5e6d7c8b9a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "ejecuciones_jobs" not in tables:
        op.create_table(
            'ejecuciones_jobs',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column('job_id', sa.String(length=100), nullable=False),
            sa.Column('inicio', sa.DateTime(timezone=True), nullable=False),
            sa.Column('fin', sa.DateTime(timezone=True), nullable=False),
            sa.Column('resultado', sa.String(length=20), nullable=False),
            sa.Column('duracion_ms', sa.Integer(), nullable=False),
            sa.Column('detalle', sa.String(length=300), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index('ix_ejecuciones_jobs_job_id', 'ejecuciones_jobs', ['job_id'], unique=False)
        op.create_index('ix_ejecuciones_jobs_inicio', 'ejecuciones_jobs', ['inicio'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "ejecuciones_jobs" in tables:
        op.drop_index('ix_ejecuciones_jobs_inicio', table_name='ejecuciones_jobs')
        op.drop_index('ix_ejecuciones_jobs_job_id', table_name='ejecuciones_jobs')
        op.drop_table('ejecuciones_jobs')
