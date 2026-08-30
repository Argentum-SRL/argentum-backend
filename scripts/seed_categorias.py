import sys
import os

# Añadir el directorio raíz al path para poder importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from sqlalchemy.orm import Session

# 16 CATEGORÍAS CANÓNICAS OFICIALES DE ARGENTUM (12 Egresos + 4 Ingresos)
CATEGORIAS_SEED = [
    # ── EGRESOS (12) ──────────────────────────────────────
    {
        "nombre": "Alimentación",
        "tipo": "egreso",
        "icono": "alimentacion",
        "color": "#F97316",
        "subcategorias": ["Supermercado", "Verdulería", "Carnicería", "Kiosco", "Otros"]
    },
    {
        "nombre": "Indumentaria",
        "tipo": "egreso",
        "icono": "remera",
        "color": "#7C3AED",
        "subcategorias": ["Ropa", "Calzado", "Accesorios"]
    },
    {
        "nombre": "Servicios",
        "tipo": "egreso",
        "icono": "luz",
        "color": "#EAB308",
        "subcategorias": ["Alquiler", "Expensas", "Luz", "Gas", "Agua", "Seguros", "Impuestos"]
    },
    {
        "nombre": "Hogar",
        "tipo": "egreso",
        "icono": "casa",
        "color": "#8B5CF6",
        "subcategorias": ["Muebles y electrodomésticos", "Reparaciones", "Limpieza"]
    },
    {
        "nombre": "Salud",
        "tipo": "egreso",
        "icono": "medicina",
        "color": "#10B981",
        "subcategorias": ["Médico / Consulta", "Odontología", "Estudios y análisis", "Farmacia", "Obra social / Prepaga", "Terapias"]
    },
    {
        "nombre": "Transporte",
        "tipo": "egreso",
        "icono": "transporte",
        "color": "#0284C7",
        "subcategorias": ["Combustible", "Transporte público", "Taxi / Apps", "Mantenimiento y seguro del auto", "Peajes", "Estacionamiento"]
    },
    {
        "nombre": "Comunicación",
        "tipo": "egreso",
        "icono": "serviciosdigitales",
        "color": "#6366F1",
        "subcategorias": ["Celular", "Internet y cable"]
    },
    {
        "nombre": "Recreativo",
        "tipo": "egreso",
        "icono": "entretenimiento",
        "color": "#EC4899",
        "subcategorias": ["Suscripciones", "Salidas", "Deportes y gimnasio", "Hobbies y juegos", "Viajes"]
    },
    {
        "nombre": "Educación",
        "tipo": "egreso",
        "icono": "libros",
        "color": "#DC2626",
        "subcategorias": ["Cuotas", "Materiales y libros", "Idiomas"]
    },
    {
        "nombre": "Restaurantes y delivery",
        "tipo": "egreso",
        "icono": "hamburguesa",
        "color": "#F59E0B",
        "subcategorias": ["Restaurantes", "Delivery", "Cafetería"]
    },
    {
        "nombre": "Otros",
        "tipo": "egreso",
        "icono": "herramienta",
        "color": "#6B7280",
        "subcategorias": ["Cuidado personal", "Mascotas", "Regalos", "Otros"]
    },
    {
        "nombre": "Banco",
        "tipo": "egreso",
        "icono": "banco",
        "color": "#64748B",
        "subcategorias": ["Comisiones y gastos bancarios", "Préstamos", "Intereses pagados", "Impuesto al cheque / movimientos"]
    },
    # ── INGRESOS (4) ─────────────────────────────────────
    {
        "nombre": "Empleo",
        "tipo": "ingreso",
        "icono": "salario",
        "color": "#16A34A",
        "subcategorias": ["Sueldo", "Aguinaldo", "Bonos y horas extras"]
    },
    {
        "nombre": "Trabajo independiente",
        "tipo": "ingreso",
        "icono": "trato",
        "color": "#0D9488",
        "subcategorias": ["Honorarios", "Venta de productos/servicios"]
    },
    {
        "nombre": "Inversiones y rentas",
        "tipo": "ingreso",
        "icono": "dineroenmano",
        "color": "#2563EB",
        "subcategorias": ["Dividendos e intereses", "Alquileres cobrados"]
    },
    {
        "nombre": "Otros",
        "tipo": "ingreso",
        "icono": "dineroenmano",
        "color": "#9CA3AF",
        "subcategorias": ["Regalos", "Reintegros", "Otros"]
    },
]


# ==============================================================================
# ADVERTENCIA PREVENTIVA:
# Este seed busca categorías y subcategorías por NOMBRE, no por un identificador
# inmutable (slug o UUID). Es puramente aditivo: si no encuentra el nombre exacto,
# lo INSERTA como nuevo y NUNCA borra ni reconcilia registros que sobren.
#
# Si en el futuro renombrás una categoría o subcategoría en la lista CATEGORIAS_SEED
# de este archivo, tenés que ejecutar también un UPDATE manual/migración en la base
# de datos para la fila existente. Si solo cambiás el string acá y reiniciás el
# backend, el seeder insertará la nueva entidad y dejará la vieja huérfana y
# duplicada en la base de datos (consecuencia sufrida en agosto 2026).
# ==============================================================================

def seed_categorias(db: Session):
    """
    Seed idempotente de las 16 categorías canónicas y sus 61 subcategorías oficiales.
    - Idempotencia estricta en categorías: busca por nombre, tipo y es_global.
    - Idempotencia estricta en subcategorías: busca por categoria_id, nombre y es_global.
    - Actualiza icono y color si ya existían.
    """
    for cat_data in CATEGORIAS_SEED:
        # 1. Buscar si ya existe la categoría por nombre, tipo y global
        existente = db.query(Categoria).filter(
            Categoria.nombre == cat_data["nombre"],
            Categoria.tipo == cat_data["tipo"],
            Categoria.es_global == True
        ).first()

        if not existente:
            categoria = Categoria(
                nombre=cat_data["nombre"],
                tipo=cat_data["tipo"],
                icono=cat_data["icono"],
                color=cat_data["color"],
                es_global=True,
                creador_id=None,
                estado="activa"
            )
            db.add(categoria)
            db.flush()  # Para obtener categoria.id
            print(f"[+] Categoria creada: {categoria.nombre} ({categoria.tipo})")
        else:
            categoria = existente
            categoria.icono = cat_data["icono"]
            categoria.color = cat_data["color"]

        # 2. Seedear subcategorías con idempotencia estricta por categoría
        for nombre_sub in cat_data["subcategorias"]:
            sub_existente = db.query(Subcategoria).filter(
                Subcategoria.categoria_id == categoria.id,
                Subcategoria.nombre == nombre_sub,
                Subcategoria.es_global == True
            ).first()

            if not sub_existente:
                subcategoria = Subcategoria(
                    categoria_id=categoria.id,
                    nombre=nombre_sub,
                    es_global=True,
                    creador_id=None,
                    estado="activa"
                )
                db.add(subcategoria)
                print(f"  [+] Subcategoria creada: {nombre_sub} en {categoria.nombre}")

    db.commit()
    try:
        from app.services.categoria_service import invalidar_cache_categorias_globales
        invalidar_cache_categorias_globales()
    except Exception:
        pass
    print(f"[OK] Seed de categorias completado: {len(CATEGORIAS_SEED)} categorias canonicas contempladas.")



if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed_categorias(db)
    finally:
        db.close()
