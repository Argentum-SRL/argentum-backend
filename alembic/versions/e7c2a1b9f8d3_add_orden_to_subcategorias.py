"""add_orden_to_subcategorias

Revision ID: e7c2a1b9f8d3
Revises: d9f1e2a3b4c5
Create Date: 2026-09-02 01:10:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7c2a1b9f8d3'
down_revision: Union[str, Sequence[str], None] = 'd9f1e2a3b4c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Orden canónico por categoría y subcategoría según probabilidad de uso
ORDEN_SUBCATEGORIAS = {
    "Alimentación": ["Supermercado", "Kiosco", "Verdulería", "Carnicería", "Otros"],
    "Indumentaria": ["Ropa", "Calzado", "Accesorios"],
    "Servicios": ["Luz", "Gas", "Agua", "Alquiler", "Expensas", "Impuestos", "Seguros"],
    "Hogar": ["Limpieza", "Reparaciones", "Muebles y electrodomésticos"],
    "Salud": ["Farmacia", "Médico / Consulta", "Obra social / Prepaga", "Estudios y análisis", "Odontología", "Terapias"],
    "Transporte": ["Taxi / Apps", "Transporte público", "Combustible", "Peajes", "Estacionamiento", "Mantenimiento y seguro del auto"],
    "Comunicación": ["Celular", "Internet y cable"],
    "Recreativo": ["Salidas", "Suscripciones", "Deportes y gimnasio", "Hobbies y juegos", "Viajes"],
    "Educación": ["Cuotas", "Materiales y libros", "Idiomas"],
    "Restaurantes y delivery": ["Restaurantes", "Delivery", "Cafetería"],
    "Otros": ["Cuidado personal", "Mascotas", "Regalos", "Otros"],
    "Banco": ["Comisiones y gastos bancarios", "Impuesto al cheque / movimientos", "Préstamos", "Intereses pagados"],
    "Empleo": ["Sueldo", "Bonos y horas extras", "Aguinaldo"],
    "Trabajo independiente": ["Honorarios", "Venta de productos/servicios"],
    "Inversiones y rentas": ["Dividendos e intereses", "Alquileres cobrados"],
}


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('subcategorias')]

    if 'orden' not in columns:
        op.add_column(
            'subcategorias',
            sa.Column('orden', sa.Integer(), nullable=False, server_default='0')
        )

    # Actualizar valores de orden según listas canónicas
    for cat_nombre, subs in ORDEN_SUBCATEGORIAS.items():
        for idx, sub_nombre in enumerate(subs):
            conn.execute(
                sa.text("""
                    UPDATE subcategorias 
                    SET orden = :orden 
                    WHERE nombre = :sub_nombre 
                      AND categoria_id IN (SELECT id FROM categorias WHERE nombre = :cat_nombre)
                """),
                {"orden": idx, "sub_nombre": sub_nombre, "cat_nombre": cat_nombre}
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('subcategorias')]

    if 'orden' in columns:
        op.drop_column('subcategorias', 'orden')
